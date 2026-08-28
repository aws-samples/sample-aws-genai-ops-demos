#!/usr/bin/env python3
"""Deterministic Locust script builder. Same TestSpec, same refusal rules.

Load-shape facts this generator is built around:
  - DLT overrides user count / spawn rate; @task(n) weights are native and
    NOT overridden — the natural home for the load ratio.
  - wait_time (think time) is not overridden.
  - on_start runs once per simulated user — the setup/correlation home,
    mirroring the JMX Once Only Controller.
  - DLT zip entry point must be named locustfile.py.

Assertions use catch_response: an HTTP 200 with a missing body marker is
reported as a failure, same policy as the JMX ResponseAssertions.

SMOKE mode: with SMOKE=1 the weighted tasks are replaced by a single task that
visits every endpoint once and quits — that is what validation uses, and it is
the only way to guarantee coverage, since Locust has no --iterations flag and a
weighted pick can leave an endpoint unvisited. DLT never sets it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from jmx_builder import SpecError, validate_spec

_VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# The spec convention for secrets is JMeter's ${__P(name,default)} property
# function. Locust reads os.environ.
_JMETER_PROP = re.compile(r"\$\{__P\(([A-Za-z0-9_.]+),([^)]*)\)\}")


def _py_str(text: str) -> str:
    return json.dumps(str(text))


def _translate_props(value: str) -> str:
    """${__P(name,default)} -> os.environ.get('name', 'default')."""
    text = str(value)
    m = _JMETER_PROP.fullmatch(text)
    if m:
        return (f"os.environ.get({_py_str(m.group(1))}, "
                f"{_py_str(m.group(2))})")
    if _JMETER_PROP.search(text):
        raise SpecError(
            f"${{__P(...)}} must be the whole value, got {text!r}")
    return _py_str(text)


def _resolve_fstring(text: str) -> str:
    """spec ${var} -> python f-string using self.v dict."""
    text = str(text)
    if not _VAR_REF.search(text):
        return _py_str(text)
    escaped = text.replace("{", "{{").replace("}", "}}")
    # our refs got double-escaped; restore them as format fields
    escaped = re.sub(r"\$\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}",
                     r"{self.v['\1']}", escaped)
    return 'f' + _py_str(escaped).replace("\\'", "'")


def _jsonpath_to_py(expr: str, source: str) -> str:
    """$.content[0].id -> source["content"][0]["id"] — simple paths only."""
    if not expr.startswith("$."):
        raise SpecError(f"locust builder supports simple JSONPath only, got {expr!r}")
    out = source
    for part in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", expr[2:]):
        key, idx = part
        out += f"[{_py_str(key)}]" if key else f"[{int(idx)}]"
    if re.search(r"[()?*@]", expr):
        raise SpecError(
            f"JSONPath {expr!r} uses filters/wildcards — not supported in the "
            "locust translation; simplify or use JMeter")
    return out


def _request_method(endpoint: dict, method_name: str, is_setup: bool) -> list[str]:
    method = endpoint["method"].upper()
    success = endpoint.get("success", {})
    status = success.get("status")
    name_tag = ("SETUP_" if is_setup else "REQ_") + endpoint["name"]

    query = endpoint.get("query") or {}
    headers = endpoint.get("headers") or {}
    body = endpoint.get("body")

    lines = [f"    def {method_name}(self):"]
    args = [f"{_resolve_fstring(endpoint['path'])}",
            f"name={_py_str(name_tag)}",
            "catch_response=True"]
    if query:
        params = ", ".join(f"{_py_str(k)}: {_resolve_fstring(str(val))}"
                           for k, val in query.items())
        args.append(f"params={{{params}}}")
    if headers:
        hdrs = ", ".join(f"{_py_str(k)}: {_resolve_fstring(str(val))}"
                         for k, val in headers.items())
        args.append(f"headers={{{hdrs}}}")
    if body:
        args.append(f"data={_resolve_fstring(body)}")

    lines.append(f"        with self.client.{method.lower()}("
                 f"{', '.join(args)}) as r:")
    lines.append(f"            if r.status_code != {status}:")
    lines.append(f"                r.failure(f\"expected {status}, "
                 f"got {{r.status_code}}\")")
    lines.append("                return")
    for marker in success.get("body_contains") or []:
        lines.append(f"            if {_py_str(marker)} not in r.text:")
        lines.append(f"                r.failure({_py_str('body missing required marker ' + marker)})")
        lines.append("                return")
    for marker in success.get("body_not_contains") or []:
        lines.append(f"            if {_py_str(marker)} in r.text:")
        lines.append(f"                r.failure({_py_str('body contains forbidden marker ' + marker)})")
        lines.append("                return")
    for jp, expected in (success.get("json_path_equals") or {}).items():
        access = _jsonpath_to_py(jp, "r.json()")
        lines.append("            try:")
        lines.append(f"                actual = {access}")
        lines.append("            except (KeyError, IndexError, TypeError):")
        lines.append(f"                r.failure({_py_str('missing ' + jp)}); return")
        # The expected value goes through _resolve_fstring like every other
        # value in the spec, so "${concertId}" compares against self.v rather
        # than against the literal text. It also has to be assigned to a name
        # before use: interpolating it into the failure message directly made
        # ${concertId} a format field, and the generated task died with
        # NameError instead of reporting a failure.
        lines.append(f"            expected = {_resolve_fstring(str(expected))}")
        lines.append("            if str(actual) != str(expected):")
        lines.append(f"                r.failure({_py_str(jp + ' == ')} + repr(actual)"
                     " + \", expected \" + repr(expected)); return")

    for ex in endpoint.get("extract") or []:
        var = ex["variable"]
        default = ex["default_value"]
        if ex["type"] == "jsonpath":
            access = _jsonpath_to_py(ex["expression"], "r.json()")
            lines.append("            try:")
            lines.append(f"                self.v[{_py_str(var)}] = "
                         f"str({access})")
            lines.append("            except (KeyError, IndexError, TypeError,"
                         " ValueError):")
            lines.append(f"                self.v[{_py_str(var)}] = "
                         f"{_py_str(default)}")
        elif ex["type"] == "regex":
            lines.append(f"            m = re.search({_py_str(ex['expression'])}"
                         ", r.text)")
            lines.append(f"            self.v[{_py_str(var)}] = "
                         f"m.group(1) if m else {_py_str(default)}")
        elif ex["type"] == "header":
            lines.append(f"            self.v[{_py_str(var)}] = "
                         f"r.headers.get({_py_str(ex['expression'])}, "
                         f"{_py_str(default)})")
        else:
            raise SpecError(f"unsupported extractor type {ex['type']!r}")
    lines.append("            r.success()")
    return lines


def build_locust(spec: dict) -> str:
    errors = validate_spec(spec)
    if errors:
        raise SpecError(
            "spec rejected:\n" + "\n".join(f"  - {e}" for e in errors))

    target = spec["target"]
    load = spec["load"]
    endpoints = spec["endpoints"]
    setup_endpoints = (spec.get("setup") or {}).get("endpoints", [])
    think_s = load.get("think_time_ms", 1000) / 1000
    jitter_s = load.get("think_time_jitter_ms", 500) / 1000

    base = f"{target['protocol']}://{target['host']}"
    if target.get("port"):
        base += f":{target['port']}"

    variables = spec.get("variables") or {}
    var_init = ", ".join(f"{_py_str(k)}: {_translate_props(val)}"
                         for k, val in variables.items())

    parts = [
        "# Generated by locust_builder from TestSpec v1 — do not edit by",
        "# hand; fix the spec and rebuild. DLT zip entry point must be",
        "# named locustfile.py.",
        f"# env={target['environment']} measured={len(endpoints)} "
        f"setup={len(setup_endpoints)}",
        "import os",
        "import re",
        "",
        "from locust import HttpUser, task, between",
        "",
        "# SMOKE=1 replaces the weighted mix with one deterministic pass over",
        "# every endpoint — that is what validation uses. DLT never sets it.",
        "SMOKE = os.environ.get(\"SMOKE\") == \"1\"",
        "",
        "",
        "class GeneratedUser(HttpUser):",
        f"    host = {_py_str(base)}",
        "    # think time — NOT overridden by DLT, unlike user count",
        f"    wait_time = between({think_s}, {round(think_s + jitter_s, 3)})",
        "",
        "    def on_start(self):",
        "        # once per simulated user — the correlation home, mirroring",
        "        # the JMX Once Only Controller (verified: N VUs -> N setups)",
        f"        self.v = {{{var_init}}}",
    ]
    if setup_endpoints:
        for i in range(len(setup_endpoints)):
            parts.append(f"        self.setup{i}()")
    else:
        parts.append("        pass")
    parts.append("")

    for i, endpoint in enumerate(setup_endpoints):
        parts.extend(_request_method(endpoint, f"setup{i}", is_setup=True))
        parts.append("")

    # Endpoint bodies are plain methods so both the weighted tasks and the
    # SMOKE task below can call them; only the thin wrappers differ.
    for i, endpoint in enumerate(endpoints):
        parts.extend(_request_method(endpoint, f"ep{i}", is_setup=False))
        parts.append("")

    parts.extend([
        "    if SMOKE:",
        "        # Validation needs every endpoint exercised. Weighted random",
        "        # cannot promise that in a few seconds — a 3:1 mix was observed",
        "        # leaving the 1-weight endpoint unvisited, so the smoke passed",
        "        # without ever checking it. One deterministic pass, then stop.",
        "        @task",
        "        def smoke_all(self):",
    ])
    for i in range(len(endpoints)):
        parts.append(f"            self.ep{i}()")
    parts.extend([
        "            self.environment.runner.quit()",
        "",
        "    else:",
    ])
    for i, endpoint in enumerate(endpoints):
        weight = endpoint.get("weight", 1)
        parts.append(f"        @task({weight})  # load ratio — native, not overridden")
        parts.append(f"        def task{i}(self):")
        parts.append(f"            self.ep{i}()")
        parts.append("")

    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a locustfile from a TestSpec.")
    parser.add_argument("spec", type=Path)
    parser.add_argument("-o", "--output", type=Path,
                        help="output path (DLT zip requires locustfile.py)")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    try:
        script = build_locust(spec)
    except SpecError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.output:
        args.output.write_text(script, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(script)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    sys.exit(main())
