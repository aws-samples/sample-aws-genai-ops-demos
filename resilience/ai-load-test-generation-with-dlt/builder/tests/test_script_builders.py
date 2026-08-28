#!/usr/bin/env python3
"""Tests for k6_builder and locust_builder.

Same philosophy as test_builder.py: generated scripts are RUN, not just
linted, against tests/mock_target.py — the mock that returns HTTP 200 with
error envelopes, which is exactly what a status-only check cannot see.

Skips (with a loud notice, never silently) the execution tests for a runtime
that is not installed.

Run: python3 tests/test_script_builders.py
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BUILDER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BUILDER_DIR))

from jmx_builder import SpecError  # noqa: E402
from k6_builder import build_k6  # noqa: E402
from locust_builder import build_locust  # noqa: E402

PORT = 18112
BASE_SPEC = json.loads(
    (BUILDER_DIR / "sample-data" / "orders.spec.json").read_text(encoding="utf-8"))

_failures: list[str] = []
_passes = 0
_skips: list[str] = []


def check(condition: bool, label: str) -> None:
    global _passes
    if condition:
        _passes += 1
    else:
        _failures.append(label)
        print(f"  FAIL {label}")


def local_spec() -> dict:
    spec = copy.deepcopy(BASE_SPEC)
    spec["target"]["port"] = PORT
    spec["load"]["think_time_ms"] = 10
    spec["load"]["think_time_jitter_ms"] = 10
    return spec


def correlated_spec(expected: str = "${orderId}") -> dict:
    """A spec whose assertion compares a response field to a correlated value.

    The mock returns orderId "ord-555" from POST /orders and the same id from
    GET /orders, so `$.orders[0].id == ${orderId}` is true in a correct build
    and false for any other variable. Passing a different reference turns this
    into a negative test — proving the assertion is not vacuous.
    """
    spec = local_spec()
    # A setup endpoint may not reference another setup endpoint's extraction
    # (validate_spec forbids it), so the token comes in as a spec variable —
    # the mock's is a constant. This test is about the expected value, not
    # about setup chaining.
    spec["variables"]["mockToken"] = "tok-abc123"
    spec["setup"]["endpoints"].append({
        "name": "Seed order",
        "path": "/orders",
        "method": "POST",
        "headers": {"Authorization": "Bearer ${mockToken}"},
        "body": '{"item":"x"}',
        "success": {"status": 201, "body_contains": ["\"orderId\""]},
        "extract": [{"variable": "orderId", "type": "jsonpath",
                     "expression": "$.orderId",
                     "default_value": "ORDER_ID_EXTRACT_FAILED"}],
    })
    spec["endpoints"] = [e for e in spec["endpoints"]
                         if e["name"] == "List orders"]
    spec["endpoints"][0]["success"]["json_path_equals"] = {
        "$.orders[0].id": expected}
    return spec


# --------------------------------------------------------------------------
# 1. generation invariants (no runtime needed)
# --------------------------------------------------------------------------


def test_generation() -> None:
    print("generation invariants")

    spec = local_spec()
    k6 = build_k6(spec)
    lc = build_locust(spec)

    # both must refuse what the JMX builder refuses — shared validate_spec
    broken = copy.deepcopy(spec)
    broken["endpoints"][0]["success"] = {"status": 200}
    for name, fn in (("k6", build_k6), ("locust", build_locust)):
        try:
            fn(broken)
            check(False, f"{name}: status-only spec must be rejected")
        except SpecError:
            check(True, f"{name}: status-only spec is rejected")

    unknown = copy.deepcopy(spec)
    unknown["setup"] = {"requests": unknown["setup"]["endpoints"]}
    for name, fn in (("k6", build_k6), ("locust", build_locust)):
        try:
            fn(unknown)
            check(False, f"{name}: unknown setup key must be rejected")
        except SpecError:
            check(True, f"{name}: unknown setup key is rejected")

    # think time is the script's job (not overridden by DLT)
    check("sleep(" in k6, "k6 emits sleep() think time")
    check("wait_time = between(" in lc, "locust emits wait_time")

    # load ratio: k6 = weighted branch, locust = @task(n)
    check("Math.random() *" in k6, "k6 load ratio is a weighted random branch")
    weights = [e.get("weight", 1) for e in spec["endpoints"]]
    for w in set(weights):
        check(f"@task({w})" in lc, f"locust emits @task({w})")

    # setup runs once per VU
    check("if (vuVars === null)" in k6, "k6 setup is once per VU")
    check("def on_start(self):" in lc, "locust setup is in on_start")

    # extractor defaults survive into both scripts
    default = spec["setup"]["endpoints"][0]["extract"][0]["default_value"]
    check(default in k6 and default in lc,
          "extractor default value present in both scripts")

    # no model-invented identifiers: options structure for DLT compat
    check("export const options" in k6, "k6 exports options")
    check("HttpUser" in lc, "locust defines an HttpUser")

    # SMOKE mode: locust has no --iterations, so coverage has to come from the
    # script. Without this the weighted pick decides which endpoints a smoke
    # run touches, and a low-weight endpoint can go unvalidated.
    check('SMOKE = os.environ.get("SMOKE") == "1"' in lc,
          "locust reads SMOKE from the environment")
    check("def smoke_all(self):" in lc and "runner.quit()" in lc,
          "locust emits a single deterministic SMOKE task that quits")
    for i in range(len(spec["endpoints"])):
        check(f"self.ep{i}()" in lc,
              f"locust SMOKE task and weighted task share ep{i}()")

    # A ${var} expected value must be resolved, not compared as literal text.
    cor = build_locust(correlated_spec())
    cork6 = build_k6(correlated_spec())
    check("self.v['orderId']" in cor and "${orderId}" not in cor.replace(
              "# ", ""),
          "locust resolves a ${var} expected value via self.v")
    check('String(v["orderId"])' in cork6,
          "k6 resolves a ${var} expected value via the vars object")
    # The failure message must not interpolate ${var} into an f-string: that
    # made {orderId} a format field and the task died with NameError.
    check("expected ${" not in cor,
          "locust failure message does not embed ${var} in an f-string")

    # unsupported jsonpath is refused, not mistranslated
    fancy = copy.deepcopy(spec)
    fancy["setup"]["endpoints"][0]["extract"][0]["expression"] = \
        "$.items[?(@.id>3)].token"
    for name, fn in (("k6", build_k6), ("locust", build_locust)):
        try:
            fn(fancy)
            check(False, f"{name}: filter jsonpath must be refused")
        except SpecError:
            check(True, f"{name}: filter jsonpath is refused, not mistranslated")


# --------------------------------------------------------------------------
# 2. execution against the mock target
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


def run_k6(script: Path) -> tuple[int, str]:
    result = subprocess.run(
        ["k6", "run", "--vus", "1", "--iterations", "1", "-e", "SMOKE=1",
         "--quiet", str(script)],
        capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout + result.stderr


def run_locust(script: Path) -> tuple[int, str]:
    # SMOKE=1 mirrors validate_script exactly — see tools/script_tools.py.
    result = subprocess.run(
        [sys.executable, "-m", "locust", "-f", str(script), "--headless",
         "-u", "1", "-r", "1", "-t", "6s", "--stop-timeout", "4",
         "--exit-code-on-error", "1"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "SMOKE": "1"})
    return result.returncode, result.stdout + result.stderr


def test_execution(tmpdir: Path) -> None:
    have_k6 = shutil.which("k6") is not None
    have_locust = subprocess.run(
        [sys.executable, "-c", "import locust"], capture_output=True).returncode == 0

    spec = local_spec()

    # the mock's core trap: wrong password -> HTTP 200 + null token
    broken = copy.deepcopy(spec)
    broken["variables"]["LOGIN_PASSWORD"] = "wrong"

    if have_k6:
        print("k6 execution (real run against mock target)")
        good = tmpdir / "good.k6.js"
        good.write_text(build_k6(spec), encoding="utf-8")
        rc, out = run_k6(good)
        check(rc == 0, f"k6: correct spec passes (rc={rc})\n{out[-500:]}")

        bad = tmpdir / "bad.k6.js"
        bad.write_text(build_k6(broken), encoding="utf-8")
        rc, out = run_k6(bad)
        check(rc != 0, "k6: HTTP-200 login failure fails the run (exit != 0)")
        check("body contains" in out or "==" in out,
              "k6: the failing check is named in the output")

        # Same correlated-assertion pair as locust below: right value passes,
        # wrong value fails. Before the fix both compared against the literal
        # text "${orderId}" and so could never pass.
        corr = tmpdir / "corr.k6.js"
        corr.write_text(build_k6(correlated_spec()), encoding="utf-8")
        rc, out = run_k6(corr)
        check(rc == 0, f"k6: correlated ${{var}} assertion passes (rc={rc})")
        corr_bad = tmpdir / "corr_bad.k6.js"
        corr_bad.write_text(build_k6(correlated_spec("${authToken}")),
                            encoding="utf-8")
        rc, out = run_k6(corr_bad)
        check(rc != 0, "k6: wrong correlated value fails the run")
    else:
        _skips.append("k6 not installed — k6 execution UNVERIFIED")

    if have_locust:
        print("locust execution (real run against mock target)")
        good = tmpdir / "locustfile.py"
        good.write_text(build_locust(spec), encoding="utf-8")
        rc, out = run_locust(good)
        agg = re.search(r"Aggregated\s+(\d+)\s+(\d+)", out)
        fails = int(agg.group(2)) if agg else -1
        check(rc == 0 and fails == 0,
              f"locust: correct spec passes (rc={rc}, fails={fails})")

        # request-count rows look like "SETUP_Login   2   0(0.00%)"; the
        # percentile table has no parenthesised fails column — do not match it
        setup_counts = re.findall(r"SETUP_\S+\s+(\d+)\s+\d+\(", out)
        check(setup_counts and setup_counts[-1] == "1",
              f"locust: on_start setup ran once for 1 user "
              f"(got {setup_counts[-1:] if setup_counts else 'none'})")

        bad = tmpdir / "locustfile_bad.py"
        bad.write_text(build_locust(broken), encoding="utf-8")
        rc, out = run_locust(bad)
        check(rc != 0, f"locust: HTTP-200 login failure fails the run (rc={rc})")
        check("missing required marker" in out or "expected" in out,
              "locust: failure message names the root cause")

        # SMOKE coverage: every measured endpoint exactly once. A weighted run
        # was observed skipping a 1-weight endpoint entirely, which made the
        # smoke pass without validating it.
        cov = tmpdir / "locustfile_cov.py"
        cov.write_text(build_locust(spec), encoding="utf-8")
        rc, out = run_locust(cov)
        req_counts = re.findall(r"REQ_\S+(?:\s\S+)*?\s+(\d+)\s+\d+\(", out)
        check(len(req_counts) == len(spec["endpoints"])
              and all(c == "1" for c in req_counts),
              f"locust SMOKE: every endpoint hit exactly once "
              f"(got {req_counts} for {len(spec['endpoints'])} endpoints)")

        # A correlated ${var} assertion must pass when right and fail when
        # wrong — and never raise NameError, which is how it failed before.
        ok = tmpdir / "locustfile_corr.py"
        ok.write_text(build_locust(correlated_spec()), encoding="utf-8")
        rc, out = run_locust(ok)
        check(rc == 0 and "NameError" not in out,
              f"locust: correlated ${{var}} assertion passes (rc={rc})")
        wrong = tmpdir / "locustfile_corr_bad.py"
        wrong.write_text(build_locust(correlated_spec("${authToken}")),
                         encoding="utf-8")
        rc, out = run_locust(wrong)
        check(rc != 0 and "NameError" not in out,
              f"locust: wrong correlated value fails, not crashes (rc={rc})")
    else:
        _skips.append("locust not installed — locust execution UNVERIFIED")


def main() -> int:
    tmpdir = Path(__file__).resolve().parent / "_tmp"
    tmpdir.mkdir(exist_ok=True)

    test_generation()

    target = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parent / "mock_target.py"),
         str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_for_target(PORT):
            print("mock target failed to start", file=sys.stderr)
            return 2
        test_execution(tmpdir)
    finally:
        target.terminate()
        target.wait(timeout=10)

    print(f"\n{_passes} passed, {len(_failures)} failed")
    for f in _failures:
        print(f"  - {f}")
    for s in _skips:
        print(f"  SKIPPED (unverified, not passed): {s}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
