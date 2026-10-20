"""Unit tests for check_training_viability (local, no AWS)."""

from __future__ import annotations

from tools.check_training_viability import check_training_viability


def test_viability_size_and_schema(fixture_path: str) -> None:
    result = check_training_viability(fixture_path, target_model="meta.llama3-70b-instruct-v1:0")

    assert result["schema_check"]["schema_valid"] is True
    # 8 records << 3000 recommended for a 70b model.
    assert result["size_check"]["meets_minimum"] is False
    assert result["size_check"]["minimum_recommended"] == 3000
    # Tiny records fit easily in an 8192-token context.
    assert result["token_budget"]["within_budget"] is True
    assert result["token_budget"]["records_exceeding_context"] == 0


def test_viability_unknown_model_uses_default_context(fixture_path: str) -> None:
    result = check_training_viability(fixture_path, target_model="some-unknown-model")

    # Degrade safely to the default context limit (8192) and default min records (500).
    assert result["token_budget"]["context_limit"] == 8192
    assert result["size_check"]["minimum_recommended"] == 500


def test_leakage_detected_against_validation(fixture_path: str) -> None:
    # Using the same file as validation path guarantees overlap.
    result = check_training_viability(
        fixture_path,
        target_model="meta.llama3-70b-instruct-v1:0",
        validation_path=fixture_path,
    )

    assert result["leakage_check"]["checked"] is True
    assert result["leakage_check"]["leakage_detected"] is True
    assert result["overall_viable"] is False
