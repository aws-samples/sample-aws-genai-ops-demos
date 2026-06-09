"""
Capture_Conversation_Context persistence layer for the orchestration agent
(Reqs 9.20, 17.9, Task 36).

This module provides the per-conversation state store the orchestration
agent uses to remember the most recently created ``capture_id`` (and the
extracted Support_Case_Context, when applicable) across chat turns. The
store enables anaphoric resolution: when a user types "stop my capture"
or "show TLS Hello sizes", the orchestration agent substitutes the
persisted ``capture_id`` into the resulting ``query_network_pcap``
invocation rather than asking the user to repeat the identifier.

Storage backend
---------------

We piggyback on the existing G.O.A.T. **Conversations** DynamoDB table
(``goat-conversations-{account}-{region}``) provisioned by the shared
``DataStack``. The table already uses the access pattern
``PK=USER#<userId>`` and ``SK=CONV#<conversationId>`` for chat
transcripts, with a 90-day TTL on the ``TTL`` attribute. We add a
**second** sort-key prefix scoped to the same partition:

    PK = USER#<userId>
    SK = CTX#CAPTURE#<conversationId>      # Capture_Conversation_Context entry

The two sort-key spaces (``CONV#`` and ``CTX#``) live in the same
partition so that a single ``Query`` against ``PK=USER#<userId>`` can
retrieve everything related to one user, while ``begins_with(SK,
'CONV#')`` and ``begins_with(SK, 'CTX#')`` cleanly separate the
chat transcript rows (owned by the frontend) from the
Capture_Conversation_Context rows (owned by the orchestration agent).
This avoids provisioning a second DynamoDB table for what is
essentially a per-conversation key-value lookup, while keeping the
frontend's existing data model untouched.

The stored item carries the same ``TTL`` attribute the chat rows do,
so it inherits the 90-day archival rule. A successful ``stop_capture``
or ``transform_capture`` does not delete the row — the design (Task
36, bullet 4) requires that a stopped capture be **replaced** when a
new capture starts, not cleared. Keeping the entry around between
those events is what enables the user to refer back to "the capture
I stopped earlier" within the same conversation.

Conversation identifier
-----------------------

The AgentCore ``RequestContext`` exposes ``session_id`` (extracted from
the ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`` header set by the
caller). When the frontend invokes the orchestration runtime it can
either supply an explicit ``conversation_id`` in the payload or rely
on the AgentCore-issued session id. The
``_extract_conversation_id`` helper inspects both sources and returns
a stable string. When neither is available — for example during local
unit tests outside the AgentCore runtime — it returns the empty
string and ``record_capture_context`` becomes a no-op so the rest of
the orchestration logic continues to function.

User identifier
---------------

Reuses the same ``_extract_user_id`` resolver that ``main.py`` uses
for the Capture_Idempotency_Token (Req 9.21). When no user id is
available the entries are stored under the synthetic ``anonymous``
user so the same record-and-resolve path still works in local tests.

Anaphoric reference detection
-----------------------------

``contains_capture_anaphor`` is a small, deterministic regex-based
detector for the common natural-language phrasings the requirements
document calls out: "my capture", "the capture", and equivalents
("our capture", "this capture", "current capture", "active capture",
"the running capture", "my running capture", "the active capture",
plus bare phrasings like "stop the capture", "transform my capture",
"is my capture ready"). The detector intentionally errs on the side
of recall because the LLM will already have routed the request to a
``query_network_pcap`` invocation, so a false positive here just
means we substitute a ``capture_id`` the user might also have
supplied — and ``substitute_persisted_capture_id`` only substitutes
when no ``capture_id`` is in the params.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import boto3
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#: Environment variable that resolves the Conversations table name. The
#: ``OrchRuntimeStack`` plumbs ``CONVERSATIONS_TABLE_NAME`` through to the
#: container; reading it lazily means tests can override it via
#: ``monkeypatch.setenv`` before importing the module.
CONVERSATIONS_TABLE_ENV = "CONVERSATIONS_TABLE_NAME"

#: TTL window for Capture_Conversation_Context rows. Matches the chat
#: transcripts' archival rule so the row falls off at the same time the
#: chat history does.
_CONTEXT_TTL_DAYS = 90
_CONTEXT_TTL_SECONDS = _CONTEXT_TTL_DAYS * 24 * 60 * 60

#: Sort-key prefix that namespaces the orchestration agent's
#: Capture_Conversation_Context rows so they cannot collide with the
#: frontend's ``CONV#`` chat transcripts in the same partition.
_CONTEXT_SK_PREFIX = "CTX#CAPTURE#"

#: Synthetic user identifier used when no Cognito-derived user is
#: available (local tests, the Auto_Stop_Schedule path that has no user
#: at all). The orchestration agent never persists production traffic
#: under this identity at runtime — it falls through to the empty-tuple
#: group list and the Capture_Action gate refuses anyway.
_ANONYMOUS_USER_ID = "anonymous"

#: Synthetic conversation identifier used when neither the AgentCore
#: ``session_id`` nor an explicit ``conversation_id`` payload field is
#: available. ``record_capture_context`` becomes a no-op in this state
#: because storing under a single synthetic conversation across
#: requests would let unrelated requests read each other's
#: ``capture_id`` — a strict regression of conversation scoping. The
#: explicit empty string makes the no-op behaviour observable and
#: testable.
_NO_CONVERSATION_ID = ""

#: Set of regular-expression patterns matched (case-insensitive) against
#: a chat prompt to decide whether the user was making an anaphoric
#: reference to "their capture" rather than supplying a fresh
#: identifier. The ordering does not matter — we OR-match any pattern.
_CAPTURE_ANAPHOR_PATTERNS = (
    # Possessive phrasings
    r"\bmy\s+(?:active\s+|running\s+)?capture\b",
    r"\bour\s+(?:active\s+|running\s+)?capture\b",
    # Demonstrative phrasings
    r"\bthe\s+(?:active\s+|running\s+|current\s+)?capture\b",
    r"\bthis\s+(?:active\s+|running\s+|current\s+)?capture\b",
    r"\bthat\s+(?:active\s+|running\s+|current\s+)?capture\b",
    # Bare phrasings — verbs immediately preceding "capture" without a
    # determiner are still anaphoric ("stop capture" feels imperative
    # but in practice maps to "stop the capture" in chat).
    r"\b(?:stop|cancel|abort|transform|show|check|describe|inspect|status|status\s+of|progress\s+of|is)\s+capture\b",
    # Catch follow-up questions like "is capture ready"
    r"\bcapture\s+(?:ready|done|finished|complete)\b",
)

#: Compiled detector — built once at module load. ``re.IGNORECASE`` is
#: applied via the ``(?i)`` inline flag in the joined alternation.
_CAPTURE_ANAPHOR_RE = re.compile(
    "(?i)(" + "|".join(_CAPTURE_ANAPHOR_PATTERNS) + ")"
)


# ---------------------------------------------------------------------------
# Lazy DynamoDB table resolver
# ---------------------------------------------------------------------------
_CACHED_TABLE: Optional[Any] = None
_CACHED_TABLE_NAME: Optional[str] = None


def _resolve_table() -> Optional[Any]:
    """Return a cached DynamoDB ``Table`` resource for the Conversations table.

    Resolution rules:

    1. Read ``CONVERSATIONS_TABLE_NAME`` from the environment. If unset
       or empty, return ``None`` — the persistence layer becomes a
       no-op so the orchestration agent still functions in local tests
       that have no AWS credentials.
    2. Cache the ``Table`` resource so subsequent calls reuse the
       same boto3 session.
    3. Re-resolve when the environment variable changes (covers the
       monkeypatch-style overrides that pytest fixtures apply).
    """
    global _CACHED_TABLE, _CACHED_TABLE_NAME
    table_name = os.environ.get(CONVERSATIONS_TABLE_ENV) or None
    if table_name is None:
        # No table configured — make every state-store call a no-op.
        # This keeps the agent runnable outside the deployment
        # environment without any setup ceremony.
        return None
    if _CACHED_TABLE is not None and _CACHED_TABLE_NAME == table_name:
        return _CACHED_TABLE
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    resource_kwargs: dict[str, Any] = {}
    if region:
        resource_kwargs["region_name"] = region
    _CACHED_TABLE = boto3.resource("dynamodb", **resource_kwargs).Table(table_name)
    _CACHED_TABLE_NAME = table_name
    return _CACHED_TABLE


def _reset_table_cache() -> None:
    """Clear the cached DynamoDB resource. Test-only helper."""
    global _CACHED_TABLE, _CACHED_TABLE_NAME
    _CACHED_TABLE = None
    _CACHED_TABLE_NAME = None


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _user_pk(user_id: Optional[str]) -> str:
    """Build the partition key for a user.

    Mirrors ``userPK`` in the frontend's ``conversations.ts`` so a
    future ``Query`` from either side hits the same partition.
    """
    return f"USER#{user_id or _ANONYMOUS_USER_ID}"


def _context_sk(conversation_id: Optional[str]) -> str:
    """Build the sort key for a Capture_Conversation_Context entry.

    The empty conversation id maps to the literal sentinel
    ``CTX#CAPTURE#`` so calls made without a session id do not
    silently overwrite each other across requests; the no-op check
    in :func:`record_capture_context` filters those calls out before
    they reach DynamoDB.
    """
    return f"{_CONTEXT_SK_PREFIX}{conversation_id or ''}"


def _compute_ttl_seconds(now: Optional[float] = None) -> int:
    """Return an epoch-seconds TTL ``_CONTEXT_TTL_SECONDS`` from now.

    ``now`` is overridable for deterministic unit tests; production
    code passes ``None`` and uses the wall clock.
    """
    base = time.time() if now is None else now
    return int(base) + _CONTEXT_TTL_SECONDS


# ---------------------------------------------------------------------------
# Conversation identifier extraction
# ---------------------------------------------------------------------------


def _extract_conversation_id(payload: object, context: object) -> str:
    """Resolve a stable per-conversation identifier for the current request.

    Inspection order (first non-empty wins):

    1. ``payload.conversation_id`` / ``payload.conversationId`` /
       ``payload.context.conversation_id`` — explicit identifier
       supplied by the caller. The frontend may set this when the user
       resumes a saved conversation; tests use it to pin a specific
       conversation without mocking AgentCore internals.
    2. ``context.session_id`` — the AgentCore-managed session id
       (``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id``), available
       on every InvokeAgentRuntime call. AgentCore always populates
       this so the conversation-scoping default works even when the
       frontend doesn't send an explicit id.
    3. Empty string — the persistence layer falls back to no-op so
       the orchestration logic continues to function.

    Args:
        payload: The deserialized request payload.
        context: The AgentCore ``RequestContext`` (or any object with
            a ``session_id`` attribute).

    Returns:
        The resolved conversation id, or the empty string when
        neither source provides one.
    """
    if isinstance(payload, dict):
        for key in (
            "conversation_id",
            "conversationId",
            "session_id",
            "sessionId",
        ):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        ctx_obj = payload.get("context")
        if isinstance(ctx_obj, dict):
            for key in (
                "conversation_id",
                "conversationId",
                "session_id",
                "sessionId",
            ):
                candidate = ctx_obj.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate

    if context is not None:
        candidate = getattr(context, "session_id", None)
        if isinstance(candidate, str) and candidate:
            return candidate

    return _NO_CONVERSATION_ID


# ---------------------------------------------------------------------------
# Capture_Conversation_Context CRUD
# ---------------------------------------------------------------------------


def record_capture_context(
    *,
    user_id: Optional[str],
    conversation_id: Optional[str],
    capture_id: str,
    eni_ids: Optional[Iterable[str]] = None,
    deadline: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    status: str = "active",
    stopped_reason: Optional[str] = None,
    extra_attributes: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Persist (or replace) the active Capture_Conversation_Context entry.

    Called immediately after a successful ``start_capture`` so future
    chat turns can resolve "my capture" / "the capture" anaphors to
    this ``capture_id``. Per Task 36 bullet 4, when a previous entry
    pointed to a ``stopped`` capture and the user starts a new one,
    the row is **replaced** with the new ``capture_id`` — the
    unconditional ``put_item`` semantics here implement that
    replacement directly.

    The function is fail-soft: any DynamoDB error is logged and
    swallowed so the caller continues without breaking the chat
    response. The returned object echoes the values that were
    written (or that would have been written if the table is
    unconfigured) so the caller can format a confirmation message.

    Args:
        user_id: Authenticated user identifier; the ``anonymous``
            placeholder is used when missing.
        conversation_id: AgentCore session id or
            payload-supplied conversation id; the call becomes a
            no-op when missing.
        capture_id: The ``capture_id`` returned by ``start_capture``.
        eni_ids: ENIs being mirrored (stored for follow-up summaries).
        deadline: ISO 8601 deadline timestamp.
        duration_minutes: Duration in minutes.
        status: Current capture status (``active``, ``stopped``,
            ``transformed``, ``queryable``, ``stopping_failed``).
        stopped_reason: Optional reason string when status is
            ``stopped`` (used by the auto-stop UX in Req 17.6).
        extra_attributes: Optional additional fields to merge into
            the stored item — used by Task 41 to layer the
            Support_Case_Context onto the same row without
            duplicating partition keys.
        now: Optional datetime for deterministic test runs.

    Returns:
        The dict that was (or would have been) written to DynamoDB.
        Always includes ``capture_id`` and ``conversation_id`` so the
        caller can echo them in a confirmation message.
    """
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    item: dict[str, Any] = {
        "PK": _user_pk(user_id),
        "SK": _context_sk(conversation_id),
        "capture_id": capture_id,
        "status": status,
        "user_id": user_id or _ANONYMOUS_USER_ID,
        "conversation_id": conversation_id or _NO_CONVERSATION_ID,
        "updated_at": timestamp,
        "TTL": _compute_ttl_seconds(),
    }
    if eni_ids is not None:
        # Stored as a list (DynamoDB string-list) so we don't depend on
        # ordering; ENIs in this entry are descriptive only.
        item["eni_ids"] = [str(e) for e in eni_ids if isinstance(e, str) and e]
    if deadline:
        item["deadline"] = deadline
    if duration_minutes is not None:
        item["duration_minutes"] = int(duration_minutes)
    if stopped_reason:
        item["stopped_reason"] = stopped_reason
    if extra_attributes:
        # Allow callers (Task 41) to merge their own fields, but
        # never let them clobber the partition keys or TTL.
        for key, value in extra_attributes.items():
            if key in {"PK", "SK", "TTL"}:
                continue
            item[key] = value

    # No-op fast paths.
    if not capture_id:
        logger.debug("record_capture_context: skipping write — empty capture_id")
        return item
    if not conversation_id:
        logger.debug(
            "record_capture_context: skipping write — no conversation id "
            "available (request likely came from outside an AgentCore session)"
        )
        return item

    table = _resolve_table()
    if table is None:
        logger.debug(
            "record_capture_context: skipping write — %s env var unset",
            CONVERSATIONS_TABLE_ENV,
        )
        return item

    try:
        table.put_item(Item=item)
    except ClientError as exc:
        logger.warning(
            "record_capture_context: PutItem failed user=%s conv=%s err=%s",
            user_id,
            conversation_id,
            exc.response.get("Error", {}).get("Code", "Unknown"),
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft, never raise
        logger.warning("record_capture_context: unexpected error: %s", exc)

    return item


def load_capture_context(
    *,
    user_id: Optional[str],
    conversation_id: Optional[str],
) -> Optional[dict]:
    """Return the active Capture_Conversation_Context entry, or ``None``.

    Returns ``None`` when:

    - the conversation id is empty (no scoping possible),
    - the table env var is unset,
    - DynamoDB returns no item, or
    - DynamoDB raises an error (logged and swallowed).
    """
    if not conversation_id:
        return None
    table = _resolve_table()
    if table is None:
        return None
    try:
        response = table.get_item(
            Key={
                "PK": _user_pk(user_id),
                "SK": _context_sk(conversation_id),
            },
            ConsistentRead=False,
        )
    except ClientError as exc:
        logger.warning(
            "load_capture_context: GetItem failed user=%s conv=%s err=%s",
            user_id,
            conversation_id,
            exc.response.get("Error", {}).get("Code", "Unknown"),
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_capture_context: unexpected error: %s", exc)
        return None
    return response.get("Item")


def update_capture_context_status(
    *,
    user_id: Optional[str],
    conversation_id: Optional[str],
    status: str,
    stopped_reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Update the ``status`` (and optional ``stopped_reason``) on the row.

    Called after ``stop_capture`` / ``transform_capture`` so the row
    reflects the up-to-date lifecycle state without rewriting the
    whole item. Failures are logged and swallowed.
    """
    if not conversation_id:
        return
    table = _resolve_table()
    if table is None:
        return
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    update_expression_parts = ["#s = :status", "#u = :updated_at"]
    expression_attribute_names: dict[str, str] = {"#s": "status", "#u": "updated_at"}
    expression_attribute_values: dict[str, Any] = {
        ":status": status,
        ":updated_at": timestamp,
    }
    if stopped_reason is not None:
        update_expression_parts.append("#r = :stopped_reason")
        expression_attribute_names["#r"] = "stopped_reason"
        expression_attribute_values[":stopped_reason"] = stopped_reason
    try:
        table.update_item(
            Key={
                "PK": _user_pk(user_id),
                "SK": _context_sk(conversation_id),
            },
            UpdateExpression="SET " + ", ".join(update_expression_parts),
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
        )
    except ClientError as exc:
        logger.warning(
            "update_capture_context_status: UpdateItem failed user=%s conv=%s err=%s",
            user_id,
            conversation_id,
            exc.response.get("Error", {}).get("Code", "Unknown"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_capture_context_status: unexpected error: %s", exc)


# ---------------------------------------------------------------------------
# Anaphoric reference detection and substitution
# ---------------------------------------------------------------------------


def contains_capture_anaphor(text: object) -> bool:
    """Return ``True`` iff ``text`` contains an anaphoric capture reference.

    Recognises the natural-language phrasings the requirements
    document calls out as anaphoric capture references: "my
    capture", "the capture", "this capture", possessive and
    demonstrative variants, plus bare verb-led phrasings like "stop
    capture", "transform capture", and the question forms "is
    capture ready" / "is capture done".

    The detector is intentionally lenient because the LLM has
    already routed the request to ``query_network_pcap``;
    substitution is gated on the params not already containing a
    ``capture_id`` so a false positive is harmless.

    Args:
        text: Any input — non-string inputs are coerced to ``False``.

    Returns:
        ``True`` when at least one anaphor pattern matches; ``False``
        otherwise.
    """
    if not isinstance(text, str):
        return False
    return _CAPTURE_ANAPHOR_RE.search(text) is not None


def substitute_persisted_capture_id(
    *,
    params: Optional[dict],
    persisted_capture_id: Optional[str],
) -> tuple[dict, bool]:
    """Inject ``persisted_capture_id`` into ``params`` when missing.

    Called by ``query_network_pcap`` immediately before forwarding a
    request to the Network Agent. Returns a new params dict with the
    ``capture_id`` key set when (a) ``params`` lacks a non-empty
    ``capture_id`` and (b) ``persisted_capture_id`` is non-empty.
    The boolean second return value reports whether a substitution
    occurred so the orchestration agent can mention it in any
    follow-up explanation if needed.

    Args:
        params: The raw ``params`` dict the LLM produced for the
            ``query_network_pcap`` invocation. ``None`` is treated
            as the empty dict.
        persisted_capture_id: The ``capture_id`` recovered from
            :func:`load_capture_context` (or supplied via the
            request payload). May be ``None`` when no context is
            available.

    Returns:
        A tuple ``(merged_params, did_substitute)``.
    """
    base = dict(params or {})
    if not persisted_capture_id:
        return base, False
    existing = base.get("capture_id")
    if isinstance(existing, str) and existing:
        return base, False
    base["capture_id"] = persisted_capture_id
    return base, True


# ---------------------------------------------------------------------------
# Support_Case_Context persistence (Task 41, Reqs 20.2, 20.12)
# ---------------------------------------------------------------------------


def record_support_case_context(
    *,
    user_id: Optional[str],
    conversation_id: Optional[str],
    support_case_context: dict,
    now: Optional[datetime] = None,
) -> dict:
    """Persist a Support_Case_Context in the Capture_Conversation_Context row.

    Called by the orchestration agent after extracting a
    Support_Case_Context from a support case body and communications.
    The context is layered onto the existing Capture_Conversation_Context
    row (if one exists) via ``extra_attributes`` so that both the
    ``capture_id`` and the ``support_case_context`` coexist on the same
    DynamoDB item. When no capture row exists yet, a placeholder row is
    created with ``capture_id`` set to empty string so the
    Support_Case_Context is still persisted and retrievable.

    Per Req 20.12, the Support_Case_Context is retained for the duration
    of the active conversation so follow-up requests referring to "the
    case" or "this case" can reuse it without re-invoking the Support_Agent.

    Args:
        user_id: Authenticated user identifier.
        conversation_id: Active conversation/session identifier.
        support_case_context: The extracted Support_Case_Context dict
            containing fields like ``case_id``, ``account_id``,
            ``affected_hostnames``, ``affected_ips``, etc.
        now: Optional datetime for deterministic test runs.

    Returns:
        The dict that was (or would have been) written to DynamoDB.
    """
    if not conversation_id:
        logger.debug(
            "record_support_case_context: skipping — no conversation id"
        )
        return {"support_case_context": support_case_context}

    # Load existing context to preserve capture_id if present
    existing = load_capture_context(
        user_id=user_id,
        conversation_id=conversation_id,
    )
    existing_capture_id = (
        existing.get("capture_id", "")
        if isinstance(existing, dict) else ""
    )

    return record_capture_context(
        user_id=user_id,
        conversation_id=conversation_id,
        capture_id=existing_capture_id or "",
        status=existing.get("status", "none") if isinstance(existing, dict) else "none",
        extra_attributes={"support_case_context": support_case_context},
        now=now,
    )


def load_support_case_context(
    *,
    user_id: Optional[str],
    conversation_id: Optional[str],
) -> Optional[dict]:
    """Return the persisted Support_Case_Context, or None.

    Reads the Capture_Conversation_Context row and extracts the
    ``support_case_context`` field. Returns None when no row exists
    or the field is absent.
    """
    item = load_capture_context(
        user_id=user_id,
        conversation_id=conversation_id,
    )
    if not isinstance(item, dict):
        return None
    ctx = item.get("support_case_context")
    return ctx if isinstance(ctx, dict) else None


def contains_support_case_anaphor(text: object) -> bool:
    """Return True if text contains an anaphoric reference to a support case.

    Recognises phrasings like "the case", "this case", "the issue from
    the case", "the ticket", "that support case", etc. Used by the
    orchestration agent to decide whether to reuse the persisted
    Support_Case_Context (Req 20.12) without re-invoking the Support_Agent.
    """
    if not isinstance(text, str):
        return False
    return _SUPPORT_CASE_ANAPHOR_RE.search(text) is not None


#: Patterns for anaphoric support case references (Req 20.12).
_SUPPORT_CASE_ANAPHOR_PATTERNS = (
    r"\bthe\s+(?:support\s+)?case\b",
    r"\bthis\s+(?:support\s+)?case\b",
    r"\bthat\s+(?:support\s+)?case\b",
    r"\bthe\s+(?:support\s+)?ticket\b",
    r"\bthis\s+(?:support\s+)?ticket\b",
    r"\bthat\s+(?:support\s+)?ticket\b",
    r"\bthe\s+issue\s+from\s+the\s+case\b",
    r"\bfrom\s+the\s+case\b",
    r"\bcase\s+(?:context|details|info)\b",
)

_SUPPORT_CASE_ANAPHOR_RE = re.compile(
    "(?i)(" + "|".join(_SUPPORT_CASE_ANAPHOR_PATTERNS) + ")"
)


__all__ = [
    "CONVERSATIONS_TABLE_ENV",
    "contains_capture_anaphor",
    "contains_support_case_anaphor",
    "load_capture_context",
    "load_support_case_context",
    "record_capture_context",
    "record_support_case_context",
    "substitute_persisted_capture_id",
    "update_capture_context_status",
    "_extract_conversation_id",  # exposed for the entrypoint
    "_reset_table_cache",  # exposed for tests
]
