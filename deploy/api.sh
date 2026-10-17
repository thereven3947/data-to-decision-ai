#!/usr/bin/env bash
# API + Copilot agent + web UI for one environment, kept running by the launchd agent
# com.d2d.api.<env> (see docs/deployment.md). Usage: deploy/api.sh <env_dir>
set -euo pipefail

ENV_DIR=${1:?usage: api.sh <env_dir>}
ENV_NAME=$(basename "$ENV_DIR")
cd "$ENV_DIR/current"
set -a
. "$ENV_DIR/.env"
set +a
case "$ENV_NAME" in
  production) DEFAULT_PORT=8080 ;;
  *) DEFAULT_PORT=8081 ;;
esac
export D2D_DATA_DIR="$ENV_DIR/data"
export D2D_WEB_DIST="$ENV_DIR/current/web/dist"
echo "== $(date -u +%FT%TZ) api $ENV_NAME $(cat REVISION 2>/dev/null || echo unknown) port ${D2D_API_PORT:-$DEFAULT_PORT}"
exec .venv/bin/d2d serve --host 127.0.0.1 --port "${D2D_API_PORT:-$DEFAULT_PORT}" --data-dir "$ENV_DIR/data"
