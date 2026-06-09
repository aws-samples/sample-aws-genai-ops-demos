"""
Unit and property-based tests for ``sql_safety.py``.

Run from the ``network-agent`` directory:

    python -m pytest test_sql_safety.py -v

These tests exercise the two public functions
:func:`sql_safety.validate_sql_shape` and
:func:`sql_safety.inject_capture_id_predicate` in isolation against
deterministic inputs plus randomized property-based generators.

The test classes are organized around the design's safety contract:

- ``TestAcceptsValidShapes`` checks that every shape the design
  documents (with and without WHERE / GROUP BY / HAVING / ORDER BY /
  LIMIT, with and without an alias) is accepted and rewritten
  correctly.
- ``TestRejectsForbiddenConstructs`` checks each rejection branch
  named in Req 5.3 plus the design's shape constraint.
- ``TestPredicateInjector`` checks the rewrite contract: existing
  WHERE → ``AND capture_id = '...'``; missing WHERE → new ``WHERE
  capture_id = '...'`` clause attached at the right position.
- ``TestProperties`` lifts Correctness Properties 5 and 6 from the
  design into hypothesis strategies that fuzz the shape validator
  against legal-shaped, illegal-shaped, and adversarial inputs.
"""

from __future__ import annotations

from typing import List

import pytest
from hypothesis import HealthCheck, assume, example, given, settings, strategies as st

import sql_safety
from sql_safety import (
    MAX_SQL_LENGTH,
    PCAP_LOGS_TABLE_NAME,
    SqlShapeError,
    inject_capture_id_predicate,
    validate_sql_shape,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accept_and_inject(sql: str, capture_id: str = "abc-123") -> str:
    """Validate ``sql`` then inject ``capture_id``; return the rewritten SQL."""
    tokens = validate_sql_shape(sql)
    return inject_capture_id_predicate(sql, capture_id, tokens=tokens)


def _expect_reject(sql: str) -> SqlShapeError:
    """Validate ``sql`` and require a :class:`SqlShapeError`; return the error."""
    with pytest.raises(SqlShapeError) as excinfo:
        validate_sql_shape(sql)
    return excinfo.value


# ---------------------------------------------------------------------------
# Accept cases
# ---------------------------------------------------------------------------


class TestAcceptsValidShapes:
    """The shape validator accepts every shape the design documents."""

    def test_select_star(self):
        rewritten = _accept_and_inject("SELECT * FROM pcap_logs")
        assert rewritten == "SELECT * FROM pcap_logs WHERE capture_id = 'abc-123'"

    def test_select_with_existing_where(self):
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs WHERE frame_size > 1500"
        )
        assert (
            rewritten
            == "SELECT * FROM pcap_logs WHERE frame_size > 1500 AND capture_id = 'abc-123'"
        )

    def test_select_with_order_by_no_where(self):
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs ORDER BY frame_time"
        )
        # New WHERE is inserted before ORDER BY.
        assert "WHERE capture_id = 'abc-123'" in rewritten
        assert rewritten.endswith("ORDER BY frame_time")

    def test_select_with_limit_no_where(self):
        rewritten = _accept_and_inject("SELECT * FROM pcap_logs LIMIT 10")
        assert "WHERE capture_id = 'abc-123'" in rewritten
        assert rewritten.endswith("LIMIT 10")

    def test_select_with_where_and_order_by(self):
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs WHERE frame_size > 1500 ORDER BY frame_time"
        )
        assert (
            rewritten
            == "SELECT * FROM pcap_logs WHERE frame_size > 1500 AND capture_id = 'abc-123' ORDER BY frame_time"
        )

    def test_select_with_where_group_having_order_limit(self):
        rewritten = _accept_and_inject(
            "SELECT src_ip, COUNT(*) FROM pcap_logs WHERE frame_size > 1500 "
            "GROUP BY src_ip HAVING COUNT(*) > 1 ORDER BY src_ip LIMIT 5"
        )
        # Predicate is attached to existing WHERE before GROUP BY.
        assert "WHERE frame_size > 1500 AND capture_id = 'abc-123' GROUP BY" in rewritten

    def test_select_with_alias_implicit(self):
        rewritten = _accept_and_inject(
            "SELECT p.frame_size FROM pcap_logs p WHERE p.frame_size > 1500"
        )
        assert "AND capture_id = 'abc-123'" in rewritten

    def test_select_with_alias_as(self):
        rewritten = _accept_and_inject(
            "SELECT p.frame_size FROM pcap_logs AS p WHERE p.frame_size > 1500"
        )
        assert "AND capture_id = 'abc-123'" in rewritten

    def test_select_with_function_call_in_projection(self):
        """Function-call parens are allowed (only subquery parens are rejected)."""
        rewritten = _accept_and_inject(
            "SELECT COUNT(*) AS cnt FROM pcap_logs"
        )
        assert "WHERE capture_id = 'abc-123'" in rewritten

    def test_select_with_function_call_in_where(self):
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs WHERE LOWER(src_ip) = '10.0.0.1'"
        )
        assert "AND capture_id = 'abc-123'" in rewritten

    def test_lowercase_keywords(self):
        rewritten = _accept_and_inject("select * from pcap_logs")
        assert "WHERE capture_id = 'abc-123'" in rewritten

    def test_mixed_case_keywords(self):
        rewritten = _accept_and_inject("SeLeCt * FrOm pcap_logs")
        assert "WHERE capture_id = 'abc-123'" in rewritten

    def test_quoted_table_name_accepted(self):
        rewritten = _accept_and_inject('SELECT * FROM "pcap_logs"')
        assert "WHERE capture_id = 'abc-123'" in rewritten


# ---------------------------------------------------------------------------
# Reject cases
# ---------------------------------------------------------------------------


class TestRejectsForbiddenConstructs:
    """Every Req 5.3 rejection path is exercised."""

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM pcap_logs",
            "INSERT INTO pcap_logs VALUES (1)",
            "UPDATE pcap_logs SET x = 1",
            "DROP TABLE pcap_logs",
            "CREATE TABLE foo AS SELECT * FROM pcap_logs",
            "ALTER TABLE pcap_logs ADD COLUMN x INT",
            "TRUNCATE TABLE pcap_logs",
            "MSCK REPAIR TABLE pcap_logs",
        ],
    )
    def test_rejects_non_select_keywords(self, sql: str):
        """Req 5.3: SQL not beginning with SELECT is rejected."""
        err = _expect_reject(sql)
        assert err.error_category == "invalid_sql"

    def test_rejects_lowercase_delete(self):
        _expect_reject("delete from pcap_logs")

    def test_rejects_with_cte(self):
        err = _expect_reject(
            "WITH x AS (SELECT 1) SELECT * FROM pcap_logs"
        )
        assert "SELECT" in err.message or "WITH" in err.message

    def test_rejects_join(self):
        err = _expect_reject(
            "SELECT * FROM pcap_logs JOIN other ON x = y"
        )
        assert "JOIN" in err.message

    def test_rejects_union(self):
        err = _expect_reject(
            "SELECT * FROM pcap_logs UNION SELECT * FROM pcap_logs"
        )
        assert "UNION" in err.message

    def test_rejects_intersect(self):
        err = _expect_reject(
            "SELECT * FROM pcap_logs INTERSECT SELECT * FROM pcap_logs"
        )
        assert "INTERSECT" in err.message

    def test_rejects_line_comment(self):
        err = _expect_reject("SELECT * FROM pcap_logs -- evil")
        assert "comments" in err.message.lower()

    def test_rejects_block_comment(self):
        err = _expect_reject("SELECT * /* evil */ FROM pcap_logs")
        assert "comments" in err.message.lower()

    def test_rejects_block_comment_at_start(self):
        err = _expect_reject("/* evil */ SELECT * FROM pcap_logs")
        assert "comments" in err.message.lower()

    def test_rejects_semicolon(self):
        err = _expect_reject("SELECT * FROM pcap_logs;")
        assert "semicolon" in err.message.lower()

    def test_rejects_stacked_query(self):
        """Classic SQL injection: terminate with ; and add a malicious query."""
        err = _expect_reject(
            "SELECT * FROM pcap_logs; DROP TABLE pcap_logs"
        )
        assert "semicolon" in err.message.lower()

    def test_rejects_subquery_in_where(self):
        err = _expect_reject(
            "SELECT * FROM pcap_logs WHERE x IN (SELECT y FROM pcap_logs)"
        )
        assert "subquer" in err.message.lower() or "SELECT" in err.message

    def test_rejects_subquery_in_from(self):
        # ``FROM (SELECT ...) p`` requires parens around SELECT — the
        # subquery-marker scan catches the inner SELECT.
        err = _expect_reject(
            "SELECT * FROM (SELECT * FROM pcap_logs) p"
        )
        assert "subquer" in err.message.lower() or "SELECT" in err.message

    def test_rejects_unbalanced_open_paren(self):
        # An unmatched open paren produces one of two rejections
        # depending on what tokens follow: either "subqueries are
        # not permitted" (when a structural keyword like FROM
        # appears inside the open paren region) or "unbalanced
        # parenthesis" (when the unbalanced state survives to the
        # end of the statement). Both signal the same defect to
        # the user — that the SQL is malformed — so we accept
        # either rejection message.
        err = _expect_reject("SELECT COUNT(* FROM pcap_logs")
        message = err.message.lower()
        assert (
            "parenthesis" in message
            or "balanced" in message
            or "subquer" in message
        )

    def test_rejects_unbalanced_close_paren(self):
        err = _expect_reject("SELECT COUNT(*)) FROM pcap_logs")
        assert "parenthesis" in err.message.lower() or "balanced" in err.message.lower()

    def test_rejects_other_table(self):
        err = _expect_reject("SELECT * FROM other_table")
        assert "pcap_logs" in err.message

    def test_rejects_empty_string(self):
        err = _expect_reject("")
        assert "empty" in err.message.lower()

    def test_rejects_whitespace_only(self):
        err = _expect_reject("   \n\t  ")
        assert "empty" in err.message.lower()

    def test_rejects_oversized_sql(self):
        # 16385 chars is one over the MAX_SQL_LENGTH limit.
        oversized = "SELECT * FROM pcap_logs WHERE x = '" + ("a" * (MAX_SQL_LENGTH)) + "'"
        # Construction may slightly differ from exact MAX_SQL_LENGTH+1
        # but the validator's bound check uses ``> MAX_SQL_LENGTH`` so
        # any string strictly longer than MAX_SQL_LENGTH is rejected.
        assert len(oversized) > MAX_SQL_LENGTH
        err = _expect_reject(oversized)
        assert str(MAX_SQL_LENGTH) in err.message

    def test_rejects_non_string_input(self):
        err = _expect_reject(12345)  # type: ignore[arg-type]
        assert "string" in err.message.lower()

    def test_rejects_unterminated_string_literal(self):
        err = _expect_reject("SELECT * FROM pcap_logs WHERE x = 'abc")
        assert "string" in err.message.lower() or "unterminated" in err.message.lower()

    def test_rejects_unterminated_quoted_identifier(self):
        err = _expect_reject('SELECT * FROM "pcap_logs WHERE x = 1')
        assert "identifier" in err.message.lower() or "unterminated" in err.message.lower()

    def test_rejects_first_token_not_select(self):
        err = _expect_reject("EXPLAIN SELECT * FROM pcap_logs")
        # ``EXPLAIN`` is not a forbidden keyword in our list, but the
        # first-token guard requires ``SELECT`` so it is still
        # rejected.
        assert "SELECT" in err.message

    def test_rejects_comma_join(self):
        err = _expect_reject(
            "SELECT * FROM pcap_logs, other_table WHERE x = y"
        )
        assert "comma" in err.message.lower() or "JOIN" in err.message

    def test_rejects_two_from_clauses(self):
        # Two FROM at depth 0 indicates a malformed statement — the
        # validator should reject this even though the keyword scan
        # alone might miss it (FROM is not in _FORBIDDEN_KEYWORD_SET).
        err = _expect_reject(
            "SELECT * FROM pcap_logs WHERE x FROM pcap_logs"
        )
        # The second FROM is reached after the first FROM/alias scan;
        # _find_top_level_keyword(FROM, start_after=alias_index)
        # returns its position so the validator rejects with "exactly
        # one top-level FROM clause".
        assert "FROM" in err.message


# ---------------------------------------------------------------------------
# Predicate injector contract
# ---------------------------------------------------------------------------


class TestPredicateInjector:
    """The injector preserves the rest of the SQL and inserts at the right place."""

    def test_appends_to_existing_where(self):
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs WHERE frame_size > 1500"
        )
        assert rewritten.endswith("AND capture_id = 'abc-123'")

    def test_inserts_new_where_before_order_by(self):
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs ORDER BY frame_time DESC"
        )
        # WHERE comes before ORDER BY in valid SQL.
        where_pos = rewritten.find("WHERE")
        order_pos = rewritten.find("ORDER")
        assert where_pos < order_pos
        assert "WHERE capture_id = 'abc-123'" in rewritten

    def test_inserts_new_where_before_limit(self):
        rewritten = _accept_and_inject("SELECT * FROM pcap_logs LIMIT 100")
        where_pos = rewritten.find("WHERE")
        limit_pos = rewritten.find("LIMIT")
        assert where_pos < limit_pos
        assert "WHERE capture_id = 'abc-123' LIMIT 100" in rewritten

    def test_inserts_predicate_before_group_by(self):
        rewritten = _accept_and_inject(
            "SELECT src_ip FROM pcap_logs WHERE frame_size > 1500 GROUP BY src_ip"
        )
        assert (
            "WHERE frame_size > 1500 AND capture_id = 'abc-123' GROUP BY src_ip"
            in rewritten
        )

    def test_capture_id_with_underscore(self):
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs", capture_id="my_capture_2024"
        )
        assert "WHERE capture_id = 'my_capture_2024'" in rewritten

    def test_capture_id_with_hyphen(self):
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs", capture_id="my-capture-2024"
        )
        assert "WHERE capture_id = 'my-capture-2024'" in rewritten

    def test_does_not_split_string_literal(self):
        """Strings containing ORDER, LIMIT, etc. should not confuse the injector."""
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs WHERE comment = 'ORDER BY hack'"
        )
        # The hack inside the literal should be preserved verbatim;
        # the predicate is appended to the WHERE before any (real)
        # trailing clause, but there is no real trailing clause here.
        assert "AND capture_id = 'abc-123'" in rewritten
        assert "'ORDER BY hack'" in rewritten


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


# Alphabet for valid Capture_Id values per the validator regex
# ``^[A-Za-z0-9_-]{1,128}$``.
_CAPTURE_ID_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)


def _capture_ids() -> st.SearchStrategy[str]:
    return st.text(alphabet=_CAPTURE_ID_ALPHABET, min_size=1, max_size=128)


# Random column names — simple identifiers that pass the FROM alias
# / projection rules.
def _column_names() -> st.SearchStrategy[str]:
    return st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)


# Forbidden top-level keywords drawn from the public list. Each
# generated string has the keyword as the first token, so the
# first-token-must-be-SELECT guard rejects it. (We intentionally do
# not interleave keywords inside an otherwise valid SELECT because
# many of those positions would be syntactically invalid anyway.)
_FORBIDDEN_TOP_LEVEL = [
    "INSERT INTO pcap_logs VALUES (1)",
    "UPDATE pcap_logs SET x = 1",
    "DELETE FROM pcap_logs",
    "DROP TABLE pcap_logs",
    "CREATE TABLE foo AS SELECT * FROM pcap_logs",
    "ALTER TABLE pcap_logs ADD COLUMN x INT",
    "TRUNCATE TABLE pcap_logs",
    "MSCK REPAIR TABLE pcap_logs",
    "WITH x AS (SELECT 1) SELECT * FROM pcap_logs",
]


# Suspicious tokens that the validator must reject when they appear
# as a forbidden keyword anywhere in the query. We embed each one
# in a host SELECT so the first-token check passes; the
# forbidden-keyword scan is then responsible for rejecting it.
_FORBIDDEN_EMBEDDED = [
    "SELECT * FROM pcap_logs UNION SELECT * FROM pcap_logs",
    "SELECT * FROM pcap_logs INTERSECT SELECT 1",
    "SELECT * FROM pcap_logs EXCEPT SELECT 1",
    "SELECT * FROM pcap_logs JOIN x ON 1=1",
]


# Constructs the validator must catch even when they sneak past the
# top-level keyword scan: comments and semicolons.
_FORBIDDEN_LEXICAL = [
    "SELECT * FROM pcap_logs -- evil",
    "SELECT * /* evil */ FROM pcap_logs",
    "SELECT * FROM pcap_logs;",
    "SELECT * FROM pcap_logs; DROP TABLE pcap_logs",
    "SELECT * FROM (SELECT * FROM pcap_logs) p",
    "SELECT * FROM pcap_logs WHERE x IN (SELECT 1)",
]


class TestProperties:
    """Property-based tests lifted from the design's Correctness Properties.

    Validates: Requirements 5.1, 5.2, 5.3, 5.7, 5.20 (Correctness
    Properties 5 and 6).
    """

    @given(capture_id=_capture_ids())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_property_capture_id_predicate_present_in_rewrite(self, capture_id: str):
        """Validates: Requirements 5.1, 5.7, 5.20 (Correctness Property 5).

        For every well-formed SELECT against ``pcap_logs`` and every
        valid ``capture_id``, the rewritten SQL contains an exact
        ``capture_id = '<id>'`` predicate.
        """
        # A small fixed grammar of accepted shapes; the property is
        # over the predicate-injection step, not the full SQL space.
        sqls = [
            "SELECT * FROM pcap_logs",
            "SELECT * FROM pcap_logs WHERE frame_size > 1500",
            "SELECT * FROM pcap_logs ORDER BY frame_time",
            "SELECT * FROM pcap_logs LIMIT 100",
            "SELECT * FROM pcap_logs WHERE frame_size > 1500 ORDER BY frame_time",
            "SELECT * FROM pcap_logs WHERE frame_size > 1500 LIMIT 100",
        ]
        for sql in sqls:
            rewritten = _accept_and_inject(sql, capture_id=capture_id)
            assert f"capture_id = '{capture_id}'" in rewritten

    @given(st.sampled_from(_FORBIDDEN_TOP_LEVEL))
    @settings(max_examples=50)
    def test_property_rejects_non_select_top_level(self, sql: str):
        """Validates: Requirements 5.3 (Correctness Property 6).

        SQL not starting with ``SELECT`` is rejected by the shape
        validator without reaching the injector.
        """
        with pytest.raises(SqlShapeError):
            validate_sql_shape(sql)

    @given(st.sampled_from(_FORBIDDEN_EMBEDDED))
    @settings(max_examples=50)
    def test_property_rejects_embedded_forbidden_keyword(self, sql: str):
        """Validates: Requirements 5.3 (Correctness Property 6)."""
        with pytest.raises(SqlShapeError):
            validate_sql_shape(sql)

    @given(st.sampled_from(_FORBIDDEN_LEXICAL))
    @settings(max_examples=50)
    def test_property_rejects_lexical_attack(self, sql: str):
        """Validates: Requirements 5.3 (Correctness Property 6).

        Comment markers, semicolons, and subquery parens are rejected.
        """
        with pytest.raises(SqlShapeError):
            validate_sql_shape(sql)

    @given(
        # Random WHERE predicate of the form ``col = 'value'``.
        column=_column_names(),
        value=st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd"),
                blacklist_characters="'\"\\;-/*",
            ),
            min_size=0,
            max_size=20,
        ),
        capture_id=_capture_ids(),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.filter_too_much],
    )
    def test_property_appends_to_existing_where(
        self, column: str, value: str, capture_id: str
    ):
        """Validates: Requirements 5.1, 5.7 (Correctness Property 5).

        For every well-formed ``WHERE col = 'value'`` predicate, the
        injector appends ``AND capture_id = '<id>'`` rather than
        replacing the existing predicate.
        """
        # Avoid SQL keyword collisions in the column name.
        assume(column.upper() not in {"AS", "AND", "OR", "NOT"})
        sql = f"SELECT * FROM pcap_logs WHERE {column} = '{value}'"
        rewritten = _accept_and_inject(sql, capture_id=capture_id)
        # The original predicate is preserved verbatim.
        assert f"{column} = '{value}'" in rewritten
        # The capture_id predicate is appended with AND.
        assert f"AND capture_id = '{capture_id}'" in rewritten
        # The combined clause appears exactly once.
        assert rewritten.count("WHERE") == 1

    @given(capture_id=_capture_ids())
    @settings(max_examples=50)
    def test_property_idempotent_double_injection_is_caught_at_validation(
        self, capture_id: str
    ):
        """Re-validating already-rewritten SQL is still accepted.

        This property guards against accidental "double rewrite"
        leaving a malformed query: the rewritten SQL produced by the
        injector should itself be a valid Pcap_Query_Action shape,
        so re-running the validator on it must succeed.
        """
        rewritten = _accept_and_inject(
            "SELECT * FROM pcap_logs", capture_id=capture_id
        )
        # Re-validate without re-injecting. validate_sql_shape must
        # still accept the rewritten SQL.
        validate_sql_shape(rewritten)
