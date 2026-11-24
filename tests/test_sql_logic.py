"""The SQL models on a hand-made extract where every right answer is known.

Customer C1 buys in store on 15 Jan (and returns it on 20 Jan), then on the app on 28 Feb local time with a
duplicate account (C2), placed 1 Mar 03:00 UTC. C3 buys once on the marketplace. Around them: a cancelled
order re-sent by the integration, a QA test order, a clock error, a guest store ticket and an unmatched return.
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from src.generate_data import generate_reference_tables
from tests.conftest import build_warehouse

START, END = "2025-01-01", "2025-12-31"


def hand_made_extract():
    catalog = pd.DataFrame([
        ("SKU-A", "Serum", "Belleza", "Skincare", "Ceiba Botánica", "Y", 100.0, 40.0, "2023-01-01"),
        ("SKU-B", "Speaker", "ELECTRONICS", "Audio", "Volta", "N", 1000.0, 800.0, "2023-01-01"),
        ("SKU-C", "Earbuds", "Electrónica", "Audio", "Volta", "N", 500.0, np.nan, "2023-01-01"),
    ], columns=["sku", "product_name", "category", "subcategory", "brand", "is_private_label", "list_price",
                "unit_cost", "launch_date"])
    crm = pd.DataFrame([
        ("C1", "c1@example.com", "2025-01-10", "Guate", "ST01"),
        ("C2", " C1@EXAMPLE.COM", "2025-02-20", "Guatemala", "ST01"),       # duplicate account of C1
        ("C3", "c3@example.com", "05/02/2025", "Xela", "ST06"),
        ("QA0001", "qa1@casaceiba-qa.test", "2025-01-02", "Guatemala", "ST01"),
    ], columns=["customer_id", "email", "signup_date", "department", "home_store"])
    orders = pd.DataFrame([
        ("W1", "C2", "App Móvil", "2025-03-01T03:00:00Z", "completed", "2025-03-01 03:10:00"),
        ("W2", "C1", "web", "2025-04-10T15:00:00Z", "pending", "2025-04-10 15:05:00"),
        ("W2", "C1", "web", "2025-04-10T15:00:00Z", "cancelled", "2025-04-11 09:00:00"),
        ("W3", "QA0001", "WEB", "2025-04-12T15:00:00Z", "completed", "2025-04-12 15:00:00"),
        ("W4", "C3", "MKT", "2025-05-01T18:00:00Z", "completed", "2025-05-01 18:05:00"),
        ("W5", "C3", "Marketplace", "2093-05-01T18:00:00Z", "completed", "2025-05-01 18:05:00"),
    ], columns=["order_id", "customer_id", "channel_label", "created_at", "status", "ingested_at"])
    items = pd.DataFrame([
        ("W1", 1, "SKU-A", 2, 100.0, 20.0, "MAMA25", "2025-03-01 03:10:00"),
        ("W1", 2, " sku-c", 1, 500.0, 0.0, None, "2025-03-01 03:10:00"),
        ("W2", 1, "SKU-A", 1, 100.0, 0.0, None, "2025-04-10 15:05:00"),
        ("W2", 1, "SKU-A", 1, 100.0, 0.0, None, "2025-04-11 09:00:00"),
        ("W3", 1, "SKU-A", 1, 1.0, 0.0, None, "2025-04-12 15:00:00"),
        ("W4", 1, "SKU-B", 1, 1000.0, 0.0, None, "2025-05-01 18:05:00"),
        ("W5", 1, "SKU-A", 1, 100.0, 0.0, None, "2025-05-01 18:05:00"),
    ], columns=["order_id", "line_no", "sku", "quantity", "unit_price", "discount_amount", "promo_code", "ingested_at"])
    returns = pd.DataFrame([
        ("R-W1-1", "W1", 1, "SKU-A", 1, "2025-03-10", 90.0, "Changed mind"),
        ("R-ORPHAN-000", "W999", 1, "SKU-A", 1, "2025-03-10", 150.0, "Changed mind"),
    ], columns=["return_id", "order_id", "line_no", "sku", "quantity", "return_date", "refund_amount", "reason"])
    pos = pd.DataFrame([
        ("T-ST01-0000001", 1, "ST01", "15/01/2025 10:30", "C1", "SKU-B", 1, "Q1,000.00", "Q0.00", "", ""),
        ("T-ST01-0000002", 1, "ST01", "20/01/2025 11:00", "C1", "SKU-B", -1, "Q1,000.00", "Q-0.00", "", "T-ST01-0000001/1"),
        ("T-ST01-0000003", 1, "ST01", "20/02/2025 12:00", "", "sku-a ", 1, "Q100.00", "Q0.00", "", ""),
    ], columns=["ticket_id", "line_no", "store_code", "ticket_ts", "loyalty_card", "sku", "quantity", "unit_price",
                "discount", "promo_code", "original_ticket_line"])
    raw = {"pos_ticket_lines": pos, "ecom_orders": orders, "ecom_order_items": items, "ecom_returns": returns,
           "crm_customers": crm, "catalog_products": catalog}
    return raw, generate_reference_tables(catalog)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    raw, ref = hand_made_extract()
    return build_warehouse(raw, ref, tmp_path_factory.mktemp("hand") / "warehouse.db", START, END)


def frame(db, sql):
    with sqlite3.connect(db) as conn:
        return pd.read_sql_query(sql, conn)


def test_orders_kept_cancelled_and_rejected(db):
    assert sorted(frame(db, "SELECT order_id FROM fct_orders")["order_id"]) == [
        "T-ST01-0000001", "T-ST01-0000003", "W1", "W4"]
    assert frame(db, "SELECT order_id FROM stg_cancelled_orders")["order_id"].tolist() == ["W2"]  # last copy wins
    rejected = frame(db, "SELECT order_id, reject_reason FROM stg_rejected_orders ORDER BY order_id")
    assert rejected.values.tolist() == [["W3", "QA test order"],
                                        ["W5", "Order date outside the extract period (clock error)"]]


def test_utc_timestamps_become_local_time_and_channels_are_mapped(db):
    w1 = frame(db, "SELECT * FROM fct_orders WHERE order_id = 'W1'").iloc[0]
    assert w1["order_ts"] == "2025-02-28 21:00:00"   # 1 Mar 03:00 UTC is still February in Guatemala
    assert w1["channel_id"] == "APP" and w1["customer_id"] == "C1"  # duplicate account C2 -> C1
    assert frame(db, "SELECT channel_id FROM fct_orders WHERE order_id = 'W4'").iloc[0, 0] == "MARKETPLACE"


def test_customers_merged_cleaned_and_test_accounts_removed(db):
    c = frame(db, "SELECT * FROM dim_customer ORDER BY customer_id").set_index("customer_id")
    assert list(c.index) == ["C1", "C3"]
    assert c.loc["C1", "accounts_merged"] == 2
    assert (c.loc["C1", "department"], c.loc["C3", "department"], c.loc["C3", "region"]) == (
        "Guatemala", "Quetzaltenango", "Western")
    assert c.loc["C3", "signup_date"] == "2025-02-05"


def test_line_amounts_returns_cost_and_commission(db):
    lines = frame(db, "SELECT * FROM fct_order_lines").set_index("order_line_id")
    a = lines.loc["W1-1"]  # 2 x 100, 20 discount, 1 unit returned for 90
    assert (a["gross_sales"], a["discount_amount"], a["returned_quantity"], a["returned_amount"]) == (200, 20, 1, 90)
    assert (a["net_sales"], a["cogs"], a["gross_margin"]) == (90, 40, 50)
    assert a["category"] == "Beauty" and a["is_promo"] == 1
    c = lines.loc["W1-2"]  # SKU-C had no cost: imputed at the subcategory's cost ratio (800 / 1000)
    assert c["product_id"] == "SKU-C" and c["cogs"] == 400 and c["category"] == "Electronics"
    m = lines.loc["W4-1"]  # marketplace: 15% commission on net sales
    assert (m["net_sales"], m["gross_margin"], m["commission"], m["contribution_margin"]) == (1000, 200, 150, 50)
    store = lines.loc["T-ST01-0000001-1"]  # bought and returned at the store
    assert (store["net_sales"], store["cogs"], store["returned_quantity"]) == (0, 0, 1)
    guest = frame(db, "SELECT o.customer_id, l.product_id FROM fct_orders o JOIN fct_order_lines l USING (order_id) "
                      "WHERE order_id = 'T-ST01-0000003'").iloc[0]
    assert guest["customer_id"] is None and guest["product_id"] == "SKU-A"


def test_returns_matched_or_rejected(db):
    r = frame(db, "SELECT return_id, refund_amount, source_system FROM fct_returns ORDER BY return_id")
    assert r.values.tolist() == [["R-W1-1", 90.0, "ECOM"], ["T-ST01-0000002/1", 1000.0, "POS"]]
    assert frame(db, "SELECT return_id FROM stg_rejected_returns")["return_id"].tolist() == ["R-ORPHAN-000"]


def test_customer_facts(db):
    c = frame(db, "SELECT * FROM dim_customer").set_index("customer_id")
    # C1: first order in store on 15 Jan, second on the app on 28 Feb (44 days) -> repeat customer
    assert (c.loc["C1", "acquisition_channel"], c.loc["C1", "days_to_second_order"],
            c.loc["C1", "repurchased_within_90d"], c.loc["C1", "total_orders"]) == ("STORE", 44, 1, 2)
    assert c.loc["C1", "cohort_month"] == "2025-01-01"
    # C3: one marketplace order on 1 May, window closed by the reference date (31 Dec) -> not a repeat
    assert (c.loc["C3", "acquisition_channel"], c.loc["C3", "repurchased_within_90d"]) == ("MARKETPLACE", 0)
    assert c["is_active"].tolist() == [1, 1]
    orders = frame(db, "SELECT order_id, customer_order_number, COALESCE(days_since_previous_order, -1) AS gap "
                       "FROM fct_orders WHERE customer_id = 'C1' ORDER BY order_ts")
    assert orders.values.tolist() == [["T-ST01-0000001", 1, -1], ["W1", 2, 44]]
