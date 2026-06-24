"""
Flow_Selector resolution for the G.O.A.T. Network Agent (Task 17).

Implements Reqs 5.24-5.27 and 19.1-19.9, 19.14: hostname/IP/port flow
targeting for Pcap_Query_Action handlers using the
Hostname_Resolution_Strategy ``combined`` (dns_in_capture →
tls_sni_in_capture → active_dns_lookup), per-hostname 5s and overall
15s active-DNS budget, and the source-only / destination-only role
logic documented in Reqs 19.6 and 19.7.

This module is intentionally side-effect-light: it only depends on
``athena_helper.run_athena_query`` for the in-capture strategies and
the standard library ``socket`` module for ``active_dns_lookup``. Tests
inject fakes by monkey-patching :func:`run_athena_query` on this
module and :data:`_ACTIVE_DNS_RESOLVER` for the active lookup.

Public entry points
-------------------

``validate_flow_selector(flow_selector)``
    Shape-validate the Flow_Selector dict. Returns the normalized form
    (port fields cast to ``int``, hostnames stripped) or raises
    :class:`FlowSelectorError` with ``error_category="invalid_parameter"``.

``resolve_flow_selector(capture_id, flow_selector)``
    Resolve any hostnames in the Flow_Selector to IP addresses using the
    ``combined`` strategy and return a :class:`ResolvedFlowSelector`
    object suitable for predicate construction. Raises
    :class:`FlowSelectorError` when a supplied hostname cannot be
    resolved by any of the three strategies (Req 19.3).

``build_flow_predicate(resolved)``
    Build the Athena ``AND``-joined predicate fragment scoping a query
    to the resolved flow. The fragment is intended to be appended after
    an existing ``WHERE capture_id = '...'`` predicate.

``build_resolved_flow_set_metadata(resolved)``
    Build the ``metadata.resolved_flow_set`` payload documented by
    Req 5.27 / Req 19.9.

``query_matched_streams(capture_id, predicate)``
    Run a single Athena aggregate to populate
    ``metadata.matched_stream_count`` and ``metadata.matched_streams``
    (Req 19.5). Returns ``(count, streams)``; streams is a list of
    ``{stream_id, client_ip, client_port, server_ip, server_port,
    packet_count}`` dicts.

The module deliberately keeps the actual SQL templates for the
in-capture strategies short and focused — they exist purely to enrich
flow-targeting predicates with locally-observed evidence and never
return rows back to the user.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from athena_helper import (
    AthenaConfigurationError,
    AthenaQueryError,
    run_athena_query,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exception type
# ---------------------------------------------------------------------------


class FlowSelectorError(Exception):
    """Raised by Flow_Selector validators and resolvers.

    Mirrors :class:`validation.ValidationError` so handlers can surface
    these via the same ``_validation_error_response`` pipeline. The
    ``error_category`` attribute defaults to ``"invalid_parameter"``
    so caller-fault errors (bad IP literals, empty selector, etc.)
    map cleanly to design Error Handling section EH-1.

    For unresolved-hostname errors (Req 19.3) callers pass
    ``error_category="hostname_unresolved"``.
    """

    def __init__(
        self,
        message: str,
        error_category: str = "invalid_parameter",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_category = error_category

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


# ---------------------------------------------------------------------------
# Strategy enumeration (Hostname_Resolution_Strategy)
# ---------------------------------------------------------------------------

# Closed enumeration from the requirements glossary. Exposed as
# constants so tests and handlers can refer to the same string set.
STRATEGY_DNS_IN_CAPTURE = "dns_in_capture"
STRATEGY_TLS_SNI_IN_CAPTURE = "tls_sni_in_capture"
STRATEGY_ACTIVE_DNS_LOOKUP = "active_dns_lookup"

_RESOLUTION_ORDER: Tuple[str, ...] = (
    STRATEGY_DNS_IN_CAPTURE,
    STRATEGY_TLS_SNI_IN_CAPTURE,
    STRATEGY_ACTIVE_DNS_LOOKUP,
)


# ---------------------------------------------------------------------------
# Field-level validation helpers
# ---------------------------------------------------------------------------


# Same alphabet as ``validate_stream_id`` in :mod:`validation`.
import re  # noqa: E402 — kept module-level so the regex compiles once.

_STREAM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# All fields the Flow_Selector may carry (per Req 19.1 / glossary).
_FLOW_SELECTOR_FIELDS = frozenset(
    {
        "source_ip",
        "source_hostname",
        "source_port",
        "destination_ip",
        "destination_hostname",
        "destination_port",
        "stream_id",
    }
)


def _validate_ip(value, field_name: str) -> str:
    """Return the value when it parses as IPv4 or IPv6, else raise."""
    if not isinstance(value, str) or not value:
        raise FlowSelectorError(
            f"flow_selector.{field_name} must be a non-empty IPv4 or "
            f"IPv6 string, got {type(value).__name__}"
        )
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise FlowSelectorError(
            f"flow_selector.{field_name} '{value}' is not a valid "
            f"IPv4 or IPv6 address ({exc})"
        ) from None
    return value


def _validate_port(value, field_name: str) -> int:
    """Return a validated port integer in 0..65535."""
    # Reject ``bool`` explicitly because ``isinstance(True, int)`` is
    # ``True`` in Python — letting ``True`` through would silently
    # become "port 1".
    if isinstance(value, bool) or not isinstance(value, int):
        raise FlowSelectorError(
            f"flow_selector.{field_name} must be an integer in 0..65535, "
            f"got {type(value).__name__}"
        )
    if value < 0 or value > 65535:
        raise FlowSelectorError(
            f"flow_selector.{field_name} must be an integer in 0..65535, "
            f"got {value}"
        )
    return value


def _validate_hostname(value, field_name: str) -> str:
    """Reject empty / non-string hostnames; otherwise return stripped value.

    We deliberately do not enforce a strict DNS label regex here. Per
    Req 19.10 the orchestration agent extracts hostnames matching
    ``[A-Za-z0-9.-]+\\.[A-Za-z]{2,}`` from chat input, so any value
    forwarded to the Network Agent is already syntactically a hostname.
    The agent's role is to attempt resolution and surface whatever the
    resolvers return.
    """
    if not isinstance(value, str):
        raise FlowSelectorError(
            f"flow_selector.{field_name} must be a non-empty string, "
            f"got {type(value).__name__}"
        )
    stripped = value.strip()
    if not stripped:
        raise FlowSelectorError(
            f"flow_selector.{field_name} must be a non-empty string"
        )
    return stripped


def _validate_stream_id(value) -> str:
    """Validate the optional ``stream_id`` field of a Flow_Selector.

    Accepts ``[A-Za-z0-9_-]{1,64}`` per Reqs 19.1 and 5.21.
    """
    if not isinstance(value, str):
        raise FlowSelectorError(
            f"flow_selector.stream_id must be a string, got "
            f"{type(value).__name__}"
        )
    if not value:
        raise FlowSelectorError(
            "flow_selector.stream_id must not be empty"
        )
    if not _STREAM_ID_PATTERN.match(value):
        raise FlowSelectorError(
            "flow_selector.stream_id must match the pattern "
            "[A-Za-z0-9_-]{1,64}"
        )
    return value


# ---------------------------------------------------------------------------
# Validated Flow_Selector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedFlowSelector:
    """The Flow_Selector after shape validation.

    Hostnames and IPs are kept as supplied (after whitespace strip);
    ports are coerced to ``int``; ``stream_id`` is preserved as-is.
    A field is ``None`` when the caller did not supply it, distinct
    from an empty string (which would have been rejected by the
    validators).
    """

    source_ip: Optional[str] = None
    source_hostname: Optional[str] = None
    source_port: Optional[int] = None
    destination_ip: Optional[str] = None
    destination_hostname: Optional[str] = None
    destination_port: Optional[int] = None
    stream_id: Optional[str] = None

    @property
    def has_source(self) -> bool:
        """True when any source_* field is set."""
        return any(
            v is not None
            for v in (self.source_ip, self.source_hostname, self.source_port)
        )

    @property
    def has_destination(self) -> bool:
        """True when any destination_* field is set."""
        return any(
            v is not None
            for v in (
                self.destination_ip,
                self.destination_hostname,
                self.destination_port,
            )
        )


def validate_flow_selector(value) -> ValidatedFlowSelector:
    """Validate a Flow_Selector dict.

    Per Req 19.1 the caller must supply at least one of the seven
    Flow_Selector fields; field combinations are AND-combined. Per
    Req 19.4 every IP must parse as IPv4/IPv6 and every port must be
    an integer in 0..65535. Hostnames are accepted as non-empty
    strings.

    Args:
        value: The raw value supplied as ``params["flow_selector"]``.

    Returns:
        :class:`ValidatedFlowSelector` with normalized field values.

    Raises:
        FlowSelectorError: If ``value`` is not a dict, contains an
            unknown key, supplies no recognized field, or any field
            fails its per-type validator.
    """
    if not isinstance(value, dict):
        raise FlowSelectorError(
            f"flow_selector must be a dict, got {type(value).__name__}"
        )

    # Reject unknown keys so a typo (e.g. ``src_ip``) surfaces a
    # validation error rather than silently producing an unscoped
    # query.
    unknown = set(value.keys()) - _FLOW_SELECTOR_FIELDS
    if unknown:
        raise FlowSelectorError(
            "flow_selector contains unknown field(s): "
            f"{', '.join(sorted(unknown))}. Allowed fields: "
            f"{', '.join(sorted(_FLOW_SELECTOR_FIELDS))}"
        )

    kwargs = {}
    if "source_ip" in value and value["source_ip"] is not None:
        kwargs["source_ip"] = _validate_ip(value["source_ip"], "source_ip")
    if "source_hostname" in value and value["source_hostname"] is not None:
        kwargs["source_hostname"] = _validate_hostname(
            value["source_hostname"], "source_hostname",
        )
    if "source_port" in value and value["source_port"] is not None:
        kwargs["source_port"] = _validate_port(
            value["source_port"], "source_port",
        )
    if "destination_ip" in value and value["destination_ip"] is not None:
        kwargs["destination_ip"] = _validate_ip(
            value["destination_ip"], "destination_ip",
        )
    if (
        "destination_hostname" in value
        and value["destination_hostname"] is not None
    ):
        kwargs["destination_hostname"] = _validate_hostname(
            value["destination_hostname"], "destination_hostname",
        )
    if (
        "destination_port" in value
        and value["destination_port"] is not None
    ):
        kwargs["destination_port"] = _validate_port(
            value["destination_port"], "destination_port",
        )
    if "stream_id" in value and value["stream_id"] is not None:
        kwargs["stream_id"] = _validate_stream_id(value["stream_id"])

    if not kwargs:
        raise FlowSelectorError(
            "flow_selector must contain at least one of: "
            f"{', '.join(sorted(_FLOW_SELECTOR_FIELDS))}"
        )

    return ValidatedFlowSelector(**kwargs)


# ---------------------------------------------------------------------------
# Resolved Flow_Selector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedTuple:
    """One resolved (ip, optional port, strategy) entry per side.

    Per Req 5.27/19.9 the response's ``metadata.resolved_flow_set``
    must report every concrete (IP, port) tuple that participated in
    the Athena predicate plus the strategy that produced each tuple.
    A hostname like ``ecr.eu-west-3.amazonaws.com`` may resolve to
    multiple IPs, all of which carry the same strategy; a literal IP
    supplied directly by the caller carries the synthetic strategy
    ``literal``.
    """

    ip: str
    port: Optional[int] = None
    strategy: str = "literal"


@dataclass(frozen=True)
class ResolvedSide:
    """Resolved IP/port set for one side (source or destination) of a flow."""

    ips: Tuple[str, ...] = ()
    port: Optional[int] = None
    tuples: Tuple[ResolvedTuple, ...] = ()
    # Strategies that returned at least one IP for this side. Order
    # matches :data:`_RESOLUTION_ORDER`.
    strategies_used: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedFlowSelector:
    """The full result of :func:`resolve_flow_selector`.

    Carries per-side resolved IP/port sets, the originally supplied
    hostnames (so the response can echo "hostname → resolved IPs"),
    the propagated ``stream_id`` (when supplied), and the optional
    ``timeout_note`` populated when the active-DNS overall budget
    was exceeded (Req 19.8).
    """

    source: ResolvedSide = field(default_factory=ResolvedSide)
    destination: ResolvedSide = field(default_factory=ResolvedSide)
    stream_id: Optional[str] = None
    source_hostname: Optional[str] = None
    destination_hostname: Optional[str] = None
    timeout_note: Optional[str] = None


# ---------------------------------------------------------------------------
# In-capture resolution strategies
# ---------------------------------------------------------------------------


def _dns_in_capture(capture_id: str, hostname: str) -> List[str]:
    """Return DNS A/AAAA answers for ``hostname`` observed in the capture.

    Athena query against ``dns_qname`` and ``dns_response_ips`` columns
    documented in the design's Pcap_Athena_Table schema. The
    ``dns_response_ips`` column is an ``array<string>`` so we ``UNNEST``
    it to get one row per IP. Both ``capture_id`` and ``hostname`` are
    interpolated as single-quoted SQL literals after the safe-alphabet
    checks below.

    Implementation note: ``capture_id`` is already validated against
    Capture_Id_Format ``[A-Za-z0-9_-]{1,128}``. Hostnames are *not*
    constrained by a safe-alphabet but DNS names cannot contain
    single quotes by definition (RFC 1035 § 2.3.1) so we escape any
    embedded apostrophes defensively before interpolation.
    """
    safe_hostname = hostname.replace("'", "''")
    sql = (  # nosec B608 — capture_id validated by Capture_Id_Format; hostname escaped above
        "SELECT DISTINCT ip "
        "FROM pcap_logs "
        "CROSS JOIN UNNEST(dns_response_ips) AS t(ip) "
        f"WHERE capture_id = '{capture_id}' "
        f"AND lower(dns_qname) = lower('{safe_hostname}') "
        "AND dns_response_ips IS NOT NULL "
        "LIMIT 100"
    )
    rows = run_athena_query(sql)
    return [_extract_ip(row.get("ip")) for row in rows if _extract_ip(row.get("ip"))]


def _tls_sni_in_capture(capture_id: str, hostname: str) -> List[str]:
    """Return destination IPs for TLS Client Hellos whose SNI matches ``hostname``.

    Aggregates across the capture so a hostname that appears in
    multiple Client Hellos to the same destination IP collapses to a
    single entry.
    """
    safe_hostname = hostname.replace("'", "''")
    sql = (  # nosec B608 — capture_id validated by Capture_Id_Format; hostname escaped above
        "SELECT DISTINCT dst_ip AS ip "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' "
        f"AND lower(tls_sni) = lower('{safe_hostname}') "
        "AND tls_handshake_type = 1 "
        "LIMIT 100"
    )
    rows = run_athena_query(sql)
    return [_extract_ip(row.get("ip")) for row in rows if _extract_ip(row.get("ip"))]


def _extract_ip(value) -> str:
    """Best-effort cast of an Athena cell value to a non-empty IP string.

    Athena cells arrive as strings via ``VarCharValue``; ``NULL`` cells
    arrive as ``None`` (see :mod:`athena_helper`). Empty strings are
    treated like NULL so a blank cell does not become a literal empty
    IP in the predicate.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()


# ---------------------------------------------------------------------------
# active_dns_lookup with per-hostname and overall budgets
# ---------------------------------------------------------------------------


# Budget constants from Req 19.8.
ACTIVE_DNS_PER_HOSTNAME_TIMEOUT_SECONDS = 5.0
ACTIVE_DNS_OVERALL_BUDGET_SECONDS = 15.0


def _default_active_dns_resolver(hostname: str) -> List[str]:
    """Resolve ``hostname`` to a list of IP strings using ``socket.getaddrinfo``.

    Returns an empty list when the hostname does not resolve. Raises
    only on truly unexpected errors; ``socket.gaierror`` (the typical
    "no such host" failure) is treated as "no IPs".

    Per Req 19.14 the runtime container must have DNS egress for this
    to succeed. The handler-side error message identifies
    ``active_dns_lookup`` so an operator can correlate a missing
    capability with this exact strategy.
    """
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        logger.info(
            "active_dns_lookup for %s returned no addresses: %s",
            hostname,
            exc,
        )
        return []
    seen: List[str] = []
    seen_set: set = set()
    for entry in results:
        # ``getaddrinfo`` returns 5-tuples; index 4 is ``sockaddr`` and
        # ``sockaddr[0]`` is the IP literal. We deduplicate while
        # preserving insertion order so the resolved tuple list is
        # deterministic across calls.
        try:
            ip = entry[4][0]
        except (IndexError, TypeError):
            continue
        if not ip or ip in seen_set:
            continue
        seen_set.add(ip)
        seen.append(ip)
    return seen


# Module-level resolver indirection so tests can monkey-patch
# :data:`_ACTIVE_DNS_RESOLVER` to a deterministic fake without having
# to patch ``socket.getaddrinfo`` globally. Production code uses
# :func:`_default_active_dns_resolver`.
_ACTIVE_DNS_RESOLVER = _default_active_dns_resolver


def _active_dns_lookup(
    hostname: str,
    *,
    deadline_monotonic: float,
) -> Tuple[List[str], bool]:
    """Resolve ``hostname`` via :data:`_ACTIVE_DNS_RESOLVER`, honouring budgets.

    Per Req 19.8: per-hostname timeout 5 seconds, overall budget shared
    across all hostnames in the request 15 seconds. Returns the IPs
    collected (possibly empty) and a boolean indicating whether the
    overall budget was exhausted before the resolver returned.

    The per-hostname 5-second timeout is enforced via
    ``socket.setdefaulttimeout`` because ``getaddrinfo`` does not
    support a per-call timeout in Python's stdlib. Tests inject a fake
    resolver and therefore bypass the timeout machinery; we only set
    it when the production resolver is in use.

    Args:
        hostname: The hostname to resolve.
        deadline_monotonic: Absolute :func:`time.monotonic` value at
            which the overall 15 second budget expires.

    Returns:
        Tuple ``(ips, budget_exhausted)``. ``budget_exhausted`` is
        ``True`` when the call returned because the overall budget
        elapsed (the resolver may still have returned IPs before
        timing out — those are included).
    """
    remaining_overall = max(0.0, deadline_monotonic - time.monotonic())
    if remaining_overall <= 0:
        return [], True

    per_hostname_budget = min(
        ACTIVE_DNS_PER_HOSTNAME_TIMEOUT_SECONDS, remaining_overall,
    )

    # Apply the timeout only when using the production resolver. Tests
    # supply deterministic fakes that don't touch the network so we
    # leave their timeout semantics untouched.
    using_default = _ACTIVE_DNS_RESOLVER is _default_active_dns_resolver
    saved_timeout = socket.getdefaulttimeout() if using_default else None
    try:
        if using_default:
            socket.setdefaulttimeout(per_hostname_budget)
        start = time.monotonic()
        ips = _ACTIVE_DNS_RESOLVER(hostname)
        elapsed = time.monotonic() - start
    finally:
        if using_default:
            socket.setdefaulttimeout(saved_timeout)

    budget_exhausted = elapsed >= remaining_overall or remaining_overall <= 0
    return list(ips), budget_exhausted


# ---------------------------------------------------------------------------
# resolve_flow_selector
# ---------------------------------------------------------------------------


def _resolve_hostname_combined(
    capture_id: str,
    hostname: str,
    *,
    active_dns_deadline: float,
) -> Tuple[List[Tuple[str, str]], bool]:
    """Apply the ``combined`` Hostname_Resolution_Strategy.

    Per Req 19.2: try ``dns_in_capture`` first, then
    ``tls_sni_in_capture``, then ``active_dns_lookup``; union all
    returned IPs. Per the task notes the strategies run in order
    regardless of whether earlier ones returned IPs, because each
    strategy may surface IPs the others missed (a Client Hello whose
    DNS lookup happened before the capture started, for example).

    Returns:
        Tuple ``(tuples, active_dns_timed_out)`` where ``tuples`` is a
        list of ``(ip, strategy)`` pairs in resolution order and
        ``active_dns_timed_out`` is ``True`` when the active DNS
        overall budget was exhausted during this hostname's lookup.
    """
    seen: set = set()
    out: List[Tuple[str, str]] = []
    active_dns_timed_out = False

    # 1. dns_in_capture
    try:
        for ip in _dns_in_capture(capture_id, hostname):
            if ip and ip not in seen:
                seen.add(ip)
                out.append((ip, STRATEGY_DNS_IN_CAPTURE))
    except (AthenaQueryError, AthenaConfigurationError) as exc:
        # In-capture strategies never block the request: log and
        # continue to the next strategy. Active DNS is the safety
        # net, and Req 19.3 only triggers when *all three* strategies
        # return zero IPs.
        logger.info(
            "dns_in_capture failed for hostname %s: %s",
            hostname,
            exc,
        )

    # 2. tls_sni_in_capture
    try:
        for ip in _tls_sni_in_capture(capture_id, hostname):
            if ip and ip not in seen:
                seen.add(ip)
                out.append((ip, STRATEGY_TLS_SNI_IN_CAPTURE))
    except (AthenaQueryError, AthenaConfigurationError) as exc:
        logger.info(
            "tls_sni_in_capture failed for hostname %s: %s",
            hostname,
            exc,
        )

    # 3. active_dns_lookup
    ips, budget_exhausted = _active_dns_lookup(
        hostname, deadline_monotonic=active_dns_deadline,
    )
    if budget_exhausted:
        active_dns_timed_out = True
    for ip in ips:
        if ip and ip not in seen:
            seen.add(ip)
            out.append((ip, STRATEGY_ACTIVE_DNS_LOOKUP))

    return out, active_dns_timed_out


def _resolve_side(
    capture_id: str,
    *,
    literal_ip: Optional[str],
    hostname: Optional[str],
    port: Optional[int],
    active_dns_deadline: float,
) -> Tuple[ResolvedSide, bool, List[str]]:
    """Resolve one side (source or destination) into a :class:`ResolvedSide`.

    Returns a triple ``(side, active_dns_timed_out, strategies_attempted)``.
    ``strategies_attempted`` is the list of strategies that ran for the
    hostname (or empty when only a literal IP was supplied) so the
    caller can include them in the Req 19.3 error message when no IPs
    were resolved.
    """
    tuples: List[ResolvedTuple] = []
    strategies_used: List[str] = []
    active_dns_timed_out = False
    strategies_attempted: List[str] = []

    if literal_ip is not None:
        tuples.append(ResolvedTuple(ip=literal_ip, port=port, strategy="literal"))

    if hostname is not None:
        strategies_attempted = list(_RESOLUTION_ORDER)
        resolved, timed_out = _resolve_hostname_combined(
            capture_id, hostname, active_dns_deadline=active_dns_deadline,
        )
        active_dns_timed_out = timed_out

        # Track which strategies returned at least one IP so we can
        # surface that information in the response metadata.
        contributed: set = set()
        for ip, strategy in resolved:
            tuples.append(ResolvedTuple(ip=ip, port=port, strategy=strategy))
            contributed.add(strategy)
        # Preserve resolution order for the strategies_used list.
        for strategy in _RESOLUTION_ORDER:
            if strategy in contributed:
                strategies_used.append(strategy)

    if not tuples and port is not None:
        # Caller supplied only a port for this side. The predicate
        # builder still needs to know about it (port-only constraint),
        # so we represent it as a single ``ResolvedTuple`` with no IP.
        # This is rare in practice — the orchestration agent always
        # supplies an IP/hostname when it constructs a Flow_Selector
        # — but it's a documented field combination per Req 19.1.
        tuples.append(ResolvedTuple(ip="", port=port, strategy="literal"))

    # Deduplicate IPs while preserving insertion order so the predicate
    # IN-list and the metadata are deterministic.
    ip_seen: set = set()
    ips: List[str] = []
    for t in tuples:
        if t.ip and t.ip not in ip_seen:
            ip_seen.add(t.ip)
            ips.append(t.ip)

    return (
        ResolvedSide(
            ips=tuple(ips),
            port=port,
            tuples=tuple(tuples),
            strategies_used=tuple(strategies_used),
        ),
        active_dns_timed_out,
        strategies_attempted,
    )


def resolve_flow_selector(
    capture_id: str,
    flow_selector,
) -> ResolvedFlowSelector:
    """Resolve a Flow_Selector to concrete IPs/ports for predicate construction.

    Implements Reqs 19.1-19.4, 19.8, and 5.24-5.27. Validation is
    delegated to :func:`validate_flow_selector` so this function is
    safe to call directly with the raw caller-supplied dict.

    Workflow:
      * Validate the selector (raise on bad shape).
      * For each side that carries a hostname, run the ``combined``
        Hostname_Resolution_Strategy under a 15 second overall budget
        for the active-DNS step (Req 19.8).
      * Verify that every supplied hostname resolved to at least one
        IP across the three strategies. Empty resolutions raise
        :class:`FlowSelectorError` with
        ``error_category="hostname_unresolved"`` so handlers can
        surface a "did not resolve" envelope per Req 19.3.

    Args:
        capture_id: The capture identifier the queries are scoped to.
            Already validated against Capture_Id_Format by the
            handler.
        flow_selector: Raw dict from the caller.

    Returns:
        :class:`ResolvedFlowSelector` with resolved per-side IP sets,
        propagated ``stream_id`` (or ``None``), and any timeout note.

    Raises:
        FlowSelectorError: On invalid shape, IP, port, or stream_id;
            on a hostname that resolves to zero IPs across all three
            strategies (``error_category="hostname_unresolved"``).
    """
    selector = validate_flow_selector(flow_selector)

    overall_deadline = (
        time.monotonic() + ACTIVE_DNS_OVERALL_BUDGET_SECONDS
    )
    timeout_note: Optional[str] = None

    source_side, src_timed_out, src_strategies = _resolve_side(
        capture_id,
        literal_ip=selector.source_ip,
        hostname=selector.source_hostname,
        port=selector.source_port,
        active_dns_deadline=overall_deadline,
    )
    dst_side, dst_timed_out, dst_strategies = _resolve_side(
        capture_id,
        literal_ip=selector.destination_ip,
        hostname=selector.destination_hostname,
        port=selector.destination_port,
        active_dns_deadline=overall_deadline,
    )

    # Req 19.3: a supplied hostname that resolves to zero IPs must
    # reject the request.
    if selector.source_hostname is not None and not source_side.ips:
        raise FlowSelectorError(
            "flow_selector.source_hostname "
            f"'{selector.source_hostname}' did not resolve to any IPs "
            f"after attempting strategies: {', '.join(src_strategies)}",
            error_category="hostname_unresolved",
        )
    if selector.destination_hostname is not None and not dst_side.ips:
        raise FlowSelectorError(
            "flow_selector.destination_hostname "
            f"'{selector.destination_hostname}' did not resolve to any "
            f"IPs after attempting strategies: "
            f"{', '.join(dst_strategies)}",
            error_category="hostname_unresolved",
        )

    if src_timed_out or dst_timed_out:
        timeout_note = (
            "active_dns_lookup overall budget of "
            f"{ACTIVE_DNS_OVERALL_BUDGET_SECONDS:.0f}s exceeded; some "
            "hostnames may have resolved with fewer IPs than expected."
        )

    return ResolvedFlowSelector(
        source=source_side,
        destination=dst_side,
        stream_id=selector.stream_id,
        source_hostname=selector.source_hostname,
        destination_hostname=selector.destination_hostname,
        timeout_note=timeout_note,
    )


# ---------------------------------------------------------------------------
# Predicate construction
# ---------------------------------------------------------------------------


def _quote_ip_list(ips: Iterable[str]) -> str:
    """Return a SQL ``(...)`` IN-clause body of single-quoted IP literals.

    IPs have already been validated by :func:`_validate_ip` (literal
    sources) or returned by Athena / ``socket.getaddrinfo`` (resolved
    sources). They cannot contain single quotes by construction so
    direct interpolation is provably injection-free.
    """
    quoted = ", ".join(f"'{ip}'" for ip in ips)
    return f"({quoted})"


def _ip_predicate(column: str, ips: Tuple[str, ...]) -> str:
    """Build ``column IN ('1.2.3.4', ...)`` or ``column = '1.2.3.4'``."""
    if len(ips) == 1:
        return f"{column} = '{ips[0]}'"
    return f"{column} IN {_quote_ip_list(ips)}"


def _side_predicates(
    side: ResolvedSide,
    *,
    ip_column: str,
    port_column: str,
) -> List[str]:
    """Build the AND-joined predicates for one side mapped to a fixed direction."""
    out: List[str] = []
    if side.ips:
        out.append(_ip_predicate(ip_column, side.ips))
    if side.port is not None:
        out.append(f"{port_column} = {side.port}")
    return out


def build_flow_predicate(resolved: ResolvedFlowSelector) -> str:
    """Build the Athena ``AND``-joined predicate fragment for a resolved flow.

    Implements Reqs 19.1, 19.6, 19.7 plus the ``stream_id`` AND-combine
    rule from Req 5.25. The returned fragment is intended to be
    appended to an existing ``WHERE capture_id = '...'`` clause as
    ``... AND <fragment>``. Returns an empty string when the resolved
    selector contains no constraints (defensive — the caller should
    not invoke this when the selector is empty).

    Direction logic:
      * Both source_* and destination_* present (Req 19.1): apply
        source constraints to the source side and destination to the
        destination side, AND-combined.
      * Only source_* present (Req 19.6): match flows where the
        constraint holds for **either** direction. We achieve this by
        OR-combining a "source matches src_*" predicate with a
        "source matches dst_*" predicate (i.e. the supplied source
        could be the responder of the flow).
      * Only destination_* present (Req 19.7): match flows where the
        constraint holds for the **responder side only**. Per the
        requirement we restrict to the responder by AND-combining
        ``dst_ip`` / ``dst_port`` with the supplied IPs/port.

    The ``stream_id`` field is AND-combined with whatever direction
    logic produced.
    """
    fragments: List[str] = []

    has_source = bool(resolved.source.ips) or resolved.source.port is not None
    has_destination = (
        bool(resolved.destination.ips) or resolved.destination.port is not None
    )

    if has_source and has_destination:
        # Both sides supplied → strict per-direction match (Req 19.1).
        src_terms = _side_predicates(
            resolved.source, ip_column="src_ip", port_column="src_port",
        )
        dst_terms = _side_predicates(
            resolved.destination, ip_column="dst_ip", port_column="dst_port",
        )
        fragments.extend(src_terms)
        fragments.extend(dst_terms)
    elif has_source:
        # Source-only → either-direction match (Req 19.6).
        as_source = _side_predicates(
            resolved.source, ip_column="src_ip", port_column="src_port",
        )
        as_destination = _side_predicates(
            resolved.source, ip_column="dst_ip", port_column="dst_port",
        )
        if as_source and as_destination:
            fragments.append(
                f"(({' AND '.join(as_source)}) "
                f"OR ({' AND '.join(as_destination)}))"
            )
        elif as_source:
            fragments.extend(as_source)
        elif as_destination:
            fragments.extend(as_destination)
    elif has_destination:
        # Destination-only → responder side only (Req 19.7).
        dst_terms = _side_predicates(
            resolved.destination, ip_column="dst_ip", port_column="dst_port",
        )
        fragments.extend(dst_terms)

    if resolved.stream_id is not None:
        # Req 5.25: AND-combine stream_id with any other constraints.
        fragments.append(f"tcp_stream = '{resolved.stream_id}'")

    return " AND ".join(fragments)


# ---------------------------------------------------------------------------
# Response metadata helpers
# ---------------------------------------------------------------------------


def _tuple_to_dict(t: ResolvedTuple) -> dict:
    """Render a :class:`ResolvedTuple` for inclusion in JSON metadata."""
    return {
        "ip": t.ip,
        "port": t.port,
        "strategy": t.strategy,
    }


def build_resolved_flow_set_metadata(
    resolved: ResolvedFlowSelector,
) -> dict:
    """Build ``metadata.resolved_flow_set`` (Reqs 5.27 / 19.9).

    Returns a dict with:
      * ``source``: list of resolved IP/port/strategy entries used for
        source matching (empty when no source_* fields supplied).
      * ``destination``: same for destination matching.
      * ``source_hostname`` / ``destination_hostname``: echoed for
        user-facing verification (None when not supplied).
      * ``stream_id``: echoed when supplied.
      * ``timeout_note``: present and non-null when the active-DNS
        budget was exceeded (Req 19.8).
    """
    payload: dict = {
        "source": [_tuple_to_dict(t) for t in resolved.source.tuples],
        "destination": [_tuple_to_dict(t) for t in resolved.destination.tuples],
    }
    if resolved.source_hostname is not None:
        payload["source_hostname"] = resolved.source_hostname
    if resolved.destination_hostname is not None:
        payload["destination_hostname"] = resolved.destination_hostname
    if resolved.stream_id is not None:
        payload["stream_id"] = resolved.stream_id
    if resolved.timeout_note is not None:
        payload["timeout_note"] = resolved.timeout_note
    return payload


def query_matched_streams(
    capture_id: str,
    predicate: str,
) -> Tuple[int, List[dict]]:
    """Run a per-stream aggregate to populate ``matched_streams`` metadata (Req 19.5).

    Builds a small Athena query that groups rows matching the
    Flow_Selector predicate by ``tcp_stream``. The per-stream
    ``client_ip`` / ``client_port`` / ``server_ip`` / ``server_port``
    are derived from the SYN sender (the initiator) computed via
    ``MIN_BY`` window aggregation, so the response describes the
    flow's logical endpoints rather than the per-frame source/dest
    which alternate by direction.

    Args:
        capture_id: Validated capture identifier.
        predicate: Predicate fragment from
            :func:`build_flow_predicate`. Must be non-empty.

    Returns:
        ``(count, streams)`` where ``streams`` is a list of dicts with
        the keys mandated by Req 19.5: ``stream_id``, ``client_ip``,
        ``client_port``, ``server_ip``, ``server_port``,
        ``packet_count``. Returns ``(0, [])`` on any Athena error so
        the calling handler can degrade gracefully — the
        ``resolved_flow_set`` metadata still surfaces the IPs that
        were used.
    """
    if not predicate:
        return 0, []

    sql = (  # nosec B608 — capture_id validated by Capture_Id_Format; predicate built by build_flow_predicate
        "WITH matched AS ("
        "SELECT tcp_stream, src_ip, src_port, dst_ip, dst_port, frame_time, tcp_flags "
        "FROM pcap_logs "
        f"WHERE capture_id = '{capture_id}' AND {predicate}"
        "), "
        "syn_packets AS ("
        "SELECT tcp_stream, src_ip AS initiator_ip, src_port AS initiator_port, "
        "dst_ip AS responder_ip, dst_port AS responder_port, "
        "ROW_NUMBER() OVER (PARTITION BY tcp_stream ORDER BY frame_time) AS rn "
        "FROM matched "
        "WHERE tcp_flags IS NOT NULL AND "
        "bitwise_and(from_base(replace(tcp_flags, '0x', ''), 16), 18) = 2"
        ") "
        "SELECT m.tcp_stream AS stream_id, "
        "COALESCE(s.initiator_ip, MIN(m.src_ip)) AS client_ip, "
        "COALESCE(s.initiator_port, CAST(MIN(m.src_port) AS INTEGER)) AS client_port, "
        "COALESCE(s.responder_ip, MIN(m.dst_ip)) AS server_ip, "
        "COALESCE(s.responder_port, CAST(MIN(m.dst_port) AS INTEGER)) AS server_port, "
        "COUNT(*) AS packet_count "
        "FROM matched m "
        "LEFT JOIN syn_packets s ON m.tcp_stream = s.tcp_stream AND s.rn = 1 "
        "GROUP BY m.tcp_stream, s.initiator_ip, s.initiator_port, s.responder_ip, s.responder_port "
        "ORDER BY packet_count DESC, stream_id "
        "LIMIT 100"
    )

    try:
        rows = run_athena_query(sql)
    except (AthenaQueryError, AthenaConfigurationError) as exc:
        # Best-effort: the main query has already produced the user's
        # result. Emitting matched_stream_count=0 with a logged
        # warning is preferable to failing the whole request because
        # the secondary query couldn't run.
        logger.warning(
            "matched_streams query failed for capture %s: %s",
            capture_id,
            exc,
        )
        return 0, []

    streams: List[dict] = []
    for row in rows:
        streams.append(
            {
                "stream_id": row.get("stream_id"),
                "client_ip": row.get("client_ip"),
                "client_port": _safe_int(row.get("client_port")),
                "server_ip": row.get("server_ip"),
                "server_port": _safe_int(row.get("server_port")),
                "packet_count": _safe_int(row.get("packet_count")) or 0,
            }
        )
    return len(streams), streams


def _safe_int(value):
    """Convert an Athena cell to ``int`` when possible, else return ``None``.

    Athena returns numeric values as strings under ``VarCharValue``;
    ``None`` cells stay ``None``. Empty strings are mapped to ``None``
    so a blank port column does not become ``0``.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


__all__ = [
    "FlowSelectorError",
    "STRATEGY_DNS_IN_CAPTURE",
    "STRATEGY_TLS_SNI_IN_CAPTURE",
    "STRATEGY_ACTIVE_DNS_LOOKUP",
    "ACTIVE_DNS_PER_HOSTNAME_TIMEOUT_SECONDS",
    "ACTIVE_DNS_OVERALL_BUDGET_SECONDS",
    "ValidatedFlowSelector",
    "ResolvedTuple",
    "ResolvedSide",
    "ResolvedFlowSelector",
    "validate_flow_selector",
    "resolve_flow_selector",
    "build_flow_predicate",
    "build_resolved_flow_set_metadata",
    "query_matched_streams",
]
