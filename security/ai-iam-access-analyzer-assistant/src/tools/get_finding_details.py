"""Tool: Get detailed information about a specific IAM Access Analyzer finding.

Retrieves a single finding by ID with full context including affected resource
details, related findings, remediation steps, and risk assessment.
"""

import json
import logging
import urllib.parse

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

securityhub_client = boto3.client("securityhub")
iam_client = boto3.client("iam")


def handler(event, context=None):
    """Get detailed information about a specific finding.

    Args:
        event: {
            finding_id: str - Security Hub finding ID (required)
            include_resource_details: bool - Fetch additional IAM details for the resource (default: true)
            include_related_findings: bool - Find related findings for the same resource (default: true)
        }

    Returns:
        {
            finding: {full finding details},
            resource_details: {current IAM state of the affected resource},
            related_findings: [{other findings for the same resource}],
            risk_assessment: {severity context and impact analysis},
            remediation_steps: [ordered list of remediation actions]
        }
    """
    finding_id = event.get("finding_id")
    role_name = event.get("role_name")
    if not finding_id and not role_name:
        return {"error": "Provide either finding_id (a Security Hub finding ID/ARN) or role_name"}

    include_resource_details = event.get("include_resource_details", True)
    include_related_findings = event.get("include_related_findings", True)

    try:
        finding = None

        # Exact-ID lookup only when we were handed a real finding ID (ARN-like).
        if finding_id and _looks_like_finding_id(finding_id):
            response = securityhub_client.get_findings(
                Filters={
                    "Id": [{"Value": finding_id, "Comparison": "EQUALS"}],
                },
                MaxResults=1,
            )
            matches = response.get("Findings", [])
            finding = matches[0] if matches else None

        # Otherwise resolve by the affected role/resource name. This covers the
        # common "investigate role N" flow: the model knows the row number maps to
        # a role name and passes role_name (or a bare token as finding_id). We
        # resolve it in ONE Security Hub call instead of the old guess-ID -> fail
        # -> re-list -> retry dance, which chained 3 tool calls and blew past the
        # API gateway's 29s timeout.
        if finding is None:
            finding = _resolve_finding_by_resource_name(role_name or finding_id)

        if finding is None:
            hint = role_name or finding_id
            return {"error": f"No active finding found matching '{hint}'. Try the exact role name from the findings list."}

        resources = finding.get("Resources", [{}])
        primary_resource = resources[0] if resources else {}

        # Build detailed finding object
        result = {
            "finding": {
                "id": finding.get("Id", ""),
                "title": finding.get("Title", ""),
                "description": finding.get("Description", ""),
                "severity": finding.get("Severity", {}),
                "resource_type": primary_resource.get("Type", ""),
                "resource_id": primary_resource.get("Id", ""),
                "resource_region": primary_resource.get("Region", ""),
                "resource_details": primary_resource.get("Details", {}),
                "status": finding.get("Workflow", {}).get("Status", ""),
                "record_state": finding.get("RecordState", ""),
                "finding_type": finding.get("ProductFields", {}).get("type", ""),
                "generator_id": finding.get("GeneratorId", ""),
                "created_at": finding.get("CreatedAt", ""),
                "updated_at": finding.get("UpdatedAt", ""),
                "first_observed_at": finding.get("FirstObservedAt", ""),
                "last_observed_at": finding.get("LastObservedAt", ""),
                "account_id": finding.get("AwsAccountId", ""),
                "compliance": finding.get("Compliance", {}),
                "product_fields": finding.get("ProductFields", {}),
            },
            "remediation": {
                "recommendation": finding.get("Remediation", {}).get("Recommendation", {}).get("Text", ""),
                "recommendation_url": finding.get("Remediation", {}).get("Recommendation", {}).get("Url", ""),
            },
        }

        # Fetch current state of the IAM resource
        if include_resource_details:
            result["resource_details"] = _get_resource_details(primary_resource)

        # Find related findings for the same resource
        if include_related_findings:
            result["related_findings"] = _get_related_findings(
                primary_resource.get("Id", ""), finding_id
            )

        # Generate risk assessment
        result["risk_assessment"] = _assess_risk(finding, result.get("resource_details", {}))

        # Generate remediation steps
        result["remediation_steps"] = _generate_remediation_steps(finding, result.get("resource_details", {}))

        return result

    except securityhub_client.exceptions.InvalidAccessException:
        return {"error": "Security Hub is not enabled or access denied."}
    except Exception as e:
        logger.error(f"Error getting finding details: {e}", exc_info=True)
        return {"error": str(e)}


def _looks_like_finding_id(value: str) -> bool:
    """True only for real Security Hub finding IDs (ARNs / long composite IDs),
    so a bare row number like "8" or a plain role name is NOT mistaken for one."""
    v = (value or "").strip()
    return v.startswith("arn:") or "/" in v or len(v) > 40


def _resolve_finding_by_resource_name(name: str):
    """Resolve the active IAM Access Analyzer finding for a role/resource name.

    Security Hub's ResourceId filter does NOT support CONTAINS (only EQUALS/PREFIX),
    so we fetch the Access Analyzer findings with the same filter list_findings uses
    (which is known to work) and match the role name in Python against the resource
    ARN and the finding Id. One API call, no dependency on server-side CONTAINS."""
    name = (name or "").strip()
    if not name:
        return None
    try:
        response = securityhub_client.get_findings(
            Filters={
                "ProductName": [{"Value": "IAM Access Analyzer", "Comparison": "EQUALS"}],
                "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
            },
            MaxResults=50,
            SortCriteria=[{"Field": "SeverityNormalized", "SortOrder": "desc"}],
        )
        lname = name.lower()
        for finding in response.get("Findings", []):
            resources = finding.get("Resources", [{}])
            resource_id = (resources[0].get("Id", "") if resources else "").lower()
            finding_id = (finding.get("Id", "") or "").lower()
            title = (finding.get("Title", "") or "").lower()
            if lname in resource_id or lname in finding_id or lname in title:
                return finding
        return None
    except Exception as e:
        logger.warning(f"Could not resolve finding by resource name '{name}': {e}")
        return None


def _get_resource_details(resource: dict) -> dict:
    """Fetch current IAM state for the affected resource."""
    resource_type = resource.get("Type", "")
    resource_id = resource.get("Id", "")

    if not resource_id:
        return {"note": "No resource ID available"}

    try:
        if resource_type == "AwsIamRole":
            role_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
            role_info = iam_client.get_role(RoleName=role_name)
            attached_policies = iam_client.list_attached_role_policies(RoleName=role_name)
            inline_policies = iam_client.list_role_policies(RoleName=role_name)

            # Fetch each inline policy DOCUMENT, not just the name — otherwise the
            # risk assessment (and any deletion/least-privilege advice) is blind to
            # permissions that live in inline policies, which is where many roles
            # keep them.
            inline_names = inline_policies.get("PolicyNames", [])
            inline_docs = []
            for policy_name in inline_names:
                try:
                    doc = iam_client.get_role_policy(
                        RoleName=role_name, PolicyName=policy_name
                    ).get("PolicyDocument", {})
                    if isinstance(doc, str):
                        doc = json.loads(urllib.parse.unquote(doc))
                    inline_docs.append({"name": policy_name, "document": doc})
                except Exception as e:
                    inline_docs.append({"name": policy_name, "document": {}, "error": str(e)})

            return {
                "type": "IAM Role",
                "name": role_name,
                "arn": role_info["Role"]["Arn"],
                "creation_date": str(role_info["Role"]["CreateDate"]),
                "last_used": str(role_info["Role"].get("RoleLastUsed", {}).get("LastUsedDate", "Never")),
                "trust_policy": role_info["Role"].get("AssumeRolePolicyDocument", {}),
                "attached_policies": [
                    {"name": p["PolicyName"], "arn": p["PolicyArn"]}
                    for p in attached_policies.get("AttachedPolicies", [])
                ],
                "inline_policy_names": inline_names,
                "inline_policies": inline_docs,
                "max_session_duration": role_info["Role"].get("MaxSessionDuration", 3600),
                "path": role_info["Role"].get("Path", "/"),
            }

        elif resource_type == "AwsIamUser":
            user_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
            user_info = iam_client.get_user(UserName=user_name)
            attached_policies = iam_client.list_attached_user_policies(UserName=user_name)

            return {
                "type": "IAM User",
                "name": user_name,
                "arn": user_info["User"]["Arn"],
                "creation_date": str(user_info["User"]["CreateDate"]),
                "password_last_used": str(user_info["User"].get("PasswordLastUsed", "Never")),
                "attached_policies": [
                    {"name": p["PolicyName"], "arn": p["PolicyArn"]}
                    for p in attached_policies.get("AttachedPolicies", [])
                ],
            }

        elif resource_type == "AwsIamPolicy":
            policy_arn = resource_id
            policy_info = iam_client.get_policy(PolicyArn=policy_arn)
            policy_version = iam_client.get_policy_version(
                PolicyArn=policy_arn,
                VersionId=policy_info["Policy"]["DefaultVersionId"],
            )

            return {
                "type": "IAM Policy",
                "name": policy_info["Policy"]["PolicyName"],
                "arn": policy_arn,
                "attachment_count": policy_info["Policy"]["AttachmentCount"],
                "is_attachable": policy_info["Policy"]["IsAttachable"],
                "policy_document": policy_version["PolicyVersion"]["Document"],
            }

        else:
            return {"type": resource_type, "id": resource_id, "note": "Detailed lookup not supported for this resource type"}

    except iam_client.exceptions.NoSuchEntityException:
        return {"type": resource_type, "id": resource_id, "note": "Resource no longer exists"}
    except Exception as e:
        return {"type": resource_type, "id": resource_id, "error": str(e)}


def _get_related_findings(resource_id: str, exclude_finding_id: str) -> list:
    """Find other findings for the same resource."""
    if not resource_id:
        return []

    try:
        response = securityhub_client.get_findings(
            Filters={
                "ResourceId": [{"Value": resource_id, "Comparison": "EQUALS"}],
                "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
            },
            MaxResults=10,
        )

        related = []
        for f in response.get("Findings", []):
            if f.get("Id") == exclude_finding_id:
                continue
            related.append({
                "id": f.get("Id", ""),
                "title": f.get("Title", ""),
                "severity": f.get("Severity", {}).get("Label", ""),
                "product": f.get("ProductName", ""),
            })

        return related

    except Exception as e:
        logger.warning(f"Could not fetch related findings: {e}")
        return []


def _assess_risk(finding: dict, resource_details: dict) -> dict:
    """Generate a risk assessment based on finding context."""
    severity = finding.get("Severity", {})
    severity_label = severity.get("Label", "UNKNOWN")
    severity_normalized = severity.get("Normalized", 0)

    title = finding.get("Title", "").lower()
    description = finding.get("Description", "").lower()

    risk_factors = []
    impact_score = severity_normalized

    # Check for public access
    if "public" in title or "public" in description:
        risk_factors.append("Resource is publicly accessible")
        impact_score = min(impact_score + 20, 100)

    # Check for cross-account access
    if "cross-account" in title or "external" in description:
        risk_factors.append("Cross-account access detected")
        impact_score = min(impact_score + 10, 100)

    # Check for unused access
    if "unused" in title:
        risk_factors.append("Permission has not been used — likely unnecessary")

    # Check resource usage
    if resource_details.get("last_used") == "Never":
        risk_factors.append("Role has never been used — consider deletion")
        impact_score = max(impact_score - 10, 0)

    # Determine blast radius
    attachment_count = resource_details.get("attachment_count", 0)
    if attachment_count > 5:
        risk_factors.append(f"Policy attached to {attachment_count} entities — high blast radius")
        impact_score = min(impact_score + 15, 100)

    return {
        "severity_label": severity_label,
        "impact_score": impact_score,
        "risk_level": "CRITICAL" if impact_score >= 80 else "HIGH" if impact_score >= 60 else "MEDIUM" if impact_score >= 40 else "LOW",
        "risk_factors": risk_factors,
        "recommendation_priority": "Immediate action required" if impact_score >= 80 else "Address within 7 days" if impact_score >= 60 else "Address within 30 days" if impact_score >= 40 else "Review during next security sprint",
    }


def _generate_remediation_steps(finding: dict, resource_details: dict) -> list:
    """Generate ordered remediation steps based on finding type."""
    title = finding.get("Title", "").lower()
    resource_type = finding.get("Resources", [{}])[0].get("Type", "")

    steps = []

    # Generic first step
    steps.append("Verify the finding is still active and the resource exists")

    if "public" in title:
        steps.append("Identify who/what is accessing the resource publicly")
        steps.append("Determine if public access is intentional and documented")
        steps.append("If unintentional, restrict the resource policy to deny public access")
        steps.append("Verify dependent workloads still function after restriction")

    elif "unused" in title:
        steps.append("Check CloudTrail for any recent access by this entity")
        steps.append("Identify any automation or scheduled jobs that might use this permission periodically")
        steps.append("If confirmed unused, use check_dependencies to map impact")
        steps.append("Remove the unused permission or archive the finding with justification")

    elif "cross-account" in title or "external" in title:
        steps.append("Identify the external principal(s) with access")
        steps.append("Verify the cross-account access is authorized and documented")
        steps.append("Add conditions (aws:PrincipalOrgID, aws:SourceAccount) to limit scope")
        steps.append("Consider using an IAM permission boundary as an additional guardrail")

    else:
        steps.append("Review the finding description for specific remediation guidance")
        steps.append("Use generate_policy to create a least-privilege replacement")
        steps.append("Use check_dependencies to understand blast radius")
        steps.append("Apply changes in a non-production environment first")

    # Always end with verification
    steps.append("After remediation, verify the finding status changes to RESOLVED")
    steps.append("Document the change in your team's runbook or wiki")

    return steps
