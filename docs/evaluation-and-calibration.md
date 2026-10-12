# Evaluation and calibration

The loop is only trustworthy if every automatic change is **measured** and **human-approved**.
D2D applies change control to itself: calibrations are pull requests with before/after metrics.

## Feedback capture (stage 12)

Every story card and every chat answer has:

- 👍 / 👎 with a reason (`wrong_category`, `wrong_grouping`, `unclear_description`, `wrong_data`,
  `missing_data`, `unhelpful_answer`, `wrong_action`, `other`)
- Label correction dropdown (owner role only)
- Free text
- "Confirm" / "Reject" on proposed action items, "Record case" on stories

Feedback rows store the lineage of the thing being rated (see [architecture](architecture.md#lineage-and-versioning)).

## Feedback routing

| Feedback | Target stage | Automatic effect | Human gate |
|----------|--------------|------------------|------------|
| Label correction | 6 Labeled training set | Writes `label(source=human)`; queues retrain | Retrain PR |
| Wrong grouping / alert not useful | 4 Linking | Adds pair constraint to linking eval set; queues threshold search | Linking-config PR |
| Unclear description | 8 Enrichment | Adds example to template eval set | Template PR |
| Wrong / missing data | 2 Changelog | Opens a GitHub issue with lineage + sample rows | Issue triage |
| Engineer confirms / records case | 5 Categorization | Creates/updates case; confirmed stories become gold labels | Owner confirms |
| Unhelpful answer / grounding failure | 11 Agent skills | Adds conversation to skill eval set | Skill PR |
| Action item rejected | 11 Playbook | Logs reason against playbook step | Playbook PR |

## Nightly calibration job

Runs on the Mac mini (self-hosted runner, scheduled workflow) at 02:00 local:

1. **Collect** routed feedback since last run.
2. **Linking:** grid-search windows/weights on scenario ground truth + human pair constraints.
3. **ML:** retrain on the expanded labeled set; choose per-class thresholds to hit target precision.
4. **Enrichment / skills:** run eval sets; for skills, the LLM may *draft* a revised prompt/skill file
   from failure examples (this is the only place an LLM writes code-adjacent text, and it is always reviewed).
5. **Gate:** compare against the current production version on the **frozen golden set**.
6. **Propose:** if a target improves without regressions, open a PR (`calibration/<layer>/<date>`) with a
   metrics table, diff and sample changes. Nothing is merged automatically.

## Eval sets

| Set | Location | Used by |
|-----|----------|---------|
| Scenario ground truth | `data/ground_truth/` (generated, seeded) | Linking, ML, end-to-end |
| Golden stories | `evals/golden/stories.jsonl` (frozen, versioned) | ML + enrichment regression gate |
| Golden conversations | `evals/golden/conversations.jsonl` | Agent skills regression gate |
| Feedback-derived | `evals/feedback/*.jsonl` (grows) | Calibration targets |

## Metrics and gates

| Layer | Metric | Gate for calibration PR |
|-------|--------|-------------------------|
| Changelog | Data-quality rule recall on injected errors | ≥ 0.95 |
| Linking | Pairwise F1, Adjusted Rand Index | No drop > 0.01; improvement on target |
| Classifier | Per-class precision at chosen threshold, coverage, abstain rate | Precision ≥ 0.90 per class; coverage not lower |
| Enrichment | Template completeness, numeric correctness | 100 % numeric correctness |
| Agent | Grounding pass rate, skill rubric score (LLM judge), latency p95 | Grounding ≥ 0.98; rubric not lower; p95 < 20 s |

CI split:

- **GitHub-hosted (every PR):** lint, type check, unit tests, deterministic evals (changelog, linking,
  classifier, enrichment, grounding checker on recorded answers).
- **Mac mini self-hosted (label `llm-eval` on PR + nightly):** live LLM skill evals against Ollama.

## KPIs (the "days → minutes" claim)

Shown on the KPI dashboard (M10) and returned by `get_kpis`:

| KPI | Definition | Human-analyst baseline (configured) | Target |
|-----|------------|-------------------------------------|--------|
| Time to detect | first event → story created | ~1 day | < 15 min (one cadence) |
| Time to diagnose | story created → labeled (auto or human) | 2–3 days | < 5 min auto; < 1 day with review |
| Time to action plan | labeled → action items proposed | 2–3 days | < 2 min |
| Time to confirmed action | proposed → human confirmed | — | tracked |
| Auto-label coverage | 1 − abstain rate | 0 | ≥ 70 % by M9 |
| Precision (auto labels) | vs human review | — | ≥ 0.90 per class |
| Feedback correction rate | corrections / published stories | — | trending down |
| Loop latency | feedback → merged calibration | — | ≤ 2 days |
