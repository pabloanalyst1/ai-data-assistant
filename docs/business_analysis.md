# Business analysis: questions and answers

Each question is answered by one query in [`sql/analysis/`](../sql/analysis). Results come from the default run (`python run_pipeline.py`, seed 42). Regenerate this file with `python scripts/run_case_study.py`.

1. [How are net sales growing year to date, and which channels drive the growth?](#q1)
2. [Which category grew most last quarter (2026-Q3 vs 2025-Q3), and at what margin?](#q2)
3. [How much of Beauty's growth comes from the new private-label line, Ceiba Botánica?](#q3)
4. [Which channels are really profitable once returns and marketplace commissions are counted (2026 YTD)?](#q4)
5. [What is the 90-day repurchase rate by acquisition channel, and is it improving?](#q5)
6. [How many customers are still buying 1, 3, 6 and 12 months after their first order, by acquisition channel?](#q6)
7. [Do promotions pay? Sales lift, margin and after-effect by discount depth.](#q7)
8. [Which promotions earned margin and which ones cost it?](#q8)
9. [Black Friday 2024 (30% off) vs 2025 (35% off): did the deeper discount pay?](#q9)
10. [How are the stores doing this year, and how much of their sales can we tie to a known customer?](#q10)

## <a id="q1"></a>1. How are net sales growing year to date, and which channels drive the growth?

[`q01_sales_by_year_and_channel.sql`](../sql/analysis/q01_sales_by_year_and_channel.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: How are net sales growing year to date, and which channels drive the growth?
-- Technique: same-period comparison (Jan-Oct each year), conditional aggregation, share of growth with a window function.
WITH ytd AS (
    SELECT o.channel_id,
           SUM(CASE WHEN o.order_date BETWEEN '2025-01-01' AND '2025-10-31' THEN o.net_sales END) AS ytd_2025,
           SUM(CASE WHEN o.order_date BETWEEN '2026-01-01' AND '2026-10-31' THEN o.net_sales END) AS ytd_2026
    FROM fct_orders o
    GROUP BY o.channel_id
)
SELECT
    channel_id,
    ROUND(ytd_2025 / 1e6, 2)                                                AS net_sales_2025_ytd_m,
    ROUND(ytd_2026 / 1e6, 2)                                                AS net_sales_2026_ytd_m,
    ROUND(100.0 * (ytd_2026 / ytd_2025 - 1), 1)                             AS growth_pct,
    ROUND(100.0 * ytd_2026 / SUM(ytd_2026) OVER (), 1)                      AS share_2026_pct,
    ROUND(100.0 * (ytd_2026 - ytd_2025) / SUM(ytd_2026 - ytd_2025) OVER (), 1) AS share_of_growth_pct
FROM ytd
ORDER BY ytd_2026 DESC;
```

</details>

| channel_id | net_sales_2025_ytd_m | net_sales_2026_ytd_m | growth_pct | share_2026_pct | share_of_growth_pct |
| --- | --- | --- | --- | --- | --- |
| STORE | 22.42 | 24.29 | 8.3 | 56.2 | 22.5 |
| WEB | 6.59 | 8.22 | 24.7 | 19.0 | 19.7 |
| APP | 4.32 | 7.4 | 71.4 | 17.1 | 37.3 |
| MARKETPLACE | 1.64 | 3.34 | 103.8 | 7.7 | 20.6 |

## <a id="q2"></a>2. Which category grew most last quarter (2026-Q3 vs 2025-Q3), and at what margin?

[`q02_category_growth_last_quarter.sql`](../sql/analysis/q02_category_growth_last_quarter.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: Which category grew most last quarter (2026-Q3 vs 2025-Q3), and at what margin?
-- Technique: year-over-year growth from the quarterly mart (LAG over 4 quarters), margin as a ratio of sums.
SELECT
    category,
    ROUND(net_sales_same_quarter_last_year / 1e6, 2)                        AS net_sales_2025_q3_m,
    ROUND(net_sales / 1e6, 2)                                               AS net_sales_2026_q3_m,
    ROUND(100.0 * yoy_growth, 1)                                            AS yoy_growth_pct,
    ROUND(100.0 * qoq_growth, 1)                                            AS qoq_growth_pct,
    ROUND(100.0 * gross_margin / net_sales, 1)                              AS gross_margin_pct
FROM mart_category_quarterly
WHERE year_quarter = '2026-Q3'
ORDER BY yoy_growth DESC;
```

</details>

| category | net_sales_2025_q3_m | net_sales_2026_q3_m | yoy_growth_pct | qoq_growth_pct | gross_margin_pct |
| --- | --- | --- | --- | --- | --- |
| Beauty | 0.79 | 1.45 | 84.2 | 15.5 | 61.8 |
| Home & Kitchen | 2.14 | 2.49 | 16.5 | 5.8 | 42.0 |
| Fashion | 1.71 | 1.98 | 15.7 | -7.2 | 50.0 |
| Toys & Kids | 0.53 | 0.61 | 14.4 | -1.7 | 41.7 |
| Sports & Outdoors | 2.23 | 2.45 | 9.9 | 1.1 | 37.0 |
| Electronics | 4.19 | 4.57 | 9.0 | 3.0 | 16.6 |

## <a id="q3"></a>3. How much of Beauty's growth comes from the new private-label line, Ceiba Botánica?

[`q03_beauty_growth_drivers.sql`](../sql/analysis/q03_beauty_growth_drivers.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: How much of Beauty's growth comes from the new private-label line, Ceiba Botánica?
-- Technique: growth decomposition by brand group, quarter by quarter.
WITH q AS (
    SELECT d.year_quarter,
           SUM(CASE WHEN l.brand = 'Ceiba Botánica' THEN l.net_sales ELSE 0 END) AS botanica,
           SUM(CASE WHEN l.brand <> 'Ceiba Botánica' THEN l.net_sales ELSE 0 END) AS other_brands,
           SUM(l.gross_margin) AS gross_margin, SUM(l.net_sales) AS net_sales
    FROM fct_order_lines l
    JOIN dim_date d ON d.date = l.order_date
    WHERE l.category = 'Beauty' AND d.year_quarter BETWEEN '2025-Q1' AND '2026-Q3'
    GROUP BY d.year_quarter
)
SELECT
    year_quarter,
    ROUND(other_brands / 1e3, 0)                                            AS other_brands_k,
    ROUND(botanica / 1e3, 0)                                                AS ceiba_botanica_k,
    ROUND(100.0 * botanica / net_sales, 1)                                  AS botanica_share_pct,
    ROUND(100.0 * (other_brands / LAG(other_brands, 4) OVER (ORDER BY year_quarter) - 1), 1) AS other_brands_yoy_pct,
    ROUND(100.0 * gross_margin / net_sales, 1)                              AS beauty_margin_pct
FROM q
ORDER BY year_quarter;
```

</details>

| year_quarter | other_brands_k | ceiba_botanica_k | botanica_share_pct | other_brands_yoy_pct | beauty_margin_pct |
| --- | --- | --- | --- | --- | --- |
| 2025-Q1 | 578.0 | 0.0 | 0.0 |  | 59.4 |
| 2025-Q2 | 662.0 | 0.0 | 0.0 |  | 58.5 |
| 2025-Q3 | 789.0 | 0.0 | 0.0 |  | 58.5 |
| 2025-Q4 | 1,150 | 0.0 | 0.0 |  | 57.2 |
| 2026-Q1 | 696.0 | 78.0 | 10.1 | 20.4 | 59.0 |
| 2026-Q2 | 901.0 | 357.0 | 28.3 | 36.2 | 61.5 |
| 2026-Q3 | 1,019 | 434.0 | 29.9 | 29.2 | 61.8 |

## <a id="q4"></a>4. Which channels are really profitable once returns and marketplace commissions are counted (2026 YTD)?

[`q04_channel_profitability.sql`](../sql/analysis/q04_channel_profitability.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: Which channels are really profitable once returns and marketplace commissions are counted (2026 YTD)?
-- Technique: margin waterfall per channel: gross sales -> discounts -> returns -> cost -> commission.
SELECT
    channel_id,
    ROUND(SUM(net_sales) / 1e6, 2)                                          AS net_sales_m,
    ROUND(100.0 * SUM(discount_amount) / SUM(gross_sales), 1)               AS discount_rate_pct,
    ROUND(100.0 * SUM(returned_amount) / SUM(gross_sales - discount_amount), 1) AS return_rate_pct,
    ROUND(100.0 * SUM(gross_margin) / SUM(net_sales), 1)                    AS gross_margin_pct,
    ROUND(100.0 * SUM(commission) / SUM(net_sales), 1)                      AS commission_pct,
    ROUND(100.0 * SUM(contribution_margin) / SUM(net_sales), 1)             AS contribution_margin_pct,
    ROUND(SUM(net_sales) / COUNT(DISTINCT order_id), 0)                     AS avg_order_value
FROM fct_order_lines
WHERE order_date BETWEEN '2026-01-01' AND '2026-10-31'
GROUP BY channel_id
ORDER BY contribution_margin_pct DESC;
```

</details>

| channel_id | net_sales_m | discount_rate_pct | return_rate_pct | gross_margin_pct | commission_pct | contribution_margin_pct | avg_order_value |
| --- | --- | --- | --- | --- | --- | --- | --- |
| APP | 7.4 | 3.8 | 6.1 | 39.2 | 0.0 | 39.2 | 757.0 |
| STORE | 24.29 | 3.4 | 3.4 | 37.2 | 0.0 | 37.2 | 756.0 |
| WEB | 8.22 | 3.2 | 5.1 | 33.0 | 0.0 | 33.0 | 954.0 |
| MARKETPLACE | 3.34 | 2.7 | 9.2 | 29.5 | 15.0 | 14.5 | 743.0 |

## <a id="q5"></a>5. What is the 90-day repurchase rate by acquisition channel, and is it improving?

[`q05_repurchase_by_channel.sql`](../sql/analysis/q05_repurchase_by_channel.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: What is the 90-day repurchase rate by acquisition channel, and is it improving?
-- Technique: weighted rates from the repurchase mart (sum of repeaters / sum of eligible), pivot by cohort year.
SELECT
    acquisition_channel,
    SUM(eligible_customers)                                                 AS eligible_customers,
    ROUND(100.0 * SUM(repeat_customers) / SUM(eligible_customers), 1)       AS repurchase_rate_pct,
    ROUND(100.0 * SUM(CASE WHEN cohort_year = 2024 THEN repeat_customers END)
          / SUM(CASE WHEN cohort_year = 2024 THEN eligible_customers END), 1) AS cohort_2024_pct,
    ROUND(100.0 * SUM(CASE WHEN cohort_year = 2025 THEN repeat_customers END)
          / SUM(CASE WHEN cohort_year = 2025 THEN eligible_customers END), 1) AS cohort_2025_pct,
    ROUND(100.0 * SUM(CASE WHEN cohort_year = 2026 THEN repeat_customers END)
          / SUM(CASE WHEN cohort_year = 2026 THEN eligible_customers END), 1) AS cohort_2026_pct,
    ROUND(SUM(avg_days_to_second_order * repeat_customers) / SUM(repeat_customers), 0) AS avg_days_to_second_order
FROM mart_repurchase
GROUP BY acquisition_channel
ORDER BY repurchase_rate_pct DESC;
```

</details>

| acquisition_channel | eligible_customers | repurchase_rate_pct | cohort_2024_pct | cohort_2025_pct | cohort_2026_pct | avg_days_to_second_order |
| --- | --- | --- | --- | --- | --- | --- |
| APP | 5122 | 50.6 | 53.0 | 49.6 | 51.1 | 36.0 |
| STORE | 9527 | 38.8 | 38.0 | 38.4 | 40.7 | 39.0 |
| WEB | 7838 | 35.4 | 34.7 | 35.3 | 36.6 | 39.0 |
| MARKETPLACE | 2043 | 25.5 |  | 25.9 | 25.0 | 38.0 |

## <a id="q6"></a>6. How many customers are still buying 1, 3, 6 and 12 months after their first order, by acquisition channel?

[`q06_cohort_retention.sql`](../sql/analysis/q06_cohort_retention.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: How many customers are still buying 1, 3, 6 and 12 months after their first order, by acquisition channel?
-- Technique: cohort retention from the cohort mart; only cohorts old enough to have reached month 12 (first order by Oct 2025).
SELECT
    acquisition_channel,
    SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END)          AS customers,
    ROUND(100.0 * SUM(CASE WHEN months_since_first = 1 THEN active_customers END)
          / SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END), 1) AS month_1_pct,
    ROUND(100.0 * SUM(CASE WHEN months_since_first = 3 THEN active_customers END)
          / SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END), 1) AS month_3_pct,
    ROUND(100.0 * SUM(CASE WHEN months_since_first = 6 THEN active_customers END)
          / SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END), 1) AS month_6_pct,
    ROUND(100.0 * SUM(CASE WHEN months_since_first = 12 THEN active_customers END)
          / SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END), 1) AS month_12_pct
FROM mart_customer_cohorts
WHERE cohort_month <= '2025-10-01'
GROUP BY acquisition_channel
ORDER BY month_12_pct DESC;
```

</details>

| acquisition_channel | customers | month_1_pct | month_3_pct | month_6_pct | month_12_pct |
| --- | --- | --- | --- | --- | --- |
| APP | 2569 | 22.7 | 21.3 | 20.4 | 15.5 |
| STORE | 6566 | 16.1 | 15.4 | 13.4 | 10.5 |
| WEB | 5462 | 14.6 | 13.2 | 10.6 | 8.2 |
| MARKETPLACE | 689 | 10.3 | 8.1 | 5.4 | 2.8 |

## <a id="q7"></a>7. Do promotions pay? Sales lift, margin and after-effect by discount depth.

[`q07_promotions_by_discount_depth.sql`](../sql/analysis/q07_promotions_by_discount_depth.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: Do promotions pay? Sales lift, margin and after-effect by discount depth.
-- Technique: difference-in-differences lift from the promotion mart (promoted vs non-promoted categories), grouped by band.
SELECT
    discount_band,
    COUNT(*)                                                                AS promotions,
    ROUND(100.0 * AVG(net_sales_lift), 1)                                   AS avg_net_sales_lift_pct,
    ROUND(100.0 * AVG(units_lift), 1)                                       AS avg_units_lift_pct,
    ROUND(100.0 * AVG(baseline_margin_pct), 1)                              AS margin_before_pct,
    ROUND(100.0 * AVG(promo_margin_pct), 1)                                 AS margin_during_pct,
    ROUND(SUM(incremental_gross_margin) / 1e3, 0)                           AS incremental_gross_margin_k,
    SUM(incremental_gross_margin > 0)                                       AS promotions_that_paid,
    ROUND(100.0 * AVG(after_vs_baseline), 1)                                AS sales_3_weeks_after_pct
FROM mart_promo_performance
GROUP BY discount_band
ORDER BY discount_band;
```

</details>

| discount_band | promotions | avg_net_sales_lift_pct | avg_units_lift_pct | margin_before_pct | margin_during_pct | incremental_gross_margin_k | promotions_that_paid | sales_3_weeks_after_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10-19% | 13 | 26.7 | 54.0 | 41.8 | 32.5 | -582.0 | 4 | 11.5 |
| 20-29% | 9 | 21.6 | 61.2 | 35.7 | 20.1 | -342.0 | 0 | 7.9 |
| 30% or more | 8 | 59.7 | 151.8 | 52.7 | 24.0 | -714.0 | 1 | -24.8 |

## <a id="q8"></a>8. Which promotions earned margin and which ones cost it?

[`q08_promotion_scorecard.sql`](../sql/analysis/q08_promotion_scorecard.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: Which promotions earned margin and which ones cost it?
-- Technique: ranking with RANK() over incremental gross margin; method shows whether a comparison group existed.
SELECT
    RANK() OVER (ORDER BY incremental_gross_margin DESC)                   AS rank,
    promo_name,
    ROUND(100 * discount_pct)                                               AS discount_pct,
    category_scope,
    ROUND(100.0 * net_sales_lift, 1)                                        AS net_sales_lift_pct,
    ROUND(100.0 * promo_margin_pct, 1)                                      AS margin_during_pct,
    ROUND(incremental_gross_margin / 1e3, 0)                                AS incremental_gross_margin_k,
    ROUND(100.0 * after_vs_baseline, 1)                                     AS sales_after_pct,
    CASE WHEN method LIKE 'Diff%' THEN 'DiD' ELSE 'before/after' END        AS method
FROM mart_promo_performance
ORDER BY incremental_gross_margin DESC;
```

</details>

| rank | promo_name | discount_pct | category_scope | net_sales_lift_pct | margin_during_pct | incremental_gross_margin_k | sales_after_pct | method |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Christmas toys 2025 | 15.0 | Toys & Kids | 146.3 | 32.0 | 47.0 | 78.2 | before/after |
| 2 | Christmas toys 2024 | 15.0 | Toys & Kids | 139.3 | 31.8 | 37.0 | 42.7 | before/after |
| 3 | Mother's Day 2025 | 15.0 | Beauty, Fashion, Home & Kitchen | 26.1 | 42.4 | 12.0 | 5.5 | DiD |
| 4 | Black Friday 2024 | 30.0 | All | 253.9 | 11.2 | 7.0 | 2.0 | before/after |
| 5 | Mother's Day 2024 | 15.0 | Beauty, Fashion, Home & Kitchen | 26.0 | 42.0 | 5.0 | 14.0 | DiD |
| 6 | Beauty week 2026 | 15.0 | Beauty | 9.4 | 56.1 | -1.0 | 1.4 | before/after |
| 7 | Beauty week 2025 | 15.0 | Beauty | 4.5 | 52.1 | -3.0 | 9.2 | before/after |
| 8 | Cyber Monday 2025 | 20.0 | All | 47.3 | 20.5 | -3.0 | 28.4 | before/after |
| 9 | Cyber Monday 2024 | 20.0 | All | 8.1 | 22.4 | -4.0 | 29.0 | before/after |
| 10 | Ceiba Botánica launch | 20.0 | Beauty | -1.9 | 53.9 | -10.0 | 27.4 | DiD |
| 11 | Home week 2025 | 20.0 | Home & Kitchen | 30.4 | 28.4 | -15.0 | 1.2 | DiD |
| 12 | Home week 2024 | 20.0 | Home & Kitchen | 6.3 | 28.1 | -24.0 | -8.9 | DiD |
| 13 | Mother's Day 2026 | 15.0 | Beauty, Fashion, Home & Kitchen | 8.8 | 43.9 | -39.0 | 2.1 | DiD |
| 14 | Home week 2026 | 20.0 | Home & Kitchen | 8.1 | 28.1 | -42.0 | -6.1 | DiD |
| 15 | Anniversary 2024 | 10.0 | All | -7.0 | 30.9 | -46.0 | 2.2 | before/after |
| 16 | Anniversary 2025 | 10.0 | All | -1.6 | 29.8 | -59.0 | 3.7 | before/after |
| 17 | Bono 14 tech days 2024 | 20.0 | Electronics | 33.1 | -0.2 | -60.0 | 11.3 | DiD |
| 18 | July clearance 2024 | 40.0 | Fashion | 6.9 | 29.6 | -71.0 | -30.1 | DiD |
| 19 | Back to school 2024 | 15.0 | Toys & Kids, Electronics | 8.4 | 10.3 | -72.0 | 14.0 | DiD |
| 20 | January clearance 2024 | 40.0 | Fashion | -14.0 | 29.5 | -73.0 | -28.1 | DiD |
| 21 | Anniversary 2026 | 10.0 | All | -2.8 | 31.1 | -78.0 | 1.5 | before/after |
| 22 | Bono 14 tech days 2025 | 20.0 | Electronics | 28.3 | -0.1 | -92.0 | -7.6 | DiD |
| 23 | Bono 14 tech days 2026 | 20.0 | Electronics | 34.4 | -0.1 | -93.0 | -3.9 | DiD |
| 24 | January clearance 2025 | 40.0 | Fashion | -5.3 | 29.4 | -96.0 | -27.8 | DiD |
| 25 | July clearance 2026 | 40.0 | Fashion | 18.8 | 29.4 | -98.0 | -37.7 | DiD |
| 26 | July clearance 2025 | 40.0 | Fashion | -2.0 | 29.7 | -118.0 | -39.8 | DiD |
| 27 | Black Friday 2025 | 35.0 | All | 224.9 | 3.4 | -127.0 | -3.4 | before/after |
| 28 | January clearance 2026 | 40.0 | Fashion | -5.9 | 29.7 | -135.0 | -33.5 | DiD |
| 29 | Back to school 2025 | 15.0 | Toys & Kids, Electronics | -6.7 | 9.9 | -157.0 | -8.9 | DiD |
| 30 | Back to school 2026 | 15.0 | Toys & Kids, Electronics | -3.2 | 9.9 | -228.0 | -16.6 | DiD |

## <a id="q9"></a>9. Black Friday 2024 (30% off) vs 2025 (35% off): did the deeper discount pay?

[`q09_black_friday.sql`](../sql/analysis/q09_black_friday.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: Black Friday 2024 (30% off) vs 2025 (35% off): did the deeper discount pay?
-- Technique: promotion-level aggregation on the line fact, compared with the promotion mart's baseline.
SELECT
    p.promo_name,
    ROUND(100 * p.discount_pct)                                             AS discount_pct,
    ROUND(SUM(l.net_sales) / 1e6, 2)                                        AS net_sales_m,
    SUM(l.quantity)                                                         AS units,
    ROUND(100.0 * SUM(l.gross_margin) / SUM(l.net_sales), 1)                AS gross_margin_pct,
    ROUND(100.0 * SUM(CASE WHEN l.category = 'Electronics' THEN l.gross_margin END)
          / SUM(CASE WHEN l.category = 'Electronics' THEN l.net_sales END), 1) AS electronics_margin_pct,
    ROUND(m.incremental_gross_margin / 1e3, 0)                              AS incremental_gross_margin_k
FROM fct_order_lines l
JOIN dim_promotion p ON p.promo_code = l.promo_code
JOIN mart_promo_performance m ON m.promo_code = p.promo_code
WHERE p.promo_name LIKE 'Black Friday%'
GROUP BY p.promo_name, p.discount_pct, m.incremental_gross_margin
ORDER BY p.promo_name;
```

</details>

| promo_name | discount_pct | net_sales_m | units | gross_margin_pct | electronics_margin_pct | incremental_gross_margin_k |
| --- | --- | --- | --- | --- | --- | --- |
| Black Friday 2024 | 30.0 | 1.31 | 4321 | 11.2 | -14.4 | 7.0 |
| Black Friday 2025 | 35.0 | 1.56 | 5443 | 3.3 | -23.4 | -127.0 |

## <a id="q10"></a>10. How are the stores doing this year, and how much of their sales can we tie to a known customer?

[`q10_store_performance.sql`](../sql/analysis/q10_store_performance.sql)

<details><summary>Show SQL</summary>

```sql
-- Question: How are the stores doing this year, and how much of their sales can we tie to a known customer?
-- Technique: year-over-year by store with conditional aggregation; share of tickets with a loyalty card.
SELECT
    s.store_name,
    s.store_format,
    ROUND(SUM(CASE WHEN o.order_date BETWEEN '2026-01-01' AND '2026-10-31' THEN o.net_sales END) / 1e6, 2) AS net_sales_2026_ytd_m,
    ROUND(100.0 * (SUM(CASE WHEN o.order_date BETWEEN '2026-01-01' AND '2026-10-31' THEN o.net_sales END)
          / SUM(CASE WHEN o.order_date BETWEEN '2025-01-01' AND '2025-10-31' THEN o.net_sales END) - 1), 1) AS growth_pct,
    ROUND(100.0 * AVG(CASE WHEN o.order_date >= '2026-01-01' THEN o.customer_id IS NOT NULL END), 1) AS identified_tickets_pct
FROM fct_orders o
JOIN dim_store s ON s.store_id = o.store_id
GROUP BY s.store_name, s.store_format
ORDER BY net_sales_2026_ytd_m DESC;
```

</details>

| store_name | store_format | net_sales_2026_ytd_m | growth_pct | identified_tickets_pct |
| --- | --- | --- | --- | --- |
| Casa Ceiba Zona 10 | Flagship | 4.88 | 5.5 | 26.5 |
| Casa Ceiba Miraflores | Mall | 4.15 | 3.3 | 27.9 |
| Casa Ceiba Xela | Mall | 3.7 | 12.4 | 45.8 |
| Casa Ceiba Naranjo | Mall | 3.64 | 13.8 | 36.8 |
| Casa Ceiba Pradera Concepción | Mall | 3.64 | 10.6 | 31.8 |
| Casa Ceiba Antigua | Street | 2.2 | 7.4 | 30.6 |
| Casa Ceiba Escuintla | Mall | 2.08 | 6.5 | 27.7 |
