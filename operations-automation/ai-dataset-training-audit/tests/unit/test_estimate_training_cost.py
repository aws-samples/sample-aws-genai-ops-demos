"""Unit tests for estimate_training_cost (local, no AWS)."""

from __future__ import annotations

from tools.estimate_training_cost import estimate_training_cost


def test_cost_before_after_and_savings() -> None:
    result = estimate_training_cost(
        total_tokens=10_000_000,
        target_model="meta.llama3-70b-instruct-v1:0",
        duplicate_rate=0.2,
        empty_record_rate=0.1,
    )

    assert result["cost_before_fixes"] > 0
    assert result["cost_after_fixes"] < result["cost_before_fixes"]
    assert result["savings_usd"] > 0
    # 30% wasteful -> ~30% savings.
    assert 29.0 <= result["savings_percent"] <= 31.0


def test_clean_dataset_has_no_savings() -> None:
    result = estimate_training_cost(
        total_tokens=1_000_000,
        duplicate_rate=0.0,
        empty_record_rate=0.0,
    )

    assert result["savings_usd"] == 0.0
    assert result["savings_percent"] == 0.0


def test_bedrock_managed_pricing_path() -> None:
    result = estimate_training_cost(
        total_tokens=1_000_000,
        target_model="amazon.nova-pro-v1:0",
        use_bedrock_managed=True,
        duplicate_rate=0.5,
    )

    assert result["method"] == "bedrock-managed"
    assert result["cost_before_fixes"] > 0
