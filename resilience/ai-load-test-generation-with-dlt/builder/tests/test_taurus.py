#!/usr/bin/env python3
"""Verify that Taurus (and therefore AWS DLT) honours the builder's load ratios.

The builder expresses per-endpoint load share as ThreadGroup.num_threads. That
only works if Taurus redistributes its own `concurrency` across thread groups in
proportion to those numbers. This test proves it rather than trusting the docs.

Taurus does not merely edit num_threads: it replaces each ThreadGroup with a
ConcurrencyThreadGroup and sets TargetLevel. ramp-up and hold-for come from the
YAML, so the scheduler values the builder writes are overridden under DLT. Both
behaviours are asserted here so a Taurus upgrade that changes them gets caught.

Skipped when `bzt` is not installed. Run: python3 tests/test_taurus.py
"""

from __future__ import annotations

import collections
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BUILDER_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
PORT = 18112

TOTAL_CONCURRENCY = 100
EXPECTED_SHARE = {"List orders": 0.833, "Create order": 0.167}
TOLERANCE = 0.03


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


def main() -> int:
    if not shutil.which("bzt"):
        print("SKIP: bzt (Taurus) is not installed — pip install bzt")
        return 0
    if not shutil.which("jmeter"):
        print("SKIP: jmeter is not on PATH")
        return 0

    workdir = Path(tempfile.mkdtemp(prefix="taurus-check-"))
    failures: list[str] = []

    spec = json.loads(
        (BUILDER_DIR / "sample-data" / "orders.spec.json").read_text(encoding="utf-8")
    )
    spec["target"]["port"] = PORT
    # Concurrency here is irrelevant; Taurus overrides it. Keep the 3:1 weights.
    spec["load"] = {
        "concurrency": 6,
        "ramp_up_s": 1,
        "hold_for_s": 10,
        "think_time_ms": 200,
        "think_time_jitter_ms": 50,
    }
    spec_path = workdir / "ratio.spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    jmx_path = workdir / "ratio.jmx"
    build = subprocess.run(
        [sys.executable, str(BUILDER_DIR / "jmx_builder.py"), str(spec_path),
         "-o", str(jmx_path)],
        capture_output=True, text=True,
    )
    if build.returncode != 0:
        print(f"build failed:\n{build.stderr}")
        return 1

    (workdir / "taurus.yml").write_text(
        f"""execution:
  - concurrency: {TOTAL_CONCURRENCY}
    ramp-up: 5s
    hold-for: 15s
    scenario: gen
scenarios:
  gen:
    script: {jmx_path.name}
""",
        encoding="utf-8",
    )

    target = subprocess.Popen(
        [sys.executable, str(TESTS_DIR / "mock_target.py"), str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_target(PORT):
            print("mock target failed to start")
            return 2

        artifacts = workdir / "artifacts"
        run = subprocess.run(
            ["bzt", "taurus.yml", "-o", f"settings.artifacts-dir={artifacts}"],
            cwd=workdir, capture_output=True, text=True, timeout=600,
        )
        if run.returncode != 0:
            print(f"bzt exited {run.returncode}\n{run.stdout[-2000:]}")
            return 1
    finally:
        target.terminate()
        target.wait(timeout=10)

    modified = next(artifacts.glob("modified_*.jmx"), None)
    if modified is None:
        print("Taurus produced no modified JMX")
        return 1
    modified_text = modified.read_text(encoding="utf-8")

    # 1. Taurus swaps in ConcurrencyThreadGroup and sets TargetLevel per group.
    if "ConcurrencyThreadGroup" not in modified_text:
        failures.append(
            "Taurus no longer converts ThreadGroup to ConcurrencyThreadGroup; "
            "the ratio mechanism may have changed"
        )

    target_levels = [
        int(value)
        for value in re.findall(
            r'<stringProp name="TargetLevel">(\d+)</stringProp>', modified_text
        )
    ]
    if sum(target_levels) not in range(TOTAL_CONCURRENCY - 1, TOTAL_CONCURRENCY + 2):
        failures.append(
            f"TargetLevel values {target_levels} do not sum to the requested "
            f"concurrency {TOTAL_CONCURRENCY}"
        )
    else:
        print(f"  TargetLevel distribution: {target_levels} (sum {sum(target_levels)})")

    # 2. The observed request mix matches the configured weights.
    kpi = artifacts / "kpi.jtl"
    with kpi.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    counts = collections.Counter(row["label"] for row in rows)

    measured = {
        label.removeprefix("TX_"): count
        for label, count in counts.items()
        if label.startswith("TX_") and label != "TX_Login"
    }
    total = sum(measured.values())
    if total == 0:
        failures.append("no measured samples in kpi.jtl")
    else:
        for name, expected in EXPECTED_SHARE.items():
            actual = measured.get(name, 0) / total
            status = "ok" if abs(actual - expected) <= TOLERANCE else "FAIL"
            print(f"  [{status}] {name:<16} expected {expected:.1%}  actual {actual:.1%}")
            if status == "FAIL":
                failures.append(
                    f"{name}: expected {expected:.1%} of load, observed {actual:.1%}"
                )

    # 3. Setup ran once per virtual user, not once globally and not every loop.
    logins = counts.get("TX_Login", 0)
    if not TOTAL_CONCURRENCY - 2 <= logins <= TOTAL_CONCURRENCY + 2:
        failures.append(
            f"setup ran {logins} times for {TOTAL_CONCURRENCY} VUs — expected "
            "roughly one login per VU (Once Only Controller per thread group)"
        )
    else:
        print(f"  [ok] setup ran {logins} times for {TOTAL_CONCURRENCY} VUs")

    # 4. Nothing failed. A ratio test that silently 500s proves nothing.
    failed = [row for row in rows if row.get("success") != "true"]
    if failed:
        failures.append(f"{len(failed)} sample(s) failed during the Taurus run")

    shutil.rmtree(workdir, ignore_errors=True)

    if failures:
        print("\nTAURUS CHECK FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nTAURUS CHECK PASSED — load ratios survive Taurus/DLT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
