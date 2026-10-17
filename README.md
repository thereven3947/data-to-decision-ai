# Data to Decision with AI (D2D)

![Change Control Beta: raw device changes become labeled stories and plain-language answers](docs/images/social-preview.png)

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

## Change Control Beta (demo)

The web app turns the simulated home's raw changes into reviewed, explained **change stories**, with a
local Copilot that answers questions about them. Screenshots from the Mac mini staging deploy
(regenerate with `uv run --with playwright python scripts/screenshots.py`):

<table>
  <tr>
    <td width="50%"><a href="docs/images/overview.png"><img src="docs/images/overview.png" alt="Overview"></a><br>
      <b>Overview</b> — stories, auto-label coverage, review backlog and every device at a glance.</td>
    <td width="50%"><a href="docs/images/story.png"><img src="docs/images/story.png" alt="Change story"></a><br>
      <b>Change story</b> — linked events, plain-language summary with computed impact, playbook, review and lineage.</td>
  </tr>
  <tr>
    <td><a href="docs/images/copilot.png"><img src="docs/images/copilot.png" alt="Ask the Copilot"></a><br>
      <b>Ask the Copilot</b> — local <code>qwen3:14b</code> via Ollama; every answer is grounding-checked and cites its stories.</td>
    <td><a href="docs/images/review.png"><img src="docs/images/review.png" alt="Review queue"></a><br>
      <b>Review queue</b> — abstained and audit-sampled stories; your label becomes training data.</td>
  </tr>
  <tr>
    <td><a href="docs/images/changelog.png"><img src="docs/images/changelog.png" alt="Semantic changelog"></a><br>
      <b>Semantic changelog</b> — typed snapshot diff with meaning from <code>semantics.toml</code>.</td>
    <td><a href="docs/images/events.png"><img src="docs/images/events.png" alt="Change events"></a><br>
      <b>Change events</b> — one idempotent event per meaningful change, linked to its story.</td>
  </tr>
</table>

A **Viewing as** switch shows what each role (owner, family, energy advisor, guest) may see, and 👍/👎
plus a 💬 Feedback button on every page collect UX feedback in Postgres.

## Security (private beta)

The demo runs on a personal machine, so it is private by default. The principles:

- **Local only.** Every service listens only on the machine it runs on; nothing is published to the
  internet or the local network, and requests addressed to other hostnames are refused.
- **Owner by invitation.** There is no sign-up. Accounts can only be created by someone already at
  the machine, and the beta allows a single owner account.
- **No stored passwords.** Only salted one-way hashes are kept, outside the repository.
- **Every request is checked on the server.** All data, review and Copilot endpoints require a valid
  session, and the server (not the browser) decides what each account may see and do.
- **A read-only Copilot.** The local LLM can only read data the signed-in user may see; it cannot
  change labels, run commands or open files, and its answers are checked against the data.
  Requests without a session are refused before the model runs.
- **Hardened browser surface.** Strict security headers, no cross-site access, and no secrets or
  source maps in the web bundle.
- **Secrets stay out of git.** Keys, accounts, data and models are git-ignored; secret scanning and
  push protection are on.
- **Clones are independent.** Anyone who clones the repo runs their own copy with their own accounts,
  data and model; it cannot reach the original machine, and forks cannot use its deploy runner.

These controls are tested in CI and were verified against the live deploy. Found a security issue?
Please open a private report through the repository's **Security** tab instead of a public issue.

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

The full toy loop runs locally on the Mac mini: simulator → changelog → change events → stories
(M1–M3), Postgres + deploys (M4), review queue and labels (M6), classifier with abstain (M7),
enrichment (M8), role-scoped API and the **Change Control web UI** (M9), and the **Copilot agent** on
local Ollama (M10), with 👍/👎 and page feedback stored for UX research (part of M11). Still open:
M4-04 (Cloudflare Tunnel), M5-03..06 (more CDC rules/scenarios, notifications) and M11 routing,
calibration PRs and KPI dashboard. See the [roadmap](docs/feature-roadmap.md) and
[backlog](planning/backlog.toml). Everything is toy data from the simulator; nothing connects to a
real home or any company system.

Run it locally (macOS; Postgres in Docker, Ollama with `qwen3:14b` for the agent):

```bash
uv sync
docker run -d --name d2d-dev-pg -e POSTGRES_USER=d2d -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=d2d \
  -p 127.0.0.1:55432:5432 postgres:16-alpine
export D2D_DATABASE_URL=postgresql+psycopg://d2d:dev@127.0.0.1:55432/d2d
uv run alembic upgrade head
uv run d2d simulate && uv run d2d run && uv run d2d load      # stages 1-4 -> Postgres
uv run d2d review export && uv run d2d train && uv run d2d classify   # stages 5-7
printf 'D2D_SECRET_KEY=%s\n' "$(openssl rand -hex 32)" >> .env   # once (.env is gitignored)
uv run d2d user add me --role owner --users-file users.toml       # prompts; users.toml is gitignored
set -a; . ./.env; set +a; export D2D_USERS_FILE=$PWD/users.toml
uv run d2d serve                       # API + agent (+ built UI) on http://127.0.0.1:8000
(cd web && npm ci && npm run dev)      # web UI with hot reload on http://127.0.0.1:5173
```

Useful commands: `d2d eval changelog|linking|classifier` (gates), `d2d review queue|label|case`,
`make check` (everything CI runs). Set `D2D_AUTH=off` to skip login during local
development (never on the Mac mini), `D2D_LLM=off` to run the agent without an LLM (grounded
fallback answers only) or `D2D_LLM_MODEL=qwen3:8b` for faster answers.

Create the GitHub milestones and issues from the backlog (idempotent, safe to re-run):

```powershell
uv run python scripts/seed_github_backlog.py --repo <owner>/data-to-decision-ai --dry-run
$env:GITHUB_TOKEN = "<token with Issues: read and write>"
uv run python scripts/seed_github_backlog.py --repo <owner>/data-to-decision-ai
uv run pytest
```
