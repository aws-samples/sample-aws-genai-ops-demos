"""Output formatting - generates reports and policy files."""

import argparse
import json
import os
from typing import Any

import boto3


def generate_outputs(
    analysis: dict[str, Any],
    policies: dict[str, Any],
    boundary: dict[str, Any],
    output_dir: str = "./output",
    s3_bucket: str | None = None,
    s3_prefix: str = "permission-boundaries/",
) -> dict[str, str]:
    """Generate all output files from the analysis results.

    Args:
        analysis: Output from analyzer.analyze_usage().
        policies: Output from policy_fetcher.fetch_policies().
        boundary: Output from boundary_generator.generate_boundary().
        output_dir: Local directory for output files.
        s3_bucket: Optional S3 bucket for upload.
        s3_prefix: S3 key prefix.

    Returns:
        Dict mapping output file names to their paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    outputs: dict[str, str] = {}

    # 1. boundary-policy.json
    policy_path = os.path.join(output_dir, "boundary-policy.json")
    with open(policy_path, "w") as f:
        json.dump(boundary["boundary_policy"], f, indent=2)
    outputs["boundary-policy.json"] = policy_path

    # 2. before-after-comparison.md
    comparison_path = os.path.join(output_dir, "before-after-comparison.md")
    summary = boundary["summary"]
    has_wildcard = "*" in policies["granted_actions"]
    no_activity = analysis["event_count"] == 0

    with open(comparison_path, "w") as f:
        f.write(f"# Permission Boundary: Before & After Comparison\n\n")
        f.write(f"**Identity:** {analysis['identity']} ({policies['identity_type']})  \n")
        f.write(f"**Analysis Period:** {analysis['period_days']} days  \n")
        f.write(f"**CloudTrail Events:** {analysis['event_count']}\n\n")

        if no_activity:
            f.write(f"> ⚠️ No CloudTrail activity found for this identity in the last {analysis['period_days']} days. "
                    f"Boundary is based on granted permissions analysis only.\n\n")

        if has_wildcard:
            f.write(f"> 🔴 This identity has **wildcard (`*`) access** — effectively all ~7,000+ AWS actions. "
                    f"The boundary restricts this to only {summary['boundary_actions_count']} specific actions.\n\n")

        f.write(f"## Summary\n\n")
        f.write(f"| Metric | Value |\n|--------|-------|\n")
        f.write(f"| Actions Used (CloudTrail, {analysis['period_days']}d) | {summary['used_actions_count']} |\n")
        if has_wildcard:
            f.write(f"| Actions Granted (Current) | **ALL** (wildcard `*`) |\n")
        else:
            f.write(f"| Actions Granted (Current) | {summary['granted_actions_count']} |\n")
        f.write(f"| Actions in Boundary | {summary['boundary_actions_count']} |\n")
        f.write(f"| **Attack Surface Reduction** | **{summary['reduction_percentage']}%** |\n\n")

        # Before section
        f.write(f"## Granted Permissions (Before)\n\n")
        if has_wildcard:
            f.write(f"🔓 **Full admin access** — all AWS API actions allowed:\n\n")
        for action in policies["granted_actions"]:
            f.write(f"- `{action}`\n")

        # After section
        f.write(f"\n## Permission Boundary (After)\n\n")
        f.write(f"🔒 **Least-privilege boundary** — only {summary['boundary_actions_count']} actions:\n\n")
        boundary_actions_list = []
        for stmt in boundary["boundary_policy"].get("Statement", []):
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            boundary_actions_list.extend(actions)
            for action in actions:
                f.write(f"- ✅ `{action}`\n")

        # Removed section
        f.write(f"\n## Permissions Removed\n\n")
        boundary_actions_set = set(boundary_actions_list)
        if has_wildcard:
            f.write(f"Wildcard `*` removed — replaced with {len(boundary_actions_set)} explicit actions above.\n")
            f.write(f"This eliminates access to ~6,900+ unused AWS API actions.\n")
        else:
            removed = sorted(set(policies["granted_actions"]) - boundary_actions_set)
            if removed:
                f.write(f"**{len(removed)} actions removed:**\n\n")
                for action in removed:
                    f.write(f"- ❌ ~~`{action}`~~\n")
            else:
                f.write(f"No actions removed — boundary matches current grants.\n")
    outputs["before-after-comparison.md"] = comparison_path

    # 3. boundary-cdk-construct.py
    cdk_path = os.path.join(output_dir, "boundary-cdk-construct.py")
    with open(cdk_path, "w") as f:
        f.write(boundary.get("cdk_construct", "# No CDK construct generated\n"))
    outputs["boundary-cdk-construct.py"] = cdk_path

    # 4. analysis-report.md
    report_path = os.path.join(output_dir, "analysis-report.md")
    with open(report_path, "w") as f:
        f.write(f"# Permission Boundary Analysis Report\n\n")
        f.write(f"**Identity:** {analysis['identity']}  \n")
        f.write(f"**Type:** {policies['identity_type']}  \n")
        f.write(f"**Analysis Period:** {analysis['period_days']} days  \n")
        f.write(f"**Total Events Analyzed:** {analysis['event_count']}  \n\n")
        f.write(f"## Services Used\n\n")
        for svc in analysis["used_services"]:
            f.write(f"- {svc}\n")
        f.write(f"\n## Attached Policies\n\n")
        for p in policies["policies"]:
            f.write(f"- **{p['name']}** ({p['type']})\n")
        f.write(f"\n## Admin Access Detected\n\n")
        f.write(f"{'⚠️  YES — this identity has admin-level access' if policies['has_admin_access'] else '✅ No admin access detected'}\n\n")
        f.write(f"## Recommendations\n\n")
        for rec in boundary.get("recommendations", []):
            f.write(f"- {rec}\n")
    outputs["analysis-report.md"] = report_path

    # Optional S3 upload
    if s3_bucket:
        s3 = boto3.client("s3")
        for filename, filepath in outputs.items():
            key = f"{s3_prefix}{analysis['identity']}/{filename}"
            s3.upload_file(filepath, s3_bucket, key)
            print(f"Uploaded: s3://{s3_bucket}/{key}")

    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate output files from analysis results")
    parser.add_argument("--analysis-file", required=True, help="Path to analyzer output JSON")
    parser.add_argument("--policies-file", required=True, help="Path to policy_fetcher output JSON")
    parser.add_argument("--boundary-file", required=True, help="Path to boundary_generator output JSON")
    parser.add_argument("--output-dir", default="./output", help="Output directory")
    parser.add_argument("--s3-bucket", default=None, help="Optional S3 bucket for upload")
    parser.add_argument("--s3-prefix", default="permission-boundaries/", help="S3 key prefix")
    args = parser.parse_args()

    with open(args.analysis_file) as f:
        analysis = json.load(f)
    with open(args.policies_file) as f:
        policies = json.load(f)
    with open(args.boundary_file) as f:
        boundary = json.load(f)

    outputs = generate_outputs(
        analysis=analysis,
        policies=policies,
        boundary=boundary,
        output_dir=args.output_dir,
        s3_bucket=args.s3_bucket,
        s3_prefix=args.s3_prefix,
    )
    print(f"\nGenerated {len(outputs)} files in {args.output_dir}:")
    for name, path in outputs.items():
        print(f"  - {name}")


if __name__ == "__main__":
    main()
