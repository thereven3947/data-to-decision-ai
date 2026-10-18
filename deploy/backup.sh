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

# Optional off-site copy (infra/terraform/aws). Off unless D2D_BACKUP_S3_URI is set here or in the
# environment's .env; the uploader may only put objects, so it cannot delete off-site history.
s3_uri=${D2D_BACKUP_S3_URI:-}
if [[ -z "$s3_uri" && -f "$ENV_DIR/.env" ]]; then
  s3_uri=$(sed -n 's/^D2D_BACKUP_S3_URI=//p' "$ENV_DIR/.env" | tail -n 1)
fi
if [[ -n "$s3_uri" ]]; then
  if command -v aws >/dev/null 2>&1; then
    if AWS_PROFILE=${D2D_BACKUP_AWS_PROFILE:-d2d-backup} aws s3 cp --only-show-errors \
      "$out" "${s3_uri%/}/$ENV_NAME/$(basename "$out")"; then
      echo "   off-site copy -> ${s3_uri%/}/$ENV_NAME/"
    else
      echo "!! off-site copy failed; local backup kept" >&2
    fi
  else
    echo "!! D2D_BACKUP_S3_URI is set but the aws CLI is not installed; local backup kept" >&2
  fi
fi

ls -1t "$ENV_DIR"/backups/d2d-*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -f
