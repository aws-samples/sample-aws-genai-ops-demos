#!/usr/bin/env python3
"""Deterministic JMX assembler.

The LLM authors a TestSpec JSON (see spec_schema.json) and nothing else. This
module turns that spec into a JMeter test plan by concatenating pre-verified XML
fragments from fragments/. No JMeter property name is ever produced by a model.

Every property name used here was extracted from JMeter 5.6.3 class constants or
confirmed by running a plan and inspecting the JTL. See tests/ for the checks.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

FRAGMENTS = Path(__file__).parent / "fragments"

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
BODYLESS_METHODS = {"GET", "HEAD"}

# A spec-level ${var} reference. JMeter resolves these itself, so they pass
# through into the JMX untouched — but their type is unknown until run time,
# which matters wherever a value is compared rather than substituted.
VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SpecError(Exception):
    """The spec is unusable. Raised instead of emitting a subtly broken JMX."""


# --------------------------------------------------------------------------
# fragment plumbing
# --------------------------------------------------------------------------

_fragment_cache: dict[str, str] = {}


def fragment(name: str) -> str:
    if name not in _fragment_cache:
        path = FRAGMENTS / f"{name}.xml"
        if not path.exists():
            raise SpecError(f"missing fragment: {path}")
        _fragment_cache[name] = path.read_text(encoding="utf-8")
    return _fragment_cache[name]


def render(name: str, **values: Any) -> str:
    """Fill a fragment's {{PLACEHOLDER}} slots. Unfilled slots are an error."""
    out = fragment(name)
    for key, value in values.items():
        out = out.replace("{{%s}}" % key, str(value))
    leftover = re.findall(r"\{\{([A-Z_]+)\}\}", out)
    if leftover:
        raise SpecError(f"fragment {name} has unfilled placeholders: {leftover}")
    return out


def xml_escape(text: str) -> str:
    """Escape for XML text content, matching how JMeter writes values.

    ${var} references survive because none of these characters appear in them.
    """
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def json_literal(expected: str) -> str:
    """Quote a JSONPath expected value the way JSONPathAssertion compares it.

    With ISREGEX off the assertion runs JSONValue.parse() on the expected value
    and compares the result with Objects.equals. So the bare text 0000 parses to
    the number 0 and never equals the string "0000" — the failure message reads
    "expected to be '0000', but found '0000'", which is unreadable. Quoting the
    value makes it parse back to a String.

    Values that are genuinely JSON scalars (numbers, booleans, null) are passed
    through so `"$.count": "5"` still compares against the number 5. The test is
    a strict round-trip: only text that parses AND re-serializes identically is
    treated as a scalar. That keeps zero-padded codes like 0000 or 007 as
    strings, which is what an API returning them means.
    """
    text = str(expected)
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return json.dumps(text)
    if isinstance(parsed, (int, float, bool)) or parsed is None:
        if json.dumps(parsed) == text:
            return text
    return json.dumps(text)


def jmeter_hash(text: str) -> int:
    """Java String.hashCode(). JMeter names collectionProp entries with it.

    The value is cosmetic (the GUI regenerates it) but matching Java keeps
    round-tripped files byte-comparable, which makes diffs meaningful.
    """
    h = 0
    for ch in text:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return h


# --------------------------------------------------------------------------
# validation — refuse rather than emit something that fails silently
# --------------------------------------------------------------------------


def validate_spec(spec: dict) -> list[str]:
    """Return a list of fatal problems. Empty list means the spec is buildable.

    These checks exist because JMeter ignores unknown/missing properties without
    complaint, so a structurally valid file can still measure nothing.
    """
    errors: list[str] = []

    if spec.get("version") != 1:
        errors.append("version must be 1")
    for key in ("name", "target", "load", "endpoints"):
        if key not in spec:
            errors.append(f"missing required key: {key}")
    if errors:
        return errors

    # Unknown keys are rejected, not skipped. Silently ignoring them is the
    # exact JMeter failure mode this builder exists to prevent: a spec with
    # setup.requests instead of setup.endpoints would "pass" and build a plan
    # with no setup at all.
    errors.extend(_reject_unknown_keys(spec))
    if errors:
        return errors

    target = spec["target"]
    host = target.get("host", "")
    if not host:
        errors.append("target.host is required (a sampler with no host cannot run)")
    if "://" in host or "/" in host:
        errors.append(f"target.host must be a bare hostname, got {host!r}")
    if target.get("environment") not in {"dev", "test", "staging", "prod"}:
        errors.append("target.environment must be one of dev/test/staging/prod")

    is_prod = target.get("environment") == "prod"
    allow_prod_writes = bool(target.get("allow_prod_writes"))

    all_endpoints = list(spec["endpoints"])
    setup_endpoints = list(spec.get("setup", {}).get("endpoints", []))

    if not all_endpoints:
        errors.append("endpoints must contain at least one entry")

    seen_names: set[str] = set()
    for endpoint in setup_endpoints + all_endpoints:
        label = endpoint.get("name", "<unnamed>")
        scope = "setup" if endpoint in setup_endpoints else "measured"

        if label in seen_names:
            errors.append(f"duplicate endpoint name {label!r}; names must be unique")
        seen_names.add(label)

        path = endpoint.get("path", "")
        if not path.startswith("/"):
            errors.append(f"{label}: path must start with '/' (got {path!r})")

        method = endpoint.get("method", "")
        body = endpoint.get("body")
        if method in BODYLESS_METHODS and body:
            errors.append(f"{label}: {method} cannot carry a body")

        if is_prod and method in WRITE_METHODS and not allow_prod_writes:
            errors.append(
                f"{label}: {method} against a prod target requires "
                "target.allow_prod_writes to be set by a human"
            )

        errors.extend(_validate_success(endpoint, label, scope))

        for extractor in endpoint.get("extract", []):
            var = extractor.get("variable", "")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var):
                errors.append(f"{label}: invalid extractor variable name {var!r}")
            if extractor.get("default_value") == "":
                errors.append(
                    f"{label}/{var}: default_value must not be empty — an empty "
                    "default makes a failed correlation indistinguishable from success"
                )
            if extractor.get("type") not in {"jsonpath", "regex", "header"}:
                errors.append(f"{label}/{var}: extractor type must be jsonpath/regex/header")
            if extractor.get("type") == "regex" and "(" not in extractor.get("expression", ""):
                errors.append(
                    f"{label}/{var}: regex extractor needs a capturing group; "
                    "the builder always uses template $1$"
                )

    errors.extend(_validate_dangling_variables(spec))

    load = spec["load"]
    if load.get("concurrency", 0) < 1:
        errors.append("load.concurrency must be >= 1")
    if load.get("hold_for_s", 0) < 1:
        errors.append("load.hold_for_s must be >= 1")
    if load.get("concurrency", 0) < len(all_endpoints):
        errors.append(
            f"load.concurrency ({load.get('concurrency')}) is below the endpoint "
            f"count ({len(all_endpoints)}); every endpoint needs at least 1 thread, "
            "so some would get zero load"
        )

    for dataset in spec.get("csv_data", []):
        filename = dataset.get("filename", "")
        if "/" in filename or "\\" in filename:
            errors.append(
                f"csv_data filename {filename!r} must be a bare filename; "
                "absolute paths do not exist inside a DLT container"
            )

    return errors


_KNOWN_KEYS = {
    "spec": {"version", "name", "target", "load", "variables", "csv_data",
             "setup", "endpoints"},
    "target": {"host", "port", "protocol", "environment", "allow_prod_writes",
               "connect_timeout_ms", "response_timeout_ms"},
    "load": {"concurrency", "ramp_up_s", "hold_for_s", "think_time_ms",
             "think_time_jitter_ms"},
    "setup": {"endpoints"},
    "endpoint": {"name", "path", "method", "weight", "headers", "query",
                 "body", "success", "extract"},
    "success": {"status", "body_contains", "body_not_contains",
                "json_path_equals", "body_check_waived", "waiver_reason"},
    "extractor": {"variable", "type", "expression", "default_value",
                  "match_number"},
    "csv": {"filename", "variable_names", "delimiter", "ignore_first_line",
            "recycle", "share_mode"},
}


def _reject_unknown_keys(spec: dict) -> list[str]:
    """A misspelled key means the author believes something is configured that
    is not. Refuse with the allowed alternatives instead of building."""
    errors: list[str] = []

    def check(obj: Any, kind: str, where: str) -> None:
        if not isinstance(obj, dict):
            errors.append(f"{where}: expected an object")
            return
        unknown = set(obj) - _KNOWN_KEYS[kind]
        if unknown:
            errors.append(
                f"{where}: unknown key(s) {sorted(unknown)}; "
                f"allowed: {sorted(_KNOWN_KEYS[kind])}"
            )

    check(spec, "spec", "spec")
    check(spec.get("target", {}), "target", "target")
    check(spec.get("load", {}), "load", "load")
    if "setup" in spec:
        check(spec["setup"], "setup", "setup")
        setup_eps = spec["setup"].get("endpoints") \
            if isinstance(spec["setup"], dict) else None
        if isinstance(spec["setup"], dict) and not isinstance(setup_eps, list):
            errors.append("setup.endpoints must be a list "
                          "(setup is {\"endpoints\": [...]})")

    setup_list = (spec.get("setup") or {}).get("endpoints", []) \
        if isinstance(spec.get("setup"), dict) else []
    all_eps = spec.get("endpoints", [])
    if not isinstance(all_eps, list):
        errors.append("endpoints must be a list")
        all_eps = []
    for endpoint in list(setup_list) + all_eps:
        if not isinstance(endpoint, dict):
            errors.append("each endpoint must be an object")
            continue
        name = endpoint.get("name", "<unnamed>")
        check(endpoint, "endpoint", f"endpoint {name}")
        if isinstance(endpoint.get("success"), dict):
            check(endpoint["success"], "success", f"{name}.success")
        for i, ex in enumerate(endpoint.get("extract") or []):
            if isinstance(ex, dict):
                check(ex, "extractor", f"{name}.extract[{i}]")
    for i, ds in enumerate(spec.get("csv_data") or []):
        if isinstance(ds, dict):
            check(ds, "csv", f"csv_data[{i}]")
    return errors


def _validate_success(endpoint: dict, label: str, scope: str) -> list[str]:
    errors: list[str] = []
    success = endpoint.get("success")
    if not isinstance(success, dict):
        return [f"{label}: success criteria are required"]

    # status may be a single code or a non-empty list of codes (accept any one
    # of a set, e.g. [200, 403] for error-path tests). bool is an int subclass,
    # so exclude it explicitly.
    status = success.get("status")
    statuses = status if isinstance(status, list) else [status]
    if not statuses or not all(
        isinstance(s, int) and not isinstance(s, bool) and 100 <= s <= 599
        for s in statuses
    ):
        errors.append(
            f"{label}: success.status must be an HTTP status code (100–599) "
            "or a non-empty list of them"
        )

    # Body markers must be lists, and this is checked rather than coerced.
    # Every builder iterates this value, so a bare string is not a harmless
    # shorthand: "content" becomes seven one-character assertions (c, o, n, t,
    # e, ...) ANDed together, which almost any JSON or HTML error body
    # satisfies. The file still builds, the smoke run still passes, and the one
    # thing the assertion existed to catch — 200 with an error envelope — sails
    # through. Coercing to [value] would hide the caller's mistake; the same
    # reasoning that makes an extractor default a loud sentinel applies here.
    for key in ("body_contains", "body_not_contains"):
        markers = success.get(key)
        if markers is None:
            continue
        if isinstance(markers, str):
            errors.append(
                f"{label}: success.{key} must be a list, got the bare string "
                f"{markers!r} — it would be iterated per character into "
                f"one-character assertions that nearly any body satisfies. "
                f"Use [{markers!r}]."
            )
            continue
        if not isinstance(markers, list):
            errors.append(
                f"{label}: success.{key} must be a list of strings, got "
                f"{type(markers).__name__}"
            )
            continue
        for marker in markers:
            if not isinstance(marker, str) or not marker:
                errors.append(
                    f"{label}: success.{key} entries must be non-empty "
                    f"strings, got {marker!r}"
                )

    # Not a silent failure (the builders call .items() and would raise), but
    # a validation error beats an opaque traceback for the caller.
    json_path_equals = success.get("json_path_equals")
    if json_path_equals is not None and not isinstance(json_path_equals, dict):
        errors.append(
            f"{label}: success.json_path_equals must be an object mapping "
            f"JSONPath to expected value, got {type(json_path_equals).__name__}"
        )

    has_body_check = bool(
        success.get("body_contains")
        or success.get("body_not_contains")
        or success.get("json_path_equals")
    )
    if not has_body_check and not success.get("body_check_waived"):
        errors.append(
            f"{label}: needs a body check (body_contains / body_not_contains / "
            "json_path_equals). A status-code-only assertion cannot detect an API "
            "that returns HTTP 200 with an error envelope."
        )
    if success.get("body_check_waived") and not success.get("waiver_reason"):
        errors.append(f"{label}: body_check_waived requires waiver_reason")

    if scope == "setup" and not has_body_check:
        errors.append(
            f"{label}: setup requests must assert on the body — a login that "
            "returns 200 with a null token would otherwise poison every iteration"
        )
    return errors


def _validate_dangling_variables(spec: dict) -> list[str]:
    """Catch ${var} references that nothing ever defines.

    JMeter leaves an unresolved reference as the literal string '${var}', so the
    request goes out malformed and the failure looks like an application bug.
    """
    defined: set[str] = set(spec.get("variables", {}))
    for dataset in spec.get("csv_data", []):
        defined.update(dataset.get("variable_names", []))

    errors: list[str] = []
    ordered = list(spec.get("setup", {}).get("endpoints", [])) + list(spec["endpoints"])

    # Setup runs before measured endpoints, so its extractions are available
    # downstream. Within the measured phase each endpoint is its own thread
    # group, so an extraction in one is NOT visible to another.
    setup_defined = set(defined)
    for endpoint in spec.get("setup", {}).get("endpoints", []):
        for extractor in endpoint.get("extract", []):
            setup_defined.add(extractor.get("variable", ""))

    for endpoint in ordered:
        label = endpoint.get("name", "<unnamed>")
        in_setup = endpoint in spec.get("setup", {}).get("endpoints", [])
        visible = set(defined) if in_setup else set(setup_defined)
        for extractor in endpoint.get("extract", []):
            visible.add(extractor.get("variable", ""))

        texts = [endpoint.get("path", ""), endpoint.get("body") or ""]
        texts += list(endpoint.get("headers", {}).values())
        texts += list(endpoint.get("query", {}).values())

        for text in texts:
            for ref in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", str(text)):
                if ref not in visible:
                    errors.append(
                        f"{label}: references ${{{ref}}} which is not defined by "
                        "variables, csv_data, setup extraction, or its own extractors"
                    )
    return errors


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


def distribute_threads(endpoints: list[dict], total: int) -> list[int]:
    """Split total concurrency across endpoints by weight.

    Guarantees every endpoint gets >= 1 thread, then hands the remainder out by
    largest fractional part so the total is exact. A zero-thread endpoint would
    be silently absent from results.
    """
    weights = [float(endpoint.get("weight", 1)) for endpoint in endpoints]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise SpecError("endpoint weights must sum to a positive number")

    raw = [total * weight / weight_sum for weight in weights]
    counts = [max(1, int(value)) for value in raw]

    remainder = total - sum(counts)
    if remainder > 0:
        order = sorted(range(len(raw)), key=lambda i: raw[i] - int(raw[i]), reverse=True)
        for i in range(remainder):
            counts[order[i % len(order)]] += 1
    elif remainder < 0:
        # Over-allocated by the max(1, ...) floor. Shave from the largest first,
        # never below 1.
        order = sorted(range(len(counts)), key=lambda i: counts[i], reverse=True)
        deficit = -remainder
        idx = 0
        while deficit > 0:
            i = order[idx % len(order)]
            if counts[i] > 1:
                counts[i] -= 1
                deficit -= 1
            idx += 1
            if idx > len(counts) * total + len(counts):
                raise SpecError("cannot distribute concurrency without a zero-thread endpoint")

    return counts


def build_headers(headers: dict[str, str]) -> str:
    if not headers:
        return ""
    parts = [fragment("header_manager_open")]
    for name, value in headers.items():
        parts.append(render("header", H_NAME=xml_escape(name), H_VALUE=xml_escape(value)))
    parts.append(fragment("header_manager_close"))
    return "".join(parts)


def build_assertions(success: dict, label: str) -> str:
    parts: list[str] = []
    # status is one code or a set: emit one <stringProp> per code. A single
    # code keeps EQUALS (8); a set adds the OR bit (32) => 40, so the assertion
    # passes if the response code equals ANY one of them. This is scoped to the
    # status ResponseAssertion only — the body assertions below stay ANDed.
    statuses = success["status"]
    if not isinstance(statuses, list):
        statuses = [statuses]
    status_test_strings = "".join(
        render("assertion_test_string",
               S_HASH=jmeter_hash(str(code)), S_VALUE=xml_escape(str(code)))
        for code in statuses
    ).rstrip("\n")
    status_label = ",".join(str(code) for code in statuses)
    status_test_type = 8 if len(statuses) == 1 else 40
    parts.append(
        render("assertion_status",
               TEST_STRINGS=status_test_strings,
               TEST_TYPE=status_test_type,
               STATUS_LABEL=xml_escape(status_label))
    )

    for key, fragment_name in (
        ("body_contains", "assertion_body_contains"),
        ("body_not_contains", "assertion_body_not_contains"),
    ):
        values = success.get(key) or []
        if values:
            strings = "".join(
                render(
                    "assertion_test_string",
                    S_HASH=jmeter_hash(value),
                    S_VALUE=xml_escape(value),
                )
                for value in values
            ).rstrip("\n")
            parts.append(render(fragment_name, TEST_STRINGS=strings))

    for index, (json_path, expected) in enumerate(
        (success.get("json_path_equals") or {}).items()
    ):
        # A ${var} reference has no knowable type at build time: json_literal
        # would quote it, JMeter would resolve it inside the quotes, and "34"
        # (String) never equals the number 34 the API returned. ISREGEX skips
        # JSONValue.parse() and matches the value's string form instead, so the
        # comparison works whatever type the reference turns out to hold.
        # Literals keep the strict, type-aware path.
        if VAR_REF.search(str(expected)):
            expected_value, is_regex = str(expected), "true"
        else:
            expected_value, is_regex = json_literal(expected), "false"
        parts.append(
            render(
                "assertion_jsonpath",
                JP_LABEL=f"{index}",
                JSON_PATH=xml_escape(json_path),
                EXPECTED_VALUE=xml_escape(expected_value),
                ISREGEX=is_regex,
            )
        )
    return "".join(parts)


def build_extractors(extractors: list[dict]) -> str:
    fragment_by_type = {
        "jsonpath": "extractor_jsonpath",
        "regex": "extractor_regex",
        "header": "extractor_header",
    }
    parts: list[str] = []
    for extractor in extractors:
        parts.append(
            render(
                fragment_by_type[extractor["type"]],
                VAR_NAME=extractor["variable"],
                EXPRESSION=xml_escape(extractor["expression"]),
                MATCH_NUMBER=extractor.get("match_number", 1),
                DEFAULT_VALUE=xml_escape(extractor.get("default_value", "EXTRACT_FAILED")),
            )
        )
    return "".join(parts)


def build_sampler(endpoint: dict, think_time: tuple[int, int] | None) -> str:
    method = endpoint["method"]
    body = endpoint.get("body")
    query = endpoint.get("query") or {}

    if body and method not in BODYLESS_METHODS:
        post_body_raw = "true"
        arguments = render("raw_body_argument", BODY=xml_escape(body)).rstrip("\n")
    elif query:
        post_body_raw = "false"
        arguments = "".join(
            render("query_argument", Q_NAME=xml_escape(k), Q_VALUE=xml_escape(v))
            for k, v in query.items()
        ).rstrip("\n")
    else:
        post_body_raw = "false"
        arguments = ""

    parts = [
        render("transaction_controller_open", TX_NAME=endpoint["name"]),
        render(
            "http_sampler_open",
            REQ_NAME=endpoint["name"],
            PATH=xml_escape(endpoint["path"]),
            METHOD=method,
            POST_BODY_RAW=post_body_raw,
            SAMPLER_ARGUMENTS=arguments,
        ),
        build_headers(endpoint.get("headers") or {}),
        build_assertions(endpoint["success"], endpoint["name"]),
        build_extractors(endpoint.get("extract") or []),
        fragment("hashtree_close_sampler"),
    ]
    if think_time is not None:
        delay, jitter = think_time
        parts.append(render("timer_think_time", DELAY=delay, RANGE=max(jitter, 1)))
    parts.append(fragment("hashtree_close_tx"))
    return "".join(parts)


def build(spec: dict) -> str:
    errors = validate_spec(spec)
    if errors:
        raise SpecError(
            "spec rejected:\n" + "\n".join(f"  - {error}" for error in errors)
        )

    target = spec["target"]
    load = spec["load"]
    endpoints = spec["endpoints"]
    setup_endpoints = spec.get("setup", {}).get("endpoints", [])

    think_time = (
        load.get("think_time_ms", 1000),
        load.get("think_time_jitter_ms", 500),
    )

    variables = "".join(
        render("argument", ARG_NAME=xml_escape(name), ARG_VALUE=xml_escape(value))
        for name, value in (spec.get("variables") or {}).items()
    ).rstrip("\n")

    parts = [
        render(
            "test_plan_open",
            TEST_NAME=xml_escape(spec["name"]),
            COMMENTS=xml_escape(
                f"Generated by jmx_builder from TestSpec v1. "
                f"env={target['environment']} "
                f"measured={len(endpoints)} setup={len(setup_endpoints)}"
            ),
            USER_VARIABLES=variables,
        ),
        render(
            "http_defaults",
            HOST=xml_escape(target["host"]),
            PORT=target.get("port", "") or "",
            PROTOCOL=target["protocol"],
            CONNECT_TIMEOUT=target.get("connect_timeout_ms", 5000),
            RESPONSE_TIMEOUT=target.get("response_timeout_ms", 30000),
        ),
    ]

    for dataset in spec.get("csv_data") or []:
        parts.append(
            render(
                "csv_data_set",
                CSV_FILENAME=xml_escape(dataset["filename"]),
                CSV_VARIABLES=xml_escape(",".join(dataset["variable_names"])),
                CSV_DELIMITER=xml_escape(dataset.get("delimiter", ",")),
                CSV_IGNORE_FIRST_LINE=str(dataset.get("ignore_first_line", True)).lower(),
                CSV_RECYCLE=str(dataset.get("recycle", True)).lower(),
                CSV_SHARE_MODE=dataset.get("share_mode", "all"),
            )
        )

    thread_counts = distribute_threads(endpoints, load["concurrency"])

    for endpoint, num_threads in zip(endpoints, thread_counts):
        parts.append(
            render(
                "thread_group_open",
                TG_NAME=xml_escape(endpoint["name"]),
                NUM_THREADS=num_threads,
                RAMP_UP=load["ramp_up_s"],
                DURATION=load["ramp_up_s"] + load["hold_for_s"],
            )
        )
        # Setup runs once per VU at the top of every thread group, so each
        # endpoint's threads authenticate themselves rather than sharing one
        # global token.
        if setup_endpoints:
            parts.append(render("once_only_open", OOC_NAME="Setup (once per VU)"))
            for setup_endpoint in setup_endpoints:
                parts.append(build_sampler(setup_endpoint, think_time=None))
            parts.append(fragment("hashtree_close_ooc"))

        parts.append(build_sampler(endpoint, think_time=think_time))
        parts.append(fragment("thread_group_close"))

    parts.append(fragment("test_plan_close"))
    return "".join(parts)


def summarize(spec: dict) -> str:
    endpoints = spec["endpoints"]
    counts = distribute_threads(endpoints, spec["load"]["concurrency"])
    lines = [
        f"Test plan : {spec['name']}",
        f"Target    : {spec['target']['protocol']}://{spec['target']['host']}"
        f" ({spec['target']['environment']})",
        f"Load      : {spec['load']['concurrency']} threads, "
        f"ramp {spec['load']['ramp_up_s']}s, hold {spec['load']['hold_for_s']}s",
        f"Setup     : {len(spec.get('setup', {}).get('endpoints', []))} request(s), "
        "once per VU, excluded from measurement",
        "Measured  :",
    ]
    total = sum(counts)
    for endpoint, num_threads in zip(endpoints, counts):
        share = 100.0 * num_threads / total
        unit = "thread" if num_threads == 1 else "threads"
        lines.append(
            f"  {endpoint['method']:6} {endpoint['path']:<40} "
            f"{num_threads:>5} {unit:<7} ({share:5.1f}%)"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a JMX from a TestSpec JSON.")
    parser.add_argument("spec", type=Path, help="TestSpec JSON file")
    parser.add_argument("-o", "--output", type=Path, help="output .jmx path")
    parser.add_argument(
        "--check", action="store_true", help="validate only, do not write output"
    )
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))

    if args.check:
        errors = validate_spec(spec)
        if errors:
            print("SPEC INVALID:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        print("SPEC VALID")
        print(summarize(spec))
        return 0

    try:
        xml = build(spec)
    except SpecError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output = args.output or args.spec.with_suffix(".jmx")
    output.write_text(xml, encoding="utf-8")
    print(summarize(spec))
    print(f"\nWrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
