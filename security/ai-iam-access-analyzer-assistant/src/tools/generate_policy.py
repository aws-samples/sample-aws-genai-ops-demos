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
    """Query CloudTrail for actual API usage by the role."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=lookback_days)

    used_actions = defaultdict(set)  # service -> set of actions
    used_resources = defaultdict(set)  # "service:Action" -> set of resource ARNs
    service_call_counts = defaultdict(int)
    event_count = 0
    truncated = False
    last_event_time = None

    try:
        paginator = cloudtrail_client.get_paginator("lookup_events")
        page_count = 0
        max_pages = 20  # Safety limit

        for page in paginator.paginate(
            LookupAttributes=[
                {"AttributeKey": "Username", "AttributeValue": role_name},
            ],
            StartTime=start_time,
            EndTime=end_time,
            PaginationConfig={"MaxItems": 1000, "PageSize": 50},
        ):
            page_count += 1
            for trail_event in page.get("Events", []):
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
        logger.warning(f"CloudTrail query error: {e}")

    return {
        "used_actions": dict(used_actions),
        "used_resources": {k: list(v) for k, v in used_resources.items()},
        "service_call_counts": dict(service_call_counts),
        "event_count": event_count,
        "truncated": truncated,
        "last_event_time": str(last_event_time) if last_event_time else None,
        "window_start": start_time.isoformat(),
        "window_end": end_time.isoformat(),
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
