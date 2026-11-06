"""Unit tests for detect_duplicates (local, no AWS)."""

from __future__ import annotations

from tools.detect_duplicates import detect_duplicates, duplicate_indices


def test_detects_exact_and_near_duplicates(fixture_path: str) -> None:
    result = detect_duplicates(fixture_path, similarity_threshold=0.85)

    assert result["exact_duplicate_count"] == 1
    assert result["near_duplicate_count"] == 1
    assert result["total_duplicate_count"] == 2
    assert result["duplicate_rate"] == 0.25
    assert result["similarity_threshold_used"] == 0.85


def test_duplicate_indices_helper() -> None:
    texts = ["hello world", "hello world", "something completely different here"]
    indices = duplicate_indices(texts, similarity_threshold=0.85, num_perm=128)

    # The second identical record is a duplicate; the distinct record is not.
    assert 1 in indices
    assert 2 not in indices
