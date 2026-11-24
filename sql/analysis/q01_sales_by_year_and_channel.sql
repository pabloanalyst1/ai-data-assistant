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
