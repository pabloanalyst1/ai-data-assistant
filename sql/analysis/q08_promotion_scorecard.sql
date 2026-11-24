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
