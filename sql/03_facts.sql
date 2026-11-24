-- =====================================================================================
-- 03 FACTS: order lines (the grain of every sales metric), orders and returns.
-- Returns are attributed back to the sale they reverse, so net sales of a month include
-- the refunds of that month's orders (recent months restate as late returns arrive).
-- =====================================================================================

DROP TABLE IF EXISTS fct_returns;
CREATE TABLE fct_returns AS
SELECT
    r.return_id,
    r.order_id || '-' || r.line_no                                          AS order_line_id,
    r.order_id,
    r.return_date,
    o.order_date,
    CAST(JULIANDAY(r.return_date) - JULIANDAY(o.order_date) AS INTEGER)     AS days_to_return,
    o.channel_id,
    l.product_id,
    p.category,
    r.quantity,
    r.refund_amount,
    r.reason,
    r.source_system
FROM stg_returns r
JOIN stg_order_lines l ON l.order_id = r.order_id AND l.line_no = r.line_no
JOIN stg_orders o ON o.order_id = r.order_id
JOIN dim_product p ON p.product_id = l.product_id;

CREATE INDEX ix_fct_returns ON fct_returns (order_line_id);


DROP TABLE IF EXISTS fct_order_lines;
CREATE TABLE fct_order_lines AS
WITH returned AS (
    SELECT order_line_id, SUM(quantity) AS quantity, SUM(refund_amount) AS amount
    FROM fct_returns
    GROUP BY order_line_id
),
lines AS (
    SELECT
        l.order_id || '-' || l.line_no                                      AS order_line_id,
        l.order_id, l.line_no, o.order_date, o.order_ts,
        o.customer_id, o.channel_id, o.store_id,
        l.product_id, p.category, p.subcategory, p.brand, p.is_private_label,
        l.promo_code,
        l.quantity,
        l.unit_list_price,
        ROUND(l.quantity * l.unit_list_price, 2)                            AS gross_sales,
        l.discount_amount,
        COALESCE(r.quantity, 0)                                             AS returned_quantity,
        COALESCE(r.amount, 0)                                               AS returned_amount,
        p.unit_cost,
        c.commission_rate
    FROM stg_order_lines l
    JOIN stg_orders o ON o.order_id = l.order_id
    JOIN dim_product p ON p.product_id = l.product_id
    JOIN dim_channel c ON c.channel_id = o.channel_id
    LEFT JOIN returned r ON r.order_line_id = l.order_id || '-' || l.line_no
)
SELECT
    order_line_id, order_id, line_no, order_date, order_ts, customer_id, channel_id, store_id,
    product_id, category, subcategory, brand, is_private_label, promo_code,
    promo_code IS NOT NULL                                                  AS is_promo,
    quantity, unit_list_price, gross_sales, discount_amount, returned_quantity, returned_amount,
    ROUND(gross_sales - discount_amount - returned_amount, 2)              AS net_sales,
    ROUND(unit_cost * (quantity - returned_quantity), 2)                   AS cogs,
    ROUND(gross_sales - discount_amount - returned_amount - unit_cost * (quantity - returned_quantity), 2) AS gross_margin,
    ROUND(commission_rate * (gross_sales - discount_amount - returned_amount), 2) AS commission,
    ROUND(gross_sales - discount_amount - returned_amount - unit_cost * (quantity - returned_quantity)
          - commission_rate * (gross_sales - discount_amount - returned_amount), 2) AS contribution_margin
FROM lines;

CREATE UNIQUE INDEX ix_fct_order_lines ON fct_order_lines (order_line_id);
CREATE INDEX ix_fct_order_lines_date ON fct_order_lines (order_date);
CREATE INDEX ix_fct_order_lines_customer ON fct_order_lines (customer_id);


DROP TABLE IF EXISTS fct_orders;
CREATE TABLE fct_orders AS
WITH totals AS (
    SELECT order_id,
           COUNT(*) AS lines, SUM(quantity) AS units, SUM(gross_sales) AS gross_sales,
           SUM(discount_amount) AS discount_amount, SUM(returned_amount) AS returned_amount,
           SUM(net_sales) AS net_sales, SUM(cogs) AS cogs, SUM(gross_margin) AS gross_margin,
           SUM(commission) AS commission, SUM(contribution_margin) AS contribution_margin,
           MAX(is_promo) AS has_promo, MAX(returned_quantity > 0) AS has_return
    FROM fct_order_lines
    GROUP BY order_id
)
SELECT
    o.order_id, o.order_date, o.order_ts, o.customer_id, o.channel_id, o.store_id, o.source_system,
    t.lines, t.units,
    ROUND(t.gross_sales, 2) AS gross_sales, ROUND(t.discount_amount, 2) AS discount_amount,
    ROUND(t.returned_amount, 2) AS returned_amount, ROUND(t.net_sales, 2) AS net_sales,
    ROUND(t.cogs, 2) AS cogs, ROUND(t.gross_margin, 2) AS gross_margin, ROUND(t.commission, 2) AS commission,
    ROUND(t.contribution_margin, 2) AS contribution_margin,
    t.has_promo, t.has_return,
    CASE WHEN o.customer_id IS NULL THEN NULL
         ELSE ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.order_ts, o.order_id) END AS customer_order_number,
    CASE WHEN o.customer_id IS NULL THEN NULL
         ELSE CAST(JULIANDAY(o.order_date) - JULIANDAY(LAG(o.order_date) OVER (
              PARTITION BY o.customer_id ORDER BY o.order_ts, o.order_id)) AS INTEGER) END AS days_since_previous_order
FROM stg_orders o
JOIN totals t ON t.order_id = o.order_id;

CREATE UNIQUE INDEX ix_fct_orders ON fct_orders (order_id);
CREATE INDEX ix_fct_orders_customer ON fct_orders (customer_id, order_date);
