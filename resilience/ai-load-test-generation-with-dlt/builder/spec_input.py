#!/usr/bin/env python3
"""Deterministic spec-input parsing and target selection.

T1  parse_spec_input : HAR / OpenAPI -> normalized endpoint inventory.
T1.5 select_targets  : inventory -> measurable / setup_only / blocked / excluded
                       verdict table. The tool never chooses — it organizes the
                       choices and leaves the decision to a human.

Both are mechanical. The LLM authors judgments (load ratios, approval), never
the normalization: path templating over 5000 HAR entries and call-frequency
aggregation silently rot when a model does them.

Design rules carried over from jmx_builder.py:
  - Refuse rather than guess. Missing information stays null and is reported
    in warnings; auth schemes and failure shapes are never inferred.
  - Specs lie. The concert swagger declares 200 for all 16 operations; the
    real service returns 401/404/500. Classification therefore relies only on
    what cannot lie: methods, content types, and parameter sources.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}

# Header names whose *values* must never appear in output. Names are kept so
# the auth requirement stays visible.
SECRET_HEADERS = {
    "authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token",
    "proxy-authorization", "x-amz-security-token",
}

STATIC_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff",
    ".woff2", ".ttf", ".eot", ".map", ".webp", ".avif", ".mp4", ".webm",
}
STATIC_MIME_PREFIXES = ("image/", "font/", "video/", "audio/")

# Streaming / upload content types that a load generator cannot measure as a
# request-response sample.
UNMEASURABLE_CONTENT = ("text/event-stream", "multipart/form-data")

# Response field names that indicate the endpoint *produces* credentials.
# Purely lexical, service-agnostic; drives setup_only, never measurable.
TOKEN_FIELDS = {"accesstoken", "token", "refreshtoken", "idtoken", "sessionid",
                "sessiontoken", "jwt", "apikey"}


class SpecInputError(Exception):
    """The input is unusable. Raised instead of guessing."""


# --------------------------------------------------------------------------
# format detection
# --------------------------------------------------------------------------


def detect_format(data: Any) -> str:
    if isinstance(data, dict):
        if "log" in data and isinstance(data["log"], dict) and "entries" in data["log"]:
            return "har"
        if "openapi" in data or "swagger" in data:
            return "openapi"
        # Recognised only to refuse it by name — see parse_spec_input. Without
        # this branch a collection would fail as "cannot determine format",
        # which sends the reader looking for a parse bug that isn't there.
        if "info" in data and ("item" in data or "items" in data) \
                and isinstance(data.get("info"), dict) \
                and ("_postman_id" in data["info"] or "schema" in data["info"]):
            return "postman"
    raise SpecInputError(
        "cannot determine input format; pass format explicitly (har / openapi)"
    )


def load_input(file_path: str) -> Any:
    path = Path(file_path)
    if not path.exists():
        raise SpecInputError(f"file not found: {file_path}")
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # YAML OpenAPI is legitimate; try it only if available.
        try:
            import yaml  # type: ignore
            return yaml.safe_load(text)
        except ImportError:
            raise SpecInputError(
                f"JSON parse failed at line {exc.lineno} col {exc.colno}: "
                f"{exc.msg} (PyYAML not installed, YAML not attempted)"
            ) from exc
        except Exception as yexc:  # yaml.YAMLError
            raise SpecInputError(
                f"neither JSON (line {exc.lineno}: {exc.msg}) nor YAML "
                f"({yexc}) parsed"
            ) from exc


# --------------------------------------------------------------------------
# OpenAPI
# --------------------------------------------------------------------------


def _resolve_ref(schema: Any, root: dict, seen: frozenset = frozenset()) -> Any:
    """Follow $ref one level at a time; cycles resolve to None with no error
    escalation — the caller records a warning, the run continues."""
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if ref is None:
        return schema
    if ref in seen:
        return None  # cycle
    node: Any = root
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return _resolve_ref(node, root, seen | {ref})


def _schema_fields(schema: Any, root: dict) -> list[str]:
    schema = _resolve_ref(schema, root)
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if isinstance(props, dict):
        return sorted(props.keys())
    items = schema.get("items")
    if items is not None:
        return _schema_fields(items, root)
    return []


def parse_openapi(data: dict) -> dict:
    warnings: list[str] = []
    servers = []
    for server in data.get("servers", []):
        url = server.get("url", "")
        host = urlsplit(url).netloc or url
        if host:
            servers.append(host)

    schemes = (data.get("components") or {}).get("securitySchemes")
    endpoints = []
    eid = 0
    for path, ops in (data.get("paths") or {}).items():
        common_params = ops.get("parameters", []) if isinstance(ops, dict) else []
        for method, op in ops.items():
            if method.upper() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            eid += 1
            params = list(common_params) + op.get("parameters", [])
            resolved_params: dict[str, list[dict]] = {"path": [], "query": [], "header": []}
            for p in params:
                p = _resolve_ref(p, data) or {}
                where = p.get("in")
                if where not in resolved_params:
                    continue
                schema = _resolve_ref(p.get("schema") or {}, data) or {}
                resolved_params[where].append({
                    "name": p.get("name"),
                    "type": schema.get("type"),
                    "required": bool(p.get("required")),
                    "default": schema.get("default"),
                    "example": p.get("example", schema.get("example")),
                })

            body_schema = None
            body_required = False
            request_body = _resolve_ref(op.get("requestBody"), data)
            if isinstance(request_body, dict):
                body_required = bool(request_body.get("required"))
                content = request_body.get("content") or {}
                for ctype, media in content.items():
                    fields = _schema_fields((media or {}).get("schema"), data)
                    body_schema = {"content_type": ctype, "fields": fields}
                    break

            response_fields: list[str] = []
            response_content_types: list[str] = []
            for code, resp in (op.get("responses") or {}).items():
                resp = _resolve_ref(resp, data) or {}
                for ctype, media in (resp.get("content") or {}).items():
                    response_content_types.append(ctype)
                    if not response_fields:
                        response_fields = _schema_fields((media or {}).get("schema"), data)

            security = op.get("security", data.get("security"))
            auth_hint = None
            if security:
                auth_hint = "declared security requirement"
            else:
                required_headers = [p["name"] for p in resolved_params["header"]
                                    if p["required"]]
                if required_headers:
                    auth_hint = f"required header(s): {', '.join(required_headers)}"

            endpoints.append({
                "id": eid,
                "method": method.upper(),
                "path": path,
                "call_count": None,
                "traffic_share": None,
                "query_params": resolved_params["query"],
                "path_params": resolved_params["path"],
                "header_params": resolved_params["header"],
                "body_schema": body_schema,
                "body_required": body_required,
                "response_fields": response_fields,
                "response_content_types": sorted(set(response_content_types)),
                "auth_hint": auth_hint,
            })

    if schemes is None:
        needing = [e for e in endpoints if e["auth_hint"]]
        if needing:
            warnings.append(
                f"securitySchemes undefined but {len(needing)} endpoint(s) "
                "carry auth-looking requirements; how credentials are obtained "
                "is not in the spec"
            )
    declared = {code for e in (data.get("paths") or {}).values()
                if isinstance(e, dict)
                for op in e.values() if isinstance(op, dict)
                for code in (op.get("responses") or {})}
    if declared and declared <= {"200", "201", "default"}:
        warnings.append(
            "only success responses are declared; failure shapes are unknown "
            "from the spec — assertions must be based on observed responses, "
            "not this document"
        )
    for e in endpoints:
        if any(ct.startswith("text/event-stream") for ct in e["response_content_types"]):
            warnings.append(f"{e['method']} {e['path']} is text/event-stream (SSE)")

    return {
        "source": {
            "format": "openapi",
            "version": data.get("openapi") or data.get("swagger"),
            "title": (data.get("info") or {}).get("title"),
        },
        "servers": servers,
        "endpoints": endpoints,
        "excluded": [],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# HAR
# --------------------------------------------------------------------------

_NUMERIC_SEG = re.compile(r"^\d+$")
_UUID_SEG = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HASH_SEG = re.compile(r"^[0-9a-f]{16,}$", re.I)
_YEAR_AMBIGUOUS = re.compile(r"^(19|20)\d{2}$")


def _template_path(path: str, warnings: list[str]) -> str:
    """Replace id-looking segments with {param}. Ambiguous segments (a 4-digit
    number could be a year) are kept literal and reported, never merged
    silently."""
    segments = path.split("/")
    out = []
    for i, seg in enumerate(segments):
        if _UUID_SEG.match(seg) or _HASH_SEG.match(seg):
            out.append("{id}")
        elif _NUMERIC_SEG.match(seg):
            if _YEAR_AMBIGUOUS.match(seg):
                warnings.append(
                    f"segment '{seg}' in {path} could be an ID or a year; "
                    "kept literal, not merged"
                )
                out.append(seg)
            else:
                prev = segments[i - 1].rstrip("s") if i > 0 else "id"
                out.append("{%sId}" % prev if prev else "{id}")
        else:
            out.append(seg)
    return "/".join(out)


def _is_static(url_path: str, mime: str) -> bool:
    ext = Path(urlsplit(url_path).path).suffix.lower()
    if ext in STATIC_EXTENSIONS:
        return True
    return any(mime.startswith(p) for p in STATIC_MIME_PREFIXES)


def _har_response_fields(response: dict) -> list[str]:
    """Top-level field names of a recorded JSON response body.

    A HAR carries what the server actually returned, which is better evidence
    than a declared schema — the swagger for this very fixture declares 200 for
    all 16 operations while the service also returns 401/404/500. Shape matches
    _schema_fields (sorted top-level keys; for an array, the first object's
    keys) so a HAR-derived inventory reads the same as an OpenAPI-derived one.

    Returns [] when the recording carries no body — a HAR can be captured
    without response content — so an empty result means "not recorded", never
    "this endpoint returns no fields". Callers must not read it as the latter.
    """
    content = response.get("content") or {}
    if "json" not in (content.get("mimeType") or "").lower():
        return []
    text = content.get("text")
    if not text:
        return []
    if (content.get("encoding") or "").lower() == "base64":
        try:
            text = base64.b64decode(text).decode("utf-8", "replace")
        except Exception:
            return []
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return []
    if isinstance(body, list):
        body = next((item for item in body if isinstance(item, dict)), None)
    if not isinstance(body, dict):
        return []
    return sorted(body.keys())


def parse_har(data: dict, exclude_static: bool = True,
              include_hosts: list[str] | None = None) -> dict:
    warnings: list[str] = []
    excluded: list[dict] = []
    entries = data["log"].get("entries", [])
    if not entries:
        # An empty result is reported as empty, never dressed up as success.
        return {"source": {"format": "har", "version": data["log"].get("version"),
                           "title": (data["log"].get("creator") or {}).get("name")},
                "servers": [], "endpoints": [], "excluded": [],
                "warnings": ["HAR contains 0 entries"]}

    merged: dict[tuple[str, str, str], dict] = {}
    hosts: dict[str, int] = {}
    total_kept = 0
    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})
        method = request.get("method", "GET").upper()
        url = urlsplit(request.get("url", ""))
        host, path = url.netloc, url.path or "/"
        mime = (response.get("content") or {}).get("mimeType", "") or ""

        if include_hosts and host not in include_hosts:
            excluded.append({"path": f"{host}{path}", "reason": "host not included"})
            continue
        if exclude_static and _is_static(path, mime):
            excluded.append({"path": path, "reason": "static resource"})
            continue

        hosts[host] = hosts.get(host, 0) + 1
        total_kept += 1
        templated = _template_path(path, warnings)
        key = (method, host, templated)
        slot = merged.setdefault(key, {
            "method": method, "host": host, "path": templated,
            "call_count": 0, "query_params": {}, "header_params": {},
            "statuses": {}, "content_types": set(), "sample_path": path,
            "has_body": False, "body_content_type": None,
            "response_fields": set(), "json_body_missing": 0,
        })
        slot["call_count"] += 1
        status = str(response.get("status", ""))
        slot["statuses"][status] = slot["statuses"].get(status, 0) + 1

        # Only successful responses contribute assertion markers: fields off a
        # 500 envelope would be exactly the wrong thing to assert on.
        if status.startswith("2"):
            fields = _har_response_fields(response)
            if fields:
                slot["response_fields"].update(fields)
            elif "json" in mime.lower():
                slot["json_body_missing"] += 1
        if mime:
            slot["content_types"].add(mime.split(";")[0].strip())
        for q in request.get("queryString", []):
            name = q.get("name")
            if name:
                slot["query_params"].setdefault(name, q.get("value"))
        for h in request.get("headers", []):
            name = (h.get("name") or "").lower()
            if name in SECRET_HEADERS:
                # Keep the requirement, drop the value. Values in a HAR are
                # live credentials.
                slot["header_params"][h["name"]] = "**masked**"
            elif name.startswith("x-") and name not in ("x-requested-with",):
                slot["header_params"][h["name"]] = h.get("value")
        post = request.get("postData")
        if post:
            slot["has_body"] = True
            slot["body_content_type"] = (post.get("mimeType") or "").split(";")[0]

    endpoints = []
    for eid, slot in enumerate(sorted(
            merged.values(), key=lambda s: -s["call_count"]), start=1):
        endpoints.append({
            "id": eid,
            "method": slot["method"],
            "path": slot["path"],
            "call_count": slot["call_count"],
            "traffic_share": round(slot["call_count"] / total_kept, 4),
            "query_params": [{"name": k, "type": None, "required": False,
                              "default": None, "example": v}
                             for k, v in slot["query_params"].items()],
            "path_params": [{"name": m.group(1), "type": None, "required": True,
                             "default": None,
                             "example": None}
                            for m in re.finditer(r"\{([^}]+)\}", slot["path"])],
            "header_params": [{"name": k, "type": None, "required": True,
                               "default": None, "example": v}
                              for k, v in slot["header_params"].items()],
            "body_schema": ({"content_type": slot["body_content_type"], "fields": []}
                            if slot["has_body"] else None),
            "body_required": slot["has_body"],
            "response_fields": sorted(slot["response_fields"]),
            "response_content_types": sorted(slot["content_types"]),
            "auth_hint": next((f"header {k} (value masked)"
                               for k in slot["header_params"]
                               if k.lower() in SECRET_HEADERS), None),
            "observed_statuses": slot["statuses"],
        })

    masked = sum(1 for e in endpoints if e["auth_hint"])
    if masked:
        warnings.append(
            f"{masked} endpoint(s) carried credential headers; values masked. "
            "A HAR is one person's session — multi-VU runs need a credential "
            "CSV, not the recorded token"
        )
    bodyless = sum(1 for slot in merged.values()
                   if slot["json_body_missing"] and not slot["response_fields"])
    if bodyless:
        # Say why it is empty. Silence here reads as "this endpoint has no
        # response fields", and an assertion gets built on that assumption.
        warnings.append(
            f"{bodyless} endpoint(s) returned JSON but the recording carries no "
            "response body, so response_fields is empty — re-record with "
            "response content enabled, or supply the body markers to assert on"
        )
    return {
        "source": {"format": "har", "version": data["log"].get("version"),
                   "title": (data["log"].get("creator") or {}).get("name")},
        "servers": sorted(hosts, key=hosts.get, reverse=True),
        "endpoints": endpoints,
        "excluded": excluded,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# T1 entry point
# --------------------------------------------------------------------------


def parse_spec_input(file_path: str, format: str = "auto",
                     exclude_static: bool = True,
                     include_hosts: list[str] | None = None) -> dict:
    data = load_input(file_path)
    fmt = format if format != "auto" else detect_format(data)
    if fmt == "openapi":
        return parse_openapi(data)
    if fmt == "har":
        return parse_har(data, exclude_static, include_hosts)
    if fmt == "postman":
        # Detected, then refused on purpose. A partial parse is worse than none
        # here: a collection's saved example responses are the only place it
        # states what a success body looks like, and every spec this builder
        # emits requires a body check. Dropping them forces the model to invent
        # one. Export a HAR (which also carries real call frequencies) or an
        # OpenAPI document instead.
        raise SpecInputError(
            "Postman collections are not supported yet — export a HAR or an "
            "OpenAPI/Swagger document instead")
    raise SpecInputError(f"unknown format: {fmt}")


# --------------------------------------------------------------------------
# T1.5 select_targets
# --------------------------------------------------------------------------


def _param_source(param: dict, auth: dict | None,
                  list_endpoints: list[dict]) -> str | None:
    """Where a parameter's value can come from. None = no source = blocked."""
    if param.get("example") is not None:
        return "example value"
    if param.get("default") is not None:
        return "literal default"
    if auth and param["name"] in auth:
        return "user-supplied auth"
    # An id-shaped path param is resolvable when a GET collection endpoint
    # exists whose path is a proper prefix — its response can seed the value.
    name = (param.get("name") or "").lower()
    if name.endswith("id"):
        for lister in list_endpoints:
            return f"setup extraction from {lister['path']}"
    return None


def _produces_credentials(endpoint: dict) -> bool:
    fields = {f.lower() for f in endpoint.get("response_fields", [])}
    return bool(fields & TOKEN_FIELDS)


def select_targets(inventory: dict, environment: str, authorized: bool,
                   auth: dict | None = None, allow_writes: bool = False,
                   focus: list[str] | None = None) -> dict:
    """Classify every endpoint with a reason and a recovery path.

    Probing is intentionally NOT implemented here: sending requests to a host
    belongs to a separate, explicitly authorized step. This function is pure —
    it looks only at the inventory.
    """
    if environment not in ("test", "stage", "prod", "unknown"):
        raise SpecInputError(f"environment must be test/stage/prod/unknown, "
                             f"got {environment!r}")
    effective_env = "prod" if environment == "unknown" else environment
    notes = []
    if environment == "unknown":
        notes.append("environment not declared -> treated as prod")

    endpoints = inventory.get("endpoints", [])
    if not endpoints:
        raise SpecInputError("inventory has no endpoints; nothing to select from")

    # Collection GETs (no path params) can seed id-shaped params downstream.
    list_endpoints = [e for e in endpoints
                      if e["method"] == "GET" and not e["path_params"]]

    measurable, setup_only, blocked, excluded = [], [], [], []
    for e in endpoints:
        label = {"id": e["id"], "method": e["method"], "path": e["path"]}

        # -- excluded: content type says it cannot be measured -------------
        streaming = [ct for ct in e.get("response_content_types", [])
                     if any(ct.startswith(u) for u in UNMEASURABLE_CONTENT)]
        body_ct = (e.get("body_schema") or {}).get("content_type") or ""
        if streaming:
            excluded.append({**label, "reason":
                             f"{streaming[0]} — long-lived/streaming response, "
                             "response time is not measurable"})
            continue
        if body_ct.startswith("multipart/form-data"):
            excluded.append({**label, "reason":
                             "multipart upload — throughput measures the "
                             "generator's disk, not the API"})
            continue

        # -- auth requirement ----------------------------------------------
        needs_auth = bool(e.get("auth_hint"))
        auth_satisfied = bool(auth) and (
            not needs_auth
            or any(p["name"] in auth for p in e.get("header_params", []))
            or "credentials" in auth
        )

        # -- parameter resolvability ----------------------------------------
        unresolved = []
        resolved_by = {}
        for p in e.get("path_params", []):
            src = _param_source(p, auth, list_endpoints)
            if src is None:
                unresolved.append(p["name"])
            else:
                resolved_by[p["name"]] = src
        if e.get("body_required") and not (e.get("body_schema") or {}).get("fields") \
                and inventory["source"]["format"] == "openapi":
            # body exists but its shape is unknown — cannot synthesize one
            unresolved.append("(request body: schema unresolved)")

        # -- credential producers are setup, not measurement -----------------
        if _produces_credentials(e):
            setup_only.append({**label,
                               "reason": "response carries credential fields; "
                                         "this endpoint prepares state, it is "
                                         "not the thing being measured",
                               "provides": sorted(
                                   f for f in e.get("response_fields", [])
                                   if f.lower() in TOKEN_FIELDS)})
            continue

        # -- writes -----------------------------------------------------------
        if e["method"] in WRITE_METHODS:
            if not allow_writes:
                setup_only.append({**label,
                                   "reason": "write method with allow_writes "
                                             "off — default is read-only",
                                   "provides": []})
                continue
            if effective_env == "prod":
                blocked.append({**label,
                                "reason": "write against prod requires the "
                                          "spec-level allow_prod_writes set by "
                                          "a person",
                                "recoverable": True,
                                "unblock": "explicit human approval"})
                continue

        # -- blocked -----------------------------------------------------------
        if needs_auth and not auth_satisfied:
            blocked.append({**label,
                            "reason": f"requires authentication "
                                      f"({e['auth_hint']}); no credentials "
                                      "supplied",
                            "recoverable": True,
                            "unblock": "supply credentials or a login procedure"})
            continue
        if unresolved:
            blocked.append({**label,
                            "reason": f"no source for parameter(s): "
                                      f"{', '.join(unresolved)} — every "
                                      "request would fail",
                            "recoverable": True,
                            "unblock": "provide a CSV of valid values, an "
                                       "example, or exclude"})
            continue

        # -- measurable ---------------------------------------------------------
        entry = {**label,
                 "reason": "idempotent; all parameters resolvable"
                 if e["method"] in ("GET", "HEAD")
                 else "write explicitly allowed; parameters resolvable",
                 "params_resolved_by": resolved_by}
        if e.get("traffic_share") is not None:
            entry["suggested_weight"] = max(1, round(e["traffic_share"] * 10))
            entry["weight_basis"] = "HAR traffic share"
        measurable.append(entry)

    # focus entries must never be silently dropped
    focus_notes = []
    if focus:
        landed = {x["path"]: bucket
                  for bucket, rows in (("measurable", measurable),
                                       ("setup_only", setup_only),
                                       ("blocked", blocked),
                                       ("excluded", excluded))
                  for x in rows}
        for f in focus:
            where = landed.get(f)
            if where and where != "measurable":
                focus_notes.append(
                    f"focused endpoint {f} is {where}, not measurable — "
                    "see its reason; it was not silently dropped")
            elif where is None:
                focus_notes.append(f"focused endpoint {f} not found in inventory")

    decisions = []
    auth_blocked = [b for b in blocked if "authentication" in b["reason"]]
    if auth_blocked:
        decisions.append({"key": "auth",
                          "question": "What do these endpoints authenticate with?",
                          "unlocks": len(auth_blocked), "blocking": False})
    writes_deferred = [s for s in setup_only if "allow_writes" in s["reason"]]
    if writes_deferred:
        decisions.append({"key": "writes",
                          "question": "Include writes? How should the data they "
                                      "leave behind be cleaned up?",
                          "unlocks": len(writes_deferred), "blocking": False})
    if measurable and all("suggested_weight" not in m for m in measurable):
        decisions.append({"key": "load_ratio",
                          "question": "Traffic share per endpoint (no HAR was "
                                      "given, and a spec carries no frequency "
                                      "information)",
                          "unlocks": 0, "blocking": True})

    result = {
        "environment": effective_env,
        "authorized": authorized,
        "probe_performed": False,
        "probe_skipped_reason": "probing is a separate, explicitly authorized step",
        "summary": {"total": len(endpoints), "measurable": len(measurable),
                    "setup_only": len(setup_only), "blocked": len(blocked),
                    "excluded": len(excluded)},
        "measurable": measurable,
        "setup_only": setup_only,
        "blocked": blocked,
        "excluded": excluded,
        "decisions_needed": decisions,
        "notes": notes + focus_notes,
    }
    if not measurable:
        # Zero targets is a failure to act on, not a result to report as done.
        result["status"] = "failed"
        result["failure"] = ("no measurable endpoint; resolve decisions_needed "
                             "before writing any spec")
    else:
        result["status"] = "ok"
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def summarize_selection(sel: dict) -> str:
    lines = [f"{sel['summary']['total']} endpoints -> "
             f"{sel['summary']['measurable']} measurable, "
             f"{sel['summary']['setup_only']} setup-only, "
             f"{sel['summary']['blocked']} blocked, "
             f"{sel['summary']['excluded']} excluded",
             ""]
    for title, rows in (("MEASURABLE", sel["measurable"]),
                        ("SETUP ONLY", sel["setup_only"]),
                        ("BLOCKED", sel["blocked"]),
                        ("EXCLUDED", sel["excluded"])):
        if not rows:
            continue
        lines.append(title)
        for r in rows:
            lines.append(f"  {r['method']:6} {r['path']:<45} {r['reason']}")
        lines.append("")
    if sel["decisions_needed"]:
        lines.append("DECISION NEEDED")
        for d in sel["decisions_needed"]:
            extra = f" (unlocks {d['unlocks']})" if d["unlocks"] else ""
            lines.append(f"  [{d['key']}] {d['question']}{extra}")
    if sel.get("status") == "failed":
        lines.append("")
        lines.append(f"FAILED: {sel['failure']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="HAR / OpenAPI file")
    parser.add_argument("--format", default="auto",
                        choices=["auto", "har", "openapi"])
    parser.add_argument("--select", action="store_true",
                        help="also run target selection")
    parser.add_argument("--environment", default="unknown",
                        choices=["test", "stage", "prod", "unknown"])
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("-o", "--output", help="write inventory JSON here")
    args = parser.parse_args()

    try:
        inventory = parse_spec_input(args.input, args.format)
    except SpecInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"{inventory['source']['format']}: "
          f"{inventory['source'].get('title') or '(untitled)'} — "
          f"{len(inventory['endpoints'])} endpoints, "
          f"{len(inventory['excluded'])} excluded")
    for w in inventory["warnings"]:
        print(f"  warning: {w}")

    if args.output:
        Path(args.output).write_text(
            json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.output}")

    if args.select:
        print()
        selection = select_targets(inventory, environment=args.environment,
                                   authorized=False,
                                   allow_writes=args.allow_writes)
        print(summarize_selection(selection))
        return 0 if selection.get("status") == "ok" else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
