"""
Unit and property-based tests for Capture_Conversation_Context anaphoric
resolution introduced by Task 36 (Reqs 9.20, 17.9).

Run from the ``orchestration-agent`` directory::

    python -m pytest test_capture_conversation_context.py -v

Scope:

- ``state.contains_capture_anaphor`` — natural-language detector for
  "my capture", "the capture", and the documented variants.
- ``state.substitute_persisted_capture_id`` — pure substitution helper.
- ``state.record_capture_context`` / ``load_capture_context`` /
  ``update_capture_context_status`` — DynamoDB persistence with a
  stubbed ``boto3.resource`` client. The stub records every put/get/
  update call so tests can assert the row layout.
- ``state._extract_conversation_id`` — payload/context inspection
  priority order.
- ``query_network_pcap`` — end-to-end behaviour:
    * anaphor + persisted id ⇒ params get the capture_id substituted
      and the response envelope carries
      ``metadata.resolvedCaptureIdFromContext``.
    * explicit capture_id wins over the persisted entry.
    * no anaphor ⇒ no substitution even when a persisted entry exists.
    * ``start_capture`` is excluded from substitution and instead
      writes a new entry on success (replacing any prior ``stopped``
      entry — Task 36 bullet 4).
    * ``stop_capture`` / ``transform_capture`` update the persisted
      row's status field.

The tests stub both ``main._invoke_network_agent`` and
``state._resolve_table`` so DynamoDB is never reached. The stub
``Table`` records every method call with arguments so behavioural
assertions stay in lockstep with the real ``boto3.resource``
contract.
"""

from __future__ import annotations

import json
import os
import string
from typing import Any, Optional

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

# AgentCore imports a region at module load. Set both env vars so
# importing main outside the AgentCore runtime succeeds (mirrors the
# pattern in the other test files in this directory).
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import main  # noqa: E402
import state  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query_network_pcap_callable():
    """Return the underlying Python function behind the ``@tool`` decorator.

    Strands wraps the function in ``DecoratedFunctionTool``; we reach
    for the underlying callable via known attribute names so the
    test stays compatible with minor SDK releases.
    """
    tool_obj = main.query_network_pcap
    for attr in ("original_function", "_function", "function", "fn"):
        candidate = getattr(tool_obj, attr, None)
        if callable(candidate):
            return candidate
    if callable(tool_obj):
        return tool_obj
    raise RuntimeError(
        "Could not resolve underlying callable for query_network_pcap"
    )


class _StubTable:
    """In-memory stand-in for a ``boto3.resource('dynamodb').Table(...)``.

    Records every ``put_item`` / ``get_item`` / ``update_item`` call
    so tests can inspect what the orchestration agent wrote to the
    Conversations table. Stores items keyed by ``(PK, SK)`` so
    ``record_capture_context`` and ``update_capture_context_status``
    interact in the same way they would against real DynamoDB.
    """

    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    # --- DynamoDB-style API ---------------------------------------------
    def put_item(self, *, Item: dict[str, Any]) -> dict[str, Any]:
        self.put_calls.append({"Item": dict(Item)})
        self.items[(Item["PK"], Item["SK"])] = dict(Item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, *, Key: dict[str, Any], ConsistentRead: bool = False) -> dict[str, Any]:
        self.get_calls.append({"Key": dict(Key), "ConsistentRead": ConsistentRead})
        item = self.items.get((Key["PK"], Key["SK"]))
        if item is None:
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}
        return {"Item": dict(item)}

    def update_item(
        self,
        *,
        Key: dict[str, Any],
        UpdateExpression: str,
        ExpressionAttributeNames: dict[str, str],
        ExpressionAttributeValues: dict[str, Any],
    ) -> dict[str, Any]:
        self.update_calls.append(
            {
                "Key": dict(Key),
                "UpdateExpression": UpdateExpression,
                "ExpressionAttributeNames": dict(ExpressionAttributeNames),
                "ExpressionAttributeValues": dict(ExpressionAttributeValues),
            }
        )
        existing = self.items.get((Key["PK"], Key["SK"])) or {}
        # Apply the SET clauses by mapping the placeholder names back
        # to actual attribute names so subsequent get_item calls
        # observe the updated value. This mimics DynamoDB's behaviour
        # closely enough for our assertions.
        for placeholder, attr_name in ExpressionAttributeNames.items():
            value_placeholder = ":" + attr_name.replace("_", "_")
            # Best-effort match — we use the same attribute-name suffix
            # when the helper builds the placeholder; a precise lookup
            # below handles the actual values the helper wrote.
        # Easier and more robust: reproduce the helper's mapping.
        # The helper always sets ``status``, ``updated_at``, and
        # optionally ``stopped_reason`` so we can hard-code that here.
        if ":status" in ExpressionAttributeValues:
            existing["status"] = ExpressionAttributeValues[":status"]
        if ":updated_at" in ExpressionAttributeValues:
            existing["updated_at"] = ExpressionAttributeValues[":updated_at"]
        if ":stopped_reason" in ExpressionAttributeValues:
            existing["stopped_reason"] = ExpressionAttributeValues[":stopped_reason"]
        existing["PK"] = Key["PK"]
        existing["SK"] = Key["SK"]
        self.items[(Key["PK"], Key["SK"])] = existing
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


@pytest.fixture
def stub_table(monkeypatch):
    """Replace ``state._resolve_table`` with a fresh in-memory stub."""
    table = _StubTable()
    monkeypatch.setattr(state, "_resolve_table", lambda: table)
    yield table


@pytest.fixture
def context_vars():
    """Reset the per-request ContextVars between tests for isolation."""
    tokens = []
    tokens.append(main._CURRENT_USER_GROUPS.set(("GOATNetworkCaptureUsers",)))
    tokens.append(main._CURRENT_USER_ID.set("alice"))
    tokens.append(main._CURRENT_CONVERSATION_ID.set("conv-test-1"))
    tokens.append(main._CURRENT_USER_PROMPT.set(""))
    yield
    for var, token in zip(
        (
            main._CURRENT_USER_GROUPS,
            main._CURRENT_USER_ID,
            main._CURRENT_CONVERSATION_ID,
            main._CURRENT_USER_PROMPT,
        ),
        tokens,
    ):
        var.reset(token)


# ---------------------------------------------------------------------------
# Anaphoric-reference detector
# ---------------------------------------------------------------------------


class TestContainsCaptureAnaphor:
    @pytest.mark.parametrize(
        "phrase",
        [
            "my capture",
            "MY CAPTURE",
            "my active capture",
            "my running capture",
            "the capture",
            "The Capture",
            "the active capture",
            "the running capture",
            "the current capture",
            "this capture",
            "that capture",
            "our capture",
            "stop my capture",
            "stop the capture",
            "stop capture",
            "cancel my capture",
            "cancel the capture",
            "cancel capture",
            "abort the capture",
            "abort capture",
            "transform my capture",
            "transform the capture",
            "transform capture",
            "show my capture",
            "show capture",
            "is my capture ready",
            "is the capture ready",
            "is capture ready",
            "is capture done",
            "is capture finished",
            "capture finished",
            "capture complete",
            "Stop my capture please.",
            "Please transform my capture and let me know when it's done.",
        ],
    )
    def test_documented_phrasings_are_detected(self, phrase):
        """Validates: Requirements 9.20."""
        assert state.contains_capture_anaphor(phrase) is True

    @pytest.mark.parametrize(
        "phrase",
        [
            "list ENIs",
            "show me cost data",
            "what is my AWS bill",
            "describe support cases",
            "transform cap-explicit-id-here for me",  # explicit id, no anaphor
            "start a 15-minute capture on instance i-0123456789abcdef0",
            # "start_capture" itself does not embed an anaphor — fresh
            # captures must always create new ids.
            "Trusted Advisor recommendations",
            "",
            "    ",
        ],
    )
    def test_non_anaphoric_phrasings_are_not_detected(self, phrase):
        """Validates: Requirements 9.20."""
        assert state.contains_capture_anaphor(phrase) is False

    @pytest.mark.parametrize("value", [None, 42, ["my capture"], {"text": "my capture"}])
    def test_non_string_inputs_return_false(self, value):
        assert state.contains_capture_anaphor(value) is False


# ---------------------------------------------------------------------------
# substitute_persisted_capture_id
# ---------------------------------------------------------------------------


class TestSubstitutePersistedCaptureId:
    def test_substitutes_into_empty_params(self):
        """Validates: Requirements 9.20."""
        merged, did = state.substitute_persisted_capture_id(
            params=None, persisted_capture_id="cap-abc"
        )
        assert merged == {"capture_id": "cap-abc"}
        assert did is True

    def test_substitutes_into_params_lacking_capture_id(self):
        """Validates: Requirements 9.20."""
        merged, did = state.substitute_persisted_capture_id(
            params={"stream_id": "s-7"}, persisted_capture_id="cap-abc"
        )
        assert merged == {"stream_id": "s-7", "capture_id": "cap-abc"}
        assert did is True

    def test_explicit_capture_id_wins(self):
        """Validates: Requirements 9.20."""
        merged, did = state.substitute_persisted_capture_id(
            params={"capture_id": "cap-explicit"}, persisted_capture_id="cap-abc"
        )
        assert merged == {"capture_id": "cap-explicit"}
        assert did is False

    def test_no_persisted_id_returns_unchanged(self):
        """Validates: Requirements 9.20."""
        merged, did = state.substitute_persisted_capture_id(
            params={"foo": "bar"}, persisted_capture_id=None
        )
        assert merged == {"foo": "bar"}
        assert did is False

    def test_empty_string_persisted_id_returns_unchanged(self):
        merged, did = state.substitute_persisted_capture_id(
            params=None, persisted_capture_id=""
        )
        assert merged == {}
        assert did is False

    def test_does_not_mutate_input_params(self):
        original = {"stream_id": "s-7"}
        state.substitute_persisted_capture_id(
            params=original, persisted_capture_id="cap-abc"
        )
        assert original == {"stream_id": "s-7"}


# ---------------------------------------------------------------------------
# _extract_conversation_id
# ---------------------------------------------------------------------------


class _Ctx:
    """Stand-in for ``bedrock_agentcore.runtime.context.RequestContext``."""

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id


class TestExtractConversationId:
    def test_payload_conversation_id_takes_priority(self):
        ctx = _Ctx(session_id="session-from-agentcore")
        result = state._extract_conversation_id(
            {"prompt": "hi", "conversation_id": "from-payload"}, ctx
        )
        assert result == "from-payload"

    def test_camelcase_payload_field_accepted(self):
        result = state._extract_conversation_id(
            {"prompt": "hi", "conversationId": "from-camel"}, None
        )
        assert result == "from-camel"

    def test_payload_session_id_field_accepted(self):
        result = state._extract_conversation_id(
            {"prompt": "hi", "session_id": "session-explicit"}, None
        )
        assert result == "session-explicit"

    def test_falls_through_to_context_session_id(self):
        ctx = _Ctx(session_id="session-from-agentcore")
        result = state._extract_conversation_id({"prompt": "hi"}, ctx)
        assert result == "session-from-agentcore"

    def test_nested_context_field_in_payload(self):
        result = state._extract_conversation_id(
            {"prompt": "hi", "context": {"conversation_id": "from-nested"}}, None
        )
        assert result == "from-nested"

    def test_returns_empty_when_no_source_provides_id(self):
        assert state._extract_conversation_id({"prompt": "hi"}, None) == ""
        assert state._extract_conversation_id({"prompt": "hi"}, _Ctx()) == ""
        assert state._extract_conversation_id("not-a-dict", None) == ""

    def test_empty_payload_field_falls_through(self):
        ctx = _Ctx(session_id="session-from-agentcore")
        result = state._extract_conversation_id(
            {"prompt": "hi", "conversation_id": ""}, ctx
        )
        assert result == "session-from-agentcore"


# ---------------------------------------------------------------------------
# record_capture_context / load_capture_context / update_capture_context_status
# ---------------------------------------------------------------------------


class TestRecordCaptureContext:
    def test_writes_partition_keys_and_capture_id(self, stub_table):
        """Validates: Requirements 9.20, 17.9."""
        item = state.record_capture_context(
            user_id="alice",
            conversation_id="conv-1",
            capture_id="cap-abc",
            eni_ids=["eni-1", "eni-2"],
            deadline="2026-04-20T13:00:00Z",
            duration_minutes=15,
        )
        assert item["PK"] == "USER#alice"
        assert item["SK"] == "CTX#CAPTURE#conv-1"
        assert item["capture_id"] == "cap-abc"
        assert item["eni_ids"] == ["eni-1", "eni-2"]
        assert item["deadline"] == "2026-04-20T13:00:00Z"
        assert item["duration_minutes"] == 15
        assert item["status"] == "active"
        assert item["TTL"] > 0
        # And the stub recorded a put_item call with the right payload.
        assert len(stub_table.put_calls) == 1
        assert stub_table.put_calls[0]["Item"]["capture_id"] == "cap-abc"

    def test_no_op_when_conversation_id_empty(self, stub_table):
        """Validates: Requirements 9.20."""
        item = state.record_capture_context(
            user_id="alice",
            conversation_id="",
            capture_id="cap-abc",
        )
        # The function still returns a fully-formed item …
        assert item["capture_id"] == "cap-abc"
        # … but it never reaches DynamoDB.
        assert stub_table.put_calls == []

    def test_no_op_when_capture_id_empty(self, stub_table):
        item = state.record_capture_context(
            user_id="alice",
            conversation_id="conv-1",
            capture_id="",
        )
        assert item["capture_id"] == ""
        assert stub_table.put_calls == []

    def test_no_op_when_table_unconfigured(self, monkeypatch):
        """When CONVERSATIONS_TABLE_NAME is unset, every call is a no-op."""
        monkeypatch.setattr(state, "_resolve_table", lambda: None)
        item = state.record_capture_context(
            user_id="alice",
            conversation_id="conv-1",
            capture_id="cap-abc",
        )
        assert item["capture_id"] == "cap-abc"

    def test_overwrites_existing_row_on_new_capture(self, stub_table):
        """Validates: Requirements 9.20 (Task 36 bullet 4 — replacement).

        ``put_item`` semantics overwrite unconditionally so the
        previously persisted ``stopped`` capture is replaced when a
        new capture starts. We simulate the replacement by writing an
        ``stopped`` entry first and then a fresh ``active`` one and
        asserting the second wins.
        """
        state.record_capture_context(
            user_id="alice",
            conversation_id="conv-1",
            capture_id="cap-old",
            status="stopped",
        )
        state.record_capture_context(
            user_id="alice",
            conversation_id="conv-1",
            capture_id="cap-new",
            status="active",
        )
        loaded = state.load_capture_context(user_id="alice", conversation_id="conv-1")
        assert loaded is not None
        assert loaded["capture_id"] == "cap-new"
        assert loaded["status"] == "active"

    def test_fail_soft_on_client_error(self, monkeypatch):
        """Any DynamoDB error is logged but never raised."""
        from botocore.exceptions import ClientError

        class _RaisingTable:
            def put_item(self, **_kwargs):
                raise ClientError(
                    error_response={"Error": {"Code": "InternalServerError"}},
                    operation_name="PutItem",
                )

        monkeypatch.setattr(state, "_resolve_table", lambda: _RaisingTable())
        # Must not raise.
        item = state.record_capture_context(
            user_id="alice",
            conversation_id="conv-1",
            capture_id="cap-abc",
        )
        assert item["capture_id"] == "cap-abc"


class TestLoadCaptureContext:
    def test_returns_none_for_missing_row(self, stub_table):
        result = state.load_capture_context(
            user_id="alice", conversation_id="conv-missing"
        )
        assert result is None

    def test_returns_none_for_empty_conversation_id(self, stub_table):
        result = state.load_capture_context(user_id="alice", conversation_id="")
        assert result is None
        # And no get_item was issued — the empty conversation id
        # short-circuits before the table read.
        assert stub_table.get_calls == []

    def test_returns_persisted_row(self, stub_table):
        state.record_capture_context(
            user_id="alice",
            conversation_id="conv-1",
            capture_id="cap-abc",
        )
        result = state.load_capture_context(user_id="alice", conversation_id="conv-1")
        assert result is not None
        assert result["capture_id"] == "cap-abc"
        assert result["status"] == "active"


class TestUpdateCaptureContextStatus:
    def test_updates_status_field(self, stub_table):
        """Validates: Requirements 17.9."""
        state.record_capture_context(
            user_id="alice", conversation_id="conv-1", capture_id="cap-abc"
        )
        state.update_capture_context_status(
            user_id="alice",
            conversation_id="conv-1",
            status="stopped",
            stopped_reason="user_initiated",
        )
        result = state.load_capture_context(user_id="alice", conversation_id="conv-1")
        assert result is not None
        assert result["status"] == "stopped"
        assert result["stopped_reason"] == "user_initiated"

    def test_no_op_for_empty_conversation_id(self, stub_table):
        state.update_capture_context_status(
            user_id="alice", conversation_id="", status="stopped"
        )
        assert stub_table.update_calls == []


# ---------------------------------------------------------------------------
# query_network_pcap end-to-end behaviour with Capture_Conversation_Context
# ---------------------------------------------------------------------------


class TestQueryNetworkPcapAnaphor:
    """Validates: Requirements 9.20, 17.9.

    Wires the real ``query_network_pcap`` ``@tool`` against a
    ``_invoke_network_agent`` stub plus the in-memory state-store
    stub from the ``stub_table`` fixture. Each test arranges a
    persisted Capture_Conversation_Context, sets the user prompt
    in the per-request ContextVar, invokes the tool, and asserts
    both (a) what was sent to the Network Agent and (b) the
    returned envelope.
    """

    @pytest.fixture(autouse=True)
    def _stub_invoke(self, monkeypatch):
        self.invocations: list[tuple[str, Optional[dict]]] = []

        def fake_invoke(action: str, params: Optional[dict] = None) -> str:
            self.invocations.append(
                (action, dict(params) if isinstance(params, dict) else params)
            )
            data: dict[str, Any] = {"action": action}
            if isinstance(params, dict) and "capture_id" in params:
                data["capture_id"] = params["capture_id"]
            return json.dumps(
                {
                    "success": True,
                    "domain": "network",
                    "data": data,
                    "formattedText": f"stub for {action}",
                    "metadata": {
                        "sourceApi": "stub",
                        "queryTimestamp": "2026-04-20T00:00:00Z",
                        "dataFreshness": "real-time",
                    },
                }
            )

        monkeypatch.setattr(main, "_invoke_network_agent", fake_invoke)

    def _call(self, action: str, params: Optional[dict] = None) -> dict:
        return json.loads(_query_network_pcap_callable()(action, params))

    def test_substitutes_persisted_capture_id_when_anaphor_present(
        self, stub_table, context_vars
    ):
        state.record_capture_context(
            user_id="alice", conversation_id="conv-test-1", capture_id="cap-persisted"
        )
        main._CURRENT_USER_PROMPT.set("show me TLS Hello sizes for my capture")
        result = self._call("check_tls_hello_size", {})

        # Network Agent received the substituted capture_id.
        assert self.invocations == [
            ("check_tls_hello_size", {"capture_id": "cap-persisted"})
        ]
        # Response envelope advertises the substitution.
        assert result["success"] is True
        assert result["metadata"]["resolvedCaptureIdFromContext"] == "cap-persisted"

    def test_does_not_substitute_when_no_anaphor(
        self, stub_table, context_vars
    ):
        state.record_capture_context(
            user_id="alice", conversation_id="conv-test-1", capture_id="cap-persisted"
        )
        main._CURRENT_USER_PROMPT.set("list_enis filtered by vpc")  # no anaphor
        self._call("list_enis", {})

        # No capture_id was injected.
        assert self.invocations == [("list_enis", {})]

    def test_explicit_capture_id_wins_over_persisted(
        self, stub_table, context_vars
    ):
        state.record_capture_context(
            user_id="alice", conversation_id="conv-test-1", capture_id="cap-persisted"
        )
        main._CURRENT_USER_PROMPT.set("transform my capture")
        result = self._call(
            "transform_capture", {"capture_id": "cap-explicit"}
        )

        # Explicit id reached the Network Agent unchanged.
        assert self.invocations == [
            ("transform_capture", {"capture_id": "cap-explicit"})
        ]
        # Substitution metadata is NOT added when no substitution
        # occurred.
        assert "resolvedCaptureIdFromContext" not in result.get("metadata", {})

    def test_start_capture_excluded_from_substitution(
        self, stub_table, context_vars
    ):
        # An anaphor in the prompt and a persisted capture_id must NOT
        # cause start_capture to reuse the persisted id — fresh
        # captures always allocate a new id.
        state.record_capture_context(
            user_id="alice", conversation_id="conv-test-1", capture_id="cap-persisted"
        )
        main._CURRENT_USER_PROMPT.set("start a new capture (replace my capture)")
        params = {"eni_ids": ["eni-1"], "duration_minutes": 15}
        self._call("start_capture", params)

        # Sent params do NOT contain a capture_id.
        assert self.invocations == [("start_capture", params)]

    def test_start_capture_success_records_new_context(
        self, stub_table, context_vars, monkeypatch
    ):
        """Validates: Requirements 9.20 (Task 36 bullet 4)."""

        # Override the stub to return a fresh capture_id.
        def fake_invoke(action: str, params: Optional[dict] = None) -> str:
            return json.dumps(
                {
                    "success": True,
                    "domain": "network",
                    "data": {
                        "capture_id": "cap-fresh-001",
                        "eni_ids": ["eni-x"],
                        "deadline": "2026-04-20T13:00:00Z",
                        "duration_minutes": 15,
                    },
                    "formattedText": "started",
                    "metadata": {
                        "sourceApi": "stub",
                        "queryTimestamp": "2026-04-20T00:00:00Z",
                        "dataFreshness": "real-time",
                    },
                }
            )

        monkeypatch.setattr(main, "_invoke_network_agent", fake_invoke)

        # Pre-existing stopped entry gets overwritten by Task 36 bullet 4.
        state.record_capture_context(
            user_id="alice",
            conversation_id="conv-test-1",
            capture_id="cap-old",
            status="stopped",
        )
        main._CURRENT_USER_PROMPT.set("start a new capture on eni-x for 15 minutes")
        self._call(
            "start_capture",
            {"eni_ids": ["eni-x"], "duration_minutes": 15},
        )

        # The persisted entry is now the new capture.
        loaded = state.load_capture_context(
            user_id="alice", conversation_id="conv-test-1"
        )
        assert loaded is not None
        assert loaded["capture_id"] == "cap-fresh-001"
        assert loaded["status"] == "active"
        assert loaded["eni_ids"] == ["eni-x"]
        assert loaded["deadline"] == "2026-04-20T13:00:00Z"

    def test_stop_capture_success_marks_status_stopped(
        self, stub_table, context_vars
    ):
        state.record_capture_context(
            user_id="alice",
            conversation_id="conv-test-1",
            capture_id="cap-active",
            status="active",
        )
        main._CURRENT_USER_PROMPT.set("stop my capture")
        self._call("stop_capture", {})

        loaded = state.load_capture_context(
            user_id="alice", conversation_id="conv-test-1"
        )
        assert loaded is not None
        assert loaded["status"] == "stopped"

    def test_transform_capture_success_marks_status_transformed(
        self, stub_table, context_vars
    ):
        state.record_capture_context(
            user_id="alice",
            conversation_id="conv-test-1",
            capture_id="cap-active",
            status="stopped",
        )
        main._CURRENT_USER_PROMPT.set("transform my capture")
        self._call("transform_capture", {})

        loaded = state.load_capture_context(
            user_id="alice", conversation_id="conv-test-1"
        )
        assert loaded is not None
        assert loaded["status"] == "transformed"

    def test_substitution_skipped_when_no_persisted_context(
        self, stub_table, context_vars
    ):
        """Validates: Requirements 9.20 (Task 36 bullet 3)."""
        # No persisted entry exists. Anaphor in the prompt; tool sends
        # the user-supplied (empty) params unchanged. The system
        # prompt is what tells the LLM to call list_captures first,
        # but the runtime never silently fabricates a capture_id.
        main._CURRENT_USER_PROMPT.set("show me TLS Hello sizes for my capture")
        self._call("check_tls_hello_size", {})

        # No substitution occurred — the Network Agent receives
        # exactly what the LLM sent.
        assert self.invocations == [("check_tls_hello_size", {})]


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


_capture_id_strategy = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=128,
)
_user_id_strategy = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=64,
)
_conversation_id_strategy = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=64,
)


class TestCaptureContextProperties:
    """Universal properties for the persistence helpers."""

    _PROP_SETTINGS = settings(
        max_examples=50,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )

    @given(
        capture_id=_capture_id_strategy,
        user_id=_user_id_strategy,
        conversation_id=_conversation_id_strategy,
    )
    @_PROP_SETTINGS
    def test_record_then_load_roundtrip(
        self, capture_id, user_id, conversation_id, monkeypatch
    ):
        """Validates: Requirements 9.20, 17.9.

        For every ``(capture_id, user_id, conversation_id)`` triple,
        recording followed by loading returns the same
        ``capture_id``.
        """
        # Reset the stub-table state per Hypothesis example so
        # results don't bleed across iterations.
        table = _StubTable()
        monkeypatch.setattr(state, "_resolve_table", lambda: table)

        state.record_capture_context(
            user_id=user_id,
            conversation_id=conversation_id,
            capture_id=capture_id,
        )
        loaded = state.load_capture_context(
            user_id=user_id, conversation_id=conversation_id
        )
        assert loaded is not None
        assert loaded["capture_id"] == capture_id

    @given(
        old_id=_capture_id_strategy,
        new_id=_capture_id_strategy,
        user_id=_user_id_strategy,
        conversation_id=_conversation_id_strategy,
    )
    @_PROP_SETTINGS
    def test_replacement_semantics(
        self, old_id, new_id, user_id, conversation_id, monkeypatch
    ):
        """Validates: Requirements 9.20 (Task 36 bullet 4).

        Recording a fresh capture id always replaces any prior
        entry — there is no merge; ``put_item`` overwrites
        unconditionally.
        """
        table = _StubTable()
        monkeypatch.setattr(state, "_resolve_table", lambda: table)

        state.record_capture_context(
            user_id=user_id,
            conversation_id=conversation_id,
            capture_id=old_id,
            status="stopped",
        )
        state.record_capture_context(
            user_id=user_id,
            conversation_id=conversation_id,
            capture_id=new_id,
            status="active",
        )
        loaded = state.load_capture_context(
            user_id=user_id, conversation_id=conversation_id
        )
        assert loaded is not None
        assert loaded["capture_id"] == new_id
        assert loaded["status"] == "active"

    @given(
        prompt=st.text(min_size=0, max_size=200),
        persisted_id=st.one_of(st.none(), _capture_id_strategy),
        explicit_id=st.one_of(st.none(), _capture_id_strategy),
    )
    @_PROP_SETTINGS
    def test_substitution_invariants(self, prompt, persisted_id, explicit_id):
        """Validates: Requirements 9.20.

        Three universal properties for the substitution helper:

        1. Explicit id always wins.
        2. Substitution only happens when persisted id is non-empty.
        3. The function never mutates its input.
        """
        params = {} if explicit_id is None else {"capture_id": explicit_id}
        original_params = dict(params)
        merged, did = state.substitute_persisted_capture_id(
            params=params, persisted_capture_id=persisted_id
        )

        # Property 3: input is never mutated.
        assert params == original_params

        if explicit_id:
            # Property 1: explicit id wins.
            assert merged["capture_id"] == explicit_id
            assert did is False
        elif persisted_id:
            # Property 2: substitution only happens with a non-empty
            # persisted id.
            assert merged["capture_id"] == persisted_id
            assert did is True
        else:
            assert "capture_id" not in merged
            assert did is False
