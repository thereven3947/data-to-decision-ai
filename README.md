# Data to Decision with AI (D2D)

A small-scale, closed-loop **change intelligence** system: it turns raw configuration and operational
changes into labeled **change stories**, explains them in plain language through an embedded AI
agent, and uses human feedback to calibrate every layer — pipeline, ML, enrichment and agent skills.

The toy domain is a **smart home** (robot vacuum, washer, dishwasher, thermostat, water heater,
EV charger, smart plugs, ...). It deliberately mirrors grid operations: devices sit on circuits
behind upstream equipment (panel, hub, router), are governed by policy zones (quiet hours, peak
tariff), and are affected by weather / demand-response windows.

## Why

With human-powered analysts, diagnosing a cluster of changes takes days, and producing an action
plan (fix the pipeline, take an operational action) takes days more. D2D shows how a closed loop
with **human-in-the-loop (HITL)** control compresses that to **minutes** while keeping humans in
charge of what matters:

- Humans own the **category taxonomy**, **review abstained stories**, **confirm cases and action items**,
  and **approve every calibration change** (as a pull request).
- ML does the **labeling** — precision-first, abstaining to human review when unsure.
- The LLM **never labels**. It reads labels, enriched descriptions and coded skills, and converts them
  into plain-language answers, briefings and *proposed* action items.

## Pipeline at a glance

| # | Stage | Toy implementation |
|---|-------|--------------------|
| 1 | Asset snapshots | Simulator (later Home Assistant) writes periodic device snapshots |
| 2 | Semantic changelog | Typed diff of consecutive snapshots: what changed, old → new, meaning |
| 3 | Change events | One event per property change per device, with timestamp |
| 4 | Event linking | Graph grouping by device, circuit, upstream (panel/hub/router), automation, time |
| 5 | Human categorization | Review queue + case workflow in the Change Control UI |
| 6 | Labeled training set | Change groups paired with human categories, versioned with provenance |
| 7 | ML classifier | Calibrated, precision-first per category; abstain → human review |
| 8 | Enrichment | Deterministic description from category + linked events + impact |
| 9 | Ontology | Stories, events, devices, categories, confidence, descriptions, cases |
| 10 | Ontology API | Read API scoped by each user's permissions (typed client SDK) |
| 11 | Copilot agent | Local open-source LLM + read-only tools + coded skills |
| 12 | Chat + feedback | Chat history, corrections, reported issues → routed back to stages 2/4/5/6/8/11 |

## Where things run

| Machine | Role |
|---------|------|
| **Mac mini** | Production: Postgres, scheduled pipeline, ML inference, Ollama (native, Metal), agent API, web UI, Cloudflare Tunnel, GitHub self-hosted runner, nightly calibration |
| **Windows laptop** | Development: simulator authoring, notebooks / ML experiments, HITL reviewer, PR review |
| **GitHub** | Source of truth, Issues/Milestones backlog, Actions CI + eval gates, `production` environment approvals, Releases for model artifacts |

## Documentation

- [Architecture](docs/architecture.md) — components, data flow, closed loops
- [Domain model](docs/domain-model.md) — smart-home ↔ grid mapping, schemas, categories
- [Agent and skills](docs/agent-and-skills.md) — what the LLM may and may not do
- [Evaluation and calibration](docs/evaluation-and-calibration.md) — feedback routing, eval gates, KPIs
- [Development plan](docs/development-plan.md) — environments, repo layout, CI/CD, workflow
- [Feature roadmap](docs/feature-roadmap.md) — milestones M0–M11 and exit criteria
- [Mac mini setup runbook](docs/mac-mini-setup.md) — M4-01 manual checklist
- [Architecture decision records](docs/adr/)
- [Backlog](planning/backlog.toml) — source of truth for GitHub milestones and issues

## Status

**M0 Foundations**, **M1 Simulator** and **M2 Changelog & events** are done; next is **M3 Event linking**
(see the [roadmap](docs/feature-roadmap.md) and [backlog](planning/backlog.toml)).

```powershell
python -m pip install uv     # once
uv sync
uv run d2d simulate          # 3 days of 15-min snapshots + 12 planted scenarios -> data/
uv run d2d run               # stages 2-3: semantic changelog + change events
uv run d2d eval changelog    # score events vs ground truth (exit 1 if a gate fails)
uv run alembic upgrade head  # optional: Postgres schema (needs D2D_DATABASE_URL, see .env.example)
uv run d2d load              # optional: devices, change events and stories -> Postgres ontology.*
```

Create the GitHub milestones and issues from the backlog (idempotent, safe to re-run):

```powershell
uv run python scripts/seed_github_backlog.py --repo <owner>/data-to-decision-ai --dry-run
$env:GITHUB_TOKEN = "<token with Issues: read and write>"
uv run python scripts/seed_github_backlog.py --repo <owner>/data-to-decision-ai
uv run pytest
```
