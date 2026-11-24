-- Question: How many customers are still buying 1, 3, 6 and 12 months after their first order, by acquisition channel?
-- Technique: cohort retention from the cohort mart; only cohorts old enough to have reached month 12 (first order by Oct 2025).
SELECT
    acquisition_channel,
    SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END)          AS customers,
    ROUND(100.0 * SUM(CASE WHEN months_since_first = 1 THEN active_customers END)
          / SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END), 1) AS month_1_pct,
    ROUND(100.0 * SUM(CASE WHEN months_since_first = 3 THEN active_customers END)
          / SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END), 1) AS month_3_pct,
    ROUND(100.0 * SUM(CASE WHEN months_since_first = 6 THEN active_customers END)
          / SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END), 1) AS month_6_pct,
    ROUND(100.0 * SUM(CASE WHEN months_since_first = 12 THEN active_customers END)
          / SUM(CASE WHEN months_since_first = 0 THEN cohort_customers END), 1) AS month_12_pct
FROM mart_customer_cohorts
WHERE cohort_month <= '2025-10-01'
GROUP BY acquisition_channel
ORDER BY month_12_pct DESC;
