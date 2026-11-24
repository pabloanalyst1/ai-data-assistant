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
