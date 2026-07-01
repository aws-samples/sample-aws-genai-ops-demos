"""AI Permission Boundary Generator - Main Entry Point."""

import argparse
import json
import os
import sys

import boto3


def main():
    parser = argparse.ArgumentParser(description="Generate least-privilege permission boundaries")
    parser.add_argument("--role-name", help="IAM role name to analyze")
    parser.add_argument("--user-name", help="IAM user name to analyze")
    parser.add_argument("--days", type=int, default=30, help="Days of CloudTrail history")
    parser.add_argument("--model-id", default="us.anthropic.claude-sonnet-4-20250514-v1:0")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--bucket-name", help="S3 bucket for upload")
    args = parser.parse_args()

    if not args.role_name and not args.user_name:
        parser.error("Either --role-name or --user-name must be provided")

    identity = args.role_name or args.user_name
    identity_type = "role" if args.role_name else "user"

    # Detect region
    session = boto3.session.Session()
    region = session.region_name or "us-east-1"

    print(f"Identity: {identity} ({identity_type})")
    print(f"Lookback: {args.days} days | Region: {region}")
    print()

    # Step 1: Analyze CloudTrail
    print("[1/4] Analyzing CloudTrail logs...")
    from analyzer import analyze_cloudtrail
    analysis = analyze_cloudtrail(identity, identity_type, args.days, region)

    # Step 2: Fetch current policies
    print("[2/4] Fetching current IAM policies...")
    from policy_fetcher import fetch_policies
    policies = fetch_policies(identity, region)

    # Step 3: Generate boundary
    print("[3/4] Generating permission boundary with Bedrock...")
    from boundary_generator import generate_boundary
    boundary = generate_boundary(
        used_actions=analysis["used_actions"],
        granted_actions=policies["granted_actions"],
        identity=identity,
        model_id=args.model_id,
        region=region,
    )
    print(f"       Reduction: {boundary['summary']['reduction_percentage']}%")

    # Step 4: Write output
    print("[4/4] Writing results...")
    from output import generate_outputs
    outputs = generate_outputs(
        analysis=analysis,
        policies=policies,
        boundary=boundary,
        output_dir=args.output_dir,
        s3_bucket=args.bucket_name,
    )
    for name in outputs:
        print(f"       {name}")

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
