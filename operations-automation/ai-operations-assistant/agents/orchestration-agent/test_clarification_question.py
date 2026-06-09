"""
Unit and property-based tests for the orchestration agent's
Clarification_Question rules and confirmation token sets introduced
by Task 37 (Reqs 16.1-16.13).

Run from the ``orchestration-agent`` directory::

    python -m pytest test_clarification_question.py -v

Scope:

- ``AFFIRMATIVE_RESPONSE_SET`` / ``NEGATIVE_RESPONSE_SET`` — the
  canonical confirmation-token sets exposed as module-level
  ``frozenset`` constants. Membership matches Req 16 verbatim.
- ``is_affirmative_response`` / ``is_negative_response`` — the
  matching helpers, with case-insensitive comparison and trailing
  punctuation/whitespace stripping per Req 16.2.
- ``CAPTURE_PARAMETER_PRIORITY_ORDER`` — the documented priority
  order (Req 16.12) used when more than one parameter is missing
  in the same chat turn.
- ``select_blocking_parameter`` — picks the highest-priority
  bucket from a set of missing parameters.
- ``_build_system_prompt`` — verifies the system prompt contains
  the expected model-agnostic descriptions of the response sets,
  the single-question-per-turn rule, the priority order, the
  Capture_Opt_In_Tag policy, and each of the seven documented
  Clarification_Question templates from Req 16.3-16.9 (Req 16.13).
"""

from __future__ import annotations

import os
import re
import string

import pytest
from hypothesis import given, settings, strategies as st

# AgentCore imports a region at module load. Set both env vars before
# importing main so the module loads cleanly outside the AgentCore
# runtime (mirrors the pattern in test_capture_authorization.py and
# test_capture_confirmation.py).
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import main  # noqa: E402


# ---------------------------------------------------------------------------
# Confirmation token sets — public constant shape
# ---------------------------------------------------------------------------


class TestAffirmativeResponseSetConstant:
    """The canonical Affirmative_Response_Set is a frozenset of nine tokens."""

    def test_is_a_frozenset(self):
        assert isinstance(main.AFFIRMATIVE_RESPONSE_SET, frozenset)

    def test_contains_exactly_the_documented_tokens(self):
        """Validates: Requirement 16.2"""
        expected = {
            "yes", "y", "ok", "okay", "sure",
            "confirm", "proceed", "go", "accept",
        }
        assert set(main.AFFIRMATIVE_RESPONSE_SET) == expected

    def test_private_alias_is_the_same_object(self):
        # Backward-compatibility check: the private name still points
        # at the same frozenset instance so existing callers (and
        # earlier tests) keep working.
        assert main._AFFIRMATIVE_RESPONSE_SET is main.AFFIRMATIVE_RESPONSE_SET


class TestNegativeResponseSetConstant:
    """The canonical Negative_Response_Set is a frozenset of six tokens."""

    def test_is_a_frozenset(self):
        assert isinstance(main.NEGATIVE_RESPONSE_SET, frozenset)

    def test_contains_exactly_the_documented_tokens(self):
        """Validates: Requirement 16.2"""
        expected = {"no", "n", "cancel", "abort", "stop", "nevermind"}
        assert set(main.NEGATIVE_RESPONSE_SET) == expected

    def test_private_alias_is_the_same_object(self):
        assert main._NEGATIVE_RESPONSE_SET is main.NEGATIVE_RESPONSE_SET


class TestResponseSetsAreDisjoint:
    """No token appears in both sets — the agent must classify unambiguously."""

    def test_intersection_is_empty(self):
        """Validates: Requirement 16.2"""
        assert main.AFFIRMATIVE_RESPONSE_SET.isdisjoint(
            main.NEGATIVE_RESPONSE_SET
        )


# ---------------------------------------------------------------------------
# Confirmation token matchers — unit tests
# ---------------------------------------------------------------------------


class TestIsAffirmativeResponse:
    """Verbose case-insensitive matching with trailing punctuation stripped."""

    @pytest.mark.parametrize(
        "value",
        ["yes", "y", "ok", "okay", "sure", "confirm",
         "proceed", "go", "accept"],
    )
    def test_documented_tokens_match(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_affirmative_response(value) is True

    @pytest.mark.parametrize(
        "value",
        ["YES", "Yes", "yEs", "Y", "OK", "Okay", "OKAY", "ACCEPT"],
    )
    def test_case_insensitive(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_affirmative_response(value) is True

    @pytest.mark.parametrize(
        "value",
        ["  yes  ", "\tok\t", "\nokay\n", " accept ", "yes ", " yes"],
    )
    def test_surrounding_whitespace_stripped(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_affirmative_response(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "yes!",
            "yes.",
            "yes,",
            "yes;",
            "yes:",
            "yes?",
            "yes...",
            "yes!!!",
            " ok. ",
            "okay???",
        ],
    )
    def test_trailing_punctuation_stripped(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_affirmative_response(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "yeah",
            "ya",
            "yep",
            "yes please",  # Multi-word answer is rejected (Req 16.2 is lexical)
            "I confirm",
            "go ahead",
            "do it",
        ],
    )
    def test_unrecognised_replies_rejected(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_affirmative_response(value) is False

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "...", "!", None, 42, ["yes"], {"yes": True}],
    )
    def test_non_string_or_empty_inputs_rejected(self, value):
        assert main.is_affirmative_response(value) is False


class TestIsNegativeResponse:
    """Same shape as is_affirmative_response but for cancellation tokens."""

    @pytest.mark.parametrize(
        "value", ["no", "n", "cancel", "abort", "stop", "nevermind"]
    )
    def test_documented_tokens_match(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_negative_response(value) is True

    @pytest.mark.parametrize(
        "value", ["NO", "No", "Cancel", "ABORT", "Stop", "NeverMind"]
    )
    def test_case_insensitive(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_negative_response(value) is True

    @pytest.mark.parametrize(
        "value",
        ["  no  ", "\tcancel\t", "stop ", " abort"],
    )
    def test_surrounding_whitespace_stripped(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_negative_response(value) is True

    @pytest.mark.parametrize(
        "value",
        ["no.", "no!", "cancel,", "abort?", "stop;", "nevermind:", "no..."],
    )
    def test_trailing_punctuation_stripped(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_negative_response(value) is True

    @pytest.mark.parametrize(
        "value",
        ["nope", "nah", "naw", "no thanks", "don't", "hold on", "wait"],
    )
    def test_unrecognised_replies_rejected(self, value):
        """Validates: Requirement 16.2"""
        assert main.is_negative_response(value) is False

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "!", None, 0, ["no"], {"no": True}],
    )
    def test_non_string_or_empty_inputs_rejected(self, value):
        assert main.is_negative_response(value) is False


class TestResponseSetsCannotMatchSimultaneously:
    """A normalized input can match at most one of the two sets."""

    @pytest.mark.parametrize(
        "value",
        # Cross-product of the documented tokens with normalization
        # variants. Each entry must be classified as exactly one of
        # affirmative/negative — never both, never neither.
        [
            "yes",
            "no",
            "  YES  ",
            "  NO  ",
            "ok!",
            "cancel?",
            "okay.",
            "stop.",
            "Y",
            "n",
        ],
    )
    def test_exactly_one_classification(self, value):
        """Validates: Requirement 16.2"""
        affirmative = main.is_affirmative_response(value)
        negative = main.is_negative_response(value)
        assert (affirmative or negative) and not (affirmative and negative)


# ---------------------------------------------------------------------------
# Property-based tests — confirmation token matchers
# ---------------------------------------------------------------------------


@st.composite
def _padded_token(draw, set_constant: frozenset[str]):
    """Generate a documented token surrounded by whitespace and trailing punct.

    Returns ``(raw, expected_token)`` where ``raw`` is the user's
    reply and ``expected_token`` is the lowercase documented token
    the matcher should classify ``raw`` as.
    """
    token = draw(st.sampled_from(sorted(set_constant)))
    leading = draw(st.text(alphabet=" \t\n\r", min_size=0, max_size=4))
    trailing_ws = draw(st.text(alphabet=" \t\n\r", min_size=0, max_size=4))
    trailing_punct = draw(
        st.text(alphabet=".!?,;: ", min_size=0, max_size=5)
    )
    # Randomly upper- or lower-case each character of the token to
    # exercise case-insensitive matching without ever changing the
    # underlying token identity.
    cased_chars = []
    for ch in token:
        if draw(st.booleans()):
            cased_chars.append(ch.upper())
        else:
            cased_chars.append(ch.lower())
    cased = "".join(cased_chars)
    return leading + cased + trailing_ws + trailing_punct, token


class TestResponseMatchersPropertyBased:
    """Property-based tests for is_affirmative_response / is_negative_response."""

    @given(_padded_token(main.AFFIRMATIVE_RESPONSE_SET))
    @settings(max_examples=200, deadline=None)
    def test_padded_affirmative_token_always_matches(self, sample):
        """Validates: Requirement 16.2

        For any documented affirmative token, surrounding whitespace
        and trailing punctuation/casing must not change the
        classification.
        """
        raw, _ = sample
        assert main.is_affirmative_response(raw) is True

    @given(_padded_token(main.NEGATIVE_RESPONSE_SET))
    @settings(max_examples=200, deadline=None)
    def test_padded_negative_token_always_matches(self, sample):
        """Validates: Requirement 16.2

        Same property as above for the cancellation tokens.
        """
        raw, _ = sample
        assert main.is_negative_response(raw) is True

    @given(_padded_token(main.AFFIRMATIVE_RESPONSE_SET))
    @settings(max_examples=100, deadline=None)
    def test_affirmative_input_never_classifies_as_negative(self, sample):
        """Validates: Requirement 16.2

        The two sets are disjoint, so a padded affirmative reply must
        never match the negative classifier.
        """
        raw, _ = sample
        assert main.is_negative_response(raw) is False

    @given(_padded_token(main.NEGATIVE_RESPONSE_SET))
    @settings(max_examples=100, deadline=None)
    def test_negative_input_never_classifies_as_affirmative(self, sample):
        """Validates: Requirement 16.2"""
        raw, _ = sample
        assert main.is_affirmative_response(raw) is False

    @given(
        st.text(
            # Reject characters that the trim list strips so the
            # generator can't accidentally produce a documented
            # token. Also exclude letters by limiting the alphabet
            # to digits + a few non-ascii so we never hit a real
            # token by chance.
            alphabet=st.characters(
                whitelist_categories=("Nd",),
                whitelist_characters="@#$%^&*()_+-/\\|<>{}[]",
            ),
            min_size=1,
            max_size=20,
        )
    )
    @settings(max_examples=200, deadline=None)
    def test_unrelated_text_never_matches_either_set(self, value):
        """Validates: Requirement 16.2

        Random non-token text must classify as neither affirmative
        nor negative — these inputs trigger the "restate the prompt"
        branch in the orchestration agent.
        """
        # The random text is digits + symbols only, so even after
        # stripping it cannot equal any documented token.
        assert main.is_affirmative_response(value) is False
        assert main.is_negative_response(value) is False


# ---------------------------------------------------------------------------
# Priority order — CAPTURE_PARAMETER_PRIORITY_ORDER + select_blocking_parameter
# ---------------------------------------------------------------------------


class TestCaptureParameterPriorityOrder:
    """The documented priority order is fixed and ordered (Req 16.12)."""

    def test_is_a_tuple(self):
        # Tuples preserve order; sets/frozensets do not. Req 16.12
        # mandates a deterministic order so the user always sees the
        # same question first when the same parameters are missing.
        assert isinstance(main.CAPTURE_PARAMETER_PRIORITY_ORDER, tuple)

    def test_exact_order_matches_design(self):
        """Validates: Requirement 16.12

        Order: ENIs to mirror → capture_id → duration → other.
        """
        assert main.CAPTURE_PARAMETER_PRIORITY_ORDER == (
            "eni_selection",
            "capture_id",
            "duration",
            "other",
        )

    def test_no_duplicates(self):
        assert len(main.CAPTURE_PARAMETER_PRIORITY_ORDER) == len(
            set(main.CAPTURE_PARAMETER_PRIORITY_ORDER)
        )


class TestSelectBlockingParameter:
    """Pick the highest-priority bucket from a set of missing parameters."""

    def test_returns_none_for_empty_input(self):
        assert main.select_blocking_parameter([]) is None
        assert main.select_blocking_parameter(()) is None

    def test_returns_none_for_none(self):
        assert main.select_blocking_parameter(None) is None

    def test_eni_selection_wins_over_capture_id(self):
        """Validates: Requirement 16.12"""
        result = main.select_blocking_parameter(["capture_id", "eni_selection"])
        assert result == "eni_selection"

    def test_capture_id_wins_over_duration(self):
        """Validates: Requirement 16.12"""
        result = main.select_blocking_parameter(["duration", "capture_id"])
        assert result == "capture_id"

    def test_duration_wins_over_other(self):
        """Validates: Requirement 16.12"""
        result = main.select_blocking_parameter(["other", "duration"])
        assert result == "duration"

    def test_single_bucket_returned(self):
        for bucket in main.CAPTURE_PARAMETER_PRIORITY_ORDER:
            assert main.select_blocking_parameter([bucket]) == bucket

    def test_unknown_bucket_falls_back_to_other(self):
        """Validates: Requirement 16.12

        The catch-all branch lets the agent introduce new parameter
        names without breaking the priority order's tie-break rule.
        """
        result = main.select_blocking_parameter(["filter_id"])
        assert result == "other"

    def test_unknown_bucket_does_not_outrank_eni_selection(self):
        result = main.select_blocking_parameter(["filter_id", "eni_selection"])
        assert result == "eni_selection"

    def test_ignores_non_string_or_empty_entries(self):
        result = main.select_blocking_parameter(
            ["", None, 42, "capture_id", ()]
        )
        assert result == "capture_id"


class TestSelectBlockingParameterPropertyBased:
    """Property-based tests for the priority resolver."""

    @given(
        st.lists(
            st.sampled_from(list(main.CAPTURE_PARAMETER_PRIORITY_ORDER)),
            min_size=1,
            max_size=8,
        )
    )
    @settings(max_examples=200, deadline=None)
    def test_result_is_in_priority_order(self, missing):
        """Validates: Requirement 16.12

        The result is always one of the documented buckets. Never
        ``None`` when the input contains at least one bucket.
        """
        result = main.select_blocking_parameter(missing)
        assert result in main.CAPTURE_PARAMETER_PRIORITY_ORDER

    @given(
        st.lists(
            st.sampled_from(list(main.CAPTURE_PARAMETER_PRIORITY_ORDER)),
            min_size=1,
            max_size=8,
        )
    )
    @settings(max_examples=200, deadline=None)
    def test_result_minimizes_priority_index(self, missing):
        """Validates: Requirement 16.12

        ``select_blocking_parameter`` returns the bucket whose index
        in :data:`CAPTURE_PARAMETER_PRIORITY_ORDER` is the lowest of
        any bucket present in the input — i.e. it is a strict
        priority-order minimum.
        """
        result = main.select_blocking_parameter(missing)
        present_indices = [
            main.CAPTURE_PARAMETER_PRIORITY_ORDER.index(b)
            for b in missing
            if b in main.CAPTURE_PARAMETER_PRIORITY_ORDER
        ]
        # Every entry in the input was a valid bucket per the
        # generator, so present_indices is non-empty.
        assert main.CAPTURE_PARAMETER_PRIORITY_ORDER.index(result) == min(
            present_indices
        )

    @given(
        st.lists(
            st.sampled_from(list(main.CAPTURE_PARAMETER_PRIORITY_ORDER)),
            min_size=1,
            max_size=8,
        ),
        st.lists(
            st.sampled_from(list(main.CAPTURE_PARAMETER_PRIORITY_ORDER)),
            min_size=1,
            max_size=8,
        ),
    )
    @settings(max_examples=200, deadline=None)
    def test_idempotent_under_set_equality(self, a, b):
        """Validates: Requirement 16.12

        The result depends only on the set of buckets present, not
        on order or repetition (the function is set-valued).
        """
        if set(a) == set(b):
            assert main.select_blocking_parameter(a) == main.select_blocking_parameter(b)


# ---------------------------------------------------------------------------
# System prompt content — Req 16.13
# ---------------------------------------------------------------------------


class TestSystemPromptCoverage:
    """The system prompt documents every Clarification_Question rule.

    Req 16.13 requires the orchestration agent system prompt to
    include the Affirmative_Response_Set, the Negative_Response_Set,
    the parameter priority order from criterion 12, and the
    Capture_Opt_In_Tag check policy from criterion 5, all in
    model-agnostic language. This test class enforces those
    invariants by asserting the prompt text contains specific
    landmarks.
    """

    @pytest.fixture(scope="class")
    def prompt(self) -> str:
        return main._build_system_prompt()

    def test_prompt_documents_section_header(self, prompt):
        """Validates: Requirement 16.13"""
        assert "CLARIFICATION_QUESTION RULES" in prompt

    def test_prompt_lists_every_affirmative_token(self, prompt):
        """Validates: Requirement 16.13"""
        for token in main.AFFIRMATIVE_RESPONSE_SET:
            assert f"``{token}``" in prompt, f"missing affirmative token {token}"

    def test_prompt_lists_every_negative_token(self, prompt):
        """Validates: Requirement 16.13"""
        for token in main.NEGATIVE_RESPONSE_SET:
            assert f"``{token}``" in prompt, f"missing negative token {token}"

    def test_prompt_documents_priority_order(self, prompt):
        """Validates: Requirement 16.12 / 16.13

        The prompt names the four priority buckets in order, with
        ENIs first. We anchor to the ONE QUESTION PER TURN section
        because earlier sections (CAPTURE_CONVERSATION_CONTEXT)
        also reference ``capture_id`` for unrelated reasons.
        """
        section_start = prompt.find("ONE QUESTION PER TURN")
        assert section_start >= 0
        section_end = prompt.find(
            "MISSING-PARAMETER QUESTION TEMPLATES", section_start
        )
        assert section_end > section_start
        section = prompt[section_start:section_end]
        eni_idx = section.find("ENIs to mirror")
        capture_idx = section.find("``capture_id``")
        duration_idx = section.find("``duration``")
        other_idx = section.find("``other``")
        assert eni_idx >= 0, "section missing ENIs to mirror"
        assert capture_idx > eni_idx, "capture_id must come after ENIs"
        assert duration_idx > capture_idx, "duration must come after capture_id"
        assert other_idx > duration_idx, "other must come after duration"

    def test_prompt_documents_one_question_per_turn(self, prompt):
        """Validates: Requirement 16.12 / 16.13"""
        assert "ONE QUESTION PER TURN" in prompt
        assert "AT MOST one Clarification_Question" in prompt

    def test_prompt_documents_unrecognised_reply_rule(self, prompt):
        """Validates: Requirement 16.2 / 16.13"""
        assert "UNRECOGNISED REPLY" in prompt
        assert "restate" in prompt.lower()

    def test_prompt_documents_capture_opt_in_tag_policy(self, prompt):
        """Validates: Requirement 16.5 / 16.13"""
        assert "CAPTURE_OPT_IN_TAG POLICY" in prompt
        assert "goat-network-capture-allowed=true" in prompt
        # The three options must be documented (Req 16.5). Use a
        # whitespace-tolerant regex so the literal newline between
        # ``EXACTLY THREE`` and ``options`` (introduced by the prompt
        # paragraph wrap) does not break the assertion.
        assert re.search(r"EXACTLY THREE\s+options", prompt)

    def test_prompt_documents_three_opt_in_options(self, prompt):
        """Validates: Requirement 16.5"""
        # Each of the three options must be visible in the prompt as
        # the user-facing choice. We anchor on the lettered options
        # the prompt template names.
        assert "Skip the offending ENIs" in prompt
        assert "Abort the capture" in prompt
        assert "Send a request to the resource owner" in prompt

    def test_prompt_documents_missing_instance_or_eni(self, prompt):
        """Validates: Requirement 16.3 / 16.13"""
        # The agent must offer to call ``list_enis`` when the user
        # has not picked a target.
        assert "instance, ENI, or endpoint" in prompt
        assert "list_enis" in prompt

    def test_prompt_documents_too_many_enis(self, prompt):
        """Validates: Requirement 16.6 / 16.13"""
        assert "Capture_Eni_Limit" in prompt
        # The prompt should reference the 1-3 selection range.
        assert "1-3" in prompt or "at most 3" in prompt

    def test_prompt_documents_missing_capture_id_for_pcap_query(self, prompt):
        """Validates: Requirement 16.7 / 16.13"""
        assert "Pcap_Query_Action" in prompt
        # The model must call list_captures with status all when no
        # capture context is available.
        assert "list_captures" in prompt
        assert "status" in prompt

    def test_prompt_documents_no_rows_offer_transform(self, prompt):
        """Validates: Requirement 16.8 / 16.13"""
        assert "transform_capture" in prompt
        # The "no rows for capture_id" template offers transform_capture
        # via a yes/no Clarification_Question.
        assert "queryable" in prompt or "Athena" in prompt

    def test_prompt_documents_multiple_active_captures_on_stop(self, prompt):
        """Validates: Requirement 16.9 / 16.13"""
        # The "all" or single capture_id reply pattern must be visible.
        assert "stop every active capture" in prompt or "stop every" in prompt
        # The agent must list captures with status active first.
        assert 'status": "active"' in prompt or 'status": "active' in prompt

    def test_prompt_does_not_use_model_specific_directives(self, prompt):
        """Validates: Requirement 9.10 / 16.13

        The system prompt must avoid model-specific tokens (e.g.
        Anthropic <function_calls>, OpenAI tool-use formatting) so
        any Bedrock-supported model swapped via ORCH_MODEL_ID
        interprets the rules consistently.
        """
        forbidden = [
            "<function_calls>",
            "<function_calls>",
            "</function_calls>",
            "[TOOL_CALL]",
            "<|tool_call|>",
            "tool_use_name",
        ]
        for marker in forbidden:
            assert marker not in prompt, f"system prompt contains model-specific token {marker!r}"


class TestPromptIsFStringSafe:
    """Smoke test: the prompt must build at runtime without f-string errors.

    The system prompt is an f-string; literal ``{...}`` JSON examples
    embedded in it must be escaped as ``{{...}}`` or Python raises a
    ``ValueError`` at format time. This test caught a regression
    where Task 33-36 left several JSON examples unescaped.
    """

    def test_prompt_builds_without_format_errors(self):
        prompt = main._build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_prompt_contains_today_date(self):
        # Sanity check that the f-string substitution actually runs —
        # if the prompt were a regular string, ``{today}`` would
        # appear literally. After f-string substitution the literal
        # string ``{today}`` must NOT appear.
        prompt = main._build_system_prompt()
        assert "{today}" not in prompt
        assert "{current_year}" not in prompt
