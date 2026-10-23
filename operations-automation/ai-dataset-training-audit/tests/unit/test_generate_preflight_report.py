"""Unit tests for generate_preflight_report (Bedrock mocked)."""

from __future__ import annotations

import json

from botocore.exceptions import ClientError

import tools.generate_preflight_report as report_mod
from tests.conftest import FakeBedrockClient
from tools.generate_preflight_report import (
    _determine_recommendation,
    generate_preflight_report,
)


def _clean_inputs() -> dict:
    return {
        "profile_result": {
            "total_records": 1000,
            "empty_record_count": 0,
            "encoding_warning": None,
            "format": "jsonl",
            "detected_schema_pattern": "instruction_response",
        },
        "duplicates_result": {
            "total_duplicate_count": 0,
            "exact_duplicate_count": 0,
            "near_duplicate_count": 0,
            "duplicate_rate": 0.0,
        },
        "pii_result": {"pii_found": False, "scan_complete": True, "scan_error": None},
        "viability_result": {
            "schema_check": {"schema_valid": True},
            "token_budget": {"records_exceeding_context": 0, "context_limit": 8192},
            "leakage_check": {"leakage_detected": False},
            "size_check": {"meets_minimum": True, "minimum_recommended": 500},
        },
        "cost_result": {"savings_percent": 0.0, "cost_before_fixes": 10.0},
    }


def test_clean_dataset_is_go(monkeypatch) -> None:
    monkeypatch.setattr(report_mod, "_bedrock_client_factory", lambda: FakeBedrockClient())
    report = generate_preflight_report(
        "s3://bucket/train.jsonl", "meta.llama3-8b-instruct-v1:0", **_clean_inputs()
    )
    assert report["quality_score"] >= 80
    assert report["recommendation"] == "GO"


def test_critical_pii_forces_no_go(monkeypatch) -> None:
    monkeypatch.setattr(report_mod, "_bedrock_client_factory", lambda: FakeBedrockClient())
    inputs = _clean_inputs()
    inputs["pii_result"] = {
        "pii_found": True,
        "scan_complete": True,
        "affected_record_count": 1,
        "entity_type_counts": {"EMAIL": 1},
    }

    report = generate_preflight_report(
        "s3://bucket/train.jsonl", "meta.llama3-8b-instruct-v1:0", **inputs
    )

    assert report["quality_score"] <= 49
    assert report["recommendation"] == "NO-GO"
    # Score and verdict are mutually consistent.
    assert _determine_recommendation(report["quality_score"]) == report["recommendation"]
    assert report["findings"][0]["severity"] == "critical"


def test_bedrock_payload_has_no_raw_records(monkeypatch) -> None:
    fake = FakeBedrockClient()
    monkeypatch.setattr(report_mod, "_bedrock_client_factory", lambda: fake)

    inputs = _clean_inputs()
    inputs["pii_result"] = {
        "pii_found": True,
        "scan_complete": True,
        "affected_record_count": 1,
        "entity_type_counts": {"EMAIL": 1},
    }
    generate_preflight_report(
        "s3://bucket/secret-records.jsonl", "meta.llama3-8b-instruct-v1:0", **inputs
    )

    assert fake.calls, "Bedrock should have been invoked"
    sent = fake.calls[0]["body"]
    payload = json.loads(sent)
    prompt_text = payload["messages"][0]["content"]
    # Only finding summaries + metadata are present; no raw record field names/values.
    assert '"instruction"' not in prompt_text
    assert '"completion"' not in prompt_text
    assert "findings" in prompt_text
    # The structured payload exposes only summary keys, never a records array.
    structured = json.loads(prompt_text.split("\n\n", 1)[1])
    assert set(structured.keys()) == {"dataset_metadata", "findings"}
    for finding in structured["findings"]:
        assert set(finding.keys()) <= {
            "severity",
            "title",
            "detail",
            "action",
            "auto_fixable",
        }


def test_templated_fallback_on_client_error(monkeypatch) -> None:
    err = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "InvokeModel")
    monkeypatch.setattr(
        report_mod, "_bedrock_client_factory", lambda: FakeBedrockClient(raise_error=err)
    )

    report = generate_preflight_report(
        "s3://bucket/train.jsonl", "meta.llama3-8b-instruct-v1:0", **_clean_inputs()
    )

    assert "DART Pre-flight Audit" in report["narrative"]


def test_incomplete_scan_forces_no_go(monkeypatch) -> None:
    monkeypatch.setattr(report_mod, "_bedrock_client_factory", lambda: FakeBedrockClient())
    inputs = _clean_inputs()
    inputs["pii_result"] = {"pii_found": False, "scan_complete": False, "scan_error": "denied"}

    report = generate_preflight_report(
        "s3://bucket/train.jsonl", "meta.llama3-8b-instruct-v1:0", **inputs
    )

    assert report["quality_score"] <= 49
    assert report["recommendation"] == "NO-GO"
    assert any(f["id"] == "PII-000" for f in report["findings"])
