"""
Flow_Selector construction for the G.O.A.T. Orchestration Agent.

Task 40 — Implements hostname/IPv4/IPv6 extraction from chat input,
role-inference rules, and Flow_Selector construction for Pcap_Query_Actions.

Requirements: 18.8, 18.9, 19.10, 19.11, 19.12, 19.13
"""
import re
from typing import Optional


# ---------------------------------------------------------------------------
# Pattern definitions (Req 19.10)
#
# These compiled regexes detect hostnames, IPv4, and IPv6 addresses in
# free-form chat text. They are intentionally permissive on boundaries
# (word-boundary anchored) so they match values embedded in natural
# language without requiring the user to quote them.
# ---------------------------------------------------------------------------

# IPv4: four dot-separated octets (0-255). Word-boundary anchored so
# "10.0.1.5" matches but "v10.0.1.5x" does not.
_IPV4_PATTERN = re.compile(
    r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
)

# IPv6: covers full form (8 groups of 1-4 hex digits separated by colons)
# and compressed forms (with ::). The regex is intentionally broad —
# validation happens in ``is_valid_ipv6``.
_IPV6_PATTERN = re.compile(
    r"\b((?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?::(?:[0-9A-Fa-f]{1,4}:){0,5}[0-9A-Fa-f]{0,4})?)\b"
)

# Hostname: one or more labels separated by dots, ending with a TLD of
# at least 2 characters. Matches "ecr.us-east-1.amazonaws.com" but not
# bare words like "hello" or version strings like "3.5.5".
_HOSTNAME_PATTERN = re.compile(
    r"\b([A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,})\b"
)

# Port extraction: "port 443", "on port 8080", "source port 12345"
_PORT_PATTERN = re.compile(
    r"\b(?:(?:source\s+)?port|on\s+port)\s+(\d{1,5})\b",
    re.IGNORECASE,
)

# Source-port qualifier: "source port 12345"
_SOURCE_PORT_PATTERN = re.compile(
    r"\bsource\s+port\s+(\d{1,5})\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Role-inference keyword sets (Req 19.11)
#
# Tokens preceding a hostname/IP that indicate its role in the flow.
# The patterns are matched case-insensitively against the word(s)
# immediately before the extracted value.
# ---------------------------------------------------------------------------

# Words that assign the following value to the SOURCE role.
_SOURCE_KEYWORDS = frozenset({
    "from", "source", "client", "originating from",
})

# Words that assign the following value to the DESTINATION role.
_DESTINATION_KEYWORDS = frozenset({
    "to", "destination", "server", "reaching",
})

# Combined pattern for role-inference context extraction. Captures
# 1-3 words before a hostname/IP token so we can check membership.
_ROLE_CONTEXT_WINDOW = 4  # max words to look back for role keywords


# ---------------------------------------------------------------------------
# TCP diagnosis natural-language phrasings (Req 18.9)
#
# These patterns detect when the user is asking for a TCP-level
# diagnosis so the orchestration agent routes to ``diagnose_tcp_stream``
# rather than individual lower-level actions.
# ---------------------------------------------------------------------------
_TCP_DIAGNOSIS_PATTERNS = (
    re.compile(r"\bwhat\s+is\s+wrong\s+with\s+(?:my\s+)?tcp\s+stream\b", re.IGNORECASE),
    re.compile(r"\bdiagnose\s+(?:tcp\s+)?stream\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+did\s+(?:this|the|my)\s+(?:tcp\s+)?connection\s+fail\b", re.IGNORECASE),
    re.compile(r"\bdiagnose\s+(?:the\s+)?tcp\s+(?:exchange|connection|flow)\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s|\s+is)\s+wrong\s+with\s+(?:the\s+)?(?:tcp\s+)?(?:connection|flow|stream)\b", re.IGNORECASE),
    re.compile(r"\btcp\s+(?:stream\s+)?(?:health|diagnosis|diagnostic|report)\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+(?:is|does)\s+(?:my|the|this)\s+(?:pod|instance|service|lambda|container)\s+fail(?:ing)?\s+to\s+(?:reach|connect|talk)\b", re.IGNORECASE),
    re.compile(r"\banalyze\s+(?:the\s+)?tcp\s+(?:stream|connection|exchange|flow)\b", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Pcap_Query_Actions that accept flow_selector targeting
# ---------------------------------------------------------------------------
FLOW_SELECTOR_ACTIONS = frozenset({
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
})


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def is_valid_ipv4(value: str) -> bool:
    """Return True if ``value`` is a syntactically valid IPv4 address."""
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        try:
            num = int(part)
        except ValueError:
            return False
        if num < 0 or num > 255:
            return False
        # Reject leading zeros (e.g. "01.02.03.04") except for "0" itself
        if len(part) > 1 and part[0] == "0":
            return False
    return True


def is_valid_ipv6(value: str) -> bool:
    """Return True if ``value`` is a syntactically valid IPv6 address.

    Accepts full and compressed (::) forms.
    """
    # Quick reject: must contain at least one colon
    if ":" not in value:
        return False
    # Handle :: expansion
    if "::" in value:
        parts = value.split("::")
        if len(parts) > 2:
            return False
        left = parts[0].split(":") if parts[0] else []
        right = parts[1].split(":") if parts[1] else []
        if len(left) + len(right) > 7:
            return False
        groups = left + ["0"] * (8 - len(left) - len(right)) + right
    else:
        groups = value.split(":")
        if len(groups) != 8:
            return False
    for group in groups:
        if not group:
            return False
        if len(group) > 4:
            return False
        try:
            int(group, 16)
        except ValueError:
            return False
    return True


def is_valid_port(value: int) -> bool:
    """Return True if ``value`` is a valid port number (0-65535)."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 65535


# ---------------------------------------------------------------------------
# Hostname exclusion list — common version strings and numeric patterns
# that the hostname regex might match but are not actual hostnames.
# ---------------------------------------------------------------------------
_HOSTNAME_EXCLUSIONS = re.compile(
    r"^\d+\.\d+\.\d+$"  # version strings like "3.5.5"
    r"|^\d+\.\d+$"       # version strings like "2.31"
    r"|^v\d"             # version prefixes like "v1.2.3"
)


def _is_likely_hostname(value: str) -> bool:
    """Return True if the value looks like a real hostname, not a version string."""
    if _HOSTNAME_EXCLUSIONS.match(value):
        return False
    # Must have at least one dot and a TLD of 2+ alpha chars
    parts = value.rsplit(".", 1)
    if len(parts) != 2:
        return False
    tld = parts[1]
    return len(tld) >= 2 and tld.isalpha()


# ---------------------------------------------------------------------------
# Core extraction and construction functions
# ---------------------------------------------------------------------------

class ExtractedEndpoint:
    """A hostname or IP extracted from chat text with optional role and port."""

    __slots__ = ("value", "kind", "role", "port", "position")

    def __init__(
        self,
        value: str,
        kind: str,  # "ipv4", "ipv6", "hostname"
        role: Optional[str] = None,  # "source", "destination", or None (ambiguous)
        port: Optional[int] = None,
        position: int = 0,
    ):
        self.value = value
        self.kind = kind
        self.role = role
        self.port = port
        self.position = position

    def __repr__(self) -> str:
        return (
            f"ExtractedEndpoint(value={self.value!r}, kind={self.kind!r}, "
            f"role={self.role!r}, port={self.port!r})"
        )


def extract_endpoints(text: str) -> list[ExtractedEndpoint]:
    """Extract all hostnames, IPv4, and IPv6 addresses from chat text.

    Returns a list of ExtractedEndpoint objects with role inference
    applied based on surrounding context words.
    """
    endpoints: list[ExtractedEndpoint] = []
    seen_values: set[str] = set()

    # Extract IPv4 addresses
    for match in _IPV4_PATTERN.finditer(text):
        value = match.group(1)
        if is_valid_ipv4(value) and value not in seen_values:
            role = _infer_role(text, match.start(), match.end())
            endpoints.append(ExtractedEndpoint(
                value=value, kind="ipv4", role=role, position=match.start()
            ))
            seen_values.add(value)

    # Extract IPv6 addresses
    for match in _IPV6_PATTERN.finditer(text):
        value = match.group(1)
        if is_valid_ipv6(value) and value not in seen_values:
            role = _infer_role(text, match.start(), match.end())
            endpoints.append(ExtractedEndpoint(
                value=value, kind="ipv6", role=role, position=match.start()
            ))
            seen_values.add(value)

    # Extract hostnames (excluding values already captured as IPs)
    for match in _HOSTNAME_PATTERN.finditer(text):
        value = match.group(1)
        if value not in seen_values and _is_likely_hostname(value):
            role = _infer_role(text, match.start(), match.end())
            endpoints.append(ExtractedEndpoint(
                value=value, kind="hostname", role=role, position=match.start()
            ))
            seen_values.add(value)

    # Sort by position in text for deterministic ordering
    endpoints.sort(key=lambda e: e.position)
    return endpoints


def extract_ports(text: str) -> list[dict]:
    """Extract port numbers from chat text with role inference.

    Returns a list of dicts with keys: ``port`` (int), ``role``
    ("source" or "destination").
    """
    ports: list[dict] = []
    seen_ports: set[tuple[int, str]] = set()

    # Source ports first (more specific pattern)
    for match in _SOURCE_PORT_PATTERN.finditer(text):
        try:
            port_val = int(match.group(1))
        except ValueError:
            continue
        if is_valid_port(port_val) and (port_val, "source") not in seen_ports:
            ports.append({"port": port_val, "role": "source"})
            seen_ports.add((port_val, "source"))

    # General port pattern — defaults to destination unless already
    # captured as source port
    for match in _PORT_PATTERN.finditer(text):
        try:
            port_val = int(match.group(1))
        except ValueError:
            continue
        if not is_valid_port(port_val):
            continue
        # Check if this match overlaps with a source port match
        # by looking at the full match text
        full_match_text = match.group(0).strip().lower()
        if full_match_text.startswith("source"):
            continue  # Already captured as source port above
        if (port_val, "destination") not in seen_ports:
            ports.append({"port": port_val, "role": "destination"})
            seen_ports.add((port_val, "destination"))

    return ports


def _infer_role(text: str, start: int, end: int) -> Optional[str]:
    """Infer the role (source/destination) of a value based on preceding words.

    Looks at the words immediately before the matched value in the text.
    Returns "source", "destination", or None (ambiguous).
    """
    # Get the text before the match, limited to a reasonable window
    prefix = text[max(0, start - 60):start].strip().lower()
    if not prefix:
        return None

    # Split into words and check the last few
    words = prefix.split()
    if not words:
        return None

    # Check multi-word patterns first ("originating from")
    tail = " ".join(words[-3:]) if len(words) >= 3 else " ".join(words)

    if "originating from" in tail:
        return "source"

    # Check single-word patterns in the last 2 words
    check_words = words[-_ROLE_CONTEXT_WINDOW:]
    for word in reversed(check_words):
        # Strip trailing punctuation from the context word
        cleaned = word.rstrip(",:;.")
        if cleaned in _SOURCE_KEYWORDS:
            return "source"
        if cleaned in _DESTINATION_KEYWORDS:
            return "destination"

    return None


def build_flow_selector(
    endpoints: list[ExtractedEndpoint],
    ports: list[dict],
    stream_id: Optional[str] = None,
) -> Optional[dict]:
    """Construct a Flow_Selector dict from extracted endpoints and ports.

    Returns None if no endpoints or stream_id are available.
    When a hostname/IP is supplied without a port, port fields are
    omitted entirely (Req 19.12 — do not default to 0).

    Args:
        endpoints: Extracted endpoints with role inference applied.
        ports: Extracted ports with role assignment.
        stream_id: Optional explicit stream_id from the user's message.

    Returns:
        A Flow_Selector dict suitable for passing to the Network Agent,
        or None if insufficient data is available.
    """
    if not endpoints and not stream_id:
        return None

    selector: dict = {}

    # Assign endpoints to source/destination based on inferred roles
    for ep in endpoints:
        if ep.role == "source":
            if ep.kind == "hostname":
                selector["source_hostname"] = ep.value
            else:
                selector["source_ip"] = ep.value
        elif ep.role == "destination":
            if ep.kind == "hostname":
                selector["destination_hostname"] = ep.value
            else:
                selector["destination_ip"] = ep.value
        else:
            # Ambiguous role — assign based on position heuristic:
            # first endpoint without a role goes to source if no source
            # exists yet, otherwise destination.
            if ep.kind == "hostname":
                if "source_hostname" not in selector and "source_ip" not in selector:
                    selector["source_hostname"] = ep.value
                elif "destination_hostname" not in selector and "destination_ip" not in selector:
                    selector["destination_hostname"] = ep.value
            else:
                if "source_ip" not in selector and "source_hostname" not in selector:
                    selector["source_ip"] = ep.value
                elif "destination_ip" not in selector and "destination_hostname" not in selector:
                    selector["destination_ip"] = ep.value

    # Assign ports (Req 19.12: omit port fields when not supplied)
    for port_entry in ports:
        port_val = port_entry["port"]
        if port_entry["role"] == "source":
            selector["source_port"] = port_val
        else:
            selector["destination_port"] = port_val

    # Include stream_id when provided
    if stream_id:
        selector["stream_id"] = stream_id

    return selector if selector else None


def has_ambiguous_roles(endpoints: list[ExtractedEndpoint]) -> bool:
    """Return True if any endpoint has an ambiguous (None) role AND there
    are multiple endpoints, making disambiguation necessary.

    When there is exactly one endpoint with no role, it can be assigned
    unambiguously (the user is asking about flows involving that single
    address). Ambiguity only arises when two or more endpoints lack
    role inference.
    """
    ambiguous_count = sum(1 for ep in endpoints if ep.role is None)
    return ambiguous_count >= 2


def is_tcp_diagnosis_request(text: str) -> bool:
    """Return True if the text matches a TCP-level diagnosis phrasing.

    These phrasings should route to ``diagnose_tcp_stream`` rather than
    individual lower-level analysis actions (Req 18.9).
    """
    for pattern in _TCP_DIAGNOSIS_PATTERNS:
        if pattern.search(text):
            return True
    return False


def format_resolved_flow_summary(
    flow_selector: dict,
    stream_count: Optional[int] = None,
) -> str:
    """Format the one-line resolved flow summary for chat replies (Req 19.13).

    Returns a string in the form:
        Resolved <source-summary> -> <destination-summary> across N stream(s)

    Where each summary lists the supplied hostname (when present) and
    the resolved IP set in parentheses.
    """
    source_parts: list[str] = []
    dest_parts: list[str] = []

    # Source summary
    if flow_selector.get("source_hostname"):
        source_parts.append(flow_selector["source_hostname"])
    if flow_selector.get("source_ip"):
        if source_parts:
            source_parts.append(f"({flow_selector['source_ip']})")
        else:
            source_parts.append(flow_selector["source_ip"])
    if flow_selector.get("source_port") is not None:
        source_parts.append(f":{flow_selector['source_port']}")

    # Destination summary
    if flow_selector.get("destination_hostname"):
        dest_parts.append(flow_selector["destination_hostname"])
    if flow_selector.get("destination_ip"):
        if dest_parts:
            dest_parts.append(f"({flow_selector['destination_ip']})")
        else:
            dest_parts.append(flow_selector["destination_ip"])
    if flow_selector.get("destination_port") is not None:
        dest_parts.append(f":{flow_selector['destination_port']}")

    source_str = " ".join(source_parts) if source_parts else "*"
    dest_str = " ".join(dest_parts) if dest_parts else "*"

    count_str = f"{stream_count} stream(s)" if stream_count is not None else "matching stream(s)"

    return f"Resolved {source_str} -> {dest_str} across {count_str}"


def should_use_flow_selector(text: str, action: str) -> bool:
    """Return True if the chat text contains extractable endpoints AND
    the action supports flow_selector targeting.

    This is the gate that decides whether to construct a flow_selector
    rather than asking for a stream_id (Req 19.10).
    """
    if action not in FLOW_SELECTOR_ACTIONS:
        return False
    endpoints = extract_endpoints(text)
    return len(endpoints) > 0


def extract_stream_id(text: str) -> Optional[str]:
    """Extract an explicit stream_id from chat text.

    Matches patterns like "stream 7", "stream_id s-7", "tcp stream 42",
    "stream abc-123".
    """
    # Pattern: "stream" followed by an identifier
    match = re.search(
        r"\bstream\s+(?:id\s+)?([A-Za-z0-9_\-]{1,64})\b",
        text,
        re.IGNORECASE,
    )
    if match:
        candidate = match.group(1)
        # Exclude common words that might follow "stream"
        if candidate.lower() not in {"in", "from", "to", "for", "of", "the", "my", "a", "an"}:
            return candidate
    return None
