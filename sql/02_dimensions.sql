-- =====================================================================================
-- 02 DIMENSIONS: date, channel, store, product, promotion and customer.
-- =====================================================================================

-- Calendar -----------------------------------------------------------------------------
DROP TABLE IF EXISTS dim_date;
CREATE TABLE dim_date AS
WITH RECURSIVE days(d) AS (
    SELECT period_start FROM ref_parameters
    UNION ALL
    SELECT DATE(d, '+1 day') FROM days WHERE d < (SELECT period_end FROM ref_parameters)
)
SELECT
    d                                                                       AS date,
    CAST(STRFTIME('%Y', d) AS INTEGER)                                      AS year,
    (CAST(STRFTIME('%m', d) AS INTEGER) + 2) / 3                            AS quarter,
    STRFTIME('%Y', d) || '-Q' || ((CAST(STRFTIME('%m', d) AS INTEGER) + 2) / 3) AS year_quarter,
    DATE(d, 'start of month', '-' || ((CAST(STRFTIME('%m', d) AS INTEGER) - 1) % 3) || ' months') AS quarter_start,
    CAST(STRFTIME('%m', d) AS INTEGER)                                      AS month,
    STRFTIME('%Y-%m', d)                                                    AS year_month,
    DATE(d, 'start of month')                                               AS month_start,
    CASE CAST(STRFTIME('%m', d) AS INTEGER)
        WHEN 1 THEN 'January' WHEN 2 THEN 'February' WHEN 3 THEN 'March' WHEN 4 THEN 'April'
        WHEN 5 THEN 'May' WHEN 6 THEN 'June' WHEN 7 THEN 'July' WHEN 8 THEN 'August'
        WHEN 9 THEN 'September' WHEN 10 THEN 'October' WHEN 11 THEN 'November' ELSE 'December' END AS month_name,
    DATE(d, '-6 days', 'weekday 1')                                         AS week_start,
    (CAST(STRFTIME('%w', d) AS INTEGER) + 6) % 7 + 1                        AS day_of_week,
    CASE CAST(STRFTIME('%w', d) AS INTEGER)
        WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday'
        WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' ELSE 'Saturday' END     AS day_name,
    CAST(STRFTIME('%w', d) AS INTEGER) IN (0, 6)                            AS is_weekend
FROM days;

CREATE UNIQUE INDEX ix_dim_date ON dim_date (date);


-- Channels and stores ------------------------------------------------------------------
DROP TABLE IF EXISTS dim_channel;
CREATE TABLE dim_channel AS
SELECT channel_id, channel_name, channel_type, launch_date, CAST(commission_rate AS REAL) AS commission_rate
FROM ref_channels;

DROP TABLE IF EXISTS dim_store;
CREATE TABLE dim_store AS
SELECT s.store_id, s.store_name, s.city, s.department, d.region, s.store_format, s.opened_date
FROM ref_stores s
LEFT JOIN ref_department_aliases d ON d.raw_department = UPPER(TRIM(s.department));


-- Products -----------------------------------------------------------------------------
DROP TABLE IF EXISTS dim_product;
CREATE TABLE dim_product AS
SELECT product_id, product_name, category, subcategory, brand, is_private_label, list_price, unit_cost,
       cost_imputed, launch_date
FROM stg_products;

CREATE UNIQUE INDEX ix_dim_product ON dim_product (product_id);


-- Promotions ---------------------------------------------------------------------------
DROP TABLE IF EXISTS dim_promotion;
CREATE TABLE dim_promotion AS
SELECT
    promo_code, promo_name, promo_type,
    CAST(discount_pct AS REAL)                                              AS discount_pct,
    CASE WHEN discount_pct >= 0.30 THEN '30% or more'
         WHEN discount_pct >= 0.20 THEN '20-29%'
         ELSE '10-19%' END                                                  AS discount_band,
    discount_pct >= 0.30                                                    AS is_deep_discount,
    start_date, end_date,
    CAST(JULIANDAY(end_date) - JULIANDAY(start_date) + 1 AS INTEGER)        AS days,
    category_scope, channel_scope
FROM ref_promotions;

DROP TABLE IF EXISTS dim_promotion_scope;
CREATE TABLE dim_promotion_scope AS
SELECT promo_code, category, channel_id FROM ref_promotion_scope;


-- Customers ----------------------------------------------------------------------------
-- One row per real customer (duplicate accounts merged, QA accounts removed), with the
-- facts every customer question needs: first order, acquisition channel, cohort, repeat.
DROP TABLE IF EXISTS dim_customer;
CREATE TABLE dim_customer AS
WITH ranked AS (
    SELECT customer_id, order_id, order_date, channel_id,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_ts, order_id) AS n
    FROM stg_orders
    WHERE customer_id IS NOT NULL
),
summary AS (
    SELECT customer_id,
           MAX(CASE WHEN n = 1 THEN order_date END)                          AS first_order_date,
           MAX(CASE WHEN n = 1 THEN channel_id END)                          AS acquisition_channel,
           MAX(CASE WHEN n = 2 THEN order_date END)                          AS second_order_date,
           MAX(order_date)                                                   AS last_order_date,
           COUNT(*)                                                          AS total_orders
    FROM ranked
    GROUP BY customer_id
)
SELECT
    c.customer_id, c.signup_date, c.department, c.region, c.home_store_id, c.accounts_merged,
    s.first_order_date,
    s.acquisition_channel,
    DATE(s.first_order_date, 'start of month')                             AS cohort_month,
    s.second_order_date,
    CAST(JULIANDAY(s.second_order_date) - JULIANDAY(s.first_order_date) AS INTEGER) AS days_to_second_order,
    s.last_order_date,
    COALESCE(s.total_orders, 0)                                            AS total_orders,
    -- repeat customer: second order within the repurchase window. NULL while the window is still open.
    CASE WHEN s.first_order_date IS NULL THEN NULL
         WHEN JULIANDAY(s.second_order_date) - JULIANDAY(s.first_order_date) <= p.repurchase_window_days THEN 1
         WHEN JULIANDAY(p.as_of_date) - JULIANDAY(s.first_order_date) < p.repurchase_window_days THEN NULL
         ELSE 0 END                                                        AS repurchased_within_90d,
    -- active customer: at least one completed order in the 12 months up to the reference date
    CASE WHEN JULIANDAY(p.as_of_date) - JULIANDAY(s.last_order_date) < p.active_window_days THEN 1 ELSE 0 END AS is_active
FROM stg_customers c
CROSS JOIN ref_parameters p
LEFT JOIN summary s ON s.customer_id = c.customer_id;

CREATE UNIQUE INDEX ix_dim_customer ON dim_customer (customer_id);
