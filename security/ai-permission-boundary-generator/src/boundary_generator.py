"""Bedrock-powered permission boundary generator."""

import argparse
import json
import sys
from typing import Any

import boto3

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"


def generate_boundary(
    used_actions: list[str],
    granted_actions: list[str],
    identity: str = "target-identity",
    model_id: str = DEFAULT_MODEL_ID,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Generate a least-privilege permission boundary using Bedrock.

    Args:
        used_actions: Actions actually used (from analyzer).
        granted_actions: Actions currently granted (from policy_fetcher).
        identity: Name of the identity for labeling.
        model_id: Bedrock model ID.
        region: AWS region for Bedrock.

    Returns:
        Dict with boundary_policy, summary, recommendations, cdk_construct.
    """
    client = boto3.client("bedrock-runtime", region_name=region)

    prompt = f"""You are an AWS IAM security expert. Generate a least-privilege permission boundary policy.

Context:
- Identity: {identity}
- Actions actually used (from CloudTrail): {json.dumps(used_actions)}
- Actions currently granted: {json.dumps(granted_actions[:100])}{"... (truncated)" if len(granted_actions) > 100 else ""}

Generate a JSON response with ONLY these keys:
1. "boundary_policy": A valid IAM policy document (Version 2012-10-17) that allows the used actions plus reasonable headroom for related operations. Use "Resource": "*".
2. "recommendations": A list of 3-5 string recommendations for further tightening.
3. "cdk_construct": A short CDK Python snippet that creates this as a managed policy.

Return ONLY valid JSON, no markdown fences, no explanation outside the JSON."""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    })

    response = client.invoke_model(modelId=model_id, body=body, contentType="application/json")
    response_body = json.loads(response["body"].read())
    assistant_text = response_body["content"][0]["text"]

    # Parse the JSON from the model response
    # Handle potential markdown code fences
    text = assistant_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # If response was truncated, try to extract what we can
        # Build a minimal boundary from used_actions directly
        parsed = {
            "boundary_policy": {
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Action": used_actions, "Resource": "*"}],
            },
            "recommendations": ["Response was truncated — boundary built directly from used actions"],
            "cdk_construct": "# Auto-generated from used actions\n",
        }

    boundary_policy = parsed["boundary_policy"]
    boundary_actions = []
    for stmt in boundary_policy.get("Statement", []):
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        boundary_actions.extend(actions)

    granted_count = len(granted_actions)
    boundary_count = len(boundary_actions)
    # If granted actions include wildcard (*), treat as "unlimited" for reduction calc
    has_wildcard = "*" in granted_actions or any(a.endswith(":*") for a in granted_actions)
    if has_wildcard:
        # Estimate ~7000+ AWS actions available; boundary is a massive reduction
        effective_granted = max(7000, boundary_count * 10)
        reduction = ((effective_granted - boundary_count) / effective_granted * 100)
    elif granted_count > 0:
        reduction = ((granted_count - boundary_count) / granted_count * 100)
    else:
        reduction = 0.0

    return {
        "boundary_policy": boundary_policy,
        "summary": {
            "used_actions_count": len(used_actions),
            "granted_actions_count": granted_count,
            "boundary_actions_count": boundary_count,
            "reduction_percentage": round(reduction, 1),
        },
        "recommendations": parsed.get("recommendations", []),
        "cdk_construct": parsed.get("cdk_construct", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate permission boundary using Bedrock")
    parser.add_argument("--analysis-file", required=True, help="Path to analyzer output JSON")
    parser.add_argument("--policies-file", required=True, help="Path to policy_fetcher output JSON")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="Bedrock model ID")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    args = parser.parse_args()

    with open(args.analysis_file) as f:
        analysis = json.load(f)
    with open(args.policies_file) as f:
        policies = json.load(f)

    result = generate_boundary(
        used_actions=analysis["used_actions"],
        granted_actions=policies["granted_actions"],
        identity=analysis["identity"],
        model_id=args.model_id,
        region=args.region,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
