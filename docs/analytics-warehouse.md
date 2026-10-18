# Analytics warehouse: BigQuery and Looker

D2D's operational store is Postgres; analytics run on a **star schema** exported as Parquet. The same
files load into BigQuery (for Looker / Looker Studio dashboards) or can be queried locally with
DuckDB. Infrastructure for the BigQuery side is in [`infra/terraform/gcp`](../infra/terraform/gcp/).

```bash
uv run d2d export warehouse --data-dir data            # files + Postgres (labels, agent, feedback)
uv run d2d export warehouse --data-dir data --no-db    # pipeline files only
analytics/bigquery/load.sh MY_GCP_PROJECT d2d_analytics data/warehouse
```

## Tables

| Table | Grain | Source |
|---|---|---|
| `dim_device` | device | `household.toml` (asset inventory) |
| `dim_circuit` | circuit | `household.toml` |
| `dim_category` | category | `categories.toml` (taxonomy, with grid analogue) |
| `fact_change_event` | change event | `events/events.parquet`, linked to its `story_id` |
| `fact_story` | change story | `stories/stories.parquet` + label, confidence, status from Postgres |
| `fact_security_alert` | scored config/topology change | `security/alerts.parquet` ([security analytics](security-analytics.md)) |
| `fact_agent_answer` | Copilot answer | Postgres `chat_message`: grounded, latency, tokens, tool calls, citations |
| `fact_feedback` | feedback item | Postgres `feedback`: rating, reason, where it was routed |

Local dates and hours are precomputed in the home's time zone (`story_date`, `event_date`,
`event_hour`) so every engine groups days the same way. **No free text is exported:** questions,
answers, comments and usernames stay in Postgres; agent answers carry only quality metrics.

## KPI views

[`analytics/bigquery/views.sql`](../analytics/bigquery/views.sql) is portable SQL. It runs unchanged
in BigQuery and DuckDB, and the test suite runs it in DuckDB against a fresh export.

| View | Answers |
|---|---|
| `v_daily_stories` | How many stories and events per day and category, how many auto-labeled or in review |
| `v_label_kpis` | Auto-label coverage and review backlog (the "days → minutes" KPI) |
| `v_device_activity` | Which devices change most, joined to the inventory |
| `v_circuit_capacity` | Connected load vs continuous rating (80% rule) per circuit |
| `v_security_daily` | Config changes scored and alerts raised per day |
| `v_agent_quality` | Copilot answers per day and skill: grounding pass rate, latency, tool calls, tokens |
| `v_feedback_routing` | Feedback volume and where the loop routed it |

## Looker (semantic layer)

[`analytics/looker`](../analytics/looker/) is a LookML project: one model with three explores
(change stories, configuration integrity, Copilot quality) and views that define each measure once:
`auto_label_coverage`, `review_backlog`, `alerts`, `grounded_rate`, `p95_latency_ms`. The test suite
checks that every explore and join points at a defined view and every view at an exported table.
Point the `d2d_bigquery` connection at the dataset that `load.sh` filled.

## Looker Studio dashboard (no LookML needed)

1. Run the export and `load.sh` (or use the BigQuery console: *Create table → Upload → Parquet*).
2. In [Looker Studio](https://lookerstudio.google.com) choose *Create → Data source → BigQuery*,
   pick the project, dataset `d2d_analytics` and add the views as data sources.
3. Suggested page layout:
   - **Scorecards** from `v_label_kpis`: stories, auto-label coverage, review backlog.
   - **Stacked bar** from `v_daily_stories`: `story_date` × `stories`, broken down by `category`.
   - **Table with heatmap** from `v_device_activity`: devices by `change_events`.
   - **Bar with reference line at 1.0** from `v_circuit_capacity`: `utilization` per circuit.
   - **Time series** from `v_security_daily` (alerts) and `v_agent_quality` (`grounded_rate`,
     `avg_latency_ms`).
4. Share the report with view access only; the dataset stays private.
