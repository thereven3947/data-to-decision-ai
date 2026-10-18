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

The public demo on GitHub Pages has a **Sign in to the live beta** button. It is a plain link to the
live app served by the Mac mini (same origin, so the strict cookie and CORS rules stay on); the
GitHub page itself never talks to the Mac. The link appears once the repository variable
`D2D_LIVE_URL` is set (Settings → Secrets and variables → Actions → **Variables**) and the `pages`
workflow runs again.

| Option | Cost | Stable address | Notes |
|--------|------|----------------|-------|
| **Tailscale Funnel** (recommended) | $0 (Personal plan) | `https://<mac>.<tailnet>.ts.net` | No domain needed; HTTPS certificate included; ports 443/8443/10000 only |
| Cloudflare Tunnel + Access | $0 for the tunnel, but needs a domain (~$10/year) | `https://beta.<your-domain>` | Adds an email login in front of the app |
| Cloudflare quick tunnel | $0 | No: random URL, changes on every restart | Testing only |
| ngrok free | $0 | One static domain | Shows an ngrok warning page to visitors; monthly bandwidth cap |

### Tailscale Funnel ($0)

1. Install Tailscale on the Mac mini (https://tailscale.com/download/mac) and sign in. In the admin
   console enable **MagicDNS** and **HTTPS certificates** (DNS page); `tailscale funnel` asks to add
   the Funnel permission to your policy the first time.
2. Publish the production app (only this port, nothing else on the Mac):
   ```bash
   tailscale funnel --bg 8080        # prints https://<mac>.<tailnet>.ts.net
   tailscale funnel status           # check; `tailscale funnel --https=443 off` to stop
   ```
3. Tell the app its public name, then restart it:
   ```bash
   cat >> ~/d2d/production/.env <<'ENV'
   D2D_ALLOWED_HOSTS=127.0.0.1,localhost,<mac>.<tailnet>.ts.net
   D2D_SECURE_COOKIES=on
   ENV
   launchctl kickstart -k gui/$(id -u)/com.d2d.api.production
   ```
4. Set the repository variable `D2D_LIVE_URL=https://<mac>.<tailnet>.ts.net` and re-run the
   `pages` workflow (Actions → pages → Run workflow). The button appears on the public demo.
   (This repository also has its owner's address as the fallback in `.github/workflows/pages.yml`,
   guarded by the repository name so forks never link to it.)
5. Invite testers: `d2d user add <name> --role guest --env production` (see the local runbook).
   Each guest may ask the Copilot 3 questions per rolling hour (`config/llm.toml` `[limits]`); the
   app shows how long to wait when the limit is reached.

### What is exposed and how it is protected

With Funnel on, the sign-in page of the live app is on the public internet. Protections:
invite-only accounts (no sign-up, `config/roles.toml` `[accounts]`), scrypt-hashed passwords,
5 failed logins per account or visitor address lock sign-in for 15 minutes, every `/api` route
except health and sign-in needs a session, HTTPS with `Secure` cookies and HSTS, strict security
headers, and the **Copilot guard** in `config/llm.toml` (`[limits]`: 3 questions per account per
hour, one answer at a time, at most two waiting; the owner is exempt from the quota). Guests only
see published stories for shared rooms and cannot label, open cases or see Insights.

Turn it off at any time with `tailscale funnel --https=443 off` (and clear `D2D_LIVE_URL` to hide
the button).

### Cloudflare Tunnel + Access (if you own a domain)

1. In Cloudflare Zero Trust create an **Access application** for `beta.<your-domain>` allowing
   only the emails you invite (one-time PIN or GitHub/Google login).
2. `brew install cloudflared`, `cloudflared tunnel login`, `cloudflared tunnel create d2d`,
   `cloudflared tunnel route dns d2d beta.<your-domain>`, a `~/.cloudflared/config.yml` ingress rule
   `hostname: beta.<your-domain>` -> `service: http://127.0.0.1:8080`, then
   `sudo cloudflared service install`.
3. Same `.env` lines as above with `beta.<your-domain>`, and `D2D_LIVE_URL=https://beta.<your-domain>`.

The Mac mini opens no inbound ports in either setup; never use router port forwarding.

Optional `.env` keys: `D2D_API_PORT` (default 8080 production, 8081 staging), `D2D_LLM_MODEL`
(default `qwen3:14b` from `config/llm.toml`), `D2D_LLM=off` (grounded fallback answers, no LLM).

The runner, Ollama, the API and the pipeline agents are launchd **user** agents: they start when you log in.
For unattended restarts after a power failure, enable System Settings → Users & Groups →
**Automatically log in as** (requires FileVault off), or log in once after each restart.
