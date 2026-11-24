-- =====================================================================================
-- 04 MARTS: business-ready tables for the dashboard, the analysis and the assistant.
-- Numerators and denominators are kept (not only ratios) so they can be re-aggregated.
-- =====================================================================================

-- Sales by month, channel and category ---------------------------------------------------
DROP TABLE IF EXISTS mart_sales_monthly;
CREATE TABLE mart_sales_monthly AS
SELECT
    DATE(order_date, 'start of month')                                      AS month_start,
    channel_id,
    category,
    COUNT(DISTINCT order_id)                                                AS orders,
    COUNT(DISTINCT customer_id)                                             AS identified_customers,
    SUM(quantity)                                                           AS units,
    ROUND(SUM(gross_sales), 2)                                              AS gross_sales,
    ROUND(SUM(discount_amount), 2)                                          AS discount_amount,
    ROUND(SUM(returned_amount), 2)                                          AS returned_amount,
    ROUND(SUM(net_sales), 2)                                                AS net_sales,
    ROUND(SUM(cogs), 2)                                                     AS cogs,
    ROUND(SUM(gross_margin), 2)                                             AS gross_margin,
    ROUND(SUM(commission), 2)                                               AS commission,
    ROUND(SUM(contribution_margin), 2)                                      AS contribution_margin
FROM fct_order_lines
GROUP BY 1, 2, 3;


-- Category growth by quarter (year over year and quarter over quarter) ------------------
DROP TABLE IF EXISTS mart_category_quarterly;
CREATE TABLE mart_category_quarterly AS
WITH q AS (
    SELECT d.year_quarter, d.year, d.quarter, l.category,
           SUM(l.net_sales) AS net_sales, SUM(l.gross_margin) AS gross_margin, SUM(l.quantity) AS units
    FROM fct_order_lines l
    JOIN dim_date d ON d.date = l.order_date
    GROUP BY 1, 2, 3, 4
)
SELECT
    year_quarter, year, quarter, category,
    ROUND(net_sales, 2)                                                     AS net_sales,
    ROUND(gross_margin, 2)                                                  AS gross_margin,
    units,
    ROUND(LAG(net_sales, 4) OVER w, 2)                                      AS net_sales_same_quarter_last_year,
    ROUND(net_sales / LAG(net_sales, 4) OVER w - 1, 4)                      AS yoy_growth,
    ROUND(LAG(net_sales, 1) OVER w, 2)                                      AS net_sales_previous_quarter,
    ROUND(net_sales / LAG(net_sales, 1) OVER w - 1, 4)                      AS qoq_growth
FROM q
WINDOW w AS (PARTITION BY category ORDER BY year, quarter);


-- Retention cohorts: share of each month's new customers who buy again k months later ---
DROP TABLE IF EXISTS mart_customer_cohorts;
CREATE TABLE mart_customer_cohorts AS
WITH activity AS (
    SELECT DISTINCT
        c.customer_id, c.cohort_month, c.acquisition_channel,
        (CAST(STRFTIME('%Y', o.order_date) AS INTEGER) - CAST(STRFTIME('%Y', c.cohort_month) AS INTEGER)) * 12
        + CAST(STRFTIME('%m', o.order_date) AS INTEGER) - CAST(STRFTIME('%m', c.cohort_month) AS INTEGER) AS months_since_first
    FROM dim_customer c
    JOIN fct_orders o ON o.customer_id = c.customer_id
),
sizes AS (
    SELECT cohort_month, acquisition_channel, COUNT(*) AS cohort_customers
    FROM dim_customer WHERE cohort_month IS NOT NULL
    GROUP BY 1, 2
)
SELECT
    a.cohort_month, a.acquisition_channel, a.months_since_first,
    s.cohort_customers,
    COUNT(*)                                                                AS active_customers,
    ROUND(1.0 * COUNT(*) / s.cohort_customers, 4)                           AS retention_rate
FROM activity a
JOIN sizes s ON s.cohort_month = a.cohort_month AND s.acquisition_channel = a.acquisition_channel
GROUP BY 1, 2, 3, 4;


-- Repurchase (second order within 90 days) by acquisition channel and cohort year -------
DROP TABLE IF EXISTS mart_repurchase;
CREATE TABLE mart_repurchase AS
SELECT
    acquisition_channel,
    CAST(STRFTIME('%Y', first_order_date) AS INTEGER)                       AS cohort_year,
    COUNT(*)                                                                AS new_customers,
    COUNT(repurchased_within_90d)                                           AS eligible_customers,
    SUM(repurchased_within_90d)                                             AS repeat_customers,
    ROUND(AVG(repurchased_within_90d), 4)                                   AS repurchase_rate,
    ROUND(AVG(CASE WHEN repurchased_within_90d = 1 THEN days_to_second_order END), 1) AS avg_days_to_second_order,
    ROUND(AVG(total_orders), 2)                                             AS avg_orders_per_customer
FROM dim_customer
WHERE first_order_date IS NOT NULL
GROUP BY 1, 2;


-- Promotion performance -------------------------------------------------------------------
-- Daily sales of the promoted categories (scope) in the 28 days before, during and the 21
-- days after each promotion. Seasonality (December -> January, Mother's Day, Black Friday
-- traffic) moves every category, so the lift is measured against the categories that were
-- NOT on promotion in the same channels (difference-in-differences). Days when another
-- promotion covered part of the scope are left out of the before/after windows, and a
-- comparison category is used only if no promotion touched it during the whole window.
-- Sitewide promotions (and a few in busy seasons) have no clean comparison group: for
-- them only the simple before/during lift exists.
DROP TABLE IF EXISTS mart_promo_performance;
CREATE TABLE mart_promo_performance AS
WITH daily AS (
    SELECT order_date, category, channel_id, SUM(net_sales) AS net_sales,
           SUM(gross_margin) AS gross_margin, SUM(quantity) AS units
    FROM fct_order_lines
    GROUP BY 1, 2, 3
),
windows AS (
    SELECT p.promo_code, d.date,
           CASE WHEN d.date < p.start_date THEN 'before' WHEN d.date <= p.end_date THEN 'during' ELSE 'after' END AS period
    FROM dim_promotion p
    JOIN dim_date d ON d.date BETWEEN DATE(p.start_date, '-28 days') AND DATE(p.end_date, '+21 days')
),
cells AS (  -- every category x channel the promotion could touch: in its scope or a comparison cell
    SELECT DISTINCT p.promo_code, c.category, s.channel_id,
           EXISTS (SELECT 1 FROM dim_promotion_scope x WHERE x.promo_code = p.promo_code
                   AND x.category = c.category AND x.channel_id = s.channel_id) AS in_scope
    FROM dim_promotion p
    JOIN dim_promotion_scope s ON s.promo_code = p.promo_code
    CROSS JOIN (SELECT DISTINCT category FROM dim_product) c
),
busy AS (  -- category x channel x day covered by any promotion
    SELECT DISTINCT p.promo_code, s.category, s.channel_id, d.date
    FROM dim_promotion p
    JOIN dim_promotion_scope s ON s.promo_code = p.promo_code
    JOIN dim_date d ON d.date BETWEEN p.start_date AND p.end_date
),
skipped_days AS (  -- before/after days when another promotion covered part of the scope
    SELECT DISTINCT w.promo_code, w.date
    FROM windows w
    JOIN cells c ON c.promo_code = w.promo_code AND c.in_scope = 1
    JOIN busy b ON b.date = w.date AND b.category = c.category AND b.channel_id = c.channel_id
               AND b.promo_code <> w.promo_code
    WHERE w.period <> 'during'
),
clean_comparison AS (  -- comparison cells no promotion touched at any point of the window
    SELECT c.promo_code, c.category, c.channel_id
    FROM cells c
    WHERE c.in_scope = 0
      AND NOT EXISTS (
          SELECT 1 FROM windows w
          JOIN busy b ON b.date = w.date AND b.category = c.category AND b.channel_id = c.channel_id
          WHERE w.promo_code = c.promo_code)
),
cell_days AS (
    SELECT w.promo_code, w.period, c.in_scope, w.date,
           SUM(d.net_sales) AS net_sales, SUM(d.gross_margin) AS gross_margin, SUM(d.units) AS units
    FROM windows w
    JOIN cells c ON c.promo_code = w.promo_code
    JOIN daily d ON d.order_date = w.date AND d.category = c.category AND d.channel_id = c.channel_id
    WHERE NOT EXISTS (SELECT 1 FROM skipped_days k WHERE k.promo_code = w.promo_code AND k.date = w.date)
      AND (c.in_scope = 1 OR EXISTS (SELECT 1 FROM clean_comparison x WHERE x.promo_code = c.promo_code
                                     AND x.category = c.category AND x.channel_id = c.channel_id))
    GROUP BY 1, 2, 3, 4
),
periods AS (
    SELECT promo_code, in_scope, period, COUNT(*) AS days,
           SUM(net_sales) / COUNT(*) AS daily_net_sales, SUM(gross_margin) / COUNT(*) AS daily_gross_margin,
           SUM(units) * 1.0 / COUNT(*) AS daily_units, SUM(gross_margin) / SUM(net_sales) AS margin_pct
    FROM cell_days
    GROUP BY 1, 2, 3
),
wide AS (
    SELECT promo_code,
        MAX(CASE WHEN in_scope = 1 AND period = 'before' THEN days END)              AS baseline_days,
        MAX(CASE WHEN in_scope = 1 AND period = 'before' THEN daily_net_sales END)   AS s_before,
        MAX(CASE WHEN in_scope = 1 AND period = 'during' THEN daily_net_sales END)   AS s_during,
        MAX(CASE WHEN in_scope = 1 AND period = 'after'  THEN daily_net_sales END)   AS s_after,
        MAX(CASE WHEN in_scope = 1 AND period = 'before' THEN daily_units END)       AS u_before,
        MAX(CASE WHEN in_scope = 1 AND period = 'during' THEN daily_units END)       AS u_during,
        MAX(CASE WHEN in_scope = 1 AND period = 'before' THEN daily_gross_margin END) AS m_before,
        MAX(CASE WHEN in_scope = 1 AND period = 'during' THEN daily_gross_margin END) AS m_during,
        MAX(CASE WHEN in_scope = 1 AND period = 'before' THEN margin_pct END)        AS margin_before,
        MAX(CASE WHEN in_scope = 1 AND period = 'during' THEN margin_pct END)        AS margin_during,
        MAX(CASE WHEN in_scope = 0 AND period = 'before' THEN daily_net_sales END)   AS c_before,
        MAX(CASE WHEN in_scope = 0 AND period = 'during' THEN daily_net_sales END)   AS c_during,
        MAX(CASE WHEN in_scope = 0 AND period = 'after'  THEN daily_net_sales END)   AS c_after
    FROM periods
    GROUP BY promo_code
)
SELECT
    p.promo_code, p.promo_name, p.promo_type, p.discount_pct, p.discount_band, p.is_deep_discount,
    p.start_date, p.end_date, p.days, p.category_scope, p.channel_scope,
    CASE WHEN w.c_before > 0 THEN 'Difference-in-differences vs categories not on promotion'
         ELSE 'Before vs during only (no clean comparison group)' END         AS method,
    w.baseline_days,
    ROUND(w.s_before, 2)                                                    AS baseline_daily_net_sales,
    ROUND(w.s_during, 2)                                                    AS promo_daily_net_sales,
    ROUND(w.s_during / w.s_before - 1, 4)                                   AS simple_net_sales_lift,
    ROUND(w.c_during / w.c_before - 1, 4)                                   AS comparison_change,
    -- lift beyond what the comparison categories did over the same days
    ROUND(COALESCE((w.s_during / w.s_before) / (w.c_during / w.c_before), w.s_during / w.s_before) - 1, 4) AS net_sales_lift,
    ROUND(w.u_during / w.u_before - 1, 4)                                   AS units_lift,
    ROUND(w.margin_before, 4)                                               AS baseline_margin_pct,
    ROUND(w.margin_during, 4)                                               AS promo_margin_pct,
    -- gross margin earned during the promotion minus what the baseline (adjusted for the
    -- comparison group's movement) would have earned over the same days
    ROUND((w.m_during - w.m_before * COALESCE(w.c_during / w.c_before, 1)) * p.days, 2) AS incremental_gross_margin,
    ROUND(COALESCE((w.s_after / w.s_before) / (w.c_after / w.c_before), w.s_after / w.s_before) - 1, 4) AS after_vs_baseline
FROM dim_promotion p
JOIN wide w ON w.promo_code = p.promo_code
WHERE w.s_before > 0;


-- Headline KPIs: year to date vs the same period last year ------------------------------
DROP TABLE IF EXISTS mart_kpi_summary;
CREATE TABLE mart_kpi_summary AS
WITH params AS (
    SELECT as_of_date,
           STRFTIME('%Y', as_of_date) || '-01-01'                            AS cy_start,
           DATE(STRFTIME('%Y', as_of_date) || '-01-01', '-1 year')           AS py_start,
           DATE(as_of_date, '-1 year')                                       AS py_end
    FROM ref_parameters
),
periods AS (
    SELECT 'current' AS period, cy_start AS start_date, as_of_date AS end_date FROM params
    UNION ALL
    SELECT 'prior', py_start, py_end FROM params
),
m AS (
    SELECT p.period,
           SUM(o.net_sales) AS net_sales, COUNT(*) AS orders, SUM(o.gross_margin) AS gross_margin,
           SUM(o.contribution_margin) AS contribution_margin, SUM(o.gross_sales) AS gross_sales,
           SUM(o.discount_amount) AS discount_amount, SUM(o.returned_amount) AS returned_amount,
           COUNT(DISTINCT o.customer_id) AS buying_customers,
           SUM(CASE WHEN o.customer_order_number = 1 THEN 1 ELSE 0 END) AS new_customers,
           AVG(CASE WHEN o.customer_id IS NOT NULL THEN 1.0 ELSE 0 END) AS identified_share
    FROM periods p
    JOIN fct_orders o ON o.order_date BETWEEN p.start_date AND p.end_date
    GROUP BY p.period
),
kpis AS (
    SELECT 1 AS sort, 'Net sales (GTQ)' AS kpi, period, net_sales AS value FROM m
    UNION ALL SELECT 2, 'Orders', period, orders FROM m
    UNION ALL SELECT 3, 'Average order value (GTQ)', period, net_sales / orders FROM m
    UNION ALL SELECT 4, 'Gross margin %', period, gross_margin / net_sales FROM m
    UNION ALL SELECT 5, 'Contribution margin %', period, contribution_margin / net_sales FROM m
    UNION ALL SELECT 6, 'Discount rate (share of gross sales)', period, discount_amount / gross_sales FROM m
    UNION ALL SELECT 7, 'Return rate (share of sales after discount)', period, returned_amount / (gross_sales - discount_amount) FROM m
    UNION ALL SELECT 8, 'Buying customers (identified)', period, buying_customers FROM m
    UNION ALL SELECT 9, 'New customers', period, new_customers FROM m
    UNION ALL SELECT 10, 'Orders with an identified customer %', period, identified_share FROM m
)
SELECT
    sort, kpi,
    (SELECT cy_start FROM params) || ' to ' || (SELECT as_of_date FROM params) AS current_period,
    ROUND(MAX(CASE WHEN period = 'current' THEN value END), 4)              AS current_value,
    ROUND(MAX(CASE WHEN period = 'prior' THEN value END), 4)                AS prior_year_value,
    ROUND(1.0 * MAX(CASE WHEN period = 'current' THEN value END) / MAX(CASE WHEN period = 'prior' THEN value END) - 1, 4) AS change
FROM kpis
GROUP BY sort, kpi
ORDER BY sort;
