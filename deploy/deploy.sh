#!/usr/bin/env bash
# Deploy the checked-out commit to an environment on the Mac mini (see docs/deployment.md).
# Usage: deploy/deploy.sh staging|production   (from the repo root; used by .github/workflows/deploy.yml)
#
# Layout: ~/d2d/<env>/{.env, releases/<sha>-<ts>/, current -> releases/..., data/, logs/}
# Steps: build release -> smoke test -> switch current -> compose up -> migrate -> pipeline run ->
# launchd agent. If anything after the switch fails, the previous release is re-activated.
set -euo pipefail

ENV_NAME=${1:?usage: deploy.sh staging|production}
case "$ENV_NAME" in
  staging | production) ;;
  *) echo "unknown environment: $ENV_NAME" >&2; exit 2 ;;
esac
export PATH="$HOME/.local/bin:$HOME/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

ENV_DIR="${D2D_HOME:-$HOME/d2d}/$ENV_NAME"
KEEP=${D2D_KEEP_RELEASES:-3}
SHA=$(git rev-parse HEAD)
REL="$ENV_DIR/releases/$(date -u +%Y%m%d%H%M%S)-$SHA"
LABEL="com.d2d.pipeline.$ENV_NAME"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

log() { echo "== $(date -u +%FT%TZ) [$ENV_NAME] $*"; }

if [ ! -f "$ENV_DIR/.env" ]; then
  echo "missing $ENV_DIR/.env (see docs/deployment.md)" >&2
  exit 1
fi
mkdir -p "$ENV_DIR/releases" "$ENV_DIR/data" "$ENV_DIR/logs" "$HOME/Library/LaunchAgents"

log "build $SHA -> $REL"
mkdir -p "$REL"
git archive HEAD | tar -x -C "$REL"
echo "$SHA" >"$REL/REVISION"
(cd "$REL" && uv sync --locked --no-dev --quiet)

log "smoke test"
SMOKE=$(mktemp -d)
trap 'rm -rf "$SMOKE"' EXIT
(
  cd "$REL"
  .venv/bin/d2d simulate --data-dir "$SMOKE" >/dev/null
  .venv/bin/d2d run --data-dir "$SMOKE" >/dev/null
  .venv/bin/d2d eval changelog --data-dir "$SMOKE"
  .venv/bin/d2d eval linking --data-dir "$SMOKE"
) || {
  log "smoke test failed; current release unchanged"
  rm -rf "$REL"
  exit 1
}

compose() {
  docker compose --project-name "d2d-$ENV_NAME" --env-file "$ENV_DIR/.env" \
    -f "$ENV_DIR/current/deploy/docker-compose.yml" "$@"
}

# errexit is ignored inside functions called from `if`, so every step checks explicitly.
activate() {
  local rel=$1 i
  ln -sfn "$rel" "$ENV_DIR/current" || return 1
  log "compose up ($(basename "$rel"))"
  compose up -d --wait --remove-orphans || return 1
  if [ -f "$rel/alembic.ini" ]; then
    log "migrate"
    (cd "$rel" && set -a && . "$ENV_DIR/.env" && set +a && .venv/bin/alembic upgrade head) || return 1
  fi
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  log "pipeline run"
  bash "$rel/deploy/pipeline.sh" "$ENV_DIR" || return 1
  log "launchd agent $LABEL"
  (cd "$rel" && .venv/bin/d2d launchd-plist --env "$ENV_NAME" --env-dir "$ENV_DIR") >"$AGENT.tmp" || return 1
  mv "$AGENT.tmp" "$AGENT" || return 1
  # bootstrap right after bootout can race; retry briefly.
  for i in 1 2 3 4 5; do
    launchctl bootstrap "$DOMAIN" "$AGENT" 2>/dev/null && return 0
    sleep 1
  done
  launchctl bootstrap "$DOMAIN" "$AGENT"
}

PREV=$(readlink "$ENV_DIR/current" 2>/dev/null || true)
if activate "$REL"; then
  log "deployed $SHA"
else
  log "deploy failed"
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    log "rollback to $(basename "$PREV")"
    activate "$PREV" || log "rollback failed"
    rm -rf "$REL"
  fi
  exit 1
fi

CURRENT=$(readlink "$ENV_DIR/current")
find "$ENV_DIR/releases" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n +$((KEEP + 1)) |
  while read -r old; do
    [ "$old" = "$CURRENT" ] || rm -rf "$old"
  done
