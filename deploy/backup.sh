#!/usr/bin/env bash
# Nightly Postgres backup of one environment (labels, feedback, chat answers, notifications).
# Started by the launchd agent com.d2d.backup.<env>. Usage: deploy/backup.sh <env_dir>
# Restore: docker compose -p d2d-<env> exec -T postgres pg_restore -U d2d -d d2d --clean < FILE
set -euo pipefail

ENV_DIR=${1:?usage: backup.sh <env_dir>}
ENV_NAME=$(basename "$ENV_DIR")
KEEP=${D2D_KEEP_BACKUPS:-14}
export PATH="$HOME/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p "$ENV_DIR/backups"
umask 077
out="$ENV_DIR/backups/d2d-$ENV_NAME-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose -p "d2d-$ENV_NAME" exec -T postgres pg_dump -U d2d -d d2d -Fc >"$out.tmp"
mv "$out.tmp" "$out"
echo "== $(date -u +%FT%TZ) backup $ENV_NAME -> $out ($(du -h "$out" | cut -f1))"
ls -1t "$ENV_DIR"/backups/d2d-*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -f
