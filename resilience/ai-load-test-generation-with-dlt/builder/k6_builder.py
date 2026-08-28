#!/usr/bin/env python3
"""Deterministic k6 script builder. Same TestSpec, same refusal rules as
jmx_builder — only the output language differs.

Load-shape facts this generator is built around (see memory/requirements):
  - DLT/Taurus overrides k6 `options.vus` / `duration` / `stages`, so
    per-scenario VU allocation cannot be trusted. Load ratio therefore lives
    in a WEIGHTED RANDOM BRANCH inside the single default function.
  - think time (sleep()) is NOT overridden — it stays in the script.
  - setup/correlation runs once per VU via module-level per-VU state
    (each k6 VU gets its own JS VM, so a module `let` is per-VU).

SMOKE mode: `k6 run -e SMOKE=1` visits every endpoint deterministically once
per iteration instead of the weighted pick — that is what validation uses;
DLT never sets it.
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
# function. Each runtime needs its own translation; k6 reads __ENV.
_JMETER_PROP = re.compile(r"\$\{__P\(([A-Za-z0-9_.]+),([^)]*)\)\}")


def _js_str(text: str) -> str:
    return json.dumps(str(text))


def _translate_props(value: str) -> str:
    """${__P(name,default)} -> a JS expression reading __ENV.name."""
    text = str(value)
    m = _JMETER_PROP.fullmatch(text)
    if m:
        return f"(__ENV[{_js_str(m.group(1))}] || {_js_str(m.group(2))})"
    if _JMETER_PROP.search(text):
        raise SpecError(
            f"${{__P(...)}} must be the whole value, got {text!r}")
    return _js_str(text)


def _resolve_expr(text: str) -> str:
    """spec ${var} template -> JS expression using the per-VU vars object."""
    parts = []
    last = 0
    for m in _VAR_REF.finditer(str(text)):
        if m.start() > last:
            parts.append(_js_str(text[last:m.start()]))
        parts.append(f'v[{_js_str(m.group(1))}]')
        last = m.end()
    if last < len(str(text)):
        parts.append(_js_str(str(text)[last:]))
    return " + ".join(parts) if parts else '""'


def _jsonpath_to_gjson(expr: str) -> str:
    """$.content[0].id -> content.0.id — simple paths only; anything fancier
    is refused rather than mistranslated."""
    if not expr.startswith("$."):
        raise SpecError(f"k6 builder supports simple JSONPath only, got {expr!r}")
    out = expr[2:]
    out = re.sub(r"\[(\d+)\]", r".\1", out)
    if re.search(r"[\[\]()?*@]", out):
        raise SpecError(
            f"JSONPath {expr!r} uses filters/wildcards — not supported in the "
            "k6 translation; simplify the expression or use JMeter"
        )
    return out


def _request_fn(endpoint: dict, fn_name: str, is_setup: bool) -> str:
    """One function per endpoint: request + checks + extractions."""
    method = endpoint["method"].upper()
    path_expr = _resolve_expr(endpoint["path"])
    query = endpoint.get("query") or {}
    if query:
        q_parts = " + \"?\" + " + ' + "&" + '.join(
            f'{_js_str(k)} + "=" + encodeURIComponent({_resolve_expr(str(val))})'
            for k, val in query.items()
        )
    else:
        q_parts = ""
    headers = endpoint.get("headers") or {}
    header_lines = ", ".join(
        f"{_js_str(k)}: {_resolve_expr(str(val))}" for k, val in headers.items()
    )
    body = endpoint.get("body")
    body_expr = _resolve_expr(body) if body else "null"

    success = endpoint.get("success", {})
    status_label = _js_str("status is %s" % success.get("status"))
    checks = [f'{status_label}: (r) => r.status === {success.get("status")}']
    for marker in success.get("body_contains") or []:
        checks.append(f'{_js_str("body contains " + marker)}: '
                      f'(r) => r.body.includes({_js_str(marker)})')
    for marker in success.get("body_not_contains") or []:
        checks.append(f'{_js_str("body lacks " + marker)}: '
                      f'(r) => !r.body.includes({_js_str(marker)})')
    for jp, expected in (success.get("json_path_equals") or {}).items():
        jp_label = _js_str("%s == %s" % (jp, expected))
        # The expected value goes through _resolve_expr like every other value
        # in the spec, so "${concertId}" compares against the correlated value
        # rather than against the literal text "${concertId}" (which could
        # never match). Both sides are String()-wrapped because a JSON number
        # and the extracted string form must still compare equal.
        checks.append(
            f'{jp_label}: '
            f'(r) => String(r.json({_js_str(_jsonpath_to_gjson(jp))})) === '
            f'String({_resolve_expr(str(expected))})'
        )

    extract_lines = []
    for ex in endpoint.get("extract") or []:
        if ex["type"] == "jsonpath":
            getter = f"r.json({_js_str(_jsonpath_to_gjson(ex['expression']))})"
        elif ex["type"] == "regex":
            getter = (f"(r.body.match(new RegExp({_js_str(ex['expression'])})) "
                      f"|| [])[1]")
        elif ex["type"] == "header":
            getter = f"r.headers[{_js_str(ex['expression'])}]"
        else:
            raise SpecError(f"unsupported extractor type {ex['type']!r}")
        extract_lines.append(
            f"  {{ const x = {getter}; "
            f"v[{_js_str(ex['variable'])}] = (x === undefined || x === null || "
            f"x === \"\") ? {_js_str(ex['default_value'])} : String(x); }}"
        )

    tag = "SETUP_" if is_setup else "REQ_"
    lines = [
        f"function {fn_name}(v) {{",
        f"  const url = BASE + {path_expr}{q_parts};",
        f"  const params = {{ headers: {{ {header_lines} }}, "
        f"tags: {{ name: {_js_str(tag + endpoint['name'])} }} }};",
    ]
    if method in ("GET", "HEAD"):
        lines.append(f"  const r = http.{method.lower()}(url, params);")
    else:
        lines.append(f"  const r = http.request({_js_str(method)}, url, "
                     f"{body_expr}, params);")
    lines.append("  check(r, {")
    lines.append("    " + ",\n    ".join(checks))
    lines.append("  });")
    lines.extend(extract_lines)
    lines.append("  return r;")
    lines.append("}")
    return "\n".join(lines)


def build_k6(spec: dict) -> str:
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

    parts = [
        "// Generated by k6_builder from TestSpec v1 — do not edit by hand;",
        "// fix the spec and rebuild.",
        f"// env={target['environment']} measured={len(endpoints)} "
        f"setup={len(setup_endpoints)}",
        "import http from 'k6/http';",
        "import { check, sleep } from 'k6';",
        "",
        "// DLT/Taurus overrides vus/duration; these values only matter when",
        "// the script is run standalone. Checks-threshold makes a failed",
        "// assertion fail the run's exit code (it does not abort the test).",
        "export const options = {",
        f"  vus: {load['concurrency']},",
        f"  duration: '{load['ramp_up_s'] + load['hold_for_s']}s',",
        "  thresholds: { checks: ['rate==1.0'] },",
        "};",
        "",
        f"const BASE = {_js_str(base)};",
        "const SMOKE = (__ENV.SMOKE === '1');",
        "",
        "// per-VU state: each VU runs this module in its own JS VM",
        "let vuVars = null;",
        "",
    ]

    fn_names = []
    for i, endpoint in enumerate(setup_endpoints):
        fn = f"setup{i}"
        parts.append(_request_fn(endpoint, fn, is_setup=True))
        parts.append("")
    for i, endpoint in enumerate(endpoints):
        fn = f"endpoint{i}"
        fn_names.append(fn)
        parts.append(_request_fn(endpoint, fn, is_setup=False))
        parts.append("")

    variables_init = ", ".join(
        f"{_js_str(k)}: {_translate_props(val)}"
        for k, val in (spec.get("variables") or {}).items())
    parts.append("function initVU() {")
    parts.append(f"  const v = {{ {variables_init} }};")
    for i in range(len(setup_endpoints)):
        parts.append(f"  setup{i}(v);")
    parts.append("  return v;")
    parts.append("}")
    parts.append("")

    weights = [e.get("weight", 1) for e in endpoints]
    total = sum(weights)
    parts.append("export default function () {")
    parts.append("  if (vuVars === null) { vuVars = initVU(); }  // once per VU")
    parts.append("  const v = vuVars;")
    parts.append("  if (SMOKE) {")
    for fn in fn_names:
        parts.append(f"    {fn}(v);")
    parts.append("  } else {")
    parts.append(f"    const pick = Math.random() * {total};")
    acc = 0
    for fn, w in zip(fn_names, weights):
        acc += w
        cond = "if" if acc == weights[0] else "else if"
        if fn == fn_names[-1]:
            parts.append(f"    else {{ {fn}(v); }}"
                         if len(fn_names) > 1 else f"    {fn}(v);")
        else:
            parts.append(f"    {cond} (pick < {acc}) {{ {fn}(v); }}")
    parts.append("  }")
    parts.append(f"  sleep({think_s} + Math.random() * {jitter_s});")
    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a k6 script from a TestSpec.")
    parser.add_argument("spec", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    try:
        script = build_k6(spec)
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
