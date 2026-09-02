"""T1–T5: spec parsing, target selection, spec validation, JMX build, smoke run.

Thin wrappers. Every implementation lives in builder/ and is covered by 88
deterministic tests; nothing here re-implements logic. Every tool returns a
JSON string (structured output) — the agent reads fields, never parses prose.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from strands import tool

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "builder"
sys.path.insert(0, str(BUILDER))

from jmx_builder import SpecError, build, summarize, validate_spec as _validate  # noqa: E402
from k6_builder import build_k6 as _build_k6  # noqa: E402
from locust_builder import build_locust as _build_locust  # noqa: E402
from spec_input import (  # noqa: E402
    SpecInputError, parse_spec_input as _parse, select_targets as _select,
)

OUT_DIR = Path(os.environ.get("DLT_OUT_DIR") or tempfile.gettempdir()) / "dlt-out"


def _fetch_s3_to_tmp(s3_uri: str) -> Path:
    """Download s3://bucket/key to a temp file and return its path.

    Deployed in AgentCore, the container holds no user files — a spec file path
    means nothing there, whatever its size. S3 is the ingress, and downloading
    to disk keeps every parser below this line path-based and unchanged.

    boto3 lives here rather than in builder/ on purpose: builder/ is
    stdlib-only so its 46 parser tests need no AWS at all.
    """
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"not an S3 URI: {s3_uri!r} (expected s3://bucket/key)")
    bucket, _, key = s3_uri[5:].partition("/")
    if not bucket or not key:
        raise ValueError(f"S3 URI needs both bucket and key: {s3_uri!r}")

    import boto3  # local import: keeps the module importable without AWS

    suffix = Path(key).suffix or ".json"
    fd, tmp_name = tempfile.mkstemp(prefix="spec-", suffix=suffix)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        boto3.client("s3").download_file(bucket, key, str(tmp))
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"cannot download {s3_uri}: {exc}") from exc
    return tmp


def _load_spec(spec_json: str, spec_path: str) -> dict:
    """Accept the spec inline or as a file path — exactly one of the two."""
    if bool(spec_json) == bool(spec_path):
        raise ValueError("pass exactly one of spec_json or spec_path")
    if spec_path:
        p = Path(spec_path)
        if not p.exists():
            raise ValueError(f"spec file not found: {spec_path}")
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(spec_json)


def _ok(**fields) -> str:
    return json.dumps({"ok": True, **fields}, ensure_ascii=False)


def _err(message: str, **fields) -> str:
    return json.dumps({"ok": False, "error": message, **fields},
                      ensure_ascii=False)


@tool
def parse_spec_input(file_path: str = "", spec_s3_uri: str = "",
                     format: str = "auto", exclude_static: bool = True) -> str:
    """Turn a HAR or OpenAPI/Swagger file into a normalised endpoint inventory.

    The first step of any script-generation request. Path parameters are
    templated (/orders/123 becomes /orders/{orderId}); for a HAR, repeated calls
    are merged and their frequency aggregated into traffic_share; secret header
    values are masked. Anything the spec does not state — the auth method, the
    shape of a failure response — is left as null plus a warning rather than
    inferred.

    Pass exactly one of a local path or an S3 URI. In the deployed environment
    (the AgentCore container) the user's file is not present, so use
    spec_s3_uri and pass the bucket path the user uploaded to verbatim. Use
    file_path when running locally.

    Args:
        file_path: absolute path to the input file (for local runs)
        spec_s3_uri: s3://bucket/key (for the deployed environment). Mutually
            exclusive with file_path.
        format: har / openapi / auto (default auto — detected from the file
            contents). A Postman collection is refused: ask the user to export a
            HAR or an OpenAPI document instead.
        exclude_static: drop static resources such as images, CSS and JS
            (default True)

    Returns:
        JSON: {ok, inventory{source, servers, endpoints[], excluded[],
        warnings[]}}. Show warnings to the user verbatim.
    """
    if bool(file_path) == bool(spec_s3_uri):
        return _err("pass exactly one of file_path or spec_s3_uri")
    tmp = None
    try:
        if spec_s3_uri:
            tmp = _fetch_s3_to_tmp(spec_s3_uri)
            file_path = str(tmp)
        inventory = _parse(file_path, format, exclude_static)
    except (SpecInputError, ValueError) as exc:
        return _err(str(exc))
    finally:
        # The inventory is fully in memory; the download was only ever a
        # staging step. Leaving it behind fills the container's /tmp.
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    # origin is the S3 URI, not the temp path it was staged to — the temp path
    # is gone by now and tells a reader nothing about what was tested.
    return _ok(inventory=inventory, origin=spec_s3_uri or file_path)


@tool
def select_targets(inventory_json: str, environment: str, authorized: bool,
                   auth_json: str = "", allow_writes: bool = False,
                   focus_json: str = "") -> str:
    """Sort an inventory into measurable/setup_only/blocked/excluded.

    This tool does not choose — it lays out the options and leaves the decision
    to a human. Every classification carries a reason and how to unblock it.
    Zero measurable endpoints returns status: failed, because a spec must not be
    built against an empty target set. Always show the table to the user and get
    answers to decisions_needed before going further.

    Args:
        inventory_json: the inventory returned by parse_spec_input (JSON string)
        environment: test / stage / prod / unknown — as declared by the user.
            Do not infer it from the hostname. unknown is treated as prod.
        authorized: whether the user confirmed they are allowed to put load on
            this host
        auth_json: JSON of the credentials the user supplied, e.g.
            {"X-User-Id": "..."}. Empty string if none, in which case endpoints
            that need auth become blocked.
        allow_writes: whether write methods may be measurement candidates
            (default False = read-only)
        focus_json: JSON list of paths the user singled out, e.g.
            ["/api/orders"]. If one of them is blocked the reason is recorded in
            notes — it is never dropped silently.

    Returns:
        JSON: {ok, selection{status, summary, measurable[], setup_only[],
        blocked[], excluded[], decisions_needed[], notes[]}}
    """
    try:
        inventory = json.loads(inventory_json)
        if "inventory" in inventory:  # tolerate passing the whole T1 result
            inventory = inventory["inventory"]
        auth = json.loads(auth_json) if auth_json else None
        focus = json.loads(focus_json) if focus_json else None
        selection = _select(inventory, environment=environment,
                            authorized=authorized, auth=auth,
                            allow_writes=allow_writes, focus=focus)
    except (SpecInputError, json.JSONDecodeError) as exc:
        return _err(str(exc))
    return _ok(selection=selection)


@tool
def validate_spec(spec_json: str = "", spec_path: str = "") -> str:
    """Check a TestSpec JSON and reject a dangerous spec before any XML exists.

    On success it also returns the load plan — thread count and share per
    endpoint — so show that table to the user. If it is rejected, fix the spec
    and call again. Do not work around it by writing XML yourself, and do not
    silence a check with body_check_waived: a waiver without a waiver_reason is
    rejected too.

    Args:
        spec_json: the TestSpec as a JSON string (mutually exclusive with
            spec_path)
        spec_path: path to a TestSpec JSON file (easier when the file exists)

    Returns:
        JSON: {ok, errors[], load_plan}. An empty errors list means it passed.
    """
    try:
        spec = _load_spec(spec_json, spec_path)
    except (json.JSONDecodeError, ValueError) as exc:
        return _err(f"cannot load spec: {exc}")
    errors = _validate(spec)
    if errors:
        return _err("spec rejected", errors=errors)
    return _ok(errors=[], load_plan=summarize(spec))


@tool
def build_jmx(spec_json: str = "", spec_path: str = "",
              output_name: str = "generated") -> str:
    """Assemble a JMeter test plan (.jmx) from a validated TestSpec.

    This is deterministic assembly of pre-verified XML fragments — no model ever
    invents a JMeter property name. Do not hand-edit the JMX it produces: if a
    change is needed, fix the spec and rebuild.

    Args:
        spec_json: a TestSpec JSON string that passed validate_spec
        output_name: output filename without extension, e.g. "concert-read-path"

    Returns:
        JSON: {ok, jmx_path, load_plan}
    """
    try:
        spec = _load_spec(spec_json, spec_path)
        errors = _validate(spec)
        if errors:
            return _err("spec rejected — fix it, do not bypass", errors=errors)
        xml = build(spec)
    except (SpecError, json.JSONDecodeError, ValueError) as exc:
        return _err(str(exc))
    OUT_DIR.mkdir(exist_ok=True)
    jmx_path = OUT_DIR / f"{output_name}.jmx"
    jmx_path.write_text(xml, encoding="utf-8")
    return _ok(jmx_path=str(jmx_path), load_plan=summarize(spec))


@tool
def build_k6_script(spec_json: str = "", spec_path: str = "",
                    output_name: str = "generated") -> str:
    """Generate a k6 script (.js) from a validated TestSpec.

    Same spec, same rejection rules — only the output language differs. The
    traffic mix is implemented as a weighted random branch inside a single
    default function, because DLT overrides options.vus/duration/stages and
    per-scenario VU allocation therefore cannot be relied on. Think time stays in
    the script as sleep(). Do not hand-edit the generated script — fix the spec
    and rebuild. Validation runs `k6 run -e SMOKE=1 --vus 1 --iterations 1`,
    where SMOKE mode visits every endpoint exactly once, deterministically.

    Args:
        spec_json: a TestSpec JSON string that passed validate_spec
        output_name: output filename without extension

    Returns:
        JSON: {ok, script_path, load_plan}
    """
    try:
        spec = _load_spec(spec_json, spec_path)
        errors = _validate(spec)
        if errors:
            return _err("spec rejected — fix it, do not bypass", errors=errors)
        script = _build_k6(spec)
    except (SpecError, json.JSONDecodeError, ValueError) as exc:
        return _err(str(exc))
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{output_name}.js"
    path.write_text(script, encoding="utf-8")
    return _ok(script_path=str(path), load_plan=summarize(spec))


@tool
def build_locust_script(spec_json: str = "", spec_path: str = "",
                        output_name: str = "locustfile") -> str:
    """Generate a Locust script from a validated TestSpec.

    The traffic mix uses @task(n) weights — a native feature that DLT does not
    override; setup goes in on_start, which runs once per user and so means the
    same thing as a JMX Once Only Controller; think time is wait_time.
    Assertions are implemented with catch_response, so HTTP 200 with an error
    body is recorded as a failure. DLT's zip entrypoint rule applies: the file
    must be named locustfile.py. Validation runs
    `locust -f <file> --headless -u 1 -r 1 -t 6s --exit-code-on-error 1`.

    Args:
        spec_json: a TestSpec JSON string that passed validate_spec
        output_name: output filename without extension; on DLT upload the name
            is forced to locustfile

    Returns:
        JSON: {ok, script_path, load_plan}
    """
    try:
        spec = _load_spec(spec_json, spec_path)
        errors = _validate(spec)
        if errors:
            return _err("spec rejected — fix it, do not bypass", errors=errors)
        script = _build_locust(spec)
    except (SpecError, json.JSONDecodeError, ValueError) as exc:
        return _err(str(exc))
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{output_name}.py"
    path.write_text(script, encoding="utf-8")
    return _ok(script_path=str(path), load_plan=summarize(spec))


@tool
def validate_script(script_path: str) -> str:
    """Validate a generated script by actually running a 1-VU smoke test.
    Required before any load run.

    The engine is chosen by extension: .jmx runs JMeter with 1 VU and 1
    iteration, .js runs k6 in SMOKE mode (every endpoint visited once,
    deterministically), .py runs Locust with 1 user for 6 seconds and
    --exit-code-on-error. Requests go to the real target, confirming that every
    request succeeds and every correlation variable resolves. A static check is
    not validation: scripts exist that are syntactically fine and measure
    nothing — nine such defect classes have been observed in practice. If the
    matching runtime is not installed this returns a failure; in that case report
    "execution not validated" and never write "confirmed working".

    Args:
        script_path: absolute path to the file produced by build_jmx,
            build_k6_script or build_locust_script

    Returns:
        JSON: {ok, passed, engine, output} — output holds the per-request results
        or the name of the check that failed.
    """
    path = Path(script_path)
    suffix = path.suffix.lower()
    env = None
    if suffix not in (".jmx", ".js", ".py"):
        return _err(f"unknown script type {suffix!r}; expected .jmx/.js/.py")
    if not path.exists():
        return _err(f"script not found: {script_path}")

    if suffix == ".jmx":
        engine = "jmeter"
        cmd = [sys.executable, str(BUILDER / "validate_run.py"), str(path)]
    elif suffix == ".js":
        engine = "k6"
        cmd = ["k6", "run", "--vus", "1", "--iterations", "1",
               "-e", "SMOKE=1", str(path)]
    else:  # .py
        engine = "locust"
        # Locust runs in whatever interpreter LOCUST_PYTHON names, falling back
        # to this one. The deployed image points it at a separate venv because
        # Locust's dependency tree in the agent's site-packages breaks the
        # AgentCore entrypoint — see the Locust block in the Dockerfile. Locally
        # the fallback keeps the existing behaviour.
        cmd = [os.environ.get("LOCUST_PYTHON") or sys.executable,
               "-m", "locust", "-f", str(path), "--headless",
               "-u", "1", "-r", "1", "-t", "6s", "--stop-timeout", "4",
               "--exit-code-on-error", "1", "--only-summary"]
        # SMOKE=1 makes the script visit every endpoint once and quit. Locust
        # has no --iterations, so without it the weighted mix decides coverage
        # and an endpoint can go unvalidated. -t stays as a safety net.
        env = {**os.environ, "SMOKE": "1"}

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=300, env=env)
    except FileNotFoundError:
        return _err(f"{engine} is not installed — execution validation "
                    "impossible; report this honestly", engine=engine,
                    passed=False)
    passed = result.returncode == 0
    output = (result.stdout + result.stderr).strip()
    if not passed and "not found" in output.lower() and engine in output.lower():
        return _err(f"{engine} is not installed — execution validation "
                    "impossible; report this honestly", engine=engine,
                    passed=False, output=output[-1500:])
    return _ok(passed=passed, engine=engine, output=output[-3000:])


@tool
def save_generated_script(script_path: str, bucket: str = "",
                          key: str = "") -> str:
    """Upload a generated script to S3 so the user can retrieve it.

    Use this whenever the user asks to see, download, save, or share the
    generated script. build_jmx / build_k6_script / build_locust_script write
    the file to a path *inside the AgentCore container* (e.g. /tmp/dlt-out/...),
    which the user cannot reach — there is no shell and no file channel back.
    Pointing them at that container path is useless; upload it here and return
    the S3 location instead.

    Args:
        script_path: the jmx_path / script_path returned by a build_* tool.
        bucket: destination bucket. Defaults to the SCRIPT_OUTPUT_BUCKET the
            stack provisioned (leave empty to use it). The runtime role is only
            granted write access to that bucket, so a different bucket would
            need its own grant.
        key: destination object key. Defaults to
            generated-scripts/<filename>.

    Returns:
        JSON: {ok, s3_uri, bucket, key, retrieve_command, presigned_url}. Give
        the user s3_uri and retrieve_command; presigned_url is a time-limited
        direct download and may be null if signing is unavailable.
    """
    bucket = bucket or os.environ.get("SCRIPT_OUTPUT_BUCKET", "")
    if not bucket:
        return _err("no destination bucket: pass bucket= or deploy with "
                    "SCRIPT_OUTPUT_BUCKET set (the stack provisions one)")
    path = Path(script_path)
    if not path.exists():
        return _err(f"script not found: {script_path} — build it first")
    key = key or f"generated-scripts/{path.name}"

    import boto3  # local import: keeps the module importable without AWS

    try:
        s3 = boto3.client("s3")
        s3.upload_file(str(path), bucket, key)
    except Exception as exc:
        return _err(f"upload to s3://{bucket}/{key} failed: {exc}")

    s3_uri = f"s3://{bucket}/{key}"
    try:
        presigned = s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600)
    except Exception:
        presigned = None  # best-effort — the s3:// URI is always returned

    return _ok(s3_uri=s3_uri, bucket=bucket, key=key,
               retrieve_command=f"aws s3 cp {s3_uri} .",
               presigned_url=presigned)
