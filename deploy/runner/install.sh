#!/usr/bin/env bash
# One-time: register this Mac mini as the repo's self-hosted runner (label mac-mini) and run it as
# a launchd user agent. Needs a GITHUB_TOKEN with admin rights on the repo in the environment:
#   set -a; source ~/Documents/project/.env; set +a; deploy/runner/install.sh
set -euo pipefail

: "${GITHUB_TOKEN:?export GITHUB_TOKEN first}"
REPO=${D2D_REPO:-thereven3947/data-to-decision-ai}
DIR=${RUNNER_DIR:-$HOME/actions-runner}
NAME=${RUNNER_NAME:-mac-mini}
API="https://api.github.com/repos"

mkdir -p "$DIR"
cd "$DIR"
if [ ! -x ./config.sh ]; then
  VERSION=$(curl -fsSL "$API/actions/runner/releases/latest" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')
  curl -fsSL "https://github.com/actions/runner/releases/download/v$VERSION/actions-runner-osx-arm64-$VERSION.tar.gz" |
    tar -xz
fi

if [ ! -f .runner ]; then
  TOKEN=$(curl -fsSL -X POST -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github+json" "$API/$REPO/actions/runners/registration-token" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
  ./config.sh --unattended --replace --url "https://github.com/$REPO" --token "$TOKEN" \
    --name "$NAME" --labels mac-mini --work _work
fi

# Jobs use this PATH: per-user tools (uv, gh, ollama) and OrbStack's docker are not on launchd's.
echo "$HOME/.local/bin:$HOME/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" >.path

if ! ls "$HOME"/Library/LaunchAgents/actions.runner.*."$NAME".plist >/dev/null 2>&1; then
  ./svc.sh install
fi
./svc.sh start || true
./svc.sh status
