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
