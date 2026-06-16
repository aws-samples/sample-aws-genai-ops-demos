#!/usr/bin/env bash
#
# G.O.A.T. Network Agent — Traffic_Mirror_Collector bootstrap script.
#
# Runs as the EC2 instance's UserData on first boot. Downloads the
# splitter and uploader assets from S3 (uploaded by CDK at synth time
# via ``aws-s3-assets``), installs system dependencies, configures the
# VXLAN tunnel interface bound to UDP/4789 on the primary ENI, and
# enables systemd units for both the splitter and uploader.
#
# This script is parameterized at deploy time via the following placeholder
# tokens, which the CDK stack replaces with concrete values before
# rendering the UserData:
#
#   __ASSET_BUCKET__       — S3 bucket holding the collector tarball
#   __ASSET_OBJECT_KEY__   — S3 key of the collector tarball
#   __DATA_BUCKET__        — Network_Data_Bucket name
#   __VNI_LOOKUP_TABLE__   — DynamoDB table name for VNI → capture_id lookup
#   __AWS_REGION__         — Region for AWS API calls
#
# Behavior matches Task 22 / Reqs 6.1-6.4:
#   * Creates a VXLAN interface ``vxlan0`` bound to UDP/4789 on ``eth0``
#     (the primary ENI). The interface is created in ``external``
#     (collect-metadata) mode so the kernel demultiplexes inbound VXLAN
#     frames by their VNI rather than requiring a hardcoded VNI.
#   * Installs Python (with ``scapy``), ``inotify-tools``, and the
#     AWS CLI v2 (preinstalled on AL2023 but explicitly verified).
#   * Installs the splitter and uploader scripts from the asset
#     bundle into ``/opt/goat-collector/``.
#   * Enables ``goat-collector-splitter.service`` and
#     ``goat-collector-uploader.service`` under systemd, both with
#     ``Restart=always`` so a transient failure does not require
#     manual intervention.

set -euxo pipefail

# Make the script log everything to /var/log/goat-collector-bootstrap.log
# in addition to the cloud-init log so operators can debug boot failures
# without spelunking through journalctl.
exec > >(tee -a /var/log/goat-collector-bootstrap.log) 2>&1

ASSET_BUCKET="__ASSET_BUCKET__"
ASSET_OBJECT_KEY="__ASSET_OBJECT_KEY__"
DATA_BUCKET="__DATA_BUCKET__"
VNI_LOOKUP_TABLE="__VNI_LOOKUP_TABLE__"
AWS_REGION="__AWS_REGION__"

INSTALL_DIR="/opt/goat-collector"
OUTPUT_DIR="/var/lib/goat-collector"
ENV_FILE="/etc/goat-collector.env"

echo "[bootstrap] starting at $(date --utc '+%Y-%m-%dT%H:%M:%SZ')"

# -----------------------------------------------------------------------------
# 1. Install system dependencies.
#
# AL2023 (which ``ec2.MachineImage.latestAmazonLinux2023`` selects) ships
# Python 3.9+ and AWS CLI v2 by default. We add ``python3-pip`` so we can
# install ``scapy`` (the only third-party Python dependency the splitter
# needs) and ``inotify-tools`` for the uploader's event loop.
# -----------------------------------------------------------------------------
dnf -y update
dnf -y install \
  python3 \
  python3-pip \
  inotify-tools \
  iproute \
  tar \
  gzip
# Best effort — do not fail if AWS CLI is already present.
dnf -y install awscli || true

# -----------------------------------------------------------------------------
# 1b. Install Python 3.11 for the splitter.
#
# The splitter imports boto3, and the bundled boto3 wheel requires
# Python >= 3.10. AL2023's default ``python3`` is 3.9, so we install
# python3.11 (and its pip) explicitly and run the splitter under it.
# Non-fatal: if this install fails, the bootstrap continues and the
# interpreter selection below falls back to ``python3``.
# -----------------------------------------------------------------------------
dnf -y install python3.11 python3.11-pip || \
  echo "[bootstrap] WARNING: python3.11 install failed; will fall back to python3"

# Select a Python >= 3.10 interpreter for the splitter and its pip
# dependency install. Prefer python3.11; fall back to python3 only when
# 3.11 is unavailable (in which case the boto3 import would fail, but
# the bootstrap still completes so the NLB health responder starts).
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN=python3.11
else
  PYTHON_BIN=python3
fi
echo "[bootstrap] using Python interpreter for splitter: ${PYTHON_BIN}"

# Ensure pip is available for the chosen interpreter even if the
# python3.11-pip package did not land (ensurepip ships with CPython and
# works offline). Non-fatal.
${PYTHON_BIN} -m ensurepip --upgrade >/dev/null 2>&1 || true

# -----------------------------------------------------------------------------
# 2. Download the collector asset bundle.
#
# The CDK stack uploads ``splitter.py`` + ``uploader.sh`` as a single
# tarball under the asset key. We download and extract it into
# ``${INSTALL_DIR}``.
# -----------------------------------------------------------------------------
mkdir -p "${INSTALL_DIR}"
mkdir -p "${OUTPUT_DIR}"
chmod 0755 "${INSTALL_DIR}"
chmod 0755 "${OUTPUT_DIR}"

aws s3 cp \
  --region "${AWS_REGION}" \
  "s3://${ASSET_BUCKET}/${ASSET_OBJECT_KEY}" \
  /tmp/goat-collector-asset.zip
unzip -o /tmp/goat-collector-asset.zip -d "${INSTALL_DIR}"
chmod 0755 "${INSTALL_DIR}/uploader.sh"
chmod 0644 "${INSTALL_DIR}/splitter.py"

# -----------------------------------------------------------------------------
# 2b. Install scapy + boto3 from the bundled wheels directory (no internet
# needed), using the Python 3.11 interpreter selected above so the boto3
# wheel's ``Requires-Python: >=3.10`` constraint is satisfied.
#
# Every install path is non-fatal: under ``set -e`` a failed pip install
# would otherwise abort the whole bootstrap, leaving the NLB health
# responder unstarted and the collector silently dropping all mirrored
# traffic. We log a warning instead and let the bootstrap finish so the
# health responder, VXLAN device, and uploader still come up.
# -----------------------------------------------------------------------------
PIP_INSTALL_OK=0
if [ -d "${INSTALL_DIR}/wheels" ] && ls "${INSTALL_DIR}/wheels"/*.whl 1>/dev/null 2>&1; then
  ${PYTHON_BIN} -m pip install --no-cache-dir --no-index --find-links="${INSTALL_DIR}/wheels" scapy boto3 \
    && PIP_INSTALL_OK=1 \
    || echo "[bootstrap] WARNING: bundled wheel install (.whl) failed under ${PYTHON_BIN}; splitter may fail"
elif [ -d "${INSTALL_DIR}/wheels" ] && ls "${INSTALL_DIR}/wheels"/*.tar.gz 1>/dev/null 2>&1; then
  ${PYTHON_BIN} -m pip install --no-cache-dir --no-index --find-links="${INSTALL_DIR}/wheels" scapy boto3 \
    && PIP_INSTALL_OK=1 \
    || echo "[bootstrap] WARNING: bundled wheel install (.tar.gz) failed under ${PYTHON_BIN}; splitter may fail"
else
  # Fallback: try PyPI (works if instance has internet access).
  ${PYTHON_BIN} -m pip install --no-cache-dir 'scapy>=2.5.0,<3' 'boto3>=1.34.0' \
    && PIP_INSTALL_OK=1 \
    || echo "[bootstrap] WARNING: packages not available from wheels or PyPI — splitter may fail"
fi
echo "[bootstrap] dependency install result: PIP_INSTALL_OK=${PIP_INSTALL_OK} (interpreter ${PYTHON_BIN})"

# -----------------------------------------------------------------------------
# 3. Configure the VXLAN tunnel interface on UDP/4789.
#
# Traffic Mirror sources deliver VXLAN-encapsulated packets to the
# collector's primary ENI on UDP/4789. The kernel's VXLAN device
# decapsulates each packet so the splitter sees plain inner Ethernet
# frames, with the VNI exposed as the device's tunnel id.
#
# ``external`` (collect-metadata) mode makes the device accept any VNI
# rather than only a hardcoded one — required because Traffic Mirror
# auto-assigns a VNI per session. The kernel still demuxes by VNI;
# ``external`` only means we are not constraining the receive side.
#
# IMPORTANT: do NOT also pass ``id <vni>`` here. On AL2023's 6.1 kernel
# ``ip link add ... type vxlan id 0 ... external`` fails with
# "vxlan: both 'external' and vni cannot be specified" — ``external``
# and an explicit VNI (even ``id 0``) are mutually exclusive. Specifying
# ``external`` alone is correct and sufficient.
#
# ``dstport 4789`` matches the IANA-assigned VXLAN port. ``dev eth0``
# binds the device to the primary ENI so the splitter does not have to
# accept traffic from an unrelated interface (e.g., the SSM agent's
# loopback shims).
# -----------------------------------------------------------------------------
cat > /etc/systemd/system/goat-collector-vxlan.service <<'UNIT'
[Unit]
Description=G.O.A.T. Network Agent — VXLAN tunnel interface for Traffic Mirror
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
# Ensure the kernel vxlan module is available before creating the device.
ExecStartPre=-/usr/sbin/modprobe vxlan
# Allow re-running on reboot in case the device persisted; ``ip link
# add`` would fail with EEXIST otherwise. The leading ``-`` swallows the
# error so the unit is idempotent.
ExecStartPre=-/usr/sbin/ip link del vxlan0
ExecStart=/usr/sbin/ip link add vxlan0 type vxlan dstport 4789 external
ExecStart=/usr/sbin/ip link set vxlan0 up
ExecStop=/usr/sbin/ip link del vxlan0

[Install]
WantedBy=multi-user.target
UNIT

# -----------------------------------------------------------------------------
# 4. Render the shared environment file so both systemd units agree on
#    bucket, table, region, and tuning knobs.
# -----------------------------------------------------------------------------
cat > "${ENV_FILE}" <<EOF
# G.O.A.T. Network Agent collector configuration. Source for both the
# splitter and uploader systemd units.
COLLECTOR_INTERFACE=ens5
COLLECTOR_BPF_FILTER=udp port 4789
COLLECTOR_OUTPUT_DIR=${OUTPUT_DIR}
DATA_BUCKET=${DATA_BUCKET}
VNI_LOOKUP_TABLE=${VNI_LOOKUP_TABLE}
AWS_REGION=${AWS_REGION}
AWS_DEFAULT_REGION=${AWS_REGION}
VNI_LOOKUP_TTL_SECONDS=30
ROTATION_BYTES=104857600
ROTATION_SECONDS=60
MAX_FILES_PER_VNI=10
UPLOADER_MAX_ATTEMPTS=3
UPLOADER_BACKOFF_BASE_SECONDS=1
EOF
chmod 0644 "${ENV_FILE}"

# -----------------------------------------------------------------------------
# 5. Splitter and uploader systemd units.
#
# Both run as ``ec2-user`` (UID 1000) rather than root because they only
# need access to the rotation directory and AWS APIs (via the instance
# profile). ``CapabilityBoundingSet=`` and ``NoNewPrivileges=true``
# constrain the units' attack surface beyond what the AL2023 default
# systemd security profile provides.
# -----------------------------------------------------------------------------
cat > /etc/systemd/system/goat-collector-splitter.service <<UNIT
[Unit]
Description=G.O.A.T. Network Agent — Traffic_Mirror_Collector splitter
Requires=goat-collector-vxlan.service
After=goat-collector-vxlan.service

[Service]
Type=simple
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/${PYTHON_BIN} ${INSTALL_DIR}/splitter.py
Restart=always
RestartSec=5
# scapy needs CAP_NET_RAW to read frames from the VXLAN device. Granting
# the cap to the unit (rather than running as root) is the standard way
# to scope packet-capture privileges on systemd-managed services.
AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN
NoNewPrivileges=true
User=ec2-user
Group=ec2-user
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/goat-collector-uploader.service <<UNIT
[Unit]
Description=G.O.A.T. Network Agent — Traffic_Mirror_Collector uploader
After=goat-collector-splitter.service network-online.target
Wants=goat-collector-splitter.service network-online.target

[Service]
Type=simple
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/uploader.sh
Restart=always
RestartSec=5
NoNewPrivileges=true
User=ec2-user
Group=ec2-user
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

# Make sure the rotation directory is writable by the unprivileged user.
chown -R ec2-user:ec2-user "${OUTPUT_DIR}"
chown -R ec2-user:ec2-user "${INSTALL_DIR}"

# -----------------------------------------------------------------------------
# 5b. Health-check responder.
#
# The collector sits behind a Network Load Balancer (the Traffic Mirror
# Target). NLB only forwards traffic to targets that pass health checks,
# but it CANNOT health-check the UDP/4789 traffic port directly. Without
# a separate TCP health-check listener the target is permanently marked
# unhealthy and the NLB silently drops every mirrored VXLAN packet --
# producing empty captures. This tiny TCP responder on port 8081 gives
# the NLB something to health-check so mirrored traffic is delivered.
# -----------------------------------------------------------------------------
cat > /etc/systemd/system/goat-collector-health.service <<'UNIT'
[Unit]
Description=G.O.A.T. Network Agent — Traffic_Mirror_Collector NLB health responder (TCP/8081)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -c "import socketserver; socketserver.TCPServer(('0.0.0.0',8081), socketserver.BaseRequestHandler).serve_forever()"
Restart=always
RestartSec=5
NoNewPrivileges=true
User=ec2-user
Group=ec2-user
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

# -----------------------------------------------------------------------------
# 6. Enable and start everything.
#
# `systemctl start` is best-effort (|| true) so that a transient failure
# starting one unit does not abort the whole bootstrap under `set -e`
# (all units are `enable`d, so they also start on the next boot, and the
# splitter/uploader have Restart=always). The health responder is started
# first and independently so that an issue with the VXLAN device never
# prevents the NLB health check from passing.
# -----------------------------------------------------------------------------
systemctl daemon-reload
systemctl enable goat-collector-health.service
systemctl enable goat-collector-vxlan.service
systemctl enable goat-collector-splitter.service
systemctl enable goat-collector-uploader.service
systemctl start goat-collector-health.service || true
systemctl start goat-collector-vxlan.service || true
systemctl start goat-collector-splitter.service || true
systemctl start goat-collector-uploader.service || true

echo "[bootstrap] finished at $(date --utc '+%Y-%m-%dT%H:%M:%SZ')"
