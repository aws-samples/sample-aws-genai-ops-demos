#!/bin/bash
# Prowler scanner entrypoint.
#
# Runs `prowler aws` in OCSF JSON mode, uploads the report to the raw-reports
# S3 bucket under `raw-reports/{scan_id}/{account}.ocsf.json`, and exits.
# The ingest pipeline listens to S3:ObjectCreated on that prefix.

set -euo pipefail

: "${RAW_REPORTS_BUCKET:?RAW_REPORTS_BUCKET env var is required}"
: "${AWS_REGION:?AWS_REGION env var is required}"
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID env var is required}"

SCAN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="/tmp/prowler-out-${SCAN_ID}"
mkdir -p "${OUT_DIR}"

echo "[scanner] scan_id=${SCAN_ID} account=${AWS_ACCOUNT_ID} region=${AWS_REGION}"
echo "[scanner] starting Prowler..."

# --output-formats json-ocsf produces the standardized OCSF JSON the ingest
# pipeline parses. csv/html are kept as human-readable artifacts.
# -S pushes ASFF to Security Hub so findings are also visible there.
set +e
prowler aws \
    --output-formats json-ocsf csv html \
    --output-directory "${OUT_DIR}" \
    --output-filename "${AWS_ACCOUNT_ID}" \
    --region "${AWS_REGION}" \
    --log-level WARNING \
    --no-banner \
    -S
PROWLER_EXIT=$?
set -e

echo "[scanner] prowler exited with ${PROWLER_EXIT}"

OCSF_FILE="$(ls "${OUT_DIR}"/*.ocsf.json 2>/dev/null | head -n1 || true)"
if [[ -z "${OCSF_FILE}" ]]; then
    echo "[scanner] ERROR: no OCSF JSON produced by Prowler" >&2
    exit 1
fi

S3_KEY="raw-reports/${SCAN_ID}/${AWS_ACCOUNT_ID}.ocsf.json"
echo "[scanner] uploading ${OCSF_FILE} → s3://${RAW_REPORTS_BUCKET}/${S3_KEY}"
aws s3 cp "${OCSF_FILE}" "s3://${RAW_REPORTS_BUCKET}/${S3_KEY}" --region "${AWS_REGION}"

CSV_FILE="$(ls "${OUT_DIR}"/*.csv 2>/dev/null | head -n1 || true)"
if [[ -n "${CSV_FILE}" ]]; then
    aws s3 cp "${CSV_FILE}" "s3://${RAW_REPORTS_BUCKET}/raw-reports/${SCAN_ID}/$(basename "${CSV_FILE}")" --region "${AWS_REGION}" || true
fi

HTML_FILE="$(ls "${OUT_DIR}"/*.html 2>/dev/null | head -n1 || true)"
if [[ -n "${HTML_FILE}" ]]; then
    aws s3 cp "${HTML_FILE}" "s3://${RAW_REPORTS_BUCKET}/raw-reports/${SCAN_ID}/$(basename "${HTML_FILE}")" --region "${AWS_REGION}" || true
fi

echo "[scanner] done."
