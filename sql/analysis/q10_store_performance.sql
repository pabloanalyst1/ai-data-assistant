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
