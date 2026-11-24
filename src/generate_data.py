"""Synthetic source extracts from a fictional omnichannel retailer in Guatemala.

**Casa Ceiba** (fictional) sells electronics, home, fashion, beauty, sports and toys through seven stores,
a website, a mobile app (launched June 2024) and a third-party marketplace (March 2025, 15% commission).
Four source systems feed the warehouse, each with its own habits:

* **POS** (stores): one row per ticket line, Spanish date format, amounts as text (`"Q1,234.50"`), returns
  as negative-quantity lines that point to the original ticket. Many shoppers do not scan a loyalty card.
* **E-commerce platform** (web, app, marketplace): orders, items and returns, timestamps in **UTC**, channel
  labels typed by hand, orders re-sent by the integration, cancelled and QA test orders.
* **CRM**: customer accounts, with duplicate accounts for the same e-mail and department names spelled
  several ways.
* **Catalog** (ERP): products, with category names in Spanish or English and a few missing unit costs.

The data is simulated, but the mechanics behind it are realistic, so the analysis has real signal:

* **Seasonality**: Black Friday ("Viernes Negro"), Christmas, Mother's Day (May 10), back to school (January),
  Bono 14 (mid-July bonus) and paydays on the 15th and the last day of each month.
* **Channels**: the app grows fast after launch and its customers buy more often; marketplace customers
  churn fastest and return the most; stores are flat.
* **Categories**: a private-label skincare line (Ceiba Botánica, March 2026) makes Beauty the fastest-growing
  category in 2026. Electronics sells a lot at thin margins. Fashion is returned most.
* **Promotions**: discounts lift sales of the categories on promotion; deep discounts (30% or more) lift
  them most but pull demand forward (sales dip for three weeks afterwards) and can sell below cost.
* **Retention**: each customer has a personal purchase rate and a monthly churn risk that depends on how they
  were acquired (and is higher for customers acquired on a deep Black Friday discount).

Same seed -> same data on every machine (see the helpers below), so every number in the README is reproducible.
"""
from __future__ import annotations

import math
from collections import defaultdict
from functools import lru_cache

import numpy as np
import pandas as pd

from src.config import CHANNELS, END_DATE, LOCAL_UTC_OFFSET_HOURS, START_DATE

# ------------------------------------------------------------------------------------------------------
# Business reference data
# ------------------------------------------------------------------------------------------------------
# category: gross margin at list price, base return rate, Spanish name used by part of the catalog
CATEGORIES = {
    "Electronics": (0.20, 0.050, "Electrónica"),
    "Home & Kitchen": (0.40, 0.040, "Hogar y Cocina"),
    "Fashion": (0.55, 0.130, "Moda"),
    "Beauty": (0.58, 0.020, "Belleza"),
    "Sports & Outdoors": (0.38, 0.050, "Deportes"),
    "Toys & Kids": (0.42, 0.030, "Juguetes y Niños"),
}

# category, subcategory, min list price, max list price (GTQ), number of products, brands
SUBCATEGORIES = [
    ("Electronics", "Audio", 150, 1800, 8, ("Volta", "Sonik", "Auralis")),
    ("Electronics", "Computer accessories", 90, 1200, 8, ("Volta", "Keyron", "Nexo")),
    ("Electronics", "Smart home", 200, 1500, 6, ("Nexo", "Lumio")),
    ("Electronics", "Phones & tablets", 900, 4500, 6, ("Nexo", "Auralis")),
    ("Home & Kitchen", "Cookware", 120, 900, 8, ("Ceiba Home", "Fogón", "Cocinarte")),
    ("Home & Kitchen", "Small appliances", 250, 1600, 7, ("Fogón", "Brisa", "Cocinarte")),
    ("Home & Kitchen", "Bedding & bath", 90, 700, 7, ("Ceiba Home", "Algodón Real")),
    ("Home & Kitchen", "Decor", 60, 600, 6, ("Ceiba Home", "Tierra Viva")),
    ("Fashion", "Women", 120, 650, 9, ("Marea", "Ceiba Studio", "Lino & Co")),
    ("Fashion", "Men", 120, 600, 8, ("Marea", "Ceiba Studio", "Norte")),
    ("Fashion", "Shoes", 250, 900, 7, ("Paso Firme", "Norte")),
    ("Fashion", "Accessories", 60, 450, 6, ("Lino & Co", "Ceiba Studio")),
    ("Beauty", "Skincare", 70, 450, 7, ("Dermalia", "Rosa Mística")),
    ("Beauty", "Makeup", 60, 350, 7, ("Rosa Mística", "Glow Lab")),
    ("Beauty", "Fragrance", 180, 900, 5, ("Esencia", "Glow Lab")),
    ("Beauty", "Hair care", 50, 300, 6, ("Dermalia", "Bambú")),
    ("Sports & Outdoors", "Fitness", 100, 1500, 7, ("Atleta", "Pulso")),
    ("Sports & Outdoors", "Outdoor & camping", 150, 1400, 6, ("Volcán", "Atleta")),
    ("Sports & Outdoors", "Cycling", 200, 3500, 5, ("Ruedas GT", "Pulso")),
    ("Toys & Kids", "Toys", 80, 700, 8, ("Jugarte", "Pequeños Genios")),
    ("Toys & Kids", "School supplies", 25, 300, 7, ("Escolar", "Pequeños Genios")),
    ("Toys & Kids", "Baby", 90, 900, 6, ("Cuna Feliz", "Jugarte")),
]

# Private-label skincare line launched in March 2026: the reason Beauty grows in 2026
BOTANICA_LAUNCH = "2026-03-16"
BOTANICA_PRODUCTS = [
    ("Ceiba Botánica Vitamin C serum", 189), ("Ceiba Botánica day cream SPF 30", 149),
    ("Ceiba Botánica gentle cleanser", 99), ("Ceiba Botánica night cream", 169),
    ("Ceiba Botánica sunscreen SPF 50", 129), ("Ceiba Botánica body lotion", 89),
]

# store_id, name, city, department, format, opened
STORES = [
    ("ST01", "Casa Ceiba Zona 10", "Guatemala City", "Guatemala", "Flagship", "2015-03-01"),
    ("ST02", "Casa Ceiba Miraflores", "Guatemala City", "Guatemala", "Mall", "2016-08-01"),
    ("ST03", "Casa Ceiba Pradera Concepción", "Santa Catarina Pinula", "Guatemala", "Mall", "2018-04-01"),
    ("ST04", "Casa Ceiba Naranjo", "Mixco", "Guatemala", "Mall", "2019-11-01"),
    ("ST05", "Casa Ceiba Antigua", "Antigua Guatemala", "Sacatepéquez", "Street", "2017-06-01"),
    ("ST06", "Casa Ceiba Xela", "Quetzaltenango", "Quetzaltenango", "Mall", "2018-09-01"),
    ("ST07", "Casa Ceiba Escuintla", "Escuintla", "Escuintla", "Mall", "2020-02-01"),
]
STORE_WEIGHT = {"ST01": 1.4, "ST02": 1.2, "ST03": 1.0, "ST04": 0.9, "ST05": 0.6, "ST06": 0.8, "ST07": 0.6}

# department, region, share of customers, how they spell it in the CRM (first one is correct)
DEPARTMENTS = [
    ("Guatemala", "Central", 0.52, ("Guatemala", "GUATEMALA", "Guate", "Ciudad de Guatemala")),
    ("Sacatepéquez", "Central", 0.06, ("Sacatepéquez", "Sacatepequez", "SACATEPEQUEZ")),
    ("Escuintla", "Central", 0.07, ("Escuintla", "ESCUINTLA")),
    ("Chimaltenango", "Central", 0.04, ("Chimaltenango", "Chimal")),
    ("Quetzaltenango", "Western", 0.10, ("Quetzaltenango", "Xela", "QUETZALTENANGO")),
    ("Huehuetenango", "Western", 0.04, ("Huehuetenango", "Huehue")),
    ("San Marcos", "Western", 0.03, ("San Marcos", "SAN MARCOS")),
    ("Alta Verapaz", "Northern", 0.04, ("Alta Verapaz", "A. Verapaz")),
    ("Petén", "Northern", 0.03, ("Petén", "Peten")),
    ("Zacapa", "Eastern", 0.03, ("Zacapa", "ZACAPA")),
    ("Jutiapa", "Eastern", 0.04, ("Jutiapa", "JUTIAPA")),
]
DEPARTMENT_STORES = {"Guatemala": ("ST01", "ST02", "ST03", "ST04"), "Sacatepéquez": ("ST05",),
                     "Escuintla": ("ST07",), "Quetzaltenango": ("ST06",), "Chimaltenango": ("ST04", "ST05"),
                     "Huehuetenango": ("ST06",), "San Marcos": ("ST06",)}

# promo_code, name, type, discount, start, end, categories (None = all), channels (None = all)
PROMOTIONS = [
    ("RC24", "Back to school 2024", "Seasonal", 0.15, "2024-01-08", "2024-01-31", ("Toys & Kids", "Electronics"), None),
    ("LIQ24E", "January clearance 2024", "Clearance", 0.40, "2024-01-15", "2024-02-04", ("Fashion",), None),
    ("HOG24", "Home week 2024", "Category event", 0.20, "2024-03-11", "2024-03-24", ("Home & Kitchen",), None),
    ("MAMA24", "Mother's Day 2024", "Seasonal", 0.15, "2024-05-01", "2024-05-10", ("Beauty", "Fashion", "Home & Kitchen"), None),
    ("APP24", "App launch", "Channel launch", 0.10, "2024-06-03", "2024-06-16", None, ("APP",)),
    ("LIQ24J", "July clearance 2024", "Clearance", 0.40, "2024-07-08", "2024-07-28", ("Fashion",), None),
    ("BONO24", "Bono 14 tech days 2024", "Category event", 0.20, "2024-07-12", "2024-07-21", ("Electronics",), None),
    ("ANIV24", "Anniversary 2024", "Sitewide", 0.10, "2024-08-19", "2024-08-25", None, None),
    ("BF24", "Black Friday 2024", "Sitewide", 0.30, "2024-11-29", "2024-12-01", None, None),
    ("CYB24", "Cyber Monday 2024", "Sitewide", 0.20, "2024-12-02", "2024-12-02", None, ("WEB", "APP")),
    ("NAV24", "Christmas toys 2024", "Seasonal", 0.15, "2024-12-09", "2024-12-22", ("Toys & Kids",), None),
    ("RC25", "Back to school 2025", "Seasonal", 0.15, "2025-01-07", "2025-01-31", ("Toys & Kids", "Electronics"), None),
    ("LIQ25E", "January clearance 2025", "Clearance", 0.40, "2025-01-13", "2025-02-02", ("Fashion",), None),
    ("MKT25", "Marketplace launch", "Channel launch", 0.15, "2025-03-03", "2025-03-16", None, ("MARKETPLACE",)),
    ("HOG25", "Home week 2025", "Category event", 0.20, "2025-03-10", "2025-03-23", ("Home & Kitchen",), None),
    ("MAMA25", "Mother's Day 2025", "Seasonal", 0.15, "2025-05-01", "2025-05-10", ("Beauty", "Fashion", "Home & Kitchen"), None),
    ("LIQ25J", "July clearance 2025", "Clearance", 0.40, "2025-07-07", "2025-07-27", ("Fashion",), None),
    ("BONO25", "Bono 14 tech days 2025", "Category event", 0.20, "2025-07-11", "2025-07-20", ("Electronics",), None),
    ("ANIV25", "Anniversary 2025", "Sitewide", 0.10, "2025-08-18", "2025-08-24", None, None),
    ("BEA25", "Beauty week 2025", "Category event", 0.15, "2025-09-01", "2025-09-07", ("Beauty",), None),
    ("BF25", "Black Friday 2025", "Sitewide", 0.35, "2025-11-28", "2025-11-30", None, None),
    ("CYB25", "Cyber Monday 2025", "Sitewide", 0.20, "2025-12-01", "2025-12-01", None, ("WEB", "APP")),
    ("NAV25", "Christmas toys 2025", "Seasonal", 0.15, "2025-12-08", "2025-12-21", ("Toys & Kids",), None),
    ("RC26", "Back to school 2026", "Seasonal", 0.15, "2026-01-07", "2026-01-31", ("Toys & Kids", "Electronics"), None),
    ("LIQ26E", "January clearance 2026", "Clearance", 0.40, "2026-01-12", "2026-02-01", ("Fashion",), None),
    ("HOG26", "Home week 2026", "Category event", 0.20, "2026-03-09", "2026-03-22", ("Home & Kitchen",), None),
    ("BOT26", "Ceiba Botánica launch", "Product launch", 0.20, "2026-03-16", "2026-03-29", ("Beauty",), None),
    ("MAMA26", "Mother's Day 2026", "Seasonal", 0.15, "2026-05-01", "2026-05-10", ("Beauty", "Fashion", "Home & Kitchen"), None),
    ("LIQ26J", "July clearance 2026", "Clearance", 0.40, "2026-07-06", "2026-07-26", ("Fashion",), None),
    ("BONO26", "Bono 14 tech days 2026", "Category event", 0.20, "2026-07-10", "2026-07-19", ("Electronics",), None),
    ("ANIV26", "Anniversary 2026", "Sitewide", 0.10, "2026-08-17", "2026-08-23", None, None),
    ("BEA26", "Beauty week 2026", "Category event", 0.15, "2026-09-07", "2026-09-13", ("Beauty",), None),
]
DEEP_DISCOUNT = 0.30         # promotions this deep pull demand forward
PULL_FORWARD_DAYS = 21
PULL_FORWARD_FACTOR = 0.70

# How each channel's customers behave: orders per year, monthly churn probability, lines per order (extra)
CHANNEL_BEHAVIOR = {
    "STORE": (2.0, 0.040, 0.45),
    "WEB": (1.8, 0.050, 0.60),
    "APP": (3.0, 0.030, 0.65),
    "MARKETPLACE": (1.2, 0.095, 0.15),
}
# Category mix by channel (before seasonality, trends and promotions)
CATEGORY_MIX = {
    "STORE": {"Electronics": .17, "Home & Kitchen": .24, "Fashion": .22, "Beauty": .15, "Sports & Outdoors": .10, "Toys & Kids": .12},
    "WEB": {"Electronics": .28, "Home & Kitchen": .20, "Fashion": .17, "Beauty": .12, "Sports & Outdoors": .13, "Toys & Kids": .10},
    "APP": {"Electronics": .17, "Home & Kitchen": .15, "Fashion": .27, "Beauty": .23, "Sports & Outdoors": .08, "Toys & Kids": .10},
    "MARKETPLACE": {"Electronics": .40, "Home & Kitchen": .18, "Fashion": .13, "Beauty": .07, "Sports & Outdoors": .14, "Toys & Kids": .08},
}
RETURN_CHANNEL_FACTOR = {"STORE": 0.6, "WEB": 1.0, "APP": 1.0, "MARKETPLACE": 1.8}
MONTH_SEASON = {1: 0.95, 2: 0.82, 3: 0.88, 4: 0.88, 5: 1.05, 6: 0.88, 7: 0.97, 8: 0.95, 9: 1.0, 10: 0.93,
                11: 1.25, 12: 1.55}
CHANNEL_CODES = [c[0] for c in CHANNELS]
CHANNEL_LAUNCH = {c[0]: pd.Timestamp(c[3]) for c in CHANNELS}

# How the e-commerce platform's free-text channel field is typed (first one is the clean code)
CHANNEL_LABELS = {
    "WEB": ("WEB", "web", "Web", "Online", "Sitio Web"),
    "APP": ("APP", "app", "App Móvil", "mobile"),
    "MARKETPLACE": ("MARKETPLACE", "Marketplace", "marketplace ", "MKT"),
}
RETURN_REASONS = ("Wrong size", "Damaged", "Not as described", "Changed mind", "Defective")


# ------------------------------------------------------------------------------------------------------
# Platform-independent random helpers
# ------------------------------------------------------------------------------------------------------
# numpy's normal/exponential/choice samplers can differ in the last digit between processors (Apple Silicon
# vs Intel / Linux). These helpers draw only uniforms, a fixed number per call, and round every result,
# so the same seed gives the same data on every machine.

def _q(x: float, digits: int = 6) -> float:
    return round(float(x), digits)


def _u(rng: np.random.Generator) -> float:
    return float(rng.random())


def _normal(rng: np.random.Generator, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Box-Muller: always two uniforms."""
    u1, u2 = rng.random(2)
    z = math.sqrt(-2.0 * math.log(1.0 - u1)) * math.cos(2.0 * math.pi * u2)
    return _q(mu + sigma * z)


def _lognormal_mult(rng: np.random.Generator, sigma: float) -> float:
    """Multiplier with mean 1 and the given log-standard deviation."""
    return _q(math.exp(-sigma ** 2 / 2 + sigma * _normal(rng)))


def _poisson(rng: np.random.Generator, lam: float) -> int:
    """Inversion with a single uniform."""
    u, k = _u(rng), 0
    p = math.exp(-lam)
    cdf = p
    while u > cdf and k < 1000:
        k += 1
        p *= lam / k
        cdf += p
    return k


def _cumulative(weights) -> tuple[float, ...]:
    total, out = 0.0, []
    for w in weights:
        total += w
        out.append(total)
    return tuple(_q(c / total, 12) for c in out)


def _pick(rng: np.random.Generator, cum: tuple[float, ...]) -> int:
    """Index drawn from cumulative weights, with one uniform."""
    u = _u(rng)
    for i, c in enumerate(cum):
        if u < c:
            return i
    return len(cum) - 1


def _money(x: float) -> float:
    return round(float(x) + 1e-9, 2)


# ------------------------------------------------------------------------------------------------------
# Reference tables (catalog, stores, promotions)
# ------------------------------------------------------------------------------------------------------

def generate_products(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    n = 0
    for category, sub, lo, hi, count, brands in SUBCATEGORIES:
        margin = CATEGORIES[category][0]
        for j in range(count):
            n += 1
            price = round(lo * (hi / lo) ** _u(rng), -1) - 0.01
            brand = brands[j % len(brands)]
            private = brand.startswith("Ceiba")
            m = margin + (0.08 if private else 0.0) + _normal(rng, 0, 0.03)
            # most products existed before the extract; a few were launched during it
            launch = "2023-01-01" if _u(rng) < 0.88 else str((pd.Timestamp("2024-03-01")
                                                              + pd.Timedelta(days=int(_u(rng) * 800))).date())
            rows.append((f"SKU-{n:04d}", f"{brand} {sub.lower()} {j + 1}", category, sub, brand, private,
                         round(price, 2), _money(price * (1 - m)), launch, _q(0.4 + 1.6 * _u(rng), 4)))
    for name, price in BOTANICA_PRODUCTS:
        n += 1
        rows.append((f"SKU-{n:04d}", name, "Beauty", "Skincare", "Ceiba Botánica", True, price - 0.01,
                     _money(price * 0.30), BOTANICA_LAUNCH, 2.6))
    return pd.DataFrame(rows, columns=["sku", "product_name", "category", "subcategory", "brand", "is_private_label",
                                       "list_price", "unit_cost", "launch_date", "popularity"])


def sql_upper(text: str) -> str:
    """UPPER(TRIM(x)) as SQLite computes it: only ASCII letters change case ('Electrónica' -> 'ELECTRóNICA')."""
    return "".join(ch.upper() if ch.isascii() else ch for ch in text.strip())


def _alias_keys(text: str) -> list[str]:
    """Lookup keys for a raw spelling, typed as is or in capitals."""
    return sorted({sql_upper(text), sql_upper(text.upper())})


def generate_reference_tables(products: pd.DataFrame) -> dict[str, pd.DataFrame]:
    stores = pd.DataFrame(STORES, columns=["store_id", "store_name", "city", "department", "store_format", "opened_date"])
    channels = pd.DataFrame(CHANNELS, columns=["channel_id", "channel_name", "channel_type", "launch_date",
                                               "commission_rate"])
    promos = pd.DataFrame([{
        "promo_code": p[0], "promo_name": p[1], "promo_type": p[2], "discount_pct": p[3], "start_date": p[4],
        "end_date": p[5], "category_scope": "All" if p[6] is None else ", ".join(p[6]),
        "channel_scope": "All" if p[7] is None else ", ".join(p[7]),
    } for p in PROMOTIONS])
    promo_scope = pd.DataFrame([(p[0], c, ch) for p in PROMOTIONS for c in (p[6] or CATEGORIES)
                                for ch in (p[7] or CHANNEL_CODES)], columns=["promo_code", "category", "channel_id"])
    cat_map = pd.DataFrame([(key, cat) for cat, (_, _, es) in CATEGORIES.items() for raw in (cat, es)
                            for key in _alias_keys(raw)], columns=["raw_category", "category"]).drop_duplicates()
    ch_alias = pd.DataFrame([(key, code) for code, labels in CHANNEL_LABELS.items() for label in labels
                             for key in _alias_keys(label)], columns=["raw_label", "channel_id"]).drop_duplicates()
    dep_alias = pd.DataFrame([(key, dep, region) for dep, region, _, aliases in DEPARTMENTS for alias in aliases
                              for key in _alias_keys(alias)],
                             columns=["raw_department", "department", "region"]).drop_duplicates()
    return {"stores": stores, "channels": channels, "promotions": promos, "promotion_scope": promo_scope,
            "category_map": cat_map, "channel_aliases": ch_alias, "department_aliases": dep_alias}


# ------------------------------------------------------------------------------------------------------
# The demand model
# ------------------------------------------------------------------------------------------------------

class Calendar:
    """Day-level demand intensity, active promotions and category mix, precomputed for the whole period."""

    def __init__(self, start: str, end: str, products: pd.DataFrame):
        self.days = pd.date_range(start, end, freq="D")
        self.index = {d: i for i, d in enumerate(self.days)}
        self.promos_by_day: list[list[tuple]] = [[] for _ in self.days]
        self.pull_forward: list[set] = [set() for _ in self.days]  # categories in a post-promo dip
        for p in PROMOTIONS:
            s, e = pd.Timestamp(p[4]), pd.Timestamp(p[5])
            for i, d in enumerate(self.days):
                if s <= d <= e:
                    self.promos_by_day[i].append(p)
                elif p[3] >= DEEP_DISCOUNT and e < d <= e + pd.Timedelta(days=PULL_FORWARD_DAYS):
                    self.pull_forward[i].update(p[6] or CATEGORIES)
        self.intensity = [self._intensity(i, d) for i, d in enumerate(self.days)]
        self.months = sorted({(d.year, d.month) for d in self.days})
        self.month_days: dict[tuple, list[int]] = defaultdict(list)
        for i, d in enumerate(self.days):
            self.month_days[(d.year, d.month)].append(i)
        self.products = products
        self.product_rows = products.to_dict("records")
        self.cats = list(CATEGORIES)
        self.month_sum = {ym: sum(self.intensity[i] for i in ix) for ym, ix in self.month_days.items()}
        self.month_cum = {ym: _cumulative(self.intensity[i] for i in ix) for ym, ix in self.month_days.items()}

    def _intensity(self, i: int, d: pd.Timestamp) -> float:
        w = MONTH_SEASON[d.month]
        nov30 = pd.Timestamp(year=d.year, month=11, day=30)
        black_friday = nov30 - pd.Timedelta(days=(nov30.day_of_week - 4) % 7)
        if black_friday <= d <= black_friday + pd.Timedelta(days=2):  # Friday to Sunday
            w *= 3.0
        if d.month == 12 and 14 <= d.day <= 23:
            w *= 1.6
        if d.month == 5 and d.day <= 10:
            w *= 1.35
        if d.month == 7 and 14 <= d.day <= 20:  # Bono 14
            w *= 1.3
        if d.day in (15, 16) or d.is_month_end or d.day == 1:  # paydays
            w *= 1.15
        if d.day_of_week >= 5:
            w *= 1.1
        for p in self.promos_by_day[i]:
            if p[6] is None:  # sitewide promotions bring more orders, not just a different mix
                w *= 1 + 1.5 * p[3]
            else:  # category promotions bring some extra orders too (and shift the mix, below)
                w *= 1 + 1.2 * p[3] * len(p[6]) / len(CATEGORIES)
        if self.pull_forward[i] and len(self.pull_forward[i]) == len(CATEGORIES):
            w *= 0.85
        return _q(w)

    @lru_cache(maxsize=None)
    def channel_mix(self, day_i: int) -> tuple[tuple[str, ...], tuple[float, ...]]:
        """Which channels exist on a day and how new customers split between them."""
        d = self.days[day_i]
        weights = {}
        for ch in CHANNEL_CODES:
            if d < CHANNEL_LAUNCH[ch]:
                continue
            months = (d - CHANNEL_LAUNCH[ch]).days / 30.4
            weights[ch] = {"STORE": 0.48, "WEB": 0.34, "APP": min(0.36, 0.05 + 0.018 * months),
                           "MARKETPLACE": min(0.20, 0.04 + 0.02 * months)}[ch]
        names = tuple(weights)
        return names, _cumulative(weights[n] for n in names)

    @lru_cache(maxsize=None)
    def category_cum(self, channel: str, day_i: int) -> tuple[float, ...]:
        d = self.days[day_i]
        w = []
        for cat in self.cats:
            x = CATEGORY_MIX[channel][cat]
            if cat == "Beauty" and d >= pd.Timestamp(BOTANICA_LAUNCH):
                x *= 1 + min(0.75, 0.75 * (d - pd.Timestamp(BOTANICA_LAUNCH)).days / 90)
            if cat == "Electronics" and d.year == 2026:
                x *= 0.92
            if cat == "Toys & Kids":
                x *= {12: 2.0, 1: 1.5}.get(d.month, 1.0)
            if cat == "Sports & Outdoors" and d.month == 1:
                x *= 1.3
            for p in self.promos_by_day[day_i]:
                if (p[6] is None or cat in p[6]) and (p[7] is None or channel in p[7]) and p[6] is not None:
                    x *= 1 + 2.2 * p[3]
            if cat in self.pull_forward[day_i]:
                x *= PULL_FORWARD_FACTOR
            w.append(x)
        return _cumulative(w)

    @lru_cache(maxsize=None)
    def product_cum(self, category: str, year: int, month: int) -> tuple[tuple[int, ...], tuple[float, ...]]:
        month_end = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
        p = self.products
        ok = p[(p["category"] == category) & (pd.to_datetime(p["launch_date"]) <= month_end)]
        return tuple(ok.index), _cumulative(ok["popularity"])

    def promo_for(self, day_i: int, category: str, channel: str) -> tuple | None:
        """Deepest promotion that applies to a category in a channel on a day."""
        best = None
        for p in self.promos_by_day[day_i]:
            if (p[6] is None or category in p[6]) and (p[7] is None or channel in p[7]):
                if best is None or p[3] > best[3]:
                    best = p
        return best


def _order_lines(rng, cal: Calendar, day_i: int, channel: str) -> list[dict]:
    d = cal.days[day_i]
    n_lines = 1 + _poisson(rng, CHANNEL_BEHAVIOR[channel][2])
    lines = []
    for _ in range(n_lines):
        cat = cal.cats[_pick(rng, cal.category_cum(channel, day_i))]
        idx, cum = cal.product_cum(cat, d.year, d.month)
        prod = cal.product_rows[idx[_pick(rng, cum)]]
        u = _u(rng)
        qty = 1 if u < (0.70 if cat in ("Beauty", "Toys & Kids") else 0.82) else (2 if u < 0.95 else 3)
        promo = cal.promo_for(day_i, cat, channel)
        disc = promo[3] if promo else 0.0
        lines.append({"sku": prod["sku"], "category": cat, "qty": qty, "list_price": float(prod["list_price"]),
                      "discount": _money(qty * float(prod["list_price"]) * disc), "promo": promo[0] if promo else None,
                      "returned": _u(rng) < CATEGORIES[cat][1] * RETURN_CHANNEL_FACTOR[channel] * (1.3 if disc >= 0.3 else 1.0),
                      "return_days": 3 + int(_u(rng) * 27), "reason": RETURN_REASONS[int(_u(rng) * len(RETURN_REASONS))]})
    return lines


def _order_time(rng, channel: str) -> int:
    """Minute of the day (local time): stores 10:00-21:00, online peaks in the evening."""
    if channel == "STORE":
        return int((10 + 11 * _u(rng) ** 1.1) * 60)
    u = _u(rng)
    hour = 19 + 4.9 * _u(rng) if u < 0.45 else 7 + 12 * _u(rng) if u < 0.92 else 4.9 * _u(rng)
    return int(hour * 60)


def simulate_customers(seed: int, cal: Calendar) -> tuple[list[dict], list[dict]]:
    """New customers month by month; each one buys at a personal rate until they churn."""
    customers, orders = [], []
    cust_n = 0
    dep_cum = _cumulative(d[2] for d in DEPARTMENTS)
    total_months = len(cal.months)
    for m_i, (year, month) in enumerate(cal.months):
        rng_m = np.random.default_rng([seed, 1, year, month])
        days = cal.month_days[(year, month)]
        n_new = _poisson(rng_m, 520 * 1.022 ** m_i * MONTH_SEASON[month])
        day_cum = _cumulative(cal.intensity[i] for i in days)
        for _ in range(n_new):
            cust_n += 1
            cid = f"C{cust_n:06d}"
            rng = np.random.default_rng([seed, 2, cust_n])
            first_i = days[_pick(rng, day_cum)]
            dep = DEPARTMENTS[_pick(rng, dep_cum)]
            names, cum = cal.channel_mix(first_i)
            if dep[0] not in DEPARTMENT_STORES:  # far from a store: mostly online
                weights = [(cum[i] - (cum[i - 1] if i else 0)) * (0.25 if n == "STORE" else 1.0) for i, n in enumerate(names)]
                cum = _cumulative(weights)
            acq = names[_pick(rng, cum)]
            home_stores = DEPARTMENT_STORES.get(dep[0], ("ST01", "ST06"))
            home = home_stores[int(_u(rng) * len(home_stores))]
            rate_year, churn, _ = CHANNEL_BEHAVIOR[acq]
            rate_year *= _lognormal_mult(rng, 0.6)
            first_promo = cal.promo_for(first_i, "Electronics", acq)
            if first_promo and first_promo[3] >= DEEP_DISCOUNT:
                churn *= 1.6  # bargain hunters acquired on Black Friday
            customers.append({"customer_id": cid, "signup_day": first_i, "department": dep, "acq": acq,
                              "home_store": home, "rng_state": None})
            # first order, then month by month until churn or the end of the extract
            order_days = [first_i]
            cur_month = m_i
            alive = True
            while alive and cur_month < total_months:
                ym = cal.months[cur_month]
                if cur_month == m_i:  # the rest of the first month
                    mdays = [i for i in cal.month_days[ym] if i > first_i]
                    msum, mcum = sum(cal.intensity[i] for i in mdays), None
                else:
                    mdays, msum, mcum = cal.month_days[ym], cal.month_sum[ym], cal.month_cum[ym]
                if mdays:
                    k = _poisson(rng, _q(rate_year / 12 * msum / 30.4))
                    if k:
                        mcum = mcum or _cumulative(cal.intensity[i] for i in mdays)
                        order_days += sorted(mdays[_pick(rng, mcum)] for _ in range(k))
                    if _u(rng) < churn:
                        alive = False
                cur_month += 1
            for j, day_i in enumerate(order_days):
                if j == 0:
                    ch = acq
                else:
                    names, cum = cal.channel_mix(day_i)
                    if _u(rng) < 0.68 or len(names) == 1:
                        ch = acq
                    else:
                        others = [n for n in names if n != acq]
                        ch = others[int(_u(rng) * len(others))]
                    if ch == "STORE" and dep[0] not in DEPARTMENT_STORES and _u(rng) < 0.6:
                        ch = "WEB"
                orders.append({"customer_id": cid, "day": day_i, "minute": _order_time(rng, ch), "channel": ch,
                               "store_id": home if ch == "STORE" else None, "lines": _order_lines(rng, cal, day_i, ch),
                               "cancelled": ch != "STORE" and _u(rng) < 0.04})
    return customers, orders


def simulate_guest_tickets(seed: int, cal: Calendar) -> list[dict]:
    """Store shoppers who do not scan a loyalty card (no customer id)."""
    orders = []
    for i, d in enumerate(cal.days):
        for store_id, *_ in STORES:
            rng = np.random.default_rng([seed, 3, d.toordinal(), int(store_id[2:])])
            growth = 1 + 0.004 * i / 30.4
            for _ in range(_poisson(rng, _q(9.5 * STORE_WEIGHT[store_id] * cal.intensity[i] * growth))):
                orders.append({"customer_id": None, "day": i, "minute": _order_time(rng, "STORE"), "channel": "STORE",
                               "store_id": store_id, "lines": _order_lines(rng, cal, i, "STORE"), "cancelled": False})
    return orders


# ------------------------------------------------------------------------------------------------------
# Source-system extracts (with the problems real extracts have)
# ------------------------------------------------------------------------------------------------------

def _fmt_pos_ts(day: pd.Timestamp, minute: int) -> str:
    return f"{day:%d/%m/%Y} {minute // 60:02d}:{minute % 60:02d}"


def _q_text(x: float) -> str:
    return f"Q{x:,.2f}"


def build_extracts(seed: int, cal: Calendar, products: pd.DataFrame, customers: list[dict],
                   orders: list[dict]) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng([seed, 4])
    end = cal.days[-1]
    orders = sorted(orders, key=lambda o: (o["day"], o["minute"], o["customer_id"] or "", o["store_id"] or ""))

    # CRM: some customers opened a second account with the same e-mail (typed differently)
    crm, dup_of = [], {}
    n_dup = 0
    for c in customers:
        dep_alias = c["department"][3][int(_u(rng) * len(c["department"][3]))] if _u(rng) < 0.35 else c["department"][0]
        signup = cal.days[c["signup_day"]]
        signup_txt = f"{signup:%d/%m/%Y}" if _u(rng) < 0.3 else f"{signup:%Y-%m-%d}"
        email = f"{c['customer_id'].lower()}@example.com"
        crm.append((c["customer_id"], email, signup_txt, dep_alias, c["home_store"]))
        if _u(rng) < 0.03 and c["signup_day"] < len(cal.days) - 60:
            n_dup += 1
            dup = f"C9{n_dup:05d}"
            dup_day = cal.days[min(len(cal.days) - 1, c["signup_day"] + 20 + int(_u(rng) * 300))]
            dup_of[c["customer_id"]] = (dup, c["signup_day"] + (dup_day - signup).days)
            messy_email = (" " + email.upper()) if _u(rng) < 0.5 else email.capitalize() + " "
            crm.append((dup, messy_email, f"{dup_day:%Y-%m-%d}", dep_alias, c["home_store"]))
    for k in range(1, 6):  # QA accounts used to test checkout in production
        crm.append((f"QA{k:04d}", f"qa{k}@casaceiba-qa.test", "2024-01-02", "Guatemala", "ST01"))
    raw_customers = pd.DataFrame(crm, columns=["customer_id", "email", "signup_date", "department", "home_store"])

    pos, ecom, items, ecom_returns = [], [], [], []
    seq = defaultdict(int)
    web_n = 0
    for o in orders:
        day = cal.days[o["day"]]
        cid = o["customer_id"]
        if cid in dup_of and o["day"] >= dup_of[cid][1] and _u(rng) < 0.5:
            cid = dup_of[cid][0]  # this order was placed with the duplicate account
        if o["channel"] == "STORE":
            seq[o["store_id"]] += 1
            ticket = f"T-{o['store_id']}-{seq[o['store_id']]:07d}"
            ts = _fmt_pos_ts(day, o["minute"])
            for ln, line in enumerate(o["lines"], 1):
                sku = line["sku"].lower() if _u(rng) < 0.03 else line["sku"]
                unit = line["list_price"]
                pos.append((ticket, ln, o["store_id"], ts, cid or "", sku, line["qty"], _q_text(unit),
                            _q_text(line["discount"]), line["promo"] or "", ""))
                ret_day = o["day"] + line["return_days"]
                if line["returned"] and ret_day < len(cal.days):
                    seq[o["store_id"]] += 1
                    rticket = f"T-{o['store_id']}-{seq[o['store_id']]:07d}"
                    pos.append((rticket, 1, o["store_id"], _fmt_pos_ts(cal.days[ret_day], 11 * 60 + int(_u(rng) * 480)),
                                cid or "", line["sku"], -line["qty"], _q_text(unit), _q_text(-line["discount"]),
                                line["promo"] or "", f"{ticket}/{ln}"))
            continue
        web_n += 1
        order_id = f"W{1_000_000 + web_n}"
        local = day + pd.Timedelta(minutes=o["minute"])
        utc = local - pd.Timedelta(hours=LOCAL_UTC_OFFSET_HOURS)
        created = f"{utc:%Y-%m-%dT%H:%M:%S}Z"
        if _u(rng) < 0.0004:  # device clock error
            created = f"20{39 + int(_u(rng) * 60)}-{utc:%m-%dT%H:%M:%S}Z"
        labels = CHANNEL_LABELS[o["channel"]]
        label = labels[0] if _u(rng) < 0.6 else labels[int(_u(rng) * len(labels))]
        status = "cancelled" if o["cancelled"] else "completed"
        ingested = utc + pd.Timedelta(minutes=5 + int(_u(rng) * 60))
        if _u(rng) < 0.015:  # the integration re-sent the order; the first copy was still "pending"
            ecom.append((order_id, cid, label, created, "pending", f"{ingested:%Y-%m-%d %H:%M:%S}"))
            for ln, line in enumerate(o["lines"], 1):
                items.append((order_id, ln, line["sku"], line["qty"], line["list_price"], line["discount"],
                              line["promo"] or None, f"{ingested:%Y-%m-%d %H:%M:%S}"))
            ingested += pd.Timedelta(hours=2 + int(_u(rng) * 30))
        ecom.append((order_id, cid, label, created, status, f"{ingested:%Y-%m-%d %H:%M:%S}"))
        for ln, line in enumerate(o["lines"], 1):
            sku = f" {line['sku']}" if _u(rng) < 0.02 else line["sku"]
            items.append((order_id, ln, sku, line["qty"], line["list_price"], line["discount"], line["promo"] or None,
                          f"{ingested:%Y-%m-%d %H:%M:%S}"))
            ret_day = o["day"] + line["return_days"]
            if line["returned"] and not o["cancelled"] and ret_day < len(cal.days):
                refund = _money(line["qty"] * line["list_price"] - line["discount"])
                ecom_returns.append((f"R-{order_id}-{ln}", order_id, ln, line["sku"], line["qty"],
                                     f"{cal.days[ret_day]:%Y-%m-%d}", refund, line["reason"]))
    # QA test orders and returns that cannot be matched to any sale
    qa_days = [int(_u(rng) * len(cal.days)) for _ in range(60)]
    for k, day_i in enumerate(sorted(qa_days)):
        web_n += 1
        order_id = f"W{1_000_000 + web_n}"
        created = f"{cal.days[day_i] + pd.Timedelta(hours=15):%Y-%m-%dT%H:%M:%S}Z"
        ecom.append((order_id, f"QA{1 + k % 5:04d}", "WEB", created, "completed", created.replace("T", " ")[:19]))
        items.append((order_id, 1, products["sku"].iloc[int(_u(rng) * len(products))], 1, 1.00, 0.0, None,
                      created.replace("T", " ")[:19]))
    for k in range(max(3, len(ecom_returns) // 250)):  # about 0.4% of returns
        ecom_returns.append((f"R-ORPHAN-{k:03d}", f"W{900_000 + k}", 1, products["sku"].iloc[k], 1,
                             f"{cal.days[int(_u(rng) * len(cal.days))]:%Y-%m-%d}", 150.0, "Changed mind"))

    # Catalog: category names in Spanish or English, a few unit costs missing
    catalog = products[["sku", "product_name", "category", "subcategory", "brand", "is_private_label", "list_price",
                        "unit_cost", "launch_date"]].copy()
    spanish = np.array([_u(rng) < 0.4 for _ in range(len(catalog))])
    catalog.loc[spanish, "category"] = catalog.loc[spanish, "category"].map(lambda c: CATEGORIES[c][2])
    catalog["category"] = [c.upper() if _u(rng) < 0.1 else c for c in catalog["category"]]
    missing = [int(_u(rng) * len(catalog)) for _ in range(6)]
    catalog.loc[catalog.index[missing], "unit_cost"] = np.nan
    catalog["is_private_label"] = catalog["is_private_label"].map({True: "Y", False: "N"})

    return {
        "pos_ticket_lines": pd.DataFrame(pos, columns=["ticket_id", "line_no", "store_code", "ticket_ts", "loyalty_card",
                                                       "sku", "quantity", "unit_price", "discount", "promo_code",
                                                       "original_ticket_line"]),
        "ecom_orders": pd.DataFrame(ecom, columns=["order_id", "customer_id", "channel_label", "created_at", "status",
                                                   "ingested_at"]),
        "ecom_order_items": pd.DataFrame(items, columns=["order_id", "line_no", "sku", "quantity", "unit_price",
                                                         "discount_amount", "promo_code", "ingested_at"]),
        "ecom_returns": pd.DataFrame(ecom_returns, columns=["return_id", "order_id", "line_no", "sku", "quantity",
                                                            "return_date", "refund_amount", "reason"]),
        "crm_customers": raw_customers,
        "catalog_products": catalog,
    }


def generate_all(seed: int = 42, start: str = START_DATE, end: str = END_DATE) -> tuple[dict, dict]:
    """Return (raw extracts, reference tables)."""
    products = generate_products(np.random.default_rng([seed, 0]))
    cal = Calendar(start, end, products)
    customers, orders = simulate_customers(seed, cal)
    orders += simulate_guest_tickets(seed, cal)
    raw = build_extracts(seed, cal, products, customers, orders)
    ref = generate_reference_tables(products)
    return raw, ref
