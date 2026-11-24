"""Data quality checks. Each check is a SQL query that returns the number of bad rows (0 = pass).

If any check fails the pipeline stops before exporting, so neither the dashboard nor the assistant
ever sees bad data.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

CHECKS: list[tuple[str, str]] = [
    ("Every e-commerce order (final copy) is loaded, cancelled or rejected with a reason",
     """SELECT ABS((SELECT COUNT(*) FROM stg_ecom_orders_all)
                - (SELECT COUNT(*) FROM stg_orders WHERE source_system = 'ECOM')
                - (SELECT COUNT(*) FROM stg_cancelled_orders)
                - (SELECT COUNT(*) FROM stg_rejected_orders WHERE source_system = 'ECOM'))"""),
    ("Every POS sales ticket is loaded",
     """SELECT ABS((SELECT COUNT(DISTINCT ticket_id) FROM stg_pos_lines WHERE quantity > 0)
                - (SELECT COUNT(*) FROM fct_orders WHERE source_system = 'POS'))"""),
    ("Rejected orders are below 0.5% of the extract",
     """SELECT CASE WHEN 1.0 * (SELECT COUNT(*) FROM stg_rejected_orders) / (SELECT COUNT(*) FROM stg_orders) > 0.005
                    THEN 1 ELSE 0 END"""),
    ("No QA test orders reach the facts",
     """SELECT COUNT(*) FROM fct_orders o JOIN stg_customer_accounts a ON a.customer_id = o.customer_id
        WHERE a.is_test_account"""),
    ("Duplicate customer accounts are merged (one customer per e-mail)",
     "SELECT COUNT(*) - COUNT(DISTINCT email) FROM stg_customers"),
    ("Every identified order belongs to a known customer",
     """SELECT COUNT(*) FROM fct_orders o LEFT JOIN dim_customer c ON c.customer_id = o.customer_id
        WHERE o.customer_id IS NOT NULL AND c.customer_id IS NULL"""),
    ("Every line has a known product (with a category), channel and date",
     """SELECT COUNT(*) FROM fct_order_lines l
        LEFT JOIN dim_product p ON p.product_id = l.product_id
        LEFT JOIN dim_channel c ON c.channel_id = l.channel_id
        LEFT JOIN dim_date d ON d.date = l.order_date
        WHERE p.product_id IS NULL OR p.category IS NULL OR c.channel_id IS NULL OR d.date IS NULL"""),
    ("Store orders have a store and online orders do not",
     """SELECT COUNT(*) FROM fct_orders
        WHERE (channel_id = 'STORE' AND store_id IS NULL) OR (channel_id <> 'STORE' AND store_id IS NOT NULL)"""),
    ("Online channels have no orders before their launch date",
     """SELECT COUNT(*) FROM fct_orders o JOIN dim_channel c USING (channel_id) WHERE o.order_date < c.launch_date"""),
    ("Returns never exceed what was sold (quantity and amount)",
     """SELECT COUNT(*) FROM fct_order_lines
        WHERE returned_quantity > quantity OR returned_amount > gross_sales - discount_amount + 0.01 OR net_sales < -0.01"""),
    ("Unmatched returns are below 1% of returns",
     """SELECT CASE WHEN 1.0 * (SELECT COUNT(*) FROM stg_rejected_returns) / (SELECT COUNT(*) FROM stg_returns_all) > 0.01
                    THEN 1 ELSE 0 END"""),
    ("Sales reconcile: staged lines = fact lines = orders = monthly mart (gross and net)",
     """SELECT CASE WHEN ABS((SELECT SUM(quantity * unit_list_price) FROM stg_order_lines) - (SELECT SUM(gross_sales) FROM fct_order_lines)) > 1
                      OR ABS((SELECT SUM(net_sales) FROM fct_order_lines) - (SELECT SUM(net_sales) FROM fct_orders)) > 1
                      OR ABS((SELECT SUM(net_sales) FROM fct_orders) - (SELECT SUM(net_sales) FROM mart_sales_monthly)) > 1
                      OR ABS((SELECT SUM(refund_amount) FROM fct_returns) - (SELECT SUM(returned_amount) FROM fct_order_lines)) > 1
                    THEN 1 ELSE 0 END"""),
    ("Unit costs imputed for fewer than 5% of products",
     "SELECT CASE WHEN AVG(cost_imputed) > 0.05 THEN 1 ELSE 0 END FROM dim_product"),
    ("Every customer with orders has a first order, cohort and acquisition channel",
     """SELECT COUNT(*) FROM dim_customer
        WHERE total_orders > 0 AND (first_order_date IS NULL OR cohort_month IS NULL OR acquisition_channel IS NULL)"""),
    ("Gross margin is plausible (25%-50% of net sales)",
     "SELECT CASE WHEN SUM(gross_margin) / SUM(net_sales) NOT BETWEEN 0.25 AND 0.50 THEN 1 ELSE 0 END FROM fct_order_lines"),
]


class DataQualityError(RuntimeError):
    pass


def run_quality_checks(db_path: Path) -> dict[str, int]:
    """Run every check, log the result and raise if any check fails."""
    results: dict[str, int] = {}
    with sqlite3.connect(db_path) as conn:
        for name, sql in CHECKS:
            bad = int(conn.execute(sql).fetchone()[0] or 0)
            results[name] = bad
            log.info("%s %s%s", "PASS" if bad == 0 else "FAIL", name, "" if bad == 0 else f" ({bad} bad rows)")
    failed = [n for n, b in results.items() if b]
    if failed:
        raise DataQualityError(f"{len(failed)} data quality check(s) failed: {failed}")
    return results
