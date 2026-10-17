#!/usr/bin/env bash
# Scheduled pipeline run, started by the launchd agent com.d2d.pipeline.<env>.
# Usage: deploy/pipeline.sh <env_dir>   (e.g. ~/d2d/production)
set -euo pipefail

ENV_DIR=${1:?usage: pipeline.sh <env_dir>}
cd "$ENV_DIR/current"
echo "== $(date -u +%FT%TZ) pipeline $(basename "$ENV_DIR") $(cat REVISION 2>/dev/null || echo unknown)"
# Database credentials (POSTGRES_PASSWORD, D2D_PG_PORT) for the Postgres steps.
set -a
. "$ENV_DIR/.env"
set +a
D2D=.venv/bin/d2d
DATA="$ENV_DIR/data"
# No live device feed yet: the simulator regenerates the household before each run.
$D2D simulate --data-dir "$DATA"
$D2D run --data-dir "$DATA"
$D2D load --data-dir "$DATA"
# Stages 5-7: reviewed labels (+ ground truth for unreviewed stories) -> retrain only when the
# training set changed -> label new stories; abstained / audit-sampled stories go to review.
$D2D review export --data-dir "$DATA"
$D2D train --training-set "$DATA/labels/training_set.jsonl" --models-dir "$ENV_DIR/models"
$D2D classify --models-dir "$ENV_DIR/models"
