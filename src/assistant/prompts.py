"""Prompt templates. The SQL prompt carries the schema, the retrieved glossary entries and worked examples."""
from __future__ import annotations

import json

import pandas as pd

from src.assistant.knowledge import Knowledge
from src.assistant.llm import TASK_MARKER
from src.assistant.retriever import RetrievedContext

SQL_SYSTEM = """You are the data assistant of Casa Ceiba, an omnichannel retailer in Guatemala (stores, web, app and
a marketplace). You answer business questions by writing ONE SQLite query against the company's data warehouse.

Rules:
- Write a single read-only SELECT (or WITH ... SELECT) statement in SQLite syntax. No other statement types.
- Use only the tables and columns listed below. Never invent columns.
- Follow the business definitions below exactly: they are how the company defines its metrics.
- Relative periods are measured from the reference date 2026-10-31, never from the clock.
- Aggregate in SQL: return a compact result (a KPI, a ranking, a trend), not raw rows. At most a few hundred rows.
- Money is in quetzales (GTQ).
- If the question cannot be answered with these tables, set "sql" to null and explain why in "notes".

Respond with ONE JSON object and nothing else:
{{"sql": "<the query>", "chart": {{"type": "bar" | "line" | "none", "x": "<column>", "y": ["<column>", ...]}},
 "notes": "<one sentence on the definition or assumption you used, in the language of the question>"}}

## Tables
{schema}

## Business definitions
{glossary}

## Worked examples
{examples}
"""

EXPLAIN_SYSTEM = """You explain query results to business users of Casa Ceiba, a retailer in Guatemala.
Answer the question in 2-4 sentences, in the same language as the question, using the numbers in the result.
Lead with the direct answer. Format money as Q1,234,567 and percentages with one decimal. Mention the definition
used when it matters (e.g. "repurchase = a second order within 90 days"). Never invent numbers that are not in
the result; if the result is truncated or empty, say so. Plain text only, no markdown tables."""


def sql_system_prompt(knowledge: Knowledge, context: RetrievedContext) -> str:
    glossary = "\n".join(e.to_prompt() for e in context.glossary)
    examples = "\n\n".join(f"Question: {e.question}\nSQL:\n{e.sql}" for e in context.examples) or "(none)"
    return SQL_SYSTEM.format(schema=knowledge.schema_prompt(), glossary=glossary, examples=examples)


def sql_user_message(question: str) -> str:
    return f"{TASK_MARKER} write_sql\nQuestion: {question}"


def fix_user_message(question: str, sql: str, error: str) -> str:
    return (f"{TASK_MARKER} fix_sql\nQuestion: {question}\nYour query:\n{sql}\n\nIt was rejected: {error}\n"
            "Return the corrected JSON object.")


def history_messages(history: list[tuple[str, str]]) -> list[dict]:
    """Earlier turns (question, sql), so follow-ups like 'and by channel?' have context."""
    messages = []
    for question, sql in history:
        messages.append({"role": "user", "content": sql_user_message(question)})
        messages.append({"role": "assistant", "content": json.dumps({"sql": sql})})
    return messages


def explain_user_message(question: str, sql: str, data: pd.DataFrame, truncated: bool, notes: str) -> str:
    preview = data.head(30).to_csv(index=False)
    more = f"\n(The result has more rows than shown: {len(data)} shown{', truncated' if truncated else ''}.)" \
        if len(data) > 30 or truncated else ""
    return (f"{TASK_MARKER} explain\nQuestion: {question}\nSQL:\n{sql}\nAssumption: {notes or 'none'}\n"
            f"Result ({len(data)} rows, CSV):\n{preview}{more}")
