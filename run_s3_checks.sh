#!/usr/bin/env bash
# run_s3_checks.sh
# Usage: ./run_s3_checks.sh [original|converted|both] [--sample-size N]
#
# Prerequisites:
#   1. cd terraform && terraform apply
#   2. Export credentials:
#        export AWS_ACCESS_KEY_ID=$(terraform output -raw aws_access_key_id)
#        export AWS_SECRET_ACCESS_KEY=$(terraform output -raw aws_secret_access_key)
#        export AWS_DEFAULT_REGION=eu-north-1
#   3. Return here and run this script.

set -euo pipefail

BUCKET="sbx-new-content-integrations-test"
ORIGINAL_PREFIX="deeplink-vod/original/fi/"
CONVERTED_PREFIX="deeplink-vod/converted/fi/"
SAMPLE_SIZE=100
MODE="${1:-both}"

if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "ERROR: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set."
  echo "  Run: cd terraform && export AWS_ACCESS_KEY_ID=\$(terraform output -raw aws_access_key_id)"
  echo "       export AWS_SECRET_ACCESS_KEY=\$(terraform output -raw aws_secret_access_key)"
  exit 1
fi

# Allow --sample-size override: ./run_s3_checks.sh both --sample-size 500
if [[ "${2:-}" == "--sample-size" && -n "${3:-}" ]]; then
  SAMPLE_SIZE="$3"
fi

mkdir -p reports

if [[ "$MODE" == "original" || "$MODE" == "both" ]]; then
  echo "==> Running original checks (sample=$SAMPLE_SIZE)..."
  python -m metadata_checker.cli run-original \
    --mode s3 \
    --bucket "$BUCKET" \
    --prefix "$ORIGINAL_PREFIX" \
    --sample-size "$SAMPLE_SIZE" \
    --output reports/original_report.json
  echo "    Report: reports/original_report.json"
fi

if [[ "$MODE" == "converted" || "$MODE" == "both" ]]; then
  echo "==> Running converted checks (sample=$SAMPLE_SIZE)..."
  python -m metadata_checker.cli run-converted \
    --mode s3 \
    --bucket "$BUCKET" \
    --original-prefix "$ORIGINAL_PREFIX" \
    --converted-prefix "$CONVERTED_PREFIX" \
    --sample-size "$SAMPLE_SIZE" \
    --output reports/converted_report.json
  echo "    Report: reports/converted_report.json"
fi

echo "Done."
