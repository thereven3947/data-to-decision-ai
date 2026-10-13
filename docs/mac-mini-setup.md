# Mac mini setup runbook (M4-01, manual, no AI credit)

The Mac mini (macOS, Apple silicon, 32 GB) is now the primary machine for both development and
serving; the original Windows laptop is no longer part of the setup. Follow top to bottom. Each
step ends with a check. After this, start an AI session with *"Implement issue M4-03 per docs"*
(runner, deploy, staging).

## Layout on the Mac mini

```text
~/Documents/project/
├── .env                      # GitHub credentials: GITHUB_TOKEN, GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL
├── .gitignore                # ignores .env
└── data-to-decision-ai/      # this repo
    └── .env                  # app secrets (copied from .env.example), ignored by the repo .gitignore
```

Both `.env` files are `chmod 600` and never committed. There is no other credentials file.

## 1. macOS basics

- System Settings → General → Software Update: install updates.
- System Settings → Energy: **Prevent automatic sleeping**, **Start up automatically after a power failure**.
- Optional, for remote access: System Settings → General → Sharing → enable **Remote Login** (SSH).
- Check: `pmset -g | grep -E ' sleep|autorestart'` shows `sleep 0` and `autorestart 1`.

## 2. Developer tools

Command Line Tools provide `git` and `make`:

```bash
xcode-select --install
```

`uv`, `gh` and `ollama` are installed per user under `~/.local` (no admin password needed);
`~/.local/bin` is put on `PATH` by the uv installer (`~/.zshrc` sources `~/.local/bin/env`).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
mkdir -p ~/.local/opt ~/.local/bin && cd "$(mktemp -d)"
GH=2.102.0   # latest from https://github.com/cli/cli/releases
curl -fsSLO "https://github.com/cli/cli/releases/download/v$GH/gh_${GH}_macOS_arm64.zip"
unzip -q "gh_${GH}_macOS_arm64.zip" && mv "gh_${GH}_macOS_arm64" ~/.local/opt/gh
ln -sf ~/.local/opt/gh/bin/gh ~/.local/bin/gh
```

Alternative with an admin account: install [Homebrew](https://brew.sh), then
`brew install uv gh ollama` and `brew services start ollama` (skip the manual steps here and in 3).

Docker and Tailscale are GUI apps that need an admin password on first launch:

- Install [OrbStack](https://orbstack.dev/download): drag it to `/Applications`, eject the installer, and
  open it from `/Applications` (it provides `docker` and `docker compose` in `~/.orbstack/bin`).
- Start it at login so containers come back after a reboot: `orb config set app.start_at_login true`.
- Check: `uv --version`, `gh --version`, `docker run --rm hello-world`.

## 3. Ollama (native, uses the Apple GPU)

```bash
mkdir -p ~/.local/opt/ollama && cd "$(mktemp -d)"
curl -fsSLO https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz
tar -xzf ollama-darwin.tgz -C ~/.local/opt/ollama
ln -sf ~/.local/opt/ollama/ollama ~/.local/bin/ollama
```

Run it as a per-user LaunchAgent (starts at login, restarts if it exits) by creating
`~/Library/LaunchAgents/com.ollama.serve.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ollama.serve</string>
  <key>ProgramArguments</key>
  <array><string>/Users/YOUR_USER/.local/opt/ollama/ollama</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/YOUR_USER/Library/Logs/ollama.log</string>
  <key>StandardErrorPath</key><string>/Users/YOUR_USER/Library/Logs/ollama.log</string>
</dict>
</plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ollama.serve.plist
ollama pull qwen3:14b
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

- Check: `curl http://127.0.0.1:11434/api/version`; `ollama run qwen3:8b "say hi"` answers in a few
  seconds; `ollama ps` shows `100% GPU`.
- Restart after changes: `launchctl kickstart -k gui/$(id -u)/com.ollama.serve`.

## 4. Tailscale (optional, private remote access)

- Install [Tailscale](https://tailscale.com/download/mac), open it and sign in.
- Check from another device on the tailnet: `ping <mac-mini-tailscale-name>`.
- Ollama listens on localhost only. For remote access add
  `<key>EnvironmentVariables</key><dict><key>OLLAMA_HOST</key><string>0.0.0.0</string></dict>`
  to the plist above and restart it, then `curl http://<mac-mini>:11434/api/tags`.

## 5. Code and first run

Put the GitHub credentials in `~/Documents/project/.env` (`GITHUB_TOKEN=` a token with read access
to the repo), then clone without an interactive login. The token is passed for this command only
and is not stored in the repo's git config:

```bash
mkdir -p ~/Documents/project && cd ~/Documents/project
printf '.env\n' > .gitignore
set -a; source .env; set +a
git -c http.extraHeader="Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64)" \
  clone https://github.com/thereven3947/data-to-decision-ai.git
cd data-to-decision-ai
uv sync
make check                        # lint, types, tests, simulate -> run -> eval gates
```

- Check: `make check` ends with `gate: PASS` twice (changelog + linking).
- `gh` uses `GITHUB_TOKEN` from the environment, so `source ../.env` before `gh` commands instead of
  `gh auth login`.

## 6. Secrets

```bash
cd ~/Documents/project/data-to-decision-ai
cp .env.example .env              # fill POSTGRES_PASSWORD and D2D_SECRET_KEY
chmod 600 .env
```

Generate values with `openssl rand -hex 32`. Never commit `.env`.

## Done when

- [x] Mac mini does not sleep and restarts after a power failure
- [x] `docker`, `uv`, `gh`, `ollama` work
- [x] `qwen3:14b` runs on the GPU
- [x] `make check` passes on the Mac mini
- [x] `.env` exists with real secrets
- [ ] Optional: SSH / Tailscale reach the Mac mini remotely

Next: M4-03 (self-hosted runner, deploy workflow, staging + production compose projects),
then M4-02 (Postgres + Alembic), then M4-04 (Cloudflare Tunnel + Access).
