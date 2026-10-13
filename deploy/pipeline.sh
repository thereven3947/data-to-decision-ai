#!/usr/bin/env bash
# Scheduled pipeline run, started by the launchd agent com.d2d.pipeline.<env>.
# Usage: deploy/pipeline.sh <env_dir>   (e.g. ~/d2d/production)
set -euo pipefail

ENV_DIR=${1:?usage: pipeline.sh <env_dir>}
cd "$ENV_DIR/current"
echo "== $(date -u +%FT%TZ) pipeline $(basename "$ENV_DIR") $(cat REVISION 2>/dev/null || echo unknown)"
# No live device feed yet: the simulator regenerates the household before each run.
.venv/bin/d2d simulate --data-dir "$ENV_DIR/data"
.venv/bin/d2d run --data-dir "$ENV_DIR/data"
