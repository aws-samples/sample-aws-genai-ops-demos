"""Tool: Compare multiple IAM roles side-by-side.

Analyzes 2-5 roles simultaneously, comparing their risk profiles,
usage patterns, permissions, and trust relationships to help users
prioritize which roles to address first.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

iam_client = boto3.client("iam")
cloudtrail_client = boto3.client("cloudtrail")


def handler(event, context=None):
    """Compare multiple IAM roles side-by-side.

    Args:
        event: {
            role_names: list[str] - 2-5 role names to compare (required)
            lookback_days: int - days of activity to check (default: 90)
            compare_by: str - focus: permissions, usage, trust, risk, all (default: all)
        }

    Returns:
        {
            comparison: [{role_name, metrics...}],
            rankings: {most_risky, least_used, most_permissive, safest_to_delete},
            summary: str,
            recommendation: str
        }
    """
    role_names = event.get("role_names", [])
    if not role_names or len(role_names) < 2:
        return {"error": "At least 2 role_names are required"}
    if len(role_names) > 5:
        return {"error": "Maximum 5 roles can be compared at once"}

    lookback_days = event.get("lookback_days", 90)
    compare_by = event.get("compare_by", "all")

    try:
        comparisons = []

        for role_name in role_names:
            role_analysis = _analyze_role(role_name, lookback_days, compare_by)
            comparisons.append(role_analysis)

        # Generate rankings
        rankings = _generate_rankings(comparisons)

        # Generate summary recommendation
        recommendation = _generate_recommendation(comparisons, rankings)

        return {
            "comparison": comparisons,
            "rankings": rankings,
            "role_count": len(comparisons),
            "lookback_days": lookback_days,
            "recommendation": recommendation,
        }

    except Exception as e:
        logger.error(f"Error comparing roles: {e}", exc_info=True)
        return {"error": str(e)}


def _analyze_role(role_name: str, lookback_days: int, compare_by: str) -> dict:
    """Analyze a single role for comparison."""
    result = {
        "role_name": role_name,
        "exists": True,
        "risk_score": 0,
        "risk_factors": [],
    }

    try:
        # Get role info
        role_response = iam_client.get_role(RoleName=role_name)
        role = role_response["Role"]
        result["arn"] = role["Arn"]
        result["created"] = str(role["CreateDate"])
        result["last_used"] = str(role.get("RoleLastUsed", {}).get("LastUsedDate", "Never"))
        result["age_days"] = (datetime.now(timezone.utc) - role["CreateDate"]).days

        # Permissions analysis
        if compare_by in ("permissions", "all"):
            result["permissions"] = _get_permissions_summary(role_name)

        # Usage analysis
        if compare_by in ("usage", "all"):
            result["usage"] = _get_usage_summary(role_name, lookback_days)

        # Trust analysis
        if compare_by in ("trust", "all"):
            result["trust"] = _analyze_trust(role)

        # Calculate risk score
        result["risk_score"] = _calculate_risk(result)
        result["risk_level"] = (
            "CRITICAL" if result["risk_score"] >= 80
            else "HIGH" if result["risk_score"] >= 60
            else "MEDIUM" if result["risk_score"] >= 40
            else "LOW"
        )

    except iam_client.exceptions.NoSuchEntityException:
        result["exists"] = False
        result["error"] = f"Role '{role_name}' not found"

    except Exception as e:
        result["error"] = str(e)

    return result


def _get_permissions_summary(role_name: str) -> dict:
    """Summarize permissions attached to a role."""
    managed_policies = []
    inline_count = 0
    total_actions = 0
    has_admin = False
    has_wildcards = False

    try:
        # Managed policies
        attached = iam_client.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            managed_policies.append(policy["PolicyName"])
            if "Admin" in policy["PolicyName"] or "FullAccess" in policy["PolicyName"]:
                has_admin = True

            # Count actions in this policy
            try:
                policy_info = iam_client.get_policy(PolicyArn=policy["PolicyArn"])
                version = iam_client.get_policy_version(
                    PolicyArn=policy["PolicyArn"],
                    VersionId=policy_info["Policy"]["DefaultVersionId"],
                )
                doc = version["PolicyVersion"]["Document"]
                for stmt in doc.get("Statement", []):
                    if stmt.get("Effect") == "Allow":
                        actions = stmt.get("Action", [])
                        if isinstance(actions, str):
                            actions = [actions]
                        total_actions += len(actions)
                        if "*" in actions:
                            has_wildcards = True
            except Exception:
                pass

        # Inline policies
        inline = iam_client.list_role_policies(RoleName=role_name)
        inline_count = len(inline.get("PolicyNames", []))

    except Exception as e:
        return {"error": str(e)}

    return {
        "managed_policies": managed_policies,
        "managed_policy_count": len(managed_policies),
        "inline_policy_count": inline_count,
        "total_actions_granted": total_actions,
        "has_admin_access": has_admin,
        "has_wildcard_actions": has_wildcards,
    }


def _get_usage_summary(role_name: str, lookback_days: int) -> dict:
    """Check CloudTrail for recent usage."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=lookback_days)
    event_count = 0
    services_used = set()

    try:
        paginator = cloudtrail_client.get_paginator("lookup_events")
        for page in paginator.paginate(
            LookupAttributes=[
                {"AttributeKey": "Username", "AttributeValue": role_name},
            ],
            StartTime=start_time,
            EndTime=end_time,
            PaginationConfig={"MaxItems": 200, "PageSize": 50},
        ):
            for event in page.get("Events", []):
                event_count += 1
                source = event.get("EventSource", "").replace(".amazonaws.com", "")
                services_used.add(source)

    except Exception as e:
        return {"error": str(e), "event_count": 0}

    return {
        "event_count": event_count,
        "services_used": sorted(services_used),
        "services_count": len(services_used),
        "is_active": event_count > 0,
        "activity_level": (
            "heavy" if event_count > 100
            else "moderate" if event_count > 10
            else "minimal" if event_count > 0
            else "inactive"
        ),
    }


def _analyze_trust(role: dict) -> dict:
    """Analyze trust policy."""
    trust_doc = role.get("AssumeRolePolicyDocument", {})
    principals = []
    is_publicly_assumable = False
    is_cross_account = False

    for stmt in trust_doc.get("Statement", []):
        if stmt.get("Effect") != "Allow":
            continue
        principal = stmt.get("Principal", {})
        conditions = stmt.get("Condition", {})

        if isinstance(principal, str):
            if principal == "*":
                is_publicly_assumable = not bool(conditions)
            principals.append({"value": principal, "type": "wildcard"})
        elif isinstance(principal, dict):
            for p_type, p_values in principal.items():
                values = p_values if isinstance(p_values, list) else [p_values]
                for v in values:
                    principals.append({"value": v, "type": p_type})
                    if p_type == "AWS" and v == "*":
                        is_publicly_assumable = not bool(conditions)
                    elif p_type == "AWS" and ":root" in v:
                        is_cross_account = True

    return {
        "principals": principals,
        "principal_count": len(principals),
        "is_publicly_assumable": is_publicly_assumable,
        "is_cross_account": is_cross_account,
        "has_conditions": any(
            bool(s.get("Condition")) for s in trust_doc.get("Statement", [])
        ),
    }


def _calculate_risk(analysis: dict) -> int:
    """Calculate a 0-100 risk score from the analysis."""
    score = 0

    # Permissions risk
    perms = analysis.get("permissions", {})
    if perms.get("has_admin_access"):
        score += 30
        analysis["risk_factors"].append("Has admin/full access policies")
    if perms.get("has_wildcard_actions"):
        score += 20
        analysis["risk_factors"].append("Has wildcard (*) actions")
    if perms.get("total_actions_granted", 0) > 50:
        score += 10
        analysis["risk_factors"].append(f"{perms.get('total_actions_granted')} actions granted")

    # Usage risk (unused = higher risk because it's unnecessary attack surface)
    usage = analysis.get("usage", {})
    if not usage.get("is_active"):
        score += 20
        analysis["risk_factors"].append("Inactive — unused permissions are unnecessary attack surface")
    elif usage.get("activity_level") == "minimal":
        score += 10
        analysis["risk_factors"].append("Minimal activity — may be over-permissioned")

    # Trust risk
    trust = analysis.get("trust", {})
    if trust.get("is_publicly_assumable"):
        score += 30
        analysis["risk_factors"].append("CRITICAL: Publicly assumable without conditions")
    elif trust.get("is_cross_account") and not trust.get("has_conditions"):
        score += 15
        analysis["risk_factors"].append("Cross-account trust without conditions")

    return min(score, 100)


def _generate_rankings(comparisons: list) -> dict:
    """Generate rankings across the compared roles."""
    valid = [c for c in comparisons if c.get("exists")]
    if not valid:
        return {}

    rankings = {}

    # Most risky
    by_risk = sorted(valid, key=lambda x: x.get("risk_score", 0), reverse=True)
    rankings["most_risky"] = {
        "role": by_risk[0]["role_name"],
        "score": by_risk[0]["risk_score"],
        "level": by_risk[0].get("risk_level", "UNKNOWN"),
    }

    # Least used
    by_usage = sorted(valid, key=lambda x: x.get("usage", {}).get("event_count", 0))
    rankings["least_used"] = {
        "role": by_usage[0]["role_name"],
        "event_count": by_usage[0].get("usage", {}).get("event_count", 0),
    }

    # Most permissive
    by_perms = sorted(
        valid,
        key=lambda x: x.get("permissions", {}).get("total_actions_granted", 0),
        reverse=True,
    )
    rankings["most_permissive"] = {
        "role": by_perms[0]["role_name"],
        "total_actions": by_perms[0].get("permissions", {}).get("total_actions_granted", 0),
    }

    # Safest to delete (highest risk + lowest usage)
    deletion_score = sorted(
        valid,
        key=lambda x: (
            x.get("risk_score", 0) * 2 - x.get("usage", {}).get("event_count", 0)
        ),
        reverse=True,
    )
    rankings["safest_to_delete"] = {
        "role": deletion_score[0]["role_name"],
        "reason": "Highest risk-to-usage ratio — most unnecessary attack surface",
    }

    return rankings


def _generate_recommendation(comparisons: list, rankings: dict) -> str:
    """Generate a summary recommendation."""
    if not rankings:
        return "Unable to generate recommendation — no valid roles found."

    safest = rankings.get("safest_to_delete", {}).get("role", "unknown")
    most_risky = rankings.get("most_risky", {}).get("role", "unknown")
    risk_level = rankings.get("most_risky", {}).get("level", "UNKNOWN")

    return (
        f"Priority recommendation: Address '{most_risky}' first ({risk_level} risk). "
        f"'{safest}' is the safest candidate for deletion based on risk-to-usage ratio. "
        f"Run blast radius analysis on each before taking action."
    )
