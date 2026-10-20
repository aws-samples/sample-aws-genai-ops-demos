"""End-to-end tool-orchestration test with all AWS mocked.

Drives the fixed FR-4.1 tool chain (the same one offline mode runs) against the synthetic
fixture: Comprehend returns a scripted EMAIL entity, Bedrock falls back to the templated
narrative, and S3/DynamoDB persistence is stubbed with failures swallowed. Asserts a
complete report dict and the fixture's single expected outcome: PII-001 -> score <= 49 ->
NO-GO.
"""

from __future__ import annotations

import importlib
import importlib.util

import audit as audit_mod
import tools.generate_preflight_report as report_mod
import tools.scan_pii as scan_pii_mod
from config import Config
from tests.conftest import (
    FakeComprehendClient,
    FakeDynamoDBClient,
    FakeS3Client,
)
from tools.check_training_viability import check_training_viability
from tools.detect_duplicates import detect_duplicates
from tools.estimate_training_cost import estimate_training_cost
from tools.profile_dataset import profile_dataset


def _load_agent_module():
    """Resolve the agent entry-point module regardless of sys.path order.

    ``agent`` can resolve to the package (agent/__init__.py) or the module
    (agent/agent.py); the container imports the module, so prefer ``agent.agent``.
    """
    if importlib.util.find_spec("agent.agent") is not None:
        return importlib.import_module("agent.agent")
    return importlib.import_module("agent")


agent_mod = _load_agent_module()


def _run_chain(fixture_path: str) -> dict:
    target_model = "meta.llama3-70b-instruct-v1:0"
    profile_result = profile_dataset(fixture_path, target_model)
    duplicates_result = detect_duplicates(fixture_path, 0.85)
    pii_result = scan_pii_mod.scan_pii(fixture_path)
    viability_result = check_training_viability(fixture_path, target_model)
    cost_result = estimate_training_cost(
        profile_result["text_stats"]["total_tokens"],
        target_model=target_model,
        duplicate_rate=duplicates_result["duplicate_rate"],
    )
    return report_mod.generate_preflight_report(
        fixture_path,
        target_model,
        profile_result,
        duplicates_result,
        pii_result,
        viability_result,
        cost_result,
        run_id="dart-e2etest",
    )


def test_e2e_report_shape_and_verdict(fixture_path: str, monkeypatch) -> None:
    # Comprehend -> scripted EMAIL entity; Bedrock -> templated fallback.
    monkeypatch.setattr(
        scan_pii_mod, "_comprehend_client_factory", lambda: FakeComprehendClient()
    )

    def _raise() -> None:
        raise RuntimeError("bedrock disabled for e2e")

    monkeypatch.setattr(report_mod, "_bedrock_client_factory", _raise)

    report = _run_chain(fixture_path)

    # Complete report dict.
    assert report["run_id"] == "dart-e2etest"
    assert set(report).issuperset(
        {
            "recommendation",
            "quality_score",
            "findings",
            "findings_count",
            "cost_summary",
            "dataset_summary",
            "narrative",
        }
    )

    # Findings are sorted by severity (critical first).
    severities = [f["severity"] for f in report["findings"]]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert severities == sorted(severities, key=lambda s: order[s])

    # Fixture's single expected outcome: PII-001 -> score <= 49 -> NO-GO.
    assert any(f["id"] == "PII-001" for f in report["findings"])
    assert report["quality_score"] <= 49
    assert report["recommendation"] == "NO-GO"
    assert "DART Pre-flight Audit" in report["narrative"]


def test_persistence_failures_are_swallowed(monkeypatch) -> None:
    """Best-effort S3/DynamoDB persistence must swallow failures, never propagate."""
    import sys

    def _client(service: str, region_name: str = ""):  # noqa: ARG001
        if service == "s3":
            return FakeS3Client(raise_error=RuntimeError("s3 down"))
        if service == "dynamodb":
            return FakeDynamoDBClient(raise_error=RuntimeError("ddb down"))
        raise AssertionError(f"unexpected client: {service}")

    fake_boto3 = _FakeBoto3(_client)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setattr(Config, "OUTPUT_BUCKET", "dart-test-bucket")

    # Neither helper may raise; the audit write returns False on failure.
    agent_mod._persist_report_to_s3("dart-x", "## report")
    assert audit_mod.write_audit_record(run_id="dart-x", status="completed") is False


class _FakeBoto3:
    """Minimal boto3 shim exposing client()."""

    def __init__(self, client_factory) -> None:
        self._factory = client_factory

    def client(self, service: str, region_name: str = ""):
        return self._factory(service, region_name)
