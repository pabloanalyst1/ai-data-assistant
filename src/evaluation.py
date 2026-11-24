"""Evaluation: execution accuracy of the assistant on eval/questions.yaml.

For each question the assistant writes and runs its SQL; the result is compared with the result of the gold
query. Comparing results (not SQL text) accepts any correct query: different joins, a mart instead of the fact
table, columns in another order or with other names, percentages instead of fractions.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from src.config import ROOT

EVAL_FILE = ROOT / "eval" / "questions.yaml"
SCALES = (1.0, 100.0, 0.01)  # a fraction matches the same percentage


@dataclass
class EvalQuestion:
    id: str
    question: str
    gold_sql: str
    compare: str = "set"
    tags: list[str] = field(default_factory=list)
    alt_gold_sql: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    id: str
    question: str
    correct: bool
    reason: str
    sql: str | None
    attempts: int
    seconds: float
    tags: list[str]


def load_questions(path: Path = EVAL_FILE) -> list[EvalQuestion]:
    with open(path, encoding="utf-8") as fh:
        return [EvalQuestion(**q) for q in yaml.safe_load(fh)]


# -- value matching -------------------------------------------------------------------------------------

def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return None if isinstance(v, float) and math.isnan(v) else float(v)
    return None


def _text(v) -> str:
    s = str(v).strip().lower()
    return s[:7] if re.fullmatch(r"\d{4}-\d{2}-01", s) else s  # '2025-12-01' == '2025-12'


def values_match(gold, pred, scale: float = 1.0) -> bool:
    g, p = _num(gold), _num(pred)
    if g is not None and p is not None:
        p *= scale
        tol = max(abs(g) * 0.005, 0.051 if abs(g) >= 1 else 0.0006)
        return abs(g - p) <= tol
    if g is None and p is None and (gold is None or pred is None):
        return gold is None and pred is None
    if g is not None or p is not None:  # a number against text: compare as text ('2026' vs 2026)
        return _text(gold) == _text(pred) or (g is not None and _text(int(g) if g.is_integer() else g) == _text(pred))
    return _text(gold) == _text(pred)


def _column_matches(gold: pd.Series, pred: pd.Series) -> float | None:
    """The scale at which two columns hold the same multiset of values, or None."""
    for scale in SCALES:
        g = sorted(gold.tolist(), key=_sort_key)
        p = sorted([(v * scale if _num(v) is not None else v) for v in pred.tolist()], key=_sort_key)
        if len(g) == len(p) and all(values_match(a, b) for a, b in zip(g, p)):
            return scale
    return None


def _sort_key(v):
    n = _num(v)
    return (0, n, "") if n is not None else (1, 0.0, _text(v))


def compare_results(gold: pd.DataFrame, pred: pd.DataFrame | None, mode: str) -> tuple[bool, str]:
    if pred is None:
        return False, "no result"
    if mode == "first_row":
        if pred.empty:
            return False, "empty result"
        row = list(pred.iloc[0])
        missing = [g for g in gold.iloc[0] if not any(values_match(g, v, s) for v in row for s in SCALES)]
        return (not missing, "ok" if not missing else f"first row lacks {missing}")
    if mode == "values":
        cells = [v for col in pred.columns for v in pred[col].tolist()]
        if len(pred) > 50 or len(cells) > 10 * gold.size + 10:
            return False, "answer too large to check"
        used: set[int] = set()
        for g in (v for col in gold.columns for v in gold[col].tolist()):
            hit = next((i for i, v in enumerate(cells) if i not in used and any(values_match(g, v, s) for s in SCALES)), None)
            if hit is None:
                return False, f"value {g} not found"
            used.add(hit)
        return True, "ok"
    # set: same rows; every gold column matched by a distinct answer column
    if len(gold) != len(pred):
        return False, f"{len(pred)} rows, expected {len(gold)}"
    mapping: dict[str, tuple[str, float]] = {}
    unmatched_labels = []
    for gcol in gold.columns:
        found = next(((pcol, s) for pcol in pred.columns if pcol not in {m[0] for m in mapping.values()}
                      for s in [_column_matches(gold[gcol], pred[pcol])] if s is not None), None)
        if found is None:
            if pd.api.types.is_numeric_dtype(gold[gcol]):
                return False, f"no column matches {gcol}"
            unmatched_labels.append(gcol)  # a text label: maybe the same rows, named in another language
            continue
        mapping[gcol] = found
    if unmatched_labels:
        # Labels may differ ('Tiendas físicas' for 'Offline') if the numbers match and identify every row
        # without ambiguity; a wrong number still fails.
        numeric = [g for g in mapping if pd.api.types.is_numeric_dtype(gold[g])]
        if not numeric or gold[numeric].duplicated().any():
            return False, f"no column matches {unmatched_labels[0]}"
        gold = gold[list(mapping)]
    # columns match one by one; now check they line up row by row
    gold_rows = sorted((tuple(r) for r in gold.itertuples(index=False)), key=lambda r: [_sort_key(v) for v in r])
    pred_proj = pd.DataFrame({g: pred[p] * s if s != 1.0 else pred[p] for g, (p, s) in mapping.items()})
    pred_rows = sorted((tuple(r) for r in pred_proj.itertuples(index=False)), key=lambda r: [_sort_key(v) for v in r])
    ok = all(values_match(a, b) for gr, pr in zip(gold_rows, pred_rows) for a, b in zip(gr, pr))
    if ok and unmatched_labels:
        return True, "ok (labels worded differently)"
    return (ok, "ok" if ok else "rows do not line up")


# -- running the evaluation ------------------------------------------------------------------------------

def gold_results(db_path: Path, q: EvalQuestion) -> list[pd.DataFrame]:
    with sqlite3.connect(db_path) as conn:
        return [pd.read_sql_query(s, conn) for s in [q.gold_sql, *q.alt_gold_sql]]


def rescore(db_path: Path, saved: list[dict], questions: list[EvalQuestion] | None = None) -> list[EvalResult]:
    """Score saved answers (their SQL) again with the current scorer, without asking the LLM."""
    by_id = {q.id: q for q in questions or load_questions()}
    out = []
    with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True) as conn:
        for r in saved:
            q = by_id[r["id"]]
            if r.get("sql"):
                try:
                    pred = pd.read_sql_query(r["sql"], conn)
                    checks = [compare_results(g, pred, q.compare) for g in gold_results(db_path, q)]
                    correct = any(ok for ok, _ in checks)
                    reason = next((why for ok, why in checks if ok), checks[0][1])
                except Exception as exc:  # the saved query no longer runs
                    correct, reason = False, str(exc)
            else:
                correct, reason = False, r["reason"]
            out.append(EvalResult(q.id, q.question, correct, reason, r.get("sql"), r["attempts"], r["seconds"], q.tags))
    return out


def evaluate(assistant, db_path: Path, questions: list[EvalQuestion] | None = None, progress=None) -> list[EvalResult]:
    results = []
    for q in questions or load_questions():
        t0 = time.perf_counter()
        answer = assistant.ask(q.question)
        if answer.ok:
            checks = [compare_results(g, answer.data, q.compare) for g in gold_results(db_path, q)]
            correct = any(ok for ok, _ in checks)
            reason = next((why for ok, why in checks if ok), checks[0][1])
        else:
            correct, reason = False, answer.error or "no answer"
        results.append(EvalResult(q.id, q.question, correct, reason, answer.sql, len(answer.attempts),
                                  time.perf_counter() - t0, q.tags))
        if progress:
            progress(results[-1])
    return results


def accuracy(results: list[EvalResult]) -> float:
    return sum(r.correct for r in results) / len(results) if results else 0.0
