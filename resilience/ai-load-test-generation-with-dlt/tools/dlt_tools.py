"""T7–T12: DLT discovery, upload, register, run, poll, results.

The payload contract here is not from documentation — it was verified against
a live DLT deployment (2026-08-09, LaunchWizard-dlt/us-east-2) after the
documented shape returned three different 400s. Key facts baked in:

  - Auth is IAM SigV4 (not Cognito; that pool is for the web console).
  - Scripts go to public/test-scenarios/<testType>/<TEST_ID>.<ext>.
  - testType is "jmeter"/"k6"/"locust", never "simple".
  - testTaskConfigs[] and regionalTaskDetails.<region> are both required and
    their infrastructure fields come from GET /regions — never hardcoded.
  - Optional fields (recurrence, cronValue) are OMITTED, not sent as "".
  - eventBridge is a string.
  - saveOnly:true registers without running; the run is the same POST with
    saveOnly:false. There is no separate run endpoint.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from strands import tool
import urllib.request

ROOT = Path(__file__).resolve().parent.parent

# Session-scoped state: discovery result and pending (registered, unapproved)
# payloads keyed by test_id. The run tool re-sends a registered payload with
# saveOnly flipped — per the verified API shape.
_config: dict = {}
_registered: dict[str, dict] = {}


def _ok(**fields) -> str:
    return json.dumps({"ok": True, **fields}, ensure_ascii=False)


def _err(message: str, **fields) -> str:
    return json.dumps({"ok": False, "error": message, **fields},
                      ensure_ascii=False)


def _sigv4_call(method: str, url: str, region: str, body: dict | None = None) -> tuple[int, dict | str]:
    """Signed call to the DLT API. IAM SigV4 — verified, not Cognito."""
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        return 0, "no AWS credentials available in the runtime environment"
    data = json.dumps(body).encode() if body is not None else None
    request = AWSRequest(method=method, url=url, data=data,
                         headers={"Content-Type": "application/json"})
    SigV4Auth(credentials, "execute-api", region).add_auth(request)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict(request.headers))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
            code = resp.status
    except urllib.error.HTTPError as exc:
        text = exc.read().decode()
        code = exc.code
    except urllib.error.URLError as exc:
        # Connection-level failure (timeout, DNS, no route). Do NOT raise —
        # return a diagnosable error so the caller reports it instead of the
        # agent turn crashing.
        return 0, f"network error reaching {url}: {exc.reason}"
    except Exception as exc:  # last-resort: never crash the agent turn on I/O
        return 0, f"request to {url} failed: {type(exc).__name__}: {exc}"
    try:
        return code, json.loads(text)
    except json.JSONDecodeError:
        return code, text


@tool
def discover_dlt_config(stack_name: str = "", region: str = "") -> str:
    """Look up the DLT stack's API endpoint, bucket and per-region run parameters.

    The first step of any DLT work. Hardcoding a bucket name, subnet or task
    definition ARN breaks silently in another account or region, so always read
    them from here. It takes two steps: the CloudFormation stack outputs (API URL
    and bucket) plus GET /regions, which supplies the eleven infrastructure
    fields that registering a scenario requires.

    Args:
        stack_name: name of the DLT CloudFormation stack. Falls back to the
            DLT_STACK_NAME environment variable when empty.
        region: the region the stack lives in. Falls back to DLT_REGION.

    Returns:
        JSON: {ok, api_endpoint, scenarios_bucket, console_url, regions[]}.
        Give console_url to the user — that is where progress and charts are.
    """
    stack_name = stack_name or os.environ.get("DLT_STACK_NAME", "")
    region = region or os.environ.get("DLT_REGION", "")
    if not stack_name or not region:
        return _err("stack_name and region are required (or set DLT_STACK_NAME/"
                    "DLT_REGION); do not guess stack names")
    try:
        cfn = boto3.client("cloudformation", region_name=region)
        stacks = cfn.describe_stacks(StackName=stack_name)["Stacks"]
    except Exception as exc:
        return _err(f"cannot read stack {stack_name} in {region}: {exc}")
    outputs = {o["OutputKey"]: o["OutputValue"]
               for o in stacks[0].get("Outputs", [])}

    # The API output key carries a logical-ID hash (DLTApiEndpointD98B09AC) —
    # match by prefix, never by exact name.
    api_endpoint = next((v for k, v in outputs.items()
                         if k.startswith("DLTApiEndpoint")), None)
    bucket = outputs.get("ScenariosBucket")
    if not api_endpoint or not bucket:
        return _err(f"stack {stack_name} lacks DLT outputs "
                    f"(found keys: {sorted(outputs)}); is this a DLT stack?")

    code, regions_body = _sigv4_call("GET", api_endpoint.rstrip("/") + "/regions",
                                     region)
    if code != 200:
        return _err(f"GET /regions returned {code}: {regions_body}")
    regions = regions_body.get("regions", [])
    incompatible = [r["region"] for r in regions if not r.get("compatible", True)]

    # Replace, never merge: re-running discovery against a different stack must
    # not leave the previous stack's bucket or endpoint behind. A half-updated
    # config uploads the script to one deployment and registers it in another.
    # Registered payloads go too — they embed the old stack's cluster, subnets
    # and role ARNs, so replaying one against a new endpoint is meaningless.
    switched = bool(_config) and _config.get("api_endpoint") != api_endpoint.rstrip("/")
    if switched:
        _registered.clear()
    _config.clear()
    _config.update({
        "api_endpoint": api_endpoint.rstrip("/"),
        "api_region": region,
        "scenarios_bucket": bucket,
        "console_url": outputs.get("ConsoleURL"),
        "regions": regions,
    })
    return _ok(api_endpoint=_config["api_endpoint"],
               scenarios_bucket=bucket,
               console_url=_config["console_url"],
               regions=[{k: r.get(k) for k in
                         ("region", "version", "compatible")} for r in regions],
               incompatible_regions=incompatible)


# extension mapping per DLT convention: the S3 filename must be
# <TEST_ID>.<ext> under public/test-scenarios/<testType>/
_ENGINE_EXT = {".jmx": ("jmeter", "jmx"), ".js": ("k6", "js"),
               ".py": ("locust", "py")}


@tool
def upload_script(script_path: str, test_id: str) -> str:
    """Upload a validated script to the S3 path DLT expects.

    The engine follows from the extension: .jmx to jmeter, .js to k6, .py to
    locust. The key is fixed at
    public/test-scenarios/<engine>/<TEST_ID>.<extension>. Dropping the public/
    prefix still uploads successfully, but the container cannot then find the
    script and only the test fails — this has been observed. Upload only files
    that passed validate_script.

    Args:
        script_path: absolute path to a script that passed validate_script
        test_id: the scenario ID. The S3 filename and create_scenario's script
            value must both match this ID.

    Returns:
        JSON: {ok, s3_key, bucket, test_type} — pass test_type through to
        create_scenario verbatim.
    """
    if not _config:
        return _err("run discover_dlt_config first")
    path = Path(script_path)
    if not path.exists():
        return _err(f"script not found: {script_path}")
    engine_info = _ENGINE_EXT.get(path.suffix.lower())
    if engine_info is None:
        return _err(f"unknown script type {path.suffix!r}; expected .jmx/.js/.py")
    test_type, ext = engine_info
    key = f"public/test-scenarios/{test_type}/{test_id}.{ext}"
    s3 = boto3.client("s3", region_name=_config["api_region"])
    try:
        s3.upload_file(str(path), _config["scenarios_bucket"], key)
        s3.head_object(Bucket=_config["scenarios_bucket"], Key=key)
    except Exception as exc:
        return _err(f"upload failed: {exc}")
    return _ok(s3_key=key, bucket=_config["scenarios_bucket"],
               test_type=test_type)


@tool
def create_scenario(test_id: str, test_name: str, test_description: str,
                    concurrency: int, ramp_up: str, hold_for: str,
                    task_count: int = 1, test_type: str = "jmeter") -> str:
    """Register a DLT scenario and nothing more (saveOnly: true — no load).

    Running it is run_scenario's job, after a separate approval. That separation
    is the entire approval gate: a POST without saveOnly registers and starts the
    test in one step. The payload's infrastructure fields come straight from the
    GET /regions values that discover_dlt_config read. ramp_up and hold_for take
    only s or m units ("10s", "5m") — the API accepts ms/h/d but its internal
    parser cannot handle them.

    Args:
        test_id: the same ID used with upload_script
        test_name: name of the scenario
        test_description: what is being tested and why
        concurrency: VUs per task
        ramp_up: ramp-up time, e.g. "10s" or "2m"
        hold_for: hold time, e.g. "30s" or "5m"
        task_count: number of Fargate tasks (total VUs = task_count ×
            concurrency)
        test_type: jmeter / k6 / locust — pass through the test_type that
            upload_script returned. If it disagrees with the uploaded file the
            container cannot find the script. All three engines have been
            verified against real DLT. k6 and locust implement the mix
            probabilistically, so the measured shares can drift ±2-3 percentage
            points from the plan — say so when you report results.

    Returns:
        JSON: {ok, test_id, status} — status should be "created".
    """
    if test_type not in ("jmeter", "k6", "locust"):
        return _err(f"test_type must be jmeter/k6/locust, got {test_type!r}")
    if not _config:
        return _err("run discover_dlt_config first")
    if not any(ramp_up.endswith(u) for u in ("s", "m")) or \
       not any(hold_for.endswith(u) for u in ("s", "m")):
        return _err("ramp_up/hold_for must use 's' or 'm' units")
    regions = _config["regions"]
    if not regions:
        return _err("no execution regions available")
    reg = regions[0]
    infra_keys = ("region", "version", "taskCluster", "taskDefinition",
                  "stackId", "subnetA", "subnetB", "taskSecurityGroup",
                  "taskRoleArn", "executionRoleArn", "ecsCloudWatchLogGroup")
    cfg = {k: reg[k] for k in infra_keys if k in reg}
    cfg.update({"taskCount": task_count, "concurrency": concurrency})
    rtd = dict(cfg)
    rtd["dltAvailableTasks"] = reg.get("dltAvailableTasks", 50)

    script_ext = {"jmeter": "jmx", "k6": "js", "locust": "py"}[test_type]
    # recurrence/cronValue are OMITTED (empty strings fail validation);
    # eventBridge is a string. All verified against the live API (jmeter).
    payload = {
        "testName": test_name,
        "testDescription": test_description,
        "testType": test_type,
        "fileType": "script",
        "testId": test_id,
        "testScenario": {
            "execution": [{"ramp-up": ramp_up, "hold-for": hold_for,
                           "scenario": test_name, "executor": test_type,
                           "taskCount": task_count, "concurrency": concurrency}],
            "scenarios": {test_name: {"script": f"{test_id}.{script_ext}"}},
        },
        "testTaskConfigs": [cfg],
        "regionalTaskDetails": {reg["region"]: rtd},
        "showLive": False,
        "eventBridge": "",
        "tags": [],
        "saveOnly": True,
    }
    code, body = _sigv4_call("POST", _config["api_endpoint"] + "/scenarios",
                             _config["api_region"], payload)
    if code != 200:
        # DLT's 400 names the offending fields — report it verbatim, it is
        # the cheapest diagnostic available.
        return _err(f"registration failed ({code})", response=body)
    _registered[test_id] = payload
    return _ok(test_id=test_id, status=body.get("status"),
               total_vu=task_count * concurrency,
               region=reg["region"])


@tool
def run_scenario(test_id: str, approval_summary: str) -> str:
    """Run a registered scenario. The only tool that actually generates load.

    Before calling it you must show the user a summary — target host, total VUs,
    duration, region — and get explicit approval. Put that same approved summary
    into approval_summary verbatim; it is the audit trail. If it is empty this
    tool refuses the call. A run cannot be undone.

    When the DLT region differs from the target's region the round trip is mixed
    into the response times (measured: +170ms cross-region). Always report that
    alongside the numbers.

    Args:
        test_id: the ID registered by create_scenario
        approval_summary: the run summary shown to the user and approved by them,
            e.g. "target X, 10 VUs, 30s, us-east-2 — approved by user"

    Returns:
        JSON: {ok, test_id, status, console_url} — status will be "queued".
        Expect about 90 seconds of provisioning plus the load duration. Do not
        block and poll.
    """
    if not approval_summary or len(approval_summary.strip()) < 10:
        return _err("REFUSED: run_scenario requires the approval summary that "
                    "was shown to and confirmed by the user. Load generation "
                    "is irreversible.")
    if test_id not in _registered:
        return _err(f"test_id {test_id} was not registered this session; "
                    "call create_scenario first")
    payload = dict(_registered[test_id])
    payload["saveOnly"] = False
    code, body = _sigv4_call("POST", _config["api_endpoint"] + "/scenarios",
                             _config["api_region"], payload)
    if code != 200:
        return _err(f"run failed ({code})", response=body)
    return _ok(test_id=test_id, status=body.get("status"),
               started_at=body.get("startTime"),
               console_url=_config.get("console_url"),
               approval_summary=approval_summary)


@tool
def poll_test_status(test_id: str) -> str:
    """Read a test's status once. Never block and poll — one read per call.

    The states go queued, provisioning (about 90s), running, complete. Even a
    40-second load run takes over three minutes end to end (3m16s measured).
    Account for that overhead before telling anyone it is nearly done. Twenty to
    thirty seconds is a sensible gap between reads.

    Args:
        test_id: the scenario ID to read

    Returns:
        JSON: {ok, status, task_failure_count, complete_tasks, start_time,
        end_time}. If the status is complete but task_failure_count > 0, do not
        report completion — some tasks died, so the metrics do not reflect the
        load that was planned.
    """
    if not _config:
        return _err("run discover_dlt_config first")
    code, body = _sigv4_call(
        "GET", f"{_config['api_endpoint']}/scenarios/{test_id}",
        _config["api_region"])
    if code != 200:
        return _err(f"status query failed ({code})", response=body)
    return _ok(status=body.get("status"),
               task_failure_count=body.get("taskFailureCount", 0),
               complete_tasks=body.get("completeTasks"),
               start_time=body.get("startTime"),
               end_time=body.get("endTime"))


@tool
def cancel_test(test_id: str, confirmed_running: bool) -> str:
    """Stop a running test. This cannot be undone.

    Before calling it, confirm with poll_test_status that the test really is
    running, and confirm with the user that they want it stopped. Caveat: this
    endpoint is derived from reading the DLT source and an actual cancellation has
    never been exercised — re-check the outcome by polling.

    Args:
        test_id: the scenario ID to stop
        confirmed_running: whether poll_test_status confirmed the running state

    Returns:
        JSON: {ok, response}
    """
    if not confirmed_running:
        return _err("verify the test is actually running first "
                    "(poll_test_status), then pass confirmed_running=true")
    code, body = _sigv4_call(
        "POST", f"{_config['api_endpoint']}/scenarios/{test_id}",
        _config["api_region"])
    if code != 200:
        return _err(f"cancel failed ({code})", response=body)
    return _ok(response=body)


@tool
def fetch_results(test_id: str) -> str:
    """Read the result metrics of a completed test.

    The results live in the results field of GET /scenarios/{testId}; there is no
    separate endpoint. labels[] is the per-endpoint breakdown, where each label is
    a JMX Transaction Controller name (TX_<endpoint name>). Use those to check and
    report two things: (1) the traffic mix — the ratio of labels[].succ against
    the planned weights, and (2) setup — whether the succ count of the TX_Seed-type
    labels equals the number of VUs. Response-time values are strings in seconds
    ("0.182"), so convert them to numbers before comparing.

    DLT metrics are client-side. Attributing a server-side bottleneck needs
    CloudWatch or X-Ray as well; do not state a root cause from client metrics
    alone.

    Args:
        test_id: the ID of the completed scenario

    Returns:
        JSON: {ok, results{total, per_region}, labels[]}
    """
    if not _config:
        return _err("run discover_dlt_config first")
    code, body = _sigv4_call(
        "GET", f"{_config['api_endpoint']}/scenarios/{test_id}",
        _config["api_region"])
    if code != 200:
        return _err(f"query failed ({code})", response=body)
    if body.get("status") != "complete":
        return _err(f"test is {body.get('status')}, not complete; "
                    "results are empty until completion")
    results = body.get("results", {})
    labels = []
    for region_key, region_val in results.items():
        if isinstance(region_val, dict):
            labels.extend(region_val.get("labels") or [])
    return _ok(status=body.get("status"),
               task_failure_count=body.get("taskFailureCount", 0),
               total=results.get("total"),
               labels=labels)
