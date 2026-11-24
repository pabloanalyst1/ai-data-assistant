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
