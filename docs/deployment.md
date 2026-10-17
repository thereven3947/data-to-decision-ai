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
| Schedule | launchd agent `com.d2d.pipeline.<env>` from `d2d launchd-plist` | Runs [deploy/pipeline.sh](../deploy/pipeline.sh) (`simulate` → `run` → `load` → `review export` → `train` (only if labels changed) → `classify` → `feedback route` → `notify`) every `cadence_minutes` (`config/pipeline.toml`, 15 min) |
| Backups | launchd agent `com.d2d.backup.<env>` | [deploy/backup.sh](../deploy/backup.sh) daily at 03:30 local, 14 kept |
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

The web app requires a login; the beta allows a single owner account per environment, created on
the Mac mini with `d2d user` (see `d2d user --help`). Passwords are never stored. The operational
runbook (commands, policy, lockout recovery, key rotation) is kept locally in
`docs/local/accounts-runbook.md`, which is git-ignored.

## Notifications, feedback, calibration and backups

- **Every pipeline run** (15 min) also routes new feedback (`d2d feedback route`) and creates one
  notification per new published story (`d2d notify`, severities in `config/notifications.toml`).
  Pushes go out only if the environment's `.env` sets `D2D_NTFY_URL` and `D2D_NTFY_TOPIC` (optional
  `D2D_NTFY_TOKEN`); otherwise they are recorded and visible on the story and in **Insights**. Use a
  long random or token-protected ntfy topic: public ntfy topics are readable by anyone who knows the
  name. The first run backfills existing stories without pushing.
- **Nightly calibration** (`.github/workflows/calibrate.yml`, 02:00 Pacific on the runner) runs
  `d2d calibrate` against production, pushes a `calibration/<date>` branch with proposed config and
  exported eval items, and opens an issue with the report and a "create pull request" link. Nothing
  changes until you merge.
- **Nightly backups**: launchd agent `com.d2d.backup.<env>` runs [deploy/backup.sh](../deploy/backup.sh)
  at 03:30 local and keeps 14 `pg_dump` files in `~/d2d/<env>/backups/` (mode 600). Restore with
  `docker compose -p d2d-<env> exec -T postgres pg_restore -U d2d -d d2d --clean < FILE`.

## Public demo (GitHub Pages)

`.github/workflows/pages.yml` publishes a read-only static build of the web app at
`https://<owner>.github.io/data-to-decision-ai/` on every push to `main`. It runs only on
GitHub-hosted runners: it rebuilds the toy household in a throwaway Postgres, exports one JSON
snapshot per role (`scripts/export_demo.py`) and replays the Copilot answers recorded in
`web/demo/copilot.json` (re-record with `scripts/export_demo.py --record-copilot` against local
Ollama). The demo has no server and never contacts the Mac mini.

## Remote access to the live beta (M4-04, optional)

Do not point a public website at the Mac mini's API: that would need the API open to the whole
internet, cross-site cookies or tokens in the browser, and CORS, which weakens every protection
above. To use the live app from your phone or laptop, publish the **same-origin** app through a
Cloudflare Tunnel with Cloudflare Access in front:

1. Create a Cloudflare account, add a domain you own, and in Zero Trust create an **Access
   application** for `beta.<your-domain>` with a policy that allows only your email (one-time PIN or
   GitHub/Google login). Free for personal use.
2. On the Mac mini: `brew install cloudflared`, `cloudflared tunnel login`,
   `cloudflared tunnel create d2d`, `cloudflared tunnel route dns d2d beta.<your-domain>`, then a
   `~/.cloudflared/config.yml` with an ingress rule `hostname: beta.<your-domain>` ->
   `service: http://127.0.0.1:8080`, and `sudo cloudflared service install`.
3. Add `D2D_ALLOWED_HOSTS=127.0.0.1,localhost,beta.<your-domain>` to `~/d2d/production/.env` and
   restart the API. Cookies switch to `Secure` automatically over HTTPS, and sign-in lockouts use the
   visitor address Cloudflare reports.

The Mac mini opens no inbound ports. Traffic must pass Cloudflare Access first and then the app's own
login; Access also absorbs scanning and password guessing before it reaches the Mac.

Optional `.env` keys: `D2D_API_PORT` (default 8080 production, 8081 staging), `D2D_LLM_MODEL`
(default `qwen3:14b` from `config/llm.toml`), `D2D_LLM=off` (grounded fallback answers, no LLM).

The runner, Ollama, the API and the pipeline agents are launchd **user** agents: they start when you log in.
For unattended restarts after a power failure, enable System Settings → Users & Groups →
**Automatically log in as** (requires FileVault off), or log in once after each restart.
