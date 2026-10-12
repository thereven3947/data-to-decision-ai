# Mac mini setup runbook (M4-01, manual, no AI credit)

Follow top to bottom on the Mac mini (macOS, Apple silicon, 32 GB). Each step ends with a check.
After this, start an AI session with *"Implement issue M4-03 per docs"* (runner, deploy, staging).

## 1. macOS basics

- System Settings → General → Software Update: install updates.
- System Settings → Energy: **Prevent automatic sleeping**, **Start up automatically after a power failure**.
- System Settings → General → Sharing: enable **Remote Login** (SSH) for your user.
- Check: `ssh <user>@<mac-mini>.local` works from the Windows laptop.

## 2. Developer tools

```bash
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install git uv gh make
brew install --cask orbstack tailscale
```

- Open OrbStack once (it provides `docker` and `docker compose`).
- Check: `uv --version`, `docker run --rm hello-world`, `gh --version`.

## 3. Ollama (native, uses the Apple GPU)

```bash
brew install ollama
brew services start ollama
ollama pull qwen3:14b
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

- Check: `ollama run qwen3:8b "say hi"` answers in a few seconds; `ollama ps` shows GPU (Metal).

## 4. Tailscale (private access from the laptop)

- Open Tailscale, sign in with the same account as the laptop.
- Check: from the laptop, `ping <mac-mini-tailscale-name>` and `curl http://<mac-mini>:11434/api/tags`
  (Ollama only listens on localhost by default; set `OLLAMA_HOST=0.0.0.0` via
  `launchctl setenv OLLAMA_HOST 0.0.0.0` and restart the service if you want laptop access).

## 5. Code and first run

```bash
mkdir -p ~/apps && cd ~/apps
gh auth login                     # GitHub account that owns the repo
gh repo clone <owner>/data-to-decision-ai
cd data-to-decision-ai
uv sync
make check                        # lint, types, tests, simulate -> run -> eval gates
```

- Check: `make check` ends with `gate: PASS` twice (changelog + linking).

## 6. Secrets

```bash
cp .env.example .env              # fill POSTGRES_PASSWORD and D2D_SECRET_KEY
chmod 600 .env
```

Generate values with `openssl rand -hex 32`. Never commit `.env`.

## Done when

- [ ] SSH and Tailscale reach the Mac mini from the laptop
- [ ] `docker`, `uv`, `gh`, `ollama` work
- [ ] `qwen3:14b` runs on the GPU
- [ ] `make check` passes on the Mac mini
- [ ] `.env` exists with real secrets

Next: M4-03 (self-hosted runner, deploy workflow, staging + production compose projects),
then M4-02 (Postgres + Alembic), then M4-04 (Cloudflare Tunnel + Access).
