-- KPI views over the D2D warehouse tables (see docs/analytics-warehouse.md).
-- Portable SQL: runs unchanged in BigQuery (GoogleSQL) and DuckDB, and the tests run it in DuckDB.
-- {dataset} is replaced by the loader, e.g. d2d_analytics. `/` is float division in both engines.

-- Stories and change events per local day and category.
CREATE OR REPLACE VIEW {dataset}.v_daily_stories AS
SELECT
  s.story_date,
  COALESCE(c.name, 'Unlabeled') AS category,
  COUNT(*) AS stories,
  SUM(s.event_count) AS change_events,
  SUM(CASE WHEN s.label_source = 'model' THEN 1 ELSE 0 END) AS auto_labeled,
  SUM(CASE WHEN s.status = 'needs_review' THEN 1 ELSE 0 END) AS in_review
FROM {dataset}.fact_story AS s
LEFT JOIN {dataset}.dim_category AS c ON c.category_id = s.category_id
GROUP BY s.story_date, COALESCE(c.name, 'Unlabeled');

-- Headline KPIs: auto-label coverage and review backlog (the "days -> minutes" story).
CREATE OR REPLACE VIEW {dataset}.v_label_kpis AS
SELECT
  COUNT(*) AS stories,
  SUM(CASE WHEN label_source = 'model' THEN 1 ELSE 0 END) AS auto_labeled,
  SUM(CASE WHEN label_source = 'human' THEN 1 ELSE 0 END) AS human_labeled,
  SUM(CASE WHEN status = 'needs_review' THEN 1 ELSE 0 END) AS review_backlog,
  SUM(CASE WHEN label_source = 'model' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)
    AS auto_label_coverage
FROM {dataset}.fact_story;

-- Change activity per device, joined to the asset inventory.
CREATE OR REPLACE VIEW {dataset}.v_device_activity AS
SELECT
  d.device_id,
  d.kind,
  d.room,
  d.circuit_id,
  COUNT(e.event_id) AS change_events,
  COUNT(DISTINCT e.story_id) AS stories,
  MAX(e.event_ts) AS last_change
FROM {dataset}.dim_device AS d
LEFT JOIN {dataset}.fact_change_event AS e ON e.device_id = d.device_id
GROUP BY d.device_id, d.kind, d.room, d.circuit_id;

-- Load on each circuit vs its continuous rating (80% rule at 120 V), for capacity reviews.
CREATE OR REPLACE VIEW {dataset}.v_circuit_capacity AS
SELECT
  c.circuit_id,
  c.rating_amps,
  c.rating_amps * 120 * 0.8 AS continuous_capacity_w,
  SUM(CASE WHEN d.active THEN d.rated_w ELSE 0 END) AS connected_load_w,
  SUM(CASE WHEN d.active THEN d.rated_w ELSE 0 END) / (c.rating_amps * 120 * 0.8) AS utilization
FROM {dataset}.dim_circuit AS c
LEFT JOIN {dataset}.dim_device AS d ON d.circuit_id = c.circuit_id
GROUP BY c.circuit_id, c.rating_amps;

-- Configuration-integrity alerts per day (d2d security scan).
CREATE OR REPLACE VIEW {dataset}.v_security_daily AS
SELECT
  event_date,
  COUNT(*) AS changes_scored,
  SUM(CASE WHEN alert THEN 1 ELSE 0 END) AS alerts,
  MAX(score) AS max_score
FROM {dataset}.fact_security_alert
GROUP BY event_date;

-- Copilot quality per day and skill: volume, grounding pass rate, latency, tokens.
CREATE OR REPLACE VIEW {dataset}.v_agent_quality AS
SELECT
  answer_date,
  skill,
  COUNT(*) AS answers,
  SUM(CASE WHEN grounded THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS grounded_rate,
  AVG(latency_ms) AS avg_latency_ms,
  MAX(latency_ms) AS max_latency_ms,
  AVG(tool_calls) AS avg_tool_calls,
  SUM(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) AS tokens
FROM {dataset}.fact_agent_answer
GROUP BY answer_date, skill;

-- Feedback volume and where it was routed (stage 12 loop).
CREATE OR REPLACE VIEW {dataset}.v_feedback_routing AS
SELECT
  feedback_date,
  target_type,
  rating,
  COALESCE(routed_to, 'unrouted') AS routed_to,
  COUNT(*) AS items
FROM {dataset}.fact_feedback
GROUP BY feedback_date, target_type, rating, COALESCE(routed_to, 'unrouted');
