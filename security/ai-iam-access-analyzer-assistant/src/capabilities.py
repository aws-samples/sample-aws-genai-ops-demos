"""Session-start capability probe (#171 phase C).

Runs the same read-only checks that ``deploy-all.sh`` runs at deploy time
(introduced in #170) but from inside the Lambda, so the assistant can tell
the user — at the moment they open the chat — what it can actually see in
this account/region. When Security Hub is turned off, or an analyzer is
missing, or CloudTrail isn't reachable, the chat's opening line names the
gap instead of leading with a hardcoded feature list that the environment
doesn't support.

The endpoint is intentionally cheap: 3–5 AWS calls in parallel semantics
(each try/except independent), sub-second in aggregate. Called once when
the chat opens; the tools themselves still emit per-call coverage on
every turn, so this probe is the header-level supplement to the per-turn
signal — not a replacement.

Output shape:

    {
      "region": "us-east-1",
      "coverage": [
        {"source": "securityhub", "state": "checked", "detail": "..."},
        {"source": "accessanalyzer", "state": "unavailable", "detail": "..."},
        {"source": "cloudtrail", "state": "checked", "detail": "..."}
      ],
      "welcome_message": "In us-east-1 I can see external access findings ..."
    }
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_REGION = os.environ.get("AWS_REGION", "us-east-1")

securityhub_client = boto3.client("securityhub")
accessanalyzer_client = boto3.client("accessanalyzer")
cloudtrail_client = boto3.client("cloudtrail")


def handler(event, context=None):
    """API Gateway proxy handler for GET /capabilities."""
    try:
        payload = _probe()
        return {
            "statusCode": 200,
            "headers": _cors_headers(),
            "body": json.dumps(payload),
        }
    except Exception as e:
        logger.error(f"capabilities probe failed unexpectedly: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": _cors_headers(),
            "body": json.dumps({"error": str(e)}),
        }


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Content-Type": "application/json",
    }


def _probe() -> dict:
    """Run all checks and produce the response payload."""
    coverage: list = []

    sh_status = _probe_security_hub()
    coverage.extend(sh_status["coverage"])

    aa_status = _probe_access_analyzer()
    coverage.extend(aa_status["coverage"])

    ct_status = _probe_cloudtrail()
    coverage.extend(ct_status["coverage"])

    welcome = _compose_welcome_message(sh_status, aa_status, ct_status)

    return {
        "region": _REGION,
        "coverage": coverage,
        "welcome_message": welcome,
    }


# --------------------------------------------------------------- Security Hub


def _probe_security_hub() -> dict:
    """Check Security Hub availability and Access Analyzer integration.

    Returns a dict with a ``coverage`` list (one or more entries) and
    higher-level flags used by the welcome message composer.
    """
    coverage: list = []
    result = {"enabled": False, "integration": False, "active_findings": None, "coverage": coverage}

    # 1. Is Security Hub enabled?
    try:
        response = securityhub_client.describe_hub()
        result["enabled"] = True
        subscribed_at = (response.get("SubscribedAt") or "")[:10]
        detail = f"Security Hub enabled in {_REGION}"
        if subscribed_at:
            detail += f" since {subscribed_at}"
        coverage.append(_cov("securityhub", "checked", detail))
    except securityhub_client.exceptions.InvalidAccessException:
        coverage.append(_cov(
            "securityhub", "unavailable",
            f"Security Hub not enabled in {_REGION}",
        ))
        return result
    except Exception as e:
        coverage.append(_cov(
            "securityhub", "unavailable",
            f"Security Hub check failed in {_REGION}: {type(e).__name__}: {e}",
        ))
        return result

    # 2. Is the IAM Access Analyzer → Security Hub integration on?
    try:
        products_resp = securityhub_client.list_enabled_products_for_import()
        products = products_resp.get("ProductSubscriptions", [])
        has_analyzer_feed = any("access-analyzer" in p for p in products)
        result["integration"] = has_analyzer_feed
        if not has_analyzer_feed:
            coverage.append(_cov(
                "securityhub", "unavailable",
                "IAM Access Analyzer → Security Hub integration is switched off "
                "(findings from Access Analyzer will not reach this assistant)",
            ))
    except Exception as e:
        # Non-fatal: SH is up, we just couldn't check the integration.
        logger.warning("could not check Access Analyzer → SH integration: %s", e)

    # 3. Count of active IAM Access Analyzer findings (informational).
    try:
        findings_resp = securityhub_client.get_findings(
            Filters={
                "ProductName": [{"Value": "IAM Access Analyzer", "Comparison": "EQUALS"}],
                "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
                "WorkflowStatus": [{"Value": "NEW", "Comparison": "EQUALS"}],
            },
            MaxResults=1,
        )
        # MaxResults=1 gives us a page; we only need to know >0. The NextToken
        # presence tells us there ARE more, but for the header we just want a
        # rough size signal.
        result["active_findings"] = len(findings_resp.get("Findings", []))
        if findings_resp.get("NextToken"):
            result["active_findings"] = "many"  # >1; the header won't quote an exact count
    except Exception as e:
        logger.warning("could not sample active findings count: %s", e)

    return result


# ------------------------------------------------------------ Access Analyzer


def _probe_access_analyzer() -> dict:
    """List active analyzers and note which kinds are present."""
    coverage: list = []
    result = {"external": None, "unused": None, "coverage": coverage}

    try:
        analyzers_resp = accessanalyzer_client.list_analyzers()
    except Exception as e:
        coverage.append(_cov(
            "accessanalyzer", "unavailable",
            f"Could not list analyzers in {_REGION}: {type(e).__name__}: {e}",
        ))
        return result

    active = [
        a for a in analyzers_resp.get("analyzers", [])
        if a.get("status") == "ACTIVE"
    ]

    external = next(
        (a for a in active if "UNUSED_ACCESS" not in a.get("type", "")),
        None,
    )
    unused = next(
        (a for a in active if "UNUSED_ACCESS" in a.get("type", "")),
        None,
    )
    result["external"] = external
    result["unused"] = unused

    if external:
        coverage.append(_cov(
            "accessanalyzer", "checked",
            f"external-access analyzer active in {_REGION}: "
            f"{external.get('name')} ({external.get('type')})",
        ))
    else:
        coverage.append(_cov(
            "accessanalyzer", "unavailable",
            f"no external-access analyzer in {_REGION} — public and cross-account "
            "access findings cannot appear",
        ))

    if unused:
        coverage.append(_cov(
            "accessanalyzer", "checked",
            f"unused-access analyzer active in {_REGION}: "
            f"{unused.get('name')} ({unused.get('type')})",
        ))
    else:
        coverage.append(_cov(
            "accessanalyzer", "unavailable",
            f"no unused-access analyzer in {_REGION} — unused roles and "
            "permissions cannot be reported",
        ))

    return result


# ------------------------------------------------------------------ CloudTrail


def _probe_cloudtrail() -> dict:
    """Confirm we can call ``cloudtrail:LookupEvents``.

    The check is a single-item lookup — the response is discarded. What we
    care about is whether the API call succeeds; if it does, the tool role
    has the permission and CloudTrail is reachable from this region.
    """
    coverage: list = []
    result = {"reachable": False, "coverage": coverage}

    try:
        cloudtrail_client.lookup_events(MaxResults=1)
        result["reachable"] = True
        coverage.append(_cov(
            "cloudtrail", "checked",
            f"CloudTrail LookupEvents reachable in {_REGION}",
        ))
    except Exception as e:
        coverage.append(_cov(
            "cloudtrail", "unavailable",
            f"CloudTrail LookupEvents not reachable in {_REGION}: "
            f"{type(e).__name__}: {e}",
        ))
    return result


# --------------------------------------------------------------------- shared


def _cov(source: str, state: str, detail: str) -> dict:
    return {"source": source, "state": state, "detail": detail}


def _compose_welcome_message(sh: dict, aa: dict, ct: dict) -> str:
    """Compose an honest welcome message from the probe results.

    Follows Ben's example in #171: name what CAN be seen and what CANNOT,
    with the specific region so a wrong-region deploy is obvious.
    """
    parts: list = []

    if sh["enabled"] and sh.get("integration") is not False:
        active = sh.get("active_findings")
        if isinstance(active, int) and active > 0:
            parts.append(
                f"I can see IAM Access Analyzer findings via Security Hub in "
                f"{_REGION} (recent activity present)"
            )
        elif active == "many":
            parts.append(
                f"I can see IAM Access Analyzer findings via Security Hub in "
                f"{_REGION} (multiple active findings)"
            )
        elif isinstance(active, int) and active == 0:
            parts.append(
                f"Security Hub is enabled in {_REGION} but I do not see any "
                "active IAM Access Analyzer findings right now"
            )
        else:
            parts.append(f"Security Hub is enabled in {_REGION}")
    elif not sh["enabled"]:
        parts.append(
            f"Security Hub is NOT enabled in {_REGION} — I cannot report any "
            "findings until it is turned on"
        )
    elif sh.get("integration") is False:
        parts.append(
            f"Security Hub is enabled in {_REGION} but the IAM Access "
            "Analyzer integration is switched off, so findings from Access "
            "Analyzer will not reach this assistant"
        )

    if aa.get("unused") is None:
        parts.append(
            f"there is NO unused-access analyzer in {_REGION}, so I cannot "
            "report unused roles or permissions"
        )
    if aa.get("external") is None:
        parts.append(
            f"there is NO external-access analyzer in {_REGION}, so public "
            "and cross-account access findings cannot appear"
        )

    if not ct.get("reachable"):
        parts.append(
            f"CloudTrail LookupEvents is NOT reachable in {_REGION} — I "
            "cannot analyze which permissions a role actually uses, so "
            "least-privilege policy generation would be unsafe"
        )
    elif aa.get("unused") is not None or aa.get("external") is not None:
        parts.append(
            "CloudTrail is reachable, so I can generate least-privilege "
            "policies from observed usage"
        )

    if not parts:
        return f"Data-source status is unclear in {_REGION}. Ask me anything, and I'll be explicit about what I could and couldn't check."

    body = ". ".join(_capitalize_first(p) for p in parts) + "."
    return body


def _capitalize_first(sentence: str) -> str:
    return sentence[:1].upper() + sentence[1:] if sentence else sentence
