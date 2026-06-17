"""
G.O.A.T. Support Agent - AWS Support case queries and communications
Plain Python handler with BedrockAgentCoreApp (sync entrypoint)

Receives structured JSON payloads from the orchestration agent's @tool functions,
routes to domain-specific handler functions, and calls AWS APIs directly via boto3.
NO Strands Agent SDK, NO Agent class, NO @tool decorators.
"""
import json
import logging
import boto3
from datetime import datetime, timezone
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from aws_utils import get_region

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = BedrockAgentCoreApp()
AWS_REGION = get_region()

# AWS Support API is a global service — always use us-east-1
SUPPORT_API_REGION = "us-east-1"

# Error message for missing Support plan
SUPPORT_PLAN_ERROR = (
    "AWS Support API access requires a Business, Enterprise On-Ramp, or Enterprise "
    "Support plan. Your account does not appear to have the required support plan. "
    "Please upgrade your support plan at https://console.aws.amazon.com/support/plans "
    "to use this feature."
)


def _is_subscription_error(error: Exception) -> bool:
    """Check if the error is a SubscriptionRequiredException (missing Support plan)."""
    error_str = str(error)
    return (
        "SubscriptionRequiredException" in error_str
        or "subscription" in error_str.lower()
    )


import re

# Pattern matching the full AWS Support case ID format: case-XXXXXXXXXX-YYYY-NNNNNN
_FULL_CASE_ID_PATTERN = re.compile(r"^case-\d+-\d{4}-\d+$")


def _is_display_id(case_id: str) -> bool:
    """Return True if ``case_id`` looks like a numeric display ID rather than
    the full ``case-XXXXXXXXXX-YYYY-NNNNNN`` format.

    Display IDs are purely numeric (e.g. ``178126584700003``). The AWS Support
    ``DescribeCases`` API only accepts the full format in ``caseIdList`` — numeric
    display IDs must be resolved by listing cases and matching ``displayId``.
    """
    if not case_id:
        return False
    # Full format always starts with "case-"
    if _FULL_CASE_ID_PATTERN.match(case_id):
        return False
    # Numeric-only strings are display IDs
    return case_id.strip().isdigit()


def _resolve_display_id_to_case(support_client, display_id: str) -> dict | None:
    """Look up a support case by its numeric display ID.

    Paginates through ``DescribeCases`` (resolved included) and returns
    the first case whose ``displayId`` matches. Returns None if not found
    after exhausting all pages.

    This is the only reliable way to look up a case by display ID since
    the AWS API's ``caseIdList`` parameter does not accept display IDs.
    """
    paginator = support_client.get_paginator("describe_cases")
    page_iterator = paginator.paginate(
        includeResolvedCases=True,
        includeCommunications=False,
    )
    for page in page_iterator:
        for case in page.get("cases", []):
            if case.get("displayId") == display_id:
                return case
    return None


def handle_describe_cases(params: dict) -> dict:
    """Retrieve AWS Support cases with optional filters.

    Optional params: caseIdList, status, afterTime, beforeTime, maxResults,
                     includeResolvedCases, language

    Note: includeResolvedCases defaults to True so resolved/closed cases are
    always returned unless explicitly set to False.
    """
    try:
        support_client = boto3.client("support", region_name=SUPPORT_API_REGION)

        kwargs = {}
        target_case_id = None

        if params.get("caseIdList") or params.get("case_id_list"):
            raw_list = params.get("caseIdList") or params.get("case_id_list")
            # Check if any item in the list is a display ID that needs resolution
            resolved_ids = []
            display_id_cases = []
            for cid in (raw_list if isinstance(raw_list, list) else [raw_list]):
                if _is_display_id(str(cid)):
                    found = _resolve_display_id_to_case(support_client, str(cid))
                    if found:
                        display_id_cases.append(found)
                        resolved_ids.append(found["caseId"])
                    else:
                        return {
                            "success": False,
                            "domain": "support",
                            "error": f"Support case with display ID '{cid}' not found in this account.",
                        }
                else:
                    resolved_ids.append(cid)
            if display_id_cases and not resolved_ids:
                # All were display IDs and we already have the full case objects
                cases = display_id_cases
                return {
                    "success": True,
                    "domain": "support",
                    "data": {"cases": _serialize_cases(cases), "count": len(cases)},
                    "formattedText": _format_support_cases(cases),
                    "metadata": {
                        "sourceApi": "support:DescribeCases",
                        "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                        "dataFreshness": "real-time",
                    },
                }
            if resolved_ids:
                kwargs["caseIdList"] = resolved_ids
        elif params.get("caseId") or params.get("case_id"):
            # Normalize single case ID string to a list
            single_id = params.get("caseId") or params.get("case_id")
            single_id_str = str(single_id).strip()

            # If it's a numeric display ID, resolve it by pagination lookup
            if _is_display_id(single_id_str):
                logger.info(
                    "describe_cases: resolving display ID '%s' to full case ID",
                    single_id_str,
                )
                found_case = _resolve_display_id_to_case(support_client, single_id_str)
                if found_case:
                    # Return the resolved case directly (we already have it)
                    cases = [found_case]
                    return {
                        "success": True,
                        "domain": "support",
                        "data": {"cases": _serialize_cases(cases), "count": len(cases)},
                        "formattedText": _format_support_cases(cases),
                        "metadata": {
                            "sourceApi": "support:DescribeCases",
                            "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                            "dataFreshness": "real-time",
                            "resolvedFromDisplayId": single_id_str,
                            "resolvedCaseId": found_case.get("caseId"),
                        },
                    }
                else:
                    return {
                        "success": False,
                        "domain": "support",
                        "error": (
                            f"Support case with display ID '{single_id_str}' not found "
                            f"in this account. Searched all cases (including resolved). "
                            f"Verify the case number is correct."
                        ),
                    }
            else:
                kwargs["caseIdList"] = [single_id_str] if isinstance(single_id_str, str) else single_id
        # The AWS describe_cases API does NOT accept a "status" filter parameter.
        # Instead, it uses includeResolvedCases boolean. If the LLM passes
        # status="open", we ignore it and let includeResolvedCases handle filtering.
        # If status="resolved" or "closed", we ensure includeResolvedCases=True.
        status_param = params.get("status", "")
        if params.get("afterTime") or params.get("after_time"):
            kwargs["afterTime"] = params.get("afterTime") or params.get("after_time")
        if params.get("beforeTime") or params.get("before_time"):
            kwargs["beforeTime"] = params.get("beforeTime") or params.get("before_time")
        # Default to including resolved cases so demo/resolved cases are visible
        include_resolved = params.get("includeResolvedCases", params.get("include_resolved_cases", True))
        if include_resolved is not False:
            kwargs["includeResolvedCases"] = True
        if params.get("maxResults") or params.get("max_results"):
            kwargs["maxResults"] = int(
                params.get("maxResults") or params.get("max_results", 10)
            )
        if params.get("language"):
            kwargs["language"] = params["language"]

        response = support_client.describe_cases(**kwargs)
        cases = response.get("cases", [])

        return {
            "success": True,
            "domain": "support",
            "data": {"cases": _serialize_cases(cases), "count": len(cases)},
            "formattedText": _format_support_cases(cases),
            "metadata": {
                "sourceApi": "support:DescribeCases",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "real-time",
            },
        }

    except Exception as e:
        if _is_subscription_error(e):
            return {"success": False, "error": SUPPORT_PLAN_ERROR}
        return {"success": False, "error": f"Describe cases query failed: {str(e)}"}


def handle_communications(params: dict) -> dict:
    """Retrieve communications for a specific support case.

    Required params: caseId
    Optional params: afterTime, beforeTime, maxResults
    """
    try:
        case_id = params.get("caseId") or params.get("case_id")
        if not case_id:
            return {
                "success": False,
                "error": "caseId is required for communications queries.",
            }

        support_client = boto3.client("support", region_name=SUPPORT_API_REGION)

        # Resolve display ID to full case ID if needed
        case_id_str = str(case_id).strip()
        if _is_display_id(case_id_str):
            logger.info(
                "describe_communications: resolving display ID '%s' to full case ID",
                case_id_str,
            )
            found_case = _resolve_display_id_to_case(support_client, case_id_str)
            if found_case:
                case_id_str = found_case["caseId"]
            else:
                return {
                    "success": False,
                    "domain": "support",
                    "error": (
                        f"Support case with display ID '{case_id}' not found "
                        f"in this account. Cannot retrieve communications."
                    ),
                }

        kwargs = {"caseId": case_id_str}
        if params.get("afterTime") or params.get("after_time"):
            kwargs["afterTime"] = params.get("afterTime") or params.get("after_time")
        if params.get("beforeTime") or params.get("before_time"):
            kwargs["beforeTime"] = params.get("beforeTime") or params.get("before_time")
        if params.get("maxResults") or params.get("max_results"):
            kwargs["maxResults"] = int(
                params.get("maxResults") or params.get("max_results", 10)
            )

        response = support_client.describe_communications(**kwargs)
        communications = response.get("communications", [])

        return {
            "success": True,
            "domain": "support",
            "data": {
                "communications": communications,
                "caseId": case_id,
                "count": len(communications),
            },
            "formattedText": _format_communications(communications, case_id),
            "metadata": {
                "sourceApi": "support:DescribeCommunications",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "real-time",
            },
        }

    except Exception as e:
        if _is_subscription_error(e):
            return {"success": False, "error": SUPPORT_PLAN_ERROR}
        return {
            "success": False,
            "error": f"Communications query failed: {str(e)}",
        }


def handle_search_cases(params: dict) -> dict:
    """Search for support cases matching criteria.

    Uses describe_cases with filters to find matching cases.
    Includes resolved cases by default so demo/closed cases are always visible.
    Optional params: status, serviceCode, severityCode, afterTime, beforeTime,
                     includeResolvedCases, maxResults
    """
    try:
        support_client = boto3.client("support", region_name=SUPPORT_API_REGION)

        # Default to including resolved cases
        include_resolved = params.get("includeResolvedCases", params.get("include_resolved_cases", True))
        kwargs = {"includeResolvedCases": include_resolved is not False}
        if params.get("status"):
            kwargs["status"] = params["status"]
        if params.get("afterTime") or params.get("after_time"):
            kwargs["afterTime"] = params.get("afterTime") or params.get("after_time")
        if params.get("beforeTime") or params.get("before_time"):
            kwargs["beforeTime"] = params.get("beforeTime") or params.get("before_time")
        if params.get("maxResults") or params.get("max_results"):
            kwargs["maxResults"] = int(
                params.get("maxResults") or params.get("max_results", 20)
            )
        if params.get("language"):
            kwargs["language"] = params["language"]

        response = support_client.describe_cases(**kwargs)
        cases = response.get("cases", [])

        # Client-side filtering by serviceCode and severityCode if provided
        service_code = params.get("serviceCode") or params.get("service_code")
        severity_code = params.get("severityCode") or params.get("severity_code")

        if service_code:
            cases = [c for c in cases if c.get("serviceCode") == service_code]
        if severity_code:
            cases = [c for c in cases if c.get("severityCode") == severity_code]

        return {
            "success": True,
            "domain": "support",
            "data": {"cases": _serialize_cases(cases), "count": len(cases)},
            "formattedText": _format_support_cases(cases),
            "metadata": {
                "sourceApi": "support:DescribeCases",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "real-time",
            },
        }

    except Exception as e:
        if _is_subscription_error(e):
            return {"success": False, "error": SUPPORT_PLAN_ERROR}
        return {"success": False, "error": f"Search cases query failed: {str(e)}"}


def _serialize_cases(cases: list) -> list:
    """Serialize support case objects for JSON response."""
    serialized = []
    for case in cases:
        item = {
            "caseId": case.get("caseId", "N/A"),
            "displayId": case.get("displayId", "N/A"),
            "subject": case.get("subject", "N/A"),
            "status": case.get("status", "N/A"),
            "severityCode": case.get("severityCode", "N/A"),
            "serviceCode": case.get("serviceCode", "N/A"),
            "categoryCode": case.get("categoryCode", "N/A"),
            "timeCreated": case.get("timeCreated", "N/A"),
            "language": case.get("language", "en"),
            "submittedBy": case.get("submittedBy", "N/A"),
        }
        serialized.append(item)
    return serialized


def _format_support_cases(cases: list) -> str:
    """Format support cases with case ID, subject, status, severity, creation date."""
    if not cases:
        return "No support cases found matching the specified criteria."

    lines = [f"AWS Support Cases ({len(cases)} found)", ""]
    for case in cases:
        case_id = case.get("displayId") or case.get("caseId", "N/A")
        subject = case.get("subject", "N/A")
        status = case.get("status", "N/A")
        severity = case.get("severityCode", "N/A")
        created = case.get("timeCreated", "N/A")
        service = case.get("serviceCode", "N/A")

        lines.append(
            f"  Case: {case_id} | Subject: {subject} | "
            f"Status: {status} | Severity: {severity} | "
            f"Created: {created} | Service: {service}"
        )

    return "\n".join(lines)


def _format_communications(communications: list, case_id: str) -> str:
    """Format communications for a support case."""
    if not communications:
        return f"No communications found for case: {case_id}"

    lines = [
        f"Communications ({len(communications)} found) for case: {case_id}",
        "",
    ]
    for comm in communications:
        submitted_by = comm.get("submittedBy", "N/A")
        time_created = comm.get("timeCreated", "N/A")
        body = comm.get("body", "No content")
        # Truncate long bodies for formatted output
        if len(body) > 200:
            body = body[:200] + "..."

        lines.append(f"  From: {submitted_by} | Time: {time_created}")
        lines.append(f"    {body}")
        lines.append("")

    return "\n".join(lines)


def handle_action(action: str, params: dict) -> dict:
    """Route to the appropriate handler based on action."""
    handlers = {
        "describe_cases": handle_describe_cases,
        "describe_communications": handle_communications,
        "search_cases": handle_search_cases,
    }
    handler = handlers.get(action)
    if not handler:
        return {
            "success": False,
            "error": f"Unknown action: {action}. Supported actions: {', '.join(handlers.keys())}",
        }
    return handler(params)


@app.entrypoint
def main_handler(payload):
    """
    Main entry point for the Support Agent.
    Receives JSON payload, routes to handler based on action field.
    Synchronous — returns dict, not async generator.

    Payload format: {"action": "describe_cases", "params": {...}}
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)

        logger.info(f"Support agent received payload: {json.dumps(payload)[:500]}")

        action = payload.get("action")
        if not action:
            if payload.get("prompt"):
                logger.warning("Received raw prompt instead of structured action, defaulting to describe_cases")
            else:
                logger.warning("No action in payload, defaulting to describe_cases")
            action = "describe_cases"

        params = payload.get("params", {})
        return handle_action(action, params)

    except Exception as e:
        return {"success": False, "error": f"Request failed: {str(e)}"}


if __name__ == "__main__":
    app.run()
