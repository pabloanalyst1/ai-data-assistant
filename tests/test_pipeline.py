"""End to end: the SQL models build a clean warehouse that passes every quality check."""
import sqlite3

import pytest

from src.export import export_marts
from src.quality import CHECKS, DataQualityError, run_quality_checks


def q(db, sql):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


def test_all_quality_checks_pass(small_warehouse):
    results = run_quality_checks(small_warehouse)
    assert len(results) == len(CHECKS) and not any(results.values())


def test_default_warehouse_passes_quality_checks(default_warehouse):
    assert not any(run_quality_checks(default_warehouse).values())


def test_quality_checks_stop_the_pipeline_on_bad_data(small_warehouse, tmp_path):
    bad = tmp_path / "bad.db"
    with sqlite3.connect(small_warehouse) as src, sqlite3.connect(bad) as dst:
        src.backup(dst)
        dst.execute("UPDATE fct_order_lines SET returned_quantity = quantity + 1 WHERE rowid = 1")
    with pytest.raises(DataQualityError, match="Returns never exceed"):
        run_quality_checks(bad)


def test_problem_rows_are_set_aside_with_a_reason(small_warehouse):
    reasons = dict(q(small_warehouse, "SELECT reject_reason, COUNT(*) FROM stg_rejected_orders GROUP BY 1"))
    assert reasons.get("QA test order", 0) > 0
    assert q(small_warehouse, "SELECT COUNT(*) FROM stg_cancelled_orders")[0][0] > 0
    assert q(small_warehouse, "SELECT COUNT(*) FROM stg_rejected_returns")[0][0] > 0
    assert q(small_warehouse, "SELECT COUNT(*) FROM dim_customer WHERE accounts_merged > 1")[0][0] > 0


def test_marts_reconcile_with_the_facts(small_warehouse):
    (lines,), = q(small_warehouse, "SELECT ROUND(SUM(net_sales)) FROM fct_order_lines")
    (mart,), = q(small_warehouse, "SELECT ROUND(SUM(net_sales)) FROM mart_sales_monthly")
    (cat,), = q(small_warehouse, "SELECT ROUND(SUM(net_sales)) FROM mart_category_quarterly")
    assert lines == mart == cat
    (cohort0,), = q(small_warehouse, "SELECT SUM(active_customers) - SUM(cohort_customers) FROM mart_customer_cohorts "
                                     "WHERE months_since_first = 0")
    assert cohort0 == 0  # every customer buys in their own first month


def test_export_writes_every_mart(small_warehouse, tmp_path):
    written = export_marts(small_warehouse, tmp_path)
    names = {p.stem for p in written}
    assert {"mart_sales_monthly", "mart_customer_cohorts", "mart_repurchase", "mart_promo_performance",
            "mart_category_quarterly", "mart_kpi_summary"} <= names
