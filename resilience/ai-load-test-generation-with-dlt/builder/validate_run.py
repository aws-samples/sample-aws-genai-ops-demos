#!/usr/bin/env python3
"""Smoke-run a generated JMX and assert on the JTL.

This is the validation step that matters. `xmllint` only proves the file parses;
every defect worth catching here parsed fine:

  - a sampler with no host           -> ran, failed at connect time
  - a status-code-only assertion     -> passed while the API returned an error
  - an extractor with a bad template -> produced an empty variable, silently
  - a JSONPath expected value        -> compared a string against a number

So we override the plan to 1 thread / 1 loop, point it at the real target, run
it, and check that requests actually succeeded and that every correlation
variable got a real value.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


class ValidationFailure(Exception):
    pass


def to_smoke_plan(jmx_text: str) -> str:
    """Rewrite the plan to 1 thread, 1 iteration, no scheduler, no think time.

    Load shape is irrelevant here; we only want to know whether each request
    works at all. Keeping it to one pass makes the expected sample count exact.
    """
    text = jmx_text
    text = re.sub(
        r'(<stringProp name="ThreadGroup.num_threads">)[^<]*(</stringProp>)',
        r"\g<1>1\g<2>",
        text,
    )
    text = re.sub(
        r'(<stringProp name="ThreadGroup.ramp_time">)[^<]*(</stringProp>)',
        r"\g<1>1\g<2>",
        text,
    )
    text = re.sub(
        r'(<boolProp name="ThreadGroup.scheduler">)[^<]*(</boolProp>)',
        r"\g<1>false\g<2>",
        text,
    )
    text = re.sub(
        r'(<intProp name="LoopController.loops">)-?\d*(</intProp>)',
        r"\g<1>1\g<2>",
        text,
    )
    # Think time only slows the smoke run down.
    text = re.sub(
        r'(<stringProp name="ConstantTimer.delay">)[^<]*(</stringProp>)',
        r"\g<1>0\g<2>",
        text,
    )
    text = re.sub(
        r'(<stringProp name="RandomTimer.range">)[^<]*(</stringProp>)',
        r"\g<1>1\g<2>",
        text,
    )
    return text


def extractor_variables(jmx_text: str) -> list[str]:
    names = re.findall(
        r'<stringProp name="(?:JSONPostProcessor.referenceNames|RegexExtractor.refname)">'
        r"([^<]*)</stringProp>",
        jmx_text,
    )
    return [name for name in names if name]


def default_values(jmx_text: str) -> set[str]:
    values = re.findall(
        r'<stringProp name="(?:JSONPostProcessor.defaultValues|RegexExtractor.default)">'
        r"([^<]*)</stringProp>",
        jmx_text,
    )
    return {value for value in values if value}


def add_debug_writer(jmx_text: str, out_path: Path) -> str:
    """Append a Simple Data Writer capturing response bodies for diagnostics.

    `<xml>true</xml>` is load-bearing: with it false the writer emits CSV, which
    has no column for response data or request headers, so the correlation checks
    below would silently have nothing to inspect. `<subresults>true` is needed
    because each request is nested inside a Transaction Controller and would
    otherwise be omitted.
    """
    writer = f"""      <ResultCollector guiclass="SimpleDataWriter" testclass="ResultCollector" testname="Smoke Detail" enabled="true">
        <boolProp name="ResultCollector.error_logging">false</boolProp>
        <objProp>
          <name>saveConfig</name>
          <value class="SampleSaveConfiguration">
            <xml>true</xml>
            <time>true</time><label>true</label><code>true</code><success>true</success>
            <message>true</message><threadName>true</threadName><assertions>true</assertions>
            <subresults>true</subresults>
            <responseData>true</responseData><samplerData>true</samplerData>
            <requestHeaders>true</requestHeaders><responseHeaders>true</responseHeaders>
            <url>true</url><fieldNames>true</fieldNames>
            <saveAssertionResultsFailureMessage>true</saveAssertionResultsFailureMessage>
          </value>
        </objProp>
        <stringProp name="filename">{out_path}</stringProp>
      </ResultCollector>
      <hashTree/>
"""
    marker = "    </hashTree>\n  </hashTree>\n</jmeterTestPlan>"
    if marker not in jmx_text:
        raise ValidationFailure("cannot locate test plan close tag to inject writer")
    return jmx_text.replace(marker, writer + marker)


def run_jmeter(jmx_path: Path, jtl_path: Path, log_path: Path, jmeter: str) -> None:
    result = subprocess.run(
        [jmeter, "-n", "-t", str(jmx_path), "-l", str(jtl_path), "-j", str(log_path)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        tail = (result.stdout or "")[-2000:] + (result.stderr or "")[-2000:]
        raise ValidationFailure(f"jmeter exited {result.returncode}\n{tail}")


def check_results(
    jtl_path: Path, detail_path: Path, variables: list[str], defaults: set[str]
) -> list[str]:
    problems: list[str] = []

    if not jtl_path.exists() or jtl_path.stat().st_size == 0:
        return ["JTL is empty — no samples were recorded at all"]

    with jtl_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    requests = [row for row in rows if row["label"].startswith("REQ_")]
    if not requests:
        return ["no REQ_ samples in the JTL — the plan issued no HTTP requests"]

    failures = [row for row in requests if row.get("success") != "true"]
    for row in failures:
        problems.append(
            f"{row['label']}: code={row['responseCode']} "
            f"{row.get('failureMessage') or row.get('responseMessage') or ''}".strip()
        )

    counts = Counter(row["label"] for row in requests)
    for label, count in sorted(counts.items()):
        if count == 0:
            problems.append(f"{label}: never executed")

    # A correlation variable that fell back to its default means the extraction
    # failed. The request still "succeeded", so only this check catches it.
    if variables and detail_path.exists():
        detail = detail_path.read_text(encoding="utf-8", errors="replace")
        for default in defaults:
            if default in detail:
                problems.append(
                    f"extractor default value {default!r} appears in a response or "
                    "request — a correlation failed and the placeholder was sent"
                )
        for variable in variables:
            if "${%s}" % variable in detail:
                problems.append(
                    f"unresolved reference ${{{variable}}} was sent literally — "
                    "the variable was never set"
                )

    return problems


def validate(jmx_path: Path, jmeter: str, keep: bool) -> int:
    original = jmx_path.read_text(encoding="utf-8")
    variables = extractor_variables(original)
    defaults = default_values(original)

    workdir = Path(tempfile.mkdtemp(prefix="jmx-smoke-"))
    try:
        # Data files referenced by CSV Data Set live beside the JMX.
        for sibling in jmx_path.parent.glob("*"):
            if sibling.is_file() and sibling.suffix in {".csv", ".txt"}:
                shutil.copy2(sibling, workdir / sibling.name)

        smoke_path = workdir / "smoke.jmx"
        detail_path = workdir / "detail.xml"
        smoke = add_debug_writer(to_smoke_plan(original), detail_path)
        smoke_path.write_text(smoke, encoding="utf-8")

        jtl_path = workdir / "smoke.jtl"
        log_path = workdir / "smoke.log"

        print(f"Smoke run: 1 thread x 1 iteration  ({smoke_path})")
        run_jmeter(smoke_path, jtl_path, log_path, jmeter)

        problems = check_results(jtl_path, detail_path, variables, defaults)

        with jtl_path.open(newline="", encoding="utf-8") as handle:
            rows = [r for r in csv.DictReader(handle) if r["label"].startswith("REQ_")]
        print(f"\n{len(rows)} request sample(s):")
        for row in rows:
            status = "ok  " if row.get("success") == "true" else "FAIL"
            print(f"  [{status}] {row['label']:<30} {row['responseCode']:>4}")

        if problems:
            print("\nVALIDATION FAILED:")
            for problem in problems:
                print(f"  - {problem}")
            print(f"\nDetail: {detail_path}\nLog: {log_path}")
            return 1

        print("\nVALIDATION PASSED")
        print(f"  {len(rows)} requests, all successful")
        if variables:
            print(f"  {len(variables)} correlation variable(s) resolved: "
                  f"{', '.join(variables)}")
        return 0
    finally:
        if keep:
            print(f"\nWorkdir kept: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jmx", type=Path)
    parser.add_argument("--jmeter", default="jmeter", help="jmeter executable")
    parser.add_argument("--keep", action="store_true", help="keep the temp workdir")
    args = parser.parse_args()

    if not shutil.which(args.jmeter):
        print(f"jmeter not found on PATH: {args.jmeter}", file=sys.stderr)
        return 2
    try:
        return validate(args.jmx, args.jmeter, args.keep)
    except ValidationFailure as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
