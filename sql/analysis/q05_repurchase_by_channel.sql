-- Question: What is the 90-day repurchase rate by acquisition channel, and is it improving?
-- Technique: weighted rates from the repurchase mart (sum of repeaters / sum of eligible), pivot by cohort year.
SELECT
    acquisition_channel,
    SUM(eligible_customers)                                                 AS eligible_customers,
    ROUND(100.0 * SUM(repeat_customers) / SUM(eligible_customers), 1)       AS repurchase_rate_pct,
    ROUND(100.0 * SUM(CASE WHEN cohort_year = 2024 THEN repeat_customers END)
          / SUM(CASE WHEN cohort_year = 2024 THEN eligible_customers END), 1) AS cohort_2024_pct,
    ROUND(100.0 * SUM(CASE WHEN cohort_year = 2025 THEN repeat_customers END)
          / SUM(CASE WHEN cohort_year = 2025 THEN eligible_customers END), 1) AS cohort_2025_pct,
    ROUND(100.0 * SUM(CASE WHEN cohort_year = 2026 THEN repeat_customers END)
          / SUM(CASE WHEN cohort_year = 2026 THEN eligible_customers END), 1) AS cohort_2026_pct,
    ROUND(SUM(avg_days_to_second_order * repeat_customers) / SUM(repeat_customers), 0) AS avg_days_to_second_order
FROM mart_repurchase
GROUP BY acquisition_channel
ORDER BY repurchase_rate_pct DESC;
