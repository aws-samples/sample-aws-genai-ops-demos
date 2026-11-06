"""Unit tests for profile_dataset (local, no AWS)."""

from __future__ import annotations

from tools.profile_dataset import profile_dataset


def test_profile_detects_schema_and_counts(fixture_path: str) -> None:
    result = profile_dataset(fixture_path)

    assert result["format"] == "jsonl"
    assert result["total_records"] == 8
    assert result["detected_schema_pattern"] == "instruction_response"
    assert result["schema"]["schema_valid"] is True
    assert result["encoding"] in ("utf-8", "ascii")


def test_profile_counts_empty_records(fixture_path: str) -> None:
    result = profile_dataset(fixture_path)

    # One record has an empty response; one has a sub-10-token response is NOT empty text,
    # so only the empty-response record is counted as empty.
    assert result["empty_record_count"] >= 1
    assert "empty_records" in result["quality_flags"]


def test_profile_text_stats_present(fixture_path: str) -> None:
    result = profile_dataset(fixture_path)

    stats = result["text_stats"]
    assert stats["total_tokens"] > 0
    assert stats["max_record_tokens"] > 0
    assert "avg_record_tokens" in stats
