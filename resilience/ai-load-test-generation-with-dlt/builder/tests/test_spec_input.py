#!/usr/bin/env python3
"""Tests for spec_input.py (T1 parse_spec_input + T1.5 select_targets).

The fixtures encode the failure modes that matter:
  - a swagger that declares only 200s (the real concert spec lies this way)
  - a HAR with live credentials that must be masked, never echoed
  - path templating that must not merge ambiguous segments silently
  - selection that must fail loudly when nothing is measurable

Run: python3 tests/test_spec_input.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

BUILDER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BUILDER_DIR))

from spec_input import (  # noqa: E402
    SpecInputError, detect_format, parse_spec_input, select_targets,
    _template_path,
)

SAMPLE_SWAGGER = BUILDER_DIR.parent / "sample-data" / "swagger-unified.json"

_failures: list[str] = []
_passes = 0


def check(condition: bool, label: str) -> None:
    global _passes
    if condition:
        _passes += 1
    else:
        _failures.append(label)
        print(f"  FAIL {label}")


def raises(fn, needle: str, label: str) -> None:
    try:
        fn()
    except SpecInputError as exc:
        if needle.lower() in str(exc).lower():
            global _passes
            _passes += 1
            return
        print(f"  FAIL {label}\n        error was: {exc}")
    else:
        print(f"  FAIL {label} (no error raised)")
    _failures.append(label)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

HAR_FIXTURE = {
    "log": {
        "version": "1.2",
        "creator": {"name": "browser"},
        "entries": [
            # 3 list calls, 1 detail call -> traffic 0.75 / 0.25
            *[{
                "request": {"method": "GET",
                            "url": "https://api.example.com/orders?page=0",
                            "queryString": [{"name": "page", "value": "0"}],
                            "headers": [{"name": "Authorization",
                                         "value": "Bearer sk-LIVE-SECRET"}]},
                "response": {"status": 200,
                             "content": {"mimeType": "application/json"}},
            } for _ in range(3)],
            {
                "request": {"method": "GET",
                            "url": "https://api.example.com/orders/12345",
                            "queryString": [],
                            "headers": [{"name": "Authorization",
                                         "value": "Bearer sk-LIVE-SECRET"}]},
                "response": {"status": 200,
                             "content": {"mimeType": "application/json"}},
            },
            {   # static asset -> excluded
                "request": {"method": "GET",
                            "url": "https://cdn.example.com/app.js",
                            "queryString": [], "headers": []},
                "response": {"status": 200,
                             "content": {"mimeType": "application/javascript"}},
            },
            {   # ambiguous year segment -> kept literal, warned
                "request": {"method": "GET",
                            "url": "https://api.example.com/reports/2024",
                            "queryString": [], "headers": []},
                "response": {"status": 200,
                             "content": {"mimeType": "application/json"}},
            },
        ],
    }
}

# The same recording, captured *with* response content. A HAR carries what the
# server actually returned, which is stronger evidence than a declared schema —
# the swagger above declares 200 for all 16 operations. So the fields to assert
# on are read off the recording instead of asked for.
HAR_BODY_FIXTURE = {
    "log": {
        "version": "1.2",
        "creator": {"name": "browser"},
        "entries": [
            {   # object body -> its top-level keys
                "request": {"method": "GET",
                            "url": "https://api.example.com/items",
                            "queryString": [], "headers": []},
                "response": {"status": 200, "content": {
                    "mimeType": "application/json",
                    "text": json.dumps({"content": [], "totalPages": 1,
                                        "size": 20})}},
            },
            {   # a 500 on the same endpoint — its envelope is exactly the wrong
                # thing to assert on, so it must not contribute fields
                "request": {"method": "GET",
                            "url": "https://api.example.com/items",
                            "queryString": [], "headers": []},
                "response": {"status": 500, "content": {
                    "mimeType": "application/json",
                    "text": json.dumps({"error": "boom", "trace": "..."})}},
            },
            {   # array body -> first element's keys, matching _schema_fields
                "request": {"method": "GET",
                            "url": "https://api.example.com/tags",
                            "queryString": [], "headers": []},
                "response": {"status": 200, "content": {
                    "mimeType": "application/json",
                    "text": json.dumps([{"id": 1, "label": "a"}])}},
            },
            {   # base64-encoded body, as browsers write for some responses
                "request": {"method": "GET",
                            "url": "https://api.example.com/me",
                            "queryString": [], "headers": []},
                "response": {"status": 200, "content": {
                    "mimeType": "application/json",
                    "encoding": "base64",
                    "text": base64.b64encode(
                        json.dumps({"email": "x@example.com",
                                    "name": "x"}).encode()).decode()}},
            },
        ],
    }
}

# Postman input is refused, not parsed. A collection states its success bodies
# only in saved example responses, and every spec this builder emits requires a
# body check — so a partial parse would force the model to invent one.
POSTMAN_FIXTURE = {
    "info": {"name": "orders", "_postman_id": "x",
             "schema": "https://schema.getpostman.com/json/collection/v2.1.0/"},
    "item": [
        {"name": "list", "request": {
            "method": "GET",
            "url": {"raw": "https://api.example.com/orders",
                    "host": ["api", "example", "com"], "path": ["orders"]},
            "header": []}},
    ],
}


# --------------------------------------------------------------------------
# 1. format detection & parsing
# --------------------------------------------------------------------------


def test_parsing(tmpdir: Path) -> None:
    print("T1 parse_spec_input")

    check(detect_format(HAR_FIXTURE) == "har", "detects HAR")
    check(detect_format(POSTMAN_FIXTURE) == "postman",
          "recognises Postman by name, so it can be refused by name")
    raises(lambda: detect_format({"random": 1}), "cannot determine",
           "refuses to guess an unknown format")

    # --- real swagger ---
    inv = parse_spec_input(str(SAMPLE_SWAGGER))
    check(inv["source"]["format"] == "openapi", "swagger parsed as openapi")
    check(len(inv["endpoints"]) == 16, f"16 endpoints (got {len(inv['endpoints'])})")
    sse = [e for e in inv["endpoints"] if "events" in e["path"]]
    check(sse and "text/event-stream" in sse[0]["response_content_types"],
          "SSE content type survives normalization")
    check(any("only success responses" in w for w in inv["warnings"]),
          "warns that the spec declares only success shapes")
    check(any("securitySchemes" in w for w in inv["warnings"]),
          "warns about undefined securitySchemes")
    me = [e for e in inv["endpoints"]
          if e["path"] == "/api/users/me" and e["method"] == "GET"][0]
    check(me["auth_hint"] == "required header(s): X-User-Id",
          "auth requirement is recorded, not inferred away")
    check(all(e["call_count"] is None for e in inv["endpoints"]),
          "openapi has no call counts — none are invented")

    # --- HAR ---
    har_path = tmpdir / "fixture.har"
    har_path.write_text(json.dumps(HAR_FIXTURE), encoding="utf-8")
    inv = parse_spec_input(str(har_path))
    check("sk-LIVE-SECRET" not in json.dumps(inv),
          "credential values never appear in output")
    orders = [e for e in inv["endpoints"] if e["path"] == "/orders"]
    check(orders and orders[0]["call_count"] == 3,
          "repeated calls merge with a count")
    check(orders and abs(orders[0]["traffic_share"] - 0.6) < 0.01,
          f"traffic share is computed from kept entries "
          f"(got {orders and orders[0]['traffic_share']})")
    detail = [e for e in inv["endpoints"] if "{orderId}" in e["path"]]
    check(bool(detail), "numeric id segment is templated to {orderId}")
    check(any(x["reason"] == "static resource" for x in inv["excluded"]),
          "static assets are excluded with a reason")
    year = [e for e in inv["endpoints"] if e["path"] == "/reports/2024"]
    check(bool(year), "ambiguous year segment kept literal")
    check(any("could be an ID or a year" in w for w in inv["warnings"]),
          "ambiguous segment is warned about, not silently merged")
    check(all(e["response_fields"] == [] for e in inv["endpoints"]),
          "a recording with no response bodies yields no response fields")
    check(any("no response body" in w for w in inv["warnings"]),
          "...and says why, so empty never reads as 'this endpoint has no fields'")

    # --- HAR with recorded bodies ---
    body_path = tmpdir / "bodies.har"
    body_path.write_text(json.dumps(HAR_BODY_FIXTURE), encoding="utf-8")
    inv = parse_spec_input(str(body_path))
    by_path = {e["path"]: e for e in inv["endpoints"]}
    check(by_path["/items"]["response_fields"] == ["content", "size", "totalPages"],
          f"object body -> sorted top-level keys "
          f"(got {by_path['/items']['response_fields']})")
    check("error" not in by_path["/items"]["response_fields"],
          "a 500 envelope contributes no assertion fields")
    check(by_path["/tags"]["response_fields"] == ["id", "label"],
          "array body -> first element's keys, same shape as the openapi path")
    check(by_path["/me"]["response_fields"] == ["email", "name"],
          "base64-encoded response body is decoded, not skipped")
    check(not any("no response body" in w for w in inv["warnings"]),
          "no bodyless warning when the bodies are there")

    # --- Postman is refused, and the message says what to use instead ---
    pm_path = tmpdir / "fixture.postman.json"
    pm_path.write_text(json.dumps(POSTMAN_FIXTURE), encoding="utf-8")
    raises(lambda: parse_spec_input(str(pm_path)), "not supported yet",
           "a Postman collection is refused rather than partly parsed")
    raises(lambda: parse_spec_input(str(pm_path)), "HAR",
           "the refusal names the formats to export instead")

    # --- errors ---
    raises(lambda: parse_spec_input("/nonexistent.json"), "not found",
           "missing file fails with the path")
    bad = tmpdir / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    raises(lambda: parse_spec_input(str(bad)), "line", "parse failure reports position")

    empty_har = tmpdir / "empty.har"
    empty_har.write_text(json.dumps({"log": {"version": "1.2", "entries": []}}),
                         encoding="utf-8")
    inv = parse_spec_input(str(empty_har))
    check(inv["endpoints"] == [] and any("0 entries" in w for w in inv["warnings"]),
          "empty HAR reports emptiness instead of dressing it up")


# --------------------------------------------------------------------------
# 2. selection
# --------------------------------------------------------------------------


def test_selection(tmpdir: Path) -> None:
    print("T1.5 select_targets")

    inv = parse_spec_input(str(SAMPLE_SWAGGER))

    sel = select_targets(inv, environment="test", authorized=True)
    s = sel["summary"]
    check(s["measurable"] == 2,
          f"concert swagger: 2 measurable (got {s['measurable']})")
    check(s["excluded"] == 1, f"SSE endpoint excluded (got {s['excluded']})")
    check(all(m["method"] == "GET" for m in sel["measurable"]),
          "default selection is read-only")
    login = [x for x in sel["setup_only"] if "login" in x["path"]]
    check(login and "accessToken" in login[0]["provides"],
          "login is setup_only and reports what it provides")
    auth_dec = [d for d in sel["decisions_needed"] if d["key"] == "auth"]
    check(auth_dec and auth_dec[0]["unlocks"] == 4,
          f"auth decision reports how many it unlocks")
    check(any(d["key"] == "load_ratio" and d["blocking"]
              for d in sel["decisions_needed"]),
          "load ratio is a blocking decision without HAR data")
    check(sel["probe_performed"] is False and sel["probe_skipped_reason"],
          "probe status is explicit, never silent")

    # supplying credentials moves auth-blocked endpoints
    sel2 = select_targets(inv, environment="test", authorized=True,
                          auth={"X-User-Id": "provided"})
    check(sel2["summary"]["blocked"] < sel["summary"]["blocked"],
          "credentials unblock auth-blocked endpoints")
    check(sel2["summary"]["measurable"] > sel["summary"]["measurable"],
          "unblocked endpoints become measurable")

    # unknown environment -> prod
    sel3 = select_targets(inv, environment="unknown", authorized=False)
    check(sel3["environment"] == "prod", "unknown environment is treated as prod")
    check(any("treated as prod" in n for n in sel3["notes"]),
          "the prod assumption is stated, not silent")

    # prod + allow_writes: writes stay blocked pending human approval
    sel4 = select_targets(inv, environment="prod", authorized=True,
                          allow_writes=True)
    prod_writes = [b for b in sel4["blocked"] if "allow_prod_writes" in b["reason"]]
    check(bool(prod_writes), "prod writes are blocked pending human approval")

    # focused endpoint that is blocked must be called out
    sel5 = select_targets(inv, environment="test", authorized=True,
                          focus=["/api/reservations/me"])
    check(any("not measurable" in n for n in sel5["notes"]),
          "a focused-but-blocked endpoint is explained, not dropped")

    # zero measurable -> loud failure
    only_sse = {"source": {"format": "openapi"},
                "endpoints": [e for e in inv["endpoints"]
                              if "events" in e["path"]]}
    sel6 = select_targets(only_sse, environment="test", authorized=True)
    check(sel6["status"] == "failed",
          "zero measurable endpoints is a failure, not a success")

    raises(lambda: select_targets(inv, environment="production", authorized=True),
           "environment", "invalid environment value is rejected")
    raises(lambda: select_targets({"source": {"format": "openapi"},
                                   "endpoints": []},
                                  environment="test", authorized=True),
           "no endpoints", "empty inventory is rejected")

    # HAR-derived selection carries suggested weights
    har_path = tmpdir / "sel.har"
    har_path.write_text(json.dumps(HAR_FIXTURE), encoding="utf-8")
    har_inv = parse_spec_input(str(har_path))
    har_sel = select_targets(har_inv, environment="test", authorized=True)
    weighted = [m for m in har_sel["measurable"] if "suggested_weight" in m]
    check(bool(weighted), "HAR traffic shares become suggested weights")
    check(all(m.get("weight_basis") == "HAR traffic share" for m in weighted),
          "weight provenance is recorded")
    check(not any(d["key"] == "load_ratio" for d in har_sel["decisions_needed"]),
          "load ratio is not asked for when HAR data answers it")


# --------------------------------------------------------------------------
# 3. path templating unit checks
# --------------------------------------------------------------------------


def test_templating() -> None:
    print("path templating")
    w: list[str] = []
    check(_template_path("/orders/12345", w) == "/orders/{orderId}",
          "numeric id inherits the collection name")
    check(_template_path(
        "/u/550e8400-e29b-41d4-a716-446655440000", w) == "/u/{id}",
        "uuid segment becomes {id}")
    check(_template_path("/reports/2024", w) == "/reports/2024",
          "year-like segment is not templated")
    check(len(w) == 1 and "2024" in w[0], "ambiguity is reported exactly once")


def main() -> int:
    tmpdir = Path(__file__).resolve().parent / "_tmp"
    tmpdir.mkdir(exist_ok=True)

    test_parsing(tmpdir)
    test_selection(tmpdir)
    test_templating()

    print(f"\n{_passes} passed, {len(_failures)} failed")
    for failure in _failures:
        print(f"  - {failure}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
