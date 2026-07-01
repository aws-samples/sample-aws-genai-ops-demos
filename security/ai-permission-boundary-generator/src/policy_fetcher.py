"""IAM policy fetcher - resolves all permissions granted to an identity."""

import argparse
import json
import sys
from typing import Any

import boto3


def _extract_actions(statement: dict) -> list[str]:
    """Extract action strings from a policy statement."""
    if statement.get("Effect") != "Allow":
        return []
    actions = statement.get("Action", [])
    if isinstance(actions, str):
        actions = [actions]
    return actions


def _get_policy_document(iam: Any, policy_arn: str) -> dict:
    """Get the default version document for a managed policy."""
    policy = iam.get_policy(PolicyArn=policy_arn)["Policy"]
    version_id = policy["DefaultVersionId"]
    version = iam.get_policy_version(PolicyArn=policy_arn, VersionId=version_id)
    return version["PolicyVersion"]["Document"]


def fetch_policies(identity_name: str, region: str = "us-east-1") -> dict[str, Any]:
    """Fetch all permissions granted to the given IAM role or user.

    Args:
        identity_name: IAM role or user name.
        region: AWS region.

    Returns:
        Dict with identity, identity_type, granted_actions, policies, has_admin_access.
    """
    iam = boto3.client("iam", region_name=region)

    identity_type = "role"
    policies_info: list[dict] = []
    all_actions: set[str] = set()

    # Try as role first, fall back to user
    try:
        iam.get_role(RoleName=identity_name)
        identity_type = "role"
    except iam.exceptions.NoSuchEntityException:
        try:
            iam.get_user(UserName=identity_name)
            identity_type = "user"
        except iam.exceptions.NoSuchEntityException:
            raise ValueError(f"Identity '{identity_name}' not found as role or user")

    if identity_type == "role":
        # Attached managed policies
        attached = iam.list_attached_role_policies(RoleName=identity_name)
        for p in attached.get("AttachedPolicies", []):
            doc = _get_policy_document(iam, p["PolicyArn"])
            policies_info.append({"name": p["PolicyName"], "arn": p["PolicyArn"], "type": "managed"})
            for stmt in doc.get("Statement", []):
                all_actions.update(_extract_actions(stmt))

        # Inline policies
        inline_names = iam.list_role_policies(RoleName=identity_name).get("PolicyNames", [])
        for name in inline_names:
            resp = iam.get_role_policy(RoleName=identity_name, PolicyName=name)
            doc = resp["PolicyDocument"]
            policies_info.append({"name": name, "type": "inline"})
            for stmt in doc.get("Statement", []):
                all_actions.update(_extract_actions(stmt))
    else:
        # Attached managed policies
        attached = iam.list_attached_user_policies(UserName=identity_name)
        for p in attached.get("AttachedPolicies", []):
            doc = _get_policy_document(iam, p["PolicyArn"])
            policies_info.append({"name": p["PolicyName"], "arn": p["PolicyArn"], "type": "managed"})
            for stmt in doc.get("Statement", []):
                all_actions.update(_extract_actions(stmt))

        # Inline policies
        inline_names = iam.list_user_policies(UserName=identity_name).get("PolicyNames", [])
        for name in inline_names:
            resp = iam.get_user_policy(UserName=identity_name, PolicyName=name)
            doc = resp["PolicyDocument"]
            policies_info.append({"name": name, "type": "inline"})
            for stmt in doc.get("Statement", []):
                all_actions.update(_extract_actions(stmt))

    granted_actions = sorted(all_actions)
    has_admin_access = "*" in all_actions or any(
        a == "*:*" for a in all_actions
    )

    return {
        "identity": identity_name,
        "identity_type": identity_type,
        "granted_actions": granted_actions,
        "policies": policies_info,
        "has_admin_access": has_admin_access,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch IAM policies for an identity")
    parser.add_argument("--identity", required=True, help="IAM role or user name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    args = parser.parse_args()

    result = fetch_policies(args.identity, args.region)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
