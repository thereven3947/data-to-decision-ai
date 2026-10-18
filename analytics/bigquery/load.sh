#!/usr/bin/env bash
# Load the D2D warehouse export into BigQuery and (re)create the KPI views.
# Usage: analytics/bigquery/load.sh PROJECT_ID [DATASET] [EXPORT_DIR]
# Needs the Google Cloud SDK (`bq`) and credentials for a principal with BigQuery Data Editor on the
# dataset and Job User on the project (infra/terraform/gcp creates both).
set -euo pipefail

PROJECT="${1:?usage: load.sh PROJECT_ID [DATASET] [EXPORT_DIR]}"
DATASET="${2:-d2d_analytics}"
EXPORT_DIR="${3:-data/warehouse}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ -f "$EXPORT_DIR/manifest.json" ]] || {
  echo "no export in $EXPORT_DIR; run: uv run d2d export warehouse --out $EXPORT_DIR" >&2
  exit 1
}

for file in "$EXPORT_DIR"/*.parquet; do
  table="$(basename "$file" .parquet)"
  echo "loading $DATASET.$table"
  bq --project_id="$PROJECT" load --replace --source_format=PARQUET "$DATASET.$table" "$file"
done

sed "s/{dataset}/$DATASET/g" "$HERE/views.sql" |
  bq --project_id="$PROJECT" query --use_legacy_sql=false --quiet
echo "views ready in $PROJECT:$DATASET (connect Looker Studio to the v_* views)"
