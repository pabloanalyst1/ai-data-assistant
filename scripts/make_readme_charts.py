"""Draw the README charts from the warehouse (run the pipeline first)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import PipelineConfig  # noqa: E402

OUT = ROOT / "docs" / "images"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, MUTED, GRID, GRAY = "#1f1f1f", "#6b6b6b", "#e6e6e6", "#b8b8b4"
# channel colors follow the entity, in a fixed order (validated for color-vision deficiency)
CHANNEL_COLORS = {"STORE": BLUE, "WEB": ORANGE, "APP": AQUA, "MARKETPLACE": YELLOW}
CHANNEL_NAMES = {"STORE": "Stores", "WEB": "Web", "APP": "App", "MARKETPLACE": "Marketplace"}

plt.rcParams.update({
    "font.size": 11, "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.titlesize": 13, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.axisbelow": True, "figure.dpi": 150, "savefig.bbox": "tight",
})


def query(sql: str) -> pd.DataFrame:
    with sqlite3.connect(PipelineConfig().db_path) as conn:
        return pd.read_sql_query(sql, conn)


def channel_trend() -> None:
    d = query("SELECT month_start, channel_id, SUM(net_sales) / 1e6 AS net_sales FROM mart_sales_monthly GROUP BY 1, 2")
    d["month"] = pd.to_datetime(d["month_start"])
    fig, ax = plt.subplots(figsize=(9.5, 4.4))
    nudge = {"WEB": 7, "APP": -7}  # their last values almost coincide: keep the labels apart
    for ch, color in CHANNEL_COLORS.items():
        s = d[d["channel_id"] == ch]
        ax.plot(s["month"], s["net_sales"], color=color, linewidth=2)
        ax.annotate(CHANNEL_NAMES[ch], (s["month"].iloc[-1], s["net_sales"].iloc[-1]), xytext=(6, nudge.get(ch, 0)),
                    textcoords="offset points", va="center", fontsize=10, color=INK)
    ax.set_title("Monthly net sales by channel: the app and the marketplace drive the growth")
    ax.set_ylabel("Net sales (GTQ millions)")
    ax.set_ylim(0, None)
    ax.grid(axis="x", visible=False)
    ax.legend([plt.Line2D([0], [0], color=c, linewidth=2) for c in CHANNEL_COLORS.values()],
              CHANNEL_NAMES.values(), loc="upper center", bbox_to_anchor=(0.5, -0.08), frameon=False, fontsize=9,
              ncol=4)
    fig.savefig(OUT / "channel_trend.png")
    plt.close(fig)


def category_growth() -> None:
    d = query("SELECT category, yoy_growth FROM mart_category_quarterly WHERE year_quarter = '2026-Q3' "
              "ORDER BY yoy_growth")
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.barh(d["category"], d["yoy_growth"], color=[BLUE if c == "Beauty" else GRAY for c in d["category"]], height=0.6)
    for y, v in enumerate(d["yoy_growth"]):
        ax.text(v + 0.01, y, f"{v:+.0%}", va="center", fontsize=10, color=INK)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_xlim(0, d["yoy_growth"].max() * 1.15)
    ax.set_title("Net sales growth by category, 2026-Q3 vs 2025-Q3")
    ax.grid(axis="y", visible=False)
    fig.savefig(OUT / "category_growth.png")
    plt.close(fig)


def retention_curves() -> None:
    d = query("""SELECT acquisition_channel, months_since_first,
                        1.0 * SUM(active_customers) / SUM(cohort_customers) AS retention
                 FROM mart_customer_cohorts WHERE cohort_month <= '2025-10-01' AND months_since_first BETWEEN 1 AND 12
                 GROUP BY 1, 2""")
    rep = query("SELECT acquisition_channel, 1.0 * SUM(repeat_customers) / SUM(eligible_customers) AS rate "
                "FROM mart_repurchase GROUP BY 1").set_index("acquisition_channel")["rate"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [1, 1.5]})
    order = rep.sort_values().index
    ax1.barh([CHANNEL_NAMES[c] for c in order], rep[order], color=[CHANNEL_COLORS[c] for c in order], height=0.6)
    for y, v in enumerate(rep[order]):
        ax1.text(v + 0.01, y, f"{v:.0%}", va="center", fontsize=10, color=INK)
    ax1.set_xlim(0, 0.65)
    ax1.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax1.grid(axis="y", visible=False)
    ax1.set_title("Second order within 90 days")
    for ch, color in CHANNEL_COLORS.items():
        s = d[d["acquisition_channel"] == ch]
        ax2.plot(s["months_since_first"], s["retention"], color=color, linewidth=2, marker="o", markersize=4)
        ax2.annotate(CHANNEL_NAMES[ch], (12, s["retention"].iloc[-1]), xytext=(6, 0), textcoords="offset points",
                     va="center", fontsize=10, color=INK)
    ax2.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax2.set_ylim(0, None)
    ax2.set_xticks(range(1, 13))
    ax2.set_xlabel("Months after the first order")
    ax2.grid(axis="x", visible=False)
    ax2.set_title("Share of a cohort buying in month k")
    fig.suptitle("Customers acquired in the app come back most; marketplace customers rarely do",
                 x=0.01, ha="left", fontweight="bold", fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "repurchase_retention.png")
    plt.close(fig)


def promo_margin() -> None:
    d = query("SELECT promo_name, discount_band, incremental_gross_margin / 1e3 AS igm FROM mart_promo_performance "
              "ORDER BY incremental_gross_margin")
    colors = {"10-19%": BLUE, "20-29%": ORANGE, "30% or more": AQUA}
    fig, ax = plt.subplots(figsize=(9, 8.5))
    ax.barh(d["promo_name"], d["igm"], color=[colors[b] for b in d["discount_band"]], height=0.7)
    ax.axvline(0, color=INK, linewidth=0.8)
    for y, v in enumerate(d["igm"]):
        ax.text(v + (3 if v >= 0 else -3), y, f"{v:+.0f}k", va="center", ha="left" if v >= 0 else "right",
                fontsize=8, color=MUTED)
    ax.set_xlim(d["igm"].min() * 1.25, d["igm"].max() * 2.2)
    ax.set_xlabel("Incremental gross margin vs baseline (GTQ thousands)")
    ax.set_title("Only 5 of 30 promotions earned margin")
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", labelsize=9)
    ax.legend([plt.Rectangle((0, 0), 1, 1, color=c) for c in colors.values()], [f"{k} off" for k in colors],
              title="Discount", loc="lower right", frameon=False, fontsize=9)
    fig.savefig(OUT / "promo_margin.png")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for chart in (channel_trend, category_growth, retention_curves, promo_margin):
        chart()
    print(f"Charts written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
