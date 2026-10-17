# Feature roadmap

Milestones follow the pipeline stages. Each one ends with something demoable; issues live in
[planning/backlog.toml](../planning/backlog.toml).

| Milestone | Stages | Scope | Exit criteria | Status |
|-----------|--------|-------|---------------|--------|
| **M0 · Foundations** | — | uv project, `d2d` CLI, ruff/mypy/pytest, CI, config loading | `make check` green locally and in CI | ✅ done |
| **M1 · Simulator** | 1 | Household topology, baseline device model, 12 scenario injectors, Parquet snapshots, ground truth JSONL | `d2d simulate` is deterministic for a seed; every scenario visible in snapshots | ✅ done |
| **M2 · Changelog & events** | 2–3 | Typed diff, `semantics.toml` rules, data-quality rules, change events, `d2d run`, `d2d eval changelog` | Event recall 1.0 and DQ recall ≥ 0.95 on ground truth; no noise events | ✅ done |
| **M3 · Event linking** | 4 | Graph linking by device / topology / campaign / window, `linking.toml` | Pairwise F1 and ARI vs scenarios reported; baseline committed | ✅ done: precision 1.00, recall 0.848, F1 0.918, ARI 0.907 with the recurrence rule (was F1 0.667 without it). Remaining splits: the multi-device heat wave and child-lock scenarios need a weather / policy-window signal |
| **M4 · Mac mini & Postgres** | — | Mac mini setup, OrbStack Postgres, Alembic, self-hosted runner, deploy workflow, Cloudflare Tunnel | Push to `main` deploys to the Mac mini after an approval click | in progress: M4-01 setup, M4-02 Postgres + Alembic (`d2d load`) and M4-03 runner + deploy (staging auto, production on Run workflow) done; M4-04 next |
| **M5 · Event-native devices & notifications** | 1–4 | Ring camera, Ring doorbell, light switches, garage door; CDC event stream next to snapshots; context rules (duration, away mode, quiet hours); home-notification stories pushed via ntfy | All new scenarios recovered from CDC (recall 1.0, no noise); ≤ 1 push per story; quiet hours respected | in progress: M5-01, M5-02 done; M5-03 started (duration rule `garage_left_open`); M5-06 `dq.cdc_gap` added |
| **M6 · HITL review** | 5–6 | Review queue, cases, labeled training set with provenance, simulated reviewer | Stories labeled through the UI/CLI and versioned | ✅ done: `hitl.label` (append-only, provenance), `hitl.review_item` (abstain + 20 % audit sample), cases, `d2d review` CLI + review UI, simulated reviewer from ground truth, versioned training set (`d2d review export`) |
| **M7 · ML classifier** | 7 | Features, calibrated classifier, per-class thresholds, abstain | Precision ≥ 0.90 per class on golden set | ✅ done: calibrated LogisticRegression on story features, per-class thresholds from out-of-fold precision, abstain → review, `d2d train` / `classify` / `eval classifier` (golden set, gate precision ≥ 0.90, coverage ≥ 0.70) |
| **M8 · Enrichment & ontology** | 8–9 | Templated descriptions, impact calculators, ontology tables | 100 % numeric correctness on golden stories | ✅ done: Jinja2 templates per category (`config/enrichment.toml`), impact calculators (kWh / $ shifted, circuit load %, RSSI gain, error episodes) with exact-number tests; computed on read, `enrichment_template_sha` in lineage |
| **M9 · Ontology API & UI** | 10 | FastAPI read API, permissions, Change Control web UI | Each role sees only its scope (tests) | ✅ done: FastAPI (`d2d serve`), login (scrypt hashes outside git, signed session cookies) with one role per account from `config/roles.toml`; owners can preview other roles, web UI (Vite + React): overview, stories, changelog, events, devices, review, Copilot, feedback |
| **M10 · Copilot agent** | 11 | Ollama provider, read-only tools, skills, router, grounding checker | Grounding ≥ 0.98 on golden conversations | ✅ done (no `llm-eval` workflow yet): Ollama `qwen3:14b`, 6 read-only tools, 6 coded skills + router, grounding checker with one retry and a templated fallback, answers stored in `feedback.chat_message` |
| **M11 · Feedback, calibration & KPIs** | 12 | Feedback capture/routing, nightly calibration PRs, KPI dashboard | One full loop: feedback → calibration PR → merged → better metric | in progress: 👍/👎 + reason on stories and answers and per-page comments stored in `feedback.feedback`; KPIs on the overview. Next: routing, nightly calibration PRs, KPI dashboard |

M4 is placed right after the first data-plane slices so the Mac mini (arriving 2026-10-15) runs the
pipeline on a schedule as early as possible.

## M5 · Event-native devices and notifications

Snapshot devices (washer, thermostat, ...) change slowly, so a 15-minute diff is enough. Doors,
switches, cameras and doorbells change **in seconds**: a garage that opens and closes between two
snapshots would be invisible. M5 adds a second stage-1 input, a **change-data-capture (CDC) event
stream**, and keeps snapshots for their slow-changing configuration (firmware, motion sensitivity,
recording mode, light automations).

| Device | CDC events (seconds) | Snapshot config (15 min) | Grid analogue |
|--------|----------------------|--------------------------|---------------|
| Ring camera | `motion`, `person_detected`, `online/offline` | firmware, motion zones, sensitivity, recording mode | Substation intrusion / CCTV alarm |
| Ring doorbell | `ring`, `motion`, `answered`, `battery_low` | firmware, chime, quiet-hours setting | Customer call / trouble report |
| Light switch | `on` / `off`, source (manual / automation / app) | automation schedule, dimmer level | Switch open / close operation |
| Garage door | `open` / `close`, source (remote / app / button) | auto-close timer, firmware | Recloser / sectionalizer operation |

Routine events (a light turning on at dusk) stay quiet. **Context rules** in `semantics.toml`
turn event sequences into meaningful changes:

- **Duration:** garage open > 30 min, light on > 4 h while nobody is home.
- **Policy zone:** motion or a garage opening during `away_mode` or `quiet_hours`.
- **Sequence:** doorbell `ring` → camera `person_detected` → no `answered` within 2 min = missed visitor.

New planted scenarios (with ground truth): `package_delivery`, `garage_left_open_overnight`,
`lights_left_on_away`, `motion_while_away`, `doorbell_battery_low`, `camera_offline`,
`lighting_automation_changed`.

Linked groups become **notification stories** with a severity (`info` = daily digest,
`notice` = push, `alert` = push and bypass quiet hours). The Mac mini pushes them through ntfy,
at most one push per story, with updates folded into the same notification.

## Toy simplifications (by design, revisit before real use)

- **Simple login:** accounts live in a local users file managed with `d2d user` (no sign-up, no
  password reset by email, no 2FA). Put Cloudflare Access in front before exposing the app (M4-04).
- **Enrichment is computed on read** from events + templates (deterministic, versioned by
  `enrichment_template_sha`) instead of being stored in an ontology table.
- **Golden set = default scenarios:** `evals/golden/stories.jsonl` is frozen from the default
  simulator run (`scripts/freeze_golden.py`), so the classifier gate is a regression check, not a
  generalization estimate. Human labels from the UI grow the real training set.
- **Simulator replays the same 3 days** every 15 minutes, so story ids stay stable and reviews persist.
- **Grounding covers ids, categories and numbers with units** (kWh, W, $, %, dB). Plain counts and
  times in a Copilot answer are not checked yet, so the model can still misstate them; 👎 feedback
  with reason `wrong_data` is how these get collected.
