"""
Support-tier inference as its OWN durable-step unit (one per account).

Split out of discover.py's discover_scope so that Trusted Advisor availability
is decided ONCE per account, in its own durable step that can retry in
isolation (the Support API is the flakiest call in the pipeline). discover then
only runs for the accounts this step marks TA-eligible — but the accounts it
rules out are NOT dropped: their tier/reason flow forward so the summary still
reports them as "not checked", exactly as before the split.

This module holds the thin, step-callable per-account worker. The ceiling
mapping (SubscriptionRequiredException->basic, normal->developer,
urgent/high->business, critical->enterprise) is NOT reimplemented here: it lives
in discover.support_tier() and is reused verbatim via that call, so there is a
single source of truth for the Support-tier logic.
"""

from typing import Any, Dict

import boto3

import discover


def determine_tier(session: boto3.session.Session, account_id: str,
                   account_name: str, region: str) -> Dict[str, Any]:
    """Infer one account's Support tier and whether Trusted Advisor is usable.

    Reuses discover.support_tier() (which never raises) for the actual ceiling
    inference, then shapes the per-account record the pipeline fans in:

        {account_id, account_name, tier, ta_eligible, reason}

    ta_eligible mirrors support_tier's ta_available (tier in Business/Enterprise).
    Never raises: a Support-API problem surfaces as tier "unknown"/"basic" with a
    reason and ta_eligible False, so the caller can isolate and report it.
    """
    tier_info = discover.support_tier(session, region)
    return {
        "account_id": account_id,
        "account_name": account_name,
        "tier": tier_info["tier"],
        "ta_eligible": bool(tier_info["ta_available"]),
        "reason": tier_info.get("reason"),
    }
