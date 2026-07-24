"""Tool: Generate a prioritized IAM remediation action plan.

Analyzes all active findings, scores them by severity and blast radius,
and produces a ranked remediation backlog with recommended order of operations.
"""

import json
import logging
from collections import defaultdict

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

securityhub_client = boto3.client("securityhub")
iam_client = boto3.client("iam")


def handler(event, context=None):
    """Generate a prioritized action plan for IAM remediation.

    Args:
        event: {
            max_items: int - Maximum findings to analyze (default: 50)
            include_quick_wins: bool - Highlight low-risk, high-impact fixes (default: true)
            focus_area: str - Optional focus: unused_roles, overpermissioned, public_access, cross_account
        }

    Returns:
        {
            action_plan: [{priority, action, role, severity, risk_score, effort, rationale}],
            summary: {total_findings, quick_wins, high_priority, estimated_time},
            quick_wins: [{action, role, why}],
            risk_distribution: {critical, high, medium, low}
        }
    """
    max_items = min(event.get("max_items", 50), 100)    include_quick_wins = event.get("include_quick_wins", True)
    focus_area = event.get("focus_area")

    try:
        # Fetch all active findings
        filters = {
            "ProductName": [{"Value": "IAM Access Analyzer", "Comparison": "EQUALS"}],
            "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
            "WorkflowStatus": [{"Value": "NEW", "Comparison": "EQUALS"}],
        }

        if focus_area:
            focus_filters = {
                "unused_roles": {"Title": [{"Value": "unused", "Comparison": "CONTAINS"}]},
                "overpermissioned": {"Title": [{"Value": "overpermissioned", "Comparison": "CONTAINS"}]},
                "public_access": {"Title": [{"Value": "public", "Comparison": "CONTAINS"}]},
                "cross_account": {"Title": [{"Value": "external", "Comparison": "CONTAINS"}]},
            }
            if focus_area in focus_filters:
                filters.update(focus_filters[focus_area])

        response = securityhub_client.get_findings(
            Filters=filters,
            MaxResults=max_items,
            SortCriteria=[{"Field": "SeverityNormalized", "SortOrder": "desc"}],
        )

        findings = response.get("Findings", [])
        if not findings:
            return {
                "action_plan": [],
                "summary": {
                    "total_findings": 0,
                    "message": "No active IAM findings found. Your IAM posture looks clean!",
                },
                "quick_wins": [],
                "risk_distribution": {"critical": 0, "high": 0, "medium": 0, "low": 0},
            }

        # Score and prioritize each finding
        scored_items = []
        risk_distribution = defaultdict(int)

        for finding in findings:
            score = _calculate_priority_score(finding)
            severity = finding.get("Severity", {}).get("Label", "MEDIUM")
            risk_distribution[severity.lower()] += 1

            resources = finding.get("Resources", [{}])
            resource_id = resources[0].get("Id", "") if resources else ""
            role_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id

            scored_items.append({
                "priority_score": score["score"],
                "action": _determine_action(finding),
                "role_name": role_name,
                "resource_id": resource_id,
                "severity": severity,
                "finding_title": finding.get("Title", ""),
                "effort": score["effort"],
                "risk_if_ignored": score["risk_if_ignored"],
                "rationale": score["rationale"],
                "is_quick_win": score["is_quick_win"],
            })

        # Sort by priority score (highest first)
        scored_items.sort(key=lambda x: x["priority_score"], reverse=True)

        # Assign priority numbers
        action_plan = []
        for i, item in enumerate(scored_items, 1):
            action_plan.append({
                "priority": i,
                "action": item["action"],
                "role_name": item["role_name"],
                "severity": item["severity"],
                "priority_score": item["priority_score"],
                "effort": item["effort"],
                "risk_if_ignored": item["risk_if_ignored"],
                "rationale": item["rationale"],
            })

        # Extract quick wins
        quick_wins = []
        if include_quick_wins:
            quick_wins = [
                {
                    "action": item["action"],
                    "role_name": item["role_name"],
                    "why": "Low effort, safe to execute, reduces attack surface",
                }
                for item in scored_items
                if item["is_quick_win"]
            ][:10]

        # Estimate total remediation time
        effort_map = {"trivial": 5, "low": 15, "medium": 30, "high": 60}
        total_minutes = sum(effort_map.get(item["effort"], 30) for item in scored_items)

        summary = {
            "total_findings": len(scored_items),
            "quick_wins_count": len(quick_wins),
            "high_priority_count": sum(1 for item in scored_items if item["priority_score"] >= 70),
            "estimated_total_time_minutes": total_minutes,
            "estimated_total_time_human": _format_time(total_minutes),
            "focus_area": focus_area or "all",
        }

        return {
            "action_plan": action_plan[:10],
            "total_items_analyzed": len(action_plan),
            "showing": min(10, len(action_plan)),
            "summary": summary,
            "quick_wins": quick_wins,
            "risk_distribution": dict(risk_distribution),
        }

    except Exception as e:
        logger.error(f"Error generating action plan: {e}", exc_info=True)
        return {"error": str(e)}


def _calculate_priority_score(finding: dict) -> dict:
    """Calculate a priority score (0-100) for a finding."""
    score = 0
    rationale_parts = []

    severity = finding.get("Severity", {}).get("Label", "MEDIUM")
    severity_scores = {"CRITICAL": 40, "HIGH": 30, "MEDIUM": 20, "LOW": 10, "INFORMATIONAL": 5}
    score += severity_scores.get(severity, 15)
    rationale_parts.append(f"{severity} severity")

    title = finding.get("Title", "").lower()

    # Unused roles are easy wins
    if "unused" in title:
        is_quick_win = True
        effort = "trivial"
        score += 15
        rationale_parts.append("unused resource (safe to remove)")
    elif "public" in title:
        is_quick_win = False
        effort = "medium"
        score += 25
        rationale_parts.append("public access exposure")
    elif "external" in title or "cross-account" in title:
        is_quick_win = False
        effort = "high"
        score += 20
        rationale_parts.append("cross-account access")
    else:
        is_quick_win = False
        effort = "medium"
        score += 10
        rationale_parts.append("general IAM finding")

    # Risk if ignored
    if score >= 60:
        risk_if_ignored = "Expanded attack surface; potential credential compromise impact"
    elif score >= 40:
        risk_if_ignored = "Unnecessary permissions increase blast radius of any future incident"
    else:
        risk_if_ignored = "Minor hygiene issue; low immediate risk"

    return {
        "score": min(score, 100),
        "effort": effort,
        "is_quick_win": is_quick_win,
        "risk_if_ignored": risk_if_ignored,
        "rationale": "; ".join(rationale_parts),
    }


def _determine_action(finding: dict) -> str:
    """Determine the recommended action for a finding."""
    title = finding.get("Title", "").lower()

    if "unused" in title and "role" in title:
        return "Delete unused role"
    elif "unused" in title and "permission" in title:
        return "Remove unused permissions"
    elif "unused" in title:
        return "Remove unused IAM entity"
    elif "public" in title:
        return "Restrict public access"
    elif "external" in title:
        return "Review and restrict cross-account access"
    elif "overpermissioned" in title or "broad" in title:
        return "Apply least-privilege policy"
    else:
        return "Review and remediate finding"


def _format_time(minutes: int) -> str:
    """Format minutes into human-readable time."""
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    remaining = minutes % 60
    if remaining == 0:
        return f"{hours} hour{'s' if hours > 1 else ''}"
    return f"{hours}h {remaining}m"
