"""
Unit tests for the Support_Case_Investigation workflow (Task 41, Reqs 20.1-20.14).

Tests cover:
- Case_Id_Format detection (standard and legacy formats)
- Support case trigger phrase detection
- Error signature to Pcap_Query_Action mapping
- Flow_Selector construction from Support_Case_Context
- Support_Case_Context extraction from case body/communications
- Four-section response formatting
- Support_Case_Context persistence in state.py
- investigate_support_case tool behavior with mocked sub-agents
"""
import json
import re
from unittest.mock import patch, MagicMock

import pytest

# Import the functions under test
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from main import (
    detect_case_id,
    contains_case_trigger,
    match_error_signatures_to_actions,
    build_flow_selector_from_case_context,
    format_support_case_investigation_response,
    _extract_support_case_context,
    _gather_case_text,
    _build_case_summary,
    CASE_ID_STANDARD_RE,
    CASE_ID_LEGACY_RE,
    SUPPORT_CASE_ERROR_ACTION_MAP,
    SUPPORT_CASE_TA_CATEGORIES,
)
import state


# ---------------------------------------------------------------------------
# Case_Id_Format detection tests (Req 20.1)
# ---------------------------------------------------------------------------


class TestCaseIdDetection:
    """Tests for detect_case_id and Case_Id_Format regex patterns."""

    def test_standard_case_id_detected(self):
        """Standard AWS Support case ID format is detected."""
        text = "Please investigate case-123456789012-2024-000001"
        assert detect_case_id(text) == "case-123456789012-2024-000001"

    def test_standard_case_id_case_insensitive(self):
        """Case ID detection is case-insensitive."""
        text = "Look into CASE-123456789012-2024-000001"
        assert detect_case_id(text) == "CASE-123456789012-2024-000001"

    def test_legacy_numeric_case_id_detected(self):
        """Legacy numeric case ID (8+ digits) is detected."""
        text = "Check ticket 12345678901234"
        assert detect_case_id(text) == "12345678901234"

    def test_legacy_numeric_minimum_8_digits(self):
        """Legacy format requires at least 8 digits."""
        text = "Case 1234567"  # Only 7 digits
        assert detect_case_id(text) is None

    def test_legacy_numeric_exactly_8_digits(self):
        """Legacy format accepts exactly 8 digits."""
        text = "Case 12345678"
        assert detect_case_id(text) == "12345678"

    def test_no_case_id_returns_none(self):
        """Returns None when no case ID is present."""
        assert detect_case_id("Hello, how are you?") is None
        assert detect_case_id("") is None
        assert detect_case_id(None) is None

    def test_standard_format_preferred_over_legacy(self):
        """Standard format is preferred when both are present."""
        text = "case-123456789012-2024-000001 and also 99999999"
        assert detect_case_id(text) == "case-123456789012-2024-000001"

    def test_case_id_embedded_in_sentence(self):
        """Case ID is detected when embedded in a sentence."""
        text = "I need help with case-111222333444-2025-123456, it's urgent"
        assert detect_case_id(text) == "case-111222333444-2025-123456"


class TestCaseTriggerDetection:
    """Tests for contains_case_trigger."""

    def test_investigate_with_case_id(self):
        """'investigate' + case ID triggers investigation."""
        assert contains_case_trigger("investigate case-123456789012-2024-000001") is True

    def test_support_case_phrase(self):
        """'support case' + case ID triggers investigation."""
        assert contains_case_trigger("look at support case 12345678") is True

    def test_ticket_phrase(self):
        """'ticket' + case ID triggers investigation."""
        assert contains_case_trigger("check ticket 12345678") is True

    def test_case_id_without_trigger_phrase(self):
        """Case ID alone without trigger phrase does not trigger."""
        # Just a number without context — the trigger requires both
        assert contains_case_trigger("12345678") is False

    def test_trigger_phrase_without_case_id(self):
        """Trigger phrase without case ID does not trigger."""
        assert contains_case_trigger("investigate the issue") is False

    def test_non_string_input(self):
        """Non-string input returns False."""
        assert contains_case_trigger(None) is False
        assert contains_case_trigger(123) is False


# ---------------------------------------------------------------------------
# Error signature to action mapping tests (Req 20.7)
# ---------------------------------------------------------------------------


class TestErrorSignatureMapping:
    """Tests for match_error_signatures_to_actions."""

    def test_connection_reset_maps_to_classify_tcp_resets(self):
        """Connection reset errors map to classify_tcp_resets."""
        actions = match_error_signatures_to_actions(["connection reset by peer"])
        assert "classify_tcp_resets" in actions

    def test_timeout_maps_to_reconstruct_tcp_handshake(self):
        """Timeout errors map to reconstruct_tcp_handshake."""
        actions = match_error_signatures_to_actions(["connection timed out"])
        assert "reconstruct_tcp_handshake" in actions

    def test_tls_error_maps_to_check_tls_hello_size(self):
        """TLS handshake errors map to check_tls_hello_size."""
        actions = match_error_signatures_to_actions(["TLS handshake failure"])
        assert "check_tls_hello_size" in actions

    def test_multiple_signatures_deduplicated(self):
        """Multiple signatures mapping to the same action are deduplicated."""
        actions = match_error_signatures_to_actions([
            "connection reset",
            "reset by peer",
            "ECONNRESET",
        ])
        assert actions.count("classify_tcp_resets") == 1

    def test_502_maps_to_both_resets_and_handshake(self):
        """502 Bad Gateway maps to both classify_tcp_resets and reconstruct_tcp_handshake."""
        actions = match_error_signatures_to_actions(["502 bad gateway"])
        assert "classify_tcp_resets" in actions
        assert "reconstruct_tcp_handshake" in actions

    def test_empty_signatures_returns_empty(self):
        """Empty signature list returns empty action list."""
        assert match_error_signatures_to_actions([]) == []
        assert match_error_signatures_to_actions(None) == []

    def test_unrecognized_signature_returns_empty(self):
        """Unrecognized error signatures return empty action list."""
        assert match_error_signatures_to_actions(["some random error"]) == []


# ---------------------------------------------------------------------------
# Flow_Selector construction tests (Req 20.3)
# ---------------------------------------------------------------------------


class TestFlowSelectorFromCaseContext:
    """Tests for build_flow_selector_from_case_context."""

    def test_hostname_populates_destination(self):
        """First hostname populates destination_hostname."""
        ctx = {"affected_hostnames": ["ecr.us-east-1.amazonaws.com"], "affected_ips": [], "affected_ports": []}
        selector = build_flow_selector_from_case_context(ctx)
        assert selector == {"destination_hostname": "ecr.us-east-1.amazonaws.com"}

    def test_ip_populates_destination_when_no_hostname(self):
        """First IP populates destination_ip when no hostnames."""
        ctx = {"affected_hostnames": [], "affected_ips": ["10.0.1.5"], "affected_ports": []}
        selector = build_flow_selector_from_case_context(ctx)
        assert selector == {"destination_ip": "10.0.1.5"}

    def test_port_included_when_available(self):
        """Port is included in the selector when available."""
        ctx = {"affected_hostnames": ["example.com"], "affected_ips": [], "affected_ports": [443]}
        selector = build_flow_selector_from_case_context(ctx)
        assert selector == {"destination_hostname": "example.com", "destination_port": 443}

    def test_no_endpoints_returns_none(self):
        """Returns None when no hostnames or IPs are available."""
        ctx = {"affected_hostnames": [], "affected_ips": [], "affected_ports": [443]}
        assert build_flow_selector_from_case_context(ctx) is None

    def test_none_input_returns_none(self):
        """Returns None for None input."""
        assert build_flow_selector_from_case_context(None) is None

    def test_invalid_port_excluded(self):
        """Invalid ports (>65535) are excluded."""
        ctx = {"affected_hostnames": ["example.com"], "affected_ips": [], "affected_ports": [99999]}
        selector = build_flow_selector_from_case_context(ctx)
        assert "destination_port" not in selector


# ---------------------------------------------------------------------------
# Support_Case_Context extraction tests (Req 20.2)
# ---------------------------------------------------------------------------


class TestSupportCaseContextExtraction:
    """Tests for _extract_support_case_context."""

    def test_extracts_hostnames(self):
        """Hostnames are extracted from case text."""
        case_body = {
            "data": {"cases": [{"subject": "Cannot reach ecr.us-east-1.amazonaws.com"}]}
        }
        ctx = _extract_support_case_context("case-123", case_body, {})
        assert "ecr.us-east-1.amazonaws.com" in ctx["affected_hostnames"]

    def test_extracts_ipv4_addresses(self):
        """IPv4 addresses are extracted from case text."""
        case_body = {
            "data": {"cases": [{"subject": "Connection to 10.0.1.5 fails"}]}
        }
        ctx = _extract_support_case_context("case-123", case_body, {})
        assert "10.0.1.5" in ctx["affected_ips"]

    def test_extracts_ports(self):
        """Ports are extracted from case text."""
        case_body = {
            "data": {"cases": [{"subject": "Cannot connect to port 443"}]}
        }
        ctx = _extract_support_case_context("case-123", case_body, {})
        assert 443 in ctx["affected_ports"]

    def test_extracts_aws_regions(self):
        """AWS regions are extracted from case text."""
        case_body = {
            "data": {"cases": [{"subject": "Issue in us-east-1 and eu-west-1"}]}
        }
        ctx = _extract_support_case_context("case-123", case_body, {})
        assert "us-east-1" in ctx["affected_regions"]
        assert "eu-west-1" in ctx["affected_regions"]

    def test_extracts_error_signatures(self):
        """Error signatures are extracted from case text."""
        case_body = {
            "data": {"cases": [{"body": "Error: connection reset by peer when connecting to the service"}]}
        }
        ctx = _extract_support_case_context("case-123", case_body, {})
        assert len(ctx["error_signatures"]) > 0

    def test_extracts_severity(self):
        """Severity is extracted from case metadata."""
        case_body = {
            "data": {"cases": [{"severityCode": "high", "subject": "test"}]}
        }
        ctx = _extract_support_case_context("case-123", case_body, {})
        assert ctx["severity"] == "high"

    def test_extracts_service_from_metadata(self):
        """Service code is extracted from case metadata."""
        case_body = {
            "data": {"cases": [{"serviceCode": "amazon-ec2", "subject": "test"}]}
        }
        ctx = _extract_support_case_context("case-123", case_body, {})
        assert "amazon-ec2" in ctx["affected_services"]

    def test_empty_case_returns_defaults(self):
        """Empty case body returns default empty context."""
        ctx = _extract_support_case_context("case-123", {}, {})
        assert ctx["case_id"] == "case-123"
        assert ctx["affected_hostnames"] == []
        assert ctx["affected_ips"] == []
        assert ctx["affected_ports"] == []
        assert ctx["error_signatures"] == []
        assert ctx["severity"] is None

    def test_extracts_iso_timestamps(self):
        """ISO 8601 timestamps are extracted for incident window."""
        case_body = {
            "data": {"cases": [{
                "body": "Issue started at 2025-01-15T10:00:00Z and resolved at 2025-01-15T12:00:00Z"
            }]}
        }
        ctx = _extract_support_case_context("case-123", case_body, {})
        assert ctx["incident_window_start"] == "2025-01-15T10:00:00Z"
        assert ctx["incident_window_end"] == "2025-01-15T12:00:00Z"


# ---------------------------------------------------------------------------
# Response formatting tests (Req 20.9)
# ---------------------------------------------------------------------------


class TestResponseFormatting:
    """Tests for format_support_case_investigation_response."""

    def test_four_sections_present(self):
        """Response contains all four required sections."""
        response = format_support_case_investigation_response(
            case_summary="Test case summary.",
            health_correlation="No Health events match the case window",
            network_analysis="No packet capture available — see options offered above",
            recommended_actions=["Action 1", "Action 2"],
        )
        assert "**Case summary**" in response
        assert "**Health correlation**" in response
        assert "**Network analysis**" in response
        assert "**Recommended next actions**" in response

    def test_case_summary_content(self):
        """Case summary content is included."""
        response = format_support_case_investigation_response(
            case_summary="Critical issue with ECR connectivity.",
            health_correlation="No Health events match the case window",
            network_analysis="No packet capture available — see options offered above",
            recommended_actions=[],
        )
        assert "Critical issue with ECR connectivity." in response

    def test_recommended_actions_as_bullets(self):
        """Recommended actions are formatted as bullet points."""
        response = format_support_case_investigation_response(
            case_summary="Summary.",
            health_correlation="No Health events match the case window",
            network_analysis="Analysis.",
            recommended_actions=["Start a capture", "Check Health events"],
        )
        assert "- Start a capture" in response
        assert "- Check Health events" in response


# ---------------------------------------------------------------------------
# State persistence tests (Req 20.12)
# ---------------------------------------------------------------------------


class TestSupportCaseContextPersistence:
    """Tests for Support_Case_Context persistence in state.py."""

    def test_contains_support_case_anaphor_the_case(self):
        """'the case' is detected as an anaphoric reference."""
        assert state.contains_support_case_anaphor("What about the case?") is True

    def test_contains_support_case_anaphor_this_case(self):
        """'this case' is detected as an anaphoric reference."""
        assert state.contains_support_case_anaphor("Tell me more about this case") is True

    def test_contains_support_case_anaphor_the_ticket(self):
        """'the ticket' is detected as an anaphoric reference."""
        assert state.contains_support_case_anaphor("What's the status of the ticket?") is True

    def test_contains_support_case_anaphor_from_the_case(self):
        """'from the case' is detected as an anaphoric reference."""
        assert state.contains_support_case_anaphor("Use the endpoints from the case") is True

    def test_no_anaphor_returns_false(self):
        """Non-anaphoric text returns False."""
        assert state.contains_support_case_anaphor("Hello world") is False
        assert state.contains_support_case_anaphor("") is False
        assert state.contains_support_case_anaphor(None) is False

    def test_record_support_case_context_no_conversation(self):
        """record_support_case_context is a no-op without conversation_id."""
        result = state.record_support_case_context(
            user_id="test-user",
            conversation_id=None,
            support_case_context={"case_id": "case-123"},
        )
        assert result["support_case_context"] == {"case_id": "case-123"}

    @patch.object(state, '_resolve_table', return_value=None)
    def test_load_support_case_context_no_table(self, mock_table):
        """load_support_case_context returns None when table is unavailable."""
        result = state.load_support_case_context(
            user_id="test-user",
            conversation_id="conv-123",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Case summary builder tests
# ---------------------------------------------------------------------------


class TestBuildCaseSummary:
    """Tests for _build_case_summary."""

    def test_includes_case_id(self):
        """Summary includes the case ID."""
        summary = _build_case_summary(
            "case-123456789012-2024-000001",
            {"severity": None, "affected_hostnames": [], "affected_ips": [],
             "incident_window_start": None, "incident_window_end": None,
             "error_signatures": []},
            {},
        )
        assert "case-123456789012-2024-000001" in summary

    def test_includes_severity(self):
        """Summary includes severity when available."""
        summary = _build_case_summary(
            "case-123",
            {"severity": "critical", "affected_hostnames": [], "affected_ips": [],
             "incident_window_start": None, "incident_window_end": None,
             "error_signatures": []},
            {},
        )
        assert "critical" in summary

    def test_includes_subject(self):
        """Summary includes subject from case body."""
        summary = _build_case_summary(
            "case-123",
            {"severity": None, "affected_hostnames": [], "affected_ips": [],
             "incident_window_start": None, "incident_window_end": None,
             "error_signatures": []},
            {"data": {"cases": [{"subject": "ECR connectivity failure"}]}},
        )
        assert "ECR connectivity failure" in summary

    def test_includes_affected_endpoints(self):
        """Summary includes affected endpoints."""
        summary = _build_case_summary(
            "case-123",
            {"severity": None, "affected_hostnames": ["ecr.us-east-1.amazonaws.com"],
             "affected_ips": ["10.0.1.5"],
             "incident_window_start": None, "incident_window_end": None,
             "error_signatures": []},
            {},
        )
        assert "ecr.us-east-1.amazonaws.com" in summary


# ---------------------------------------------------------------------------
# Integration-style test with mocked sub-agents
# ---------------------------------------------------------------------------


class TestInvestigateSupportCaseTool:
    """Integration tests for the investigate_support_case tool with mocked agents."""

    @patch('main._invoke_sub_agent')
    @patch('main._invoke_network_agent')
    @patch('main._CURRENT_USER_ID')
    @patch('main._CURRENT_CONVERSATION_ID')
    def test_support_agent_error_returns_refusal(
        self, mock_conv_id, mock_user_id, mock_network, mock_sub_agent
    ):
        """When Support_Agent returns an error, the tool returns a refusal."""
        mock_user_id.get.return_value = "test-user"
        mock_conv_id.get.return_value = "conv-123"
        mock_sub_agent.return_value = json.dumps({
            "success": False,
            "error": "Case not found or access denied"
        })

        from main import investigate_support_case
        # Call the underlying function (not the @tool wrapper)
        result_str = investigate_support_case.__wrapped__(
            case_id="case-999999999999-2024-000001"
        )
        result = json.loads(result_str)
        assert result["success"] is False
        assert "not found" in result["error"].lower() or "access" in result["error"].lower()

    @patch('main._invoke_sub_agent')
    @patch('main._invoke_network_agent')
    @patch('main._CURRENT_USER_ID')
    @patch('main._CURRENT_CONVERSATION_ID')
    @patch('main.state.record_support_case_context')
    @patch('main.state.load_capture_context')
    def test_successful_investigation_returns_four_sections(
        self, mock_load_ctx, mock_record_ctx, mock_conv_id, mock_user_id,
        mock_network, mock_sub_agent
    ):
        """Successful investigation returns a four-section response."""
        mock_user_id.get.return_value = "test-user"
        mock_conv_id.get.return_value = "conv-123"
        mock_load_ctx.return_value = None
        mock_record_ctx.return_value = {}

        # Mock Support_Agent responses
        def sub_agent_side_effect(env_var, action, params=None):
            if action == "describe_cases":
                return json.dumps({
                    "success": True,
                    "data": {"cases": [{
                        "subject": "ECR connectivity failure",
                        "body": "Cannot reach ecr.us-east-1.amazonaws.com on port 443. Error: connection timed out",
                        "severityCode": "high",
                        "serviceCode": "amazon-ecr",
                    }]}
                })
            elif action == "describe_communications":
                return json.dumps({"success": True, "data": {"communications": []}})
            elif action == "list_recommendations":
                return json.dumps({"success": True, "data": {"recommendations": []}})
            return json.dumps({"success": True, "data": {}})

        mock_sub_agent.side_effect = sub_agent_side_effect
        mock_network.return_value = json.dumps({"success": True, "data": {}})

        from main import investigate_support_case
        result_str = investigate_support_case.__wrapped__(
            case_id="case-123456789012-2024-000001"
        )
        result = json.loads(result_str)
        assert result["success"] is True
        assert "Case summary" in result["formattedText"]
        assert "Health correlation" in result["formattedText"]
        assert "Network analysis" in result["formattedText"]
        assert "Recommended next actions" in result["formattedText"]

    @patch('main._invoke_sub_agent')
    @patch('main._invoke_network_agent')
    @patch('main._CURRENT_USER_ID')
    @patch('main._CURRENT_CONVERSATION_ID')
    @patch('main.state.record_support_case_context')
    @patch('main.state.load_capture_context')
    def test_no_capture_offers_three_options(
        self, mock_load_ctx, mock_record_ctx, mock_conv_id, mock_user_id,
        mock_network, mock_sub_agent
    ):
        """When no capture_id is available, three options are offered (Req 20.6)."""
        mock_user_id.get.return_value = "test-user"
        mock_conv_id.get.return_value = "conv-123"
        mock_load_ctx.return_value = None  # No persisted capture
        mock_record_ctx.return_value = {}

        def sub_agent_side_effect(env_var, action, params=None):
            if action == "describe_cases":
                return json.dumps({
                    "success": True,
                    "data": {"cases": [{
                        "subject": "Issue with 10.0.1.5",
                        "body": "Connection to 10.0.1.5 port 443 fails",
                        "severityCode": "normal",
                    }]}
                })
            elif action == "describe_communications":
                return json.dumps({"success": True, "data": {"communications": []}})
            elif action == "list_recommendations":
                return json.dumps({"success": True, "data": {"recommendations": []}})
            return json.dumps({"success": True, "data": {}})

        mock_sub_agent.side_effect = sub_agent_side_effect

        from main import investigate_support_case
        result_str = investigate_support_case.__wrapped__(
            case_id="case-123456789012-2024-000001"
        )
        result = json.loads(result_str)
        assert result["success"] is True
        formatted = result["formattedText"]
        # Req 20.6: Three options offered
        assert "Start a new capture" in formatted or "a." in formatted
        assert "existing capture_id" in formatted or "b." in formatted
        assert "Proceed without" in formatted or "c." in formatted

    @patch('main._invoke_sub_agent')
    def test_plan_error_offers_manual_endpoints(self, mock_sub_agent):
        """Support plan error offers to proceed with manual endpoints (Req 20.11)."""
        mock_sub_agent.return_value = json.dumps({
            "success": False,
            "error": "Not subscribed to Business or Enterprise Support plan"
        })

        from main import investigate_support_case
        with patch('main._CURRENT_USER_ID') as mock_uid, \
             patch('main._CURRENT_CONVERSATION_ID') as mock_cid:
            mock_uid.get.return_value = "test-user"
            mock_cid.get.return_value = "conv-123"
            result_str = investigate_support_case.__wrapped__(
                case_id="case-123456789012-2024-000001"
            )
        result = json.loads(result_str)
        assert result["success"] is False
        assert result["metadata"]["errorCategory"] == "support_plan_required"
        assert "manual" in result["formattedText"].lower() or "endpoints" in result["formattedText"].lower()
