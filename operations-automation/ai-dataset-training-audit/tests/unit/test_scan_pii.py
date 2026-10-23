"""Unit tests for scan_pii (Comprehend mocked)."""

from __future__ import annotations

import tools.scan_pii as scan_pii_mod
from tests.conftest import FakeComprehendClient
from tools.generate_preflight_report import _build_findings
from tools.scan_pii import scan_pii


def test_pii_hit_sets_found_with_entity_counts(fixture_path: str, monkeypatch) -> None:
    fake = FakeComprehendClient()
    monkeypatch.setattr(scan_pii_mod, "_comprehend_client_factory", lambda: fake)

    result = scan_pii(fixture_path)

    assert result["scan_complete"] is True
    assert result["pii_found"] is True
    assert result["entity_type_counts"].get("EMAIL") == 1
    assert result["affected_record_count"] == 1


def test_access_denied_never_reports_clean(fixture_path: str, monkeypatch) -> None:
    fake = FakeComprehendClient(raise_error=Exception("AccessDeniedException: not allowed"))
    monkeypatch.setattr(scan_pii_mod, "_comprehend_client_factory", lambda: fake)

    result = scan_pii(fixture_path)

    assert result["scan_complete"] is False
    assert result["scan_error"] is not None
    assert result["pii_found"] is False

    # A non-completed scan must become the critical PII-000 finding — never "clean".
    findings = _build_findings(
        profile_result={"empty_record_count": 0, "encoding_warning": None},
        duplicates_result={"total_duplicate_count": 0},
        pii_result=result,
        viability_result={},
        cost_result={},
    )
    ids = {f["id"] for f in findings}
    assert "PII-000" in ids
    assert "PII-001" not in ids


def test_redacted_copy_requires_output_path(fixture_path: str, monkeypatch) -> None:
    fake = FakeComprehendClient()
    monkeypatch.setattr(scan_pii_mod, "_comprehend_client_factory", lambda: fake)

    import pytest

    with pytest.raises(ValueError):
        scan_pii(fixture_path, produce_redacted_copy=True, output_path="")


def test_redacted_copy_writes_new_file(fixture_path: str, tmp_path, monkeypatch) -> None:
    fake = FakeComprehendClient()
    monkeypatch.setattr(scan_pii_mod, "_comprehend_client_factory", lambda: fake)

    out = tmp_path / "redacted.jsonl"
    result = scan_pii(
        fixture_path,
        redaction_strategy="mask",
        produce_redacted_copy=True,
        output_path=str(out),
    )

    assert result["redacted_output_path"] == str(out)
    assert out.exists()
    assert "user@example.com" not in out.read_text(encoding="utf-8")
