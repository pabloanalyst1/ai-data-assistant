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
