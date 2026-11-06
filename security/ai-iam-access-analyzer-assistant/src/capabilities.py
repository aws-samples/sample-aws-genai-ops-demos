"""Session-start capability probe (#171 phase C).

Runs the same read-only checks that ``deploy-all.sh`` runs at deploy time
(introduced in #170) but from inside the Lambda, so the assistant can tell
the user — at the moment they open the chat — what it can actually see in
this account/region. When Security Hub is turned off, or an analyzer is
missing, or CloudTrail isn't reachable, the chat's opening line names the
gap instead of leading with a hardcoded feature list that the environment
doesn't support.

The endpoint is intentionally cheap: a handful of AWS calls in parallel
semantics (each try/except independent), sub-second in aggregate. Called
once when the chat opens; the tools themselves still emit per-call
coverage on every turn, so this probe is the header-level supplement to
the per-turn signal — not a replacement.

Also includes a passive, read-only AWS Organizations / Security Hub
delegated-admin check ("Level 1" of the multi-account work tracked in
#173) — no spoke roles, no cross-account assume-role, no new
infrastructure. It only reports what this account can already see about
its own org membership and Security Hub administrator relationship.

Output shape (illustrative example — actual region is whatever the Lambda
runs in, taken from ``AWS_REGION`` at cold start):

    {
      "region": "<region>",
      "coverage": [
        {"source": "securityhub", "state": "checked", "detail": "..."},
        {"source": "accessanalyzer", "state": "unavailable", "detail": "..."},
        {"source": "cloudtrail", "state": "checked", "detail": "..."},
        {"source": "organizations", "state": "checked", "detail": "..."}
      ],
      "welcome_message": "In <region> I can see external access findings ...",
      "org_id": "o-xxxxxxxxxx" | null,
      "is_delegated_admin": true | false | null
    }
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# The AWS_REGION env var is set automatically by the Lambda runtime. The
# "us-east-1" literal is a defensive fallback ONLY for the offline/unit-test
# path where AWS_REGION is not exported by the test runner; it must never
# reach production because a Lambda always has AWS_REGION populated. Same
# pattern as the sibling tools (see src/tools/export_report.py:24 and
# src/tools/list_exports.py:22).
_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Module-level boto3 clients so they persist across warm Lambda invocations
# — client construction is the expensive part (loads service model, sets up
# SigV4 signer), and re-using clients across invocations is the documented
# Lambda cold-start optimization pattern. The clients pick up credentials
# lazily from the execution role on first API call. The tests monkey-patch
# these module-level references directly (see tests/test_capabilities.py).
securityhub_client = boto3.client("securityhub")
accessanalyzer_client = boto3.client("accessanalyzer")
cloudtrail_client = boto3.client("cloudtrail")
organizations_client = boto3.client("organizations")


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

    org_status = _probe_org_detection(sh_status)
    coverage.extend(org_status["coverage"])

    welcome = _compose_welcome_message(sh_status, aa_status, ct_status, org_status)

    return {
        "region": _REGION,
        "coverage": coverage,
        "welcome_message": welcome,
        # Structured org fields, in addition to the prose already folded
        # into the "organizations" coverage entry's `detail` string above.
        # The frontend threads these into each /conversation POST as
        # `org_context` so the chat model can answer org/delegated-admin
        # questions directly instead of guessing from tool-result data it
        # has no business inferring org structure from (see #173 Level 1
        # follow-up: live testing on 2026-10-09 found the model, with no
        # access to this fact, confidently guessed the WRONG account as
        # delegated admin when asked directly).
        "org_id": org_status.get("org_id"),
        "is_delegated_admin": org_status.get("is_delegated_admin"),
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


# --------------------------------------------------------------- Organizations


def _probe_org_detection(sh: dict) -> dict:
    """Passive, read-only check for AWS Organizations membership (Level 1).

    This is deliberately NOT the Level 2 hub-and-spoke feature (#173) — no
    spoke role, no cross-account assume-role, no new infrastructure. It
    answers one narrow question: is this account part of an AWS
    Organization, and if Security Hub is enabled here, is this account the
    Security Hub delegated administrator (which means ``get_findings`` in
    ``_probe_security_hub`` above is already returning org-aggregated
    findings, not just this account's own findings — confirmed in practice
    via live testing against a 5-account org on 2026-10-09, where findings
    from 7 distinct accounts appeared from a single delegated-admin
    account's Security Hub query).

    ``describe_organization`` and ``describe_organization_configuration``
    are both control-plane calls with no resource-level authorization, same
    shape as the other probes in this module — see the IAM policy comment
    in ``api_construct.py`` for the ``resources=["*"]`` justification.

    Delegated-admin detection uses ``describe_organization_configuration``
    rather than ``get_administrator_account`` — per the AWS docs, "Only the
    Security Hub administrator account can invoke this operation," so a
    successful call IS the confirmation (not an inference from an empty
    response), and a failure means this account is a plain member (or SH
    isn't org-integrated at all). This exact behavior — success only from
    the delegated admin account, ``InvalidAccessException`` from any other
    account including the Organizations management account — was verified
    directly via AWS CLI on 2026-10-09 against this project's own 5-account
    org before this probe was written.
    """
    coverage: list = []
    result = {"in_org": False, "is_delegated_admin": None, "org_id": None, "coverage": coverage}

    try:
        org = organizations_client.describe_organization()["Organization"]
    except organizations_client.exceptions.AWSOrganizationsNotInUseException:
        coverage.append(_cov(
            "organizations", "unavailable",
            "this account is not part of an AWS Organization — "
            "findings and policy reads are limited to this single account",
        ))
        return result
    except Exception as e:
        coverage.append(_cov(
            "organizations", "unavailable",
            f"AWS Organizations check failed: {type(e).__name__}: {e}",
        ))
        return result

    result["in_org"] = True
    org_id = org.get("Id", "unknown")
    result["org_id"] = org_id

    # Only meaningful if Security Hub itself is enabled here (sh["enabled"]
    # from _probe_security_hub, run just before this). If SH is off, there
    # is nothing to be a delegated admin FOR, so skip the second call
    # entirely rather than report a misleading "not delegated admin".
    if not sh.get("enabled"):
        coverage.append(_cov(
            "organizations", "checked",
            f"account is part of AWS Organization {org_id}, but Security "
            "Hub is not enabled here, so org-wide findings aggregation "
            "cannot be confirmed",
        ))
        return result

    try:
        # Succeeds ONLY on the delegated admin account itself (AWS docs:
        # "Only the Security Hub administrator account can invoke this
        # operation") — a clean, authoritative signal, not an inference.
        securityhub_client.describe_organization_configuration()
        result["is_delegated_admin"] = True
        coverage.append(_cov(
            "organizations", "checked",
            f"account is part of AWS Organization {org_id} and is the "
            "Security Hub delegated administrator — findings from other "
            "member accounts in the org aggregate here",
        ))
    except securityhub_client.exceptions.InvalidAccessException:
        result["is_delegated_admin"] = False
        coverage.append(_cov(
            "organizations", "checked",
            f"account is part of AWS Organization {org_id}; Security Hub "
            "delegated administration is handled by a different account, "
            "so findings here are local to this account only",
        ))
    except Exception as e:
        # Non-fatal — we still know we're in an org, just not the admin
        # relationship. Report what we have rather than discarding it.
        coverage.append(_cov(
            "organizations", "checked",
            f"account is part of AWS Organization {org_id}; could not "
            f"determine Security Hub delegated-admin status: "
            f"{type(e).__name__}: {e}",
        ))

    return result


# --------------------------------------------------------------------- shared


def _cov(source: str, state: str, detail: str) -> dict:
    return {"source": source, "state": state, "detail": detail}


def _compose_welcome_message(sh: dict, aa: dict, ct: dict, org: dict | None = None) -> str:
    """Compose an honest welcome message from the probe results.

    Follows Ben's example in #171: name what CAN be seen and what CANNOT,
    with the specific region so a wrong-region deploy is obvious.

    ``org`` is optional (defaults to None) so existing callers/tests that
    only pass the original three probes keep working unchanged.
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

    if org and org.get("in_org"):
        if org.get("is_delegated_admin") is True:
            parts.append(
                "this account is the Security Hub delegated administrator "
                "for its AWS Organization, so the findings above are "
                "aggregated across every member account, not just this one"
            )
        elif org.get("is_delegated_admin") is False:
            parts.append(
                "this account is part of an AWS Organization, but a "
                "different account is the Security Hub delegated "
                "administrator, so the findings above are local to this "
                "account only"
            )
        # is_delegated_admin is None (SH disabled, or the admin check
        # itself failed) — org membership alone isn't worth a sentence on
        # its own; the SH-disabled or SH-error sentences above already
        # cover why findings are limited.

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
