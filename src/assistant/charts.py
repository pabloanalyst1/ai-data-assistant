"""Choose a chart for a query result: the LLM's suggestion if it is valid, otherwise a simple heuristic."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

TIME_NAME = re.compile(r"(date|month|week|quarter|year|period|day)", re.IGNORECASE)
TIME_VALUE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?|-Q[1-4])?$")


@dataclass
class ChartSpec:
    type: str                      # "bar" or "line"
    x: str
    y: list[str] = field(default_factory=list)


def _numeric(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]


def _is_time(df: pd.DataFrame, col: str) -> bool:
    values = df[col].dropna().astype(str)
    return bool(TIME_NAME.search(col)) and (values.str.match(TIME_VALUE).all() or col.lower() in ("year", "month"))


def choose_chart(df: pd.DataFrame | None, hint: dict | None = None) -> ChartSpec | None:
    if df is None or len(df) < 2 or len(df.columns) < 2:
        return None  # a single number or row reads better as text
    numeric = _numeric(df)
    if hint and hint.get("type") in ("bar", "line") and hint.get("x") in df.columns:
        y = [c for c in (hint.get("y") or []) if c in numeric and c != hint["x"]]
        if y:
            return ChartSpec(hint["type"], hint["x"], y[:3])
    if hint and hint.get("type") == "none":
        return None
    candidates = [c for c in df.columns if c not in numeric] or [c for c in df.columns if _is_time(df, c)]
    if not candidates:
        return None
    x = next((c for c in candidates if _is_time(df, c)), candidates[0])
    y = [c for c in numeric if c != x][::-1]  # the last column is usually the metric that was asked for
    if not y or len(df) > 60:
        return None
    y = y[:1]  # one measure per chart: two measures with different units would need two axes
    if df[x].duplicated().any():  # x repeats (e.g. month x channel): too complex for a quick chart
        return None
    return ChartSpec("line" if _is_time(df, x) and len(df) > 2 else "bar", x, y)
