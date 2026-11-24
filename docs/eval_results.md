# Evaluation results

Each question in [`eval/questions.yaml`](../eval/questions.yaml) has a gold SQL query. The assistant's answer counts as correct when its result matches the gold result (execution accuracy; see [`src/evaluation.py`](../src/evaluation.py)). Regenerate with `python scripts/run_eval.py`.

| Model | Correct | Execution accuracy | Run on |
|---|---|---|---|
| Claude (`claude-sonnet-5-5`) | 24 / 25 | **96%** | 2026-11-24 |
| Offline baseline: no LLM, reuses the closest worked example | 0 / 25 | **0%** | 2026-11-23 |

## Claude · `claude-sonnet-5-5`

| # | Question | Result | Detail |
|---|---|---|---|
| q01 | ¿Qué categoría creció más el último trimestre? | ✅ |  |
| q02 | ¿Cuál es la tasa de recompra por canal? | ✅ |  |
| q03 | How much did we sell this year compared with the same period last year? | ✅ |  |
| q04 | ¿Cuáles fueron las ventas netas por canal en 2025? | ✅ |  |
| q05 | What is the gross margin % by category this year? | ✅ |  |
| q06 | What is the average order value by channel in 2026? | ✅ |  |
| q07 | How many active customers do we have? | ✅ |  |
| q08 | Which store had the highest net sales last quarter? | ✅ |  |
| q09 | What is the return rate by category? | ✅ |  |
| q10 | Show monthly net sales for 2025. | ✅ |  |
| q11 | How many new customers did each channel acquire in 2026? | ✅ |  |
| q12 | What share of net sales came from online channels in 2026? | ✅ |  |
| q13 | Which promotion generated the most incremental gross margin? | ✅ |  |
| q14 | What percentage of net sales was sold on promotion each year? | ✅ |  |
| q15 | What is the 3-month retention of customers acquired in January 2025? | ✅ |  |
| q16 | ¿Cuál es el ticket promedio en tiendas físicas frente a los canales en línea en 2026? | ❌ | no column matches channel_type |
| q17 | What were the top 5 products by net sales in 2026? | ✅ |  |
| q18 | How many orders did we have in October 2026, and how does that compare with October 2025? | ✅ |  |
| q19 | What is the contribution margin % by channel in 2026? | ✅ |  |
| q20 | Which department has the most active customers? | ✅ |  |
| q21 | What were Beauty net sales by quarter in 2026? | ✅ |  |
| q22 | ¿Qué porcentaje de las ventas netas de Belleza en 2026 vino de productos de marca propia? | ✅ |  |
| q23 | What are the three most common return reasons for online orders, by number of returns? | ✅ |  |
| q24 | Among customers who placed a second order, what is the average number of days between their first and second order, by acquisition channel? | ✅ |  |
| q25 | Which month of 2025 had the highest net sales? | ✅ |  |

## Offline baseline: no LLM, reuses the closest worked example · `nearest-example`

| # | Question | Result | Detail |
|---|---|---|---|
| q01 | ¿Qué categoría creció más el último trimestre? | ❌ | Demo mode: no worked example is close to this question. |
| q02 | ¿Cuál es la tasa de recompra por canal? | ❌ | Demo mode: no worked example is close to this question. |
| q03 | How much did we sell this year compared with the same period last year? | ❌ | answer too large to check |
| q04 | ¿Cuáles fueron las ventas netas por canal en 2025? | ❌ | Demo mode: no worked example is close to this question. |
| q05 | What is the gross margin % by category this year? | ❌ | 4 rows, expected 6 |
| q06 | What is the average order value by channel in 2026? | ❌ | 10 rows, expected 4 |
| q07 | How many active customers do we have? | ❌ | answer too large to check |
| q08 | Which store had the highest net sales last quarter? | ❌ | first row lacks ['Casa Ceiba Zona 10'] |
| q09 | What is the return rate by category? | ❌ | 4 rows, expected 6 |
| q10 | Show monthly net sales for 2025. | ❌ | no column matches month_start |
| q11 | How many new customers did each channel acquire in 2026? | ❌ | 12 rows, expected 4 |
| q12 | What share of net sales came from online channels in 2026? | ❌ | answer too large to check |
| q13 | Which promotion generated the most incremental gross margin? | ❌ | first row lacks ['Christmas toys 2025'] |
| q14 | What percentage of net sales was sold on promotion each year? | ❌ | 11 rows, expected 3 |
| q15 | What is the 3-month retention of customers acquired in January 2025? | ❌ | value 11.8 not found |
| q16 | ¿Cuál es el ticket promedio en tiendas físicas frente a los canales en línea en 2026? | ❌ | Demo mode: no worked example is close to this question. |
| q17 | What were the top 5 products by net sales in 2026? | ❌ | 10 rows, expected 5 |
| q18 | How many orders did we have in October 2026, and how does that compare with October 2025? | ❌ | value 4765 not found |
| q19 | What is the contribution margin % by channel in 2026? | ❌ | no column matches contribution_margin_pct |
| q20 | Which department has the most active customers? | ❌ | first row lacks ['Guatemala'] |
| q21 | What were Beauty net sales by quarter in 2026? | ❌ | 12 rows, expected 4 |
| q22 | ¿Qué porcentaje de las ventas netas de Belleza en 2026 vino de productos de marca propia? | ❌ | Demo mode: no worked example is close to this question. |
| q23 | What are the three most common return reasons for online orders, by number of returns? | ❌ | 4 rows, expected 3 |
| q24 | Among customers who placed a second order, what is the average number of days between their first and second order, by acquisition channel? | ❌ | 10 rows, expected 4 |
| q25 | Which month of 2025 had the highest net sales? | ❌ | first row lacks ['2025-12'] |
