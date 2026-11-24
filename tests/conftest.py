"""Shared fixtures: a small warehouse (four months, fast) and the default warehouse (the README's data)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import PipelineConfig  # noqa: E402
from src.generate_data import generate_all  # noqa: E402
from src.load import load_raw  # noqa: E402
from src.transform import run_sql_models  # noqa: E402

SMALL = {"seed": 7, "start": "2025-09-01", "end": "2025-12-31"}


def build_warehouse(raw, ref, db: Path, start: str, end: str) -> Path:
    load_raw(raw, ref, db, start, end)
    run_sql_models(db, PipelineConfig().sql_dir)
    return db


@pytest.fixture(scope="session")
def small_extract():
    return generate_all(**SMALL)


@pytest.fixture(scope="session")
def small_warehouse(tmp_path_factory, small_extract) -> Path:
    raw, ref = small_extract
    return build_warehouse(raw, ref, tmp_path_factory.mktemp("small") / "warehouse.db", SMALL["start"], SMALL["end"])


@pytest.fixture(scope="session")
def default_extract():
    return generate_all()


@pytest.fixture(scope="session")
def default_warehouse(tmp_path_factory, default_extract) -> Path:
    """The full default run (seed 42, Jan 2024 - Oct 2026): the data the eval set and the README use."""
    cfg = PipelineConfig()
    raw, ref = default_extract
    return build_warehouse(raw, ref, tmp_path_factory.mktemp("default") / "warehouse.db", cfg.start_date, cfg.end_date)


class ScriptedLLM:
    """Test double: returns the given replies in order and records every prompt it was sent."""

    provider, model = "scripted", "test"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, list[dict]]] = []

    def complete(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))  # a snapshot: the caller extends its list
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply) if isinstance(reply, dict) else reply


@pytest.fixture
def scripted():
    return ScriptedLLM
