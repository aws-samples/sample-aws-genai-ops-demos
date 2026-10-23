"""Unit tests for apply_safe_fixes (local, no AWS)."""

from __future__ import annotations

from pathlib import Path

from tools.apply_safe_fixes import apply_safe_fixes


def test_original_unchanged_and_output_cleaned(fixture_path: str, tmp_path) -> None:
    before_bytes = Path(fixture_path).read_bytes()

    out = tmp_path / "cleaned.jsonl"
    result = apply_safe_fixes(fixture_path, output_path=str(out))

    # The original fixture file must be byte-for-byte unchanged.
    assert Path(fixture_path).read_bytes() == before_bytes

    # Output has duplicates and empties removed.
    assert out.exists()
    assert result["records_before"] == 8
    assert result["exact_duplicates_removed"] == 1
    assert result["empty_records_removed"] >= 1
    assert result["records_after"] < result["records_before"]
    assert result["output_path"] == str(out)


def test_auto_generated_output_path(fixture_path: str) -> None:
    result = apply_safe_fixes(fixture_path)

    out = Path(result["output_path"])
    try:
        assert out.exists()
        assert out != Path(fixture_path)
        assert ".cleaned" in out.name
    finally:
        out.unlink(missing_ok=True)
