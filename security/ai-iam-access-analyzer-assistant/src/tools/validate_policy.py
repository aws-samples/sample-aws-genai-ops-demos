"""Tool: Validate IAM policy documents with Access Analyzer integration.

Validates policies for syntax, security best practices, overly broad permissions,
and optionally checks that specific dangerous actions are not granted.
"""

import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

access_analyzer_client = boto3.client("accessanalyzer")


def handler(event, context=None):
    """Validate an IAM policy document.

    Args:
        event: {
            policy_document: str - JSON policy document (required)
            validation_type: str - syntax, access_level, least_privilege, all (default: all)
            context_role: str - optional role ARN for contextual validation
            check_actions_not_granted: list - actions that MUST NOT be allowed (optional)
            policy_type: str - IDENTITY_POLICY or RESOURCE_POLICY (default: IDENTITY_POLICY)
        }

    Returns:
        {
            is_valid: bool,
            findings: [{type, message, severity, location, ...}],
            access_not_granted_check: {passed, violations},
            security_analysis: {wildcards, dangerous_patterns, missing_conditions},
            summary: {errors, warnings, suggestions, total_findings}
        }
    """
    policy_document = event.get("policy_document")
    if not policy_document:
        return {"error": "policy_document is required"}

    validation_type = event.get("validation_type", "all")
    context_role = event.get("context_role")
    check_actions = event.get("check_actions_not_granted", [])
    policy_type = event.get("policy_type", "IDENTITY_POLICY")

    # Parse policy document
    if isinstance(policy_document, str):
        try:
            policy_json = json.loads(policy_document)
        except json.JSONDecodeError as e:
            return {
                "is_valid": False,
                "findings": [{"type": "ERROR", "severity": "CRITICAL",
                             "message": f"Invalid JSON: {e}"}],
                "summary": {"errors": 1, "warnings": 0, "suggestions": 0,
                           "total_findings": 1},
            }
    else:
        policy_json = policy_document
        policy_document = json.dumps(policy_json)

    results = {
        "is_valid": True,
        "findings": [],
        "security_analysis": {
            "wildcards": [],
            "dangerous_patterns": [],
            "missing_conditions": [],
        },
    }

    try:
        # IAM Access Analyzer ValidatePolicy API
        if validation_type in ("syntax", "all"):
            _run_access_analyzer_validation(policy_document, policy_type, results)

        # Security pattern analysis
        if validation_type in ("access_level", "least_privilege", "all"):
            _analyze_security_patterns(policy_json, results)

        # Missing conditions check
        if validation_type in ("least_privilege", "all"):
            _check_missing_conditions(policy_json, results)

        # CheckAccessNotGranted — verify specific actions are blocked
        if check_actions:
            results["access_not_granted_check"] = _check_access_not_granted(
                policy_document, check_actions, policy_type
            )

        # Build summary
        errors = sum(1 for f in results["findings"] if f.get("type") == "ERROR")
        warnings = sum(1 for f in results["findings"] if f.get("type") == "WARNING")
        suggestions = sum(1 for f in results["findings"] if f.get("type") == "SUGGESTION")

        results["is_valid"] = errors == 0
        results["summary"] = {
            "errors": errors,
            "warnings": warnings,
            "suggestions": suggestions,
            "total_findings": len(results["findings"]),
            "verdict": (
                "FAIL — policy has errors that must be fixed"
                if errors > 0
                else "WARN — policy is valid but has security concerns"
                if warnings > 0
                else "PASS — policy follows best practices"
            ),
        }

        return results

    except Exception as e:
        logger.error(f"Error validating policy: {e}", exc_info=True)
        return {"error": str(e)}


def _run_access_analyzer_validation(policy_document: str, policy_type: str, results: dict):
    """Use IAM Access Analyzer ValidatePolicy API."""
    try:
        response = access_analyzer_client.validate_policy(
            policyDocument=policy_document,
            policyType=policy_type,
        )

        for finding in response.get("findings", []):
            finding_type = finding.get("findingType", "WARNING")
            results["findings"].append({
                "type": finding_type,
                "severity": finding_type,
                "message": finding.get("findingDetails", ""),
                "issue_code": finding.get("issueCode", ""),
                "learn_more_link": finding.get("learnMoreLink", ""),
                "source": "IAM Access Analyzer",
                "locations": [
                    {"path": loc.get("path", []), "span": loc.get("span", {})}
                    for loc in finding.get("locations", [])
                ],
            })

            if finding_type == "ERROR":
                results["is_valid"] = False

    except access_analyzer_client.exceptions.ValidationException as e:
        results["findings"].append({
            "type": "ERROR",
            "severity": "CRITICAL",
            "message": f"Policy document is malformed: {e}",
            "source": "IAM Access Analyzer",
        })
        results["is_valid"] = False
    except Exception as e:
        results["findings"].append({
            "type": "WARNING",
            "severity": "LOW",
            "message": f"Could not run Access Analyzer validation: {e}",
            "source": "system",
        })


def _check_access_not_granted(policy_document: str, actions: list, policy_type: str) -> dict:
    """Use CheckAccessNotGranted to verify dangerous actions are blocked."""
    try:
        # Build access list for the API
        access_list = [{"actions": actions}]

        response = access_analyzer_client.check_access_not_granted(
            policyDocument=policy_document,
            policyType=policy_type,
            access=access_list,
        )

        result = response.get("result", "PASS")
        message = response.get("message", "")

        if result == "PASS":
            return {
                "passed": True,
                "message": f"Confirmed: policy does NOT grant these actions: {actions}",
                "violations": [],
            }
        else:
            # Extract which actions are granted
            reasons = response.get("reasons", [])
            violations = []
            for reason in reasons:
                violations.append({
                    "description": reason.get("description", ""),
                    "statement_index": reason.get("statementIndex"),
                })

            return {
                "passed": False,
                "message": f"VIOLATION: Policy grants one or more of these actions: {actions}",
                "violations": violations,
            }

    except access_analyzer_client.exceptions.ValidationException as e:
        return {
            "passed": None,
            "message": f"Could not validate (invalid input): {e}",
            "violations": [],
        }
    except Exception as e:
        return {
            "passed": None,
            "message": f"CheckAccessNotGranted unavailable: {e}",
            "violations": [],
        }


def _analyze_security_patterns(policy_json: dict, results: dict):
    """Check for dangerous permission patterns."""
    dangerous_actions = {
        "iam:*": "Full IAM access — can create admins, delete policies, escalate privileges",
        "iam:CreateUser": "Can create new IAM users — privilege escalation risk",
        "iam:CreateRole": "Can create new roles — privilege escalation risk",
        "iam:AttachUserPolicy": "Can attach policies to users — privilege escalation",
        "iam:AttachRolePolicy": "Can attach policies to roles — privilege escalation",
        "iam:PutUserPolicy": "Can add inline policies — privilege escalation",
        "iam:PutRolePolicy": "Can add inline policies to roles — privilege escalation",
        "iam:CreatePolicyVersion": "Can modify existing policies — privilege escalation",
        "iam:PassRole": "Can pass roles to services — potential privilege escalation",
        "sts:AssumeRole": "Can assume other roles — needs resource restriction",
        "s3:*": "Full S3 access — consider restricting to specific buckets",
        "ec2:*": "Full EC2 access — very broad, includes network and security group changes",
        "lambda:*": "Full Lambda access — can execute arbitrary code",
        "kms:*": "Full KMS access — can decrypt all data",
        "organizations:*": "Full Organizations access — can modify account structure",
    }

    for statement in policy_json.get("Statement", []):
        if statement.get("Effect") != "Allow":
            continue

        actions = statement.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]

        resources = statement.get("Resource", [])
        if isinstance(resources, str):
            resources = [resources]

        # Check for wildcard action
        if "*" in actions:
            results["security_analysis"]["wildcards"].append({
                "issue": "Action: * grants ALL permissions for ALL services",
                "resource": resources,
            })
            results["findings"].append({
                "type": "WARNING",
                "severity": "CRITICAL",
                "message": "Wildcard Action:* grants all permissions. This is almost never appropriate.",
                "source": "security_analysis",
            })

        # Check for wildcard resource with broad actions
        if "*" in resources:
            broad_actions = [a for a in actions if a.endswith(":*") or a == "*"]
            if broad_actions:
                results["security_analysis"]["wildcards"].append({
                    "issue": f"Broad actions {broad_actions} with Resource:*",
                    "actions": broad_actions,
                })

        # Check for known dangerous patterns
        for action in actions:
            action_lower = action.lower()
            for dangerous, description in dangerous_actions.items():
                if action_lower == dangerous.lower():
                    # Only flag if resource is also broad
                    if "*" in resources or not resources:
                        results["security_analysis"]["dangerous_patterns"].append({
                            "action": action,
                            "description": description,
                            "resource": resources,
                        })
                        results["findings"].append({
                            "type": "WARNING",
                            "severity": "HIGH",
                            "message": f"{action}: {description}",
                            "source": "security_analysis",
                        })


def _check_missing_conditions(policy_json: dict, results: dict):
    """Check for sensitive actions that should have conditions."""
    condition_recommendations = {
        "sts:AssumeRole": {
            "recommended": ["aws:PrincipalOrgID", "aws:SourceAccount"],
            "message": "AssumeRole without conditions allows any principal to assume. Add aws:PrincipalOrgID or aws:SourceAccount.",
        },
        "s3:PutObject": {
            "recommended": ["s3:x-amz-server-side-encryption"],
            "message": "PutObject without encryption condition allows unencrypted uploads.",
        },
        "s3:GetObject": {
            "recommended": ["aws:SourceVpc", "aws:SourceIp"],
            "message": "Consider restricting GetObject by VPC or IP for sensitive data.",
        },
        "kms:Decrypt": {
            "recommended": ["kms:ViaService"],
            "message": "Decrypt without kms:ViaService allows direct decryption outside service context.",
        },
        "kms:CreateGrant": {
            "recommended": ["kms:GrantIsForAWSResource"],
            "message": "CreateGrant without GrantIsForAWSResource allows arbitrary grant creation.",
        },
    }

    for statement in policy_json.get("Statement", []):
        if statement.get("Effect") != "Allow":
            continue

        actions = statement.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]

        conditions = statement.get("Condition", {})
        has_conditions = bool(conditions)

        for action in actions:
            if action in condition_recommendations and not has_conditions:
                rec = condition_recommendations[action]
                results["security_analysis"]["missing_conditions"].append({
                    "action": action,
                    "recommended_conditions": rec["recommended"],
                    "message": rec["message"],
                })
                results["findings"].append({
                    "type": "SUGGESTION",
                    "severity": "MEDIUM",
                    "message": rec["message"],
                    "source": "security_analysis",
                    "action": action,
                })
