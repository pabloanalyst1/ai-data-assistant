"""AI data assistant for Casa Ceiba: ask business questions in plain English or Spanish.

Run locally:
    streamlit run app/streamlit_app.py
The API key is read from ANTHROPIC_API_KEY (or OPENAI_API_KEY), as an environment variable or in Streamlit
secrets (.streamlit/secrets.toml locally, the app's Secrets on Streamlit Community Cloud). Without a key the app
runs in demo mode. If data/warehouse.db does not exist yet, the app builds it on first start (this is what
happens on Streamlit Community Cloud). Set ASSISTANT_DB_PATH to use another warehouse file instead.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assistant.core import Answer, DataAssistant  # noqa: E402
from src.assistant.knowledge import load_knowledge  # noqa: E402
from src.assistant.llm import LLMError, resolve_llm  # noqa: E402
from src.assistant.retriever import KnowledgeRetriever  # noqa: E402
from src.config import MAX_RESULT_ROWS, PipelineConfig  # noqa: E402

st.set_page_config(page_title="AI Data Assistant · Casa Ceiba", layout="wide")

EXAMPLES = [
    "¿Qué categoría creció más el último trimestre?",
    "¿Cuál es la tasa de recompra por canal?",
    "What is the gross margin % by category this year?",
    "Which promotions paid off, and which ones cost us margin?",
    "Show monthly net sales by channel for 2026",
    "What is month-6 retention for the 2025 cohorts by acquisition channel?",
]


@st.cache_resource(show_spinner="First start: building the data warehouse (about a minute)...")
def build_warehouse(db_path: str) -> str:
    """Run the pipeline once when the app starts without a warehouse (e.g. on Streamlit Community Cloud)."""
    from run_pipeline import run

    run(PipelineConfig(), export=False)
    return db_path


def secrets():
    try:
        return st.secrets
    except Exception:  # no secrets file
        return None


def llm_settings() -> tuple:
    """Which provider settings exist (never the key itself), so the cached assistant is rebuilt when they change."""
    sec = secrets()

    def present(name):
        try:
            return bool(os.environ.get(name) or (sec is not None and sec.get(name)))
        except Exception:
            return False

    return tuple(present(n) for n in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"))


@st.cache_resource
def get_assistant(db_path: str, settings: tuple = ()) -> DataAssistant:
    knowledge = load_knowledge()
    retriever = KnowledgeRetriever(knowledge)
    return DataAssistant(db_path, resolve_llm(retriever, secrets=secrets()), knowledge, retriever)


@st.cache_data
def read_table(db_path: str, sql: str) -> pd.DataFrame:
    with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True) as conn:
        return pd.read_sql_query(sql, conn)


def render_answer(answer: Answer) -> None:
    if answer.error:
        st.error(answer.error)
    else:
        st.markdown(answer.explanation)
        if answer.chart:
            chart_data = answer.data.set_index(answer.chart.x)[answer.chart.y]
            if answer.chart.type == "line":
                st.line_chart(chart_data)
            else:
                st.bar_chart(chart_data)
        st.dataframe(answer.data, hide_index=True)
        if answer.truncated:
            st.caption(f"Showing the first {MAX_RESULT_ROWS} rows.")
    with st.expander("How this was answered"):
        if answer.sql:
            st.code(answer.sql, language="sql")
        if answer.notes:
            st.markdown(f"**Assumption:** {answer.notes}")
        if answer.tables:
            st.markdown("**Tables read:** " + ", ".join(f"`{t}`" for t in answer.tables))
        if answer.context:
            st.markdown("**Definitions used:** " + ", ".join(g.term for g in answer.context.glossary))
        for i, a in enumerate(answer.attempts[:-1] if answer.ok else answer.attempts, 1):
            st.markdown(f"**Attempt {i} rejected ({a.stage}):** {a.error}")
        st.caption(f"{answer.provider} · {answer.elapsed_s:.1f} s · read-only, max {MAX_RESULT_ROWS} rows")


def chat_page(assistant: DataAssistant) -> None:
    st.title("Ask Casa Ceiba's data")
    st.caption("Business questions in plain English or Spanish. The assistant writes the SQL, a guard checks it "
               "(read-only, approved tables only), and you get the answer with a table and a chart.")
    if assistant.llm.provider == "mock":
        st.info("**Demo mode**: no API key is configured, so instead of an LLM the assistant reuses the closest "
                "worked example. Try the example questions in the sidebar, or add `ANTHROPIC_API_KEY` to the "
                "app's secrets for real answers.")

    st.session_state.setdefault("messages", [])
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            if m["role"] == "assistant":
                render_answer(m["answer"])
            else:
                st.markdown(m["content"])

    question = st.chat_input("e.g. Which channel has the best contribution margin this year?")
    question = question or st.session_state.pop("pending_question", None)
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        history = [(m["answer"].question, m["answer"].sql) for m in st.session_state.messages
                   if m["role"] == "assistant" and m["answer"].ok]
        with st.chat_message("assistant"):
            with st.spinner("Writing and checking the query..."):
                answer = assistant.ask(question, history=history)
            render_answer(answer)
        st.session_state.messages += [{"role": "user", "content": question},
                                      {"role": "assistant", "answer": answer}]


def overview_page(db_path: str) -> None:
    st.title("Casa Ceiba · Business overview")
    kpis = read_table(db_path, "SELECT * FROM mart_kpi_summary ORDER BY sort")
    st.caption(f"Year to date ({kpis['current_period'].iloc[0]}) vs the same period of 2025.")
    show = {"Net sales (GTQ)": "Q{:,.0f}", "Orders": "{:,.0f}", "Average order value (GTQ)": "Q{:,.0f}",
            "Gross margin %": "{:.1%}"}
    for col, (_, row) in zip(st.columns(len(show)), kpis[kpis["kpi"].isin(show)].iterrows()):
        if pd.isna(row["prior_year_value"]):  # no data for the same period last year
            delta = None
        elif "%" in row["kpi"]:
            delta = f"{100 * (row['current_value'] - row['prior_year_value']):+.1f} pts"
        else:
            delta = f"{row['change']:+.1%}"
        col.metric(row["kpi"], show[row["kpi"]].format(row["current_value"]), delta)

    st.subheader("Net sales by channel (monthly, GTQ)")
    monthly = read_table(db_path, "SELECT month_start, channel_id, SUM(net_sales) AS net_sales FROM mart_sales_monthly "
                                  "GROUP BY 1, 2")
    st.line_chart(monthly.pivot(index="month_start", columns="channel_id", values="net_sales"))

    left, right = st.columns(2)
    with left:
        st.subheader("Category growth, 2026-Q3 vs 2025-Q3")
        growth = read_table(db_path, "SELECT category, ROUND(100 * yoy_growth, 1) AS yoy_growth_pct "
                                     "FROM mart_category_quarterly WHERE year_quarter = '2026-Q3'")
        st.bar_chart(growth.set_index("category"))
    with right:
        st.subheader("90-day repurchase rate by acquisition channel")
        rep = read_table(db_path, "SELECT acquisition_channel, ROUND(100.0 * SUM(repeat_customers) / "
                                  "SUM(eligible_customers), 1) AS repurchase_rate_pct FROM mart_repurchase GROUP BY 1")
        st.bar_chart(rep.set_index("acquisition_channel"))
    st.subheader("Promotions: sales lift vs incremental gross margin")
    promos = read_table(db_path, "SELECT promo_name, discount_pct, ROUND(100 * net_sales_lift, 1) AS lift_pct, "
                                 "ROUND(incremental_gross_margin) AS incremental_gross_margin_gtq, "
                                 "ROUND(100 * after_vs_baseline, 1) AS sales_after_vs_baseline_pct "
                                 "FROM mart_promo_performance ORDER BY start_date")
    st.dataframe(promos, hide_index=True)


# -- main -----------------------------------------------------------------------------------------------
if "ASSISTANT_DB_PATH" in os.environ:  # an explicit warehouse file: use it as is
    db_path = Path(os.environ["ASSISTANT_DB_PATH"])
    if not db_path.exists():
        st.error("The warehouse does not exist yet. Run `python run_pipeline.py` first, then reload this page.")
        st.stop()
else:  # default location: build it on first start if needed
    db_path = PipelineConfig().db_path
    if not db_path.exists():
        build_warehouse(str(db_path))

st.sidebar.title("AI Data Assistant")
page = st.sidebar.radio("View", ["Ask the data", "Business overview"], label_visibility="collapsed")
if page == "Ask the data":
    try:
        assistant = get_assistant(str(db_path), llm_settings())
    except LLMError as exc:
        st.error(str(exc))
        st.stop()
    # demo mode can only answer questions close to a worked example, so it suggests those
    examples = EXAMPLES if assistant.llm.provider != "mock" else [e.question for e in assistant.knowledge.examples[:6]]
    st.sidebar.subheader("Try asking")
    for i, example in enumerate(examples):
        if st.sidebar.button(example, key=f"example_{i}"):
            st.session_state.pending_question = example
    if st.sidebar.button("Clear conversation", key="clear"):
        st.session_state.messages = []
    chat_page(assistant)
else:
    overview_page(str(db_path))
st.sidebar.caption("Casa Ceiba is a fictional retailer; the data is synthetic. "
                   "[Source code](https://github.com/pabloanalyst1/ai-data-assistant)")
