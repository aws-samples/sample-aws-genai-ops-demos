"""Tool: Check IAM entity dependencies with recursive traversal and risk scoring.

Maps what roles, users, services, and resources depend on a given IAM entity.
Provides risk scoring to help understand the blast radius of changes.
"""

import logging
from collections import defaultdict

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

iam_client = boto3.client("iam")


def handler(event, context=None):
    """Map dependencies for an IAM entity.

    Args:
        event: {
            entity_arn: str - ARN of IAM role, user, or policy (required)
            depth: int - levels of dependency traversal (default: 2, max: 3)
            include_service_linked: bool - include service-linked roles (default: false)
        }

    Returns:
        {
            entity_arn, entity_type, entity_name,
            direct_dependents: [...],
            trust_relationships: [...],
            policy_attachments: [...],
            dependency_graph: {visual representation},
            risk_score: {blast radius assessment},
            warnings: [...]
        }
    """
    entity_arn = event.get("entity_arn")
    if not entity_arn:
        return {"error": "entity_arn is required"}

    depth = min(event.get("depth", 2), 3)
    include_service_linked = event.get("include_service_linked", False)

    try:
        entity_type = _get_entity_type(entity_arn)
        entity_name = entity_arn.split("/")[-1] if "/" in entity_arn else entity_arn.split(":")[-1]

        result = {
            "entity_arn": entity_arn,
            "entity_type": entity_type,
            "entity_name": entity_name,
            "direct_dependents": [],
            "trust_relationships": [],
            "policy_attachments": [],
            "dependency_graph": {},
            "warnings": [],
        }

        if entity_type == "policy":
            _analyze_policy_dependencies(entity_arn, result, include_service_linked)
        elif entity_type == "role":
            _analyze_role_dependencies(entity_name, result, include_service_linked, depth)
        elif entity_type == "user":
            _analyze_user_dependencies(entity_name, result)
        else:
            result["warnings"].append(f"Could not determine entity type from ARN: {entity_arn}")
            return result

        # Build dependency graph summary
        result["dependency_graph"] = _build_graph_summary(result)

        # Calculate risk score
        result["risk_score"] = _calculate_risk_score(result)

        return result

    except Exception as e:
        logger.error(f"Error checking dependencies: {e}", exc_info=True)
        return {"error": str(e)}


def _get_entity_type(arn: str) -> str:
    """Determine entity type from ARN."""
    if ":policy/" in arn:
        return "policy"
    elif ":role/" in arn:
        return "role"
    elif ":user/" in arn:
        return "user"
    elif ":group/" in arn:
        return "group"
    return "unknown"


def _analyze_policy_dependencies(policy_arn: str, result: dict, include_service_linked: bool):
    """Find all entities attached to a managed policy."""
    try:
        paginator = iam_client.get_paginator("list_entities_for_policy")
        for page in paginator.paginate(PolicyArn=policy_arn):
            for role in page.get("PolicyRoles", []):
                role_name = role["RoleName"]
                is_sl = role_name.startswith("AWSServiceRoleFor")
                if not include_service_linked and is_sl:
                    continue
                result["direct_dependents"].append({
                    "type": "role",
                    "name": role_name,
                    "is_service_linked": is_sl,
                    "impact": "Role will lose permissions from this policy",
                })

            for user in page.get("PolicyUsers", []):
                result["direct_dependents"].append({
                    "type": "user",
                    "name": user["UserName"],
                    "impact": "User will lose permissions from this policy",
                })

            for group in page.get("PolicyGroups", []):
                result["direct_dependents"].append({
                    "type": "group",
                    "name": group["GroupName"],
                    "impact": "All users in this group will lose permissions",
                })

        # Get policy metadata for context
        try:
            policy_info = iam_client.get_policy(PolicyArn=policy_arn)
            result["policy_metadata"] = {
                "name": policy_info["Policy"]["PolicyName"],
                "attachment_count": policy_info["Policy"]["AttachmentCount"],
                "is_aws_managed": policy_arn.startswith("arn:aws:iam::aws:"),
            }
        except Exception:
            pass

    except iam_client.exceptions.NoSuchEntityException:
        result["warnings"].append(f"Policy not found: {policy_arn}")
    except Exception as e:
        result["warnings"].append(f"Error listing policy entities: {e}")


def _analyze_role_dependencies(role_name: str, result: dict, include_service_linked: bool, depth: int):
    """Analyze who/what assumes this role and what it depends on."""
    try:
        # Trust policy — who can assume this role
        role_info = iam_client.get_role(RoleName=role_name)
        trust_policy = role_info["Role"].get("AssumeRolePolicyDocument", {})

        for statement in trust_policy.get("Statement", []):
            if statement.get("Effect") != "Allow":
                continue
            principal = statement.get("Principal", {})
            conditions = statement.get("Condition", {})

            if isinstance(principal, str):
                result["trust_relationships"].append({
                    "principal": principal,
                    "principal_type": "wildcard" if principal == "*" else "unknown",
                    "conditions": conditions,
                    "risk": "CRITICAL" if principal == "*" and not conditions else "LOW",
                })
            elif isinstance(principal, dict):
                for p_type, p_values in principal.items():
                    values = p_values if isinstance(p_values, list) else [p_values]
                    for v in values:
                        risk = "LOW"
                        if p_type == "AWS" and v == "*":
                            risk = "CRITICAL" if not conditions else "HIGH"
                        elif p_type == "Service":
                            risk = "INFO"

                        result["trust_relationships"].append({
                            "principal": v,
                            "principal_type": p_type,
                            "conditions": conditions,
                            "risk": risk,
                        })

        # Attached policies — what this role depends on
        attached = iam_client.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            result["policy_attachments"].append({
                "policy_name": policy["PolicyName"],
                "policy_arn": policy["PolicyArn"],
                "type": "managed",
                "is_aws_managed": policy["PolicyArn"].startswith("arn:aws:iam::aws:"),
            })

        # Inline policies
        inline = iam_client.list_role_policies(RoleName=role_name)
        for policy_name in inline.get("PolicyNames", []):
            result["policy_attachments"].append({
                "policy_name": policy_name,
                "type": "inline",
            })

        # Recursive: find roles that reference this role in their policies
        if depth > 1:
            _find_roles_referencing(role_info["Role"]["Arn"], result, include_service_linked)

    except iam_client.exceptions.NoSuchEntityException:
        result["warnings"].append(f"Role '{role_name}' not found")
    except Exception as e:
        result["warnings"].append(f"Error analyzing role: {e}")


def _analyze_user_dependencies(user_name: str, result: dict):
    """Analyze user's policies, groups, and access keys."""
    try:
        user_info = iam_client.get_user(UserName=user_name)

        # Attached policies
        attached = iam_client.list_attached_user_policies(UserName=user_name)
        for policy in attached.get("AttachedPolicies", []):
            result["policy_attachments"].append({
                "policy_name": policy["PolicyName"],
                "policy_arn": policy["PolicyArn"],
                "type": "managed",
            })

        # Group memberships
        groups = iam_client.list_groups_for_user(UserName=user_name)
        for group in groups.get("Groups", []):
            result["direct_dependents"].append({
                "type": "group_membership",
                "name": group["GroupName"],
                "impact": "User inherits permissions from this group",
            })

        # Access keys
        keys = iam_client.list_access_keys(UserName=user_name)
        for key in keys.get("AccessKeyMetadata", []):
            result["direct_dependents"].append({
                "type": "access_key",
                "name": key["AccessKeyId"],
                "status": key["Status"],
                "created": str(key["CreateDate"]),
                "impact": "Access key will stop working if user is deleted",
            })

    except iam_client.exceptions.NoSuchEntityException:
        result["warnings"].append(f"User '{user_name}' not found")
    except Exception as e:
        result["warnings"].append(f"Error analyzing user: {e}")


def _find_roles_referencing(target_arn: str, result: dict, include_service_linked: bool):
    """Find other roles that can assume the target role (depth=2 analysis)."""
    try:
        paginator = iam_client.get_paginator("list_roles")
        referencing_roles = []

        for page in paginator.paginate(MaxItems=200):
            for role in page.get("Roles", []):
                if not include_service_linked and role["RoleName"].startswith("AWSServiceRoleFor"):
                    continue

                trust_doc = role.get("AssumeRolePolicyDocument", {})
                for stmt in trust_doc.get("Statement", []):
                    principal = stmt.get("Principal", {})
                    aws_principals = principal.get("AWS", []) if isinstance(principal, dict) else []
                    if isinstance(aws_principals, str):
                        aws_principals = [aws_principals]

                    if target_arn in aws_principals:
                        referencing_roles.append({
                            "type": "role",
                            "name": role["RoleName"],
                            "relationship": "can_assume_target",
                            "impact": "This role assumes the target — changes may break its workflows",
                        })

        if referencing_roles:
            result["direct_dependents"].extend(referencing_roles)

    except Exception as e:
        result["warnings"].append(f"Could not scan for referencing roles: {e}")


def _build_graph_summary(result: dict) -> dict:
    """Build a human-readable dependency graph summary."""
    entity_name = result["entity_name"]
    entity_type = result["entity_type"]

    graph = {
        "root": f"{entity_type}:{entity_name}",
        "depends_on": [],
        "depended_on_by": [],
        "total_impact_radius": 0,
    }

    # What this entity depends on (policies)
    for policy in result["policy_attachments"]:
        graph["depends_on"].append(f"policy:{policy['policy_name']}")

    # What depends on this entity
    for dep in result["direct_dependents"]:
        graph["depended_on_by"].append(f"{dep['type']}:{dep['name']}")

    # Trust relationships (who can become this entity)
    for trust in result["trust_relationships"]:
        graph["depended_on_by"].append(f"trust:{trust['principal']}")

    graph["total_impact_radius"] = len(graph["depended_on_by"])

    return graph


def _calculate_risk_score(result: dict) -> dict:
    """Calculate blast radius risk score."""
    score = 0
    factors = []

    # Number of dependents
    dep_count = len(result["direct_dependents"])
    if dep_count > 10:
        score += 40
        factors.append(f"High dependent count ({dep_count} entities)")
    elif dep_count > 5:
        score += 25
        factors.append(f"Moderate dependent count ({dep_count} entities)")
    elif dep_count > 0:
        score += 10
        factors.append(f"{dep_count} dependent(s)")

    # Trust policy risks
    for trust in result["trust_relationships"]:
        if trust.get("risk") == "CRITICAL":
            score += 30
            factors.append(f"CRITICAL trust: {trust['principal']} can assume this role without conditions")
        elif trust.get("risk") == "HIGH":
            score += 15
            factors.append(f"Broad trust: {trust['principal']}")

    # Service-linked role dependencies
    sl_count = sum(1 for d in result["direct_dependents"] if d.get("is_service_linked"))
    if sl_count > 0:
        score += 20
        factors.append(f"{sl_count} service-linked role(s) — AWS-managed, cannot be modified")

    # AWS-managed policy dependency
    aws_managed = sum(1 for p in result["policy_attachments"] if p.get("is_aws_managed"))
    if aws_managed > 0:
        factors.append(f"Uses {aws_managed} AWS-managed policy(ies) — stable dependency")

    # Cap at 100
    score = min(score, 100)

    return {
        "score": score,
        "level": "CRITICAL" if score >= 70 else "HIGH" if score >= 50 else "MEDIUM" if score >= 25 else "LOW",
        "factors": factors,
        "recommendation": (
            "Do NOT modify without change management approval and rollback plan"
            if score >= 70
            else "Test changes in a non-production environment first"
            if score >= 50
            else "Moderate risk — verify dependent workloads after changes"
            if score >= 25
            else "Low blast radius — changes can be made with standard review"
        ),
    }
