# Development plan

## Principles

1. **Docs → issue → PR.** Every change starts from an issue in [planning/backlog.toml](../planning/backlog.toml)
   (seeded to GitHub). One issue ≈ one focused AI-assisted session ≈ one PR.
2. **Vertical slices.** Each milestone ends with something demoable on the Mac mini.
3. **Deterministic first.** Pipeline, ML, enrichment and grounding are testable without an LLM. LLM evals
   run only on the Mac mini.
4. **Everything versioned.** Configs, models, templates and skills carry a SHA recorded in lineage.
5. **Humans approve changes.** Calibration PRs and production deploys need a human click.

## Environments

| | Windows laptop (dev) | Mac mini M-series, 32 GB (prod) | GitHub |
|---|---|---|---|
| Purpose | Write code, run tests, ML experiments, act as reviewer | Run the whole system 24/7 | Control plane |
| Python | `uv` (3.12) | `uv` (3.12) | `astral-sh/setup-uv` |
| Containers | Docker Desktop (Postgres only) | OrbStack: Postgres, API, web | — |
| LLM | Remote Ollama on Mac mini via Tailscale, or `qwen3:8b` mocked in tests | **Ollama native** (`qwen3:14b`, `nomic-embed-text`) | — |
| Scheduling | manual `d2d run` | launchd: pipeline every 15 min, calibration 02:00 | scheduled workflow (self-hosted) |
| Exposure | localhost | Cloudflare Tunnel + Access (no open ports) | — |
| Data | small seeded datasets | rolling 90 days of simulated home | eval fixtures only |

Model memory budget on 32 GB: `qwen3:14b` Q4 ≈ 9–10 GB + KV cache ≈ 2 GB, embeddings < 1 GB,
Postgres + API + web ≈ 2 GB → ~16 GB headroom for macOS and training.

## Tech stack

| Concern | Choice |
|---------|--------|
| Language | Python 3.12 (backend, pipeline, ML), TypeScript (web) |
| Packaging | `uv` workspace, single `pyproject.toml` with optional groups |
| Lint / types / tests | `ruff`, `mypy --strict` on `src/`, `pytest`, `vitest` |
| Data | Parquet + DuckDB (transforms), Postgres 16 (ontology, HITL, feedback), Alembic migrations |
| ML | scikit-learn (LogisticRegression / HistGradientBoosting + `CalibratedClassifierCV`), pandas |
| API | FastAPI + Pydantic v2, SSE for chat streaming |
| Web | Vite + React + TypeScript, TanStack Query, generated OpenAPI client |
| LLM | OpenAI-compatible client → Ollama (default) / Anthropic (optional) |
| CLI | `d2d` (Typer): `simulate`, `run`, `review`, `train`, `calibrate`, `eval` |
| Notifications | ntfy (self-hosted topic) for new high-severity stories |

## Target repository layout

```
data-to-decision-ai/
  README.md
  pyproject.toml  uv.lock  Makefile  .env.example
  config/            household.toml  semantics.toml  linking.toml  categories.toml  llm.toml  pipeline.toml
  src/d2d/
    simulator/       devices.py  scenarios/  writer.py
    pipeline/        snapshots.py  changelog.py  quality.py  events.py  linking.py  run.py
    hitl/            review.py  cases.py  training_set.py  sim_reviewer.py
    ml/              features.py  train.py  thresholds.py  infer.py  registry.py
    enrichment/      templates/  impact.py  enrich.py
    ontology/        models.py  repo.py  permissions.py
    api/             main.py  routes/  auth.py  sse.py
    agent/           provider.py  tools.py  router.py  checker.py  runtime.py
    feedback/        capture.py  router.py  github_issues.py
    calibration/     jobs/  gate.py  pr.py
    kpi/             compute.py
    cli.py
  agent/skills/      explain_story/  household_briefing/  device_history/  why_question/  action_items/  glossary.toml
  migrations/        alembic/
  web/               Vite React app
  evals/             golden/  feedback/  run_evals.py
  models/            (gitignored; artifacts published to GitHub Releases)
  data/              (gitignored)
  deploy/            docker-compose.yml  docker-compose.dev.yml  launchd/  cloudflared/  runner/
  tests/             unit/  integration/  e2e/
  docs/  planning/  scripts/  .github/
```

## Branching and PR workflow

- `main` is always deployable. The repo is private on GitHub Free, so branch protection and required
  reviewers are unavailable: keep `ci` green before merging, and production deploys only on a manual
  **Run workflow** click (see [deployment.md](deployment.md)).
- Feature branches: `feat/<issue-id>-<slug>` (e.g. `feat/M1-02-simulator-core`).
- Calibration branches (opened by bots): `calibration/<layer>/<yyyy-mm-dd>`.
- Conventional commits; PR title starts with the issue ID: `[M1-02] Simulator core`.
- PR template requires: linked issue, tests, docs touched, and for calibration PRs the metrics table.

## CI/CD

| Workflow | Trigger | Runner | Jobs |
|----------|---------|--------|------|
| `ci.yml` (M0-01) | PR / push | GitHub-hosted | ruff, mypy, pytest, docs/backlog link check; later vitest + deterministic evals |
| `llm-eval.yml` (M10-01) | PR label `llm-eval`, nightly | self-hosted `mac-mini` | Golden conversation evals vs Ollama |
| `deploy.yml` (M4-03) | `ci` green on `main` → staging; Run workflow → production | self-hosted `mac-mini`, environments `staging` / `production` | build, smoke test, `docker compose up -d`, migrate, pipeline run, launchd agent, rollback on failure |
| `calibrate.yml` (M11-01) | schedule 02:00 / manual | self-hosted `mac-mini` | calibration jobs → PRs |

Self-hosted runner safety: repo is **private**, runner only accepts jobs from this repo, and
`pull_request` jobs from forks are disabled.

## Secrets

| Secret | Where |
|--------|-------|
| `POSTGRES_PASSWORD`, `D2D_SECRET_KEY` | `.env` on Mac mini (never committed), `.env.example` documents keys |
| `ANTHROPIC_API_KEY` (optional) | Mac mini `.env`; spend cap set in Anthropic console |
| `CALIBRATION_BOT_TOKEN` | Fine-grained PAT (Contents + Pull requests + Issues: write, this repo only) in Mac mini `.env` |
| Cloudflare tunnel token | Mac mini keychain / `.env` |

## Working session protocol (to stay within monthly AI credit)

Each development session follows the same steps:

1. Pick the next unblocked issue from the current milestone (dependencies listed in the issue).
2. Start the session with: *"Implement issue Mx-yy per docs; follow .github/copilot-instructions.md."*
3. Agent reads only the issue + linked docs, implements, adds tests, runs `make check`.
4. Open PR → CI green → you review/merge → deploy to Mac mini (approval click).
5. Tick the issue's acceptance criteria; close it.

Budget guidance: ~8–10 issues per month, sized S (≤ 1 session), M (1–2), L (2–3). If a month runs out of
credit, the remaining issues roll to the next month; milestones are ordered so nothing downstream starts
before its dependencies are done. Manual / non-AI tasks (Mac mini setup, Cloudflare, reviewing labels)
are tagged `manual` and cost no credit.

## Definition of done

- Acceptance criteria in the issue are met and demonstrated.
- Unit tests for new logic; integration test if a stage boundary changed.
- `make check` (lint, types, tests, deterministic evals) passes locally and in CI.
- Docs updated when behaviour, schema or config changed.
- Lineage/version fields populated for anything published.
