"""
Tests for Flow_Selector construction in the orchestration agent.

Task 40 — Validates hostname/IPv4/IPv6 extraction, role-inference rules,
Flow_Selector construction, TCP diagnosis routing, and resolved flow summary.

Requirements: 18.8, 18.9, 19.10, 19.11, 19.12, 19.13
"""
import pytest
from flow_selector import (
    ExtractedEndpoint,
    build_flow_selector,
    extract_endpoints,
    extract_ports,
    extract_stream_id,
    format_resolved_flow_summary,
    has_ambiguous_roles,
    is_tcp_diagnosis_request,
    is_valid_ipv4,
    is_valid_ipv6,
    is_valid_port,
    should_use_flow_selector,
    FLOW_SELECTOR_ACTIONS,
)


# ---------------------------------------------------------------------------
# IPv4 validation
# ---------------------------------------------------------------------------

class TestIsValidIpv4:
    def test_valid_addresses(self):
        assert is_valid_ipv4("10.0.1.5") is True
        assert is_valid_ipv4("192.168.0.1") is True
        assert is_valid_ipv4("0.0.0.0") is True
        assert is_valid_ipv4("255.255.255.255") is True

    def test_invalid_addresses(self):
        assert is_valid_ipv4("256.0.0.1") is False
        assert is_valid_ipv4("10.0.1") is False
        assert is_valid_ipv4("10.0.1.5.6") is False
        assert is_valid_ipv4("abc.def.ghi.jkl") is False
        assert is_valid_ipv4("01.02.03.04") is False  # leading zeros


# ---------------------------------------------------------------------------
# IPv6 validation
# ---------------------------------------------------------------------------

class TestIsValidIpv6:
    def test_valid_full_form(self):
        assert is_valid_ipv6("2001:0db8:85a3:0000:0000:8a2e:0370:7334") is True

    def test_valid_compressed(self):
        assert is_valid_ipv6("::1") is True
        assert is_valid_ipv6("fe80::1") is True
        assert is_valid_ipv6("2001:db8::1") is True

    def test_invalid(self):
        assert is_valid_ipv6("not-an-ipv6") is False
        assert is_valid_ipv6("10.0.1.5") is False
        assert is_valid_ipv6("") is False


# ---------------------------------------------------------------------------
# Port validation
# ---------------------------------------------------------------------------

class TestIsValidPort:
    def test_valid_ports(self):
        assert is_valid_port(0) is True
        assert is_valid_port(443) is True
        assert is_valid_port(65535) is True

    def test_invalid_ports(self):
        assert is_valid_port(-1) is False
        assert is_valid_port(65536) is False
        assert is_valid_port(True) is False


# ---------------------------------------------------------------------------
# Endpoint extraction with role inference (Req 19.10, 19.11)
# ---------------------------------------------------------------------------

class TestExtractEndpoints:
    def test_ipv4_extraction(self):
        text = "find resets from 10.0.1.5 to 172.16.0.1"
        endpoints = extract_endpoints(text)
        assert len(endpoints) == 2
        assert endpoints[0].value == "10.0.1.5"
        assert endpoints[0].kind == "ipv4"
        assert endpoints[0].role == "source"
        assert endpoints[1].value == "172.16.0.1"
        assert endpoints[1].kind == "ipv4"
        assert endpoints[1].role == "destination"

    def test_hostname_extraction(self):
        text = "diagnose the flow to ecr.us-east-1.amazonaws.com"
        endpoints = extract_endpoints(text)
        assert len(endpoints) == 1
        assert endpoints[0].value == "ecr.us-east-1.amazonaws.com"
        assert endpoints[0].kind == "hostname"
        assert endpoints[0].role == "destination"

    def test_source_keyword(self):
        text = "show traffic from my-service.internal.corp"
        endpoints = extract_endpoints(text)
        assert len(endpoints) == 1
        assert endpoints[0].role == "source"

    def test_client_keyword(self):
        text = "the client 10.0.2.3 is sending resets"
        endpoints = extract_endpoints(text)
        assert len(endpoints) == 1
        assert endpoints[0].role == "source"

    def test_server_keyword(self):
        text = "connections to server api.example.com are failing"
        endpoints = extract_endpoints(text)
        assert len(endpoints) == 1
        assert endpoints[0].role == "destination"

    def test_no_role_inference(self):
        text = "check 10.0.1.5 traffic"
        endpoints = extract_endpoints(text)
        assert len(endpoints) == 1
        assert endpoints[0].role is None

    def test_excludes_version_strings(self):
        text = "OpenSSL 3.5.5 is causing issues with ecr.amazonaws.com"
        endpoints = extract_endpoints(text)
        # Should extract the hostname but not "3.5.5"
        hostnames = [e for e in endpoints if e.kind == "hostname"]
        assert len(hostnames) == 1
        assert hostnames[0].value == "ecr.amazonaws.com"
        # "3.5.5" should not appear as an IPv4
        ipv4s = [e for e in endpoints if e.kind == "ipv4"]
        assert len(ipv4s) == 0

    def test_deduplication(self):
        text = "traffic from 10.0.1.5 to 10.0.1.5"
        endpoints = extract_endpoints(text)
        assert len(endpoints) == 1


# ---------------------------------------------------------------------------
# Port extraction (Req 19.11)
# ---------------------------------------------------------------------------

class TestExtractPorts:
    def test_destination_port(self):
        ports = extract_ports("connections on port 443")
        assert len(ports) == 1
        assert ports[0]["port"] == 443
        assert ports[0]["role"] == "destination"

    def test_source_port(self):
        ports = extract_ports("from source port 12345 to port 443")
        assert len(ports) == 2
        source_ports = [p for p in ports if p["role"] == "source"]
        dest_ports = [p for p in ports if p["role"] == "destination"]
        assert len(source_ports) == 1
        assert source_ports[0]["port"] == 12345
        assert len(dest_ports) == 1
        assert dest_ports[0]["port"] == 443

    def test_invalid_port_excluded(self):
        ports = extract_ports("on port 99999")
        assert len(ports) == 0


# ---------------------------------------------------------------------------
# Flow_Selector construction (Req 19.12)
# ---------------------------------------------------------------------------

class TestBuildFlowSelector:
    def test_source_and_destination(self):
        endpoints = [
            ExtractedEndpoint("10.0.1.5", "ipv4", role="source"),
            ExtractedEndpoint("ecr.amazonaws.com", "hostname", role="destination"),
        ]
        ports = [{"port": 443, "role": "destination"}]
        selector = build_flow_selector(endpoints, ports)
        assert selector == {
            "source_ip": "10.0.1.5",
            "destination_hostname": "ecr.amazonaws.com",
            "destination_port": 443,
        }

    def test_no_port_omitted(self):
        """Req 19.12: when hostname/IP supplied without port, omit port fields."""
        endpoints = [
            ExtractedEndpoint("api.example.com", "hostname", role="destination"),
        ]
        selector = build_flow_selector(endpoints, [])
        assert selector == {"destination_hostname": "api.example.com"}
        assert "destination_port" not in selector
        assert "source_port" not in selector

    def test_with_stream_id(self):
        endpoints = [
            ExtractedEndpoint("10.0.1.5", "ipv4", role="source"),
        ]
        selector = build_flow_selector(endpoints, [], stream_id="s-7")
        assert selector == {"source_ip": "10.0.1.5", "stream_id": "s-7"}

    def test_empty_returns_none(self):
        assert build_flow_selector([], []) is None

    def test_stream_id_only(self):
        selector = build_flow_selector([], [], stream_id="abc-123")
        assert selector == {"stream_id": "abc-123"}

    def test_ambiguous_role_assignment(self):
        """When roles are ambiguous, first goes to source, second to destination."""
        endpoints = [
            ExtractedEndpoint("10.0.1.5", "ipv4", role=None),
            ExtractedEndpoint("10.0.2.3", "ipv4", role=None),
        ]
        selector = build_flow_selector(endpoints, [])
        assert selector == {
            "source_ip": "10.0.1.5",
            "destination_ip": "10.0.2.3",
        }


# ---------------------------------------------------------------------------
# Ambiguity detection
# ---------------------------------------------------------------------------

class TestHasAmbiguousRoles:
    def test_no_ambiguity_single_endpoint(self):
        endpoints = [ExtractedEndpoint("10.0.1.5", "ipv4", role=None)]
        assert has_ambiguous_roles(endpoints) is False

    def test_ambiguous_multiple_endpoints(self):
        endpoints = [
            ExtractedEndpoint("10.0.1.5", "ipv4", role=None),
            ExtractedEndpoint("10.0.2.3", "ipv4", role=None),
        ]
        assert has_ambiguous_roles(endpoints) is True

    def test_no_ambiguity_all_roles_assigned(self):
        endpoints = [
            ExtractedEndpoint("10.0.1.5", "ipv4", role="source"),
            ExtractedEndpoint("10.0.2.3", "ipv4", role="destination"),
        ]
        assert has_ambiguous_roles(endpoints) is False


# ---------------------------------------------------------------------------
# TCP diagnosis detection (Req 18.9)
# ---------------------------------------------------------------------------

class TestIsTcpDiagnosisRequest:
    def test_matches_diagnosis_phrasings(self):
        assert is_tcp_diagnosis_request("what is wrong with my TCP stream") is True
        assert is_tcp_diagnosis_request("diagnose stream s-7") is True
        assert is_tcp_diagnosis_request("why did this connection fail") is True
        assert is_tcp_diagnosis_request("diagnose the TCP exchange between A and B") is True
        assert is_tcp_diagnosis_request("analyze the TCP connection") is True
        assert is_tcp_diagnosis_request("why does my pod fail to reach ecr") is True

    def test_does_not_match_unrelated(self):
        assert is_tcp_diagnosis_request("show me TLS Client Hello sizes") is False
        assert is_tcp_diagnosis_request("list all ENIs") is False
        assert is_tcp_diagnosis_request("start a capture") is False


# ---------------------------------------------------------------------------
# Resolved flow summary formatting (Req 19.13)
# ---------------------------------------------------------------------------

class TestFormatResolvedFlowSummary:
    def test_full_summary(self):
        selector = {
            "source_ip": "10.0.1.5",
            "destination_hostname": "ecr.us-east-1.amazonaws.com",
            "destination_port": 443,
        }
        result = format_resolved_flow_summary(selector, stream_count=3)
        assert "Resolved" in result
        assert "10.0.1.5" in result
        assert "ecr.us-east-1.amazonaws.com" in result
        assert ":443" in result
        assert "3 stream(s)" in result

    def test_source_only(self):
        selector = {"source_ip": "10.0.1.5"}
        result = format_resolved_flow_summary(selector, stream_count=1)
        assert "10.0.1.5" in result
        assert "-> *" in result
        assert "1 stream(s)" in result

    def test_destination_only(self):
        selector = {"destination_hostname": "api.example.com"}
        result = format_resolved_flow_summary(selector)
        assert "* ->" in result
        assert "api.example.com" in result
        assert "matching stream(s)" in result


# ---------------------------------------------------------------------------
# should_use_flow_selector gate (Req 19.10)
# ---------------------------------------------------------------------------

class TestShouldUseFlowSelector:
    def test_returns_true_for_supported_action_with_endpoints(self):
        text = "find resets to 10.0.1.5"
        assert should_use_flow_selector(text, "classify_tcp_resets") is True

    def test_returns_false_for_unsupported_action(self):
        text = "find resets to 10.0.1.5"
        assert should_use_flow_selector(text, "list_enis") is False

    def test_returns_false_when_no_endpoints(self):
        text = "show me all retransmissions"
        assert should_use_flow_selector(text, "detect_retransmissions") is False


# ---------------------------------------------------------------------------
# Stream ID extraction
# ---------------------------------------------------------------------------

class TestExtractStreamId:
    def test_extracts_numeric_stream(self):
        assert extract_stream_id("diagnose stream 7") == "7"

    def test_extracts_alphanumeric_stream(self):
        assert extract_stream_id("look at stream s-42") == "s-42"

    def test_extracts_with_id_prefix(self):
        assert extract_stream_id("stream id abc-123") == "abc-123"

    def test_returns_none_when_no_stream(self):
        assert extract_stream_id("show me TLS sizes") is None

    def test_excludes_common_words(self):
        assert extract_stream_id("stream in my capture") is None
