#!/bin/bash
# Prowler scanner entrypoint.
#
# SCAN_MODE=full (default): runs `prowler aws` in OCSF JSON mode, uploads the
# report to the raw-reports S3 bucket under
# `raw-reports/{scan_id}/{account}.ocsf.json`, and exits. The ingest pipeline
# listens to S3:ObjectCreated on that prefix.
#
# SCAN_MODE=event: targeted run started by the event-scan trigger Lambda with
# PROWLER_CHECKS plus either PROWLER_RESOURCE_ARNS or PROWLER_REGION. The
# partial report goes to `event-reports/{EVENT_SCAN_ID}/{account}.ocsf.json`,
# which ingest upserts by finding_uid; it is not listed as a scan in the
# dashboard history.

set -euo pipefail

: "${RAW_REPORTS_BUCKET:?RAW_REPORTS_BUCKET env var is required}"
: "${AWS_REGION:?AWS_REGION env var is required}"
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID env var is required}"

SCAN_MODE="${SCAN_MODE:-full}"

fail() {
    echo "[scanner] ERROR: $*" >&2
    exit 2
}

case "${SCAN_MODE}" in
    full)
        SCAN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
        REPORT_PREFIX="raw-reports"
        ;;
    event)
        : "${EVENT_SCAN_ID:?EVENT_SCAN_ID env var is required in event mode}"
        : "${PROWLER_CHECKS:?PROWLER_CHECKS env var is required in event mode}"
        [[ "${EVENT_SCAN_ID}" =~ ^[0-9]{8}T[0-9]{6}Z-event-[a-z0-9-]{1,48}$ ]] || fail "invalid EVENT_SCAN_ID"
        SCAN_ID="${EVENT_SCAN_ID}"
        REPORT_PREFIX="event-reports"
        read -r -a CHECKS <<< "${PROWLER_CHECKS}"
        [[ ${#CHECKS[@]} -gt 0 ]] || fail "PROWLER_CHECKS is empty"
        for check in "${CHECKS[@]}"; do
            [[ "${check}" =~ ^[a-z0-9_]+$ ]] || fail "invalid check id: ${check}"
        done
        ;;
    *)
        fail "unknown SCAN_MODE: ${SCAN_MODE}"
        ;;
esac

OUT_DIR="${TMPDIR:-/tmp}/prowler-out-${SCAN_ID}"
mkdir -p "${OUT_DIR}"

if [[ "${SCAN_MODE}" == "full" ]]; then
    # --output-formats json-ocsf produces the standardized OCSF JSON the ingest
    # pipeline parses. csv/html are kept as human-readable artifacts.
    # -S pushes ASFF to Security Hub so findings are also visible there.
    PROWLER_ARGS=(
        --output-formats json-ocsf csv html
        --output-directory "${OUT_DIR}"
        --output-filename "${AWS_ACCOUNT_ID}"
        --region "${AWS_REGION}"
        --log-level WARNING
        --no-banner
        -S
    )
else
    PROWLER_ARGS=(
        --output-formats json-ocsf
        --output-directory "${OUT_DIR}"
        --output-filename "${AWS_ACCOUNT_ID}"
        --checks "${CHECKS[@]}"
    )
    if [[ -n "${PROWLER_RESOURCE_ARNS:-}" ]]; then
        read -r -a RESOURCE_ARNS <<< "${PROWLER_RESOURCE_ARNS}"
        for arn in "${RESOURCE_ARNS[@]}"; do
            [[ "${arn}" =~ ^arn:[a-z-]+:[a-z0-9-]+:[a-z0-9-]*:[0-9]*:[A-Za-z0-9_./:*-]+$ ]] || fail "invalid resource ARN: ${arn}"
        done
        PROWLER_ARGS+=(--resource-arn "${RESOURCE_ARNS[@]}")
    elif [[ -n "${PROWLER_REGION:-}" ]]; then
        [[ "${PROWLER_REGION}" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]] || fail "invalid PROWLER_REGION"
        PROWLER_ARGS+=(--region "${PROWLER_REGION}")
    else
        fail "event mode needs PROWLER_RESOURCE_ARNS or PROWLER_REGION"
    fi
    # Without --skip-sh-update, Prowler archives every Security Hub finding this partial run did not report.
    PROWLER_ARGS+=(--log-level WARNING --no-banner -S --skip-sh-update)
fi

echo "[scanner] scan_id=${SCAN_ID} mode=${SCAN_MODE} account=${AWS_ACCOUNT_ID} region=${AWS_REGION}"
echo "[scanner] starting Prowler..."

set +e
prowler aws "${PROWLER_ARGS[@]}"
PROWLER_EXIT=$?
set -e

echo "[scanner] prowler exited with ${PROWLER_EXIT}"

OCSF_FILE="$(ls "${OUT_DIR}"/*.ocsf.json 2>/dev/null | head -n1 || true)"
if [[ -z "${OCSF_FILE}" ]]; then
    # Prowler writes no report and exits 0 when the targeted resources produce no finding.
    if [[ "${SCAN_MODE}" == "event" && ${PROWLER_EXIT} -eq 0 ]]; then
        echo "[scanner] no findings for the targeted resources, nothing to upload"
        echo "[scanner] done."
        exit 0
    fi
    echo "[scanner] ERROR: no OCSF JSON produced by Prowler" >&2
    exit 1
fi

S3_KEY="${REPORT_PREFIX}/${SCAN_ID}/${AWS_ACCOUNT_ID}.ocsf.json"
echo "[scanner] uploading ${OCSF_FILE} → s3://${RAW_REPORTS_BUCKET}/${S3_KEY}"
aws s3 cp "${OCSF_FILE}" "s3://${RAW_REPORTS_BUCKET}/${S3_KEY}" --region "${AWS_REGION}"

CSV_FILE="$(ls "${OUT_DIR}"/*.csv 2>/dev/null | head -n1 || true)"
if [[ -n "${CSV_FILE}" ]]; then
    aws s3 cp "${CSV_FILE}" "s3://${RAW_REPORTS_BUCKET}/${REPORT_PREFIX}/${SCAN_ID}/$(basename "${CSV_FILE}")" --region "${AWS_REGION}" || true
fi

HTML_FILE="$(ls "${OUT_DIR}"/*.html 2>/dev/null | head -n1 || true)"
if [[ -n "${HTML_FILE}" ]]; then
    aws s3 cp "${HTML_FILE}" "s3://${RAW_REPORTS_BUCKET}/${REPORT_PREFIX}/${SCAN_ID}/$(basename "${HTML_FILE}")" --region "${AWS_REGION}" || true
fi

echo "[scanner] done."
