#!/usr/bin/env python3
"""Local test harness for individual tool Lambda functions.

Run this against your actual AWS account (requires configured credentials)
to verify each tool works before deploying CDK.

Usage:
    cd ai-iam-access-analyzer-assistant
    python -m tests.test_tools_local

    # Or test a specific tool:
    python -m tests.test_tools_local --tool list_findings
    python -m tests.test_tools_local --tool get_finding_details --finding-id "arn:aws:..."
    python -m tests.test_tools_local --tool generate_policy --role-name MyRole
    python -m tests.test_tools_local --tool check_dependencies --entity-arn "arn:aws:iam::123:role/MyRole"
    python -m tests.test_tools_local --tool validate_policy
"""

import argparse
import json
import sys
import os

# Add src to path for local imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_list_findings(args):
    """Test list_findings tool."""
    from tools.list_findings import handler

    print("\n--- Testing list_findings ---")
    print("Querying Security Hub for IAM Access Analyzer findings...\n")

    # Test 1: Get all active findings
    result = handler({"status": "ACTIVE", "limit": 10})
    _print_result("All active findings (limit 10)", result)

    if result.get("error"):
        print(f"\n  ERROR: {result['error']}")
        print("  Make sure Security Hub is enabled with IAM Access Analyzer integration.")
        return

    # Test 2: Filter by severity
    if result.get("total_count", 0) > 0:
        result_critical = handler({"severity": "CRITICAL", "limit": 5})
        _print_result("Critical findings only", result_critical)

        result_high = handler({"severity": "HIGH", "limit": 5})
        _print_result("High findings only", result_high)

    print("\n  list_findings: PASSED")


def test_get_finding_details(args):
    """Test get_finding_details tool."""
    from tools.get_finding_details import handler
    from tools.list_findings import handler as list_handler

    print("\n--- Testing get_finding_details ---")

    # First get a finding ID to look up
    finding_id = args.finding_id
    if not finding_id:
        print("  No --finding-id provided, fetching first available finding...")
        list_result = list_handler({"status": "ACTIVE", "limit": 1})
        findings = list_result.get("findings", [])
        if not findings:
            print("  No findings available. Skipping get_finding_details test.")
            print("  (Enable IAM Access Analyzer and wait for findings to generate)")
            return
        finding_id = findings[0]["id"]
        print(f"  Using finding: {findings[0]['title']}")

    result = handler({
        "finding_id": finding_id,
        "include_resource_details": True,
        "include_related_findings": True,
    })
    _print_result("Finding details", result)

    if result.get("error"):
        print(f"\n  ERROR: {result['error']}")
        return

    # Verify key sections are populated
    assert "finding" in result, "Missing 'finding' key"
    assert "risk_assessment" in result, "Missing 'risk_assessment' key"
    assert "remediation_steps" in result, "Missing 'remediation_steps' key"

    print(f"\n  Risk level: {result['risk_assessment'].get('risk_level', 'N/A')}")
    print(f"  Remediation steps: {len(result.get('remediation_steps', []))}")
    print("\n  get_finding_details: PASSED")


def test_generate_policy(args):
    """Test generate_policy tool."""
    from tools.generate_policy import handler

    print("\n--- Testing generate_policy ---")

    role_name = args.role_name
    if not role_name:
        # Try to find a role to test with
        import boto3
        iam = boto3.client("iam")
        roles = iam.list_roles(MaxItems=5)
        # Pick first non-service-linked role
        for role in roles.get("Roles", []):
            if not role["RoleName"].startswith("AWSServiceRoleFor"):
                role_name = role["RoleName"]
                break

        if not role_name:
            print("  No suitable role found for testing. Use --role-name to specify one.")
            return

    print(f"  Analyzing role: {role_name}")
    print(f"  Lookback: 30 days (shortened for testing)\n")

    result = handler({
        "role_name": role_name,
        "lookback_days": 30,
        "output_format": "json",
    })
    _print_result("Policy generation result", result)

    if result.get("error"):
        print(f"\n  ERROR: {result['error']}")
        return

    # Show key metrics
    metrics = result.get("reduction_metrics", {})
    print(f"\n  Events analyzed: {result.get('events_analyzed', 0)}")
    print(f"  Current actions granted: {metrics.get('current_actions', 'N/A')}")
    print(f"  Actions actually used: {metrics.get('proposed_actions', 'N/A')}")
    print(f"  Reduction: {metrics.get('reduction_percentage', 'N/A')}%")

    if result.get("warnings"):
        print(f"\n  Warnings:")
        for w in result["warnings"]:
            print(f"    - {w}")

    print("\n  generate_policy: PASSED")


def test_check_dependencies(args):
    """Test check_dependencies tool."""
    from tools.check_dependencies import handler

    print("\n--- Testing check_dependencies ---")

    entity_arn = args.entity_arn
    if not entity_arn:
        # Find a role to test with
        import boto3
        iam = boto3.client("iam")
        roles = iam.list_roles(MaxItems=10)
        for role in roles.get("Roles", []):
            if not role["RoleName"].startswith("AWSServiceRoleFor"):
                entity_arn = role["Arn"]
                break

        if not entity_arn:
            print("  No suitable entity found. Use --entity-arn to specify one.")
            return

    print(f"  Analyzing: {entity_arn}\n")

    result = handler({
        "entity_arn": entity_arn,
        "depth": 2,
        "include_service_linked": False,
    })
    _print_result("Dependency analysis", result)

    if result.get("error"):
        print(f"\n  ERROR: {result['error']}")
        return

    # Show key findings
    risk = result.get("risk_score", {})
    graph = result.get("dependency_graph", {})
    print(f"\n  Risk level: {risk.get('level', 'N/A')} (score: {risk.get('score', 0)})")
    print(f"  Impact radius: {graph.get('total_impact_radius', 0)} entities")
    print(f"  Trust relationships: {len(result.get('trust_relationships', []))}")
    print(f"  Policy attachments: {len(result.get('policy_attachments', []))}")

    if risk.get("factors"):
        print(f"\n  Risk factors:")
        for f in risk["factors"]:
            print(f"    - {f}")

    print("\n  check_dependencies: PASSED")


def test_validate_policy(args):
    """Test validate_policy tool."""
    from tools.validate_policy import handler

    print("\n--- Testing validate_policy ---")

    # Test 1: Valid but overly broad policy
    print("\n  Test 1: Overly broad policy (should produce warnings)")
    broad_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "s3:*",
                "Resource": "*",
            }
        ],
    })
    result = handler({"policy_document": broad_policy, "validation_type": "all"})
    _print_result("Broad policy validation", result)
    print(f"  Valid: {result.get('is_valid')}")
    print(f"  Findings: {result.get('summary', {}).get('total_findings', 0)}")
    print(f"  Verdict: {result.get('summary', {}).get('verdict', 'N/A')}")

    # Test 2: Privilege escalation pattern
    print("\n  Test 2: Privilege escalation pattern (should flag)")
    escalation_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["iam:CreateUser", "iam:AttachUserPolicy", "iam:CreateRole"],
                "Resource": "*",
            }
        ],
    })
    result = handler({"policy_document": escalation_policy, "validation_type": "all"})
    _print_result("Escalation policy validation", result)
    dangerous = result.get("security_analysis", {}).get("dangerous_patterns", [])
    print(f"  Dangerous patterns found: {len(dangerous)}")

    # Test 3: Minimal valid policy
    print("\n  Test 3: Minimal least-privilege policy (should pass clean)")
    minimal_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    "arn:aws:s3:::my-bucket",
                    "arn:aws:s3:::my-bucket/*",
                ],
            }
        ],
    })
    result = handler({"policy_document": minimal_policy, "validation_type": "all"})
    _print_result("Minimal policy validation", result)
    print(f"  Valid: {result.get('is_valid')}")
    print(f"  Verdict: {result.get('summary', {}).get('verdict', 'N/A')}")

    # Test 4: Invalid JSON
    print("\n  Test 4: Invalid JSON (should return error)")
    result = handler({"policy_document": "not valid json {{{", "validation_type": "syntax"})
    print(f"  Valid: {result.get('is_valid')} (expected: False)")

    # Test 5: CheckAccessNotGranted
    print("\n  Test 5: CheckAccessNotGranted (verify actions are blocked)")
    result = handler({
        "policy_document": minimal_policy,
        "check_actions_not_granted": ["iam:CreateUser", "iam:DeleteRole"],
    })
    check = result.get("access_not_granted_check", {})
    print(f"  Check passed: {check.get('passed')} (expected: True — s3 policy shouldn't grant iam actions)")

    print("\n  validate_policy: PASSED")


def _print_result(label: str, result: dict):
    """Pretty-print a result dict."""
    print(f"\n  [{label}]")
    # Print compact JSON with truncation for large outputs
    output = json.dumps(result, indent=2, default=str)
    lines = output.split("\n")
    if len(lines) > 40:
        print("  " + "\n  ".join(lines[:20]))
        print(f"  ... ({len(lines) - 40} lines truncated) ...")
        print("  " + "\n  ".join(lines[-20:]))
    else:
        print("  " + "\n  ".join(lines))


def main():
    parser = argparse.ArgumentParser(
        description="Local test harness for IAM Analyzer Assistant tools"
    )
    parser.add_argument(
        "--tool",
        choices=["list_findings", "get_finding_details", "generate_policy", "check_dependencies", "validate_policy", "all"],
        default="all",
        help="Which tool to test (default: all)",
    )
    parser.add_argument("--role-name", help="Role name for generate_policy test")
    parser.add_argument("--entity-arn", help="Entity ARN for check_dependencies test")
    parser.add_argument("--finding-id", help="Finding ID for get_finding_details test")

    args = parser.parse_args()

    print("=" * 60)
    print(" AI IAM Access Analyzer Assistant — Local Tool Tests")
    print("=" * 60)
    print(f"\n  Using credentials from environment/AWS CLI config")

    import boto3
    sts = boto3.client("sts")
    try:
        identity = sts.get_caller_identity()
        print(f"  Account: {identity['Account']}")
        print(f"  Identity: {identity['Arn']}")
        print(f"  Region: {boto3.session.Session().region_name}")
    except Exception as e:
        print(f"\n  ERROR: Cannot connect to AWS: {e}")
        print("  Make sure your credentials are configured.")
        sys.exit(1)

    tools_to_test = {
        "list_findings": test_list_findings,
        "get_finding_details": test_get_finding_details,
        "generate_policy": test_generate_policy,
        "check_dependencies": test_check_dependencies,
        "validate_policy": test_validate_policy,
    }

    if args.tool == "all":
        for name, test_fn in tools_to_test.items():
            try:
                test_fn(args)
            except Exception as e:
                print(f"\n  {name}: FAILED — {e}")
    else:
        try:
            tools_to_test[args.tool](args)
        except Exception as e:
            print(f"\n  {args.tool}: FAILED — {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(" Tests complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
