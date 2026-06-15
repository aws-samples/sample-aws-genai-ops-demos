"""
G.O.A.T. Orchestration Agent - Strands Agent SDK + @tool pattern
Uses LLM reasoning to classify intent, invoke sub-agents, and correlate results.
Build: 2026-04-03T18:00

This is the ONLY agent that uses Strands Agent SDK. It takes natural language input,
uses LLM reasoning to classify intent, decides which sub-agents to invoke via @tool
functions, correlates cross-domain results, and streams natural language responses.
Follows the password-reset chatbot pattern (async streaming).
"""
import base64
import contextvars
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import boto3
from botocore.config import Config as BotoConfig
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from aws_utils import get_region

# Per-session Agent pool (LRU + TTL eviction).
from session_manager import SessionManager

# Capture_Conversation_Context persistence (Task 36, Reqs 9.20 / 17.9).
# ``state.py`` lives next to ``main.py`` in the agent container so this
# is a sibling import. The module is fail-soft: every helper logs and
# swallows DynamoDB errors so missing AWS credentials or an unset
# ``CONVERSATIONS_TABLE_NAME`` env var (the local-test case) cannot
# break the chat response path.
import state
import flow_selector as fs

app = BedrockAgentCoreApp()
AWS_REGION = get_region()


# ---------------------------------------------------------------------------
# Capture authorization (Req 9.16)
#
# Three Network Agent actions perform write operations against the capture
# infrastructure: ``start_capture``, ``stop_capture``, and
# ``transform_capture``. Membership in the Cognito group
# ``GOATNetworkCaptureUsers`` (Capture_Authorization_Group) is the
# server-side gate the Orchestration Agent enforces before invoking any of
# them. Read-only Network Agent actions (``list_enis``, ``list_captures``,
# ``query_pcap`` and friends) remain available to every authenticated user
# and are NOT in this set.
#
# The actual group list comes from the per-request ContextVar populated by
# ``agent_invocation`` from whichever Cognito plumbing path is in place at
# runtime (payload field, AgentCore request headers, or a parsed JWT).
# ---------------------------------------------------------------------------
CAPTURE_ACTIONS = frozenset({
    "start_capture",
    "stop_capture",
    "transform_capture",
})

GOAT_NETWORK_CAPTURE_GROUP = "GOATNetworkCaptureUsers"

# Per-request Cognito group list. Defaults to an empty tuple so that a
# request without any group plumbing fails closed: the user sees the
# refusal message rather than silently bypassing the check. ``set`` returns
# a token, but we never need to ``reset`` because each invocation runs in
# its own asyncio task and therefore in its own ``contextvars.Context``.
_CURRENT_USER_GROUPS: "contextvars.ContextVar[tuple[str, ...]]" = contextvars.ContextVar(
    "current_user_groups", default=()
)


def _decode_jwt_groups(jwt_token: str) -> tuple[str, ...]:
    """Best-effort extraction of ``cognito:groups`` from a Bearer JWT.

    AgentCore's ``customJWTAuthorizer`` validates the token signature
    upstream — by the time the request reaches this handler the token is
    already authenticated. We therefore decode the payload without
    re-validating the signature; we only ever read it to populate the
    Capture_Authorization_Group check.

    Returns an empty tuple on any decode error so the caller falls back to
    the empty group list and the request fails closed at the
    Capture_Action gate.
    """
    try:
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return ()
        # Pad the base64 payload to a multiple of 4 before decoding —
        # JWTs strip trailing ``=`` padding by convention.
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return ()

    raw_groups = claims.get("cognito:groups")
    if isinstance(raw_groups, list):
        return tuple(str(g) for g in raw_groups if isinstance(g, str))
    if isinstance(raw_groups, str):
        # Some IdPs encode groups as a single space- or comma-separated
        # string; tolerate both shapes.
        if "," in raw_groups:
            return tuple(g.strip() for g in raw_groups.split(",") if g.strip())
        return tuple(g for g in raw_groups.split() if g)
    return ()


def _normalize_group_list(value: object) -> tuple[str, ...]:
    """Coerce a payload-supplied groups field into a tuple of strings."""
    if isinstance(value, list):
        return tuple(str(g) for g in value if isinstance(g, str) and g)
    if isinstance(value, str) and value:
        if "," in value:
            return tuple(g.strip() for g in value.split(",") if g.strip())
        return tuple(g for g in value.split() if g)
    return ()


def _extract_user_groups(payload: object, context: object) -> tuple[str, ...]:
    """Resolve the calling user's Cognito group list for this invocation.

    Inspection order — the first non-empty source wins so an explicit
    payload override always takes precedence over header parsing:

    1. ``payload.user_groups`` or ``payload.context.cognito_groups`` —
       used when the frontend (or a calling Lambda) elects to pass the
       group list directly. The frontend already has the ID token in
       hand and can pass the decoded ``cognito:groups`` claim without
       requiring AgentCore to be reconfigured for ``customJWTAuthorizer``.
    2. ``context.request_headers['authorization']`` — used when AgentCore
       is configured with ``customJWTAuthorizer`` so the validated
       Bearer JWT lands in the request headers. We decode the payload
       (signature already validated upstream) and read ``cognito:groups``.
    3. Empty tuple — fail closed; ``query_network_pcap`` will refuse any
       Capture_Action.
    """
    # Source 1: explicit payload-level group list.
    if isinstance(payload, dict):
        for key in ("user_groups", "userGroups", "cognito_groups", "cognitoGroups"):
            groups = _normalize_group_list(payload.get(key))
            if groups:
                return groups
        ctx_obj = payload.get("context")
        if isinstance(ctx_obj, dict):
            for key in ("user_groups", "userGroups", "cognito_groups", "cognitoGroups"):
                groups = _normalize_group_list(ctx_obj.get(key))
                if groups:
                    return groups

    # Source 2: AgentCore request headers (Authorization: Bearer <jwt>).
    headers = getattr(context, "request_headers", None) if context is not None else None
    if isinstance(headers, dict):
        # HTTP header names are case-insensitive; check both common spellings.
        auth_header = headers.get("authorization") or headers.get("Authorization")
        if isinstance(auth_header, str) and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token:
                groups = _decode_jwt_groups(token)
                if groups:
                    return groups

    return ()


def _user_in_group(required_group: str, groups: Optional[Iterable[str]] = None) -> bool:
    """Return True iff the current request's user list includes ``required_group``."""
    candidate: Sequence[str]
    if groups is None:
        candidate = _CURRENT_USER_GROUPS.get()
    else:
        candidate = tuple(groups)
    return required_group in candidate


# ---------------------------------------------------------------------------
# Per-request user identifier (Req 9.21)
#
# The Capture_Idempotency_Token derived for ``start_capture`` invocations
# (Req 9.21) is a SHA-256 hash over ``eni_ids ∥ duration_minutes ∥ user_id
# ∥ floor(timestamp, 1m)``. The ``user_id`` component must be stable
# across an immediate retry of the same prompt — i.e. it must come from
# the authenticated identity, not from request metadata that changes
# turn-over-turn. We resolve it from the same JWT/payload plumbing as
# the Cognito group list and stash it in a ContextVar so the
# ``derive_capture_idempotency_token`` helper can consume it without
# threading an extra argument through the Strands ``@tool`` boundary.
#
# The default ``""`` ensures a deterministic hash even when the
# orchestrator is invoked outside the AgentCore runtime (for example
# during local tests). In that case the token is still a valid SHA-256
# hex digest; only its uniqueness across users degrades.
# ---------------------------------------------------------------------------
_CURRENT_USER_ID: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "current_user_id", default=""
)


# ---------------------------------------------------------------------------
# Per-request conversation identifier and user prompt (Task 36, Reqs 9.20/17.9)
#
# Capture_Conversation_Context resolution is keyed on the active
# conversation id (the AgentCore ``RequestContext.session_id``,
# overridable by the caller through a ``conversation_id`` payload
# field). The value is resolved once per ``agent_invocation`` call
# and stashed here so the ``query_network_pcap`` ``@tool`` can
# consult it without threading additional arguments through the
# Strands runtime.
#
# The user's natural-language prompt is stashed alongside the
# conversation id so the anaphoric-reference detector can run
# without re-parsing payload structure inside the tool. Both
# variables default to the empty string so the persistence layer
# falls back to no-op semantics when invoked from local tests
# that bypass ``agent_invocation`` (the existing
# ``test_capture_authorization`` and ``test_capture_confirmation``
# suites).
# ---------------------------------------------------------------------------
_CURRENT_CONVERSATION_ID: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "current_conversation_id", default=""
)
_CURRENT_USER_PROMPT: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "current_user_prompt", default=""
)

def _decode_jwt_user_id(jwt_token: str) -> str:
    """Best-effort extraction of a stable user identifier from a Bearer JWT.

    Reads (in order) ``cognito:username``, ``sub``, and ``username`` from
    the JWT payload. The signature is intentionally not validated —
    AgentCore's ``customJWTAuthorizer`` performs that step upstream.
    Returns the empty string on any decode error so callers fall back
    to the ContextVar default.
    """
    try:
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return ""
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return ""

    for key in ("cognito:username", "sub", "username"):
        candidate = claims.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def _extract_user_id(payload: object, context: object) -> str:
    """Resolve the calling user's stable identifier for this invocation.

    Inspection order mirrors ``_extract_user_groups``: explicit payload
    fields win over JWT header parsing so callers can override the
    identity (used by tests), and the empty string is returned when no
    source provides one.
    """
    if isinstance(payload, dict):
        for key in ("user_id", "userId", "username", "sub"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        ctx_obj = payload.get("context")
        if isinstance(ctx_obj, dict):
            for key in ("user_id", "userId", "username", "sub"):
                candidate = ctx_obj.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate

    headers = getattr(context, "request_headers", None) if context is not None else None
    if isinstance(headers, dict):
        auth_header = headers.get("authorization") or headers.get("Authorization")
        if isinstance(auth_header, str) and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token:
                user_id = _decode_jwt_user_id(token)
                if user_id:
                    return user_id

    return ""


# ---------------------------------------------------------------------------
# Capture cost estimation (Req 17.2)
#
# The Capture_Confirmation_Prompt that the orchestration agent emits
# before invoking ``start_capture`` must include an estimated USD cost
# computed from the documented Traffic Mirror unit prices. The unit
# prices live in ``agents/shared/prices.json`` (see ``prices.py`` in
# that directory for documentation). At container build time the JSON
# file is copied alongside ``main.py`` so this module can read it
# without crossing a package boundary.
#
# The formula matches the design's ``Capture_Confirmation_Prompt``
# section verbatim:
#
#     cost_usd = (eni_count * duration_hours * price_per_eni_hour)
#              + (estimated_bytes / 1e9 * price_per_gb)
#
# where ``estimated_bytes`` defaults to a 1 Mbps per ENI heuristic
# (``eni_count * duration_minutes * 60 * 125000``).
#
# The README cost-estimate table (Req 14.2) reads the same JSON file,
# so the chat estimate and the documentation cannot drift.
# ---------------------------------------------------------------------------
_PRICES_JSON_FILENAME = "prices.json"
_PRICES_PATH = Path(__file__).parent / _PRICES_JSON_FILENAME

_PRICES_CACHE: Optional[dict] = None


def _load_prices() -> dict:
    """Load and memoize the shared price table.

    The file is bundled into the container at build time (Dockerfile
    ``COPY . .``) so reading it is a synchronous filesystem call. The
    result is cached on the first call to keep subsequent
    ``compute_capture_cost_usd`` calls in the sub-millisecond range.

    Returns:
        The deserialized JSON object.

    Raises:
        FileNotFoundError: When ``prices.json`` is missing from the
            container — this indicates a broken build pipeline rather
            than a runtime condition, so it is surfaced to the caller.
    """
    global _PRICES_CACHE
    if _PRICES_CACHE is None:
        with open(_PRICES_PATH, "r", encoding="utf-8") as f:
            _PRICES_CACHE = json.load(f)
    return _PRICES_CACHE


def _get_traffic_mirror_eni_hour_price(region: Optional[str] = None) -> float:
    """Return the regional per-ENI-hour Traffic Mirror price in USD."""
    table = _load_prices()["trafficMirror"]
    by_region = table.get("eniHourPriceByRegion", {})
    default_price = float(table["eniHourPriceDefault"])
    if region is None:
        return default_price
    raw = by_region.get(region)
    if raw is None:
        return default_price
    return float(raw)


def estimate_capture_bytes(eni_count: int, duration_minutes: int) -> int:
    """Return the default ``estimated_bytes`` heuristic for a capture.

    Implements ``eni_count * duration_minutes * 60 * 125000``, which is
    equivalent to assuming the ``mbpsPerEni`` heuristic from
    ``prices.json`` (1 Mbps per ENI).
    """
    table = _load_prices()["heuristic"]
    bytes_per_second_per_mbps = int(table["bytesPerSecondPerMbps"])
    mbps_per_eni = float(table["mbpsPerEni"])
    return int(
        eni_count
        * duration_minutes
        * 60
        * mbps_per_eni
        * bytes_per_second_per_mbps
    )


def compute_capture_cost_usd(
    eni_count: int,
    duration_minutes: int,
    region: Optional[str] = None,
    estimated_bytes: Optional[int] = None,
) -> float:
    """Compute the estimated USD cost of a capture using the design formula.

    Matches the formula in the design's ``Capture_Confirmation_Prompt``
    section exactly. Reads the unit prices from the bundled
    ``prices.json`` so the chat confirmation and the README cost
    estimate cannot drift (Req 17.2 and Req 14.2 share this module).

    Args:
        eni_count: The number of ENIs in ``eni_ids``.
        duration_minutes: The capture duration in minutes.
        region: AWS region for the regional per-ENI-hour price; falls
            back to the default rate when ``None`` or unknown.
        estimated_bytes: Optional override for the assumed total
            mirrored byte count. When ``None`` the heuristic from
            :func:`estimate_capture_bytes` is used.

    Returns:
        The estimated cost in USD as a ``float``.

    Raises:
        ValueError: When any of ``eni_count``, ``duration_minutes``, or
            ``estimated_bytes`` is negative.
    """
    if eni_count < 0:
        raise ValueError("eni_count must be non-negative")
    if duration_minutes < 0:
        raise ValueError("duration_minutes must be non-negative")
    if estimated_bytes is not None and estimated_bytes < 0:
        raise ValueError("estimated_bytes must be non-negative")

    table = _load_prices()["trafficMirror"]
    price_per_gb = float(table["dataPricePerGb"])
    price_per_eni_hour = _get_traffic_mirror_eni_hour_price(region)

    duration_hours = duration_minutes / 60.0
    if estimated_bytes is None:
        estimated_bytes = estimate_capture_bytes(eni_count, duration_minutes)

    eni_hours_cost = eni_count * duration_hours * price_per_eni_hour
    data_cost = (estimated_bytes / 1e9) * price_per_gb
    return eni_hours_cost + data_cost


# ---------------------------------------------------------------------------
# Capture_Idempotency_Token derivation (Req 9.21)
#
# The orchestration agent passes a Capture_Idempotency_Token to every
# ``start_capture`` invocation so that an immediate retry of the same
# prompt within the same minute does not create a duplicate
# Capture_Session. The token is a SHA-256 hex digest over
#
#     eni_ids ∥ duration_minutes ∥ user_id ∥ floor(timestamp, 1m)
#
# where ``floor(timestamp, 1m)`` is the request timestamp truncated to
# the minute (``YYYY-MM-DDTHH:MM:00Z``). Because the token is fully
# deterministic for any ``(eni_ids, duration_minutes, user_id, minute)``
# 4-tuple, two requests in the same minute compute identical tokens
# and the Network Agent's idempotency check (Req 3.15) returns the
# existing ``capture_id`` instead of starting a second capture.
# ---------------------------------------------------------------------------
_CAPTURE_IDEMPOTENCY_TOKEN_DELIMITER = "\x1f"  # ASCII unit separator (US)


def derive_capture_idempotency_token(
    eni_ids: Sequence[str],
    duration_minutes: int,
    user_id: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> str:
    """Return the SHA-256 Capture_Idempotency_Token for a ``start_capture`` call.

    The hash inputs are concatenated with the ASCII unit separator
    (``\\x1f``) to remove any chance of ambiguity at component
    boundaries (a user identifier containing a literal newline or
    comma cannot collide with an adjacent component). ``eni_ids`` is
    sorted before hashing so that ``[a, b]`` and ``[b, a]`` derive the
    same token (matching the Network Agent's idempotency-check
    semantics, which compares ENI sets rather than ordered lists).

    Args:
        eni_ids: The ENIs that will be mirrored.
        duration_minutes: The capture duration in minutes.
        user_id: The authenticated user identifier. When ``None``, the
            value is read from the per-request ContextVar populated by
            ``agent_invocation``; the empty string is used when the
            ContextVar has no value.
        timestamp: Optional request timestamp. When ``None``, the
            current UTC time is used.

    Returns:
        A 64-character lowercase hex string suitable for the
        ``idempotency_token`` parameter of ``start_capture``.

    Raises:
        ValueError: When ``eni_ids`` is empty or any element is not a
            non-empty string, or when ``duration_minutes`` is not a
            non-negative integer.
    """
    if not eni_ids:
        raise ValueError("eni_ids must be a non-empty sequence")
    cleaned: list[str] = []
    for entry in eni_ids:
        if not isinstance(entry, str) or not entry:
            raise ValueError("every eni_ids entry must be a non-empty string")
        cleaned.append(entry)
    if not isinstance(duration_minutes, int) or isinstance(duration_minutes, bool):
        raise ValueError("duration_minutes must be an int")
    if duration_minutes < 0:
        raise ValueError("duration_minutes must be non-negative")

    sorted_enis = sorted(cleaned)
    resolved_user_id = user_id if user_id is not None else _CURRENT_USER_ID.get()
    ts = timestamp if timestamp is not None else datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    minute_bucket = ts.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:00Z")

    payload = _CAPTURE_IDEMPOTENCY_TOKEN_DELIMITER.join((
        ",".join(sorted_enis),
        str(duration_minutes),
        resolved_user_id,
        minute_bucket,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Capture_Confirmation_Prompt formatting (Req 9.18, Req 17.2)
#
# The orchestration agent emits a structured natural-language prompt
# before invoking ``start_capture``. Its content is fixed by the
# design's ``Capture_Confirmation_Prompt structure`` section:
#
#   - Bulleted list of resolved ENI identifiers (with parent instance
#     IDs when attached).
#   - The capture duration in minutes (with the literal suffix
#     " (default)" appended when the 15-minute default is applied per
#     Req 16.4).
#   - The estimated cost in USD computed from the formula above.
#   - The closing sentence "Reply 'yes' to start the capture or 'no' to
#     cancel.".
#
# Centralising the formatter in Python rather than the system prompt
# guarantees that the cost figure on screen matches the figure stored
# alongside the design property test (Property 12) for the same
# (eni_count, duration_minutes) input.
# ---------------------------------------------------------------------------
_CAPTURE_DURATION_DEFAULT_MINUTES = 15

# ---------------------------------------------------------------------------
# Confirmation token sets (Task 37, Req 16.2 / 16.13)
#
# The two response sets below are the canonical, model-independent
# definitions of Affirmative_Response_Set and Negative_Response_Set
# referenced by Requirement 16. The orchestration agent classifies the
# user's reply to any yes/no Clarification_Question against these sets:
# a member of ``AFFIRMATIVE_RESPONSE_SET`` confirms, a member of
# ``NEGATIVE_RESPONSE_SET`` cancels, anything else triggers a
# single restated prompt (Req 16.2).
#
# ``_AFFIRMATIVE_RESPONSE_SET`` and ``_NEGATIVE_RESPONSE_SET`` are
# preserved as private aliases so existing callers (and tests) keep
# working — they reference the same ``frozenset`` objects.
#
# Tokens are stored lowercase and stripped of punctuation so the
# matching helpers below can compare against a normalized input
# without re-normalizing the set on every call.
# ---------------------------------------------------------------------------
AFFIRMATIVE_RESPONSE_SET: frozenset[str] = frozenset({
    "yes", "y", "ok", "okay", "sure", "confirm", "proceed", "go", "accept",
})
NEGATIVE_RESPONSE_SET: frozenset[str] = frozenset({
    "no", "n", "cancel", "abort", "stop", "nevermind",
})

# Backward-compatible private aliases — same frozenset objects.
_AFFIRMATIVE_RESPONSE_SET = AFFIRMATIVE_RESPONSE_SET
_NEGATIVE_RESPONSE_SET = NEGATIVE_RESPONSE_SET

# Trailing characters stripped from a user reply before set membership
# check. The set covers (a) the punctuation a user is likely to append
# to a one-word reply ("yes!", "no.", "ok,") AND (b) every whitespace
# character (space, tab, CR, LF, vertical tab, form feed) so that
# interleaved trailing punctuation+whitespace ("yes! ", "ok\r:") is
# stripped in a single ``rstrip`` pass without needing to alternate
# strip operations. Leading punctuation is NOT stripped — Req 16.2
# calls for trailing punctuation only — but ``str.strip()`` is still
# applied to the leading side first to remove leading whitespace.
_RESPONSE_TRAILING_TRIM = ".!?,;: \t\r\n\v\f"


# ---------------------------------------------------------------------------
# Clarification_Question priority order (Task 37, Req 16.12 / 16.13)
#
# When more than one Capture_Action parameter is missing or ambiguous in
# the same chat turn, the orchestration agent asks ONE question and
# defers the others to subsequent turns. The question it asks first is
# determined by the documented priority order: the most blocking
# parameter wins. The order is stored as a tuple (not a set) because
# its index conveys the priority ranking — index 0 is asked first.
#
# ``ENI selection`` covers any ambiguity around which ENIs to mirror
# (a missing instance/ENI/endpoint, multiple ENIs returned by
# ``list_enis``, more than 3 ENIs resolved, or some ENIs missing the
# Capture_Opt_In_Tag — every variant of "which ENIs?" sits at the top
# of the priority order). ``capture_id`` covers Pcap_Query_Action and
# stop/transform/progress when no capture context is available.
# ``duration`` covers ``start_capture`` invocations missing
# ``duration_minutes``. ``other`` is the catch-all bucket for any
# remaining missing parameter (filter_id, top_n, min_size, etc.).
# ---------------------------------------------------------------------------
CAPTURE_PARAMETER_PRIORITY_ORDER: tuple[str, ...] = (
    "eni_selection",
    "capture_id",
    "duration",
    "other",
)


def select_blocking_parameter(missing: Iterable[str]) -> Optional[str]:
    """Return the most-blocking parameter from ``missing`` per the priority order.

    The orchestration agent uses this helper to decide which
    Clarification_Question to emit when multiple parameters are
    missing in the same turn (Req 16.12). The function returns the
    first entry in :data:`CAPTURE_PARAMETER_PRIORITY_ORDER` that
    appears in ``missing``; unknown bucket names fall through to
    ``"other"`` so any new parameter the agent introduces in the
    future still gets a deterministic tie-break.

    Args:
        missing: An iterable of priority bucket names. Accepts any
            of the values in :data:`CAPTURE_PARAMETER_PRIORITY_ORDER`,
            or the empty iterable. Unknown names are coerced to
            ``"other"``.

    Returns:
        The highest-priority bucket name from ``missing``, or
        ``None`` when ``missing`` is empty or contains no buckets.
    """
    if missing is None:
        return None
    seen: set[str] = set()
    for entry in missing:
        if not isinstance(entry, str) or not entry:
            continue
        seen.add(entry if entry in CAPTURE_PARAMETER_PRIORITY_ORDER else "other")
    if not seen:
        return None
    for bucket in CAPTURE_PARAMETER_PRIORITY_ORDER:
        if bucket in seen:
            return bucket
    return None

# Compiled patterns shared by helper functions and the LLM-facing tool
# descriptions. Match the AWS standard formats: the legacy 8-character
# form and the modern 17-character form are both valid in account
# responses.
_EC2_INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")
_EC2_ENI_ID_PATTERN = re.compile(r"^eni-[0-9a-f]{8,17}$")


def _format_eni_bullet(eni_id: str, instance_id: Optional[str] = None) -> str:
    """Return one line of the Capture_Confirmation_Prompt's ENI bullet list."""
    if instance_id:
        return f"- `{eni_id}` (attached to `{instance_id}`)"
    return f"- `{eni_id}`"


def format_capture_confirmation_prompt(
    enis: Sequence[dict],
    duration_minutes: Optional[int] = None,
    region: Optional[str] = None,
    estimated_bytes: Optional[int] = None,
) -> dict:
    """Build the Capture_Confirmation_Prompt text plus structured metadata.

    Args:
        enis: A sequence of ENI descriptors. Each entry is a dict with
            at minimum an ``eni_id`` key; ``attached_instance_id`` is
            included on the bullet line when present and non-null.
        duration_minutes: The capture duration in minutes. When
            ``None``, the documented 15-minute default
            (``_CAPTURE_DURATION_DEFAULT_MINUTES``) is applied and the
            text annotates the duration with " (default)" per Req
            17.2.
        region: AWS region for cost computation; defaults to whatever
            :func:`get_region` resolves at runtime.
        estimated_bytes: Optional override for the cost estimate.

    Returns:
        A dict with two keys:

        - ``prompt_text``: the multi-line markdown string the LLM
          should emit verbatim (or near-verbatim — the LLM may re-flow
          surrounding text but should not alter the bullet list, the
          duration line, or the cost line).
        - ``metadata``: a structured object the LLM can pass to
          ``start_capture`` after the user confirms. Contains
          ``eni_ids`` (sorted), ``duration_minutes``,
          ``applied_default_15``, ``estimated_cost_usd`` (already
          rounded to 4 decimals so the printed and returned values
          agree), ``estimated_bytes``, and ``region``.

    Raises:
        ValueError: When ``enis`` is empty or any entry is missing the
            required ``eni_id`` field.
    """
    if not enis:
        raise ValueError("enis must be a non-empty sequence")

    parsed_enis: list[tuple[str, Optional[str]]] = []
    for entry in enis:
        if not isinstance(entry, dict):
            raise ValueError("every enis entry must be a dict")
        eni_id = entry.get("eni_id")
        if not isinstance(eni_id, str) or not eni_id:
            raise ValueError("every enis entry must include eni_id (non-empty string)")
        instance_id = entry.get("attached_instance_id")
        if instance_id is not None and not isinstance(instance_id, str):
            instance_id = None
        if isinstance(instance_id, str) and not instance_id:
            instance_id = None
        parsed_enis.append((eni_id, instance_id))

    applied_default_15 = duration_minutes is None
    effective_duration = (
        _CAPTURE_DURATION_DEFAULT_MINUTES if applied_default_15 else int(duration_minutes)
    )
    if effective_duration < 1 or effective_duration > 60:
        raise ValueError(
            "duration_minutes must be between 1 and 60 (Capture_Duration_Limit)"
        )

    resolved_region = region if region is not None else get_region()
    cost = compute_capture_cost_usd(
        len(parsed_enis),
        effective_duration,
        region=resolved_region,
        estimated_bytes=estimated_bytes,
    )
    cost_rounded = round(cost, 4)

    bullets = "\n".join(
        _format_eni_bullet(eni_id, inst) for eni_id, inst in parsed_enis
    )
    duration_line = (
        f"**Duration**: {effective_duration} minutes (default)"
        if applied_default_15
        else f"**Duration**: {effective_duration} minutes"
    )
    cost_line = f"**Estimated cost**: ${cost_rounded:.4f} USD"

    prompt_text = (
        "I'm about to start a VPC packet capture with the following "
        "settings:\n\n"
        "**ENIs to mirror**:\n"
        f"{bullets}\n\n"
        f"{duration_line}\n"
        f"{cost_line}\n\n"
        "Reply 'yes' to start the capture or 'no' to cancel."
    )

    return {
        "prompt_text": prompt_text,
        "metadata": {
            "eni_ids": [eni_id for eni_id, _ in parsed_enis],
            "duration_minutes": effective_duration,
            "applied_default_15": applied_default_15,
            "estimated_cost_usd": cost_rounded,
            "estimated_bytes": (
                estimated_bytes
                if estimated_bytes is not None
                else estimate_capture_bytes(len(parsed_enis), effective_duration)
            ),
            "region": resolved_region,
        },
    }


def is_affirmative_response(text: object) -> bool:
    """Return True iff ``text`` is a member of the Affirmative_Response_Set.

    Implements the canonical confirmation-token classifier referenced
    throughout the design's Clarification_Question rules (Req 16.2 and
    Req 16.13). Matching is case-insensitive, ignores surrounding
    whitespace, and ignores trailing punctuation (``.``, ``!``, ``?``,
    ``,``, ``;``, ``:``) — including interleaved trailing whitespace
    and punctuation, so ``"yes! "`` and ``"accept\r:"`` both match
    after normalization. Leading punctuation is NOT stripped — Req
    16.2 calls for trailing punctuation only.

    The orchestration agent calls this from two places:

    1. Past the Capture_Confirmation_Prompt (Req 9.18 / 17.2), to
       decide whether to invoke ``start_capture``.
    2. Past any other yes/no Clarification_Question the agent emits
       (Req 16.2: "skip the offending ENIs and proceed", "transform
       this capture first", etc.) so the caller can branch on the
       user's answer with consistent normalization.
    """
    if not isinstance(text, str):
        return False
    normalized = text.strip().lower().rstrip(_RESPONSE_TRAILING_TRIM)
    return normalized in AFFIRMATIVE_RESPONSE_SET


def is_negative_response(text: object) -> bool:
    """Return True iff ``text`` is a member of the Negative_Response_Set.

    Companion of :func:`is_affirmative_response` for the cancellation
    tokens. Same normalization rules: case-insensitive, surrounding
    whitespace ignored, trailing punctuation in
    :data:`_RESPONSE_TRAILING_TRIM` ignored — including interleaved
    trailing whitespace and punctuation.
    """
    if not isinstance(text, str):
        return False
    normalized = text.strip().lower().rstrip(_RESPONSE_TRAILING_TRIM)
    return normalized in NEGATIVE_RESPONSE_SET


# ---------------------------------------------------------------------------
# Task 38 — Chat-driven capture progress, stop, and transform UX helpers
# (Reqs 17.4, 17.5, 17.6, 17.7, 17.8)
#
# These pure helpers format the chat replies the orchestration agent
# emits during the Capture_Lifecycle conversational workflow. They are
# imported directly from the @tool implementations and from the
# ``query_network_pcap`` post-invocation hook so the LLM-facing chat
# replies stay deterministic and testable. Centralising the formatting
# in Python (rather than the system prompt) means:
#
#   - ``time_remaining_seconds`` always renders as the same human-readable
#     string for any given seconds value.
#   - Bytes are always rendered with binary units (KiB, MiB, GiB) per
#     Req 17.4.
#   - Auto_Stop replies always state explicitly that the capture was
#     stopped automatically (Req 17.6) — the LLM can paraphrase the
#     surrounding sentences but the literal phrase is fixed.
#   - The transform interim/final reply structure is identical between
#     turns, so the `formattedText` the user sees in the chatbot does
#     not drift turn-over-turn.
# ---------------------------------------------------------------------------

# Suggested follow-up commands listed after a successful capture-related
# event. Placed in a constant so the same set surfaces whenever the
# orchestration agent describes "what can I do next?". Req 17.3 lists
# the four required minimum follow-ups; we add a fifth for retransmission
# detection because it is the most useful Pcap_Query_Action when the
# user does not yet know what they're looking for.
_CAPTURE_FOLLOW_UP_COMMANDS: tuple[str, ...] = (
    "transform my capture",
    "stop my capture",
    "is my capture ready",
    "show TLS Client Hello sizes",
    "find TCP retransmissions in my capture",
)

# Pcap_Query_Action names the agent will list after a successful
# `transform_capture` so the user knows what to ask next (Req 17.7).
# Sourced from the Network Agent dispatch table; we list them by
# domain so the user can scan the list quickly.
_PCAP_QUERY_ACTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "TLS / Encryption",
        ("check_tls_hello_size",),
    ),
    (
        "TCP analysis",
        (
            "diagnose_tcp_stream",
            "reconstruct_tcp_handshake",
            "classify_tcp_resets",
            "detect_retransmissions",
            "detect_out_of_order_packets",
            "detect_zero_window",
            "analyze_tcp_options",
            "get_rtt_distribution",
            "get_request_response_latency",
        ),
    ),
    (
        "Search / Aggregation",
        (
            "search_fragmented_packets",
            "correlate_tcp_streams",
            "get_conversation_stats",
            "query_pcap",
        ),
    ),
)

# Re-used phrase for the auto-stop announcement (Req 17.6). Centralising
# the literal so any test asserting the LLM-facing copy matches a
# single source of truth.
_AUTO_STOP_PHRASE = (
    "stopped automatically when its capture deadline elapsed (auto-stop)"
)

# Glossary values for the three Capture_Action guardrails (Req 4 +
# Req 17.11). Centralised here so the chat reply formatter and the
# system prompt agree on a single source of truth — and so a future
# limit change touches exactly one place.
#
# Keep these in sync with the Network Agent constants
# ``CAPTURE_CONCURRENCY_LIMIT`` in ``agents/network-agent/main.py``
# and ``_CAPTURE_ENI_LIMIT`` plus the ``[1, 60]`` range guard in
# ``agents/network-agent/validation.py``.
_CAPTURE_CONCURRENCY_LIMIT = 5
_CAPTURE_ENI_LIMIT = 3
_CAPTURE_DURATION_LIMIT_MINUTES = 60

# Step Functions terminal status values, used by ``poll_transform_execution``
# to decide when polling can return.
_TRANSFORM_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"})

# Polling cadence and total budget for ``poll_transform_execution`` per
# Req 17.7 ("at intervals of at most 30 seconds for up to 10 minutes
# total"). The cadence is conservative — most pcap-to-Parquet jobs
# finish well under a minute for a single 15-minute capture.
_TRANSFORM_POLL_INTERVAL_SECONDS = 15
_TRANSFORM_POLL_TIMEOUT_SECONDS = 10 * 60  # 10 minutes


def format_bytes_binary(num_bytes: object) -> str:
    """Format ``num_bytes`` as a binary-unit string (B, KiB, MiB, GiB, TiB).

    Per Req 17.4, ``bytes_uploaded`` must be rendered with binary
    units. Returns the largest unit at which the value is < 1024 with
    one decimal place, falling back to the raw bytes for values < 1024.
    Negative values render as ``"0 B"`` because S3 byte counts are
    never negative; non-numeric input also renders as ``"0 B"`` so a
    missing/null upstream field never crashes the LLM-facing reply.

    Args:
        num_bytes: Either an ``int``/``float`` byte count, or any
            other object (treated as ``0``).

    Returns:
        A human-readable string with the largest binary unit at which
        the integer part is < 1024.
    """
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return "0 B"
    if value < 0 or value != value:  # value != value is the NaN check
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {units[idx]}"


def format_time_remaining(seconds: object) -> str:
    """Format ``time_remaining_seconds`` as a human-readable duration.

    Per Req 17.4:

    - When ``seconds`` is positive, render as e.g. "8 minutes
      remaining" or "1 hour 5 minutes remaining".
    - When ``seconds`` is negative or zero, render as the literal
      ``"deadline passed"``.

    Args:
        seconds: Either an integer/float seconds value (positive or
            negative), or any other object (treated as ``0``).

    Returns:
        A human-readable string suitable for direct inclusion in a
        chat reply.
    """
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "deadline passed"
    if total <= 0:
        return "deadline passed"

    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, secs = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if not parts:
        # Only seconds (under a minute). Round up to "less than a
        # minute" rather than printing "0 minutes" so the user knows
        # the capture is about to end.
        return "less than a minute remaining"
    # Cap to two units so e.g. "1 day 5 hours remaining" not
    # "1 day 5 hours 3 minutes remaining".
    return " ".join(parts[:2]) + " remaining"


def _parse_iso_timestamp(value: object) -> Optional[datetime]:
    """Best-effort parse of an ISO 8601 timestamp into a UTC ``datetime``.

    Accepts the AgentCore-typical ``YYYY-MM-DDTHH:MM:SSZ`` and the
    fractional/offset variants. Returns ``None`` on any parse error
    so callers can fall back gracefully.
    """
    if not isinstance(value, str) or not value:
        return None
    candidate = value
    # ``datetime.fromisoformat`` rejects the trailing ``Z`` UTC
    # marker before Python 3.11; replace it pre-emptively.
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_elapsed_duration(
    start_time: object,
    end_time: Optional[datetime] = None,
) -> str:
    """Format the elapsed duration between ``start_time`` and ``end_time``.

    Per Req 17.5, the stop-capture confirmation must include an
    elapsed duration computed as ``now - start_time``. ``end_time``
    defaults to the current UTC time so callers get the standard
    behaviour for free; tests pass an explicit value to keep the
    output deterministic.

    Args:
        start_time: ISO 8601 string (or any other object — non-strings
            and unparseable strings render as the literal
            ``"unknown"``).
        end_time: Optional override for the comparison anchor.

    Returns:
        Either ``"<X> hour(s) <Y> minute(s)"``, ``"<Y> minute(s) <Z>
        second(s)"``, ``"<Z> second(s)"``, or ``"unknown"``.
    """
    parsed_start = _parse_iso_timestamp(start_time)
    if parsed_start is None:
        return "unknown"
    anchor = (end_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    delta = anchor - parsed_start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        # Defensive: a clock skew or stale start_time should never
        # produce a negative elapsed string.
        total_seconds = 0

    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return (
            f"{hours} hour{'s' if hours != 1 else ''} "
            f"{minutes} minute{'s' if minutes != 1 else ''}"
        )
    if minutes:
        return (
            f"{minutes} minute{'s' if minutes != 1 else ''} "
            f"{seconds} second{'s' if seconds != 1 else ''}"
        )
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def _format_follow_up_commands_bullet_list() -> str:
    """Return the suggested follow-up commands as a markdown bullet list."""
    return "\n".join(f"- `{cmd}`" for cmd in _CAPTURE_FOLLOW_UP_COMMANDS)


def _format_pcap_query_action_listing() -> str:
    """Return the Pcap_Query_Action commands grouped by domain.

    Used by :func:`format_transform_final_reply` to tell the user what
    queries are now available against the freshly transformed capture
    (Req 17.7). The grouping matches the design's
    "Action-specific output schemas" table for easier cross-reference.
    """
    sections: list[str] = []
    for label, actions in _PCAP_QUERY_ACTION_GROUPS:
        bullets = "\n".join(f"- `{a}`" for a in actions)
        sections.append(f"**{label}**:\n{bullets}")
    return "\n\n".join(sections)


def format_capture_progress_reply(progress_data: object) -> str:
    """Render a chat reply for a successful ``get_capture_progress`` response.

    Implements Req 17.4: state the ``status``, render
    ``time_remaining_seconds`` as a human-readable duration ("8
    minutes remaining" / "deadline passed"), include the
    ``s3_objects_uploaded_count`` and ``bytes_uploaded`` (formatted
    with binary units).

    The reply also surfaces the Auto_Stop case (Req 17.6) when
    ``status=stopped`` AND ``stopped_reason=auto_stop_deadline``: the
    literal phrase ``"stopped automatically when its capture deadline
    elapsed (auto-stop)"`` is included, and a follow-up offer to run
    ``transform_capture`` is appended as a yes/no Clarification_Question.

    Args:
        progress_data: The ``data`` field of a successful
            ``get_capture_progress`` response, or any other object —
            non-dict input renders a graceful "no progress data
            available" reply.

    Returns:
        A multi-line markdown string ready for direct inclusion in
        the chat output. Returns an empty string only when the input
        is unequivocally empty/missing the required fields.
    """
    if not isinstance(progress_data, dict):
        return ""

    capture_id = progress_data.get("capture_id")
    status = progress_data.get("status")
    stopped_reason = progress_data.get("stopped_reason")
    time_remaining = progress_data.get("time_remaining_seconds")
    objects = progress_data.get("s3_objects_uploaded_count")
    bytes_uploaded = progress_data.get("bytes_uploaded")
    deadline = progress_data.get("deadline")

    if not isinstance(capture_id, str) or not capture_id:
        return ""

    lines: list[str] = []
    lines.append(f"Capture `{capture_id}` progress:")
    if isinstance(status, str) and status:
        lines.append(f"- **Status**: `{status}`")
    if deadline:
        lines.append(f"- **Deadline**: {deadline}")

    # Time-remaining handling. ``deadline passed`` is rendered when
    # the value is non-positive — but if the capture is already
    # `stopped`, the time-remaining line is misleading, so we suppress
    # it and let the auto-stop section below carry the lifecycle
    # message instead.
    if status not in {"stopped", "stopping_failed", "transformed", "queryable"}:
        lines.append(f"- **Time remaining**: {format_time_remaining(time_remaining)}")

    # S3 ingest statistics — always included so the user can confirm
    # the collector is actually receiving traffic.
    objects_str = (
        str(int(objects))
        if isinstance(objects, (int, float)) and objects == objects  # NaN check
        else "0"
    )
    lines.append(f"- **Objects uploaded**: {objects_str}")
    lines.append(f"- **Bytes uploaded**: {format_bytes_binary(bytes_uploaded)}")

    # Req 17.6 — Auto_Stop awareness. When the row is `stopped` with
    # `stopped_reason=auto_stop_deadline`, the chat reply must state
    # explicitly that the capture stopped automatically and offer
    # `transform_capture` via a yes/no Clarification_Question.
    if status == "stopped" and stopped_reason == "auto_stop_deadline":
        lines.append("")
        lines.append(
            f"Capture `{capture_id}` was {_AUTO_STOP_PHRASE}. "
            "Should I run `transform_capture` now to convert its raw "
            "pcap files into a queryable Athena partition? (yes / no)"
        )

    return "\n".join(lines)


def format_stop_capture_reply(
    stop_data: object,
    start_time: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> str:
    """Render the chat reply for a successful ``stop_capture`` invocation.

    Implements Req 17.5: confirm the stop and report the elapsed
    capture duration computed as ``now - start_time``. The
    ``start_time`` is read from the response's ``data`` when present
    and falls back to the explicit ``start_time`` argument so callers
    that only have a Capture_Conversation_Context entry can still
    render the elapsed line.

    Args:
        stop_data: The ``data`` field of a ``stop_capture`` response.
        start_time: Optional explicit ISO 8601 start_time override.
        now: Optional reference time for deterministic tests.

    Returns:
        A multi-line markdown string ending with the suggested
        follow-up commands (transform / progress / etc.).
    """
    if not isinstance(stop_data, dict):
        return "Capture stopped."

    capture_id = stop_data.get("capture_id")
    resolved_start = stop_data.get("start_time") or start_time
    stopped_reason = stop_data.get("stopped_reason")

    elapsed_str = format_elapsed_duration(resolved_start, end_time=now)

    if isinstance(capture_id, str) and capture_id:
        first = f"Capture `{capture_id}` has been stopped."
    else:
        first = "Capture has been stopped."

    lines = [first]
    if elapsed_str != "unknown":
        lines.append(f"- **Elapsed**: {elapsed_str}")
    else:
        lines.append("- **Elapsed**: unknown (start_time unavailable)")
    if isinstance(stopped_reason, str) and stopped_reason:
        # ``stopped_reason`` is informational only — surface it so
        # the user knows whether the stop was user-initiated or
        # the result of an auto_stop_deadline trigger that arrived
        # while the user was typing.
        if stopped_reason == "auto_stop_deadline":
            lines.append(f"- **Reason**: {_AUTO_STOP_PHRASE}")
        else:
            lines.append(f"- **Reason**: `{stopped_reason}`")

    lines.append("")
    lines.append(
        "Now that the capture has stopped, you can ask me to:"
    )
    lines.append(_format_follow_up_commands_bullet_list())
    return "\n".join(lines)


def format_transform_interim_reply(
    transform_data: object,
    capture_id: Optional[str] = None,
) -> str:
    """Render the interim reply emitted within 5 seconds of ``transform_capture``.

    Per Req 17.7, the interim reply must contain the Step Functions
    execution ARN. We also include the ``capture_id`` (when known)
    and a one-liner explaining what happens next so the user is not
    left wondering whether the chat is hung.

    Args:
        transform_data: The ``data`` field of a ``transform_capture``
            response. Must contain ``transform_execution_arn``.
        capture_id: Optional capture id; when missing we attempt to
            read it from ``transform_data``.

    Returns:
        A multi-line markdown string for the interim reply.
    """
    execution_arn = ""
    cap = capture_id
    if isinstance(transform_data, dict):
        for key in ("transform_execution_arn", "execution_arn", "executionArn"):
            value = transform_data.get(key)
            if isinstance(value, str) and value:
                execution_arn = value
                break
        if cap is None:
            value = transform_data.get("capture_id")
            if isinstance(value, str) and value:
                cap = value

    if cap:
        first = (
            f"Started the transformation pipeline for capture `{cap}`."
        )
    else:
        first = "Started the transformation pipeline."

    lines = [first]
    if execution_arn:
        lines.append(f"- **Execution ARN**: `{execution_arn}`")
    lines.append("")
    lines.append(
        "I'll keep checking the workflow status and let you know "
        "as soon as the Athena partition is queryable. Most "
        "transformations finish in a few minutes."
    )
    return "\n".join(lines)


def format_transform_final_reply(
    *,
    success: bool,
    capture_id: Optional[str],
    failed_task: Optional[str] = None,
    error_reason: Optional[str] = None,
    timed_out: bool = False,
) -> str:
    """Render the final reply for a completed (or timed-out) transformation.

    Per Req 17.7:

    - On success, state that the Pcap_Athena_Table is now queryable
      for the supplied ``capture_id`` and list the available
      Pcap_Query_Action commands.
    - On Step Functions failure, surface the failed task name and
      reason so the user knows where in the pipeline the failure
      occurred (per the design's "Step Functions task failure"
      behaviour, the state machine emits ``{ failed_task,
      error_reason }``).

    Args:
        success: Whether the Step Functions execution reached
            ``SUCCEEDED``.
        capture_id: The capture id (used in the success message).
        failed_task: Optional task name (only used when ``success`` is
            ``False``).
        error_reason: Optional failure reason (only used when
            ``success`` is ``False``).
        timed_out: Whether ``poll_transform_execution`` exhausted its
            10-minute budget before reaching a terminal state.

    Returns:
        A multi-line markdown string for the final reply.
    """
    if success and capture_id:
        return (
            f"Capture `{capture_id}` is now queryable. The Athena "
            "partition is populated and you can ask me to run any "
            "of these queries against it:\n\n"
            f"{_format_pcap_query_action_listing()}"
        )
    if success and not capture_id:
        return (
            "The transformation pipeline completed successfully and "
            "the Athena partition is queryable. You can ask me to "
            "run any Pcap_Query_Action against it now."
        )
    if timed_out:
        # Polling budget exhausted but the execution may still be
        # running — point the user at `get_capture_progress` and the
        # AWS Step Functions console for ground truth.
        cap_phrase = f"capture `{capture_id}` " if capture_id else ""
        return (
            f"The transformation for {cap_phrase}is still running "
            "after 10 minutes of polling. I've stopped waiting so "
            "the chat session does not stall, but the workflow may "
            "still finish in the background. Ask me again in a few "
            "minutes (`is my capture ready`) or check the AWS Step "
            "Functions console for the execution status."
        )
    # Failed path — include whatever diagnostic information we have.
    cap_phrase = f"capture `{capture_id}`" if capture_id else "the capture"
    parts = [f"The transformation pipeline for {cap_phrase} failed."]
    if isinstance(failed_task, str) and failed_task:
        parts.append(f"- **Failed task**: `{failed_task}`")
    if isinstance(error_reason, str) and error_reason:
        # Truncate very long Step Functions error strings so the chat
        # reply stays readable.
        truncated = error_reason if len(error_reason) <= 1000 else (
            error_reason[:1000] + "… (truncated)"
        )
        parts.append(f"- **Error**: {truncated}")
    parts.append("")
    parts.append(
        "Ask me to run `transform my capture` to retry, or check "
        "the AWS Step Functions console for the full execution "
        "history."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pcap_Query_Action interpretation hints (Req 17.10)
#
# When a Pcap_Query_Action returns rows, the orchestration agent's chat
# reply must include a one-sentence interpretation generated by the
# orchestration model. The hints below are deterministic
# action-specific seeds the formatter consults so the LLM always has
# a concrete observation to anchor its sentence on (the requirement's
# example: "potential TLS Client Hello fragmentation when
# `check_tls_hello_size` returns rows where `frame_size > 1400`").
#
# Each hint is a callable that receives the response's ``data`` field
# and returns either a hint string OR ``None`` when no rule matches.
# The formatter falls back to a generic interpretation in that case.
# Hints are intentionally short and end with a period so the LLM can
# concatenate them into surrounding prose without re-flowing.
# ---------------------------------------------------------------------------
_TLS_FRAGMENTATION_THRESHOLD_BYTES = 1400


def _data_rows(data: object) -> list:
    """Extract the row list from a Pcap_Query_Action response payload.

    Pcap query handlers return rows under varying keys. We probe the
    common ones (``rows``, ``records``, ``items``, ``streams``) and
    fall back to ``[]`` so callers can iterate without ``None``-guards.
    """
    if not isinstance(data, dict):
        return []
    for key in ("rows", "records", "items", "streams", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _hint_check_tls_hello_size(data: object) -> Optional[str]:
    """Flag potential TLS Client Hello fragmentation (Req 17.10 example)."""
    rows = _data_rows(data)
    if not rows:
        return None
    over = 0
    fragmented = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        size = row.get("frame_size")
        if isinstance(size, (int, float)) and size > _TLS_FRAGMENTATION_THRESHOLD_BYTES:
            over += 1
        fragments = row.get("fragment_count")
        if isinstance(fragments, (int, float)) and fragments > 1:
            fragmented += 1
    parts: list[str] = []
    if over:
        parts.append(
            f"{over} TLS Client Hello frame{'s' if over != 1 else ''} "
            f"exceed {_TLS_FRAGMENTATION_THRESHOLD_BYTES} bytes — this often "
            "fragments the SNI across TCP segments and breaks stateful "
            "firewall SNI inspection"
        )
    if fragmented:
        parts.append(
            f"{fragmented} Client Hello{'s' if fragmented != 1 else ''} "
            "had fragment_count > 1 (already fragmented in capture)"
        )
    if not parts:
        return None
    return "; ".join(parts) + "."


def _hint_search_fragmented_packets(data: object) -> Optional[str]:
    """Surface the count of packets at or above the fragmentation threshold."""
    rows = _data_rows(data)
    if not rows:
        return None
    return (
        f"{len(rows)} packet{'s' if len(rows) != 1 else ''} at or above the "
        "fragmentation threshold — large frames close to or above the path "
        "MTU often correlate with PMTU discovery failures or TLS record "
        "fragmentation."
    )


def _hint_detect_retransmissions(data: object) -> Optional[str]:
    """Highlight the top destination by retransmission count."""
    rows = _data_rows(data)
    if not rows:
        return None
    head = rows[0]
    if not isinstance(head, dict):
        return None
    dst_ip = head.get("dst_ip") or head.get("destination_ip")
    dst_port = head.get("dst_port") or head.get("destination_port")
    count = (
        head.get("retransmission_count")
        or head.get("retransmissions")
        or head.get("count")
    )
    if dst_ip and isinstance(count, (int, float)) and count:
        suffix = f":{dst_port}" if dst_port else ""
        return (
            f"Top retransmission destination is {dst_ip}{suffix} with "
            f"{int(count)} retransmissions — this is typically a sign of "
            "packet loss, congestion, or an intermittent middlebox drop."
        )
    return None


def _hint_classify_tcp_resets(data: object) -> Optional[str]:
    """Summarise where the resets originated."""
    rows = _data_rows(data)
    if not rows:
        return None
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = row.get("reset_origin_side")
        if isinstance(side, str) and side:
            counts[side] = counts.get(side, 0) + 1
    if not counts:
        return f"{len(rows)} TCP RST packet{'s' if len(rows) != 1 else ''} observed."
    parts = ", ".join(f"{count} {side}" for side, count in sorted(counts.items()))
    return (
        f"{sum(counts.values())} TCP RST packets observed ({parts}) — RSTs "
        "from a `middlebox` origin commonly indicate a stateful firewall, "
        "load balancer, or NAT terminating the connection."
    )


def _hint_detect_zero_window(data: object) -> Optional[str]:
    """Call out the worst zero-window stall."""
    rows = _data_rows(data)
    if not rows:
        return None
    head = rows[0]
    if not isinstance(head, dict):
        return None
    dur = head.get("zero_window_total_duration_ms")
    stream = head.get("stream_id")
    if isinstance(dur, (int, float)) and dur > 0:
        stream_phrase = f" on stream `{stream}`" if isinstance(stream, str) and stream else ""
        return (
            f"Worst zero-window stall is {int(dur)} ms{stream_phrase} — "
            "the receiver advertised a zero TCP window, blocking the sender "
            "until the application drained its socket buffer."
        )
    return None


def _hint_get_conversation_stats(data: object) -> Optional[str]:
    """Highlight the top talker."""
    rows = _data_rows(data)
    if not rows:
        return None
    head = rows[0]
    if not isinstance(head, dict):
        return None
    src = head.get("source") or head.get("src_ip")
    dst = head.get("destination") or head.get("dst_ip")
    total = head.get("total_bytes") or head.get("bytes")
    if src and dst and isinstance(total, (int, float)):
        return (
            f"Top conversation is {src} → {dst} carrying "
            f"{format_bytes_binary(total)}."
        )
    return None


# Action-name → hint-callable mapping. Ordered to match
# ``_PCAP_QUERY_ACTION_GROUPS`` so the source of truth is easy to
# scan; missing entries fall through to the generic-row-count hint
# in :func:`format_pcap_query_action_reply`.
_PCAP_QUERY_INTERPRETATION_HINTS: dict[str, callable] = {
    "check_tls_hello_size": _hint_check_tls_hello_size,
    "search_fragmented_packets": _hint_search_fragmented_packets,
    "detect_retransmissions": _hint_detect_retransmissions,
    "classify_tcp_resets": _hint_classify_tcp_resets,
    "detect_zero_window": _hint_detect_zero_window,
    "get_conversation_stats": _hint_get_conversation_stats,
}


# Mapping of upstream ``errorCategory`` plus message-substring rules
# to the canonical guardrail key the formatter uses (Req 17.11).
# ``capture_concurrency_limit`` is a dedicated ``error_category`` in
# the Network Agent (see ``agents/network-agent/main.py``), but the
# ENI-count and duration-range violations are reported as
# ``invalid_parameter`` carrying the limit name in the error message.
# The classifier inspects the message verbatim so a future Network
# Agent change to a dedicated ``error_category`` value still routes
# to the same chat reply.
_GUARDRAIL_CONCURRENCY = "Capture_Concurrency_Limit"
_GUARDRAIL_ENI = "Capture_Eni_Limit"
_GUARDRAIL_DURATION = "Capture_Duration_Limit"


def _classify_guardrail_violation(error_category: object, error_text: object) -> Optional[str]:
    """Identify which Capture_*_Limit (if any) was hit by a rejection."""
    cat = error_category if isinstance(error_category, str) else ""
    msg = error_text if isinstance(error_text, str) else ""
    if cat == "capture_concurrency_limit" or _GUARDRAIL_CONCURRENCY in msg:
        return _GUARDRAIL_CONCURRENCY
    if cat == "capture_eni_limit" or _GUARDRAIL_ENI in msg:
        return _GUARDRAIL_ENI
    if cat == "capture_duration_limit" or _GUARDRAIL_DURATION in msg:
        return _GUARDRAIL_DURATION
    return None


def format_guardrail_violation_reply(
    guardrail: str,
    *,
    error_text: Optional[str] = None,
    active_capture_count: Optional[int] = None,
    eni_count: Optional[int] = None,
    duration_minutes: Optional[int] = None,
) -> str:
    """Render the chat reply for a Capture_Action rejected by a guardrail.

    Implements Req 17.11. The reply has the same shape regardless of
    which limit was hit:

    1. A first line stating which limit was hit AND the limit value
       from the corresponding glossary entry (so the user knows the
       exact number from this conversation alone).
    2. The upstream error message verbatim (when provided) so the
       user can read the Network Agent's own framing.
    3. A bullet list of the user's options.
    4. A closing yes/no Clarification_Question offering to invoke
       the chosen option.

    The frontend is expected to render the result as plain markdown
    (Req 17.12 — no new rendering component is introduced).

    Args:
        guardrail: One of :data:`_GUARDRAIL_CONCURRENCY`,
            :data:`_GUARDRAIL_ENI`, :data:`_GUARDRAIL_DURATION`. Any
            other value falls through to a generic refusal.
        error_text: The Network Agent's verbatim ``error`` message
            when available — included so the user can see the
            offending input.
        active_capture_count: Optional context for the
            Capture_Concurrency_Limit reply (e.g. the number of
            captures currently active).
        eni_count: Optional context for the Capture_Eni_Limit reply
            (e.g. the number of ENIs the user requested).
        duration_minutes: Optional context for the
            Capture_Duration_Limit reply (e.g. the duration the user
            requested).

    Returns:
        A multi-line markdown string ready to surface to the user.
    """
    lines: list[str] = []

    if guardrail == _GUARDRAIL_CONCURRENCY:
        lines.append(
            f"`start_capture` was rejected: **{_GUARDRAIL_CONCURRENCY}** is "
            f"{_CAPTURE_CONCURRENCY_LIMIT} simultaneous captures."
        )
        if isinstance(active_capture_count, int) and active_capture_count >= 0:
            lines.append(
                f"There are currently {active_capture_count} active "
                f"capture{'s' if active_capture_count != 1 else ''} in "
                "your account."
            )
        if isinstance(error_text, str) and error_text:
            lines.append(f"- **Network Agent reason**: {error_text}")
        lines.append("")
        lines.append("You can:")
        lines.append(
            "- Stop one of the active captures to free a concurrency slot, "
            "then retry."
        )
        lines.append(
            "- Ask me to `list captures` so you can pick which active "
            "capture to stop."
        )
        lines.append(
            "- Wait for an in-flight capture to reach its deadline (the "
            "Auto_Stop_Schedule will free its slot automatically)."
        )
        lines.append("")
        lines.append(
            "Should I list your active captures so you can pick one to "
            "stop? (yes / no)"
        )
        return "\n".join(lines)

    if guardrail == _GUARDRAIL_ENI:
        lines.append(
            f"`start_capture` was rejected: **{_GUARDRAIL_ENI}** is "
            f"{_CAPTURE_ENI_LIMIT} ENIs per capture."
        )
        if isinstance(eni_count, int) and eni_count > 0:
            lines.append(
                f"You asked to mirror {eni_count} ENIs in a single "
                "capture, which exceeds the limit."
            )
        if isinstance(error_text, str) and error_text:
            lines.append(f"- **Network Agent reason**: {error_text}")
        lines.append("")
        lines.append("You can:")
        lines.append(
            f"- Pick at most {_CAPTURE_ENI_LIMIT} of the ENIs and start "
            "a single capture."
        )
        lines.append(
            "- Split the request into multiple captures (each capture can "
            f"mirror up to {_CAPTURE_ENI_LIMIT} ENIs, and you can run up "
            f"to {_CAPTURE_CONCURRENCY_LIMIT} captures concurrently — "
            f"{_GUARDRAIL_CONCURRENCY})."
        )
        lines.append("")
        lines.append(
            "Should I split the request into multiple captures of up to "
            f"{_CAPTURE_ENI_LIMIT} ENIs each? (yes / no)"
        )
        return "\n".join(lines)

    if guardrail == _GUARDRAIL_DURATION:
        lines.append(
            f"`start_capture` was rejected: **{_GUARDRAIL_DURATION}** is "
            f"{_CAPTURE_DURATION_LIMIT_MINUTES} minutes per capture."
        )
        if isinstance(duration_minutes, int) and duration_minutes > 0:
            lines.append(
                f"You asked for {duration_minutes} minutes, which exceeds "
                "the limit."
            )
        if isinstance(error_text, str) and error_text:
            lines.append(f"- **Network Agent reason**: {error_text}")
        lines.append("")
        lines.append("You can:")
        lines.append(
            f"- Lower the duration to at most "
            f"{_CAPTURE_DURATION_LIMIT_MINUTES} minutes and retry."
        )
        lines.append(
            f"- Run several back-to-back captures of up to "
            f"{_CAPTURE_DURATION_LIMIT_MINUTES} minutes each to cover a "
            "longer observation window."
        )
        lines.append("")
        lines.append(
            f"Should I retry with the documented "
            f"{_CAPTURE_DURATION_LIMIT_MINUTES}-minute maximum instead? "
            "(yes / no)"
        )
        return "\n".join(lines)

    # Unknown guardrail — fall through to a generic reply that still
    # surfaces the upstream message so the user is not left guessing.
    lines.append(
        "`start_capture` was rejected by a Network Agent guardrail."
    )
    if isinstance(error_text, str) and error_text:
        lines.append(f"- **Network Agent reason**: {error_text}")
    lines.append("")
    lines.append(
        "Adjust the offending parameter and retry, or ask me for "
        "guidance on the documented Capture_*_Limit values."
    )
    return "\n".join(lines)


def format_pcap_query_action_reply(
    *,
    action: str,
    capture_id: Optional[str],
    data: object,
    formatted_text: Optional[str] = None,
) -> str:
    """Render the chat reply for a successful Pcap_Query_Action result set.

    Implements Req 17.10. Every reply includes:

    - The source ``capture_id`` enclosed in a markdown inline code span.
    - The ``action`` name (also in a code span) so the user knows which
      query produced the rows.
    - A one-sentence interpretation of the result. Concrete
      observations come from a deterministic per-action hint
      (``_PCAP_QUERY_INTERPRETATION_HINTS``); unmapped actions fall
      back to a generic row-count interpretation. The orchestration
      model is expected to expand this into surrounding prose without
      altering the literal hint text.
    - The Network Agent's existing ``formattedText`` (when supplied),
      because it carries the structured tabular preview the user
      already expects to see.

    The reply is intentionally short and self-contained so the
    Cloudscape markdown renderer can present it without any new
    component (Req 17.12).

    Args:
        action: The Pcap_Query_Action name (e.g. ``check_tls_hello_size``).
        capture_id: Source capture id; rendered with markdown inline code.
            Falls back to a placeholder when missing.
        data: The ``data`` field of the Network Agent response.
        formatted_text: Optional ``formattedText`` from the Network Agent
            envelope to include verbatim.

    Returns:
        A multi-line markdown string.
    """
    cap_phrase = (
        f"`{capture_id}`" if isinstance(capture_id, str) and capture_id
        else "the source capture"
    )
    action_phrase = f"`{action}`" if isinstance(action, str) and action else "the query"
    rows = _data_rows(data)
    rows_count = len(rows)

    lines: list[str] = []
    lines.append(f"Capture {cap_phrase} — {action_phrase} result:")

    interpretation: Optional[str] = None
    hint_fn = _PCAP_QUERY_INTERPRETATION_HINTS.get(action) if isinstance(action, str) else None
    if hint_fn is not None:
        try:
            interpretation = hint_fn(data)
        except Exception:  # noqa: BLE001 — hints must never fail the chat reply
            interpretation = None
    if interpretation:
        lines.append(f"- {interpretation}")
    else:
        # Generic interpretation: state how many rows were returned and
        # invite the user to ask for a deeper analysis. Always
        # actionable — never leaves the LLM without a sentence.
        if rows_count > 0:
            lines.append(
                f"- {rows_count} row{'s' if rows_count != 1 else ''} returned; "
                "ask me to drill into a specific row, stream, or endpoint "
                "for a deeper analysis."
            )
        else:
            # Empty rows are normally handled by
            # :func:`format_empty_pcap_query_offer`, but include a
            # safety fallback here for actions whose hint didn't match
            # and whose data didn't reach the empty-detector.
            lines.append(
                "- No rows returned. The capture may be empty for the "
                "supplied filters, or the partition may not be transformed yet."
            )

    if isinstance(formatted_text, str) and formatted_text:
        lines.append("")
        lines.append(formatted_text)

    return "\n".join(lines)


def format_empty_pcap_query_offer(
    capture_id: Optional[str],
    action: Optional[str],
) -> str:
    """Render the empty-data offer to run ``transform_capture`` first.

    Per Req 17.8 (and the design's Clarification_Question rules), when
    a Pcap_Query_Action returns success=true with empty rows because
    the capture has not been transformed yet, the orchestration agent
    offers ``transform_capture`` via a yes/no Clarification_Question
    and, on yes, re-issues the original action.

    The reply is intentionally short — a single sentence framing
    the question — so the LLM can flow it inline with whatever
    intro text it generated explaining the empty result.

    Args:
        capture_id: Capture id (rendered with markdown inline code).
        action: The original Pcap_Query_Action name.

    Returns:
        A single-line markdown string ending with "(yes / no)".
    """
    cap_phrase = (
        f"Capture `{capture_id}`" if isinstance(capture_id, str) and capture_id
        else "This capture"
    )
    action_phrase = (
        f" `{action}` " if isinstance(action, str) and action
        else " the requested query "
    )
    return (
        f"{cap_phrase} does not have any queryable rows yet. Should "
        f"I run `transform_capture` first to populate the Athena "
        f"partition, then re-run{action_phrase}automatically? "
        f"(yes / no)"
    )


# ---------------------------------------------------------------------------
# Support_Case_Investigation constants (Task 41, Reqs 20.1-20.14)
# ---------------------------------------------------------------------------

#: Case_Id_Format — matches standard AWS Support case IDs
#: (case-XXXXXXXXXXXX-XXXX-XXXXXX) and legacy numeric IDs (8+ digits).
CASE_ID_STANDARD_RE = re.compile(r"case-\d{12}-\d{4}-\d{6}", re.IGNORECASE)
CASE_ID_LEGACY_RE = re.compile(r"\b\d{8,}\b")

#: Mapping of error signature keywords to the Pcap_Query_Action subset
#: that should be proactively invoked (Req 20.7).
SUPPORT_CASE_ERROR_ACTION_MAP: dict[str, list[str]] = {
    # Connection reset patterns
    "connection reset": ["classify_tcp_resets"],
    "reset by peer": ["classify_tcp_resets"],
    "rst": ["classify_tcp_resets"],
    "econnreset": ["classify_tcp_resets"],
    # Connection timeout patterns
    "connection timed out": ["reconstruct_tcp_handshake"],
    "connection timeout": ["reconstruct_tcp_handshake"],
    "timeout": ["reconstruct_tcp_handshake"],
    "etimedout": ["reconstruct_tcp_handshake"],
    "connect timeout": ["reconstruct_tcp_handshake"],
    # TLS handshake failure patterns
    "tls handshake": ["check_tls_hello_size"],
    "ssl handshake": ["check_tls_hello_size"],
    "certificate": ["check_tls_hello_size"],
    "tls error": ["check_tls_hello_size"],
    "ssl error": ["check_tls_hello_size"],
    "handshake failure": ["check_tls_hello_size"],
    "client hello": ["check_tls_hello_size"],
    # DNS resolution failure patterns
    "dns resolution": ["get_conversation_stats"],
    "name resolution": ["get_conversation_stats"],
    "nxdomain": ["get_conversation_stats"],
    "could not resolve": ["get_conversation_stats"],
    # HTTP 5xx with backend connection language
    "502 bad gateway": ["classify_tcp_resets", "reconstruct_tcp_handshake"],
    "503 service unavailable": ["classify_tcp_resets"],
    "504 gateway timeout": ["reconstruct_tcp_handshake"],
    "backend connection": ["classify_tcp_resets", "reconstruct_tcp_handshake"],
}

#: Trusted Advisor categories relevant to Support_Case_Investigation (Req 20.8).
SUPPORT_CASE_TA_CATEGORIES = frozenset({"security", "performance", "fault_tolerance"})


def detect_case_id(text: str) -> Optional[str]:
    """Extract a Case_Id_Format string from user input.

    Returns the first match of either the standard AWS Support case ID
    format (case-XXXXXXXXXXXX-XXXX-XXXXXX) or the legacy numeric format
    (8+ digits). Returns None when no match is found.
    """
    if not isinstance(text, str):
        return None
    # Try standard format first (more specific)
    match = CASE_ID_STANDARD_RE.search(text)
    if match:
        return match.group(0)
    # Try legacy numeric format
    match = CASE_ID_LEGACY_RE.search(text)
    if match:
        return match.group(0)
    return None


def contains_case_trigger(text: str) -> bool:
    """Return True if text contains a support case investigation trigger.

    Matches phrasings like "support case", "case", "ticket", or
    "investigate this case" followed by or containing a Case_Id_Format
    identifier (Req 20.1).
    """
    if not isinstance(text, str):
        return False
    # Must contain a case ID AND a trigger phrase
    has_case_id = detect_case_id(text) is not None
    trigger_phrases = (
        "support case", "investigate", "case", "ticket",
        "look into case", "check case", "analyze case",
    )
    has_trigger = any(phrase in text.lower() for phrase in trigger_phrases)
    return has_case_id and has_trigger


def match_error_signatures_to_actions(error_signatures: list) -> list[str]:
    """Map error signatures from a support case to Pcap_Query_Actions.

    Returns a deduplicated list of Pcap_Query_Action names that should
    be proactively invoked based on the error patterns found in the
    case (Req 20.7).
    """
    if not isinstance(error_signatures, list):
        return []
    actions: list[str] = []
    seen: set[str] = set()
    for sig in error_signatures:
        if not isinstance(sig, str):
            continue
        sig_lower = sig.lower()
        for pattern, action_list in SUPPORT_CASE_ERROR_ACTION_MAP.items():
            if pattern in sig_lower:
                for action in action_list:
                    if action not in seen:
                        actions.append(action)
                        seen.add(action)
    return actions


def build_flow_selector_from_case_context(case_context: dict) -> Optional[dict]:
    """Build a Flow_Selector from a Support_Case_Context (Req 20.3).

    Populates the flow_selector from affected_hostnames, affected_ips,
    and affected_ports. Returns None when no usable endpoint data is
    available.
    """
    if not isinstance(case_context, dict):
        return None

    hostnames = case_context.get("affected_hostnames", [])
    ips = case_context.get("affected_ips", [])
    ports = case_context.get("affected_ports", [])

    if not hostnames and not ips:
        return None

    selector: dict = {}

    # Use the first hostname as destination (most common case: user
    # reports they cannot reach a service endpoint)
    if hostnames and isinstance(hostnames, list) and isinstance(hostnames[0], str):
        selector["destination_hostname"] = hostnames[0]
    elif ips and isinstance(ips, list) and isinstance(ips[0], str):
        selector["destination_ip"] = ips[0]

    # Use the first port if available
    if ports and isinstance(ports, list):
        first_port = ports[0] if ports else None
        if isinstance(first_port, int) and 0 <= first_port <= 65535:
            selector["destination_port"] = first_port

    return selector if selector else None


def format_support_case_investigation_response(
    *,
    case_summary: str,
    health_correlation: str,
    network_analysis: str,
    recommended_actions: list[str],
) -> str:
    """Format the four-section Support_Case_Investigation response (Req 20.9).

    Returns a markdown-formatted string with labeled sections:
    "Case summary", "Health correlation", "Network analysis", and
    "Recommended next actions".
    """
    sections = []

    sections.append(f"**Case summary**\n{case_summary}")

    sections.append(f"**Health correlation**\n{health_correlation}")

    sections.append(f"**Network analysis**\n{network_analysis}")

    if recommended_actions:
        actions_text = "\n".join(f"- {a}" for a in recommended_actions)
        sections.append(f"**Recommended next actions**\n{actions_text}")
    else:
        sections.append("**Recommended next actions**\n- No specific actions recommended at this time.")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Network Agent action set (Req 9.2)
#
# Local copy of the Network Agent dispatch table used to validate the
# ``action`` argument of ``query_network_pcap`` *before* invoking the
# AgentCore runtime. Must stay in sync with the ``ACTIONS`` dictionary in
# ``agents/network-agent/main.py``. Listed in the same order as the design
# document's dispatch table (ENI inventory → capture lifecycle → pcap
# query actions).
# ---------------------------------------------------------------------------
NETWORK_AGENT_ACTIONS = (
    # ENI Inventory
    "list_enis",
    # Reverse DNS
    "reverse_dns_lookup",
    # Capture Lifecycle
    "start_capture",
    "stop_capture",
    "list_captures",
    "transform_capture",
    "get_capture_progress",
    # Pcap Query Actions
    "query_pcap",
    "search_fragmented_packets",
    "correlate_tcp_streams",
    "detect_retransmissions",
    "check_tls_hello_size",
    "get_conversation_stats",
    "reconstruct_tcp_handshake",
    "classify_tcp_resets",
    "detect_out_of_order_packets",
    "detect_zero_window",
    "analyze_tcp_options",
    "get_rtt_distribution",
    "get_request_response_latency",
    "diagnose_tcp_stream",
)


def _invoke_sub_agent(agent_arn_env: str, action: str, params: dict = None) -> str:
    """Invoke a sub-agent AgentCore runtime via boto3 and return the response."""
    agent_arn = os.environ.get(agent_arn_env)
    if not agent_arn:
        return json.dumps({
            "success": False,
            "error": f"Sub-agent ARN not configured: {agent_arn_env}"
        })

    try:
        client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
        payload_bytes = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            payload=payload_bytes,
        )
        # Read the streaming response
        response_body = response.get("response", None)
        if response_body:
            result = response_body.read().decode("utf-8")
            return result
        return json.dumps({"success": False, "error": "Empty response from sub-agent"})
    except Exception as e:
        return json.dumps({"success": False, "error": f"Sub-agent invocation failed: {str(e)}"})


def _invoke_network_agent(action: str, params: dict = None) -> str:
    """Invoke the Network Agent AgentCore runtime with a 60-second timeout.

    Distinct from ``_invoke_sub_agent`` because Req 9.1 mandates a 60-second
    invocation timeout for the Network Agent specifically. Returns a JSON
    error envelope (rather than raising) on missing configuration, client
    error, timeout, or upstream ``success=false`` so that Strands treats
    the failure as a per-tool error and keeps any other tool results
    produced in the same turn intact (Req 9.3).
    """
    agent_arn = os.environ.get("NETWORK_AGENT_ARN")
    if not agent_arn:
        # Req 9.4: return error result without invoking the runtime when
        # NETWORK_AGENT_ARN is unset or empty.
        return json.dumps({
            "success": False,
            "domain": "network",
            "error": "Network Agent runtime not configured: NETWORK_AGENT_ARN is unset or empty"
        })

    # Req 9.1: 120-second invocation timeout for diagnose_tcp_stream and
    # other multi-query handlers. ``read_timeout`` bounds how long boto3
    # waits for response bytes; ``connect_timeout`` bounds the initial
    # TCP/TLS handshake. ``retries`` is set to a single attempt so a slow
    # upstream cannot extend the wall-clock past the read_timeout via
    # implicit retry.
    boto_config = BotoConfig(
        connect_timeout=10,
        read_timeout=600,
        retries={"max_attempts": 1, "mode": "standard"},
    )

    try:
        client = boto3.client(
            "bedrock-agentcore",
            region_name=AWS_REGION,
            config=boto_config,
        )
        payload_bytes = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            payload=payload_bytes,
        )
        response_body = response.get("response", None)
        if not response_body:
            # Req 9.3: empty/timeout-equivalent upstream → per-tool error
            return json.dumps({
                "success": False,
                "domain": "network",
                "error": "Empty response from Network Agent"
            })

        raw = response_body.read().decode("utf-8")

        # Inspect the upstream envelope. When the Network Agent reports
        # ``success=false``, Req 9.3 requires the tool to surface the
        # error to the orchestrator without aborting the turn. Pass the
        # upstream JSON through unchanged so the LLM can read both the
        # error text and any partial ``data``/``formattedText``.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("success") is False:
                return raw
        except (TypeError, ValueError):
            # Non-JSON upstream body — return the raw text so the LLM can
            # still reason about it; this is treated as a soft error.
            return raw

        return raw
    except Exception as e:  # noqa: BLE001 — surface any boto/client error as JSON
        # Req 9.3: client error or timeout → JSON error envelope, NEVER
        # re-raise. Strands converts a returned string into a tool
        # observation; raising would abort other tool calls in the turn.
        return json.dumps({
            "success": False,
            "domain": "network",
            "error": f"Network Agent invocation failed: {str(e)}"
        })


@tool
def prepare_capture_confirmation(
    eni_ids: list,
    duration_minutes: int = None,
    region: str = None,
    instance_ids: list = None,
) -> str:
    """Build a Capture_Confirmation_Prompt and an idempotency token for start_capture.

    USE THIS BEFORE invoking ``query_network_pcap`` with action
    ``start_capture``. The tool packages the four mandatory pieces of
    a capture confirmation in a single deterministic call so the
    cost figure and the token cannot drift from one turn to the next.

    Workflow expected by the design (Reqs 9.17, 9.18, 9.21, 17.1, 17.2):

    1. If the user supplied an EC2 instance ID without ENIs, FIRST
       call ``query_network_pcap`` with action ``list_enis`` and
       ``params={"instance_id": "<i-...>"}`` to resolve the instance's
       ENIs. If the resolution returns multiple ENIs, ask the user
       to pick which ones to mirror BEFORE calling this tool. Pass
       only the user-selected ENIs into ``eni_ids``.
    2. Call this tool with the resolved ``eni_ids`` (sorted
       alphabetically is fine — the tool sorts internally), the
       requested ``duration_minutes`` (omit to apply the documented
       15-minute default and have the prompt annotate it as
       "(default)"), and optionally a region and a per-ENI list of
       parent instance IDs (``instance_ids[i]`` is the parent of
       ``eni_ids[i]``; pass ``None`` for unattached ENIs).
    3. Emit the returned ``prompt_text`` verbatim to the user as
       part of your reply, ask the user to confirm, and DO NOT
       invoke ``start_capture`` until the user replies with one of
       ``yes``, ``y``, ``ok``, ``okay``, ``sure``, ``confirm``,
       ``proceed``, ``go``, or ``accept`` (case-insensitive,
       trailing punctuation ignored). If the user replies with
       ``no``, ``n``, ``cancel``, ``abort``, ``stop``, or
       ``nevermind``, abort and tell the user the capture was not
       started.
    4. After the user confirms, invoke ``query_network_pcap`` with
       action ``start_capture`` and ``params`` containing
       ``eni_ids`` (from this tool's ``metadata.eni_ids``),
       ``duration_minutes`` (from ``metadata.duration_minutes``),
       and ``idempotency_token`` (from this tool's
       ``idempotency_token``). The token is derived from
       ``sha256(eni_ids ∥ duration_minutes ∥ user_id ∥
       floor(timestamp, 1m))`` so an immediate retry within the
       same minute does not start a duplicate capture.

    The returned JSON envelope contains:

    - ``success``: ``true`` when the prompt was built, ``false`` on
      validation errors.
    - ``prompt_text``: the markdown text to emit to the user.
    - ``idempotency_token``: the token to pass to ``start_capture``.
    - ``metadata.eni_ids``, ``metadata.duration_minutes``,
      ``metadata.applied_default_15``,
      ``metadata.estimated_cost_usd``,
      ``metadata.estimated_bytes``, ``metadata.region``: the
      structured echo of every value that appears in the prompt
      text. Pass these into ``start_capture`` rather than
      re-deriving them.

    Args:
        eni_ids: list of 1-3 ENI identifiers like ``["eni-0123...",
            ...]``. ENIs are sorted internally before hashing so
            ``["a", "b"]`` and ``["b", "a"]`` produce the same
            idempotency token.
        duration_minutes: integer in 1..60. Omit (or pass ``None``)
            to apply the documented 15-minute default — the prompt
            annotates the duration line with " (default)" in that
            case.
        region: optional AWS region for the per-ENI-hour price
            lookup. Defaults to the runtime's resolved region.
        instance_ids: optional list aligned with ``eni_ids`` that
            names each ENI's parent EC2 instance (or ``None`` for
            unattached). Used to render the ENI bullet list as
            "<eni-id> (attached to <instance-id>)".

    Returns:
        A JSON string with the fields documented above. Never
        raises — validation errors are surfaced via ``success=false``
        and an ``error`` field.
    """
    # Validate the action's input shapes here so the LLM gets a
    # structured error message rather than a Python traceback.
    if not isinstance(eni_ids, list) or not eni_ids:
        return json.dumps({
            "success": False,
            "error": "eni_ids must be a non-empty list of ENI identifiers",
        })
    if len(eni_ids) > 3:
        return json.dumps({
            "success": False,
            "error": (
                "eni_ids must contain at most 3 entries (Capture_Eni_Limit). "
                f"Received {len(eni_ids)}."
            ),
        })
    if duration_minutes is not None:
        if not isinstance(duration_minutes, int) or isinstance(duration_minutes, bool):
            return json.dumps({
                "success": False,
                "error": "duration_minutes must be an integer in 1..60 (or None for the default)",
            })
        if duration_minutes < 1 or duration_minutes > 60:
            return json.dumps({
                "success": False,
                "error": (
                    "duration_minutes must be in 1..60 (Capture_Duration_Limit is 60). "
                    f"Received {duration_minutes}."
                ),
            })

    if instance_ids is not None and (
        not isinstance(instance_ids, list) or len(instance_ids) != len(eni_ids)
    ):
        return json.dumps({
            "success": False,
            "error": (
                "instance_ids, when supplied, must be a list of the same length as "
                "eni_ids (one entry per ENI, with None for unattached ENIs)"
            ),
        })

    enis_payload: list[dict] = []
    for index, eni_id in enumerate(eni_ids):
        if not isinstance(eni_id, str) or not eni_id:
            return json.dumps({
                "success": False,
                "error": f"eni_ids[{index}] must be a non-empty string",
            })
        instance_id = None
        if instance_ids is not None:
            raw = instance_ids[index]
            if isinstance(raw, str) and raw:
                instance_id = raw
        enis_payload.append({
            "eni_id": eni_id,
            "attached_instance_id": instance_id,
        })

    try:
        prompt = format_capture_confirmation_prompt(
            enis_payload,
            duration_minutes=duration_minutes,
            region=region,
        )
        token = derive_capture_idempotency_token(
            [entry["eni_id"] for entry in enis_payload],
            prompt["metadata"]["duration_minutes"],
        )
    except (ValueError, KeyError, FileNotFoundError) as e:
        return json.dumps({
            "success": False,
            "error": f"Failed to build Capture_Confirmation_Prompt: {str(e)}",
        })

    return json.dumps({
        "success": True,
        "prompt_text": prompt["prompt_text"],
        "idempotency_token": token,
        "metadata": prompt["metadata"],
    })


@tool
def query_cost_data(action: str, params: dict = None) -> str:
    """Query AWS cost and usage data from Cost Explorer.
    USE THIS for: spending, budgets, forecasts, cost trends, "how much did I spend", cost optimization recommendations.
    DO NOT use this for Trusted Advisor checks or health events.

    Available actions:
    - get_cost_and_usage: Retrieve cost data for a time range. Params: startDate (optional, defaults to 30 days ago), endDate (optional, defaults to today), granularity (DAILY or MONTHLY)
    - get_cost_forecast: Get cost forecast. Params: startDate, endDate, granularity, metric
    - list_recommendations: Get cost optimization recommendations from Cost Optimization Hub. Params: category, maxResults

    You MUST always provide the action parameter. Example: action="get_cost_and_usage", params={"granularity": "MONTHLY"}
    """
    return _invoke_sub_agent("COST_AGENT_ARN", action, params)


@tool
def query_health_events(action: str, params: dict = None) -> str:
    """Query AWS Health Dashboard for service outages, incidents, and scheduled maintenance.
    USE THIS for: "any AWS issues", "service outages", "health events", "what happened on date X", scheduled changes, lifecycle events.
    DO NOT use this for Trusted Advisor optimization recommendations.

    Available actions:
    - describe_events: List health events. Params: region, service, event_type, startTime (ISO 8601), endTime (ISO 8601), maxResults
    - describe_affected_entities: Get affected resources. Params: event_arn
    - describe_event_details: Get event details. Params: event_arn

    You MUST always provide the action parameter. Example: action="describe_events", params={"startTime": "2026-03-01T00:00:00Z", "endTime": "2026-03-31T23:59:59Z"}
    """
    return _invoke_sub_agent("HEALTH_AGENT_ARN", action, params)


@tool
def query_support_cases(action: str, params: dict = None) -> str:
    """Query AWS Support cases including resolved/closed cases.
    USE THIS for: "support cases", "tickets", "case history", "did I open a case".
    DO NOT use this for health events or Trusted Advisor.

    Available actions:
    - describe_cases: List support cases (includes resolved by default). Params: maxResults
    - describe_communications: Get case communications. Params: caseId
    - search_cases: Search for cases by criteria. Params: serviceCode, severityCode, afterTime, beforeTime

    You MUST always provide the action parameter. Example: action="describe_cases", params={"maxResults": 10}
    """
    return _invoke_sub_agent("SUPPORT_AGENT_ARN", action, params)


@tool
def query_trusted_advisor(action: str, params: dict = None) -> str:
    """Query AWS Trusted Advisor for optimization recommendations and best practice checks.
    USE THIS for: "trusted advisor", "optimization", "best practices", "check trusted advisor", "recommendations", "underutilized resources", "security checks", "cost savings".
    DO NOT use this for health events or service outages.

    Available actions:
    - list_recommendations: Get actionable recommendations with warnings/errors. Params: pillar (cost_optimizing, security, performance, fault_tolerance, service_limits), maxResults
    - describe_checks: List all available checks. Params: pillar
    - describe_check_result: Get detailed results for a specific check. Params: checkId

    You MUST always provide the action parameter. Example: action="list_recommendations", params={}
    """
    return _invoke_sub_agent("TA_AGENT_ARN", action, params)


@tool
def query_cur_data(action: str, params: dict = None) -> str:
    """Query Cost and Usage Report (CUR) data via Athena for granular resource-level cost analysis.
    USE THIS for: "resource-level costs", "detailed usage", "CUR data", "usage patterns".
    DO NOT use this for high-level cost summaries (use query_cost_data instead).

    Available actions:
    - query_cur_data: Run a custom SQL query against CUR. Params: query, maxRows
    - get_resource_costs: Get costs for specific resources. Params: resourceId, service, startDate, endDate
    - analyze_usage_patterns: Analyze usage patterns by service. Params: service, startDate, endDate, granularity

    You MUST always provide the action parameter. Example: action="get_resource_costs", params={"startDate": "2026-01-01", "endDate": "2026-03-31"}
    """
    return _invoke_sub_agent("CUR_AGENT_ARN", action, params)


@tool
def query_network_pcap(action: str, params: dict = None) -> str:
    """Query the Network Agent for ENI inventory, capture lifecycle, and pcap analysis.
    USE THIS for: list ENIs, start/stop/transform/list captures, fragmented packets,
    TLS Client Hello sizes, retransmissions, TCP stream analysis, RTT, request-response
    latency, TCP resets, zero-window events, out-of-order packets, TCP options, or
    any "why does my pod / EC2 instance / Lambda fail to reach <host>" diagnosis that
    benefits from VPC packet capture.
    DO NOT use this for high-level cost or health questions (use the other tools).

    Available actions (must match exactly — unsupported actions are rejected
    locally without invoking the runtime):
    - ENI inventory: list_enis (params: vpc_id, instance_id, attachment_status, tag_key, tag_value — all optional. Use tag_key="goat-network-capture-allowed" tag_value="true" to find ENIs eligible for packet capture)
    - Reverse DNS: reverse_dns_lookup (params: ip [single IP string] OR ips [list of up to 50 IPs]). Resolves IP addresses to hostnames via PTR records. Use this to turn dst_ip/src_ip values from pcap rows into human-readable hostnames.
    - Capture lifecycle: start_capture, stop_capture, list_captures, transform_capture,
      get_capture_progress (start_capture params: eni_ids [list of 1-3], duration_minutes
      [1-60, default 15], filter_id, capture_id [optional], idempotency_token [optional])
    - Pcap query actions (all require capture_id): query_pcap, search_fragmented_packets,
      correlate_tcp_streams, detect_retransmissions, check_tls_hello_size,
      get_conversation_stats, reconstruct_tcp_handshake, classify_tcp_resets,
      detect_out_of_order_packets, detect_zero_window, analyze_tcp_options,
      get_rtt_distribution, get_request_response_latency, diagnose_tcp_stream

    Reads NETWORK_AGENT_ARN from environment. Invocation timeout is 60 seconds.

    You MUST always provide the action parameter. Example:
        action="list_enis", params={"instance_id": "i-0123456789abcdef0"}
        action="diagnose_tcp_stream", params={"capture_id": "cap-abc123", "stream_id": "s-7"}
    """
    # Req 9.2: Validate action against the locally maintained list of
    # Network Agent actions and return an error result identifying the
    # unsupported action *without* invoking the runtime.
    if not isinstance(action, str) or action not in NETWORK_AGENT_ACTIONS:
        return json.dumps({
            "success": False,
            "domain": "network",
            "error": (
                f"Unsupported Network Agent action: {action!r}. "
                f"Supported actions: {', '.join(NETWORK_AGENT_ACTIONS)}"
            )
        })

    # Req 9.16: Capture_Action authorization gate. Before invoking the
    # Network Agent runtime for ``start_capture``, ``stop_capture``, or
    # ``transform_capture``, verify the calling user's Cognito groups
    # include ``GOATNetworkCaptureUsers`` (Capture_Authorization_Group).
    # The group list is sourced from the per-request ContextVar populated
    # by ``agent_invocation``. When the user is not a member, we emit a
    # refusal envelope identifying the required group and skip the
    # AgentCore invocation entirely so no capture lifecycle work is
    # initiated. Read-only actions (``list_enis``, ``list_captures``,
    # ``query_pcap``, etc.) are not in ``CAPTURE_ACTIONS`` and bypass
    # this check.
    if action in CAPTURE_ACTIONS and not _user_in_group(GOAT_NETWORK_CAPTURE_GROUP):
        return json.dumps({
            "success": False,
            "domain": "network",
            "error": (
                f"Capture lifecycle action {action!r} requires membership in the "
                f"Cognito group {GOAT_NETWORK_CAPTURE_GROUP!r}. The authenticated "
                f"user is not a member of that group, so the orchestration agent "
                f"will not invoke this action. Ask an administrator to add the "
                f"user to {GOAT_NETWORK_CAPTURE_GROUP!r} via the Cognito console "
                f"or CLI and retry."
            ),
            "metadata": {
                "errorCategory": "unauthorized",
                "requiredGroup": GOAT_NETWORK_CAPTURE_GROUP,
            },
        })

    # Task 36, Reqs 9.20 / 17.9 — Capture_Conversation_Context anaphoric
    # resolution. When the user message contains an anaphoric reference
    # ("my capture", "the capture", etc.) AND ``params`` does not already
    # carry a ``capture_id``, substitute the ``capture_id`` persisted in
    # the active conversation's context. The substitution applies to
    # every Network Agent action that consumes a ``capture_id``: pcap
    # query actions all require it, and lifecycle actions
    # ``stop_capture``, ``transform_capture``, and ``get_capture_progress``
    # do too.
    #
    # ``start_capture`` is intentionally excluded from substitution: a
    # new capture must always create a fresh ``capture_id``, never reuse
    # the persisted one. ``list_enis`` and ``list_captures`` do not
    # consume a ``capture_id`` so substitution is a no-op for them.
    persisted_context = state.load_capture_context(
        user_id=_CURRENT_USER_ID.get() or None,
        conversation_id=_CURRENT_CONVERSATION_ID.get() or None,
    )
    persisted_capture_id = (
        persisted_context.get("capture_id")
        if isinstance(persisted_context, dict) else None
    )
    used_substituted_id = False
    if action != "start_capture" and persisted_capture_id:
        # Only substitute when the user's prompt contains an anaphor —
        # this preserves explicit-id-wins precedence. Without this
        # gate, any params dict missing ``capture_id`` would silently
        # be filled in.
        prompt = _CURRENT_USER_PROMPT.get() or ""
        if state.contains_capture_anaphor(prompt):
            params, used_substituted_id = state.substitute_persisted_capture_id(
                params=params,
                persisted_capture_id=persisted_capture_id,
            )

    raw_result = _invoke_network_agent(action, params)

    # Post-invocation hook: keep the Capture_Conversation_Context in
    # sync with successful capture lifecycle responses so future turns
    # can resolve the anaphor. Parsing is opportunistic — non-JSON or
    # non-success bodies are ignored so the chat reply is untouched.
    if action in {"start_capture", "stop_capture", "transform_capture"}:
        try:
            parsed = json.loads(raw_result) if isinstance(raw_result, str) else None
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("success") is True:
            data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
            new_capture_id = (
                data.get("capture_id")
                if isinstance(data.get("capture_id"), str)
                else None
            )
            user_id = _CURRENT_USER_ID.get() or None
            conversation_id = _CURRENT_CONVERSATION_ID.get() or None

            if action == "start_capture" and new_capture_id:
                # Task 36 bullet 4 — when the active context entry
                # points to a stopped capture and the user starts a
                # new one, the entry is replaced. ``put_item``
                # semantics overwrite unconditionally so this falls
                # out for free.
                state.record_capture_context(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    capture_id=new_capture_id,
                    eni_ids=data.get("eni_ids"),
                    deadline=data.get("deadline"),
                    duration_minutes=data.get("duration_minutes"),
                    status="active",
                )
            elif action == "stop_capture":
                state.update_capture_context_status(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    status="stopped",
                    stopped_reason=(
                        data.get("stopped_reason")
                        if isinstance(data.get("stopped_reason"), str)
                        else None
                    ),
                )
            elif action == "transform_capture":
                state.update_capture_context_status(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    status="transformed",
                )

    if used_substituted_id:
        # Surface the substitution to the LLM through the response
        # envelope so the chat reply can mention "Using your active
        # capture <id>…". Non-JSON responses pass through unchanged.
        try:
            envelope = (
                json.loads(raw_result) if isinstance(raw_result, str) else None
            )
        except (TypeError, ValueError):
            envelope = None
        if isinstance(envelope, dict):
            metadata = envelope.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["resolvedCaptureIdFromContext"] = persisted_capture_id
                return json.dumps(envelope)

    # Task 38 — enrich the upstream envelope with pre-formatted chat
    # replies for the chat-driven capture lifecycle UX (Reqs 17.4,
    # 17.5, 17.6, 17.7, 17.8). The orchestration agent's LLM consumes
    # the result of this @tool as an observation, then writes the chat
    # reply. Centralising the formatting in Python (and exposing it on
    # the response envelope) keeps the user-facing copy deterministic
    # turn-over-turn even when the LLM paraphrases the surrounding
    # prose. Failures are silent: if the upstream body is non-JSON or
    # the expected fields are missing, the envelope passes through
    # unchanged.
    try:
        envelope = (
            json.loads(raw_result) if isinstance(raw_result, str) else None
        )
    except (TypeError, ValueError):
        envelope = None
    if isinstance(envelope, dict):
        success = envelope.get("success") is True
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        metadata = envelope.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            envelope["metadata"] = metadata

        chat_reply: Optional[str] = None
        ux_hint: Optional[str] = None

        # Req 17.11 — guardrail-violation chat handling. The check runs
        # before any of the success-path branches because the upstream
        # ``success`` flag is False on rejection. A guardrail failure on
        # ``start_capture`` (the only Capture_Action subject to the three
        # numeric limits) is mapped to a category-specific reply that
        # names the limit, lists the user's options, and offers a
        # follow-up Clarification_Question.
        if (
            not success
            and action == "start_capture"
        ):
            guardrail = _classify_guardrail_violation(
                metadata.get("errorCategory"),
                envelope.get("error"),
            )
            if guardrail is not None:
                params_dict = params if isinstance(params, dict) else {}
                requested_eni_count = (
                    len(params_dict["eni_ids"])
                    if isinstance(params_dict.get("eni_ids"), list)
                    else None
                )
                requested_duration = (
                    params_dict.get("duration_minutes")
                    if isinstance(params_dict.get("duration_minutes"), int)
                    and not isinstance(params_dict.get("duration_minutes"), bool)
                    else None
                )
                active_count = (
                    data.get("active_capture_count")
                    if isinstance(data.get("active_capture_count"), int)
                    else None
                )
                chat_reply = format_guardrail_violation_reply(
                    guardrail,
                    error_text=(
                        envelope.get("error")
                        if isinstance(envelope.get("error"), str)
                        else None
                    ),
                    active_capture_count=active_count,
                    eni_count=requested_eni_count,
                    duration_minutes=requested_duration,
                )
                ux_hint = "guardrail_violation_offer_remediation"

        if success and action == "get_capture_progress":
            # Req 17.4 + 17.6 — render progress reply, possibly with
            # the auto-stop transform offer appended.
            chat_reply = format_capture_progress_reply(data) or None
            if (
                data.get("status") == "stopped"
                and data.get("stopped_reason") == "auto_stop_deadline"
            ):
                ux_hint = "auto_stop_offer_transform"
        elif success and action == "stop_capture":
            # Req 17.5 — confirm stop with elapsed duration. The
            # ``start_time`` is read from the response when present;
            # callers pass an explicit value via the persisted
            # Capture_Conversation_Context fallback in
            # ``format_stop_capture_reply``.
            chat_reply = format_stop_capture_reply(
                data,
                start_time=data.get("start_time") if isinstance(data, dict) else None,
            )
        elif success and action == "transform_capture":
            # Req 17.7 — interim reply within 5 seconds containing
            # the execution ARN. The polling-and-final-reply pair is
            # delivered by ``poll_transform_execution`` below.
            chat_reply = format_transform_interim_reply(
                data,
                capture_id=(
                    data.get("capture_id") if isinstance(data, dict) else None
                ),
            )
            ux_hint = "transform_started_poll_next"
        elif success and action in {
            "query_pcap",
            "search_fragmented_packets",
            "correlate_tcp_streams",
            "detect_retransmissions",
            "check_tls_hello_size",
            "get_conversation_stats",
            "reconstruct_tcp_handshake",
            "classify_tcp_resets",
            "detect_out_of_order_packets",
            "detect_zero_window",
            "analyze_tcp_options",
            "get_rtt_distribution",
            "get_request_response_latency",
            "diagnose_tcp_stream",
        }:
            # Req 17.8 — when a Pcap_Query_Action returns success=true
            # but with empty rows, the empty-data path offers
            # transform_capture first. We detect "empty" pragmatically:
            # the data field has no rows / records / streams arrays
            # populated, or the count fields are all zero. The LLM
            # uses the ``ux_hint`` to decide whether to emit the
            # transform offer.
            params_dict = params if isinstance(params, dict) else {}
            resolved_capture = (
                params_dict.get("capture_id")
                if isinstance(params_dict.get("capture_id"), str)
                else None
            )
            # Fallback to the substituted/persisted id when the
            # caller did not pass one explicitly.
            if not resolved_capture and used_substituted_id:
                resolved_capture = persisted_capture_id

            if _looks_empty_pcap_response(data):
                chat_reply = format_empty_pcap_query_offer(
                    capture_id=resolved_capture, action=action
                )
                ux_hint = "pcap_empty_offer_transform_then_retry"
            else:
                # Req 17.10 — populated Pcap_Query_Action result. The
                # chat reply must include the source capture_id in a
                # markdown inline code span, the action name, and a
                # one-sentence interpretation of the result. We seed
                # the interpretation with a deterministic per-action
                # hint so the LLM always has a concrete observation
                # to anchor its sentence on (e.g. flag potential TLS
                # Client Hello fragmentation when frame_size > 1400).
                upstream_text = envelope.get("formattedText")
                chat_reply = format_pcap_query_action_reply(
                    action=action,
                    capture_id=resolved_capture,
                    data=data,
                    formatted_text=(
                        upstream_text if isinstance(upstream_text, str) else None
                    ),
                )
                ux_hint = "pcap_query_action_result"

                # Task 40, Req 19.13 — when the action was invoked
                # with a flow_selector, prepend the resolved flow
                # summary line so the user immediately sees which
                # IPs the analysis covered.
                flow_sel = (
                    params_dict.get("flow_selector")
                    if isinstance(params_dict.get("flow_selector"), dict)
                    else None
                )
                if flow_sel and chat_reply:
                    stream_count = (
                        metadata.get("matched_stream_count")
                        if isinstance(metadata.get("matched_stream_count"), int)
                        else None
                    )
                    flow_summary = fs.format_resolved_flow_summary(
                        flow_sel, stream_count=stream_count
                    )
                    chat_reply = f"{flow_summary}\n\n{chat_reply}"

        if chat_reply:
            metadata["uxFormattedText"] = chat_reply
        if ux_hint:
            metadata["uxHint"] = ux_hint
        if chat_reply or ux_hint:
            return json.dumps(envelope)

    return raw_result


def _looks_empty_pcap_response(data: object) -> bool:
    """Heuristically decide whether a Pcap_Query_Action response is empty.

    Used by :func:`query_network_pcap`'s post-invocation hook to spot
    the Req 17.8 empty-data case without needing to know each action's
    response shape exhaustively. The detector returns ``True`` when:

    - ``data`` is ``None`` or an empty dict, OR
    - every list/dict field at the top level is empty, OR
    - all numeric count fields (``count``, ``packet_count``,
      ``frame_count``, ``stream_count``, ``row_count``) are zero AND
      no list field has at least one entry.

    The detector errs on the side of recall — a false positive here
    just causes the LLM to ask the user whether to run
    ``transform_capture`` first, which is a benign Clarification_Question.
    """
    if data is None:
        return True
    if not isinstance(data, dict):
        return False
    if not data:
        return True

    # If any top-level list/tuple has at least one element, the
    # response carries actual rows.
    for value in data.values():
        if isinstance(value, (list, tuple)) and len(value) > 0:
            return False
        if isinstance(value, dict) and value:
            # Nested dicts may contain rows under a ``rows`` /
            # ``items`` / ``streams`` key — check those specifically
            # rather than treating every nested dict as non-empty.
            for nested_key in ("rows", "items", "streams", "records"):
                nested = value.get(nested_key)
                if isinstance(nested, (list, tuple)) and len(nested) > 0:
                    return False

    # No populated lists found. Check whether at least one numeric
    # count field is non-zero — if so, the response is summary-only
    # but still describes data and we should not treat it as empty.
    for count_key in (
        "count",
        "packet_count",
        "frame_count",
        "stream_count",
        "row_count",
        "total_count",
        "total_packets",
        "matched_stream_count",
    ):
        value = data.get(count_key)
        if isinstance(value, (int, float)) and value > 0:
            return False
    return True


@tool
def poll_transform_execution(
    execution_arn: str,
    capture_id: str = None,
) -> str:
    """Poll a Step Functions ``transform_capture`` execution until it terminates.

    USE THIS AFTER invoking ``query_network_pcap`` with action
    ``transform_capture``. The orchestration agent's chat-driven
    workflow (Req 17.7) is:

    1. Call ``query_network_pcap("transform_capture", {"capture_id":
       "..."})`` and emit the interim reply contained in
       ``metadata.uxFormattedText`` within 5 seconds.
    2. Call THIS tool with the returned ``transform_execution_arn``
       to block until the workflow reaches a terminal state.
    3. Use the returned ``chat_reply`` as the final reply, then —
       on success — re-issue any deferred Pcap_Query_Action
       (per Req 17.8's empty-data → transform → retry path).

    The polling cadence is at most 30 seconds (Req 17.7) and the
    total wall-clock budget is 10 minutes. When the budget is
    exhausted without a terminal state, the tool returns a
    ``timed_out=true`` envelope and the chat reply tells the user to
    check back later.

    Args:
        execution_arn: The Step Functions execution ARN returned by
            ``query_network_pcap("transform_capture", ...)``. Read it
            from ``data.transform_execution_arn`` of that response.
        capture_id: Optional capture id to include in the final
            chat reply (used to list the now-queryable Pcap_Query_Action
            commands).

    Returns:
        A JSON string with fields ``success`` (true on
        ``SUCCEEDED``), ``status`` (the terminal status), ``timed_out``,
        ``chat_reply`` (markdown text the LLM should emit verbatim),
        and ``execution_arn``.
    """
    if not isinstance(execution_arn, str) or not execution_arn:
        return json.dumps({
            "success": False,
            "status": "INVALID_INPUT",
            "timed_out": False,
            "chat_reply": (
                "I cannot poll the transformation pipeline because no "
                "execution ARN was supplied. Re-run "
                "`transform_capture` to get a fresh ARN and try again."
            ),
            "execution_arn": execution_arn or "",
        })

    # Lazy import — avoid creating a Step Functions client at module
    # load time so the agent stays runnable in environments without
    # AWS credentials (local tests).
    import time as _time

    boto_config = BotoConfig(
        connect_timeout=10,
        read_timeout=30,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    sfn = boto3.client(
        "stepfunctions", region_name=AWS_REGION, config=boto_config
    )

    deadline = _time.monotonic() + _TRANSFORM_POLL_TIMEOUT_SECONDS
    last_status = "UNKNOWN"
    last_error: Optional[str] = None
    last_failed_task: Optional[str] = None

    while _time.monotonic() < deadline:
        try:
            response = sfn.describe_execution(executionArn=execution_arn)
        except Exception as exc:  # noqa: BLE001 — fail soft, surface to LLM
            return json.dumps({
                "success": False,
                "status": "DESCRIBE_EXECUTION_ERROR",
                "timed_out": False,
                "chat_reply": (
                    "I could not poll the Step Functions execution: "
                    f"{exc}. Check the AWS Step Functions console "
                    f"for execution `{execution_arn}` directly."
                ),
                "execution_arn": execution_arn,
            })

        last_status = str(response.get("status") or "UNKNOWN")
        if last_status in _TRANSFORM_TERMINAL_STATUSES:
            # Try to extract a helpful failure reason out of the
            # execution output (the design's Step Functions Failed
            # state emits ``{ failed_task, error_reason }``).
            output_raw = response.get("output")
            if isinstance(output_raw, str) and output_raw:
                try:
                    output_obj = json.loads(output_raw)
                    if isinstance(output_obj, dict):
                        if isinstance(output_obj.get("failed_task"), str):
                            last_failed_task = output_obj["failed_task"]
                        if isinstance(output_obj.get("error_reason"), str):
                            last_error = output_obj["error_reason"]
                        elif isinstance(output_obj.get("Cause"), str):
                            last_error = output_obj["Cause"]
                except (TypeError, ValueError):
                    pass
            # Step Functions also surfaces a top-level ``cause`` /
            # ``error`` on FAILED executions; read those when the
            # output JSON did not carry the design's structured
            # failure shape.
            if last_error is None and isinstance(response.get("cause"), str):
                last_error = response["cause"]

            chat_reply = format_transform_final_reply(
                success=(last_status == "SUCCEEDED"),
                capture_id=capture_id if isinstance(capture_id, str) else None,
                failed_task=last_failed_task,
                error_reason=last_error,
                timed_out=False,
            )
            return json.dumps({
                "success": last_status == "SUCCEEDED",
                "status": last_status,
                "timed_out": False,
                "chat_reply": chat_reply,
                "execution_arn": execution_arn,
                "failed_task": last_failed_task,
                "error_reason": last_error,
            })

        # Sleep until the next poll, capped by the deadline.
        remaining = deadline - _time.monotonic()
        sleep_for = min(_TRANSFORM_POLL_INTERVAL_SECONDS, max(remaining, 0))
        if sleep_for <= 0:
            break
        _time.sleep(sleep_for)

    # Polling budget exhausted.
    chat_reply = format_transform_final_reply(
        success=False,
        capture_id=capture_id if isinstance(capture_id, str) else None,
        timed_out=True,
    )
    return json.dumps({
        "success": False,
        "status": last_status,
        "timed_out": True,
        "chat_reply": chat_reply,
        "execution_arn": execution_arn,
    })





# ---------------------------------------------------------------------------
# Support_Case_Investigation workflow (Task 41, Reqs 20.1-20.14)
# ---------------------------------------------------------------------------


@tool
def investigate_support_case(case_id: str, capture_id: str = None) -> str:
    """Investigate a support case by extracting context and driving multi-agent analysis.
    USE THIS when the user provides a support case identifier (case-XXXXXXXXXXXX-XXXX-XXXXXX
    or a legacy numeric ID) and asks to investigate, analyze, or troubleshoot it.
    DO NOT use this for general support case listing — use query_support_cases instead.

    This tool orchestrates a Support_Case_Investigation workflow:
    1. Retrieves the case body and communications via the Support_Agent
    2. Extracts a Support_Case_Context (endpoints, time windows, error signatures)
    3. Correlates with Health events when a time window is available
    4. Drives Network Agent analysis when a capture_id is available
    5. Filters Trusted Advisor results by affected services/regions
    6. Returns a four-section response: Case summary, Health correlation,
       Network analysis, Recommended next actions

    Args:
        case_id: AWS Support case identifier (case-XXXXXXXXXXXX-XXXX-XXXXXX or numeric)
        capture_id: Optional capture_id for packet-level analysis. When absent,
                    the tool checks the Capture_Conversation_Context and offers
                    options if no capture is available.

    Example: investigate_support_case(case_id="case-123456789012-2024-000001")
    Example: investigate_support_case(case_id="case-123456789012-2024-000001", capture_id="cap-abc123")
    """
    results: dict = {
        "case_summary": "",
        "health_correlation": "No Health events match the case window",
        "network_analysis": "No packet capture available — see options offered above",
        "recommended_actions": [],
        "support_case_context": None,
        "no_capture_options": None,
    }

    # Step 1: Retrieve case body and communications (Req 20.1)
    case_body_raw = _invoke_sub_agent("SUPPORT_AGENT_ARN", "describe_cases", {"caseId": case_id})
    try:
        case_body = json.loads(case_body_raw) if isinstance(case_body_raw, str) else {}
    except (TypeError, ValueError):
        case_body = {}

    # Req 20.10: Check for access/plan errors from the Support_Agent
    if isinstance(case_body, dict) and case_body.get("success") is False:
        error_msg = case_body.get("error", "Unknown error")
        error_lower = str(error_msg).lower()
        # Req 20.11: Plan-level error — offer to proceed with user-supplied endpoints
        if "plan" in error_lower or "subscription" in error_lower or "not subscribed" in error_lower:
            return json.dumps({
                "success": False,
                "domain": "orchestration",
                "error": f"Support plan error for case {case_id}: {error_msg}",
                "metadata": {
                    "errorCategory": "support_plan_required",
                    "case_id": case_id,
                    "offer_manual_endpoints": True,
                },
                "formattedText": (
                    f"I cannot access support case `{case_id}` — your account may not have "
                    f"the required Business or Enterprise Support plan.\n\n"
                    f"Would you like me to proceed with the investigation using endpoints "
                    f"you provide manually? You can supply hostnames, IPs, and ports, and "
                    f"I will drive the Health and Network analysis without the case context."
                ),
            })
        # Req 20.10: Case not found or not accessible
        if "not found" in error_lower or "access" in error_lower or "does not exist" in error_lower:
            return json.dumps({
                "success": False,
                "domain": "orchestration",
                "error": f"Case {case_id} not found or not accessible: {error_msg}",
                "metadata": {"errorCategory": "case_not_found", "case_id": case_id},
                "formattedText": (
                    f"Support case `{case_id}` was not found or is not accessible "
                    f"in this account. Please verify the case identifier and ensure "
                    f"you have the required permissions."
                ),
            })
        # Generic Support_Agent error
        return json.dumps({
            "success": False,
            "domain": "orchestration",
            "error": f"Support Agent error for case {case_id}: {error_msg}",
            "formattedText": f"Error retrieving support case `{case_id}`: {error_msg}",
        })

    # Retrieve communications
    comms_raw = _invoke_sub_agent("SUPPORT_AGENT_ARN", "describe_communications", {"caseId": case_id})
    try:
        comms_body = json.loads(comms_raw) if isinstance(comms_raw, str) else {}
    except (TypeError, ValueError):
        comms_body = {}

    # Step 2: Extract Support_Case_Context (Req 20.2)
    # Build a text corpus from the case body and communications for the
    # foundation model to extract structured context from. The extraction
    # is done heuristically here (pattern-based) since we cannot call the
    # foundation model from within a @tool function in Strands. The LLM
    # will refine this in its response.
    case_context = _extract_support_case_context(case_id, case_body, comms_body)
    results["support_case_context"] = case_context

    # Persist the Support_Case_Context (Req 20.2, 20.12)
    user_id = _CURRENT_USER_ID.get() or None
    conversation_id = _CURRENT_CONVERSATION_ID.get() or None
    state.record_support_case_context(
        user_id=user_id,
        conversation_id=conversation_id,
        support_case_context=case_context,
    )

    # Build case summary (Req 20.9 — 1 to 5 sentences)
    results["case_summary"] = _build_case_summary(case_id, case_context, case_body)

    # Step 3: Health correlation (Req 20.4)
    incident_start = case_context.get("incident_window_start")
    incident_end = case_context.get("incident_window_end")
    if incident_start and incident_end:
        health_params: dict = {
            "startTime": incident_start,
            "endTime": incident_end,
        }
        # Filter by affected services/regions when available
        affected_services = case_context.get("affected_services", [])
        affected_regions = case_context.get("affected_regions", [])
        if affected_services:
            health_params["serviceFilter"] = affected_services
        if affected_regions:
            health_params["regionFilter"] = affected_regions

        health_raw = _invoke_sub_agent("HEALTH_AGENT_ARN", "describe_events", health_params)
        try:
            health_data = json.loads(health_raw) if isinstance(health_raw, str) else {}
        except (TypeError, ValueError):
            health_data = {}

        if isinstance(health_data, dict) and health_data.get("success") is True:
            health_events = health_data.get("data", {})
            if isinstance(health_events, dict):
                events_list = health_events.get("events", [])
                if events_list:
                    bullets = []
                    for evt in events_list[:10]:  # Cap at 10 events
                        if isinstance(evt, dict):
                            svc = evt.get("service", "Unknown")
                            desc = evt.get("statusCode", evt.get("eventTypeCode", ""))
                            region = evt.get("region", "")
                            bullets.append(
                                f"- [Health] {svc} ({region}): {desc}"
                            )
                    if bullets:
                        results["health_correlation"] = "\n".join(bullets)

    # Step 4: Network analysis (Reqs 20.5, 20.7)
    # Resolve capture_id from argument, conversation context, or offer options
    resolved_capture_id = capture_id
    if not resolved_capture_id:
        # Check Capture_Conversation_Context
        persisted = state.load_capture_context(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if isinstance(persisted, dict) and persisted.get("capture_id"):
            resolved_capture_id = persisted["capture_id"]

    if resolved_capture_id:
        # Build flow_selector from case context (Req 20.3)
        flow_selector = build_flow_selector_from_case_context(case_context)

        # Run diagnose_tcp_stream if we have endpoint info (Req 20.5)
        if flow_selector:
            diag_params: dict = {
                "capture_id": resolved_capture_id,
                "flow_selector": flow_selector,
            }
            diag_raw = _invoke_network_agent("diagnose_tcp_stream", diag_params)
            try:
                diag_data = json.loads(diag_raw) if isinstance(diag_raw, str) else {}
            except (TypeError, ValueError):
                diag_data = {}

            if isinstance(diag_data, dict) and diag_data.get("success") is True:
                network_text = diag_data.get("formattedText", "")
                if network_text:
                    results["network_analysis"] = f"[Network] {network_text}"

        # Proactively invoke matching Pcap_Query_Actions (Req 20.7)
        error_sigs = case_context.get("error_signatures", [])
        matched_actions = match_error_signatures_to_actions(error_sigs)
        network_findings: list[str] = []
        for action_name in matched_actions[:3]:  # Cap at 3 proactive queries
            action_params: dict = {"capture_id": resolved_capture_id}
            if flow_selector:
                action_params["flow_selector"] = flow_selector
            action_raw = _invoke_network_agent(action_name, action_params)
            try:
                action_data = json.loads(action_raw) if isinstance(action_raw, str) else {}
            except (TypeError, ValueError):
                action_data = {}
            if isinstance(action_data, dict) and action_data.get("success") is True:
                action_text = action_data.get("formattedText", "")
                if action_text:
                    network_findings.append(f"[Network/{action_name}] {action_text}")

        if network_findings:
            existing_analysis = results["network_analysis"]
            if existing_analysis and "No packet capture" not in existing_analysis:
                results["network_analysis"] = existing_analysis + "\n\n" + "\n\n".join(network_findings)
            else:
                results["network_analysis"] = "\n\n".join(network_findings)
    else:
        # Req 20.6: No capture_id available — offer three options
        # Build proposed ENIs from the case context
        proposed_enis_text = ""
        flow_selector = build_flow_selector_from_case_context(case_context)
        if flow_selector:
            proposed_enis_text = (
                f" I can resolve ENIs from the case endpoints "
                f"({flow_selector.get('destination_hostname', flow_selector.get('destination_ip', 'unknown'))})."
            )

        results["no_capture_options"] = (
            f"No packet capture is available for this investigation.{proposed_enis_text}\n\n"
            f"Choose one:\n"
            f"  a. Start a new capture using the endpoints from the case\n"
            f"  b. Point me at an existing capture_id\n"
            f"  c. Proceed without packet capture (Health and Trusted Advisor only)"
        )
        results["network_analysis"] = "No packet capture available — see options offered above"

    # Step 5: Trusted Advisor correlation (Req 20.8)
    ta_raw = _invoke_sub_agent("TA_AGENT_ARN", "list_recommendations", {})
    try:
        ta_data = json.loads(ta_raw) if isinstance(ta_raw, str) else {}
    except (TypeError, ValueError):
        ta_data = {}

    ta_findings: list[str] = []
    if isinstance(ta_data, dict) and ta_data.get("success") is True:
        ta_results = ta_data.get("data", {})
        recommendations = (
            ta_results.get("recommendations", [])
            if isinstance(ta_results, dict) else []
        )
        affected_services = set(case_context.get("affected_services", []))
        affected_regions = set(case_context.get("affected_regions", []))
        for rec in recommendations:
            if not isinstance(rec, dict):
                continue
            category = rec.get("pillar", rec.get("category", ""))
            if category not in SUPPORT_CASE_TA_CATEGORIES:
                continue
            # Filter by resource intersection with affected services/regions
            rec_service = rec.get("service", "")
            rec_region = rec.get("region", "")
            if affected_services and rec_service and rec_service not in affected_services:
                continue
            if affected_regions and rec_region and rec_region not in affected_regions:
                continue
            name = rec.get("name", rec.get("checkName", "Unknown"))
            status = rec.get("status", "")
            ta_findings.append(f"[Trusted Advisor/{category}] {name}: {status}")

    # Step 6: Build recommended actions (Req 20.9)
    recommended_actions: list[str] = []
    if results["health_correlation"] != "No Health events match the case window":
        recommended_actions.append(
            "[Health] Review the correlated Health events for service impact details"
        )
    if resolved_capture_id and "No packet capture" not in results["network_analysis"]:
        recommended_actions.append(
            f"[Network] Run additional pcap analysis on capture `{resolved_capture_id}` "
            f"for deeper TCP/TLS diagnostics"
        )
    elif not resolved_capture_id:
        recommended_actions.append(
            "[Network] Start a packet capture to collect traffic evidence for this issue"
        )
    if ta_findings:
        recommended_actions.append(
            "[Trusted Advisor] Address the flagged recommendations that intersect with the affected services"
        )
    error_sigs = case_context.get("error_signatures", [])
    if error_sigs:
        recommended_actions.append(
            f"[Support] Follow up on error signatures: {', '.join(error_sigs[:3])}"
        )
    if not recommended_actions:
        recommended_actions.append("Monitor the situation and re-check after applying any suggested fixes")

    results["recommended_actions"] = recommended_actions

    # Format the four-section response (Req 20.9)
    formatted_response = format_support_case_investigation_response(
        case_summary=results["case_summary"],
        health_correlation=results["health_correlation"],
        network_analysis=results["network_analysis"],
        recommended_actions=recommended_actions,
    )

    # Append Trusted Advisor findings if any
    if ta_findings:
        formatted_response += "\n\n**Trusted Advisor findings**\n" + "\n".join(ta_findings[:5])

    # Append the no-capture options if applicable (Req 20.6)
    if results.get("no_capture_options"):
        formatted_response += "\n\n" + results["no_capture_options"]

    return json.dumps({
        "success": True,
        "domain": "orchestration",
        "data": {
            "support_case_context": case_context,
            "capture_id_used": resolved_capture_id,
            "health_events_found": results["health_correlation"] != "No Health events match the case window",
            "network_analysis_performed": "No packet capture" not in results["network_analysis"],
            "ta_findings_count": len(ta_findings),
        },
        "formattedText": formatted_response,
        "metadata": {
            "sourceWorkflow": "Support_Case_Investigation",
            "case_id": case_id,
            "capture_id": resolved_capture_id,
            "flow_selector": build_flow_selector_from_case_context(case_context),
        },
    })


def _extract_support_case_context(
    case_id: str,
    case_body: dict,
    comms_body: dict,
) -> dict:
    """Extract a Support_Case_Context from case body and communications (Req 20.2).

    Performs heuristic extraction of hostnames, IPs, ports, services,
    regions, time windows, and error signatures from the case text.
    Fields that cannot be determined are set to empty list or None.
    """
    context: dict = {
        "case_id": case_id,
        "account_id": None,
        "affected_hostnames": [],
        "affected_ips": [],
        "affected_ports": [],
        "affected_services": [],
        "affected_regions": [],
        "incident_window_start": None,
        "incident_window_end": None,
        "error_signatures": [],
        "severity": None,
    }

    # Gather all text from case body and communications
    text_corpus = _gather_case_text(case_body, comms_body)

    # Extract severity from case metadata
    if isinstance(case_body, dict):
        data = case_body.get("data", case_body)
        if isinstance(data, dict):
            cases = data.get("cases", [data])
            if isinstance(cases, list) and cases:
                case_obj = cases[0] if isinstance(cases[0], dict) else {}
                context["severity"] = case_obj.get("severityCode", case_obj.get("severity"))
                context["account_id"] = case_obj.get("accountId")
                # Extract service from case metadata
                svc_code = case_obj.get("serviceCode", "")
                if svc_code:
                    context["affected_services"].append(svc_code)

    # Extract hostnames (DNS names)
    hostname_re = re.compile(
        r"\b([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,})\b"
    )
    hostnames = set()
    for match in hostname_re.finditer(text_corpus):
        hostname = match.group(1)
        # Filter out common non-endpoint hostnames
        if not hostname.endswith((".md", ".txt", ".json", ".yaml", ".yml", ".py", ".ts", ".js")):
            hostnames.add(hostname)
    context["affected_hostnames"] = sorted(hostnames)[:10]  # Cap at 10

    # Extract IPv4 addresses
    ipv4_re = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
    ips = set()
    for match in ipv4_re.finditer(text_corpus):
        ip = match.group(1)
        # Basic validation — each octet 0-255
        octets = ip.split(".")
        if all(0 <= int(o) <= 255 for o in octets):
            ips.add(ip)
    context["affected_ips"] = sorted(ips)[:10]

    # Extract ports (numbers following "port" keyword or in host:port notation)
    port_re = re.compile(r"(?:port\s+|:)(\d{1,5})\b", re.IGNORECASE)
    ports = set()
    for match in port_re.finditer(text_corpus):
        port_val = int(match.group(1))
        if 0 <= port_val <= 65535:
            ports.add(port_val)
    context["affected_ports"] = sorted(ports)[:10]

    # Extract AWS regions
    region_re = re.compile(
        r"\b(us-east-[12]|us-west-[12]|eu-west-[123]|eu-central-[12]|"
        r"eu-north-1|eu-south-[12]|ap-southeast-[1234]|ap-northeast-[123]|"
        r"ap-south-[12]|ap-east-1|sa-east-1|ca-central-1|me-south-1|"
        r"me-central-1|af-south-1|il-central-1)\b"
    )
    regions = set()
    for match in region_re.finditer(text_corpus):
        regions.add(match.group(1))
    context["affected_regions"] = sorted(regions)

    # Extract AWS service names
    service_patterns = [
        "ec2", "ecs", "eks", "lambda", "rds", "s3", "dynamodb",
        "elasticache", "cloudfront", "api gateway", "apigateway",
        "elb", "alb", "nlb", "route53", "route 53", "vpc",
        "network firewall", "waf", "acm", "ecr", "fargate",
        "sqs", "sns", "kinesis", "msk", "kafka",
    ]
    services = set(context["affected_services"])  # Keep any from metadata
    text_lower = text_corpus.lower()
    for svc in service_patterns:
        if svc in text_lower:
            services.add(svc.upper() if len(svc) <= 4 else svc.title())
    context["affected_services"] = sorted(services)[:10]

    # Extract ISO 8601 timestamps for incident window
    iso_re = re.compile(
        r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\b"
    )
    timestamps = []
    for match in iso_re.finditer(text_corpus):
        timestamps.append(match.group(1))
    if len(timestamps) >= 2:
        context["incident_window_start"] = timestamps[0]
        context["incident_window_end"] = timestamps[-1]
    elif len(timestamps) == 1:
        context["incident_window_start"] = timestamps[0]
        # Default to 1 hour window
        context["incident_window_end"] = None

    # Extract error signatures
    error_patterns = [
        r"(?:error|exception|failure|failed)[\s:]+([^\n.]{10,100})",
        r"(connection\s+(?:reset|refused|timed?\s*out|timeout))",
        r"(tls\s+(?:handshake|error|failure)[^\n.]{0,60})",
        r"(ssl\s+(?:handshake|error|failure)[^\n.]{0,60})",
        r"(dns\s+(?:resolution|lookup)\s+(?:failed|error|timeout)[^\n.]{0,60})",
        r"(50[234]\s+(?:bad\s+gateway|service\s+unavailable|gateway\s+timeout))",
        r"(ECONNRESET|ETIMEDOUT|ECONNREFUSED|EHOSTUNREACH)",
    ]
    error_sigs = set()
    for pattern in error_patterns:
        for match in re.finditer(pattern, text_corpus, re.IGNORECASE):
            sig = match.group(1).strip()
            if sig and len(sig) >= 5:
                error_sigs.add(sig[:100])  # Cap individual signature length
    context["error_signatures"] = sorted(error_sigs)[:10]

    return context


def _gather_case_text(case_body: dict, comms_body: dict) -> str:
    """Concatenate all text from case body and communications into a single corpus."""
    parts: list[str] = []

    # Extract text from case body
    if isinstance(case_body, dict):
        data = case_body.get("data", case_body)
        if isinstance(data, dict):
            cases = data.get("cases", [data])
            if isinstance(cases, list):
                for case_obj in cases:
                    if not isinstance(case_obj, dict):
                        continue
                    for field in ("subject", "body", "recentCommunications",
                                  "displayId", "serviceCode", "categoryCode"):
                        val = case_obj.get(field)
                        if isinstance(val, str):
                            parts.append(val)
                        elif isinstance(val, dict):
                            # recentCommunications is a nested object
                            comms = val.get("communications", [])
                            if isinstance(comms, list):
                                for comm in comms:
                                    if isinstance(comm, dict):
                                        body_text = comm.get("body", "")
                                        if isinstance(body_text, str):
                                            parts.append(body_text)
        # Also check formattedText from the agent response
        formatted = case_body.get("formattedText")
        if isinstance(formatted, str):
            parts.append(formatted)

    # Extract text from communications
    if isinstance(comms_body, dict):
        data = comms_body.get("data", comms_body)
        if isinstance(data, dict):
            comms = data.get("communications", [])
            if isinstance(comms, list):
                for comm in comms:
                    if isinstance(comm, dict):
                        body_text = comm.get("body", "")
                        if isinstance(body_text, str):
                            parts.append(body_text)
        formatted = comms_body.get("formattedText")
        if isinstance(formatted, str):
            parts.append(formatted)

    return "\n".join(parts)


def _build_case_summary(case_id: str, case_context: dict, case_body: dict) -> str:
    """Build a 1-5 sentence case summary for the investigation response (Req 20.9)."""
    parts: list[str] = []

    # Case identifier
    parts.append(f"Support case `{case_id}`")

    # Severity
    severity = case_context.get("severity")
    if severity:
        parts[0] += f" (severity: {severity})"

    # Subject from case body
    subject = None
    if isinstance(case_body, dict):
        data = case_body.get("data", case_body)
        if isinstance(data, dict):
            cases = data.get("cases", [data])
            if isinstance(cases, list) and cases:
                case_obj = cases[0] if isinstance(cases[0], dict) else {}
                subject = case_obj.get("subject")
    if subject:
        parts.append(f"Subject: {subject}")

    # Affected endpoints
    hostnames = case_context.get("affected_hostnames", [])
    ips = case_context.get("affected_ips", [])
    if hostnames or ips:
        endpoints = hostnames[:3] + ips[:3]
        parts.append(f"Affected endpoints: {', '.join(endpoints)}")

    # Time window
    start = case_context.get("incident_window_start")
    end = case_context.get("incident_window_end")
    if start:
        window_text = f"Incident window: {start}"
        if end:
            window_text += f" to {end}"
        parts.append(window_text)

    # Error signatures
    errors = case_context.get("error_signatures", [])
    if errors:
        parts.append(f"Error patterns: {', '.join(errors[:3])}")

    return ". ".join(parts) + "."


# Foundation model identifier for the Orchestration Agent. Read from the
# `ORCH_MODEL_ID` environment variable (set on the OrchRuntime by the
# OrchRuntimeStack, with operator override via the `--orch-model-id` deploy
# parameter). Default is `amazon.nova-pro-v1:0`. Switching to any
# Amazon Bedrock-supported foundation model identifier (for example
# `anthropic.claude-opus-4-7`) requires only updating the env var and
# redeploying the OrchRuntimeStack — no source code changes (Req 9.9).
ORCH_MODEL_ID = os.environ.get("ORCH_MODEL_ID") or "amazon.nova-pro-v1:0"
model = BedrockModel(model_id=ORCH_MODEL_ID)

# ---------------------------------------------------------------------------
# Per-session Agent pool
#
# Each AgentCore session_id maps to a persistent Strands Agent instance
# that retains conversation history across turns. This enables multi-turn
# flows like the Capture_Confirmation_Prompt (agent asks "yes/no?" → user
# replies "yes" → agent recognizes it as confirmation and proceeds).
#
# The SessionManager handles:
#   - LRU eviction when MAX_SESSIONS (50) is exceeded
#   - TTL expiry after 30 minutes of inactivity
#   - Automatic creation of new Agent instances for unknown sessions
# ---------------------------------------------------------------------------
_ORCH_TOOLS = [query_cost_data, query_health_events, query_support_cases,
               query_trusted_advisor, query_cur_data, query_network_pcap,
               prepare_capture_confirmation, poll_transform_execution,
               investigate_support_case]


def _create_agent() -> Agent:
    """Factory: create a new Strands Agent instance for a session."""
    return Agent(
        model=model,
        tools=_ORCH_TOOLS,
        system_prompt=_build_system_prompt(),
    )


_session_manager = SessionManager(agent_factory=_create_agent)


def _build_system_prompt() -> str:
    """Build system prompt with current date for accurate time references."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_year = datetime.now(timezone.utc).strftime("%Y")

    return f"""You are an AWS operations analytics assistant (G.O.A.T.).
Today's date is {today}. The current year is {current_year}. ALWAYS use this when interpreting time references.

CRITICAL DATE RULES:
- "last month" = previous calendar month in {current_year}
- "in March" = March {current_year} (startTime: "{current_year}-03-01T00:00:00Z", endTime: "{current_year}-03-31T23:59:59Z")
- "past 3 months" = 3 months before {today}
- "recently" = last 30 days from {today}
- "on October 20th" = October 20, 2025 (startTime: "2025-10-20T00:00:00Z", endTime: "2025-10-21T00:00:00Z")
- ALWAYS pass startTime and endTime as ISO 8601 strings when the user mentions ANY time period
- NEVER omit date parameters when the user asks about a specific time range
- ALWAYS use the current year ({current_year}) unless the user explicitly mentions a different year

When calling tools:
- ALWAYS provide the action parameter (e.g., action="describe_events")
- ALWAYS convert relative time references ("last week", "in March", "recently") to precise ISO 8601 date strings
- For health events: use startTime and endTime params
- For cost data: use startDate and endDate params (format: YYYY-MM-DD)
- For "top cost drivers" or "cost breakdown": use groupBy=["SERVICE"] to get per-service costs. ALWAYS use metric="UNBLENDED_COST" (default, most reliable).
- For "this month" cost queries: use startDate as the 1st of current month, endDate as {today}
- Cost Explorer data has a 24-48 hour delay — if current month returns $0, explain this to the user and suggest querying the previous month instead
- For cost queries: make ONE call with the right parameters. Do NOT retry with different date ranges — the agent handles retries internally. If the call fails, report the error and suggest the user try again in 2-3 minutes.
- For support cases: when correlating with a health event or incident, use action="describe_cases" with maxResults=20 and do NOT filter by date — support cases about an incident may be created days after the event. Look for cases matching the service name in the subject or serviceCode.

When a question spans multiple domains, call multiple tools and correlate the results.
For example, if a user asks about a service outage's cost impact, query both Health
and Cost tools, then explain how they relate.

NETWORK AGENT CAPABILITIES:
The query_network_pcap tool exposes a Network Agent that performs on-demand VPC
packet capture and pcap analysis. Use it for any question that benefits from
inspecting actual network traffic. The Network Agent organizes its actions in
three groups:
1. ENI inventory — "list_enis" enumerates Elastic Network Interfaces in the
   account, optionally filtered by vpc_id, instance_id, attachment_status,
   or tag_key/tag_value. To find ENIs eligible for packet capture, use
   tag_key="goat-network-capture-allowed" and tag_value="true". This is
   particularly useful when the user asks "which ENI can I capture?" or
   "show me capture-eligible ENIs" — call list_enis with the tag filter
   and present the results. Each ENI in the response includes a "tags"
   dict with all its tags for easy identification.
   Reverse DNS — "reverse_dns_lookup" resolves IP addresses to hostnames
   via PTR records (params: "ip" for a single address, or "ips" for a
   list of up to 50). Use it when a user asks "what host is <ip>?" or to
   annotate dst_ip/src_ip values from pcap query results with
   human-readable hostnames.
2. Capture lifecycle — "start_capture", "stop_capture", "list_captures",
   "transform_capture", and "get_capture_progress" manage on-demand VPC
   Traffic Mirror sessions. A capture writes raw pcap files to S3, and
   "transform_capture" runs a workflow that converts those files into a
   queryable Athena partition keyed by capture_id.
3. Pcap query actions — once a capture has been transformed, query its
   data with "query_pcap" (caller-supplied SELECT against pcap_logs),
   "search_fragmented_packets", "correlate_tcp_streams",
   "detect_retransmissions", "check_tls_hello_size",
   "get_conversation_stats", "reconstruct_tcp_handshake",
   "classify_tcp_resets", "detect_out_of_order_packets",
   "detect_zero_window", "analyze_tcp_options", "get_rtt_distribution",
   "get_request_response_latency", or "diagnose_tcp_stream" for a
   one-shot structured TCP stream health report.

PCAP_LOGS SCHEMA (for free-form "query_pcap" SELECTs — use this to
investigate ANY network issue, not just the canned analyses above).
Every row is one captured frame with these columns:
- Frame: frame_time (timestamp), frame_size (bytes),
  frame_payload_summary (first 256 bytes as hex)
- L2: eth_src, eth_dst, eth_type
- L3/IP: src_ip, dst_ip, ip_version, ip_ttl (TTL/hop limit — low or
  decreasing values reveal routing loops/asymmetry), ip_id, ip_flags
  (DF/MF), ip_frag_offset (non-zero = IP fragment), ip_total_length,
  ip_proto_num, ip_dscp, ip_ecn (3=congestion experienced)
- ICMP: icmp_type (3=unreachable, 11=time-exceeded), icmp_code
  (type 3 code 4 = fragmentation-needed / PMTU black-hole)
- L4 ports/transport: protocol (tcp/udp/icmp/icmpv6/other),
  src_port, dst_port, udp_length
- TCP: tcp_seq, tcp_ack, tcp_flags (hex), tcp_window (0 = zero-window),
  tcp_options (array; MSS/WS/SACK_PERM/TS), tcp_stream (per-flow id),
  tcp_urgent_ptr, tcp_payload_len (0 = pure ACK/SYN/FIN)
- TLS: tls_content_type (22=handshake,21=alert,23=appdata),
  tls_version, tls_handshake_type (1=ClientHello,2=ServerHello),
  tls_record_size, tls_sni, tls_fragment_count
- DNS: dns_qname, dns_qtype, dns_response_ips, dns_rcode
  (2=SERVFAIL,3=NXDOMAIN), dns_id, dns_is_response
Always filter by capture_id (the handler injects it automatically).
Pick the columns relevant to the symptom: e.g. ICMP type/code for
unreachables and PMTU, ip_ttl for routing loops, tcp_flags + tcp_window
for resets/zero-window, dns_rcode for resolution failures, ip_flags +
ip_frag_offset for IP fragmentation. Do NOT assume every investigation
is about TLS.

TLS CLIENT HELLO FRAGMENTATION DIAGNOSIS (ML-KEM / post-quantum):
When investigating "connection reset"/"connection refused"/"image pull
failing" symptoms to an HTTPS endpoint that traverses AWS Network
Firewall, use "check_tls_hello_size" and look for this specific
signature in the returned rows:
- frame_size / record_size LARGER than the TCP MSS (~1460 bytes) — a
  Client Hello around 1500+ bytes is the tell. Modern clients
  (OpenSSL 3.5+, AL2023) default to post-quantum hybrid key exchange
  (X25519MLKEM768), whose key share inflates the Client Hello past one
  TCP segment.
- fragment_count >= 2 — the Client Hello record spans multiple TCP
  segments.
- server_name (SNI) is null/empty on that same row — because the SNI
  extension bytes fall in the SECOND segment, the SNI cannot be read
  from the first segment alone.
When you see (large record_size) AND (fragment_count >= 2) AND (null
server_name) together, conclude: the TLS Client Hello is fragmented
across two TCP segments, so AWS Network Firewall with a stateful
"Drop Established" default action cannot reassemble the segments to
read the SNI; its SNI-based pass rule (e.g. *.amazonaws.com) therefore
never matches and the firewall drops the first segment. Corroborate
with "detect_retransmissions" (the dropped Client Hello segment is
retransmitted) and "classify_tcp_resets". The recommended remediation
to surface to the user: switch the firewall policy's stateful default
action from "aws:drop_established" to
"aws:drop_established_app_layer" (which reassembles the
application-layer message before rule evaluation), or add the
AWS-recommended TCP pass rules; alternatively use VPC endpoints to
bypass the firewall path. Present this as the root cause, not a
transient error.

Every Network Agent response uses the same envelope shape as the other tools
(success, domain="network", data, formattedText, metadata.sourceApi,
metadata.queryTimestamp, metadata.dataFreshness). Pcap query actions all
require a capture_id. When the user references "my capture" or "the
capture" without an explicit ID, prefer calling "list_captures" with
status="active" and asking the user to choose rather than guessing.

CAPTURE_CONVERSATION_CONTEXT (anaphoric capture references):
The orchestration agent transparently maintains a per-conversation
"active capture" entry that remembers the most recently created
``capture_id`` (along with its ENIs, deadline, duration, and lifecycle
status). Use this entry when resolving anaphoric references in the
chat and when planning follow-up Network Agent invocations:

1. ANAPHORIC SUBSTITUTION (automatic). When the user types "my
   capture", "the capture", "this capture", "stop my capture",
   "transform my capture", "is my capture ready", or any
   semantically equivalent phrasing, the orchestration runtime
   automatically injects the persisted ``capture_id`` into the
   ``params`` you pass to ``query_network_pcap``, but ONLY when
   ``params`` does not already contain an explicit ``capture_id``.
   You therefore do NOT need to call ``list_captures`` first when
   the conversation already has an active capture and the user is
   making an anaphoric reference. Just call ``query_network_pcap``
   with the action you want and an empty (or partial) ``params``,
   and the runtime will fill in ``capture_id`` for you. The
   resulting Network Agent response will include
   ``metadata.resolvedCaptureIdFromContext`` set to the
   substituted id; mention it in the chat reply with markdown
   inline code (e.g. "Using your active capture `cap-abc123`…")
   so the user always knows which capture you are operating on.

2. NO CONTEXT, NO EXPLICIT ID. When (a) the conversation has no
   persisted ``capture_id``, AND (b) the user has not supplied
   one explicitly, AND (c) the requested action is one that
   requires a ``capture_id`` (every action except ``list_enis``,
   ``start_capture``, and ``list_captures``), call
   ``query_network_pcap("list_captures", {{"status": "active"}})``
   first and present the active captures back to the user. Ask
   them which capture they want to act on (or whether they want
   to start a new one). Do NOT invoke the original action with a
   guessed ``capture_id`` and do NOT prompt the user to retype
   the identifier they already typed earlier in the conversation.

3. EXPLICIT ID WINS. When the user supplies an explicit
   ``capture_id`` in the message (a string matching
   ``[A-Za-z0-9_-]{{1,128}}``), pass that value through to
   ``query_network_pcap`` regardless of the persisted context.
   The runtime will never overwrite an explicit id.

4. STOPPED CAPTURE → REPLACED ON NEW CAPTURE. When the persisted
   entry points to a ``stopped`` (or ``transformed``) capture
   AND the user starts a new ``start_capture``, the runtime
   replaces the persisted entry with the new ``capture_id``
   automatically. You don't need to clear it explicitly. The
   stopped capture is still queryable until its raw S3 data
   ages out (7 days) — if the user wants to go back to it,
   they can supply the explicit ``capture_id`` and the
   substitution skips per rule 3 above.

CAPTURE LIFECYCLE WORKFLOW (start_capture):
Starting a packet capture is a write operation that costs money and consumes
infrastructure. It must follow this exact ordered workflow before invoking
``query_network_pcap`` with action ``start_capture``:

1. Authorization check — already enforced server-side by query_network_pcap.
   If the user is not in the GOATNetworkCaptureUsers Cognito group the tool
   refuses the call. You do not need to pre-check membership.

2. Instance-to-ENI resolution — when the user references an EC2 instance
   identifier (a string matching the pattern ``i-`` followed by 8 to 17
   lowercase hex characters, e.g. ``i-0123456789abcdef0``) WITHOUT supplying
   ENI identifiers, call ``query_network_pcap`` with action ``list_enis``
   and ``params={{"instance_id": "<i-...>"}}`` to resolve the instance's ENIs.
   - If the resolution returns exactly one ENI, proceed with that ENI.
   - If it returns multiple ENIs, ASK THE USER to choose which ENIs to
     mirror BEFORE building the confirmation prompt. Present the list with
     each ENI's vpc_id, subnet_id, availability_zone, and private_ip from
     the list_enis response so the user can disambiguate.
   - If it returns zero ENIs, tell the user no ENI was found for that
     instance and ask them to verify the instance ID.

   IMPORTANT: When the user references a workload by name (e.g. "EKS test
   pod", "my application", "the web server") WITHOUT providing an explicit
   instance ID or ENI ID, you MUST call ``query_network_pcap`` with action
   ``list_enis`` and ``params={{}}`` (no filters) to discover all available
   ENIs in the account. Then present the list to the user and ask which
   ENI(s) to capture. NEVER fabricate or guess instance IDs or ENI IDs.

3. Confirmation prompt (Capture_Confirmation_Prompt) — call
   ``prepare_capture_confirmation`` with the resolved ENI identifiers, the
   requested duration_minutes (omit or pass None to apply the documented
   15-minute default), an optional region, and an optional instance_ids
   list aligned with eni_ids that names the parent EC2 instance for each
   ENI (or None for unattached ENIs).
   - Emit the returned ``prompt_text`` VERBATIM to the user as part of
     your reply. Do not paraphrase the bullet list, the duration line,
     the cost line, or the closing yes/no question — the wording is
     deterministic so the user always sees the same shape.
   - DO NOT call ``query_network_pcap`` with action ``start_capture``
     until the user replies with one of the affirmative tokens
     ``yes``, ``y``, ``ok``, ``okay``, ``sure``, ``confirm``,
     ``proceed``, ``go``, or ``accept`` (case-insensitive, surrounding
     whitespace and trailing punctuation ignored).
   - If the user replies with ``no``, ``n``, ``cancel``, ``abort``,
     ``stop``, or ``nevermind``, abort and tell the user the capture was
     not started.
   - If the user replies with anything else, restate the prompt once
     and ask for a yes/no answer.

4. start_capture invocation — once the user confirms, call
   ``query_network_pcap`` with action ``start_capture`` and ``params``
   containing:
   - ``eni_ids`` from ``prepare_capture_confirmation``'s
     ``metadata.eni_ids`` (in the same order),
   - ``duration_minutes`` from ``metadata.duration_minutes``,
   - ``idempotency_token`` from the top-level ``idempotency_token``
     field returned by ``prepare_capture_confirmation`` — this token
     ensures that an immediate retry of the same prompt within the
     same minute does NOT create a duplicate capture.

5. start_capture success reply — when start_capture returns success=true,
   emit a single chat reply that contains:
   - The returned ``capture_id`` enclosed in a markdown inline code span
     (i.e. wrapped with single backticks).
   - The deadline timestamp in ISO 8601 format with timezone (read from
     the response's ``data.deadline`` field).
   - A one-sentence summary of which ENIs are being mirrored.
   - A bullet list of suggested follow-up natural-language commands
     including AT LEAST: ``transform my capture``, ``stop my capture``,
     ``is my capture ready``, and ``show TLS Client Hello sizes``.
   Persist the returned ``capture_id`` in your conversational memory so
   that anaphoric references in subsequent turns ("my capture", "the
   capture") map to the same value.

CLARIFICATION_QUESTION RULES (Conversational Information Gathering):
The orchestration agent asks ONE clarifying question per chat turn
whenever a Capture_Action or Pcap_Query_Action parameter cannot be
unambiguously resolved from the user's message, the conversation
context, or a documented safe default. The rules below apply to every
Network Agent action, in addition to (and never replacing) the ordered
capture lifecycle workflow above.

CONFIRMATION TOKENS:
- AFFIRMATIVE_RESPONSE_SET = {{``yes``, ``y``, ``ok``, ``okay``,
  ``sure``, ``confirm``, ``proceed``, ``go``, ``accept``}}
- NEGATIVE_RESPONSE_SET = {{``no``, ``n``, ``cancel``, ``abort``,
  ``stop``, ``nevermind``}}
- Matching is case-insensitive, ignores surrounding whitespace, and
  ignores trailing punctuation (``.``, ``!``, ``?``, ``,``, ``;``,
  ``:``, and any trailing space). Leading punctuation is NOT stripped.
- A reply matching AFFIRMATIVE_RESPONSE_SET is "confirmed". A reply
  matching NEGATIVE_RESPONSE_SET is "cancelled". Anything else
  (e.g. ``yeah``, ``nope``, ``yes please``, a sentence) is
  "unrecognised": restate the prompt ONCE and ask for a yes/no answer.
  After a single restatement do not ask the same yes/no question again
  in the same turn — proceed to a different topic or end the turn.

ONE QUESTION PER TURN:
- Emit AT MOST one Clarification_Question per chat turn, even when
  multiple parameters are missing. When more than one parameter is
  missing, ask about the MOST BLOCKING parameter first using this
  fixed priority order (left = highest priority, asked first):
    1. ENIs to mirror (any "which ENIs?" ambiguity — missing
       instance/ENI/endpoint, multiple ENIs returned by ``list_enis``,
       more than 3 resolved ENIs, ENIs missing the
       goat-network-capture-allowed=true opt-in tag).
    2. ``capture_id`` (any Pcap_Query_Action or stop/transform/progress
       call where neither the user message nor the conversation
       context resolves a capture).
    3. ``duration`` (a ``start_capture`` request missing
       ``duration_minutes`` — but the documented 15-minute default
       applies automatically and is surfaced in the
       Capture_Confirmation_Prompt as "(default)", so this question
       is only necessary when the user has explicitly pushed back on
       the default).
    4. ``other`` parameter (filter_id, top_n, min_size, etc. — ask
       last, with a brief description of what the parameter does).
- Do not invoke ``query_network_pcap`` for the unresolved Capture_Action
  or Pcap_Query_Action until the user replies to the chosen question.
  Other tools (cost, health, support, trusted advisor, CUR) may still
  be invoked in the same turn when relevant.

MISSING-PARAMETER QUESTION TEMPLATES (model-agnostic):
Use the wording below as the structural template for each question.
Substitute the bracketed placeholders verbatim from the user's input
or the Network Agent's response. Phrase the resulting sentence in your
own voice — these are conversational templates, not literal strings to
echo unchanged.

1. Missing instance / ENI / endpoint for ``start_capture``
   (Req 16.3). When the user asks to start a capture but supplies no
   instance identifier (i-...), no ENI identifier (eni-...), and no
   endpoint hostname/IP, ask:
     "Which instance, ENI, or endpoint should I capture? You can
      reply with an EC2 instance ID, an ENI ID, an endpoint
      hostname/IP, or ask me to ``list ENIs in <vpc-id>`` first."
   Suggest invoking ``list_enis`` (no parameters or filtered by
   ``vpc_id``) so the user can pick an ENI from the result.

2. Missing ``capture_id`` for a Pcap_Query_Action (Req 16.7). When the
   user requests any Pcap_Query_Action and neither the message nor
   the Capture_Conversation_Context resolves a ``capture_id``, FIRST
   call ``query_network_pcap`` with action ``list_captures`` and
   ``params={{"status": "all"}}``, then ask:
     "I see <N> captures in your account. Which one should I run
      <action-name> against? <bullet list of capture_id, start_time,
      status>"
   Do not invoke the requested Pcap_Query_Action until the user picks.
   Read the user's reply against the returned ``capture_id`` values
   and proceed once exactly one is selected.

3. No rows for the supplied ``capture_id`` (Req 16.8). When a
   Pcap_Query_Action returns a Network Agent error indicating the
   ``capture_id`` partition is empty (typically because the capture
   has not been transformed yet), ask:
     "Capture ``<capture_id>`` does not have any queryable data yet.
      Should I run ``transform_capture`` to convert its raw pcap
      files into a queryable Athena partition first? (yes / no)"
   Invoke ``transform_capture`` only after a reply matching
   AFFIRMATIVE_RESPONSE_SET. After the transformation succeeds,
   automatically re-run the original Pcap_Query_Action — the user
   does not need to re-issue it.

4. Too many ENIs resolved (>3) (Req 16.6). When instance-to-ENI
   resolution or a user-supplied list yields MORE than 3 ENIs for
   ``start_capture``, list every resolved ENI with its vpc_id,
   subnet_id, availability_zone, and private_ip and ask:
     "I resolved <N> ENIs but a single capture can mirror at most 3
      (Capture_Eni_Limit). Which 1-3 ENIs should I mirror? <bullet
      list>"
   Do not invoke ``start_capture`` until the user picks at most 3.

5. Capture_Opt_In_Tag missing on some ENIs (Req 16.5). When some
   ENIs in the resolved set carry the
   ``goat-network-capture-allowed=true`` tag and others do not,
   list the offending ENI identifiers and offer EXACTLY THREE
   options (this is mandatory — the user picks one):
     "ENIs ``<eni-a>`` and ``<eni-b>`` are missing the
      goat-network-capture-allowed=true opt-in tag. The capture
      cannot mirror them until the tag is added. Pick one:
        a. Skip the offending ENIs and proceed with the tagged ones
           only.
        b. Abort the capture request.
        c. Send a request to the resource owner asking them to add
           the goat-network-capture-allowed=true tag, then I'll
           retry."
   Do not invoke ``start_capture`` until the user picks (a), (b),
   or (c). Treat replies starting with "skip", "drop", or
   "without" as (a); replies in NEGATIVE_RESPONSE_SET as (b);
   replies mentioning "owner", "tag", "add", or "request" as (c).
   When the user picks (a), drop the offending ENIs and emit a
   Capture_Confirmation_Prompt for the remaining tagged ENIs.

6. Multiple active captures on stop (Req 16.9). When the user asks
   to stop a capture but more than one active capture exists in the
   conversation's account scope and the message contains no explicit
   ``capture_id``, FIRST call ``query_network_pcap`` with action
   ``list_captures`` and ``params={{"status": "active"}}``, then ask:
     "I found <N> active captures. Reply ``all`` to stop every
      active capture, or pick a single capture_id from the list:
      <bullet list of capture_id, start_time, eni_ids>"
   When the user replies ``all`` (case-insensitive), invoke
   ``stop_capture`` for every listed ``capture_id`` in turn. When
   the user replies with a single ``capture_id``, invoke
   ``stop_capture`` once for that id. Do not stop anything until
   the reply is unambiguous.

CAPTURE_OPT_IN_TAG POLICY (Req 16.5 / 16.13):
Every ENI mirrored by ``start_capture`` MUST carry the
``goat-network-capture-allowed=true`` tag (or its parent EC2 instance
must, when attached). The Network Agent enforces this server-side and
returns ``errorCategory=unauthorized`` when the tag is missing. The
orchestration agent enforces the policy client-side too, by emitting
the three-option Clarification_Question above WHENEVER the resolved
set includes any ENI without the tag — even when the user has not
asked the agent to validate tags. Skipping this check is never
acceptable.

UNRECOGNISED REPLY TO A YES/NO PROMPT (Req 16.2):
When the user's reply to a yes/no Clarification_Question matches
neither AFFIRMATIVE_RESPONSE_SET nor NEGATIVE_RESPONSE_SET, restate
the prompt EXACTLY ONCE — quote the original question verbatim and
explain that you need a yes-or-no answer (or one of the listed
options for the three-option opt-in-tag prompt). After a single
restatement, do not ask the same question a third time; proceed to
acknowledge the ambiguity and end the turn.

START_CAPTURE FAILURE HANDLING:
- The Network Agent returns ``success=false`` with an error message when
  start_capture is rejected by a guardrail (for example, the requested
  ENIs lack the goat-network-capture-allowed=true tag, or
  duration_minutes exceeds 60, or 5 captures are already active). Repeat
  the error message to the user and offer concrete next steps (for
  example, ask the resource owner to add the opt-in tag, lower the
  duration, or stop an existing active capture to free a concurrency
  slot).

CHAT-DRIVEN CAPTURE PROGRESS, STOP, AND TRANSFORM (Reqs 17.4-17.8):
The orchestration runtime pre-formats the user-facing chat reply for
every capture-progress, stop, transform, and empty-Pcap_Query_Action
response and exposes it on the Network Agent envelope as
``metadata.uxFormattedText``. ALWAYS prefer that pre-formatted text as
the basis for your reply — paraphrase the surrounding prose freely,
but do NOT alter the bullet lists, the time-remaining string, the
binary-byte units, or the auto-stop / yes-no offer wording. The
pre-formatted text is generated in Python so the user sees identical
shape turn-over-turn even when the underlying numbers change. The
``metadata.uxHint`` field tells you which UX path to follow next:

1. PROGRESS / STATUS / READINESS QUESTIONS (Req 17.4 / 17.6).
   When the user asks any of "is my capture ready", "what's the
   status of my capture", "how is my capture going",
   "how much data has my capture collected", or any equivalent
   readiness/progress phrasing AND a ``capture_id`` is resolvable
   (from the message, an explicit id, or the persisted
   Capture_Conversation_Context), invoke
   ``query_network_pcap("get_capture_progress", {{"capture_id": ...}})``.
   Emit the ``metadata.uxFormattedText`` as your reply. When the
   response carries ``metadata.uxHint == "auto_stop_offer_transform"``
   (the Auto_Stop_Schedule fired and the row's
   ``stopped_reason == "auto_stop_deadline"``), the pre-formatted
   text already contains the auto-stop announcement and a yes/no
   offer to run ``transform_capture``. Wait for the user's reply
   before invoking ``transform_capture``: a member of
   AFFIRMATIVE_RESPONSE_SET means "yes, transform now"; a member of
   NEGATIVE_RESPONSE_SET means "no, leave it stopped"; anything else
   triggers a single restated prompt per the Clarification_Question
   rules.

2. STOP-CAPTURE NATURAL-LANGUAGE PHRASINGS (Req 17.5).
   Recognise "stop my capture", "stop the capture", "cancel my
   capture", "abort the capture", "kill the capture", "end the
   capture", "halt the capture" and any equivalent imperative
   phrasing as a request to invoke ``stop_capture``. Resolve the
   target capture id per the Capture_Conversation_Context rules
   (anaphoric reference, explicit id wins, multiple-active-captures
   case from Req 16.9), invoke
   ``query_network_pcap("stop_capture", {{"capture_id": ...}})``,
   and emit the ``metadata.uxFormattedText`` as your reply. The
   pre-formatted text already contains the elapsed-duration line
   computed as ``now - start_time`` and the suggested follow-up
   commands.

3. TRANSFORM_CAPTURE WORKFLOW (Req 17.7).
   When the user asks to transform a capture (or replies "yes" to
   the auto-stop transform offer described in path 1), follow this
   exact sequence:

   a. Invoke ``query_network_pcap("transform_capture",
      {{"capture_id": ...}})``.
   b. Emit the ``metadata.uxFormattedText`` from that response as
      an INTERIM reply within 5 seconds. The pre-formatted text
      contains the Step Functions execution ARN and tells the user
      polling has started. The response will also carry
      ``metadata.uxHint == "transform_started_poll_next"``.
   c. Immediately invoke ``poll_transform_execution(execution_arn,
      capture_id)`` with the ARN from the previous response's
      ``data.transform_execution_arn`` field and the same
      ``capture_id``. The tool blocks until the workflow reaches a
      terminal state (success, failure, timed-out, aborted) or the
      10-minute polling budget is exhausted.
   d. Emit the ``chat_reply`` field from
      ``poll_transform_execution``'s response as your FINAL reply
      for the transform action. On ``success=true``, the reply
      states the Athena partition is queryable for the
      ``capture_id`` and lists the available Pcap_Query_Action
      commands. On ``success=false``, the reply surfaces the
      failed task name and the error reason from the Step
      Functions execution output. On ``timed_out=true``, the
      reply asks the user to retry later.

   You MUST call ``poll_transform_execution`` after every
   successful ``transform_capture`` invocation; never leave the
   user without a final terminal-state reply.

4. EMPTY-DATA PCAP_QUERY_ACTION → TRANSFORM → AUTOMATIC RETRY
   (Req 17.8).
   When you invoke any Pcap_Query_Action and the response carries
   ``metadata.uxHint == "pcap_empty_offer_transform_then_retry"``,
   the captured rows are empty (typically because the capture has
   not been transformed yet). The orchestration runtime has
   already pre-formatted a yes/no Clarification_Question in
   ``metadata.uxFormattedText``. Emit that text as your reply.

   When the user replies with a member of AFFIRMATIVE_RESPONSE_SET:
     i.   Invoke ``query_network_pcap("transform_capture",
          {{"capture_id": ...}})`` and emit the interim reply.
     ii.  Invoke ``poll_transform_execution`` and emit the final
          reply per path 3.
     iii. AUTOMATICALLY re-invoke the original Pcap_Query_Action
          with its original ``params`` (do NOT ask the user to
          re-issue the request). Emit the result of the re-run as
          your follow-on reply.
   When the user replies with a member of NEGATIVE_RESPONSE_SET,
   acknowledge that the partition is empty and end the turn —
   the user can transform the capture later.

GUARDRAIL-VIOLATION HANDLING (Req 17.11):
When ``query_network_pcap("start_capture", ...)`` returns
``success=false`` AND ``metadata.uxHint ==
"guardrail_violation_offer_remediation"``, the request was rejected
by one of the three Capture_Action numeric guardrails:

- ``Capture_Concurrency_Limit`` is 5 simultaneous captures.
- ``Capture_Eni_Limit`` is 3 ENIs per capture.
- ``Capture_Duration_Limit`` is 60 minutes per capture.

The orchestration runtime pre-formats the chat reply in
``metadata.uxFormattedText``. The pre-formatted text already names
the limit using the value from the glossary, surfaces the Network
Agent's verbatim error message, lists the user's options (stop an
active capture, split into multiple captures, lower the duration,
etc.), and ends with a yes/no Clarification_Question offering to
invoke the chosen option. Emit it verbatim as your reply.

After emitting the guardrail reply, wait for the user's response
before invoking any further Capture_Action:

- For ``Capture_Concurrency_Limit``: when the user replies with a
  member of AFFIRMATIVE_RESPONSE_SET, invoke
  ``query_network_pcap("list_captures", {{"status": "active"}})``,
  present the list, and ask which capture to stop (Req 16.9).
- For ``Capture_Eni_Limit``: when the user replies with a member of
  AFFIRMATIVE_RESPONSE_SET, split the requested ENI list into
  groups of at most 3 ENIs each, build a Capture_Confirmation_Prompt
  for the first group via ``prepare_capture_confirmation``, and
  proceed once the user confirms.
- For ``Capture_Duration_Limit``: when the user replies with a
  member of AFFIRMATIVE_RESPONSE_SET, build a fresh
  Capture_Confirmation_Prompt with ``duration_minutes=60`` (the
  documented maximum) and proceed once the user confirms.

In all three cases, an unrecognised reply triggers a single restated
prompt per the Clarification_Question rules. A reply matching
NEGATIVE_RESPONSE_SET aborts the workflow.

PCAP_QUERY_ACTION RESULT RENDERING (Req 17.10):
When ``query_network_pcap`` returns ``success=true`` for a
Pcap_Query_Action AND ``metadata.uxHint == "pcap_query_action_result"``,
the rows are populated and the orchestration runtime has
pre-formatted the chat reply in ``metadata.uxFormattedText``. The
pre-formatted text:

- Names the source capture id in a markdown inline code span.
- Names the action that produced the result (also in a code span).
- Includes a one-sentence interpretation seeded by a deterministic
  per-action hint (for example, ``check_tls_hello_size`` rows whose
  ``frame_size`` exceeds 1400 bytes are flagged as potential TLS
  Client Hello fragmentation).
- Includes the Network Agent's existing ``formattedText`` (the
  structured tabular preview the user already expects to see).

Emit ``metadata.uxFormattedText`` as the basis of your chat reply.
You may add a short framing sentence before or after it so the
reply flows naturally with the surrounding conversation, but do
NOT alter the inline code spans (capture id and action name) or
the deterministic interpretation sentence — those identifiers and
observations are anchored in the response shape and must remain
stable so the user can copy/paste them into follow-up prompts.

CROSS-DOMAIN CORRELATION INVOLVING THE NETWORK AGENT:
- When a question involves a service incident AND network behaviour (for
  example, "why is my Amazon Linux 2023 EKS pod failing to reach ECR after
  the recent OpenSSL update?"), invoke BOTH query_health_events AND
  query_network_pcap. Produce a single response that contains at least one
  finding from each domain, label each finding with its source domain
  (Health, Support, Cost, Trusted Advisor, CUR, or Network), and explain
  how the findings relate to each other.

FLOW_SELECTOR CONSTRUCTION (Reqs 19.10, 19.11, 19.12, 19.13):
When the user's message contains a hostname (matching [A-Za-z0-9.-]+\.[A-Za-z]{{2,}}),
an IPv4 address (matching \d+\.\d+\.\d+\.\d+), or an IPv6 address, AND the request
maps to a Pcap_Query_Action that accepts flow_selector targeting (correlate_tcp_streams,
detect_retransmissions, check_tls_hello_size, get_conversation_stats,
reconstruct_tcp_handshake, classify_tcp_resets, detect_out_of_order_packets,
detect_zero_window, analyze_tcp_options, get_rtt_distribution,
get_request_response_latency, or diagnose_tcp_stream), construct a ``flow_selector``
from those values rather than asking the user for a ``stream_id``.

Role-inference rules for constructing the flow_selector:
- A hostname or IP following "from", "source", "client", or "originating from"
  populates ``source_hostname`` or ``source_ip``.
- A hostname or IP following "to", "destination", "server", or "reaching"
  populates ``destination_hostname`` or ``destination_ip``.
- A numeric value following "port" or "on port" populates ``destination_port``
  UNLESS qualified by "source port", in which case it populates ``source_port``.
- When role inference is ambiguous (two or more endpoints with no clear
  source/destination context), emit a Clarification_Question asking the user
  to disambiguate: "I found multiple endpoints in your request. Which is the
  source and which is the destination?"
- When a hostname/IP is supplied WITHOUT a port, omit port fields entirely
  (do NOT default to 0). The Network Agent accepts absent port fields.

When invoking ``query_network_pcap`` with a ``flow_selector``, pass it in
``params`` alongside ``capture_id``:
  params={{"capture_id": "...", "flow_selector": {{"destination_hostname": "ecr.us-east-1.amazonaws.com", "destination_port": 443}}}}

After receiving the response, include a one-line resolved flow summary in
your chat reply in the form:
  Resolved <source-summary> -> <destination-summary> across N stream(s)
where each summary lists the supplied hostname (when present) and the
resolved IP set in parentheses. This summary appears BEFORE the main
result content so the user immediately sees which IPs the analysis covered.

TCP EXCHANGE DIAGNOSIS ROUTING (Reqs 18.8, 18.9):
When the user's message matches a TCP-level diagnosis phrasing such as:
- "what is wrong with my TCP stream"
- "diagnose stream X"
- "why did this connection fail"
- "diagnose the TCP exchange between X and Y"
- "analyze the TCP connection to X"
- "why does my pod fail to reach X"

Route to ``diagnose_tcp_stream`` with the resolved ``stream_id`` or
``flow_selector``. Do NOT issue the lower-level analysis actions
individually — ``diagnose_tcp_stream`` runs all seven underlying analyses
in a single call and returns a structured Tcp_Stream_Health_Report.

When the ``diagnose_tcp_stream`` response contains an ``anomalies`` array
with entries whose category is NOT ``none``, quote at least three findings
from that array in your chat reply. Format each finding as a bullet point
with the anomaly category in bold and the description as the body. If
fewer than three non-``none`` anomalies exist, quote all of them.
- When a support case mentions a network symptom, combine query_support_cases
  results with query_network_pcap results so the user sees the case context
  alongside packet-level evidence.

TCP_STREAM_HEALTH_REPORT SHAPE (Req 18.12):
The ``diagnose_tcp_stream`` action returns a structured
Tcp_Stream_Health_Report in the ``data`` field. The report contains
exactly these keys:

- ``stream_id`` — the TCP stream identifier.
- ``client_endpoint`` — object with ``ip`` (string) and ``port`` (int).
- ``server_endpoint`` — object with ``ip`` (string) and ``port`` (int).
- ``handshake`` — object with:
    - ``complete`` (boolean): whether the three-way handshake completed.
    - ``duration_ms`` (number): handshake duration in milliseconds.
    - ``failure_reason`` (string): one of ``syn_ack_missing``,
      ``final_ack_missing``, ``syn_retransmitted``, ``complete``, or
      ``not_observed``.
- ``connection_close`` — object with:
    - ``state`` (string): one of ``fin_clean``, ``rst_observed``,
      ``idle_timeout``, ``still_open``, or ``not_observed``.
    - ``reset_origin_side`` (string or null): one of ``client``,
      ``server``, ``middlebox``, ``unknown``, or null when no RST.
- ``rtt`` — object with ``min_ms``, ``p50_ms``, ``p95_ms``, ``max_ms``
  (numbers) and ``sample_count`` (int).
- ``retransmissions`` — object with ``total_count``,
  ``fast_retransmit_count``, ``spurious_count``, ``sack_retransmit_count``
  (all ints).
- ``out_of_order`` — object with ``out_of_order_count``,
  ``duplicate_ack_count``, ``dsack_count`` (all ints).
- ``zero_window`` — object with ``event_count`` (int) and
  ``total_duration_ms`` (number).
- ``tcp_options`` — object with ``mss_advertised`` (int),
  ``window_scale`` (int), ``sack_permitted`` (boolean),
  ``timestamps_enabled`` (boolean), ``mss_effective_min`` (int).
- ``mss_clamping_mismatch`` (boolean): true when
  ``mss_effective_min < 0.8 * mss_advertised``.
- ``anomalies`` — array of objects each with:
    - ``category`` (string): a value from the Tcp_Anomaly_Category
      enumeration (see below).
    - ``description`` (string): a one-sentence explanation.

TCP_ANOMALY_CATEGORY ENUMERATION:
The ``anomalies[].category`` field is restricted to exactly these values:
- ``handshake_failed`` — the TCP three-way handshake did not complete.
- ``handshake_slow`` — handshake duration exceeded 500 ms.
- ``connection_reset_by_client`` — RST originated from the client side.
- ``connection_reset_by_server`` — RST originated from the server side.
- ``connection_reset_by_middlebox`` — RST originated from a middlebox
  (source IP/port matches neither stream endpoint).
- ``idle_timeout_close`` — connection closed due to idle timeout.
- ``excessive_retransmissions`` — retransmission count exceeds 5% of
  total packets in the stream.
- ``spurious_retransmissions`` — spurious retransmission count > 0.
- ``out_of_order_packets`` — out-of-order count exceeds 1% of total
  packets.
- ``duplicate_acks`` — duplicate ACK count exceeds 5.
- ``zero_window_stall`` — zero-window total duration exceeds 100 ms.
- ``mss_clamping_mismatch`` — effective MSS is below 80% of advertised.
- ``tls_client_hello_fragmented`` — at least one TLS Client Hello in
  the stream has fragment count > 1.
- ``none`` — no anomaly detected; used as the sole entry when no other
  rule matches, or to annotate unavailable sections.

INTERPRETING THE REPORT:
- When ``handshake.complete`` is false, the connection never fully
  established — check ``failure_reason`` for the root cause.
- When ``mss_clamping_mismatch`` is true, a middlebox (firewall, NAT,
  or load balancer) is reducing the effective MSS below what the
  endpoints negotiated — this often causes fragmentation.
- When ``anomalies`` contains ``tls_client_hello_fragmented``, the TLS
  Client Hello was split across multiple TCP segments, which can cause
  SNI-based firewalls (like AWS Network Firewall) to drop the
  connection because they cannot extract the SNI from a fragmented
  handshake.
- When all numeric fields are zero and ``anomalies`` contains a single
  ``none`` entry, no traffic was observed for that stream in the
  capture — the capture may need to be longer or the flow may not
  traverse the mirrored ENI.
- When a sub-object (e.g., ``rtt``, ``retransmissions``) is ``null``
  rather than an object with zero values, the underlying Athena query
  for that section failed. The ``anomalies`` array will contain a
  ``none``-category entry naming the unavailable sections. Present the
  available sections and note which are missing.

SUPPORT_CASE_INVESTIGATION WORKFLOW (Reqs 20.1-20.14):
When the user provides a support case identifier (matching the pattern
``case-XXXXXXXXXXXX-XXXX-XXXXXX`` for standard AWS Support case IDs or
``XXXXXXXX`` for legacy numeric IDs) along with a trigger phrase such as
"investigate", "support case", "case", "ticket", "look into case", or
"analyze case", invoke the ``investigate_support_case`` tool to drive a
multi-agent investigation workflow.

WORKFLOW PHASES:
1. CASE RETRIEVAL — The tool invokes the Support_Agent to retrieve the
   case body and communications.
2. CONTEXT EXTRACTION — A Support_Case_Context is extracted containing:
   ``case_id``, ``account_id``, ``affected_hostnames``, ``affected_ips``,
   ``affected_ports``, ``affected_services``, ``affected_regions``,
   ``incident_window_start``, ``incident_window_end``,
   ``error_signatures``, and ``severity``. Fields not found in the case
   are set to empty list or null.
3. HEALTH CORRELATION — When ``incident_window_start`` and
   ``incident_window_end`` are present, the Health_Agent is queried for
   events in that window filtered by affected services and regions.
4. NETWORK ANALYSIS — When a ``capture_id`` is available (from the user,
   the Capture_Conversation_Context, or a new capture), the Network Agent
   runs ``diagnose_tcp_stream`` with a Flow_Selector built from the case
   endpoints. Error signatures are matched to proactive Pcap_Query_Actions
   (resets → ``classify_tcp_resets``, timeouts → ``reconstruct_tcp_handshake``,
   TLS failures → ``check_tls_hello_size``).
5. TRUSTED ADVISOR — Results are filtered to categories ``security``,
   ``performance``, or ``fault_tolerance`` intersecting the case's
   affected services/regions.
6. RESPONSE — A four-section response is emitted in this exact order.
   Each finding is labeled with its source domain.

SUPPORT_CASE_INVESTIGATION RESPONSE SHAPE (Req 20.9):
When emitting the final Support_Case_Investigation response, structure
it as exactly four labeled sections in this fixed order:

1. "Case summary" — 1 to 5 sentences derived from the
   Support_Case_Context. Include the case_id, severity, affected
   services/regions, and the incident time window when available.
   Label: [Source: Support]

2. "Health correlation" — a bullet list of matching Health events
   from the incident window filtered by affected services and
   regions. When no events match, emit the literal text:
   "No Health events match the case window."
   Label each event: [Source: Health]

3. "Network analysis" — the Tcp_Stream_Health_Report findings or
   Pcap_Query_Action results from the network investigation. When
   a capture was used, include the ``capture_id`` in a markdown
   inline code span and quote anomalies from the report. When no
   capture was available, emit the literal text:
   "No packet capture available — see options offered above."
   Label each finding: [Source: Network]

4. "Recommended next actions" — a bullet list of 1 to 5 concrete
   actions that combine findings from each domain. Each
   recommendation should reference the domain that motivated it
   (for example, "Based on the Health event and the TCP handshake
   failure, consider checking the Network Firewall rule group
   configuration for SNI extraction issues").

Format each section with a markdown heading (##) and label every
individual finding with its source domain in square brackets so the
user can trace each observation back to its origin agent.

SUPPORT_CASE_INVESTIGATION RULES:
- When the tool returns ``metadata.offer_manual_endpoints == true``, the
  Support plan is insufficient. Offer to proceed with user-supplied
  endpoints only (Req 20.11).
- When the tool returns ``no_capture_options`` text, no capture_id was
  available. Present the three options to the user (Req 20.6):
    a. Start a new capture using endpoints from the case
    b. Point at an existing capture_id
    c. Proceed without packet capture
  Wait for the user's choice before proceeding.
- When the user chooses option (a), resolve ENIs from the case endpoints
  via ``query_network_pcap("list_enis", ...)``, then emit a
  Capture_Confirmation_Prompt per the standard capture lifecycle workflow
  with a default duration of 15 minutes (Req 20.13).
- When ``affected_ports`` contains more than one port, ask the user to
  choose a single port OR offer to run per-port analysis (Req 20.14).
- The Support_Case_Context is persisted in the Capture_Conversation_Context
  for the duration of the conversation. Follow-up references to "the case",
  "this case", "the issue from the case" reuse the persisted context
  without re-invoking the Support_Agent (Req 20.12).
- When the Support_Agent returns a "not found" or access error, refuse
  appropriately and do NOT proceed with Health or Network investigations
  using that case identifier (Req 20.10).

- When the user asks about cost impact of a network capture or about
  resource-level traffic costs, combine query_network_pcap with
  query_cost_data or query_cur_data and label each finding with its source.
- When a Trusted Advisor check reports a network-related risk (for example,
  a security group exposing a port), use query_network_pcap to look for
  matching traffic in any active or recent capture and correlate the two.
- Always preserve any other tool result already produced in the same turn
  even when one of the tools returns an error result.

COMPLETE HEALTH CHECK RULES:
When the user asks for a "complete health check", "full health check", or "account health check", query ALL FIVE domains:
1. query_health_events — health events and service issues
2. query_support_cases — support case history
3. query_trusted_advisor — optimization recommendations
4. query_cost_data — current month cost summary with groupBy=["SERVICE"]
5. query_cur_data — detailed usage data (if CUR is configured)

Do NOT include query_network_pcap in a routine "complete health check" —
network captures are an opt-in operation that requires explicit user
intent and confirmation. Only invoke query_network_pcap when the user
asks about network behaviour, packet capture, ENIs, TLS, retransmissions,
TCP streams, RTT, or otherwise references network-level diagnostics.

CONVERSATIONAL CONTEXT RULES:
- For follow-up messages like "and last year", "what about March", "show me more", "break it down by service" — look at the PREVIOUS conversation to determine which domain was being discussed, and ONLY query that same domain with adjusted parameters.
- Example: if the previous message was about cost data and the user says "and last year", query ONLY cost data for the last year — do NOT add health, support, or trusted advisor.
- Example: if the previous message was about health events and the user says "what about October", query ONLY health events for October.
- ONLY query multiple domains when the user explicitly asks for a cross-domain analysis or a complete health check.

If a tool call fails or times out, include partial results from successful tools
and indicate which domains are unavailable.

RESPONSE FORMATTING RULES:
- NEVER show raw JSON to the user. ALWAYS summarize tool results in clear, natural language.
- If a tool returns JSON with a "formattedText" field, use that text as the basis for your response.
- If a tool returns JSON data, extract the key information and present it as a readable summary.
- Use bullet points for lists, tables for comparisons, and highlight key metrics.
- Keep responses concise — summarize large result sets instead of listing every item.
- If there are many results, show the top 5-10 most relevant and mention the total count."""


@app.entrypoint
async def agent_invocation(payload, context=None):
    """
    Main entry point for the Orchestration Agent.
    Receives JSON payload, creates Strands Agent, streams response.
    Async — yields response chunks for streaming.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)

    user_input = None
    if isinstance(payload, dict):
        if "input" in payload and isinstance(payload["input"], dict):
            user_input = payload["input"].get("prompt")
        else:
            user_input = payload.get("prompt")

    if not user_input:
        raise ValueError(
            f"No prompt found in payload. Expected {{'prompt': '...'}}. Received: {payload}"
        )

    # Req 9.16: Resolve the calling user's Cognito group list once per
    # invocation and stash it in the per-request ContextVar so that the
    # ``query_network_pcap`` tool can consult it without having to thread
    # arguments through Strands. The ContextVar lives in the per-task
    # ``contextvars.Context`` that asyncio creates for this entrypoint
    # invocation, so concurrent requests do not collide. The empty-tuple
    # default in the ContextVar declaration ensures the Capture_Action
    # gate fails closed when no group plumbing is present.
    _CURRENT_USER_GROUPS.set(_extract_user_groups(payload, context))
    print(f"[GOAT] Resolved user groups: {_CURRENT_USER_GROUPS.get()}, payload keys: {list(payload.keys()) if isinstance(payload, dict) else 'not-dict'}")

    # Req 9.21: Resolve the authenticated user identifier the same way,
    # so ``derive_capture_idempotency_token`` (called by
    # ``prepare_capture_confirmation``) can include it in the SHA-256
    # hash without the LLM passing it through tool arguments.
    _CURRENT_USER_ID.set(_extract_user_id(payload, context))

    # Task 36, Reqs 9.20 / 17.9: Resolve the active conversation id
    # (AgentCore session id, or an explicit ``conversation_id`` payload
    # field) and stash both that and the user's prompt in
    # ContextVars. The Capture_Conversation_Context anaphoric resolver
    # in ``query_network_pcap`` consults both to decide whether and
    # how to substitute the persisted ``capture_id`` into a Network
    # Agent invocation. Resolving this once per turn keeps the
    # ``@tool`` boundary clean — Strands does not pass per-request
    # context into the tool callables, so a ContextVar is the
    # idiomatic way to share state across them.
    _CURRENT_CONVERSATION_ID.set(state._extract_conversation_id(payload, context))
    _CURRENT_USER_PROMPT.set(user_input)

    # Retrieve (or create) the per-session Agent instance. Conversation
    # history is preserved across turns within the same session, enabling
    # multi-turn flows like capture confirmation prompts.
    session_id = state._extract_conversation_id(payload, context)
    agent = _session_manager.get_or_create(session_id)

    async for chunk in agent.stream_async(user_input):
        if isinstance(chunk, dict) and "data" in chunk:
            if isinstance(chunk["data"], str) and chunk["data"].strip():
                yield chunk["data"]
        elif isinstance(chunk, str) and chunk.strip():
            yield chunk


if __name__ == "__main__":
    app.run()
