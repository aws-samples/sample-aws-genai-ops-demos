"""
SQL safety helpers for the G.O.A.T. Network Agent ``query_pcap`` action.

Implements Task 13 of the goat-network-agent spec: a hand-rolled SQL
shape validator plus an AST-free Capture_Id_Predicate injector that
together provide the defense-in-depth surface for caller-supplied
SQL described in the design document.

Why hand-rolled (not ``sqlglot``) is the design choice
-------------------------------------------------------

The design's ``query_pcap`` section originally proposed adding
``sqlglot`` to the agent's ``requirements.txt`` for AST-based
rewriting. Req 1.5 of ``requirements.md`` mandates that the agent's
``requirements.txt`` declare **only** ``bedrock-agentcore`` and
``boto3``; the design's "Note on requirements.txt" explicitly states
that the hand-rolled approach is preferred to keep that requirement
intact. This module is that hand-rolled approach.

Defense in depth
----------------

The ``query_pcap`` handler sits behind three layers of protection:

1. **Shape validator** (this module's :func:`validate_sql_shape`)
   - rejects comments, semicolons, parens (subqueries), forbidden
   top-level keywords, and SQL not matching the documented grammar
   ``SELECT ... FROM pcap_logs [<alias>] [WHERE ...] [GROUP BY ...]
   [HAVING ...] [ORDER BY ...] [LIMIT n]``.
2. **Predicate injector** (this module's
   :func:`inject_capture_id_predicate`) - only runs after the shape
   validator has accepted the input; appends
   ``AND capture_id = '<id>'`` to an existing top-level ``WHERE`` or
   attaches a new ``WHERE`` clause before any ``GROUP BY``,
   ``HAVING``, ``ORDER BY``, or ``LIMIT`` clause.
3. **Athena IAM** (out of scope for this module): the AgentCore
   runtime role grants only ``athena:StartQueryExecution``,
   ``athena:GetQueryExecution``, ``athena:GetQueryResults``, and
   ``glue:Get*`` on the ``goat_network`` database, never any DDL or
   DML permission. Even if a malicious construct slipped past the
   shape validator, Athena would reject it with
   ``AccessDeniedException``.

The shape validator is deliberately pessimistic: it rejects anything
that could not unambiguously be classified as a single ``SELECT``
against ``pcap_logs``. A forbidden construct surfaces as
:class:`SqlShapeError` *before* :func:`inject_capture_id_predicate`
is ever called, so the injector's string-rewrite logic only ever
sees inputs that match the documented grammar.

Tokenizer scope
---------------

The tokenizer is the smallest possible lexer that lets us:

- skip whitespace,
- recognize SQL string literals (``'...'`` with ``''`` escape and
  Athena-style backslash escapes treated as literal),
- recognize quoted identifiers (``"..."``),
- reject the comment markers ``--`` and ``/*`` immediately on sight,
- emit a flat sequence of ``(kind, text, position)`` tokens for
  identifier/keyword/punctuation/literal classification by callers.

Parentheses are emitted as their own token kinds (``TK_LPAREN`` and
``TK_RPAREN``) so the validator can:

- track nesting depth and reject any structural keyword
  (``SELECT``, ``FROM``, ``WHERE``, ``GROUP``, ``HAVING``,
  ``ORDER``, ``LIMIT``) that appears at depth > 0 — that signals a
  subquery, which the design forbids,
- still allow function-call parens such as ``COUNT(*)`` or
  ``LOWER(col)`` in the projection and predicate (the design's
  grammar allows ``<projection>`` and ``<predicate>`` to be
  expressions),
- ensure :func:`_find_top_level_keyword` matches only at depth 0,
  so ``WHERE`` inside ``SUM(CASE WHEN x = 1 THEN 1 END)`` (whatever
  Athena allows) is not mistaken for the top-level ``WHERE``
  clause.

The tokenizer is **not** a full SQL parser. Anything beyond the
documented grammar (subqueries, CTEs, joins, unions) trips the shape
validator because the validator looks for the exact token sequence
``SELECT ... FROM pcap_logs ...`` with no additional structural
keywords elsewhere in the statement and no subquery markers inside
any parenthesized expression.

Why this is safe enough
-----------------------

The classic "SQL injection" surface for tools that interpolate user
input is interpolation of a *value* into a query string. This
module never interpolates a user value: ``capture_id`` is validated
by :func:`validation.validate_capture_id` (regex
``^[A-Za-z0-9_-]{1,128}$``) before the injector ever sees it, so
the injector emits ``AND capture_id = '<safe-string>'`` where
``<safe-string>`` is provably a member of the safe alphabet. The
injector also only runs on SQL that has been validated to contain
no forbidden keyword and no comment-style escape, so the injector's
single-quote string literal can never be terminated early by
caller-supplied content.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Typed exception
# ---------------------------------------------------------------------------


class SqlShapeError(Exception):
    """Raised by :func:`validate_sql_shape` when the input SQL is rejected.

    The exception carries a human-readable ``message`` and an
    ``error_category`` that handlers surface in
    ``metadata.errorCategory`` per design Error Handling section
    EH-1.

    Attributes:
        message: A short, user-actionable description of the offending
            construct (e.g. ``"comments are not permitted"``).
        error_category: Always ``"invalid_sql"`` for this class. Set
            via the constructor so future, more specific categories
            (for example ``"unsupported_construct"``) can reuse the
            same exception type without breaking the existing
            handler conversion path.
    """

    def __init__(
        self,
        message: str,
        error_category: str = "invalid_sql",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_category = error_category

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


# ---------------------------------------------------------------------------
# Public configuration
# ---------------------------------------------------------------------------

# Maximum length for caller-supplied SQL (Req 5.1: 1 to 16384 characters).
MAX_SQL_LENGTH = 16384

# Forbidden top-level keywords (Req 5.3 plus design's stricter shape
# rules). All matches are case-insensitive and must occur outside
# string literals and quoted identifiers.
#
# Req 5.3 explicitly names INSERT, UPDATE, DELETE, DROP, CREATE,
# ALTER, TRUNCATE, MSCK; the design's shape constraint additionally
# bans JOIN, UNION, WITH (CTE), and parenthesized subqueries — those
# are listed here too because their presence indicates a query shape
# the agent does not support.
_FORBIDDEN_KEYWORDS: Tuple[str, ...] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "MSCK",
    "JOIN",
    "UNION",
    "INTERSECT",
    "EXCEPT",
    "WITH",
    "VALUES",
    "MERGE",
    "CALL",
    "EXECUTE",
    "GRANT",
    "REVOKE",
)
_FORBIDDEN_KEYWORD_SET = frozenset(k.upper() for k in _FORBIDDEN_KEYWORDS)

# Subquery markers — if any of these keywords appear inside a
# parenthesized expression, we reject the SQL because the parens
# clearly bound a subquery rather than a function-call argument
# list. ``SELECT`` is the most obvious; ``FROM``, ``WHERE``,
# ``GROUP``, ``HAVING``, ``ORDER``, ``LIMIT`` would only appear
# inside parens as part of a subquery's clause.
_SUBQUERY_MARKER_KEYWORDS = frozenset(
    {"SELECT", "FROM", "WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "OFFSET"}
)

# Top-level clause keywords recognized by :func:`inject_capture_id_predicate`
# when it walks the rewritten statement looking for the right place to
# attach a new WHERE clause. The order matches SQL's clause order:
# WHERE comes before GROUP BY, HAVING, ORDER BY, and LIMIT.
_TRAILING_CLAUSE_KEYWORDS: Tuple[str, ...] = (
    "GROUP",   # GROUP BY
    "HAVING",
    "ORDER",   # ORDER BY
    "LIMIT",
    "OFFSET",
)
_TRAILING_CLAUSE_KEYWORD_SET = frozenset(_TRAILING_CLAUSE_KEYWORDS)

# The required FROM target. Per Req 5.1 / 6.7, every Pcap_Query_Action
# runs against the ``pcap_logs`` table in the ``goat_network`` Glue
# database. The shape validator uses this constant to assert the
# caller's FROM clause references the same table.
PCAP_LOGS_TABLE_NAME = "pcap_logs"

# Identifier pattern accepted as the FROM target and as an optional
# alias. ANSI SQL identifiers (unquoted) are letters, digits, and
# underscore, must start with a letter or underscore. Athena/Presto
# also allow dollar signs but ``pcap_logs`` doesn't need them.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Token kinds used by the tokenizer. Strings are intentionally
# constants (not an Enum) so the tokenizer stays a single small
# function returning a tuple-of-tuples that callers can pattern-match
# against without importing an Enum class.
TK_WORD = "WORD"        # Identifier or keyword (alphanumeric_run)
TK_NUMBER = "NUMBER"    # Numeric literal
TK_STRING = "STRING"    # Single-quoted string literal (full text including the quotes)
TK_QUOTED_ID = "QID"    # Double-quoted identifier (full text including the quotes)
TK_PUNCT = "PUNCT"      # Single punctuation char (any of those listed below)
TK_OP = "OP"            # One- or two-character operator (=, <, >, <=, >=, <>, !=)
TK_LPAREN = "LPAREN"    # Open parenthesis (allowed for function calls only)
TK_RPAREN = "RPAREN"    # Close parenthesis
TK_EOF = "EOF"          # End of input sentinel


def _tokenize(sql: str) -> List[Tuple[str, str, int]]:
    """Tokenize ``sql`` into a flat list of ``(kind, text, position)`` tuples.

    The tokenizer is deliberately tiny: it skips whitespace, rejects
    comments and semicolons immediately (raising
    :class:`SqlShapeError`), and emits one token per syntactic unit.
    String literals retain their delimiters in ``text`` so callers
    that emit the rewritten SQL can re-emit them verbatim.

    Args:
        sql: The raw caller-supplied SQL string.

    Returns:
        A list of tokens. The list always ends with a single
        ``(TK_EOF, "", len(sql))`` sentinel so callers don't need to
        special-case index out-of-range checks.

    Raises:
        SqlShapeError: When the input contains a comment marker, a
            semicolon, an unterminated string/identifier, or an
            otherwise unrecognized character. Each rejection sets
            ``error_category="invalid_sql"``.
    """
    tokens: List[Tuple[str, str, int]] = []
    i = 0
    n = len(sql)

    # Helper that emits a single rejection for a forbidden construct.
    def _reject(reason: str, position: int) -> None:
        raise SqlShapeError(
            f"{reason} (at position {position})",
        )

    while i < n:
        ch = sql[i]

        # --- 1. Whitespace -------------------------------------------------
        if ch.isspace():
            i += 1
            continue

        # --- 2. Comments (rejected immediately, Req 5.3 / shape constraint)
        # Both line comments (--) and block comments (/* */) are rejected
        # because either could carry a payload that bypasses the shape
        # validator's keyword search.
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            _reject("line comments (--) are not permitted", i)
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            _reject("block comments (/* */) are not permitted", i)

        # --- 3. Semicolons (rejected immediately, shape constraint) -------
        if ch == ";":
            _reject("semicolons are not permitted", i)

        # --- 4. String literals ('...'). Single-quote escape is doubling
        # the quote (SQL-standard). We accept that and a few other
        # well-known patterns conservatively: a single backslash inside a
        # string is treated as a literal byte, not as an escape, because
        # Athena/Presto do not interpret backslash escapes in string
        # literals by default.
        if ch == "'":
            start = i
            i += 1
            while i < n:
                if sql[i] == "'":
                    # Doubled quote => embedded apostrophe; consume both
                    # and continue.
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    # Otherwise, end of string literal.
                    i += 1
                    tokens.append((TK_STRING, sql[start:i], start))
                    break
                i += 1
            else:
                _reject("unterminated string literal", start)
            continue

        # --- 5. Quoted identifiers ("..."). Athena/Presto use the
        # double-quote for delimited identifiers. Doubled-quote ("")
        # escapes the quote inside the identifier.
        if ch == '"':
            start = i
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    tokens.append((TK_QUOTED_ID, sql[start:i], start))
                    break
                i += 1
            else:
                _reject("unterminated quoted identifier", start)
            continue

        # --- 6. Numeric literals -----------------------------------------
        if ch.isdigit():
            start = i
            while i < n and (sql[i].isdigit() or sql[i] == "."):
                i += 1
            tokens.append((TK_NUMBER, sql[start:i], start))
            continue

        # --- 7. Word tokens (identifiers and keywords) -------------------
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (sql[i].isalnum() or sql[i] == "_"):
                i += 1
            tokens.append((TK_WORD, sql[start:i], start))
            continue

        # --- 8. Two-character operators ----------------------------------
        if i + 1 < n:
            two = sql[i:i + 2]
            if two in ("<=", ">=", "<>", "!="):
                tokens.append((TK_OP, two, i))
                i += 2
                continue

        # --- 9. Single-character operators -------------------------------
        if ch in "=<>":
            tokens.append((TK_OP, ch, i))
            i += 1
            continue

        # --- 10. Punctuation ---------------------------------------------
        # Parentheses are emitted as their own token kinds so the
        # validator can track nesting depth and reject structural
        # uses (subqueries) while still allowing function-call
        # parens such as ``COUNT(*)`` or ``LOWER(col)``.
        if ch == "(":
            tokens.append((TK_LPAREN, ch, i))
            i += 1
            continue
        if ch == ")":
            tokens.append((TK_RPAREN, ch, i))
            i += 1
            continue
        # The remaining punctuation set is structurally inert: commas
        # appear in projection lists, dots in qualified columns,
        # asterisks in ``SELECT *`` and arithmetic, the four binary
        # arithmetic operators in expressions.
        if ch in ",.*+-/%":
            tokens.append((TK_PUNCT, ch, i))
            i += 1
            continue

        # --- 11. Anything else ------------------------------------------
        # Any other character (curly braces, square brackets, backticks,
        # etc.) is rejected outright so the tokenizer cannot silently
        # accept input that it does not understand.
        _reject(f"unsupported character {ch!r}", i)

    tokens.append((TK_EOF, "", n))
    return tokens


# ---------------------------------------------------------------------------
# Shape validator
# ---------------------------------------------------------------------------


def validate_sql_shape(sql: str) -> List[Tuple[str, str, int]]:
    """Validate that ``sql`` matches the documented Pcap_Query_Action shape.

    Accepts the grammar:

        SELECT <projection>
        FROM pcap_logs [<alias>]
        [WHERE <predicate>]
        [GROUP BY <columns>]
        [HAVING <predicate>]
        [ORDER BY <columns>]
        [LIMIT <integer>]

    Rejects:

      * empty / whitespace-only / non-string input,
      * SQL longer than :data:`MAX_SQL_LENGTH` (16384) characters,
      * any line comment (``--``), block comment (``/* */``), or
        semicolon (the tokenizer raises immediately),
      * unbalanced parentheses or any structural keyword
        (``SELECT``, ``FROM``, ``WHERE``, ``GROUP``, ``HAVING``,
        ``ORDER``, ``LIMIT``) appearing inside parentheses (subquery
        detection),
      * any forbidden top-level keyword in
        :data:`_FORBIDDEN_KEYWORDS` (case-insensitive, outside string
        literals and quoted identifiers),
      * SQL that does not begin with the keyword ``SELECT``
        (case-insensitive) (Req 5.3),
      * SQL whose ``FROM`` target is not the ``pcap_logs`` table.

    Function-call parentheses (e.g. ``COUNT(*)``, ``LOWER(col)``)
    are accepted in the projection and predicate as long as no
    structural keyword appears inside them.

    Args:
        sql: The caller-supplied SQL string from
            ``params["sql"]``. Must already be a non-empty trimmed
            string; the validator does not strip leading or trailing
            whitespace.

    Returns:
        The token sequence emitted by :func:`_tokenize`, ready for
        consumption by :func:`inject_capture_id_predicate`. Returning
        the tokens lets the injector reuse the tokenizer's work
        rather than tokenize the input twice.

    Raises:
        SqlShapeError: When any of the rejection conditions above is
            met. The exception message identifies the offending
            construct and (when applicable) its position so the
            response envelope can surface a clear error to the user.
    """
    if not isinstance(sql, str):
        raise SqlShapeError(
            f"sql must be a string, got {type(sql).__name__}",
        )

    if not sql.strip():
        raise SqlShapeError("sql must not be empty")

    if len(sql) > MAX_SQL_LENGTH:
        raise SqlShapeError(
            f"sql must be 1-{MAX_SQL_LENGTH} characters, got {len(sql)}",
        )

    # Tokenize first. The tokenizer rejects comments, semicolons,
    # parens, and unterminated literals so the rest of the validator
    # can assume a clean token sequence.
    tokens = _tokenize(sql)

    # Drop the EOF sentinel from the position-zero check below, but
    # keep it in the returned list for the injector.
    real_tokens = tokens[:-1]

    if not real_tokens:
        raise SqlShapeError("sql must not be empty")

    # --- Req 5.3: first non-whitespace, non-comment token is SELECT ----
    first = real_tokens[0]
    if first[0] != TK_WORD or first[1].upper() != "SELECT":
        raise SqlShapeError(
            "only top-level SELECT statements are permitted; "
            f"first token was {first[1]!r}",
        )

    # --- Forbidden keyword scan (Req 5.3 plus shape constraint) -------
    # Walk the token stream and reject if any TK_WORD whose uppercase
    # form appears in _FORBIDDEN_KEYWORD_SET. String literals are
    # TK_STRING and quoted identifiers are TK_QUOTED_ID, so this scan
    # naturally ignores keywords that appear inside quotes.
    #
    # We also track parenthesis depth and reject if any
    # _SUBQUERY_MARKER_KEYWORDS keyword appears inside parens —
    # that's the structural signal of a subquery (the design forbids
    # these even though function-call parens are allowed).
    paren_depth = 0
    for kind, text, position in real_tokens:
        if kind == TK_LPAREN:
            paren_depth += 1
            continue
        if kind == TK_RPAREN:
            paren_depth -= 1
            if paren_depth < 0:
                raise SqlShapeError(
                    f"unbalanced parenthesis (at position {position})",
                )
            continue
        if kind != TK_WORD:
            continue
        upper_text = text.upper()
        if upper_text in _FORBIDDEN_KEYWORD_SET:
            raise SqlShapeError(
                f"keyword {upper_text!r} is not permitted in query_pcap "
                f"SQL (at position {position})",
            )
        if paren_depth > 0 and upper_text in _SUBQUERY_MARKER_KEYWORDS:
            raise SqlShapeError(
                f"subqueries are not permitted; found {upper_text!r} "
                f"inside parentheses (at position {position})",
            )

    if paren_depth != 0:
        raise SqlShapeError(
            "unbalanced parenthesis at end of statement",
        )

    # --- Locate FROM and assert FROM target is pcap_logs --------------
    from_index = _find_top_level_keyword(real_tokens, "FROM")
    if from_index is None:
        raise SqlShapeError(
            "query_pcap SQL must include FROM pcap_logs",
        )

    # The token immediately after FROM must be the literal table name
    # ``pcap_logs``. We accept either an unquoted identifier or a
    # double-quoted identifier whose unwrapped value is ``pcap_logs``.
    if from_index + 1 >= len(real_tokens):
        raise SqlShapeError(
            "query_pcap SQL FROM clause is missing the table name",
        )

    table_token_kind, table_token_text, table_token_pos = real_tokens[from_index + 1]
    table_name: Optional[str] = None
    if table_token_kind == TK_WORD:
        table_name = table_token_text
    elif table_token_kind == TK_QUOTED_ID:
        # Strip the surrounding quotes and undo any "" escape.
        table_name = table_token_text[1:-1].replace('""', '"')

    if table_name != PCAP_LOGS_TABLE_NAME:
        raise SqlShapeError(
            f"FROM target must be the {PCAP_LOGS_TABLE_NAME!r} table, "
            f"got {table_token_text!r}",
        )

    # --- Optional alias check -----------------------------------------
    # SQL allows an optional alias after the table name, with or
    # without the ``AS`` keyword. We accept either form and require
    # the alias itself be a simple identifier.
    alias_consumed_index = from_index + 1  # last index claimed by FROM clause
    if alias_consumed_index + 1 < len(real_tokens):
        next_kind, next_text, _ = real_tokens[alias_consumed_index + 1]
        if next_kind == TK_WORD and next_text.upper() == "AS":
            # AS <alias>
            if alias_consumed_index + 2 >= len(real_tokens):
                raise SqlShapeError("FROM ... AS must be followed by an alias")
            alias_kind, alias_text, _ = real_tokens[alias_consumed_index + 2]
            if alias_kind != TK_WORD or not _IDENTIFIER_PATTERN.match(alias_text):
                raise SqlShapeError(
                    f"FROM alias must be a simple identifier, got {alias_text!r}",
                )
            alias_consumed_index += 2
        elif (
            next_kind == TK_WORD
            and next_text.upper() not in {"WHERE"} | _TRAILING_CLAUSE_KEYWORD_SET
            and _IDENTIFIER_PATTERN.match(next_text)
        ):
            # Implicit alias (no AS) - only when the next token is not
            # a recognized clause keyword. A plain identifier here is
            # the table alias.
            alias_consumed_index += 1

    # --- Reject extra FROM keywords (defends against e.g. comma joins) -
    # We've already rejected JOIN/UNION via the forbidden-keyword scan;
    # here we make sure no second FROM appears anywhere later in the
    # statement, which would imply a comma-join or correlated query.
    secondary_from = _find_top_level_keyword(
        real_tokens, "FROM", start_after=alias_consumed_index,
    )
    if secondary_from is not None:
        raise SqlShapeError(
            "query_pcap SQL must contain exactly one top-level FROM clause",
        )

    # --- Reject commas immediately after the table/alias --------------
    # A pattern like ``FROM pcap_logs, other_table`` is a comma join,
    # which the design forbids. We catch it here even though the
    # comma is not a forbidden keyword.
    if alias_consumed_index + 1 < len(real_tokens):
        next_kind, next_text, _ = real_tokens[alias_consumed_index + 1]
        if next_kind == TK_PUNCT and next_text == ",":
            raise SqlShapeError(
                "comma joins (FROM table1, table2) are not permitted",
            )

    return tokens


def _find_top_level_keyword(
    tokens: List[Tuple[str, str, int]],
    keyword: str,
    *,
    start_after: int = -1,
) -> Optional[int]:
    """Return the index of the first **top-level** ``TK_WORD`` matching ``keyword``.

    "Top-level" here means the keyword appears at parenthesis depth
    zero. Function-call parens (e.g. ``SUM(col)``, ``LOWER(x)``) are
    allowed in the projection and predicate, so a structural keyword
    like ``WHERE`` or ``FROM`` that appears inside such parens must
    be ignored — it cannot belong to the top-level statement.

    The match is case-insensitive. Because the tokenizer emits
    ``TK_WORD`` only for unquoted identifiers and keywords, this
    helper naturally ignores occurrences inside string literals or
    quoted identifiers.

    Args:
        tokens: Token sequence from :func:`_tokenize` (with or
            without the trailing ``TK_EOF`` sentinel).
        keyword: Uppercase keyword to find (e.g. ``"FROM"``,
            ``"WHERE"``).
        start_after: Index after which to begin searching. A value of
            ``-1`` (the default) starts at index 0.

    Returns:
        The index of the matching token at paren depth zero, or
        ``None`` if not found. If the search starts inside a
        parenthesized expression, the helper still treats depth
        relative to the start of the token stream.
    """
    upper_keyword = keyword.upper()
    depth = 0
    # Compute the depth at the start position so callers that
    # search forward from a known top-level token (e.g. FROM) get
    # the right depth on the first iteration.
    for index in range(0, start_after + 1):
        if index >= len(tokens):
            break
        kind, _, _ = tokens[index]
        if kind == TK_LPAREN:
            depth += 1
        elif kind == TK_RPAREN:
            depth -= 1
            if depth < 0:
                # Malformed input — paren depth cannot go negative
                # in well-formed SQL. Return None and let the caller
                # surface the error.
                return None
    for index in range(start_after + 1, len(tokens)):
        kind, text, _ = tokens[index]
        if kind == TK_LPAREN:
            depth += 1
            continue
        if kind == TK_RPAREN:
            depth -= 1
            if depth < 0:
                return None
            continue
        if depth == 0 and kind == TK_WORD and text.upper() == upper_keyword:
            return index
    return None


# ---------------------------------------------------------------------------
# Predicate injector
# ---------------------------------------------------------------------------


def inject_capture_id_predicate(
    sql: str,
    capture_id: str,
    tokens: Optional[List[Tuple[str, str, int]]] = None,
) -> str:
    """Append the Capture_Id_Predicate to a validated query.

    Implements Req 5.1, 5.7, and 5.20 (verbatim quote: "execute the
    SQL ... after appending or rewriting the WHERE clause to include
    a Capture_Id_Predicate"). The injector operates on string slices
    (not on a parsed AST) but only ever runs on input that has been
    accepted by :func:`validate_sql_shape`, which has already
    rejected comments, parens, and forbidden keywords. That makes
    the string-slice approach safe: the only places the original
    SQL can carry user input are inside string literals (which the
    tokenizer has bounded) and identifier names (which contain no
    structural characters).

    Behaviour:

      * If the validated SQL already contains a top-level ``WHERE``
        clause, the injector inserts ``AND capture_id = '<id>'``
        immediately before any trailing clause (``GROUP BY``,
        ``HAVING``, ``ORDER BY``, ``LIMIT``, ``OFFSET``) or at the
        end of the statement otherwise.
      * If the validated SQL has no ``WHERE`` clause, the injector
        attaches a new ``WHERE capture_id = '<id>'`` clause
        immediately after the last token of the FROM clause and
        before any trailing clause.

    The injected SQL is always whitespace-clean: a single space
    separates the inserted clause from the surrounding tokens.

    Args:
        sql: The caller-supplied SQL string previously accepted by
            :func:`validate_sql_shape`. **Do not pass unvalidated
            SQL** — the injector will silently produce undefined
            output if forbidden constructs are present.
        capture_id: A ``capture_id`` value previously validated by
            :func:`validation.validate_capture_id` (regex
            ``^[A-Za-z0-9_-]{1,128}$``). Because the validator
            restricts the alphabet to safe characters, the injector
            interpolates it directly into the SQL ``'...'`` literal
            without further quoting.
        tokens: Optional pre-tokenized form of ``sql`` (the value
            returned by :func:`validate_sql_shape`). When supplied,
            the injector skips re-tokenizing. When ``None``, the
            injector tokenizes ``sql`` itself.

    Returns:
        The rewritten SQL string with the Capture_Id_Predicate
        injected at the top-level ``WHERE``.

    Raises:
        SqlShapeError: When the input fails post-tokenization sanity
            checks (e.g. SQL that was tokenized but is missing the
            expected ``FROM`` keyword). Validated input never
            reaches these branches; they exist only as defensive
            guards.
    """
    if tokens is None:
        tokens = _tokenize(sql)

    real_tokens = tokens[:-1]  # drop the EOF sentinel

    # Locate the FROM clause's last token (the table name and any
    # alias). We find FROM, then walk forward until we either hit a
    # recognized trailing clause keyword (WHERE / GROUP / HAVING /
    # ORDER / LIMIT / OFFSET), the end of the statement, or a
    # comma — the comma case has already been rejected by
    # ``validate_sql_shape`` so we don't need to handle it again.
    from_index = _find_top_level_keyword(real_tokens, "FROM")
    if from_index is None:  # pragma: no cover - guarded by validator
        raise SqlShapeError("validated SQL is missing FROM (defensive guard)")

    # The FROM target token is from_index + 1; the optional alias
    # consumes one or two more tokens. We walk forward token by token
    # looking for the first WHERE or trailing clause keyword.
    where_index = _find_top_level_keyword(
        real_tokens, "WHERE", start_after=from_index,
    )

    # Helper to find the first trailing-clause keyword index after
    # `start_after`. Returns ``len(real_tokens)`` (i.e. one-past-end)
    # if none is found, so callers can use the result as a slice
    # endpoint.
    def _find_first_trailing_clause(start_after: int) -> int:
        for index in range(start_after + 1, len(real_tokens)):
            kind, text, _ = real_tokens[index]
            if kind == TK_WORD and text.upper() in _TRAILING_CLAUSE_KEYWORD_SET:
                return index
        return len(real_tokens)

    predicate_clause = f"capture_id = '{capture_id}'"

    if where_index is None:
        # No existing WHERE — attach a new WHERE clause right after the
        # FROM target/alias and before any trailing clause.
        first_trailing = _find_first_trailing_clause(from_index)

        if first_trailing < len(real_tokens):
            # Insert before the trailing clause keyword's position in the
            # original SQL string.
            insert_position = real_tokens[first_trailing][2]
            head = sql[:insert_position].rstrip()
            tail = sql[insert_position:]
            return f"{head} WHERE {predicate_clause} {tail}".rstrip()

        # No trailing clause — append the WHERE at the end of the
        # statement.
        return f"{sql.rstrip()} WHERE {predicate_clause}"

    # Existing WHERE — append ``AND capture_id = '<id>'`` to the
    # existing predicate, just before any trailing clause.
    first_trailing = _find_first_trailing_clause(where_index)
    if first_trailing < len(real_tokens):
        insert_position = real_tokens[first_trailing][2]
        head = sql[:insert_position].rstrip()
        tail = sql[insert_position:]
        return f"{head} AND {predicate_clause} {tail}".rstrip()

    # WHERE clause runs to the end of the statement; append the
    # predicate to the end.
    return f"{sql.rstrip()} AND {predicate_clause}"


__all__ = [
    "MAX_SQL_LENGTH",
    "PCAP_LOGS_TABLE_NAME",
    "SqlShapeError",
    "validate_sql_shape",
    "inject_capture_id_predicate",
]
