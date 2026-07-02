"""
Validation helpers for G.O.A.T. Network Agent diagnostic-action inputs.

Implements Task 1.1 of the goat-network-diagnostics spec: validators for
the six new diagnostic actions (tcp_traceroute, tls_traceroute,
agentic_reachability_analyze, dns_resolve, db_connectivity_probe,
ssm_health_check).

Validators:
- ``validate_instance_id``          — EC2 instance ID ``^i-[0-9a-f]{8,17}$``
  (Reqs 5.1, 5.2)
- ``validate_destination_host``     — hostname 1-253 chars (Reqs 5.3, 5.5)
- ``validate_port``                 — integer in 1-65535 (Req 5.4)
- ``validate_max_hops``             — integer in 1-30 (Req 5.6)
- ``validate_probe_timeout``        — integer in 1-5 (Req 5.7)
- ``validate_vpc_resource_id``      — VPC resource ID patterns (Req 9.8)
- ``validate_reachability_source``  — VPC resource ID only, rejects IPv4
  (Req 9.9, 9.34)
- ``validate_reachability_destination`` — VPC resource ID or IPv4 (Req 9.33)
- ``validate_ipv4_address``         — IPv4 format
- ``validate_record_type``          — DNS record type enum
- ``validate_engine``               — database engine enum
- ``validate_sni_override``         — SNI string 1-253 chars
- ``validate_protocol``             — tcp or udp

Every validator raises :class:`ValidationError` (imported from the existing
``validation`` module in the same directory) with
``error_category="invalid_parameter"`` and a human-readable message on
failure.
"""

from __future__ import annotations

import re

from validation import ValidationError


# ---------------------------------------------------------------------------
# Compiled patterns
#
# Compiled at module level so each validator call is just a regex match.
# ---------------------------------------------------------------------------

# EC2 instance ID format (Req 5.1): ``i-`` followed by 8-17 hex characters.
_INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")

# IPv4 address format (Req 9.33): four dot-separated groups of 1-3 digits.
_IPV4_PATTERN = re.compile(r"^([0-9]{1,3}\.){3}[0-9]{1,3}$")

# VPC resource ID patterns (Req 9.8). Order matters: longer prefixes first
# so that ``tgw-attach-`` is tested before ``tgw-``.
_VPC_RESOURCE_PATTERNS = [
    re.compile(r"^tgw-attach-[0-9a-f]{17}$"),
    re.compile(r"^vpce-svc-[0-9a-f]{17}$"),
    re.compile(r"^i-[0-9a-f]{8,17}$"),
    re.compile(r"^eni-[0-9a-f]{8,17}$"),
    re.compile(r"^igw-[0-9a-f]{8,17}$"),
    re.compile(r"^tgw-[0-9a-f]{17}$"),
    re.compile(r"^vpce-[0-9a-f]{8,17}$"),
    re.compile(r"^pcx-[0-9a-f]{8,17}$"),
    re.compile(r"^vgw-[0-9a-f]{8,17}$"),
]

# Accepted DNS record types (Req 10.2).
_VALID_RECORD_TYPES = frozenset({"A", "AAAA", "CNAME", "MX", "TXT", "SRV", "PTR"})

# Accepted database engines (Req 11.2).
_VALID_ENGINES = frozenset({"mysql", "postgresql"})

# Accepted protocols for reachability analysis (Req 9.11).
_VALID_PROTOCOLS = frozenset({"tcp", "udp"})


# ---------------------------------------------------------------------------
# Helper guards (same pattern as the existing validation.py module)
# ---------------------------------------------------------------------------


def _ensure_string(value, field_name: str) -> str:
    """Reject non-string inputs with a uniform error message."""
    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    return value


def _ensure_int(value, field_name: str) -> int:
    """Reject non-integer inputs (including ``bool``) with a uniform error."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    return value


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_instance_id(value) -> str:
    """Validate an EC2 ``instance_id`` against Instance_Id_Format.

    Pattern: ``^i-[0-9a-f]{8,17}$`` (Reqs 5.1, 5.2).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated instance_id string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, empty,
            or does not match ``^i-[0-9a-f]{8,17}$``.
    """
    if value is None:
        raise ValidationError("instance_id is required")

    s = _ensure_string(value, "instance_id")

    if not s:
        raise ValidationError("instance_id must not be empty")

    if not _INSTANCE_ID_PATTERN.match(s):
        raise ValidationError(
            "instance_id must match the pattern ^i-[0-9a-f]{8,17}$ "
            f"(e.g. i-0123456789abcdef0), got {s!r}"
        )

    return s


def validate_destination_host(value) -> str:
    """Validate a ``destination_host`` hostname.

    Constraints: non-empty string of 1-253 characters (Reqs 5.3, 5.5).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated destination_host string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, empty,
            or exceeds 253 characters.
    """
    if value is None:
        raise ValidationError("destination_host is required")

    s = _ensure_string(value, "destination_host")

    if not s:
        raise ValidationError("destination_host must not be empty")

    if len(s) > 253:
        raise ValidationError(
            f"destination_host must be 1-253 characters, got {len(s)}"
        )

    return s


def validate_port(value, param_name: str = "destination_port") -> int:
    """Validate a port number is an integer in range 1-65535.

    Args:
        value: The raw value supplied by the caller.
        param_name: The parameter name to use in error messages.

    Returns:
        The validated integer.

    Raises:
        ValidationError: If ``value`` is not an integer or is outside
            the inclusive range 1..65535.
    """
    if value is None:
        raise ValidationError(f"{param_name} is required")

    n = _ensure_int(value, param_name)

    if n < 1 or n > 65535:
        raise ValidationError(
            f"{param_name} must be an integer in 1..65535, got {n}"
        )

    return n


def validate_max_hops(value) -> int:
    """Validate ``max_hops`` against the range 1-30 (Req 5.6).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated integer.

    Raises:
        ValidationError: If ``value`` is not an integer or is outside
            the inclusive range 1..30.
    """
    if value is None:
        raise ValidationError("max_hops is required")

    n = _ensure_int(value, "max_hops")

    if n < 1 or n > 30:
        raise ValidationError(
            f"max_hops must be an integer in 1..30, got {n}"
        )

    return n


def validate_probe_timeout(value) -> int:
    """Validate ``probe_timeout`` against the range 1-5 (Req 5.7).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated integer.

    Raises:
        ValidationError: If ``value`` is not an integer or is outside
            the inclusive range 1..5.
    """
    if value is None:
        raise ValidationError("probe_timeout is required")

    n = _ensure_int(value, "probe_timeout")

    if n < 1 or n > 5:
        raise ValidationError(
            f"probe_timeout must be an integer in 1..5, got {n}"
        )

    return n


def _is_vpc_resource_id(value: str) -> bool:
    """Return True if value matches any supported VPC resource ID pattern."""
    return any(pattern.match(value) for pattern in _VPC_RESOURCE_PATTERNS)


def validate_vpc_resource_id(value, param_name: str) -> str:
    """Validate a VPC resource ID against supported patterns (Req 9.8).

    Supported prefixes: i-, eni-, igw-, tgw-, tgw-attach-, vpce-,
    vpce-svc-, pcx-, vgw-.

    Args:
        value: The raw value supplied by the caller.
        param_name: The parameter name to use in error messages.

    Returns:
        The validated VPC resource ID string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, or does
            not match any supported VPC resource ID pattern.
    """
    if value is None:
        raise ValidationError(f"{param_name} is required")

    s = _ensure_string(value, param_name)

    if not s:
        raise ValidationError(f"{param_name} must not be empty")

    if not _is_vpc_resource_id(s):
        raise ValidationError(
            f"{param_name} must be a valid VPC resource ID "
            "(supported prefixes: i-, eni-, igw-, tgw-, tgw-attach-, "
            f"vpce-, vpce-svc-, pcx-, vgw-), got {s!r}"
        )

    return s


def validate_reachability_source(value) -> str:
    """Validate source for reachability analysis (Reqs 9.9, 9.34).

    Accepts ONLY VPC resource IDs (NOT IPv4 addresses). If the value
    looks like an IPv4 address, returns an explicit error indicating
    that IP addresses are only supported as destinations.

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated VPC resource ID string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, matches
            an IPv4 pattern (rejected with explicit message), or does
            not match any supported VPC resource ID pattern.
    """
    if value is None:
        raise ValidationError("source is required")

    s = _ensure_string(value, "source")

    if not s:
        raise ValidationError("source must not be empty")

    # Check if the value looks like an IPv4 address — reject explicitly.
    if _IPV4_PATTERN.match(s):
        raise ValidationError(
            "IP addresses are only supported as destinations, not sources. "
            "Use a VPC resource ID (instance, ENI, gateway) as the source."
        )

    if not _is_vpc_resource_id(s):
        raise ValidationError(
            "source must be a valid VPC resource ID "
            "(supported prefixes: i-, eni-, igw-, tgw-, tgw-attach-, "
            f"vpce-, vpce-svc-, pcx-, vgw-), got {s!r}"
        )

    return s


def validate_reachability_destination(value) -> str:
    """Validate destination for reachability analysis (Req 9.33).

    Accepts VPC resource IDs OR IPv4 addresses matching
    ``^([0-9]{1,3}\\.){3}[0-9]{1,3}$``.

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, or
            matches neither a VPC resource ID nor an IPv4 address.
    """
    if value is None:
        raise ValidationError("destination is required")

    s = _ensure_string(value, "destination")

    if not s:
        raise ValidationError("destination must not be empty")

    # Accept VPC resource ID patterns first.
    if _is_vpc_resource_id(s):
        return s

    # Accept IPv4 address pattern.
    if _IPV4_PATTERN.match(s):
        return s

    raise ValidationError(
        "destination must be a valid VPC resource ID "
        "(supported prefixes: i-, eni-, igw-, tgw-, tgw-attach-, "
        "vpce-, vpce-svc-, pcx-, vgw-) or an IPv4 address "
        f"(e.g. 10.0.1.5), got {s!r}"
    )


def validate_ipv4_address(value) -> str:
    """Validate IPv4 address format ``^([0-9]{1,3}\\.){3}[0-9]{1,3}$``.

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated IPv4 address string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, or does
            not match the IPv4 address pattern.
    """
    if value is None:
        raise ValidationError("ipv4_address is required")

    s = _ensure_string(value, "ipv4_address")

    if not s:
        raise ValidationError("ipv4_address must not be empty")

    if not _IPV4_PATTERN.match(s):
        raise ValidationError(
            f"ipv4_address must match the pattern "
            f"^([0-9]{{1,3}}\\.){{3}}[0-9]{{1,3}}$ "
            f"(e.g. 10.0.1.5), got {s!r}"
        )

    return s


def validate_record_type(value) -> str:
    """Validate DNS record type is one of A, AAAA, CNAME, MX, TXT, SRV, PTR.

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated record_type string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, or not
            one of the accepted record types.
    """
    if value is None:
        raise ValidationError("record_type is required")

    s = _ensure_string(value, "record_type")

    if s not in _VALID_RECORD_TYPES:
        accepted = ", ".join(sorted(_VALID_RECORD_TYPES))
        raise ValidationError(
            f"record_type must be one of {accepted}, got {s!r}"
        )

    return s


def validate_engine(value) -> str:
    """Validate database engine is one of mysql, postgresql.

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated engine string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, or not
            one of the accepted engines.
    """
    if value is None:
        raise ValidationError("engine is required")

    s = _ensure_string(value, "engine")

    if s not in _VALID_ENGINES:
        accepted = ", ".join(sorted(_VALID_ENGINES))
        raise ValidationError(
            f"engine must be one of {accepted}, got {s!r}"
        )

    return s


def validate_sni_override(value) -> str:
    """Validate SNI override is 1-253 characters.

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated sni_override string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, empty,
            or exceeds 253 characters.
    """
    if value is None:
        raise ValidationError("sni_override is required")

    s = _ensure_string(value, "sni_override")

    if not s:
        raise ValidationError("sni_override must not be empty")

    if len(s) > 253:
        raise ValidationError(
            f"sni_override must be 1-253 characters, got {len(s)}"
        )

    return s


def validate_protocol(value) -> str:
    """Validate protocol is tcp or udp (Req 9.11).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated protocol string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, or not
            one of the accepted protocols.
    """
    if value is None:
        raise ValidationError("protocol is required")

    s = _ensure_string(value, "protocol")

    if s not in _VALID_PROTOCOLS:
        accepted = ", ".join(sorted(_VALID_PROTOCOLS))
        raise ValidationError(
            f"protocol must be one of {accepted}, got {s!r}"
        )

    return s


__all__ = [
    "validate_instance_id",
    "validate_destination_host",
    "validate_port",
    "validate_max_hops",
    "validate_probe_timeout",
    "validate_vpc_resource_id",
    "validate_reachability_source",
    "validate_reachability_destination",
    "validate_ipv4_address",
    "validate_record_type",
    "validate_engine",
    "validate_sni_override",
    "validate_protocol",
]
