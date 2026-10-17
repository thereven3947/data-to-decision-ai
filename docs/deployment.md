# Deployment on the Mac mini (M4-03)

Push to `main` → `ci` (GitHub-hosted) → `deploy` to **staging** on the Mac mini's self-hosted runner.
**Production** deploys only when you click **Actions → deploy → Run workflow** (that click is the
approval). GitHub Free does not offer required reviewers or branch protection on private repos, so
the click replaces the environment reviewer; both environments only accept deployments from `main`.

## Pieces

| Piece | Where | What |
|-------|-------|------|
| Runner | `~/actions-runner`, launchd agent `actions.runner.thereven3947-data-to-decision-ai.mac-mini` | Labels `self-hosted, macOS, ARM64, mac-mini`; installed by [deploy/runner/install.sh](../deploy/runner/install.sh) |
| Workflow | [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) | `workflow_run` after `ci` → staging; `workflow_dispatch` → production (or staging) |
| Deploy script | [deploy/deploy.sh](../deploy/deploy.sh) | Build release → smoke test → switch → compose up → migrate → pipeline run → launchd agent, rollback on failure |
| Services | [deploy/docker-compose.yml](../deploy/docker-compose.yml) | Postgres 16 per environment (compose project `d2d-<env>`, localhost only) |
| Schema | [migrations/alembic](../migrations/alembic) via [alembic.ini](../alembic.ini) | `ontology.device`, `change_event`, `change_story`, `change_story_event` (M4-02) |
| Schedule | launchd agent `com.d2d.pipeline.<env>` from `d2d launchd-plist` | Runs [deploy/pipeline.sh](../deploy/pipeline.sh) (`simulate` → `run` → `load` → `review export` → `train` (only if labels changed) → `classify`) every `cadence_minutes` (`config/pipeline.toml`, 15 min) |
| API + web UI | launchd agent `com.d2d.api.<env>` (`--service api`, KeepAlive) | [deploy/api.sh](../deploy/api.sh) runs `d2d serve` on `127.0.0.1:8080` (production) / `8081` (staging), serving `/api`, the Copilot and the built web UI; it uses native Ollama at `127.0.0.1:11434` |

## Layout per environment

```text
~/d2d/<env>/                  env = staging | production
├── .env                      POSTGRES_PASSWORD, D2D_SECRET_KEY, D2D_PG_PORT (prod 5432, staging 5433); chmod 600
├── releases/<ts>-<sha>/      git archive of the commit + .venv (last 3 kept)
├── current -> releases/...   active release
├── data/                     pipeline output (simulate + run, labels/)
├── models/                   classifier.joblib + classifier.json (retrained when labels change)
└── logs/                     pipeline.log (scheduled runs), api.log (API server)
```

## Deploy steps and rollback

1. `git archive` the commit into a new release, `uv sync --locked --no-dev`, and build the web UI
   (`npm ci && npm run build` in `web/`, Node from `~/.local/bin`).
2. Smoke test: `d2d simulate` → `d2d run` → changelog and linking gates → train on ground truth →
   classifier gate, all on a temp dir. A failure stops here; the running release is untouched.
3. Point `current` at the new release, `docker compose up -d --wait`, `alembic upgrade head` (URL from
   the environment `.env`: `POSTGRES_PASSWORD`, `D2D_PG_PORT`), one pipeline run against
   `~/d2d/<env>/data` that also loads Postgres and labels stories, reinstall both launchd agents, and
   wait until `/api/health` answers with this release's revision.
4. If step 3 fails, `current` is pointed back at the previous release and step 3 is repeated for it;
   the job still fails so the run is red in GitHub. Migrations are not rolled back: every migration
   must keep the previous release working (add columns/tables, do not drop or rename in one step).

## One-time setup (done on this Mac mini)

```bash
# per-environment secrets
for e in staging production; do
  mkdir -p ~/d2d/$e && umask 077
  port=$([ $e = production ] && echo 5432 || echo 5433)
  printf 'POSTGRES_PASSWORD=%s\nD2D_SECRET_KEY=%s\nD2D_PG_PORT=%s\n' \
    "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" $port > ~/d2d/$e/.env
done
# runner (token needs admin on the repo)
set -a; source ~/Documents/project/.env; set +a
deploy/runner/install.sh
```

GitHub environments `staging` and `production` exist with a deployment branch policy of `main`.

## Operations

```bash
deploy/deploy.sh staging                                  # manual deploy of the checked-out commit
tail -f ~/d2d/production/logs/pipeline.log                # scheduled runs
launchctl print gui/$(id -u)/com.d2d.pipeline.production  # agent state, last exit code
docker compose -p d2d-production ps                       # Postgres
docker compose -p d2d-production exec postgres psql -U d2d -d d2d \
  -c "select status, count(*) from ontology.change_story group by 1"   # loaded stories
(cd ~/d2d/production/current && set -a && . ~/d2d/production/.env && .venv/bin/alembic current)  # schema version
~/actions-runner/svc.sh status                            # runner
open http://127.0.0.1:8080                                # production web UI (staging: 8081)
tail -f ~/d2d/production/logs/api.log                     # API / agent log
launchctl kickstart -k gui/$(id -u)/com.d2d.api.production   # restart the API
```

## Accounts and login

The web app requires a login. Accounts are per environment and live in `~/d2d/<env>/users.toml`
(mode 600, outside the repo, ignored by git). That file holds only salted **scrypt hashes**, never
the passwords; sessions are signed with `D2D_SECRET_KEY` from the environment's `.env`.

```bash
cd ~/d2d/production/current
.venv/bin/d2d user add <name> --role owner --env production    # prompts twice, input hidden
.venv/bin/d2d user add <friend> --role guest --env production  # invite others with a lower role
.venv/bin/d2d user list --env production
.venv/bin/d2d user passwd <name> --env production              # also signs that user out
.venv/bin/d2d user remove <friend> --env production
```

Changes apply immediately (no restart). Passwords need 12+ characters; 5 failed logins per user or
client lock that key for 15 minutes. Roles: `owner` (everything, can preview other roles), `family`,
`energy_advisor`, `guest` (see `config/roles.toml`). `/api/health` and the static UI are public;
every other `/api` route needs a session. Interactive API docs are off unless `D2D_API_DOCS=on`
(then at `/api/docs`, still behind nothing but your own machine). Behind a tunnel all clients reach
the API from one address, so a burst of failed logins can briefly lock the login for everyone; that
is the trade-off for a simple beta login.

Optional `.env` keys: `D2D_API_PORT` (default 8080 production, 8081 staging), `D2D_LLM_MODEL`
(default `qwen3:14b` from `config/llm.toml`), `D2D_LLM=off` (grounded fallback answers, no LLM).

The runner, Ollama, the API and the pipeline agents are launchd **user** agents: they start when you log in.
For unattended restarts after a power failure, enable System Settings → Users & Groups →
**Automatically log in as** (requires FileVault off), or log in once after each restart.
