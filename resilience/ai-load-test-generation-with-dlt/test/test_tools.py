#!/usr/bin/env python3
"""Tool-contract tests: call every @tool directly, no Bedrock, no AWS writes.

builder/tests/ proves the implementations; this file proves the wrapper
contract the LLM depends on:
  - every tool returns parseable JSON with an "ok" field
  - errors come back as {ok:false, error} instead of exceptions
  - the safety refusals (unapproved run, unconfirmed cancel) fire in code

Run: python3 test/test_tools.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_failures: list[str] = []
_passes = 0


def check(condition: bool, label: str) -> None:
    global _passes
    if condition:
        _passes += 1
    else:
        _failures.append(label)
        print(f"  FAIL {label}")


def call(tool_fn, **kwargs) -> dict:
    """Tools are Strands DecoratedFunctionTool objects but stay callable."""
    return json.loads(tool_fn(**kwargs))


def main() -> int:
    from tools import ALL_TOOLS
    from tools.script_tools import (
        build_jmx, parse_spec_input, select_targets, validate_spec,
        validate_script,
    )
    from tools.dlt_tools import (
        cancel_test, create_scenario, run_scenario, upload_script,
        poll_test_status, fetch_results,
    )

    print("tool registry")
    check(len(ALL_TOOLS) == 14, f"14 tools registered (got {len(ALL_TOOLS)})")
    for t in ALL_TOOLS:
        spec = t.tool_spec
        check(bool(spec.get("description")),
              f"{spec.get('name')} has a docstring-derived description")

    print("script pipeline (real implementations)")
    swagger = ROOT / "sample-data" / "swagger-unified.json"
    r = call(parse_spec_input, file_path=str(swagger))
    check(r["ok"] and len(r["inventory"]["endpoints"]) == 16,
          "parse_spec_input returns the 16-endpoint inventory")

    check(r["origin"] == str(swagger), "origin records where the spec came from")

    # Spec ingress: exactly one source. Deployed there is no local file, so a
    # silent default either way would parse the wrong thing or nothing.
    check(not call(parse_spec_input)["ok"],
          "parse_spec_input with neither input is refused")
    check(not call(parse_spec_input, file_path=str(swagger),
                   spec_s3_uri="s3://b/k.json")["ok"],
          "parse_spec_input with both inputs is refused")
    r1b = call(parse_spec_input, spec_s3_uri="not-a-uri")
    check(not r1b["ok"] and "s3://" in r1b["error"],
          "a non-S3 URI is rejected with the expected form")
    r1c = call(parse_spec_input, spec_s3_uri="s3://bucket-only")
    check(not r1c["ok"] and "bucket and key" in r1c["error"],
          "an S3 URI without a key is rejected before any AWS call")

    # The S3 path end to end, with the download stubbed: parse the same swagger
    # and confirm the staged temp file does not survive the call.
    import tools.script_tools as st
    staged: list[Path] = []
    real_fetch = st._fetch_s3_to_tmp

    def fake_fetch(uri: str) -> Path:
        assert uri == "s3://spec-bucket/uploads/swagger-unified.json"
        tmp = Path(str(ROOT / "out" / "s3-staged.json"))
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_text(swagger.read_text(encoding="utf-8"), encoding="utf-8")
        staged.append(tmp)
        return tmp

    st._fetch_s3_to_tmp = fake_fetch
    try:
        uri = "s3://spec-bucket/uploads/swagger-unified.json"
        r1d = call(parse_spec_input, spec_s3_uri=uri)
    finally:
        st._fetch_s3_to_tmp = real_fetch
    check(r1d["ok"] and len(r1d["inventory"]["endpoints"]) == 16,
          "an S3-sourced spec parses to the same inventory as the local file")
    check(r1d["origin"] == uri, "origin reports the S3 URI, not the temp path")
    check(staged and not staged[0].exists(),
          "the staged temp file is deleted — /tmp does not accumulate specs")

    r2 = call(select_targets, inventory_json=json.dumps(r["inventory"]),
              environment="test", authorized=True)
    check(r2["ok"] and r2["selection"]["summary"]["measurable"] == 2,
          "select_targets classifies the concert swagger (2 measurable)")
    check(r2["selection"]["probe_performed"] is False,
          "probe status is explicit in tool output")

    # whole-T1-result tolerance
    r2b = call(select_targets, inventory_json=json.dumps(r),
               environment="test", authorized=True)
    check(r2b["ok"], "select_targets tolerates the full T1 result envelope")

    spec_path = ROOT / "builder" / "sample-data" / "concerts.spec.json"
    spec_json = spec_path.read_text(encoding="utf-8")
    r3 = call(validate_spec, spec_json=spec_json)
    check(r3["ok"] and r3["errors"] == [], "validate_spec passes the known-good spec")
    check("threads" in r3["load_plan"], "load plan is returned for the user")

    broken = json.loads(spec_json)
    broken["endpoints"][0]["success"] = {"status": 200}
    r4 = call(validate_spec, spec_json=json.dumps(broken))
    check(not r4["ok"] and any("body check" in e for e in r4["errors"]),
          "status-only assertion is rejected through the wrapper")

    r5 = call(build_jmx, spec_json=spec_json, output_name="tooltest")
    check(r5["ok"] and Path(r5["jmx_path"]).exists(), "build_jmx writes the file")
    check(not call(build_jmx, spec_json=json.dumps(broken),
                   output_name="x")["ok"],
          "build_jmx re-validates — a rejected spec cannot be built")

    r6 = call(validate_script, script_path="/nonexistent.jmx")
    check(not r6["ok"], "validate_script fails on a missing file")

    print("DLT safety gates (no AWS calls)")
    r7 = call(run_scenario, test_id="x", approval_summary="")
    check(not r7["ok"] and "REFUSED" in r7["error"],
          "run without approval summary is refused in code")
    r8 = call(run_scenario, test_id="never-registered",
              approval_summary="target X, 10 VUs, 30s — approved by user")
    check(not r8["ok"] and "not registered" in r8["error"],
          "run of an unregistered test_id is refused")
    r9 = call(cancel_test, test_id="x", confirmed_running=False)
    check(not r9["ok"] and "poll_test_status" in r9["error"],
          "cancel without confirming running state is refused")
    r10 = call(upload_script, script_path="/tmp/x.jmx", test_id="t")
    check(not r10["ok"] and "discover_dlt_config" in r10["error"],
          "upload before discovery is refused")
    r11 = call(create_scenario, test_id="t", test_name="n",
               test_description="d", concurrency=10,
               ramp_up="10x", hold_for="30s")
    check(not r11["ok"],
          "bad ramp-up unit is rejected before any API call")
    for fn, name in ((poll_test_status, "poll"), (fetch_results, "fetch")):
        r = call(fn, test_id="t")
        check(not r["ok"] and "discover_dlt_config" in r["error"],
              f"{name} before discovery is refused")

    print("multi-engine dispatch")
    r12 = call(validate_script, script_path="/nonexistent.xyz")
    check(not r12["ok"] and "unknown script type" in r12["error"],
          "unknown extension is rejected with the expected list")
    r13 = call(create_scenario, test_id="t", test_name="n",
               test_description="d", concurrency=10,
               ramp_up="10s", hold_for="30s", test_type="gatling")
    check(not r13["ok"] and "jmeter/k6/locust" in r13["error"],
          "unsupported engine type is rejected before any API call")

    print(f"\n{_passes} passed, {len(_failures)} failed")
    for f in _failures:
        print(f"  - {f}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
