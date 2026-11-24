"""Load step: save the raw extracts as received (CSV) and load them into raw_* / ref_* tables."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

from src.config import ACTIVE_WINDOW_DAYS, LOCAL_UTC_OFFSET_HOURS, REPURCHASE_WINDOW_DAYS

log = logging.getLogger(__name__)


def parameters_table(start_date: str, end_date: str) -> pd.DataFrame:
    """Business rules as a one-row table, so the SQL models (and the assistant) read them instead of hard-coding them."""
    return pd.DataFrame([{
        "period_start": start_date,
        "period_end": end_date,
        "as_of_date": end_date,
        "local_utc_offset_hours": LOCAL_UTC_OFFSET_HOURS,
        "repurchase_window_days": REPURCHASE_WINDOW_DAYS,
        "active_window_days": ACTIVE_WINDOW_DAYS,
        "currency": "GTQ",
    }])


def save_raw_files(raw: dict[str, pd.DataFrame], raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, df in raw.items():
        df.to_csv(raw_dir / f"{name}.csv", index=False)


def load_raw(raw: dict[str, pd.DataFrame], ref: dict[str, pd.DataFrame], db_path: Path,
             start_date: str, end_date: str) -> None:
    """Replace the database with a fresh copy of the source extracts, the reference data and the rules."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    with sqlite3.connect(db_path) as conn:
        for name, df in raw.items():
            df.to_sql(f"raw_{name}", conn, index=False)
            log.info("Loaded raw_%s (%s rows)", name, f"{len(df):,}")
        for name, df in ref.items():
            df.to_sql(f"ref_{name}", conn, index=False)
        parameters_table(start_date, end_date).to_sql("ref_parameters", conn, index=False)
        log.info("Loaded %d reference tables and ref_parameters", len(ref))
