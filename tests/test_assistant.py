"""The assistant loop with test doubles for the LLM: retrieval, repair, charts, explanations, providers."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from src.assistant.charts import choose_chart
from src.assistant.core import DataAssistant, parse_llm_json, summarize_result
from src.assistant.knowledge import load_knowledge
from src.assistant.llm import AnthropicLLM, LLMError, MockLLM, OpenAILLM, resolve_llm
from src.assistant.retriever import KnowledgeRetriever, tokenize

GOOD_SQL = "SELECT channel_id, ROUND(SUM(net_sales), 2) AS net_sales FROM fct_orders GROUP BY 1"


@pytest.fixture(scope="module")
def knowledge():
    return load_knowledge()


@pytest.fixture(scope="module")
def retriever(knowledge):
    return KnowledgeRetriever(knowledge)


def make(db, llm, knowledge, retriever, **kw):
    return DataAssistant(db, llm, knowledge, retriever, **kw)


# -- knowledge and retrieval ----------------------------------------------------------------------------

def test_knowledge_documents_every_column_of_every_allowed_table(knowledge, small_warehouse):
    import sqlite3

    with sqlite3.connect(small_warehouse) as conn:
        for name, table in knowledge.tables.items():
            actual = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
            assert actual == list(table.columns), name
    assert not any(t.startswith(("raw_", "stg_")) for t in knowledge.allowed_tables)


@pytest.mark.parametrize("question,expected", [
    ("¿Cuál es la tasa de recompra por canal?", "repurchase_rate"),
    ("What is our repeat rate?", "repurchase_rate"),
    ("ventas netas por categoría", "net_sales"),
    ("¿Cuál es el ticket promedio?", "aov"),
    ("How many active customers do we have?", "active_customers"),
    ("Did Black Friday work?", "promotions"),
    ("margen de contribución del marketplace", "contribution_margin"),
])
def test_retrieval_finds_the_definition_in_english_and_spanish(retriever, question, expected):
    ids = [g.id for g in retriever.retrieve(question).glossary]
    assert expected in ids
    assert ids[:2] == ["reference_date", "sqlite_conventions"]  # always included


def test_tokenizer_strips_accents_plurals_stopwords_and_years():
    assert tokenize("¿Cuáles categorías crecieron en 2026?") == ["categoria", "crecieron"]


# -- the loop -------------------------------------------------------------------------------------------

def test_answer_with_table_chart_and_explanation(small_warehouse, knowledge, retriever, scripted):
    llm = scripted({"sql": GOOD_SQL, "chart": {"type": "bar", "x": "channel_id", "y": ["net_sales"]},
                    "notes": "Net sales = gross - discounts - returns."}, "Stores sell the most.")
    ans = make(small_warehouse, llm, knowledge, retriever).ask("Net sales by channel?")
    assert ans.ok and list(ans.data.columns) == ["channel_id", "net_sales"]
    assert (ans.chart.type, ans.chart.x, ans.chart.y) == ("bar", "channel_id", ["net_sales"])
    assert ans.explanation == "Stores sell the most."
    assert ans.tables == ["fct_orders"] and len(ans.attempts) == 1
    system, messages = llm.calls[0]
    assert "Net sales" in system and "fct_order_lines" in system and "2026-10-31" in system
    assert "Net sales by channel?" in messages[-1]["content"]
    assert "STORE" in llm.calls[1][1][0]["content"]  # the explanation sees the result


def test_rejected_query_is_sent_back_and_repaired(small_warehouse, knowledge, retriever, scripted):
    llm = scripted({"sql": "SELECT email FROM stg_customers"}, {"sql": "SELECT nope FROM fct_orders"},
                   {"sql": GOOD_SQL}, "ok")
    ans = make(small_warehouse, llm, knowledge, retriever).ask("Who are our customers?")
    assert ans.ok and ans.sql == GOOD_SQL
    assert [a.stage for a in ans.attempts] == ["validation", "validation", None]
    fix_prompt = llm.calls[1][1][-1]["content"]
    assert "stg_customers is not available" in fix_prompt and "fix_sql" in fix_prompt


def test_gives_up_after_the_repair_budget(small_warehouse, knowledge, retriever, scripted):
    llm = scripted(*[{"sql": "DELETE FROM fct_orders"}] * 5)
    ans = make(small_warehouse, llm, knowledge, retriever, max_repairs=2).ask("Delete everything")
    assert not ans.ok and "rejected" in ans.error and len(ans.attempts) == 3 and len(llm.calls) == 3


def test_unanswerable_question_is_explained(small_warehouse, knowledge, retriever, scripted):
    llm = scripted({"sql": None, "notes": "There is no weather data."})
    ans = make(small_warehouse, llm, knowledge, retriever).ask("Will it rain tomorrow?")
    assert not ans.ok and ans.error == "There is no weather data."


def test_api_errors_are_reported_not_raised(small_warehouse, knowledge, retriever, scripted):
    ans = make(small_warehouse, scripted(LLMError("rate limited")), knowledge, retriever).ask("Sales?")
    assert not ans.ok and "rate limited" in ans.error
    llm = scripted({"sql": GOOD_SQL}, LLMError("timeout"))  # explanation fails -> deterministic summary
    ans = make(small_warehouse, llm, knowledge, retriever).ask("Sales?")
    assert ans.ok and ans.explanation.startswith("The query returned 4 rows")


def test_follow_up_questions_carry_the_conversation(small_warehouse, knowledge, retriever, scripted):
    llm = scripted({"sql": GOOD_SQL}, "ok")
    make(small_warehouse, llm, knowledge, retriever).ask("And by category?", history=[("Net sales?", GOOD_SQL)])
    messages = llm.calls[0][1]
    assert len(messages) == 3 and json.loads(messages[1]["content"])["sql"] == GOOD_SQL


def test_mock_mode_runs_the_whole_loop_offline(small_warehouse, knowledge, retriever):
    assistant = make(small_warehouse, MockLLM(retriever), knowledge, retriever)
    ans = assistant.ask("What is the repurchase rate by customer region?")
    assert ans.ok and "repurchase_rate_pct" in ans.data.columns and "Demo mode" in ans.notes
    assert ans.explanation.startswith("The query returned")


@pytest.mark.parametrize("text,sql", [
    ('{"sql": "SELECT 1"}', "SELECT 1"),
    ('```json\n{"sql": "SELECT 1", "chart": null}\n```', "SELECT 1"),
    ('Here you go: {"sql": "SELECT 1"} hope it helps', "SELECT 1"),
    ("```sql\nSELECT 2\n```", "SELECT 2"),
    ("SELECT 3 FROM x", "SELECT 3 FROM x"),
])
def test_llm_replies_are_parsed_leniently(text, sql):
    assert parse_llm_json(text)["sql"] == sql


def test_reply_without_a_query_is_an_error():
    with pytest.raises(ValueError):
        parse_llm_json("I am not sure.")


# -- charts and summaries ------------------------------------------------------------------------------

def test_chart_heuristics():
    trend = pd.DataFrame({"month_start": ["2026-01-01", "2026-02-01", "2026-03-01"], "net_sales": [1.0, 2.0, 3.0]})
    assert choose_chart(trend).type == "line"
    ranking = pd.DataFrame({"category": ["A", "B"], "customers": [100, 200], "rate_pct": [50.0, 40.0]})
    assert (choose_chart(ranking).type, choose_chart(ranking).y) == ("bar", ["rate_pct"])
    assert choose_chart(pd.DataFrame({"x": [1]})) is None                             # a single number
    assert choose_chart(ranking, {"type": "bar", "x": "nope", "y": ["customers"]}).x == "category"  # bad hint
    assert choose_chart(ranking, {"type": "none"}) is None


def test_summary_of_a_result():
    assert summarize_result(pd.DataFrame()) == "The query returned no rows."
    assert summarize_result(pd.DataFrame({"active_customers": [18937]})) == "Result: active_customers = 18,937."


# -- providers ------------------------------------------------------------------------------------------

def test_provider_is_chosen_from_keys(monkeypatch, retriever):
    for var in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert resolve_llm(retriever).provider == "mock"
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        resolve_llm(retriever, "anthropic")
    with pytest.raises(LLMError, match="Unknown"):
        resolve_llm(retriever, "gemini")


def test_keys_can_come_from_streamlit_secrets(monkeypatch, retriever):
    for var in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    pytest.importorskip("anthropic")
    llm = resolve_llm(retriever, secrets={"ANTHROPIC_API_KEY": "sk-test", "LLM_MODEL": "claude-haiku-5-5"})
    assert (llm.provider, llm.model) == ("anthropic", "claude-haiku-5-5")


def test_anthropic_adapter_sends_system_prompt_and_reads_text_blocks():
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="..."),
                                        SimpleNamespace(type="text", text='{"sql": "SELECT 1"}')])

    llm = AnthropicLLM(client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert llm.complete("SYSTEM", [{"role": "user", "content": "hi"}]) == '{"sql": "SELECT 1"}'
    assert sent["system"] == "SYSTEM" and sent["model"] == "claude-sonnet-5-5" and sent["max_tokens"] >= 4000


def test_openai_adapter_puts_the_system_prompt_first():
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))])

    llm = OpenAILLM(client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    assert llm.complete("SYSTEM", [{"role": "user", "content": "hi"}]) == "answer"
    assert sent["messages"][0] == {"role": "system", "content": "SYSTEM"}


def test_adapter_errors_become_llm_errors_with_a_plain_explanation():
    def boom(**kwargs):
        raise RuntimeError("Error code: 401 - invalid x-api-key")

    llm = AnthropicLLM(client=SimpleNamespace(messages=SimpleNamespace(create=boom)))
    with pytest.raises(LLMError, match="Claude API error: the API key was rejected"):
        llm.complete("s", [{"role": "user", "content": "x"}])


def test_pasted_keys_are_trimmed(monkeypatch):
    from src.assistant.llm import _setting

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _setting("ANTHROPIC_API_KEY", {"ANTHROPIC_API_KEY": "  sk-ant-abc\n"}) == "sk-ant-abc"
    assert _setting("ANTHROPIC_API_KEY", {"ANTHROPIC_API_KEY": "   "}) is None
