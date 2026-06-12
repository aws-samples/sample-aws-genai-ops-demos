"""Output assembly — generates summary reports and MITRE ATT&CK coverage matrix."""

import argparse
import json
import os
import re
from datetime import datetime, timezone


def load_threat_assessment(output_dir):
    """Load the threat assessment from the reports directory."""
    path = os.path.join(output_dir, "reports", "threat-assessment.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def load_architecture_profile(output_dir):
    """Load the architecture profile from the reports directory."""
    path = os.path.join(output_dir, "reports", "architecture-profile.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def generate_architecture_summary(profile, output_dir):
    """Generate a human-readable architecture summary markdown."""
    ri = profile.get("risk_indicators", {})
    network = profile.get("network", {})
    compute = profile.get("compute", {})
    data = profile.get("data_stores", {})
    identity = profile.get("identity", {})
    endpoints = profile.get("endpoints", {})

    lines = [
        f"# Architecture Profile — {profile.get('account_id', 'Unknown')}",
        f"",
        f"**Region**: {profile.get('region', 'Unknown')}",
        f"**Scanned**: {profile.get('scan_timestamp', 'Unknown')}",
        f"",
        f"## Network",
        f"- VPCs: {len(network.get('vpcs', []))}",
        f"- Public subnets: {len(network.get('public_subnets', []))}",
        f"- Internet gateways: {network.get('internet_gateways', 0)}",
        f"- Security groups: {network.get('security_groups_total', 0)}",
        f"- Risky SGs (0.0.0.0/0 ingress): {len(network.get('risky_security_groups', []))}",
        f"",
        f"## Compute",
        f"- EC2 instances: {len(compute.get('ec2_instances', []))}",
        f"- Lambda functions: {len(compute.get('lambda_functions', []))}",
        f"- ECS clusters: {len(compute.get('ecs_clusters', []))}",
        f"- EKS clusters: {len(compute.get('eks_clusters', []))}",
        f"",
        f"## Data Stores",
        f"- S3 buckets: {len(data.get('s3_buckets', []))}",
        f"- RDS instances: {len(data.get('rds_instances', []))}",
        f"- DynamoDB tables: {len(data.get('dynamodb_tables', []))}",
        f"",
        f"## Identity",
        f"- IAM roles: {identity.get('total_roles', 0)}",
        f"- IAM users: {identity.get('total_users', 0)}",
        f"- Overprivileged roles: {', '.join(ri.get('overprivileged_roles', [])) or 'None'}",
        f"- Long-lived access keys: {ri.get('long_lived_access_keys', 0)}",
        f"",
        f"## Endpoints",
        f"- Load balancers: {len(endpoints.get('load_balancers', []))}",
        f"- API Gateways: {len(endpoints.get('api_gateways', []))}",
        f"- Public endpoints: {ri.get('public_endpoints_count', 0)}",
        f"",
        f"## Risk Summary",
        f"| Indicator | Count |",
        f"|---|---|",
        f"| Public endpoints | {ri.get('public_endpoints_count', 0)} |",
        f"| Risky security groups | {ri.get('risky_security_groups_count', 0)} |",
        f"| Overprivileged roles | {len(ri.get('overprivileged_roles', []))} |",
        f"| Long-lived access keys | {ri.get('long_lived_access_keys', 0)} |",
        f"| Public S3 buckets | {len(ri.get('public_s3_buckets', []))} |",
        f"| Publicly accessible RDS | {len(ri.get('publicly_accessible_rds', []))} |",
        f"| EC2 with public IP | {len(ri.get('ec2_with_public_ip', []))} |",
    ]

    path = os.path.join(output_dir, "reports", "architecture-profile.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def generate_attack_coverage_matrix(threats, output_dir):
    """Generate a MITRE ATT&CK coverage matrix from threat assessments."""
    # Collect all technique mappings
    technique_map = {}  # technique_id -> list of playbook names
    for threat in threats:
        name = threat.get("threat_name", "Unknown")
        slug = name.lower().replace(" ", "-").replace("/", "-").replace(":", "")
        for tech_id in threat.get("mitre_attack_techniques", []):
            if tech_id not in technique_map:
                technique_map[tech_id] = []
            technique_map[tech_id].append({"name": name, "file": f"{slug}.md"})

    lines = [
        "# MITRE ATT&CK Coverage Matrix",
        "",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Total Techniques Covered**: {len(technique_map)}",
        f"**Total Playbooks**: {len(threats)}",
        "",
        "## Coverage Table",
        "",
        "| ATT&CK Technique | Playbook | Severity |",
        "|---|---|---|",
    ]

    for tech_id in sorted(technique_map.keys()):
        for playbook in technique_map[tech_id]:
            # Find severity from threats
            severity = "—"
            for t in threats:
                if t.get("threat_name") == playbook["name"]:
                    severity = t.get("severity", "—")
                    break
            lines.append(f"| {tech_id} | [{playbook['name']}](../playbooks/{playbook['file']}) | {severity} |")

    lines.extend([
        "",
        "## Threat Scenarios",
        "",
        "| # | Threat | Likelihood | Severity | ATT&CK Techniques | Affected Resources |",
        "|---|---|---|---|---|---|",
    ])

    for i, threat in enumerate(threats, 1):
        techniques = ", ".join(threat.get("mitre_attack_techniques", []))
        resources = ", ".join(threat.get("affected_resources", [])[:3])
        if len(threat.get("affected_resources", [])) > 3:
            resources += "..."
        lines.append(
            f"| {i} | {threat.get('threat_name', '')} | {threat.get('likelihood', '')} "
            f"| {threat.get('severity', '')} | {techniques} | {resources} |"
        )

    path = os.path.join(output_dir, "reports", "attack-coverage-matrix.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def generate_threat_assessment_md(threats, output_dir):
    """Generate a human-readable threat assessment markdown."""
    lines = [
        "# Threat Assessment",
        "",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Scenarios Identified**: {len(threats)}",
        "",
    ]

    for i, threat in enumerate(threats, 1):
        lines.extend([
            f"## {i}. {threat.get('threat_name', 'Unknown')}",
            "",
            f"**Likelihood**: {threat.get('likelihood', 'Unknown')} | "
            f"**Severity**: {threat.get('severity', 'Unknown')}",
            "",
            f"{threat.get('description', '')}",
            "",
            f"**Rationale**: {threat.get('rationale', '')}",
            "",
            f"**MITRE ATT&CK**: {', '.join(threat.get('mitre_attack_techniques', []))}",
            "",
            f"**Affected Resources**: {', '.join(threat.get('affected_resources', []))}",
            "",
            "---",
            "",
        ])

    path = os.path.join(output_dir, "reports", "threat-assessment.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Assemble output reports and coverage matrix")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--output-format", default="both", choices=["ssm", "markdown", "both"])
    args = parser.parse_args()

    profile = load_architecture_profile(args.output_dir)
    threats = load_threat_assessment(args.output_dir)

    if profile:
        generate_architecture_summary(profile, args.output_dir)
        print("    ✓ Architecture summary")

    if threats:
        generate_attack_coverage_matrix(threats, args.output_dir)
        print("    ✓ MITRE ATT&CK coverage matrix")
        generate_threat_assessment_md(threats, args.output_dir)
        print("    ✓ Threat assessment report")


if __name__ == "__main__":
    main()
