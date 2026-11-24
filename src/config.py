"""Project settings: study period, business calendar, assistant guardrails and paths.

Business rules the SQL models need (reference date, marketplace commission, repurchase window) are also loaded
into reference tables (`ref_parameters`, `dim_channel`), so the SQL never hard-codes them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Period covered by the extract (order dates, Guatemala local time)
START_DATE = "2024-01-01"
END_DATE = "2026-10-31"
# The "today" of the case study. Relative periods ("last quarter", "this year", "active customer")
# are always measured from this date, never from the clock, so answers are reproducible.
AS_OF_DATE = END_DATE

# E-commerce timestamps arrive in UTC; Guatemala is UTC-6 all year (no daylight saving time)
LOCAL_UTC_OFFSET_HOURS = -6

# Customer metrics
REPURCHASE_WINDOW_DAYS = 90      # a repeat customer orders again within 90 days of the first order
ACTIVE_WINDOW_DAYS = 365         # an active customer ordered in the last 12 months

# Channels: id, name, type, launch date, commission on net sales (marketplace partner fee)
CHANNELS = [
    ("STORE", "Store", "Offline", "2015-03-01", 0.00),
    ("WEB", "Web", "Online", "2019-06-01", 0.00),
    ("APP", "App", "Online", "2024-06-03", 0.00),
    ("MARKETPLACE", "Marketplace", "Online", "2025-03-03", 0.15),
]

# Assistant guardrails
MAX_RESULT_ROWS = 500            # rows returned to the user / the LLM (the query itself is not rewritten)
QUERY_TIMEOUT_SECONDS = 10
MAX_REPAIR_ATTEMPTS = 2          # how many times the LLM may fix a query that failed validation or execution


@dataclass
class PipelineConfig:
    seed: int = 42
    start_date: str = START_DATE
    end_date: str = END_DATE
    raw_dir: Path = ROOT / "data" / "raw"
    db_path: Path = ROOT / "data" / "warehouse.db"
    sql_dir: Path = ROOT / "sql"
    export_dir: Path = ROOT / "exports"
