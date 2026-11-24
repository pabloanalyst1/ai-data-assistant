"""The SQL guard: read-only, allowlisted tables, safe functions, row cap and time limit."""
import sqlite3

import pytest

from src.assistant.knowledge import load_knowledge
from src.assistant.sql_guard import GuardError, SQLGuard, cte_names, mask_literals_and_comments, strip_sql


@pytest.fixture(scope="module")
def guard(small_warehouse):
    return SQLGuard(small_warehouse, load_knowledge().allowed_tables, max_rows=5, timeout_s=1)


ALLOWED = [
    "SELECT channel_id, SUM(net_sales) FROM fct_orders GROUP BY 1",
    "select count(*) from dim_customer;",
    "WITH t AS (SELECT * FROM fct_orders) SELECT COUNT(*) FROM t",
    "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) SELECT SUM(i) FROM n",
    "SELECT 'DROP TABLE x; -- not code' AS text_that_looks_dangerous",
    "SELECT REPLACE(category, '&', 'and') FROM dim_product",
    "SELECT * FROM fct_order_lines -- a trailing comment; DROP TABLE x",
    "```sql\nSELECT as_of_date FROM ref_parameters\n```",
    "SELECT d.year_quarter, LAG(SUM(l.net_sales)) OVER (ORDER BY d.year_quarter) FROM fct_order_lines l "
    "JOIN dim_date d ON d.date = l.order_date GROUP BY 1",
]

BLOCKED = [
    ("DELETE FROM fct_orders", "start with SELECT"),
    ("UPDATE fct_orders SET net_sales = 0", "UPDATE is not allowed"),
    ("DROP TABLE fct_orders", "DROP is not allowed"),
    ("SELECT 1; DROP TABLE fct_orders", "Only one statement"),
    ("INSERT OR REPLACE INTO dim_store VALUES (1)", "start with SELECT"),
    ("PRAGMA table_info(fct_orders)", "PRAGMA"),
    ("ATTACH DATABASE '/tmp/x.db' AS x", "ATTACH"),
    ("WITH x AS (SELECT 1) DELETE FROM fct_orders", "DELETE is not allowed"),
    ("SELECT * FROM raw_crm_customers", "raw_crm_customers is not available"),
    ("SELECT email FROM stg_customers", "stg_customers is not available"),
    ("SELECT * FROM (SELECT * FROM stg_customer_accounts)", "stg_customer_accounts is not available"),
    ("WITH c AS (SELECT * FROM raw_ecom_orders) SELECT * FROM c", "raw_ecom_orders is not available"),
    ('SELECT * FROM "stg_customers"', "stg_customers is not available"),
    ("SELECT name FROM sqlite_master", "sqlite_master is not available"),
    ("SELECT load_extension('evil.so')", "load_extension() is not allowed"),
    ("SELECT randomblob(100000000)", "randomblob() is not allowed"),
    ("SELECT DATE('now')", "reference date"),
    ("SELECT * FROM fct_orders WHERE order_date > CURRENT_DATE", "reference date"),
    ("SELECT no_such_column FROM fct_orders", "no such column"),
    ("", "empty"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_read_queries_on_allowed_tables_run(guard, sql):
    result = guard.execute(sql)
    assert len(result.data) <= 5


@pytest.mark.parametrize("sql,reason", BLOCKED)
def test_unsafe_queries_are_rejected_before_running(guard, sql, reason):
    with pytest.raises(GuardError) as exc:
        guard.execute(sql)
    assert reason in str(exc.value)


def test_nothing_was_changed_by_the_attempts(guard, small_warehouse):
    with sqlite3.connect(small_warehouse) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fct_orders").fetchone()[0] > 0


def test_rows_are_capped_and_flagged(guard):
    result = guard.execute("SELECT * FROM fct_order_lines")
    assert len(result.data) == 5 and result.truncated
    assert not guard.execute("SELECT COUNT(*) FROM fct_order_lines").truncated


def test_runaway_query_is_stopped(guard):
    with pytest.raises(TimeoutError):
        guard.execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n")


def test_a_cte_named_like_a_forbidden_table_only_reads_the_cte(guard):
    assert guard.execute("WITH stg_customers AS (SELECT 1 AS x) SELECT * FROM stg_customers").data.values.tolist() == [[1]]


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM fct_orders JOIN stg_customers USING (customer_id)",   # the authorizer never sees this
    "SELECT COUNT(*) FROM fct_orders NATURAL JOIN stg_customers",
    "SELECT o.order_id FROM fct_orders o JOIN raw_crm_customers c USING (customer_id)",
])
def test_joins_the_authorizer_cannot_see_are_caught_in_the_compiled_program(guard, sql):
    with pytest.raises(GuardError, match="not available"):
        guard.execute(sql)


def test_tables_read_are_reported(guard):
    assert guard.execute("SELECT COUNT(*) FROM fct_orders o JOIN dim_store s USING (store_id)").tables == [
        "dim_store", "fct_orders"]


def test_connection_is_read_only_even_without_the_guard_checks(guard):
    conn = guard._connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM fct_orders")
    conn.close()


def test_helpers():
    masked = mask_literals_and_comments("SELECT 'a;b' -- c;\n/* d; */ x")
    assert ";" not in masked and masked.startswith("SELECT '") and masked.endswith(" x")
    assert strip_sql(" SELECT 1 ;; ") == "SELECT 1"
    assert cte_names("WITH a AS (SELECT 1), b(x, y) AS (SELECT 1, 2) SELECT * FROM a, b") == {"a", "b"}


def test_missing_warehouse_is_explained(tmp_path):
    with pytest.raises(FileNotFoundError, match="run_pipeline.py"):
        SQLGuard(tmp_path / "none.db", {"fct_orders"}).execute("SELECT 1")
