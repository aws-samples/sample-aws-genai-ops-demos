# Network Transformation Lambdas

Lambda source code for the four tasks of the **Transformation_Workflow**
Step Functions state machine provisioned by `network-infra-stack.ts`
(Task 25, Reqs 6.8, 6.9, 6.12).

## Workflow

```
ListRawObjects → Map(ConvertPcapToParquet) → RunCrawler → ValidateAthena
```

Any task failure transitions to a single `Fail` state emitting:

```json
{ "failed_task": "<name>", "error_reason": "<message>" }
```

The state machine never lingers in a running state after a task failure
(Req 6.9).

## Files

| File | Step | Purpose |
|---|---|---|
| `list_raw_objects.py` | 1 | Lists `s3://{bucket}/raw/{capture_id}/*` objects |
| `convert_pcap_to_parquet.py` | 2 (Map) | tshark → JSON → Parquet conversion (one Lambda invocation per pcap file) |
| `run_crawler.py` | 3 | Triggers the Glue Crawler and waits for completion |
| `validate_athena.py` | 4 | Runs `SELECT 1 FROM pcap_logs WHERE capture_id = '<id>' LIMIT 1` against Athena |

## Deploy-time layer dependency

`convert_pcap_to_parquet.py` requires a Lambda layer providing:

- `tshark` (from the `wireshark` package, ARM64 build) — used to parse
  pcap files into JSON via `tshark -T json`.
- `pyarrow` — used to write Parquet output from the parsed frames.

The CDK stack passes the layer ARN(s) to the Lambda function via the
`TSHARK_LAYER_ARN` environment variable / `Layers` attribute. Operators
build and publish the layer separately (the layer artifact is not in
this repository's scope).

If the layer is missing at runtime, the Lambda raises
`RuntimeError("tshark binary not found on PATH")` which Step Functions
captures and routes to the `Fail` state with a clear `error_reason`.

## Environment variables (set by CDK)

All four Lambdas read configuration exclusively from environment
variables set by the CDK stack — never from event payload — so a
malformed Step Functions input cannot redirect operations to arbitrary
buckets / databases / crawlers.

| Variable | Lambda | Purpose |
|---|---|---|
| `DATA_BUCKET_NAME` | `list_raw_objects`, `convert_pcap_to_parquet`, `validate_athena` | Resolved Network_Data_Bucket name |
| `GLUE_CRAWLER_NAME` | `run_crawler` | Glue Crawler that targets `s3://{bucket}/parquet/` |
| `GLUE_DATABASE` | `validate_athena` | Glue database hosting `pcap_logs` (`goat_network`) |
| `CRAWL_POLL_INTERVAL_SECONDS` | `run_crawler` | Optional polling cadence (default 10s) |
| `CRAWL_TIMEOUT_SECONDS` | `run_crawler` | Optional polling timeout (default 14 min) |
| `ATHENA_POLL_INTERVAL_SECONDS` | `validate_athena` | Optional polling cadence (default 1s) |
| `ATHENA_TIMEOUT_SECONDS` | `validate_athena` | Optional polling timeout (default 60s) |

## IAM scoping

IAM permissions are declared in `network-infra-stack.ts`. Each Lambda's
role is scoped to the minimum permissions required for its single task,
matching the Network Agent runtime role's scoping pattern (Task 27).

## Failure envelope

All four Lambdas raise on failure with messages prefixed by their task
name (e.g. `"ListRawObjects: s3:ListObjectsV2 failed for ..."`). Step
Functions captures these via `Catch` blocks and routes the workflow to
a single shared `Fail` state which emits the structured envelope
documented above.
