#!/usr/bin/env python3
"""Tests for the JMX builder.

Two kinds of check:

  1. Spec validation — bad specs must be rejected before any XML is written.
  2. End-to-end — the generated plan is actually run against tests/mock_target.py
     and the JTL is asserted. This is the only way to catch defects that JMeter
     swallows in silence.

Run: python3 tests/test_builder.py
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BUILDER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BUILDER_DIR))

import jmx_builder  # noqa: E402
from jmx_builder import SpecError, build, distribute_threads, json_literal, validate_spec  # noqa: E402

PORT = 18111
BASE_SPEC = json.loads(
    (BUILDER_DIR / "sample-data" / "orders.spec.json").read_text(encoding="utf-8")
)

_failures: list[str] = []
_passes = 0


def check(condition: bool, label: str) -> None:
    global _passes
    if condition:
        _passes += 1
    else:
        _failures.append(label)
        print(f"  FAIL {label}")


def rejects(spec: dict, needle: str, label: str) -> None:
    """The spec must be rejected with an error mentioning `needle`."""
    errors = validate_spec(spec)
    matched = any(needle.lower() in error.lower() for error in errors)
    if not matched:
        print(f"  FAIL {label}\n        errors were: {errors}")
        _failures.append(label)
    else:
        global _passes
        _passes += 1


def spec_without(**overrides) -> dict:
    spec = copy.deepcopy(BASE_SPEC)
    spec.update(overrides)
    return spec


# --------------------------------------------------------------------------
# 1. spec validation
# --------------------------------------------------------------------------


def test_validation() -> None:
    print("spec validation")

    check(validate_spec(copy.deepcopy(BASE_SPEC)) == [], "baseline spec is valid")

    spec = copy.deepcopy(BASE_SPEC)
    spec["target"]["host"] = "https://api.example.com/v1"
    rejects(spec, "bare hostname", "reject host containing scheme and path")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["path"] = "orders"
    rejects(spec, "must start with", "reject path without leading slash")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["success"] = {"status": 200}
    rejects(spec, "needs a body check", "reject status-code-only success criteria")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["success"] = {"status": 204, "body_check_waived": True}
    rejects(spec, "waiver_reason", "reject waiver without a reason")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["success"] = {
        "status": 204,
        "body_check_waived": True,
        "waiver_reason": "204 responses carry no body",
    }
    check(validate_spec(spec) == [], "accept waiver with a reason")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["headers"]["Authorization"] = "Bearer ${nosuchvar}"
    rejects(spec, "not defined", "reject dangling variable reference")

    spec = copy.deepcopy(BASE_SPEC)
    spec["setup"]["endpoints"][0]["extract"][0]["default_value"] = ""
    rejects(spec, "must not be empty", "reject empty extractor default")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["method"] = "GET"
    spec["endpoints"][0]["body"] = "{}"
    rejects(spec, "cannot carry a body", "reject GET with a body")

    spec = copy.deepcopy(BASE_SPEC)
    spec["target"]["environment"] = "prod"
    rejects(spec, "allow_prod_writes", "reject prod writes without explicit opt-in")

    spec = copy.deepcopy(BASE_SPEC)
    spec["target"]["environment"] = "prod"
    spec["target"]["allow_prod_writes"] = True
    check(validate_spec(spec) == [], "accept prod writes when explicitly allowed")

    spec = copy.deepcopy(BASE_SPEC)
    spec["load"]["concurrency"] = 1
    rejects(spec, "below the endpoint", "reject concurrency below endpoint count")

    spec = copy.deepcopy(BASE_SPEC)
    spec["csv_data"] = [{"filename": "/abs/path/users.csv", "variable_names": ["u"]}]
    rejects(spec, "bare filename", "reject absolute CSV path")

    spec = copy.deepcopy(BASE_SPEC)
    spec["setup"]["endpoints"][0]["extract"][0] = {
        "variable": "authToken",
        "type": "regex",
        "expression": "accessToken",
        "default_value": "X",
    }
    rejects(spec, "capturing group", "reject regex extractor with no capture group")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][1]["name"] = spec["endpoints"][0]["name"]
    rejects(spec, "duplicate", "reject duplicate endpoint names")

    spec = copy.deepcopy(BASE_SPEC)
    spec["setup"]["endpoints"][0]["success"] = {"status": 200, "body_check_waived": True,
                                                "waiver_reason": "trust me"}
    rejects(spec, "assert on the body", "reject setup request without a body check")

    # Unknown keys must be rejected, not skipped. Found by a live agent run:
    # the model wrote setup.requests and extractor.var, validate_spec passed
    # both, and the built plan silently had no setup and no extractor — the
    # exact JMeter failure mode this builder exists to prevent.
    spec = copy.deepcopy(BASE_SPEC)
    spec["setup"] = {"requests": spec["setup"]["endpoints"]}
    rejects(spec, "unknown key", "reject setup.requests (must be setup.endpoints)")

    spec = copy.deepcopy(BASE_SPEC)
    spec["setup"]["endpoints"][0]["extract"][0] = {
        "var": "authToken", "type": "jsonpath",
        "expression": "$.token", "default_value": "X"}
    rejects(spec, "unknown key", "reject extractor field 'var' (must be 'variable')")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["succes"] = spec["endpoints"][0].pop("success")
    rejects(spec, "unknown key", "reject misspelled endpoint key")

    spec = copy.deepcopy(BASE_SPEC)
    spec["load"]["rampup_s"] = 10
    rejects(spec, "unknown key", "reject misspelled load key")

    # A bare string is not a harmless shorthand for a one-marker list. Every
    # builder iterates this value, so "content" becomes seven one-character
    # SUBSTRING assertions ANDed together — "the body contains c and o and n
    # and t and e" — which nearly any JSON or HTML error envelope satisfies.
    # The file still builds and the smoke run still passes, so nothing surfaces
    # it. Found by the 8/25 HAR cycle test; the swagger run happened to pass
    # lists and never hit it. Rejected rather than coerced to [value], for the
    # same reason extractors default to a loud sentinel.
    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["success"] = {"status": 200, "body_contains": "content"}
    rejects(spec, "must be a list", "reject bare-string body_contains")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["success"] = {"status": 200,
                                       "body_contains": ['"content":[{'],
                                       "body_not_contains": '"content":[]'}
    rejects(spec, "must be a list", "reject bare-string body_not_contains")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["success"] = {"status": 200, "body_contains": [""]}
    rejects(spec, "non-empty", "reject an empty body marker")

    spec = copy.deepcopy(BASE_SPEC)
    spec["endpoints"][0]["success"] = {"status": 200,
                                       "json_path_equals": [["$.total", 1]]}
    rejects(spec, "json_path_equals", "reject non-object json_path_equals")


# --------------------------------------------------------------------------
# 2. unit behaviour
# --------------------------------------------------------------------------


def test_units() -> None:
    print("unit behaviour")

    counts = distribute_threads([{"weight": 3}, {"weight": 1}], 100)
    check(counts == [75, 25], f"weights 3:1 of 100 -> 75/25 (got {counts})")

    counts = distribute_threads([{"weight": 1}, {"weight": 1}, {"weight": 1}], 10)
    check(sum(counts) == 10, f"totals are exact (got {counts} sum {sum(counts)})")
    check(all(c >= 1 for c in counts), "no endpoint gets zero threads")

    counts = distribute_threads([{"weight": 1000}, {"weight": 1}], 5)
    check(
        all(c >= 1 for c in counts) and sum(counts) == 5,
        f"extreme weights still give everyone a thread (got {counts})",
    )

    # JSONPathAssertion parses the expected value as JSON, so zero-padded codes
    # must stay quoted or they become the number 0.
    check(json_literal("0000") == '"0000"', "zero-padded code is quoted")
    check(json_literal("5") == "5", "plain number passes through")
    check(json_literal("true") == "true", "boolean passes through")
    check(json_literal("OK") == '"OK"', "plain text is quoted")
    check(json_literal("007") == '"007"', "leading-zero number is quoted")

    xml = build(copy.deepcopy(BASE_SPEC))
    check("<HTTPSampler.domain>" not in xml, "samplers do not repeat the domain")
    check('name="HTTPSampler.domain"' in xml, "HTTP Request Defaults sets the domain")
    check(xml.count('testclass="ThreadGroup"') == 2, "one thread group per endpoint")
    check(
        xml.count('testclass="OnceOnlyController"') == 2,
        "setup is repeated in every thread group so each VU authenticates",
    )
    check(
        'name="ConstantTimer.delay"' in xml and 'name="RandomTimer.range"' in xml,
        "think time uses the verified UniformRandomTimer property names",
    )
    check(
        'name="TransactionController.includeTimers">false' in xml,
        "think time is excluded from transaction response times",
    )
    check("BackendListener" not in xml, "no backend listener (unreachable from DLT)")
    check(
        'name="Assertion.test_type">8' in xml,
        "status assertion uses Equals (8), not SUBSTRING",
    )

    # Body checks must be SUBSTRING (16) / NOT|SUBSTRING (20), never CONTAINS (2).
    # JMeter's "Contains" is a REGEX match, so a literal marker like
    # '"content":[]' raises MalformedCachePatternException at runtime and the
    # sampler fails with "Bad test configuration" instead of asserting anything.
    body_spec = copy.deepcopy(BASE_SPEC)
    body_spec["endpoints"][0]["success"] = {
        "status": 200,
        "body_contains": ['"content":[{'],
        "body_not_contains": ['"content":[]'],
    }
    body_xml = build(body_spec)
    check(
        'name="Assertion.test_type">16' in body_xml,
        "body_contains uses Substring (16), not regex Contains (2)",
    )
    check(
        'name="Assertion.test_type">20' in body_xml,
        "body_not_contains uses Not|Substring (20), not Not|Contains (6)",
    )
    check(
        'name="Assertion.test_type">2<' not in body_xml,
        "no regex-based Contains assertion is emitted for literal markers",
    )
    # A marker must survive as one whole test string. Checked structurally
    # because the per-character failure mode (a bare string iterated into 12
    # one-char assertions) produces XML that is still well-formed and still
    # passes a smoke run — the marker simply is not in there any more.
    collections = [
        [prop.text for prop in coll]
        for assertion in ET.fromstring(body_xml).iter("ResponseAssertion")
        for coll in assertion.iter("collectionProp")
        if coll.get("name") == "Asserion.test_strings"
    ]
    for marker in ('"content":[{', '"content":[]'):
        holding = [texts for texts in collections if marker in texts]
        check(
            len(holding) == 1 and len(holding[0]) == 1,
            f"marker {marker!r} renders as one whole test string, not one per "
            f"character (collections were {collections})",
        )


# --------------------------------------------------------------------------
# 3. end-to-end against the mock target
# --------------------------------------------------------------------------


def wait_for_target(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/empty", timeout=1)
            return True
        except urllib.error.HTTPError:
            return True
        except OSError:
            time.sleep(0.2)
    return False


def run_validator(spec: dict, tmpdir: Path, label: str) -> tuple[int, str]:
    spec_path = tmpdir / f"{label}.spec.json"
    jmx_path = tmpdir / f"{label}.jmx"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    build_result = subprocess.run(
        [sys.executable, str(BUILDER_DIR / "jmx_builder.py"), str(spec_path),
         "-o", str(jmx_path)],
        capture_output=True, text=True,
    )
    if build_result.returncode != 0:
        return build_result.returncode, build_result.stderr

    validate_result = subprocess.run(
        [sys.executable, str(BUILDER_DIR / "validate_run.py"), str(jmx_path)],
        capture_output=True, text=True,
    )
    return validate_result.returncode, validate_result.stdout + validate_result.stderr


def test_end_to_end(tmpdir: Path) -> None:
    print("end-to-end (real jmeter run against mock target)")

    spec = copy.deepcopy(BASE_SPEC)
    spec["target"]["port"] = PORT

    rc, output = run_validator(spec, tmpdir, "happy")
    check(rc == 0, f"correct spec passes the smoke run (rc={rc})\n{output}")

    # The core claim: a wrong password returns HTTP 200 with a null token. A
    # status-code-only assertion calls that success; ours must not.
    broken = copy.deepcopy(spec)
    broken["variables"]["LOGIN_PASSWORD"] = "wrong"
    rc, output = run_validator(broken, tmpdir, "badlogin")
    check(rc != 0, "HTTP 200 login failure is detected as a failure")
    check(
        "REQ_Login" in output,
        f"the failure is attributed to the login request\n{output}",
    )

    # An endpoint that returns an error envelope with HTTP 200.
    envelope = copy.deepcopy(spec)
    envelope["endpoints"] = [
        {
            "name": "Flaky",
            "path": "/flaky",
            "method": "GET",
            "success": {
                "status": 200,
                "body_contains": ['"resultCode":"0000"'],
            },
        }
    ]
    envelope["load"]["concurrency"] = 1
    del envelope["setup"]
    rc, output = run_validator(envelope, tmpdir, "envelope")
    check(rc == 0, f"passing case of the flaky endpoint succeeds (rc={rc})\n{output}")

    # A 204 with a waiver is legitimate and must pass.
    empty = copy.deepcopy(spec)
    empty["endpoints"] = [
        {
            "name": "Empty",
            "path": "/empty",
            "method": "GET",
            "success": {
                "status": 204,
                "body_check_waived": True,
                "waiver_reason": "204 No Content has no body to assert on",
            },
        }
    ]
    empty["load"]["concurrency"] = 1
    del empty["setup"]
    rc, output = run_validator(empty, tmpdir, "empty")
    check(rc == 0, f"204 with waiver passes (rc={rc})\n{output}")

    # A correlation that silently yields nothing must be caught. Point the
    # extractor at a field the login response does not contain.
    dangling = copy.deepcopy(spec)
    dangling["setup"]["endpoints"][0]["extract"][0]["expression"] = "$.noSuchField"
    rc, output = run_validator(dangling, tmpdir, "dangling")
    check(rc != 0, f"failed correlation is detected (rc={rc})\n{output[:600]}")


def main() -> int:
    tmpdir = Path(__file__).resolve().parent / "_tmp"
    tmpdir.mkdir(exist_ok=True)

    test_validation()
    test_units()

    target = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parent / "mock_target.py"), str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_target(PORT):
            print("mock target failed to start", file=sys.stderr)
            return 2
        test_end_to_end(tmpdir)
    finally:
        target.terminate()
        target.wait(timeout=10)

    print(f"\n{_passes} passed, {len(_failures)} failed")
    for failure in _failures:
        print(f"  - {failure}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
