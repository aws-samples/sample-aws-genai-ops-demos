"""Unit tests for the Support-tier step worker (backend/support.py).

The per-account unit must: reuse discover.support_tier (not reimplement the
ceiling), map Business/Enterprise -> ta_eligible True and everything else ->
False, and never raise even when the Support API fails."""
from unittest.mock import MagicMock

import discover
import support


def _determine(monkeypatch, tier_info):
    monkeypatch.setattr(discover, "support_tier", lambda session, region: tier_info)
    return support.determine_tier(MagicMock(), "111111111111", "prod", "us-east-1")


def test_determine_tier_business_is_eligible(monkeypatch):
    out = _determine(monkeypatch, {"tier": "business", "reason": None, "ta_available": True})
    assert out == {"account_id": "111111111111", "account_name": "prod",
                   "tier": "business", "ta_eligible": True, "reason": None}


def test_determine_tier_enterprise_is_eligible(monkeypatch):
    out = _determine(monkeypatch, {"tier": "enterprise", "reason": None, "ta_available": True})
    assert out["ta_eligible"] is True
    assert out["tier"] == "enterprise"


def test_determine_tier_basic_is_not_eligible(monkeypatch):
    out = _determine(monkeypatch, {"tier": "basic", "reason": None, "ta_available": False})
    assert out["ta_eligible"] is False
    assert out["tier"] == "basic"


def test_determine_tier_developer_is_not_eligible(monkeypatch):
    out = _determine(monkeypatch, {"tier": "developer", "reason": None, "ta_available": False})
    assert out["ta_eligible"] is False


def test_determine_tier_unknown_carries_reason(monkeypatch):
    out = _determine(monkeypatch, {"tier": "unknown",
                                   "reason": "Throttling: rate exceeded", "ta_available": False})
    assert out["ta_eligible"] is False
    assert out["tier"] == "unknown"
    assert out["reason"] == "Throttling: rate exceeded"


def test_determine_tier_never_raises(monkeypatch):
    # support_tier is contractually non-raising; determine_tier must stay so too.
    # Here support_tier returns an unknown/erroring result rather than raising,
    # which is exactly how the Support-API failure path is modeled.
    out = _determine(monkeypatch, {"tier": "unknown", "reason": "boom", "ta_available": False})
    assert out["ta_eligible"] is False
