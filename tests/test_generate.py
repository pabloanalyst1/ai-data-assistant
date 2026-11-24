"""The simulated extracts: reproducible on every machine, and as messy as the README says."""
import hashlib

from src.generate_data import CHANNEL_LABELS, sql_upper


def fingerprint(raw) -> str:
    h = hashlib.sha256()
    for name in sorted(raw):
        h.update(name.encode())
        h.update(raw[name].to_csv(index=False).encode())
    return h.hexdigest()


def test_data_is_identical_on_every_machine(default_extract):
    """Golden fingerprint of the default extract. If this fails on your machine, the simulated data (and every
    number in the README) differ there: the generator must only use platform-independent draws."""
    raw, _ = default_extract
    assert fingerprint(raw) == "f2327c1448ef23e0bbffedf8d1aa240f1cbdedd36e53d72145201ec5ef264861"


def test_same_seed_same_data_and_another_seed_differs(small_extract):
    from src.generate_data import generate_all
    from tests.conftest import SMALL

    again, _ = generate_all(**SMALL)
    other, _ = generate_all(**{**SMALL, "seed": SMALL["seed"] + 1})
    raw, _ = small_extract
    assert fingerprint(again) == fingerprint(raw)
    assert fingerprint(other) != fingerprint(raw)


def test_extract_has_the_documented_problems(default_extract):
    raw, _ = default_extract
    pos, orders, items, returns = (raw["pos_ticket_lines"], raw["ecom_orders"], raw["ecom_order_items"],
                                   raw["ecom_returns"])
    crm, catalog = raw["crm_customers"], raw["catalog_products"]
    assert pos["ticket_ts"].str.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$").all()        # Spanish date format
    assert pos["unit_price"].str.startswith("Q").all()                                  # amounts as text
    assert (pos["quantity"] < 0).sum() > 1000                                            # returns mixed into sales
    assert (pos["loyalty_card"] == "").mean() > 0.3                                      # guest shoppers
    assert orders["created_at"].str.endswith("Z").all()                                  # UTC timestamps
    assert orders["order_id"].duplicated().sum() > 100                                   # re-sent orders
    assert set(orders["status"]) == {"completed", "cancelled", "pending"}
    assert (orders["created_at"].str[:4] > "2030").sum() > 0                             # clock errors
    assert orders["channel_label"].nunique() >= 10                                       # labels typed by hand
    assert (items["sku"] != items["sku"].str.strip()).sum() > 0                          # SKUs with spaces
    assert returns["return_id"].str.startswith("R-ORPHAN").sum() > 10                    # unmatched returns
    assert crm["email"].str.strip().str.lower().duplicated().sum() > 300                 # duplicate accounts
    assert crm["email"].str.endswith(".test").sum() == 5                                 # QA accounts
    assert crm["signup_date"].str.contains("/").sum() > 1000                             # two date formats
    assert catalog["unit_cost"].isna().sum() > 0                                         # missing costs
    assert catalog["category"].isin(["Belleza", "Moda", "Electrónica"]).any()            # Spanish categories


def test_alias_keys_match_sqlite_upper():
    """SQLite's UPPER() only changes ASCII letters, so the lookup keys must be built the same way."""
    assert sql_upper(" App Móvil ") == "APP MóVIL"
    assert sql_upper("Electrónica".upper()) == "ELECTRÓNICA"
    assert all(sql_upper(label) for labels in CHANNEL_LABELS.values() for label in labels)
