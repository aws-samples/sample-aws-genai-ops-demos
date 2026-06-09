#!/usr/bin/env bash
#
# G.O.A.T. Network Agent — Traffic_Mirror_Collector S3 uploader.
#
# Watches the splitter's rotation directory and uploads each closed pcap
# file to S3 within 60 seconds of rotation, with a 3× exponential backoff
# retry policy. Behavior matches Task 22 / Reqs 6.3, 6.4:
#
#   * On each ``CLOSE_WRITE`` event under ``${COLLECTOR_OUTPUT_DIR}``,
#     enqueue the file for upload.
#   * Resolve the destination key from the file's parent directory name
#     (which the splitter sets to ``${capture_id}``) and the file's
#     basename, yielding ``s3://${DATA_BUCKET}/raw/${capture_id}/${name}``.
#   * Retry a failed upload with exponential backoff: 1s, 2s, 4s.
#     Retain the file across retries; only after the third failed
#     attempt does the file get logged to journald and discarded.
#   * Successful uploads delete the local file so the splitter's
#     10-file local cap (Req 6.2) does not get tripped.
#
# This script depends on:
#   * ``inotifywait``  (provided by ``inotify-tools`` on AL2023)
#   * ``aws s3 cp``    (AL2023 ships with AWS CLI v2)
#   * The collector instance's IAM role grants ``s3:PutObject`` on
#     ``${DATA_BUCKET}/raw/*``.
#
# Configuration (all required, sourced from systemd unit environment):
#   COLLECTOR_OUTPUT_DIR  — root directory the splitter writes to
#   DATA_BUCKET           — Network_Data_Bucket name
#   AWS_REGION            — region for S3 calls; matches stack region
#
# The script logs to STDERR so journald captures every line under the
# ``goat-collector-uploader.service`` unit.

set -euo pipefail

# Use ``${X:?msg}`` so the unit fails immediately at start if any
# required variable is missing — better than silently malfunctioning.
: "${COLLECTOR_OUTPUT_DIR:?COLLECTOR_OUTPUT_DIR is required}"
: "${DATA_BUCKET:?DATA_BUCKET is required}"
: "${AWS_REGION:?AWS_REGION is required}"

MAX_ATTEMPTS="${UPLOADER_MAX_ATTEMPTS:-3}"
BACKOFF_BASE_SECONDS="${UPLOADER_BACKOFF_BASE_SECONDS:-1}"

# Logging helper that prints ISO-8601 timestamps so journald entries
# are easy to read alongside the splitter's structured log output.
log() {
  local level="$1"
  shift
  printf '%s uploader[%s] %s %s\n' \
    "$(date --utc '+%Y-%m-%dT%H:%M:%SZ')" \
    "$$" \
    "${level}" \
    "$*" >&2
}

# Validate the rotation directory exists; create it on first boot if
# not. The splitter's systemd unit also creates it, so this is just
# defense-in-depth so the uploader does not crash on first boot.
mkdir -p "${COLLECTOR_OUTPUT_DIR}"

log INFO "uploader starting; watching ${COLLECTOR_OUTPUT_DIR} for closed pcap files"

# ``upload_one`` performs the per-file upload with exponential backoff.
# Returns 0 on successful upload (file deleted), non-zero on exhaustion.
upload_one() {
  local local_path="$1"

  if [[ ! -f "${local_path}" ]]; then
    log WARN "file disappeared before upload: ${local_path}"
    return 0
  fi

  # The splitter writes files into ``${COLLECTOR_OUTPUT_DIR}/${capture_id}/``
  # so the parent directory name is the ``capture_id`` per Req 6.3.
  local capture_id
  capture_id="$(basename "$(dirname "${local_path}")")"
  local filename
  filename="$(basename "${local_path}")"

  if [[ -z "${capture_id}" || "${capture_id}" == "/" ]]; then
    log WARN "skipping ${local_path}: unable to derive capture_id from path"
    return 0
  fi

  local s3_uri="s3://${DATA_BUCKET}/raw/${capture_id}/${filename}"
  local attempt=0

  while (( attempt < MAX_ATTEMPTS )); do
    attempt=$(( attempt + 1 ))
    if aws s3 cp \
        --no-progress \
        --region "${AWS_REGION}" \
        "${local_path}" \
        "${s3_uri}"; then
      log INFO "uploaded ${local_path} -> ${s3_uri} (attempt ${attempt})"
      rm -f -- "${local_path}"
      return 0
    fi

    if (( attempt < MAX_ATTEMPTS )); then
      local sleep_seconds=$(( BACKOFF_BASE_SECONDS * (1 << (attempt - 1)) ))
      log WARN "upload attempt ${attempt}/${MAX_ATTEMPTS} failed for ${local_path}; sleeping ${sleep_seconds}s"
      sleep "${sleep_seconds}"
    fi
  done

  log ERROR "upload exhausted ${MAX_ATTEMPTS} attempts for ${local_path}; discarding file"
  rm -f -- "${local_path}"
  return 1
}

# Main event loop. ``inotifywait -m -r -e close_write`` emits one line
# per closed file in the form ``DIR EVENT FILENAME``. We drain the
# fd through a ``while read`` to keep memory bounded.
exec inotifywait \
    --monitor \
    --recursive \
    --event close_write \
    --format '%w%f' \
    --quiet \
    "${COLLECTOR_OUTPUT_DIR}" \
| while IFS= read -r path; do
    case "${path}" in
      *.pcap)
        upload_one "${path}" || true
        ;;
      *)
        # Ignore non-pcap files (e.g., tmp files the kernel may create).
        ;;
    esac
  done
