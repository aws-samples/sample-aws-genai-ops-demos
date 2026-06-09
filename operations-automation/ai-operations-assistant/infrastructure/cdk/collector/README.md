# G.O.A.T. Network Agent — Traffic_Mirror_Collector assets

This directory holds the runtime payload that the
**Traffic_Mirror_Collector** EC2 instance pulls down on first boot. The
files are bundled into a single `aws-s3-assets` Asset by
`network-infra-stack.ts` and downloaded by the collector's UserData
script before the splitter and uploader systemd units start.

| File             | Role                                                                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `splitter.py`    | Per-VNI scapy splitter. Reads VXLAN-decapsulated frames off the kernel `vxlan0` interface, looks each frame's VNI up against the **Vni_Lookup_Table** (DynamoDB) with a 30-second in-memory TTL cache, drops frames whose VNI is unknown, and writes one rotating pcap file per VNI per rotation window. Rotation triggers at 100 MiB or 60 seconds with a maximum of 10 closed files per VNI on local disk. |
| `uploader.sh`    | `inotifywait` + `aws s3 cp` loop. Watches the splitter's rotation directory and uploads each closed pcap to `s3://${DATA_BUCKET}/raw/${capture_id}/${name}`. Uploads retry three times with exponential backoff (1s, 2s, 4s) and the file is retained on disk across retries; only after exhaustion is the file logged and discarded. |
| `bootstrap.sh`   | UserData script. Renders the systemd units (VXLAN device, splitter, uploader), drops the shared environment file at `/etc/goat-collector.env`, and enables/starts everything. The CDK stack templates the asset bucket, asset key, data bucket, VNI lookup table name, and region into placeholder tokens (`__ASSET_BUCKET__`, etc.) before the script is rendered into UserData. |

## Why the assets ship as code rather than baked into an AMI

The collector is intentionally generic — every Capture_Session created
by the agent reuses the same instance. Shipping the splitter and
uploader as files in S3 means a future bug fix only requires a CDK
redeploy, no AMI rebake. The cost is one S3 GET on first boot, which is
negligible.

## Configuration

All tuning knobs are exposed via environment variables in
`/etc/goat-collector.env` rather than hard-coded into the scripts.
Operators can edit the file and restart the unit (`sudo systemctl
restart goat-collector-splitter.service`) to adjust rotation
thresholds, the VNI cache TTL, or the uploader retry policy without
re-deploying. The defaults match the values mandated by Req 6.2 / 6.3.

## Local testing

The splitter and uploader are not Lambda functions — they are plain
Linux services — so `pytest` is the wrong fit. The collector behavior
is exercised end-to-end by the integration tests under
`agents/network-agent/test_*.py` (which simulate the agent →
DynamoDB → collector → S3 path with `moto`).
