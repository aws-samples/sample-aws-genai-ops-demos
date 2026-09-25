"""Tool: Generate least-privilege IAM policy from CloudTrail analysis.

Analyzes CloudTrail logs for a specific IAM role over a configurable period,
identifies which permissions are actually used vs. granted, and produces a
least-privilege policy with unused permission diff and resource-level scoping.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cloudtrail_client = boto3.client("cloudtrail")
iam_client = boto3.client("iam")


def handler(event, context=None):
    """Generate a least-privilege policy based on CloudTrail activity.

    Args:
        event: {
            role_name: str - IAM role to analyze (required)
            lookback_days: int - days of history to analyze (default: 90, max: 365)
            output_format: str - json, cdk_python, cdk_typescript, cloudformation (default: json)
            include_headroom: bool - add common companion actions (default: true)
        }

    Returns:
        {
            role_name, role_arn, analysis_period_days, events_analyzed,
            current_permissions: {policy list + total action count},
            used_permissions: {by service, with resources and last-used timestamps},
            unused_permissions: [actions granted but never observed],
            proposed_policy: {the generated policy document},
            formatted_policy: str,
            reduction_metrics: {percentage reduction, attack surface analysis}
        }
    """
    role_name = event.get("role_name")
    if not role_name:
        return {"error": "role_name is required"}

    lookback_days = min(event.get("lookback_days", 90), 365)
    output_format = event.get("output_format", "json")
    include_headroom = event.get("include_headroom", True)

    try:
        # Step 1: Get current role info and permissions
        role_info = _get_role_info(role_name)
        if "error" in role_info:
            return role_info

        current_actions = _get_current_granted_actions(role_name)

        # Step 2: Query CloudTrail for actual usage
        usage_data = _analyze_cloudtrail_usage(role_name, role_info["arn"], lookback_days)

        # Safety fence: if the CloudTrail query failed (permission denied,
        # throttling, service outage, network error, etc.), refuse to propose
        # a policy. `_analyze_cloudtrail_usage` returns an empty `used_actions`
        # accumulator on failure, and if we proceeded we would recommend
        # stripping 100% of the role's permissions — a "least-privilege" answer
        # driven by absence of data, not observed non-use. That is the single
        # most dangerous silent-failure in this demo. Fix per #171 phase A:
        # separate "CloudTrail query failed" from "zero events".
        cloudtrail_coverage = next(
            (c for c in usage_data.get("coverage", []) if c.get("source") == "cloudtrail"),
            None,
        )
        if cloudtrail_coverage and cloudtrail_coverage.get("state") == "unavailable":
            return {
                "error": (
                    "Could not read CloudTrail usage for this role — "
                    f"{cloudtrail_coverage.get('detail', 'unknown error')}. "
                    "Refusing to propose a least-privilege policy without usage data: "
                    "the resulting recommendation would remove permissions the role "
                    "actually needs. Fix CloudTrail access (verify the tool role has "
                    "cloudtrail:LookupEvents in this region and that CloudTrail is "
                    "logging management events) and re-run."
                ),
                "role_name": role_name,
                "role_arn": role_info.get("arn"),
                "coverage": usage_data.get("coverage", []),
            }

        # Step 3: Build least-privilege policy with resource scoping
        proposed_policy = _build_least_privilege_policy(
            usage_data["used_actions"],
            usage_data["used_resources"],
            include_headroom,
        )

        # Step 4: Calculate unused permissions (diff)
        used_action_set = set()
        for service_actions in usage_data["used_actions"].values():
            for action in service_actions:
                used_action_set.add(action)

        unused_permissions = sorted(current_actions - used_action_set)

        # Step 5: Calculate reduction metrics
        total_current = len(current_actions)
        total_used = len(used_action_set)
        total_unused = len(unused_permissions)
        reduction_pct = round((total_unused / total_current * 100), 1) if total_current > 0 else 0

        # Step 6: Format output
        formatted_policy = _format_policy(proposed_policy, output_format)

        result = {
            "role_name": role_name,
            "role_arn": role_info["arn"],
            "analysis_period_days": lookback_days,
            "events_analyzed": usage_data["event_count"],
            "analysis_window": {
                "start": usage_data["window_start"],
                "end": usage_data["window_end"],
            },
            "current_permissions": {
                "policies": role_info["attached_policies"],
                "total_actions_granted": total_current,
            },
            "used_permissions": {
                "total_actions_used": total_used,
                "total_services_used": len(usage_data["used_actions"]),
                "by_service": {
                    service: {
                        "actions": sorted(actions),
                        "call_count": usage_data["service_call_counts"].get(service, 0),
                    }
                    for service, actions in sorted(usage_data["used_actions"].items())
                },
                "last_activity": usage_data.get("last_event_time", "Unknown"),
            },
            "unused_permissions": unused_permissions[:50],  # Cap for readability
            "unused_count": total_unused,
            "proposed_policy": proposed_policy,
            "formatted_policy": formatted_policy,
            "output_format": output_format,
            "reduction_metrics": {
                "current_actions": total_current,
                "proposed_actions": total_used,
                "removed_actions": total_unused,
                "reduction_percentage": reduction_pct,
                "attack_surface_reduction": f"{reduction_pct}% of permissions removed",
                "risk_level": "HIGH" if reduction_pct > 70 else "MEDIUM" if reduction_pct > 40 else "LOW",
            },
            "coverage": usage_data.get("coverage", []),
        }

        # Add warnings
        warnings = []
        if usage_data["event_count"] == 0:
            warnings.append(
                f"No CloudTrail events found for {role_name} in the last {lookback_days} days. "
                "The role may be unused or events may not be logged."
            )
        if lookback_days < 30:
            warnings.append(
                "Short lookback period may miss infrequently-used permissions "
                "(e.g., monthly batch jobs). Consider 90+ days."
            )
        if usage_data["truncated"]:
            warnings.append(
                "CloudTrail results were truncated. Some actions may be missing from the analysis. "
                "Consider a shorter lookback period for more complete results."
            )
        if result["reduction_metrics"]["reduction_percentage"] > 80:
            warnings.append(
                "Very high reduction (>80%). Double-check that no seasonal or "
                "infrequent workloads are being missed."
            )
        if warnings:
            result["warnings"] = warnings

        return result

    except Exception as e:
        logger.error(f"Error generating policy: {e}", exc_info=True)
        return {"error": str(e)}


def _get_role_info(role_name: str) -> dict:
    """Get role metadata."""
    try:
        role_response = iam_client.get_role(RoleName=role_name)
        role = role_response["Role"]

        attached = iam_client.list_attached_role_policies(RoleName=role_name)
        inline = iam_client.list_role_policies(RoleName=role_name)

        policies = []
        for p in attached.get("AttachedPolicies", []):
            policies.append({"name": p["PolicyName"], "arn": p["PolicyArn"], "type": "managed"})
        for p_name in inline.get("PolicyNames", []):
            policies.append({"name": p_name, "type": "inline"})

        return {
            "arn": role["Arn"],
            "creation_date": str(role["CreateDate"]),
            "last_used": str(role.get("RoleLastUsed", {}).get("LastUsedDate", "Never")),
            "attached_policies": policies,
        }

    except iam_client.exceptions.NoSuchEntityException:
        return {"error": f"Role '{role_name}' not found in this account."}
    except Exception as e:
        return {"error": f"Error fetching role info: {e}"}


def _get_current_granted_actions(role_name: str) -> set:
    """Extract all actions currently granted to a role via attached policies."""
    actions = set()

    try:
        # Managed policies
        attached = iam_client.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            policy_info = iam_client.get_policy(PolicyArn=policy["PolicyArn"])
            version_id = policy_info["Policy"]["DefaultVersionId"]
            policy_version = iam_client.get_policy_version(
                PolicyArn=policy["PolicyArn"], VersionId=version_id
            )
            doc = policy_version["PolicyVersion"]["Document"]
            actions.update(_extract_actions_from_document(doc))

        # Inline policies
        inline_policies = iam_client.list_role_policies(RoleName=role_name)
        for policy_name in inline_policies.get("PolicyNames", []):
            policy_response = iam_client.get_role_policy(
                RoleName=role_name, PolicyName=policy_name
            )
            doc = policy_response["PolicyDocument"]
            actions.update(_extract_actions_from_document(doc))

    except Exception as e:
        logger.warning(f"Error extracting granted actions: {e}")

    return actions


def _extract_actions_from_document(document: dict) -> set:
    """Extract all Allow actions from a policy document."""
    actions = set()
    for statement in document.get("Statement", []):
        if statement.get("Effect") != "Allow":
            continue
        stmt_actions = statement.get("Action", [])
        if isinstance(stmt_actions, str):
            stmt_actions = [stmt_actions]
        for action in stmt_actions:
            if action == "*":
                actions.add("*")
            else:
                actions.add(action.lower())
    return actions


def _analyze_cloudtrail_usage(role_name: str, role_arn: str, lookback_days: int) -> dict:
    """Query CloudTrail for actual API usage by the role.

    Returns a dict with the observed usage plus a `coverage` entry describing
    what the CloudTrail call actually did — one of three states:

      * ``checked``       — the call succeeded and returned ``event_count > 0``
      * ``empty``         — the call succeeded but returned zero events (the
                            honest "role has not called AWS in ``lookback_days``"
                            case that the caller can act on)
      * ``unavailable``   — the call raised (``AccessDenied``, throttling,
                            service outage, IAM misconfiguration, transient
                            network error, etc.). ``used_actions`` will be
                            empty, but the caller MUST NOT interpret that as
                            "role has no usage" — the caller has no idea what
                            usage the role has.

    The caller inspects ``coverage`` to decide whether it is safe to propose a
    least-privilege policy. See #171 for the wider `coverage` contract this
    entry participates in.
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=lookback_days)

    used_actions = defaultdict(set)  # service -> set of actions
    used_resources = defaultdict(set)  # "service:Action" -> set of resource ARNs
    service_call_counts = defaultdict(int)
    event_count = 0
    truncated = False
    last_event_time = None

    coverage_detail = f"{lookback_days}-day event history, role {role_name}"
    coverage_state = "checked"
    coverage_error: str | None = None

    # CloudTrail's LookupEvents "Username" attribute filter does NOT match a
    # role name for assumed-role activity -- it resolves to the SESSION name
    # (the value passed as RoleSessionName, or a service-generated name for
    # AWS-service callers like Lambda). Filtering LookupAttributes on
    # Username=role_name therefore silently matches zero events for EVERY
    # role, regardless of how much real activity exists under that role --
    # a systemic false-negative, not a "role is unused" signal.
    #
    # LookupAttributes has no RoleName/RoleArn key at all (valid keys: EventId,
    # EventName, ReadOnly, Username, ResourceType, ResourceName, EventSource,
    # AccessKeyId -- confirmed against the API reference), and AccessKeyId
    # doesn't help either since assumed-role sessions get fresh STS
    # credentials per session, not a stable key. There is no server-side
    # filter that identifies "activity by this role" for assumed-role
    # events. Query unfiltered (paginating through the window) and match
    # client-side against userIdentity.arn, which DOES contain the role name
    # in both forms CloudTrail uses:
    #   direct role events:    arn:aws:iam::<acct>:role/<role_name>
    #   assumed-role sessions: arn:aws:sts::<acct>:assumed-role/<role_name>/<session>
    # Match on a trailing "/" boundary (or end-of-string for the direct
    # form) so a role whose name is a prefix of another role's name (e.g.
    # "my-role" vs "my-role-v2") cannot cross-attribute the other role's
    # activity.
    #
    # PERFORMANCE TRADEOFF: this scans ALL management events in the window
    # (still capped at PageSize=50 x max_pages=20 = 1000 events total),
    # not just this role's, because there's no way to filter server-side.
    # In a low-traffic account this is unnoticeable. In a busy account with
    # thousands of daily events, the 1000-event cap can be exhausted by
    # OTHER principals' activity before this role's events are reached,
    # producing `truncated: true` with an incomplete picture rather than a
    # true "role has N events". If this proves to matter on a real customer
    # account, the fix is EventHistory export to a CloudTrail Lake query
    # (SQL filter on userIdentity.arn) instead of LookupEvents pagination --
    # out of scope for this fix.
    def _actor_matches_role(actor_arn: str) -> bool:
        if f"role/{role_name}/" in actor_arn:
            return True
        # Direct-role form has no trailing session segment -- only match
        # when the role name is the LAST path component (end of string),
        # not merely a prefix of a longer role name.
        return actor_arn.endswith(f"role/{role_name}")

    try:
        paginator = cloudtrail_client.get_paginator("lookup_events")
        page_count = 0
        max_pages = 20  # Safety limit

        for page in paginator.paginate(
            StartTime=start_time,
            EndTime=end_time,
            PaginationConfig={"MaxItems": 1000, "PageSize": 50},
        ):
            page_count += 1
            for trail_event in page.get("Events", []):
                # The lookup_events summary record doesn't expose
                # userIdentity directly -- it's embedded in the raw
                # CloudTrailEvent JSON string.
                try:
                    raw_event = json.loads(trail_event.get("CloudTrailEvent", "{}"))
                except (TypeError, ValueError):
                    raw_event = {}
                actor_arn = raw_event.get("userIdentity", {}).get("arn", "")
                if not _actor_matches_role(actor_arn):
                    continue

                event_count += 1
                event_name = trail_event.get("EventName", "")
                event_source = trail_event.get("EventSource", "")

                # Normalize service name: "iam.amazonaws.com" -> "iam"
                service = event_source.replace(".amazonaws.com", "")

                # Skip read-only events that are just credential checks
                if event_name in ("GetCallerIdentity", "AssumeRole", "GetSessionToken"):
                    continue

                action_key = f"{service}:{event_name}"
                used_actions[service].add(event_name)
                service_call_counts[service] += 1

                # Track last event time
                event_time = trail_event.get("EventTime")
                if event_time and (last_event_time is None or event_time > last_event_time):
                    last_event_time = event_time

                # Extract resources for resource-level scoping
                for resource in trail_event.get("Resources", []):
                    resource_arn = resource.get("ResourceName", "")
                    if resource_arn and resource_arn.startswith("arn:"):
                        used_resources[action_key].add(resource_arn)

            if page_count >= max_pages:
                truncated = True
                break

    except Exception as e:
        # Do NOT let the caller mistake this for "role made no API calls".
        # The used_actions accumulator is empty because the call failed, not
        # because the role is unused. The caller must inspect `coverage` and
        # refuse to propose a policy when state == "unavailable".
        logger.warning(f"CloudTrail query error: {e}")
        coverage_state = "unavailable"
        coverage_error = f"{type(e).__name__}: {e}"

    if coverage_state == "checked" and event_count == 0:
        coverage_state = "empty"

    coverage_entry = {
        "source": "cloudtrail",
        "state": coverage_state,
        "count": event_count,
        "detail": coverage_detail if coverage_error is None else coverage_error,
    }

    return {
        "used_actions": dict(used_actions),
        "used_resources": {k: list(v) for k, v in used_resources.items()},
        "service_call_counts": dict(service_call_counts),
        "event_count": event_count,
        "truncated": truncated,
        "last_event_time": str(last_event_time) if last_event_time else None,
        "window_start": start_time.isoformat(),
        "window_end": end_time.isoformat(),
        "coverage": [coverage_entry],
    }


def _build_least_privilege_policy(
    used_actions: dict,
    used_resources: dict,
    include_headroom: bool,
) -> dict:
    """Build a least-privilege policy from observed usage."""
    statements = []

    # Headroom: common companion actions that should be included
    headroom_map = {
        "s3": {"ListBucket", "GetBucketLocation"},
        "logs": {"CreateLogGroup", "CreateLogStream", "PutLogEvents"},
        "sts": {"GetCallerIdentity"},
        "ec2": {"DescribeRegions"},
    }

    for service, actions in sorted(used_actions.items()):
        all_actions = set(actions)

        # Add headroom actions if enabled
        if include_headroom and service in headroom_map:
            all_actions.update(headroom_map[service])

        # Try to scope resources for this service
        service_resources = set()
        for action in all_actions:
            action_key = f"{service}:{action}"
            if action_key in used_resources:
                service_resources.update(used_resources[action_key])

        # Use specific resources if we have them, otherwise wildcard
        resource = sorted(service_resources)[:10] if service_resources else ["*"]

        statement = {
            "Sid": f"{service.capitalize().replace('.', '')}Access",
            "Effect": "Allow",
            "Action": sorted([f"{service}:{a}" for a in all_actions]),
            "Resource": resource if len(resource) > 1 else resource[0],
        }
        statements.append(statement)

    return {
        "Version": "2012-10-17",
        "Statement": statements,
    }


def _format_policy(policy: dict, output_format: str) -> str:
    """Format the policy in the requested output format."""
    if output_format == "json":
        return json.dumps(policy, indent=2)

    elif output_format == "cdk_python":
        lines = [
            "from aws_cdk import aws_iam as iam",
            "",
            "policy_document = iam.PolicyDocument(",
            "    statements=[",
        ]
        for stmt in policy.get("Statement", []):
            actions = stmt.get("Action", [])
            resources = stmt.get("Resource", "*")
            if isinstance(resources, str):
                resources = [resources]
            actions_str = ",\n                ".join(f'"{a}"' for a in actions)
            resources_str = ",\n                ".join(f'"{r}"' for r in resources)
            lines.append(f"        iam.PolicyStatement(")
            lines.append(f"            sid=\"{stmt.get('Sid', '')}\",")
            lines.append(f"            actions=[")
            lines.append(f"                {actions_str},")
            lines.append(f"            ],")
            lines.append(f"            resources=[")
            lines.append(f"                {resources_str},")
            lines.append(f"            ],")
            lines.append(f"        ),")
        lines.append("    ]")
        lines.append(")")
        return "\n".join(lines)

    elif output_format == "cdk_typescript":
        lines = [
            "import * as iam from 'aws-cdk-lib/aws-iam';",
            "",
            "const policyDocument = new iam.PolicyDocument({",
            "  statements: [",
        ]
        for stmt in policy.get("Statement", []):
            actions = stmt.get("Action", [])
            resources = stmt.get("Resource", "*")
            if isinstance(resources, str):
                resources = [resources]
            actions_str = ", ".join(f"'{a}'" for a in actions)
            resources_str = ", ".join(f"'{r}'" for r in resources)
            lines.append(f"    new iam.PolicyStatement({{")
            lines.append(f"      sid: '{stmt.get('Sid', '')}',")
            lines.append(f"      actions: [{actions_str}],")
            lines.append(f"      resources: [{resources_str}],")
            lines.append(f"    }}),")
        lines.append("  ],")
        lines.append("});")
        return "\n".join(lines)

    elif output_format == "cloudformation":
        cfn = {
            "Type": "AWS::IAM::ManagedPolicy",
            "Properties": {
                "PolicyDocument": policy,
                "Description": "Least-privilege policy generated by AI IAM Analyzer Assistant",
            },
        }
        return json.dumps(cfn, indent=2)

    return json.dumps(policy, indent=2)
