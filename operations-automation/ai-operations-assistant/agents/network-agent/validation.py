"""
Validation helpers for G.O.A.T. Network Agent capture-parameter inputs.

Implements Task 4 of the goat-network-agent spec: a typed ``ValidationError``
class plus seven validator functions that enforce the parameter shapes
documented in the requirements:

- ``validate_capture_id``  — Capture_Id_Format ``[A-Za-z0-9_-]{1,128}``
  (Reqs 3.4, 5.20, 6.10).
- ``validate_eni_ids``     — list of 1-3 distinct EC2 ENI identifiers
  matching ``^eni-[0-9a-f]{8,17}$`` (Reqs 3.2, 4.3, 4.4).
- ``validate_duration_minutes`` — integer in ``[1, 60]`` (Reqs 4.1, 4.2).
- ``validate_filter_id``   — string of 1-128 characters (Req 3.2).
- ``validate_idempotency_token`` — string of 1-256 characters when present
  (Req 3.15).
- ``validate_stream_id``   — ``[A-Za-z0-9_-]{1,64}`` (Req 5.21).
- ``validate_status_filter`` — value against a caller-supplied accepted set
  (Reqs 3.10, 3.11).

Every validator raises :class:`ValidationError` with
``error_category="invalid_parameter"`` and a human-readable message on
failure. Handlers catch ``ValidationError`` and convert it to the
response envelope by setting ``metadata.errorCategory =
"invalid_parameter"`` (see design Error Handling section EH-1).
"""

from __future__ import annotations

import re
from typing import Iterable, List


# ---------------------------------------------------------------------------
# Typed validation error
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    """Raised by validators when a caller-supplied value fails a shape check.

    Attributes:
        error_category: An ``errorCategory`` token from the design's EH-1
            table. Validators in this module always set this to
            ``"invalid_parameter"``; the attribute is a constructor
            argument so future shape errors with a different category
            (for example ``"invalid_sql"``) can reuse the same class.
        message: A human-readable explanation suitable for inclusion in
            the response envelope's ``error`` field.
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
# Compiled patterns
#
# Compiled at import time so each validator call is just a regex match.
# ---------------------------------------------------------------------------

# Capture_Id_Format (glossary in requirements.md): a non-empty string of
# 1 to 128 characters drawn from the character set [A-Za-z0-9_-].
_CAPTURE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Stream identifier (Reqs 5.21, 19.4): same character class as
# Capture_Id_Format but capped at 64 characters.
_STREAM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# EC2 ENI identifier — AWS standard format: ``eni-`` followed by 8 to 17
# lowercase hex characters. The 8-character form is the legacy short
# identifier; AWS now issues 17-character identifiers by default but
# both remain valid in account responses.
_ENI_ID_PATTERN = re.compile(r"^eni-[0-9a-f]{8,17}$")

# Hard cap on the number of ENIs a single Capture_Session may mirror
# (Capture_Eni_Limit, Req 4.3).
_CAPTURE_ENI_LIMIT = 3


# ---------------------------------------------------------------------------
# Helper guards
# ---------------------------------------------------------------------------


def _ensure_string(value, field_name: str) -> str:
    """Reject non-string inputs with a uniform error message."""
    # ``bool`` is a subclass of ``int`` but never a ``str`` so we don't
    # need a special case here; Python's ``isinstance(True, str)`` is
    # already ``False``.
    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    return value


def _ensure_int(value, field_name: str) -> int:
    """Reject non-integer inputs (including ``bool``) with a uniform error."""
    # In Python, ``bool`` is a subclass of ``int`` (``True == 1``,
    # ``False == 0``). For a numeric range guard we want to reject
    # ``True`` / ``False`` explicitly since they are clearly not the
    # caller's intent for ``duration_minutes``.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    return value


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_capture_id(value) -> str:
    """Validate a ``capture_id`` against Capture_Id_Format.

    Pattern: ``^[A-Za-z0-9_-]{1,128}$`` (Req 3.4, Req 5.20, Req 6.10).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated ``capture_id`` string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, empty,
            longer than 128 characters, or contains characters outside
            ``[A-Za-z0-9_-]``.
    """
    if value is None:
        raise ValidationError("capture_id is required")

    s = _ensure_string(value, "capture_id")

    if not s:
        raise ValidationError("capture_id must not be empty")

    if len(s) > 128:
        raise ValidationError(
            f"capture_id must be 1-128 characters, got {len(s)}"
        )

    if not _CAPTURE_ID_PATTERN.match(s):
        raise ValidationError(
            "capture_id must match the pattern [A-Za-z0-9_-]{1,128}"
        )

    return s


def validate_eni_ids(value) -> List[str]:
    """Validate the ``eni_ids`` list supplied to ``start_capture``.

    Enforces:
      * a non-empty list (Req 4.4),
      * length at most ``Capture_Eni_Limit`` (3) (Req 4.3),
      * each element matching the AWS ENI identifier pattern
        ``^eni-[0-9a-f]{8,17}$`` (Req 3.1),
      * no duplicate identifiers across the list (Req 4.4).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        A new list containing the validated ENI identifiers in the
        order the caller supplied them.

    Raises:
        ValidationError: If the value is missing, not a list, empty,
            larger than ``Capture_Eni_Limit``, contains an element that
            is not a string, contains an element that does not match
            the AWS ENI identifier pattern, or contains duplicates.
    """
    if value is None:
        raise ValidationError("eni_ids is required")

    if not isinstance(value, list):
        raise ValidationError(
            f"eni_ids must be a list, got {type(value).__name__}"
        )

    if len(value) == 0:
        raise ValidationError("eni_ids must contain at least one ENI identifier")

    if len(value) > _CAPTURE_ENI_LIMIT:
        raise ValidationError(
            f"eni_ids must contain at most {_CAPTURE_ENI_LIMIT} entries "
            f"(Capture_Eni_Limit), got {len(value)}"
        )

    seen = set()
    validated: List[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, str):
            raise ValidationError(
                f"eni_ids[{index}] must be a string, got "
                f"{type(raw).__name__}"
            )
        if not _ENI_ID_PATTERN.match(raw):
            raise ValidationError(
                f"eni_ids[{index}] '{raw}' is not a valid ENI identifier "
                "(must match ^eni-[0-9a-f]{8,17}$)"
            )
        if raw in seen:
            raise ValidationError(
                f"eni_ids contains duplicate identifier '{raw}'"
            )
        seen.add(raw)
        validated.append(raw)

    return validated


def validate_duration_minutes(value) -> int:
    """Validate ``duration_minutes`` against the Capture_Duration_Limit.

    Required range: integer in ``[1, 60]`` (Reqs 4.1, 4.2).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated integer.

    Raises:
        ValidationError: If ``value`` is missing, is a ``bool``, is not
            an ``int``, or is outside the inclusive range 1..60.
    """
    if value is None:
        raise ValidationError("duration_minutes is required")

    n = _ensure_int(value, "duration_minutes")

    if n < 1 or n > 60:
        raise ValidationError(
            f"duration_minutes must be an integer in 1..60, got {n}"
        )

    return n


def validate_filter_id(value) -> str:
    """Validate a ``filter_id`` against the 1-128 character constraint.

    The Network Agent does not enforce a character-class restriction on
    ``filter_id`` (Req 3.1 documents only the length range), so this
    validator accepts any non-empty string up to 128 characters.

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated string.

    Raises:
        ValidationError: If ``value`` is missing, not a string, empty,
            or longer than 128 characters.
    """
    if value is None:
        raise ValidationError("filter_id is required")

    s = _ensure_string(value, "filter_id")

    if not s:
        raise ValidationError("filter_id must not be empty")

    if len(s) > 128:
        raise ValidationError(
            f"filter_id must be 1-128 characters, got {len(s)}"
        )

    return s


def validate_idempotency_token(value) -> str:
    """Validate an optional ``idempotency_token`` (Req 3.15).

    The token is *optional*; callers should only invoke this function
    when the parameter is supplied. The validator enforces a 1-256
    character string.

    Args:
        value: The raw value supplied by the caller. Must not be ``None``
            — unsupplied tokens should be skipped by the caller.

    Returns:
        The validated string.

    Raises:
        ValidationError: If ``value`` is missing, not a string, empty,
            or longer than 256 characters.
    """
    if value is None:
        raise ValidationError("idempotency_token is required when supplied")

    s = _ensure_string(value, "idempotency_token")

    if not s:
        raise ValidationError("idempotency_token must not be empty")

    if len(s) > 256:
        raise ValidationError(
            f"idempotency_token must be 1-256 characters, got {len(s)}"
        )

    return s


def validate_stream_id(value) -> str:
    """Validate a ``stream_id`` against ``[A-Za-z0-9_-]{1,64}`` (Req 5.21).

    Args:
        value: The raw value supplied by the caller.

    Returns:
        The validated string (unchanged).

    Raises:
        ValidationError: If ``value`` is missing, not a string, empty,
            longer than 64 characters, or contains characters outside
            ``[A-Za-z0-9_-]``.
    """
    if value is None:
        raise ValidationError("stream_id is required")

    s = _ensure_string(value, "stream_id")

    if not s:
        raise ValidationError("stream_id must not be empty")

    if len(s) > 64:
        raise ValidationError(
            f"stream_id must be 1-64 characters, got {len(s)}"
        )

    if not _STREAM_ID_PATTERN.match(s):
        raise ValidationError(
            "stream_id must match the pattern [A-Za-z0-9_-]{1,64}"
        )

    return s


def validate_min_size(value) -> int:
    """Validate the optional ``min_size`` parameter for ``search_fragmented_packets`` (Req 5.4).

    Required range when supplied: integer in ``[64, 65535]`` (Req 5.4 —
    "optional ``min_size`` (integer in the range 64 to 65535)"). The
    value is *optional*; callers should only invoke this function when
    the parameter is supplied.

    The lower bound of 64 mirrors the minimum Ethernet frame size
    (after the preamble); the upper bound of 65535 mirrors the IPv4
    total-length field maximum. Together they bound ``min_size`` to
    physically plausible IP packet sizes, so a caller cannot ask for
    "frames larger than 1 byte" (defeating the action) or "frames
    larger than 1 GB" (a typo that would silently match nothing).

    Args:
        value: The raw value supplied by the caller. Must not be
            ``None`` — unsupplied values should be skipped by the
            caller and replaced with the default of 1400 (Req 5.5).

    Returns:
        The validated integer.

    Raises:
        ValidationError: If ``value`` is missing, is a ``bool``, is
            not an ``int``, or is outside the inclusive range
            ``[64, 65535]``.
    """
    if value is None:
        raise ValidationError("min_size is required when supplied")

    n = _ensure_int(value, "min_size")

    if n < 64 or n > 65535:
        raise ValidationError(
            f"min_size must be an integer in 64..65535, got {n}"
        )

    return n


def validate_top_n(value) -> int:
    """Validate the optional ``top_n`` parameter for ``get_conversation_stats`` (Req 5.10).

    Required range when supplied: integer in ``[1, 1000]`` (Req 5.10 —
    "optional ``top_n`` (integer in the range 1 to 1000)"). The value
    is *optional*; callers should only invoke this function when the
    parameter is supplied.

    Args:
        value: The raw value supplied by the caller. Must not be
            ``None`` — unsupplied values should be skipped by the
            caller and replaced with the default of 20 (Req 5.11).

    Returns:
        The validated integer.

    Raises:
        ValidationError: If ``value`` is missing, is a ``bool``, is
            not an ``int``, or is outside the inclusive range
            ``[1, 1000]``.
    """
    if value is None:
        raise ValidationError("top_n is required when supplied")

    n = _ensure_int(value, "top_n")

    if n < 1 or n > 1000:
        raise ValidationError(
            f"top_n must be an integer in 1..1000, got {n}"
        )

    return n


def validate_status_filter(value, accepted_set: Iterable[str]) -> str:
    """Validate a ``status`` filter for ``list_captures`` (Reqs 3.10, 3.11).

    The ``list_captures`` action accepts a closed set of status values
    (typically ``{"all", "active", "historical"}``); other actions may
    reuse this validator with their own accepted sets.

    Args:
        value: The raw value supplied by the caller.
        accepted_set: An iterable of accepted string values. The
            validator materializes this into a frozenset so the caller
            may pass any iterable (list, set, tuple).

    Returns:
        The validated string.

    Raises:
        ValidationError: If ``accepted_set`` is empty or ``value`` is
            missing, not a string, or not present in ``accepted_set``.
    """
    accepted = frozenset(accepted_set)
    if not accepted:
        # Defensive guard so a misconfigured handler can never accept
        # an arbitrary value silently.
        raise ValidationError(
            "status accepted_set must contain at least one value"
        )

    if value is None:
        raise ValidationError("status is required")

    s = _ensure_string(value, "status")

    if s not in accepted:
        accepted_clause = ", ".join(sorted(accepted))
        raise ValidationError(
            f"status must be one of {accepted_clause}, got {s!r}"
        )

    return s


__all__ = [
    "ValidationError",
    "validate_capture_id",
    "validate_eni_ids",
    "validate_duration_minutes",
    "validate_filter_id",
    "validate_idempotency_token",
    "validate_min_size",
    "validate_stream_id",
    "validate_status_filter",
    "validate_top_n",
]
