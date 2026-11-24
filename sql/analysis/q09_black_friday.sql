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
