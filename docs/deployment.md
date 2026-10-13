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
| Schedule | launchd agent `com.d2d.pipeline.<env>` from `d2d launchd-plist` | Runs [deploy/pipeline.sh](../deploy/pipeline.sh) every `cadence_minutes` (`config/pipeline.toml`, 15 min) |

## Layout per environment

```text
~/d2d/<env>/                  env = staging | production
├── .env                      POSTGRES_PASSWORD, D2D_SECRET_KEY, D2D_PG_PORT (prod 5432, staging 5433); chmod 600
├── releases/<ts>-<sha>/      git archive of the commit + .venv (last 3 kept)
├── current -> releases/...   active release
├── data/                     pipeline output (simulate + run)
└── logs/pipeline.log         scheduled runs
```

## Deploy steps and rollback

1. `git archive` the commit into a new release, `uv sync --locked --no-dev`.
2. Smoke test: `d2d simulate` → `d2d run` → both eval gates on a temp dir. A failure stops here; the
   running release is untouched.
3. Point `current` at the new release, `docker compose up -d --wait`, `alembic upgrade head` (once
   M4-02 adds `alembic.ini`), one pipeline run against `~/d2d/<env>/data`, reinstall the launchd agent.
4. If step 3 fails, `current` is pointed back at the previous release and step 3 is repeated for it;
   the job still fails so the run is red in GitHub.

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
~/actions-runner/svc.sh status                            # runner
```

The runner, Ollama and the pipeline agents are launchd **user** agents: they start when you log in.
For unattended restarts after a power failure, enable System Settings → Users & Groups →
**Automatically log in as** (requires FileVault off), or log in once after each restart.
