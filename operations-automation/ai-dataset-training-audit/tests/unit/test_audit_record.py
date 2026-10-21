"""Unit tests for the audit-trail record wiring (PR review feedback).

A completed run must persist the verdict (recommendation) and quality score on the
DynamoDB audit item so the row is a self-contained EU AI Act Article 10 compliance
record. These tests cover ``write_audit_record``'s own field mapping and the free-text
agent path's narrow verdict/score extraction + safe fallback. All boto3 use is mocked, so
the suite runs with no AWS credentials.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any

import audit as audit_mod
from config import Config
from tests.conftest import FakeDynamoDBClient


def _load_agent_module() -> Any:
    if importlib.util.find_spec("agent.agent") is not None:
        return importlib.import_module("agent.agent")
    return importlib.import_module("agent")


agent_mod = _load_agent_module()


class _FakeBoto3:
    """Minimal boto3 shim returning a single recording DynamoDB client."""

    def __init__(self, ddb_client: FakeDynamoDBClient) -> None:
        self._ddb = ddb_client

    def client(self, service: str, region_name: str = ""):  # noqa: ARG002
        assert service == "dynamodb"
        return self._ddb


def test_write_audit_record_persists_verdict_and_score(monkeypatch) -> None:
    """A completed row carries recommendation (S) and quality_score (N)."""
    ddb = FakeDynamoDBClient()
    monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3(ddb))
    monkeypatch.setattr(Config, "AUDIT_TABLE", "dart-test-table")

    ok = audit_mod.write_audit_record(
        run_id="dart-verdict",
        status="completed",
        recommendation="NO-GO",
        quality_score=49,
    )

    assert ok is True
    assert len(ddb.items) == 1
    item = ddb.items[0]["Item"]
    assert item["status"] == {"S": "completed"}
    assert item["recommendation"] == {"S": "NO-GO"}
    assert item["quality_score"] == {"N": "49"}


def test_write_audit_record_omits_verdict_when_none(monkeypatch) -> None:
    """The error path passes no verdict/score; those keys must be absent."""
    ddb = FakeDynamoDBClient()
    monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3(ddb))
    monkeypatch.setattr(Config, "AUDIT_TABLE", "dart-test-table")

    audit_mod.write_audit_record(run_id="dart-err", status="error")

    item = ddb.items[0]["Item"]
    assert item["status"] == {"S": "error"}
    assert "recommendation" not in item
    assert "quality_score" not in item


def test_extract_verdict_and_score_from_rendered_header() -> None:
    """The _render_report header line is parsed into (verdict, score)."""
    text = "Quality score: 49/100 — Recommendation: NO-GO\n\n## narrative"
    assert agent_mod._extract_verdict_and_score(text) == ("NO-GO", 49)


def test_extract_verdict_and_score_from_templated_narrative() -> None:
    """The templated narrative's bold lines are parsed into (verdict, score)."""
    text = "## DART Pre-flight Audit\n\n**Verdict:** GO WITH FIXES  \n**Quality score:** 72/100\n"
    assert agent_mod._extract_verdict_and_score(text) == ("GO WITH FIXES", 72)


def test_extract_verdict_and_score_falls_back_to_none() -> None:
    """Unparseable free text yields (None, None) rather than raising."""
    assert agent_mod._extract_verdict_and_score("The dataset looks fine overall.") == (
        None,
        None,
    )
