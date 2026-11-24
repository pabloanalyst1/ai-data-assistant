"""The assistant: question -> retrieve definitions -> LLM writes SQL -> guard validates -> run -> chart + explanation.

If the SQL is rejected by the guard or fails to run, the error goes back to the LLM, which gets
MAX_REPAIR_ATTEMPTS chances to fix it. Nothing the LLM writes runs without passing the guard.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.assistant import prompts
from src.assistant.charts import ChartSpec, choose_chart
from src.assistant.knowledge import Knowledge, load_knowledge
from src.assistant.llm import LLM, LLMError
from src.assistant.retriever import KnowledgeRetriever, RetrievedContext
from src.assistant.sql_guard import GuardError, SQLGuard
from src.config import MAX_REPAIR_ATTEMPTS, MAX_RESULT_ROWS, QUERY_TIMEOUT_SECONDS

log = logging.getLogger(__name__)


@dataclass
class Attempt:
    sql: str | None
    error: str | None = None
    stage: str | None = None          # "validation", "execution" or None if it worked


@dataclass
class Answer:
    question: str
    sql: str | None = None
    data: pd.DataFrame | None = None
    truncated: bool = False
    chart: ChartSpec | None = None
    explanation: str = ""
    notes: str = ""
    error: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    context: RetrievedContext | None = None
    tables: list[str] = field(default_factory=list)
    provider: str = ""
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and self.data is not None


def parse_llm_json(text: str) -> dict:
    """Read the LLM's JSON reply; tolerate code fences, text around the JSON or a bare SQL query."""
    text = (text or "").strip()
    candidates = [text, re.sub(r"^```(?:json)?\s*|\s*```$", "", text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    fenced = re.search(r"```sql\s*(.+?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return {"sql": fenced.group(1).strip()}
    if re.match(r"(?is)^\s*(select|with)\b", text):
        return {"sql": text}
    raise ValueError("The model did not return a query.")


def summarize_result(df: pd.DataFrame, truncated: bool = False) -> str:
    """Deterministic plain-language summary (used in demo mode and if the LLM explanation fails)."""
    if df is None or df.empty:
        return "The query returned no rows."

    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.4f}"
        return f"{v:,}" if isinstance(v, int) else str(v)

    if len(df) == 1:
        return "Result: " + ", ".join(f"{c} = {fmt(v)}" for c, v in df.iloc[0].items()) + "."
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    label = next((c for c in df.columns if c not in numeric), df.columns[0])
    text = f"The query returned {len(df)} rows{' (truncated)' if truncated else ''}."
    if numeric:
        m = numeric[-1] if numeric[-1] != label else numeric[0]
        top, low = df.loc[df[m].idxmax()], df.loc[df[m].idxmin()]
        text += f" Highest {m}: {top[label]} ({fmt(top[m])}); lowest: {low[label]} ({fmt(low[m])})."
    return text


class DataAssistant:
    def __init__(self, db_path: Path | str, llm: LLM, knowledge: Knowledge | None = None,
                 retriever: KnowledgeRetriever | None = None, max_repairs: int = MAX_REPAIR_ATTEMPTS,
                 max_rows: int = MAX_RESULT_ROWS, timeout_s: float = QUERY_TIMEOUT_SECONDS, explain: bool = True):
        self.knowledge = knowledge or load_knowledge()
        self.retriever = retriever or KnowledgeRetriever(self.knowledge)
        self.guard = SQLGuard(db_path, self.knowledge.allowed_tables, max_rows=max_rows, timeout_s=timeout_s)
        self.llm = llm
        self.max_repairs = max_repairs
        self.explain = explain

    def ask(self, question: str, history: list[tuple[str, str]] | None = None) -> Answer:
        t0 = time.perf_counter()
        answer = Answer(question=question.strip(), provider=f"{self.llm.provider}:{self.llm.model}")
        if not answer.question:
            answer.error = "Please type a question."
            return answer
        answer.context = self.retriever.retrieve(answer.question)
        system = prompts.sql_system_prompt(self.knowledge, answer.context)
        messages = prompts.history_messages((history or [])[-3:])
        messages.append({"role": "user", "content": prompts.sql_user_message(answer.question)})

        reply: dict = {}
        for attempt_no in range(self.max_repairs + 1):
            try:
                reply = parse_llm_json(self.llm.complete(system, messages))
            except (LLMError, ValueError) as exc:
                answer.error = str(exc)
                break
            sql = reply.get("sql")
            answer.notes = str(reply.get("notes") or "")
            if not sql:
                answer.error = answer.notes or "The assistant could not write a query for this question."
                break
            try:
                result = self.guard.execute(sql)
            except GuardError as exc:
                answer.attempts.append(Attempt(sql, str(exc), "validation"))
                error = str(exc)
            except Exception as exc:  # SQLite runtime error, timeout
                answer.attempts.append(Attempt(sql, str(exc), "execution"))
                error = str(exc)
            else:
                answer.attempts.append(Attempt(result.sql))
                answer.sql, answer.data, answer.truncated, answer.tables = (
                    result.sql, result.data, result.truncated, result.tables)
                break
            if attempt_no == self.max_repairs:
                answer.error = f"The query was rejected or failed: {error}"
                break
            messages += [{"role": "assistant", "content": json.dumps({"sql": sql})},
                         {"role": "user", "content": prompts.fix_user_message(answer.question, sql, error)}]

        if answer.ok:
            answer.chart = choose_chart(answer.data, reply.get("chart") if isinstance(reply.get("chart"), dict) else None)
            answer.explanation = self._explain(answer) if self.explain else ""
        elif answer.attempts and answer.attempts[-1].sql:
            answer.sql = answer.attempts[-1].sql
        answer.elapsed_s = time.perf_counter() - t0
        return answer

    def _explain(self, answer: Answer) -> str:
        fallback = summarize_result(answer.data, answer.truncated)
        try:
            text = self.llm.complete(prompts.EXPLAIN_SYSTEM, [{"role": "user", "content": prompts.explain_user_message(
                answer.question, answer.sql, answer.data, answer.truncated, answer.notes)}])
        except LLMError as exc:
            log.warning("Explanation failed: %s", exc)
            return fallback
        return text or fallback
