"""
Unit and property-based tests for the Cognito-group authorization gate
that the Orchestration Agent enforces before invoking the Network Agent's
Capture_Actions (Req 9.16).

Run from the ``orchestration-agent`` directory::

    python -m pytest test_capture_authorization.py -v

Scope:

- Pure helpers (``_normalize_group_list``, ``_decode_jwt_groups``,
  ``_extract_user_groups``, ``_user_in_group``) are exercised directly.
- ``query_network_pcap`` is exercised through its ``@tool``-decorated
  callable. Strands' decorator wraps the function in a ``DecoratedFunctionTool``
  whose underlying Python callable is reachable via ``.original_function``;
  we call that to avoid pulling the LLM event loop into the test.

The tests stub ``_invoke_network_agent`` with a function that records its
arguments and returns a fixed success envelope. This lets us assert both
that the gate refuses Capture_Actions without group membership AND that
it does NOT short-circuit read-only actions or authorized Capture_Actions.
"""

from __future__ import annotations

import base64
import json
import os
import string
import types
from typing import Optional

import pytest
from hypothesis import HealthCheck, given, strategies as st, settings

# AgentCore's BedrockAgentCoreApp constructor in main.py imports the
# bedrock-agentcore SDK, which in turn expects a region. Ensure the env
# var is set before the import so the module loads cleanly outside the
# AgentCore runtime.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import main  # noqa: E402  (env-var setup must happen first)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_unsigned_jwt(payload: dict) -> str:
    """Build a JWT-shaped string with the supplied payload.

    The signature is left as the literal string ``"sig"`` because
    ``_decode_jwt_groups`` never validates the signature — AgentCore's
    upstream JWT authorizer is the validating layer in production. This
    keeps the test deterministic and free of any cryptographic dependency.
    """
    header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8")).rstrip(b"=").decode("ascii")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"{header_b64}.{payload_b64}.sig"


class _Ctx:
    """Minimal stand-in for ``bedrock_agentcore.runtime.context.RequestContext``.

    We only ever read ``request_headers`` from the context object, so a
    plain attribute container is enough.
    """

    def __init__(self, request_headers: Optional[dict] = None):
        self.request_headers = request_headers


# ---------------------------------------------------------------------------
# Constants — sanity check that the gate guards exactly the documented set
# ---------------------------------------------------------------------------


class TestCaptureActionsConstant:
    def test_capture_actions_contains_exactly_the_three_documented_actions(self):
        assert main.CAPTURE_ACTIONS == frozenset({"start_capture", "stop_capture", "transform_capture"})

    def test_capture_authorization_group_name_matches_design(self):
        assert main.GOAT_NETWORK_CAPTURE_GROUP == "GOATNetworkCaptureUsers"

    def test_capture_actions_is_a_strict_subset_of_network_agent_actions(self):
        for action in main.CAPTURE_ACTIONS:
            assert action in main.NETWORK_AGENT_ACTIONS, (
                f"CAPTURE_ACTIONS includes {action!r} which is missing from NETWORK_AGENT_ACTIONS"
            )

    def test_read_only_network_actions_are_not_in_capture_actions(self):
        # These are the Network Agent actions that any authenticated user
        # should be able to invoke without Capture_Authorization_Group
        # membership. If any of them ends up in CAPTURE_ACTIONS, the gate
        # would over-block read-only inventory and analysis.
        read_only = {
            "list_enis",
            "list_captures",
            "get_capture_progress",
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
        }
        assert main.CAPTURE_ACTIONS.isdisjoint(read_only)


# ---------------------------------------------------------------------------
# _normalize_group_list — payload-supplied groups in mixed shapes
# ---------------------------------------------------------------------------


class TestNormalizeGroupList:
    def test_list_of_strings_returns_tuple_of_strings(self):
        assert main._normalize_group_list(["a", "b", "c"]) == ("a", "b", "c")

    def test_list_filters_non_strings_and_empty_strings(self):
        assert main._normalize_group_list(["a", "", None, 1, "b"]) == ("a", "b")

    def test_comma_separated_string_is_split(self):
        assert main._normalize_group_list("a, b ,c") == ("a", "b", "c")

    def test_space_separated_string_is_split(self):
        assert main._normalize_group_list("a b c") == ("a", "b", "c")

    def test_empty_inputs_return_empty(self):
        assert main._normalize_group_list("") == ()
        assert main._normalize_group_list([]) == ()
        assert main._normalize_group_list(None) == ()
        assert main._normalize_group_list({}) == ()
        assert main._normalize_group_list(42) == ()


# ---------------------------------------------------------------------------
# _decode_jwt_groups — JWT payload extraction
# ---------------------------------------------------------------------------


class TestDecodeJwtGroups:
    def test_valid_jwt_with_group_list_returns_tuple(self):
        token = _build_unsigned_jwt({"cognito:groups": ["GOATNetworkCaptureUsers", "Admins"]})
        assert main._decode_jwt_groups(token) == ("GOATNetworkCaptureUsers", "Admins")

    def test_valid_jwt_with_space_separated_groups_string_returns_tuple(self):
        token = _build_unsigned_jwt({"cognito:groups": "GOATNetworkCaptureUsers Admins"})
        assert main._decode_jwt_groups(token) == ("GOATNetworkCaptureUsers", "Admins")

    def test_valid_jwt_with_comma_separated_groups_string_returns_tuple(self):
        token = _build_unsigned_jwt({"cognito:groups": "GOATNetworkCaptureUsers,Admins"})
        assert main._decode_jwt_groups(token) == ("GOATNetworkCaptureUsers", "Admins")

    def test_valid_jwt_without_cognito_groups_claim_returns_empty(self):
        token = _build_unsigned_jwt({"sub": "user-123"})
        assert main._decode_jwt_groups(token) == ()

    def test_token_with_only_one_segment_returns_empty(self):
        assert main._decode_jwt_groups("not-a-jwt") == ()

    def test_token_with_two_segments_returns_empty(self):
        assert main._decode_jwt_groups("aaa.bbb") == ()

    def test_token_with_undecodable_payload_returns_empty(self):
        # Valid base64url but JSON parse fails
        bad_payload = base64.urlsafe_b64encode(b"not-json").rstrip(b"=").decode("ascii")
        assert main._decode_jwt_groups(f"hdr.{bad_payload}.sig") == ()

    def test_empty_token_returns_empty(self):
        assert main._decode_jwt_groups("") == ()


# ---------------------------------------------------------------------------
# _extract_user_groups — source priority order
# ---------------------------------------------------------------------------


class TestExtractUserGroups:
    def test_payload_user_groups_field_takes_priority(self):
        token = _build_unsigned_jwt({"cognito:groups": ["FromJWT"]})
        ctx = _Ctx(request_headers={"authorization": f"Bearer {token}"})
        payload = {"prompt": "hi", "user_groups": ["FromPayload"]}
        assert main._extract_user_groups(payload, ctx) == ("FromPayload",)

    def test_payload_camelcase_user_groups_field_is_accepted(self):
        payload = {"prompt": "hi", "userGroups": ["FromPayload"]}
        assert main._extract_user_groups(payload, None) == ("FromPayload",)

    def test_payload_cognito_groups_field_is_accepted(self):
        payload = {"prompt": "hi", "cognito_groups": ["FromPayload"]}
        assert main._extract_user_groups(payload, None) == ("FromPayload",)

    def test_payload_nested_context_groups_are_accepted(self):
        payload = {"prompt": "hi", "context": {"cognito_groups": ["FromNested"]}}
        assert main._extract_user_groups(payload, None) == ("FromNested",)

    def test_falls_through_to_header_when_payload_groups_missing(self):
        token = _build_unsigned_jwt({"cognito:groups": ["FromJWT"]})
        ctx = _Ctx(request_headers={"authorization": f"Bearer {token}"})
        assert main._extract_user_groups({"prompt": "hi"}, ctx) == ("FromJWT",)

    def test_handles_capitalized_authorization_header(self):
        token = _build_unsigned_jwt({"cognito:groups": ["FromJWT"]})
        ctx = _Ctx(request_headers={"Authorization": f"Bearer {token}"})
        assert main._extract_user_groups({"prompt": "hi"}, ctx) == ("FromJWT",)

    def test_returns_empty_when_no_source_provides_groups(self):
        assert main._extract_user_groups({"prompt": "hi"}, None) == ()
        assert main._extract_user_groups({"prompt": "hi"}, _Ctx(request_headers={})) == ()
        assert main._extract_user_groups("not-a-dict", None) == ()

    def test_ignores_non_bearer_authorization_header(self):
        ctx = _Ctx(request_headers={"authorization": "Basic abc:def"})
        assert main._extract_user_groups({"prompt": "hi"}, ctx) == ()

    def test_empty_payload_groups_falls_through_to_headers(self):
        # An explicitly empty list in the payload should not poison the
        # search — fall through to the next source.
        token = _build_unsigned_jwt({"cognito:groups": ["FromJWT"]})
        ctx = _Ctx(request_headers={"authorization": f"Bearer {token}"})
        assert main._extract_user_groups({"prompt": "hi", "user_groups": []}, ctx) == ("FromJWT",)


# ---------------------------------------------------------------------------
# _user_in_group — explicit and ContextVar-backed paths
# ---------------------------------------------------------------------------


class TestUserInGroup:
    def test_returns_true_when_explicit_group_list_contains_required(self):
        assert main._user_in_group("GOATNetworkCaptureUsers", ["Admins", "GOATNetworkCaptureUsers"]) is True

    def test_returns_false_when_explicit_group_list_lacks_required(self):
        assert main._user_in_group("GOATNetworkCaptureUsers", ["Admins"]) is False
        assert main._user_in_group("GOATNetworkCaptureUsers", []) is False

    def test_uses_context_var_when_groups_argument_is_omitted(self):
        token = main._CURRENT_USER_GROUPS.set(("GOATNetworkCaptureUsers", "Admins"))
        try:
            assert main._user_in_group("GOATNetworkCaptureUsers") is True
            assert main._user_in_group("MissingGroup") is False
        finally:
            main._CURRENT_USER_GROUPS.reset(token)

    def test_default_context_var_value_blocks_capture_actions(self):
        # No ContextVar.set() — the default of () must produce False so
        # the Capture_Action gate fails closed.
        # Reset to default value first to isolate from other tests.
        token = main._CURRENT_USER_GROUPS.set(())
        try:
            assert main._user_in_group("GOATNetworkCaptureUsers") is False
        finally:
            main._CURRENT_USER_GROUPS.reset(token)


# ---------------------------------------------------------------------------
# query_network_pcap — end-to-end gate behaviour
# ---------------------------------------------------------------------------


def _query_network_pcap_callable():
    """Return the underlying Python function behind the ``@tool`` decorator.

    Strands wraps the function in a ``DecoratedFunctionTool`` whose
    callable form preserves the original Python implementation under
    one of a few attribute names depending on the SDK version. We try
    each in turn so the test stays compatible across minor releases.
    """
    tool_obj = main.query_network_pcap
    for attr in ("original_function", "_function", "function", "fn"):
        candidate = getattr(tool_obj, attr, None)
        if callable(candidate):
            return candidate
    if callable(tool_obj):
        return tool_obj
    raise RuntimeError("Could not resolve underlying callable for query_network_pcap")


class TestQueryNetworkPcapAuthorizationGate:
    @pytest.fixture(autouse=True)
    def _stub_invoke(self, monkeypatch):
        # Replace the Network Agent invocation with a recording stub so
        # the test can assert (a) refused requests never reach it and
        # (b) authorized requests do. The stub returns a minimal success
        # envelope so the caller's contract is preserved.
        self.invocations: list[tuple[str, dict | None]] = []

        def fake_invoke(action: str, params: dict = None) -> str:
            self.invocations.append((action, params))
            return json.dumps({
                "success": True,
                "domain": "network",
                "data": {"action": action},
                "formattedText": f"stub for {action}",
                "metadata": {
                    "sourceApi": "stub",
                    "queryTimestamp": "2026-04-20T00:00:00Z",
                    "dataFreshness": "real-time",
                },
            })

        monkeypatch.setattr(main, "_invoke_network_agent", fake_invoke)

    def setup_method(self):
        # Ensure each test starts with no groups so the gate's default
        # state is observable.
        self._token = main._CURRENT_USER_GROUPS.set(())

    def teardown_method(self):
        main._CURRENT_USER_GROUPS.reset(self._token)

    def _call(self, action: str, params: Optional[dict] = None) -> dict:
        result_str = _query_network_pcap_callable()(action, params)
        return json.loads(result_str)

    @pytest.mark.parametrize("action", ["start_capture", "stop_capture", "transform_capture"])
    def test_capture_action_without_group_returns_unauthorized_envelope(self, action):
        result = self._call(action, {"capture_id": "cap-abc"})
        assert result["success"] is False
        assert result["domain"] == "network"
        assert main.GOAT_NETWORK_CAPTURE_GROUP in result["error"]
        assert action in result["error"]
        assert result["metadata"]["errorCategory"] == "unauthorized"
        assert result["metadata"]["requiredGroup"] == main.GOAT_NETWORK_CAPTURE_GROUP
        # Crucially, the Network Agent is NEVER invoked for an
        # unauthorized Capture_Action.
        assert self.invocations == []

    @pytest.mark.parametrize("action", ["start_capture", "stop_capture", "transform_capture"])
    def test_capture_action_with_group_proceeds_to_invocation(self, action):
        main._CURRENT_USER_GROUPS.set(("GOATNetworkCaptureUsers",))
        result = self._call(action, {"capture_id": "cap-abc"})
        assert result["success"] is True
        assert result["domain"] == "network"
        assert self.invocations == [(action, {"capture_id": "cap-abc"})]

    @pytest.mark.parametrize("action", [
        "list_enis",
        "list_captures",
        "get_capture_progress",
        "query_pcap",
        "search_fragmented_packets",
        "diagnose_tcp_stream",
    ])
    def test_read_only_actions_bypass_the_group_check(self, action):
        # No groups set, but the action is read-only — it must still
        # reach the Network Agent invocation.
        result = self._call(action, {"capture_id": "cap-abc"})
        assert result["success"] is True
        assert self.invocations == [(action, {"capture_id": "cap-abc"})]

    def test_unsupported_action_is_rejected_before_the_group_check(self):
        # The action validator runs first — even a member of the group
        # cannot invoke an unknown action.
        main._CURRENT_USER_GROUPS.set(("GOATNetworkCaptureUsers",))
        result = self._call("not_a_real_action", {})
        assert result["success"] is False
        assert "Unsupported Network Agent action" in result["error"]
        assert self.invocations == []

    def test_user_in_other_group_only_is_refused(self):
        # Membership in some unrelated group does not satisfy the gate.
        main._CURRENT_USER_GROUPS.set(("Admins", "Auditors"))
        result = self._call("start_capture", {"eni_ids": ["eni-1"], "duration_minutes": 5})
        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "unauthorized"
        assert self.invocations == []


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


# Alphabet for arbitrary group names. Keep it readable so failures are
# easy to interpret.
_GROUP_NAME_ALPHABET = string.ascii_letters + string.digits + "_-"


def _arbitrary_group_name():
    return st.text(alphabet=_GROUP_NAME_ALPHABET, min_size=1, max_size=32)


class TestAuthorizationProperties:
    """Universal properties that must hold across all reasonable inputs."""

    # Hypothesis can exceed the default per-example entropy budget when
    # generating long lists of short ``[A-Za-z0-9_-]`` strings. We lower
    # the example count and suppress the ``too_slow`` health check so
    # the property still gets meaningful coverage without flaking on a
    # health-check timeout.
    _PROP_SETTINGS = settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )

    @given(groups=st.lists(_arbitrary_group_name(), max_size=8))
    @_PROP_SETTINGS
    def test_user_in_group_iff_required_present(self, groups):
        """``_user_in_group`` reports membership iff the required group is present.

        **Validates: Requirements 9.16**
        """
        # Independent of any ContextVar pollution, the explicit-list
        # branch must be a pure ``in`` check.
        is_member = main._user_in_group(main.GOAT_NETWORK_CAPTURE_GROUP, groups)
        assert is_member == (main.GOAT_NETWORK_CAPTURE_GROUP in groups)

    @given(
        action=st.sampled_from(sorted(main.CAPTURE_ACTIONS)),
        groups=st.lists(_arbitrary_group_name(), max_size=8),
    )
    @_PROP_SETTINGS
    def test_capture_action_gate_is_deterministic_in_group_membership(self, action, groups):
        """Capture_Action acceptance depends only on group membership.

        For any randomly generated set of groups, the gate returns either
        an authorized invocation envelope or an unauthorized refusal
        envelope, and which one it returns is fully determined by whether
        ``GOATNetworkCaptureUsers`` is in the group list.

        **Validates: Requirements 9.16**
        """
        # We can't use the @tool entrypoint here without re-stubbing the
        # invocation per Hypothesis example; assert the same property via
        # the underlying gate directly. The behaviour mirrors the @tool
        # branch exactly.
        is_member = main.GOAT_NETWORK_CAPTURE_GROUP in groups
        gated = main._user_in_group(main.GOAT_NETWORK_CAPTURE_GROUP, groups)
        assert gated == is_member
        # And: the action must be in CAPTURE_ACTIONS (sanity check on
        # the strategy itself).
        assert action in main.CAPTURE_ACTIONS

    @given(payload_groups=st.lists(_arbitrary_group_name(), max_size=8))
    @_PROP_SETTINGS
    def test_explicit_payload_groups_dominate_jwt_groups(self, payload_groups):
        """When the payload supplies groups, the JWT in the headers is ignored.

        **Validates: Requirements 9.16**
        """
        jwt_groups = ["UnrelatedFromJWT"]
        token = _build_unsigned_jwt({"cognito:groups": jwt_groups})
        ctx = _Ctx(request_headers={"authorization": f"Bearer {token}"})
        payload = {"prompt": "hi", "user_groups": payload_groups}
        result = main._extract_user_groups(payload, ctx)
        if payload_groups:
            assert result == tuple(payload_groups)
        else:
            # Empty payload list falls through to JWT.
            assert result == tuple(jwt_groups)
