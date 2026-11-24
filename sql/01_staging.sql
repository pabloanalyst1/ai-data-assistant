-- =====================================================================================
-- 01 STAGING: clean the four source extracts (POS, e-commerce platform, CRM, catalog).
-- Fixes: duplicate customer accounts (same e-mail typed differently), QA test accounts,
-- department names spelled several ways, two date formats, UTC timestamps, channel labels
-- typed by hand, orders re-sent by the integration, cancelled orders, clock errors,
-- amounts stored as text ("Q1,234.50"), SKUs in lower case or with spaces, returns mixed
-- into the POS sales lines, returns that match no sale, category names in two languages
-- and missing unit costs.
-- Nothing is silently dropped: rejected rows go to stg_rejected_* with a reason.
-- =====================================================================================

-- Customers --------------------------------------------------------------------------
-- The CRM has duplicate accounts for the same person (same e-mail, typed with spaces or
-- capitals). The earliest account is the real customer; the others map to it.
DROP TABLE IF EXISTS stg_customer_accounts;
CREATE TABLE stg_customer_accounts AS
WITH typed AS (
    SELECT
        TRIM(customer_id)                                                   AS source_customer_id,
        LOWER(TRIM(email))                                                  AS email,
        -- 'dd/mm/yyyy' and 'yyyy-mm-dd' -> 'yyyy-mm-dd'
        CASE WHEN signup_date LIKE '__/__/____'
             THEN SUBSTR(signup_date, 7, 4) || '-' || SUBSTR(signup_date, 4, 2) || '-' || SUBSTR(signup_date, 1, 2)
             ELSE SUBSTR(signup_date, 1, 10) END                            AS signup_date,
        UPPER(TRIM(department))                                             AS department_key,
        TRIM(home_store)                                                    AS home_store_id
    FROM raw_crm_customers
)
SELECT
    t.*,
    FIRST_VALUE(t.source_customer_id) OVER (
        PARTITION BY t.email ORDER BY t.signup_date, t.source_customer_id) AS customer_id,
    t.email LIKE '%.test'                                                   AS is_test_account
FROM typed t;

CREATE INDEX ix_stg_customer_accounts ON stg_customer_accounts (source_customer_id);

DROP TABLE IF EXISTS stg_customers;
CREATE TABLE stg_customers AS
SELECT
    a.customer_id,
    a.email,
    a.signup_date,
    COALESCE(d.department, 'Unknown')                                       AS department,
    COALESCE(d.region, 'Unknown')                                           AS region,
    a.home_store_id,
    n.accounts                                                              AS accounts_merged
FROM stg_customer_accounts a
JOIN (SELECT customer_id, COUNT(*) AS accounts FROM stg_customer_accounts GROUP BY customer_id) n
  ON n.customer_id = a.customer_id
LEFT JOIN ref_department_aliases d ON d.raw_department = a.department_key
WHERE a.source_customer_id = a.customer_id AND NOT a.is_test_account;


-- Products ---------------------------------------------------------------------------
-- Category names arrive in Spanish or English; a few products have no unit cost, which is
-- imputed from the cost-to-price ratio of the other products in the same subcategory.
DROP TABLE IF EXISTS stg_products;
CREATE TABLE stg_products AS
WITH typed AS (
    SELECT
        UPPER(TRIM(p.sku))                                                  AS product_id,
        TRIM(p.product_name)                                                AS product_name,
        m.category,
        TRIM(p.subcategory)                                                 AS subcategory,
        TRIM(p.brand)                                                       AS brand,
        CASE WHEN UPPER(TRIM(p.is_private_label)) IN ('Y', 'S', '1', 'TRUE') THEN 1 ELSE 0 END AS is_private_label,
        CAST(p.list_price AS REAL)                                          AS list_price,
        CAST(p.unit_cost AS REAL)                                           AS unit_cost,
        p.launch_date
    FROM raw_catalog_products p
    LEFT JOIN ref_category_map m ON m.raw_category = UPPER(TRIM(p.category))
)
SELECT
    product_id, product_name, category, subcategory, brand, is_private_label, list_price,
    ROUND(COALESCE(unit_cost, list_price * AVG(unit_cost / list_price) OVER (PARTITION BY subcategory)), 2) AS unit_cost,
    unit_cost IS NULL                                                       AS cost_imputed,
    launch_date
FROM typed;


-- E-commerce orders --------------------------------------------------------------------
-- The integration sometimes re-sends an order (first copy 'pending'); the last copy wins.
DROP TABLE IF EXISTS stg_ecom_order_copies;
CREATE TABLE stg_ecom_order_copies AS
SELECT
    TRIM(order_id)                                                          AS order_id,
    ingested_at,
    ROW_NUMBER() OVER (PARTITION BY TRIM(order_id) ORDER BY ingested_at DESC) AS copy_rank,
    COUNT(*) OVER (PARTITION BY TRIM(order_id))                             AS copies
FROM raw_ecom_orders;

DROP TABLE IF EXISTS stg_ecom_orders_all;
CREATE TABLE stg_ecom_orders_all AS
SELECT
    o.order_id,
    c.copies,
    a.customer_id,
    a.is_test_account,
    ch.channel_id,
    TRIM(o.channel_label)                                                   AS channel_label,
    LOWER(TRIM(o.status))                                                   AS status,
    -- created_at is UTC ('2025-03-04T02:15:00Z'); Guatemala is UTC-6 all year
    DATETIME(SUBSTR(o.created_at, 1, 19),
             printf('%+d hours', (SELECT local_utc_offset_hours FROM ref_parameters))) AS order_ts
FROM raw_ecom_orders o
JOIN stg_ecom_order_copies c ON c.order_id = TRIM(o.order_id) AND c.ingested_at = o.ingested_at AND c.copy_rank = 1
LEFT JOIN stg_customer_accounts a ON a.source_customer_id = TRIM(o.customer_id)
LEFT JOIN ref_channel_aliases ch ON ch.raw_label = UPPER(TRIM(o.channel_label));


-- POS tickets --------------------------------------------------------------------------
-- One row per ticket line. Dates are 'dd/mm/yyyy hh:mm', amounts are text ('Q1,234.50'),
-- and returns are negative-quantity lines that point to the original ticket line.
DROP TABLE IF EXISTS stg_pos_lines;
CREATE TABLE stg_pos_lines AS
SELECT
    TRIM(p.ticket_id)                                                       AS ticket_id,
    CAST(p.line_no AS INTEGER)                                              AS line_no,
    TRIM(p.store_code)                                                      AS store_id,
    SUBSTR(p.ticket_ts, 7, 4) || '-' || SUBSTR(p.ticket_ts, 4, 2) || '-' || SUBSTR(p.ticket_ts, 1, 2)
        || ' ' || SUBSTR(p.ticket_ts, 12, 5) || ':00'                       AS ticket_ts,
    a.customer_id,
    a.is_test_account,
    UPPER(TRIM(p.sku))                                                      AS product_id,
    CAST(p.quantity AS INTEGER)                                             AS quantity,
    CAST(REPLACE(REPLACE(p.unit_price, 'Q', ''), ',', '') AS REAL)          AS unit_price,
    CAST(REPLACE(REPLACE(p.discount, 'Q', ''), ',', '') AS REAL)            AS discount,
    NULLIF(TRIM(p.promo_code), '')                                          AS promo_code,
    NULLIF(TRIM(p.original_ticket_line), '')                                AS original_ticket_line
FROM raw_pos_ticket_lines p
LEFT JOIN stg_customer_accounts a ON a.source_customer_id = NULLIF(TRIM(p.loyalty_card), '');


-- Orders: one row per completed order from both systems ------------------------------
DROP TABLE IF EXISTS stg_orders_all;
CREATE TABLE stg_orders_all AS
SELECT order_id, 'ECOM' AS source_system, order_ts, customer_id, is_test_account, channel_id,
       NULL AS store_id, status
FROM stg_ecom_orders_all
UNION ALL
SELECT ticket_id, 'POS', MIN(ticket_ts), MIN(customer_id), MAX(COALESCE(is_test_account, 0)), 'STORE',
       MIN(store_id), 'completed'
FROM stg_pos_lines
WHERE quantity > 0
GROUP BY ticket_id;

DROP TABLE IF EXISTS stg_rejected_orders;
CREATE TABLE stg_rejected_orders AS
SELECT o.*,
       CASE WHEN o.is_test_account THEN 'QA test order'
            WHEN o.channel_id IS NULL THEN 'Unknown channel label'
            WHEN DATE(o.order_ts) > p.period_end OR DATE(o.order_ts) < p.period_start THEN 'Order date outside the extract period (clock error)'
       END AS reject_reason
FROM stg_orders_all o CROSS JOIN ref_parameters p
WHERE o.status <> 'cancelled'
  AND (o.is_test_account OR o.channel_id IS NULL
       OR DATE(o.order_ts) > p.period_end OR DATE(o.order_ts) < p.period_start);

DROP TABLE IF EXISTS stg_cancelled_orders;
CREATE TABLE stg_cancelled_orders AS
SELECT * FROM stg_orders_all WHERE status = 'cancelled';

DROP TABLE IF EXISTS stg_orders;
CREATE TABLE stg_orders AS
SELECT order_id, source_system, order_ts, DATE(order_ts) AS order_date, customer_id, channel_id, store_id
FROM stg_orders_all
WHERE status = 'completed'
  AND order_id NOT IN (SELECT order_id FROM stg_rejected_orders);

CREATE UNIQUE INDEX ix_stg_orders ON stg_orders (order_id);


-- Order lines --------------------------------------------------------------------------
DROP TABLE IF EXISTS stg_order_lines;
CREATE TABLE stg_order_lines AS
SELECT o.order_id, CAST(i.line_no AS INTEGER) AS line_no, UPPER(TRIM(i.sku)) AS product_id,
       CAST(i.quantity AS INTEGER) AS quantity, CAST(i.unit_price AS REAL) AS unit_list_price,
       CAST(i.discount_amount AS REAL) AS discount_amount, NULLIF(TRIM(i.promo_code), '') AS promo_code
FROM raw_ecom_order_items i
JOIN stg_ecom_order_copies c ON c.order_id = TRIM(i.order_id) AND c.ingested_at = i.ingested_at AND c.copy_rank = 1
JOIN stg_orders o ON o.order_id = c.order_id
UNION ALL
SELECT p.ticket_id, p.line_no, p.product_id, p.quantity, p.unit_price, p.discount, p.promo_code
FROM stg_pos_lines p
JOIN stg_orders o ON o.order_id = p.ticket_id
WHERE p.quantity > 0;

CREATE UNIQUE INDEX ix_stg_order_lines ON stg_order_lines (order_id, line_no);


-- Returns ------------------------------------------------------------------------------
DROP TABLE IF EXISTS stg_returns_all;
CREATE TABLE stg_returns_all AS
SELECT TRIM(return_id) AS return_id, TRIM(order_id) AS order_id, CAST(line_no AS INTEGER) AS line_no,
       return_date, CAST(quantity AS INTEGER) AS quantity, CAST(refund_amount AS REAL) AS refund_amount,
       TRIM(reason) AS reason, 'ECOM' AS source_system
FROM raw_ecom_returns
UNION ALL
-- POS returns: original_ticket_line is 'T-ST01-0000123/2'
SELECT ticket_id || '/' || line_no,
       SUBSTR(original_ticket_line, 1, INSTR(original_ticket_line, '/') - 1),
       CAST(SUBSTR(original_ticket_line, INSTR(original_ticket_line, '/') + 1) AS INTEGER),
       DATE(ticket_ts), -quantity, ROUND(-(quantity * unit_price - discount), 2), 'Not recorded', 'POS'
FROM stg_pos_lines
WHERE quantity < 0;

DROP TABLE IF EXISTS stg_rejected_returns;
CREATE TABLE stg_rejected_returns AS
SELECT r.*, 'Return does not match any completed sale line' AS reject_reason
FROM stg_returns_all r
LEFT JOIN stg_order_lines l ON l.order_id = r.order_id AND l.line_no = r.line_no
WHERE l.order_id IS NULL;

DROP TABLE IF EXISTS stg_returns;
CREATE TABLE stg_returns AS
SELECT r.*
FROM stg_returns_all r
JOIN stg_order_lines l ON l.order_id = r.order_id AND l.line_no = r.line_no;
