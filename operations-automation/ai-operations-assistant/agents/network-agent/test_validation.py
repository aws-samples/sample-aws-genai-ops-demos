"""
Unit and property-based tests for ``validation.py``.

Run from the ``network-agent`` directory:

    python -m pytest test_validation.py -v

The unit tests assert that each validator accepts a known-good value
and rejects each documented failure mode with an
``invalid_parameter``-categorized :class:`validation.ValidationError`.

The property tests use Hypothesis to assert universal properties:

- The capture_id, stream_id, and ENI character-class regexes accept
  every string drawn from the documented character classes.
- Length-bounded validators reject every string strictly longer than
  the bound and accept every length within the bound.
- ``duration_minutes`` accepts every integer in 1..60 and rejects
  every integer outside that range.
- ``validate_eni_ids`` rejects every list with a duplicate, with
  length 0, or with length above ``Capture_Eni_Limit`` (3).

Each property test is annotated with the requirement(s) it validates.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given, strategies as st, assume, settings

from validation import (
    ValidationError,
    validate_capture_id,
    validate_duration_minutes,
    validate_eni_ids,
    validate_filter_id,
    validate_idempotency_token,
    validate_status_filter,
    validate_stream_id,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Character class shared by Capture_Id_Format and stream_id format.
_CAPTURE_ID_ALPHABET = string.ascii_letters + string.digits + "_-"

# Single hex character set used to build ENI identifiers.
_HEX_ALPHABET = "0123456789abcdef"


def _capture_id_strings(min_len: int = 1, max_len: int = 128):
    """Strings drawn entirely from the Capture_Id_Format character class."""
    return st.text(
        alphabet=_CAPTURE_ID_ALPHABET,
        min_size=min_len,
        max_size=max_len,
    )


def _stream_id_strings(min_len: int = 1, max_len: int = 64):
    """Strings drawn from the stream_id character class."""
    return st.text(
        alphabet=_CAPTURE_ID_ALPHABET,
        min_size=min_len,
        max_size=max_len,
    )


def _eni_strings():
    """Strings matching the AWS ENI identifier pattern."""
    return st.integers(min_value=8, max_value=17).flatmap(
        lambda n: st.text(alphabet=_HEX_ALPHABET, min_size=n, max_size=n).map(
            lambda hex_part: f"eni-{hex_part}"
        )
    )


# ---------------------------------------------------------------------------
# validate_capture_id  (Reqs 3.4, 5.20, 6.10)
# ---------------------------------------------------------------------------


class TestValidateCaptureId:
    """Unit tests for validate_capture_id."""

    @pytest.mark.parametrize(
        "good",
        [
            "a",
            "abc-123_DEF",
            "0",
            "_",
            "-",
            "A" * 128,
            "Capture_2026-04-20T12-34-56_abc",
        ],
    )
    def test_accepts_good_values(self, good):
        assert validate_capture_id(good) == good

    @pytest.mark.parametrize(
        "bad,reason",
        [
            (None, "missing"),
            ("", "empty"),
            ("a" * 129, "too long"),
            ("bad spaces", "space character"),
            ("bad/slash", "slash"),
            ("bad.dot", "dot"),
            ("bad\nnewline", "newline"),
            (123, "non-string"),
            ([], "non-string list"),
        ],
    )
    def test_rejects_bad_values(self, bad, reason):
        with pytest.raises(ValidationError) as exc_info:
            validate_capture_id(bad)
        assert exc_info.value.error_category == "invalid_parameter"
        assert exc_info.value.message  # non-empty

    @given(_capture_id_strings())
    @settings(max_examples=200)
    def test_property_accepts_all_in_class_within_length(self, value):
        """Validates: Requirements 3.4, 5.20, 6.10."""
        assert validate_capture_id(value) == value

    @given(st.text(alphabet=_CAPTURE_ID_ALPHABET, min_size=129, max_size=400))
    @settings(max_examples=100)
    def test_property_rejects_over_length(self, value):
        """Validates: Requirements 3.4, 5.20, 6.10 (length cap)."""
        with pytest.raises(ValidationError):
            validate_capture_id(value)

    @given(st.text(min_size=1, max_size=128))
    @settings(max_examples=200)
    def test_property_rejects_chars_outside_class(self, value):
        """Validates: Requirements 3.4, 5.20, 6.10 (character class)."""
        # Skip strings that happen to be entirely within the allowed
        # character class — those should pass and are covered by the
        # complementary property above.
        if all(c in _CAPTURE_ID_ALPHABET for c in value):
            assume(False)
        with pytest.raises(ValidationError):
            validate_capture_id(value)


# ---------------------------------------------------------------------------
# validate_eni_ids  (Reqs 3.1, 3.2, 4.3, 4.4)
# ---------------------------------------------------------------------------


class TestValidateEniIds:
    """Unit tests for validate_eni_ids."""

    @pytest.mark.parametrize(
        "good",
        [
            ["eni-12345678"],  # legacy 8-char form
            ["eni-0123456789abcdef0"],  # 17-char modern form
            ["eni-12345678", "eni-0123456789abcdef0"],
            ["eni-12345678", "eni-aaaaaaaa", "eni-bbbbbbbb"],  # exactly 3
        ],
    )
    def test_accepts_good_lists(self, good):
        assert validate_eni_ids(good) == good

    @pytest.mark.parametrize(
        "bad,reason",
        [
            (None, "missing"),
            ([], "empty"),
            (["eni-12345678", "eni-12345678"], "duplicate"),
            (
                ["eni-12345678", "eni-aaaaaaaa", "eni-bbbbbbbb", "eni-cccccccc"],
                "over limit",
            ),
            (["eni-XYZ12345"], "non-hex characters"),
            (["eni-1234"], "too short"),
            (["eni-0123456789abcdef01"], "too long"),
            (["i-12345678"], "wrong prefix"),
            ([12345], "non-string element"),
            ("eni-12345678", "not a list"),
        ],
    )
    def test_rejects_bad_lists(self, bad, reason):
        with pytest.raises(ValidationError) as exc_info:
            validate_eni_ids(bad)
        assert exc_info.value.error_category == "invalid_parameter"

    @given(st.lists(_eni_strings(), min_size=1, max_size=3, unique=True))
    @settings(max_examples=200)
    def test_property_accepts_distinct_lists_within_limit(self, ids):
        """Validates: Requirements 3.1, 4.3, 4.4."""
        assert validate_eni_ids(list(ids)) == list(ids)

    @given(st.lists(_eni_strings(), min_size=4, max_size=10, unique=True))
    @settings(max_examples=100)
    def test_property_rejects_lists_over_capture_eni_limit(self, ids):
        """Validates: Requirement 4.3 (Capture_Eni_Limit = 3)."""
        with pytest.raises(ValidationError):
            validate_eni_ids(list(ids))

    @given(_eni_strings(), st.integers(min_value=2, max_value=3))
    @settings(max_examples=100)
    def test_property_rejects_duplicate_entries(self, eni, repeat_count):
        """Validates: Requirement 4.4 (no duplicate ENI identifiers)."""
        with pytest.raises(ValidationError):
            validate_eni_ids([eni] * repeat_count)


# ---------------------------------------------------------------------------
# validate_duration_minutes  (Reqs 4.1, 4.2)
# ---------------------------------------------------------------------------


class TestValidateDurationMinutes:
    """Unit tests for validate_duration_minutes."""

    @pytest.mark.parametrize("good", [1, 15, 30, 45, 60])
    def test_accepts_in_range(self, good):
        assert validate_duration_minutes(good) == good

    @pytest.mark.parametrize(
        "bad,reason",
        [
            (None, "missing"),
            (0, "below range"),
            (-1, "negative"),
            (61, "above range"),
            (1000, "way over"),
            (15.0, "float"),
            ("15", "string"),
            (True, "bool true"),
            (False, "bool false"),
        ],
    )
    def test_rejects_invalid(self, bad, reason):
        with pytest.raises(ValidationError) as exc_info:
            validate_duration_minutes(bad)
        assert exc_info.value.error_category == "invalid_parameter"

    @given(st.integers(min_value=1, max_value=60))
    @settings(max_examples=60)
    def test_property_accepts_full_range(self, n):
        """Validates: Requirements 4.1, 4.2 (range 1..60)."""
        assert validate_duration_minutes(n) == n

    @given(
        st.one_of(
            st.integers(max_value=0),
            st.integers(min_value=61),
        )
    )
    @settings(max_examples=200)
    def test_property_rejects_outside_range(self, n):
        """Validates: Requirements 4.1, 4.2 (out-of-range rejection)."""
        with pytest.raises(ValidationError):
            validate_duration_minutes(n)


# ---------------------------------------------------------------------------
# validate_filter_id  (Req 3.2)
# ---------------------------------------------------------------------------


class TestValidateFilterId:
    """Unit tests for validate_filter_id."""

    @pytest.mark.parametrize(
        "good",
        [
            "a",
            "tmf-12345",
            "filter with spaces and punctuation!",
            "x" * 128,
        ],
    )
    def test_accepts_good_values(self, good):
        assert validate_filter_id(good) == good

    @pytest.mark.parametrize(
        "bad,reason",
        [
            (None, "missing"),
            ("", "empty"),
            ("x" * 129, "too long"),
            (123, "non-string"),
            ([], "non-string list"),
        ],
    )
    def test_rejects_bad_values(self, bad, reason):
        with pytest.raises(ValidationError) as exc_info:
            validate_filter_id(bad)
        assert exc_info.value.error_category == "invalid_parameter"

    @given(st.text(min_size=1, max_size=128))
    @settings(max_examples=200)
    def test_property_accepts_any_string_within_length(self, value):
        """Validates: Requirement 3.2 (filter_id length 1..128)."""
        assert validate_filter_id(value) == value

    @given(st.text(min_size=129, max_size=500))
    @settings(max_examples=100)
    def test_property_rejects_over_length(self, value):
        """Validates: Requirement 3.2 (length cap)."""
        with pytest.raises(ValidationError):
            validate_filter_id(value)


# ---------------------------------------------------------------------------
# validate_idempotency_token  (Req 3.15)
# ---------------------------------------------------------------------------


class TestValidateIdempotencyToken:
    """Unit tests for validate_idempotency_token."""

    @pytest.mark.parametrize(
        "good",
        [
            "a",
            "deadbeef-1234-cafe-babe-1234567890ab",
            "x" * 256,
        ],
    )
    def test_accepts_good_values(self, good):
        assert validate_idempotency_token(good) == good

    @pytest.mark.parametrize(
        "bad,reason",
        [
            (None, "missing"),
            ("", "empty"),
            ("x" * 257, "too long"),
            (12345, "non-string"),
        ],
    )
    def test_rejects_bad_values(self, bad, reason):
        with pytest.raises(ValidationError) as exc_info:
            validate_idempotency_token(bad)
        assert exc_info.value.error_category == "invalid_parameter"

    @given(st.text(min_size=1, max_size=256))
    @settings(max_examples=200)
    def test_property_accepts_any_string_within_length(self, value):
        """Validates: Requirement 3.15 (idempotency_token 1..256)."""
        assert validate_idempotency_token(value) == value

    @given(st.text(min_size=257, max_size=600))
    @settings(max_examples=100)
    def test_property_rejects_over_length(self, value):
        """Validates: Requirement 3.15 (length cap)."""
        with pytest.raises(ValidationError):
            validate_idempotency_token(value)


# ---------------------------------------------------------------------------
# validate_stream_id  (Reqs 5.21, 19.4)
# ---------------------------------------------------------------------------


class TestValidateStreamId:
    """Unit tests for validate_stream_id."""

    @pytest.mark.parametrize(
        "good",
        [
            "1",
            "tcp-stream_42",
            "A" * 64,
        ],
    )
    def test_accepts_good_values(self, good):
        assert validate_stream_id(good) == good

    @pytest.mark.parametrize(
        "bad,reason",
        [
            (None, "missing"),
            ("", "empty"),
            ("A" * 65, "too long"),
            ("bad space", "space"),
            ("bad/slash", "slash"),
            ("bad.dot", "dot"),
            (42, "non-string"),
        ],
    )
    def test_rejects_bad_values(self, bad, reason):
        with pytest.raises(ValidationError) as exc_info:
            validate_stream_id(bad)
        assert exc_info.value.error_category == "invalid_parameter"

    @given(_stream_id_strings())
    @settings(max_examples=200)
    def test_property_accepts_in_class_within_length(self, value):
        """Validates: Requirements 5.21, 19.4."""
        assert validate_stream_id(value) == value

    @given(st.text(alphabet=_CAPTURE_ID_ALPHABET, min_size=65, max_size=200))
    @settings(max_examples=100)
    def test_property_rejects_over_length(self, value):
        """Validates: Requirement 5.21 (stream_id length cap = 64)."""
        with pytest.raises(ValidationError):
            validate_stream_id(value)


# ---------------------------------------------------------------------------
# validate_status_filter  (Reqs 3.10, 3.11)
# ---------------------------------------------------------------------------


class TestValidateStatusFilter:
    """Unit tests for validate_status_filter."""

    _LIST_CAPTURES_SET = ("all", "active", "historical")

    @pytest.mark.parametrize("good", _LIST_CAPTURES_SET)
    def test_accepts_list_captures_values(self, good):
        assert validate_status_filter(good, self._LIST_CAPTURES_SET) == good

    @pytest.mark.parametrize(
        "bad,reason",
        [
            (None, "missing"),
            ("", "empty string is not in set"),
            ("ALL", "case-sensitive"),
            ("running", "not in set"),
            (42, "non-string"),
        ],
    )
    def test_rejects_bad_values(self, bad, reason):
        with pytest.raises(ValidationError) as exc_info:
            validate_status_filter(bad, self._LIST_CAPTURES_SET)
        assert exc_info.value.error_category == "invalid_parameter"

    def test_rejects_empty_accepted_set(self):
        """A misconfigured handler should never silently accept anything."""
        with pytest.raises(ValidationError):
            validate_status_filter("any", [])

    def test_works_with_arbitrary_iterables(self):
        # list, tuple, set, frozenset all supported via frozenset()
        assert validate_status_filter("a", ["a", "b"]) == "a"
        assert validate_status_filter("a", ("a", "b")) == "a"
        assert validate_status_filter("a", {"a", "b"}) == "a"
        assert validate_status_filter("a", frozenset({"a", "b"})) == "a"

    @given(
        st.sets(st.text(min_size=1, max_size=20), min_size=1, max_size=5),
        st.text(min_size=1, max_size=20),
    )
    @settings(max_examples=200)
    def test_property_membership_decides_outcome(self, accepted, candidate):
        """Validates: Requirements 3.10, 3.11 (closed set semantics)."""
        if candidate in accepted:
            assert validate_status_filter(candidate, accepted) == candidate
        else:
            with pytest.raises(ValidationError):
                validate_status_filter(candidate, accepted)


# ---------------------------------------------------------------------------
# ValidationError class
# ---------------------------------------------------------------------------


class TestValidationError:
    """The typed ValidationError exposes errorCategory + message."""

    def test_default_category_is_invalid_parameter(self):
        err = ValidationError("something went wrong")
        assert err.error_category == "invalid_parameter"
        assert err.message == "something went wrong"
        assert str(err) == "something went wrong"

    def test_custom_category_is_preserved(self):
        err = ValidationError("bad sql", error_category="invalid_sql")
        assert err.error_category == "invalid_sql"
        assert err.message == "bad sql"

    def test_is_an_exception_subclass(self):
        err = ValidationError("x")
        assert isinstance(err, Exception)
        with pytest.raises(ValidationError):
            raise err
