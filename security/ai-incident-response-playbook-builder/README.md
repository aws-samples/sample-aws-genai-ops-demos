# AI Incident Response Playbook Builder

**Problem**: Your team knows it needs incident response playbooks, but writing them takes weeks of specialized effort — and generic templates don't reflect your actual architecture.

**Solution**: Point this tool at any AWS account and get tailored incident response playbooks with step-by-step containment, eradication, and recovery procedures — mapped to MITRE ATT&CK techniques and ready to execute as SSM Automation documents.

## What You Get

🔍 **Architecture Discovery**: Automated inventory of VPCs, public endpoints, IAM roles, data stores, and compute resources

📋 **Tailored Playbooks**: 6–12 incident response playbooks specific to your architecture's threat surface

🛡️ **MITRE ATT&CK Mapping**: Each playbook mapped to relevant ATT&CK technique IDs with a coverage matrix

⚙️ **Executable Outputs**: SSM Automation documents (JSON) ready to import into Systems Manager, plus markdown for team review

## How It Works

1. **Discover**: Scans your AWS account (read-only) to build an architecture profile — VPCs, subnets, public endpoints, IAM roles, S3 buckets, databases, compute resources
2. **Analyze & Generate**: Sends the architecture profile to Amazon Bedrock, which identifies the most likely threat scenarios and generates tailored playbooks
3. **Output**: Writes SSM Automation documents and markdown playbooks to a local directory, with a MITRE ATT&CK coverage matrix

**Processing Time**: ~2–8 minutes depending on account complexity

## Example Output

**Playbooks Generated** (varies by architecture):
- `playbooks/credential-compromise.md` — Compromised IAM credentials response
- `playbooks/data-exfiltration-s3.md` — S3 data exfiltration containment
- `playbooks/cryptomining.md` — Crypto mining detection and eradication
- `playbooks/ransomware.md` — Ransomware containment and recovery
- `playbooks/unauthorized-access.md` — Unauthorized public exposure response
- `playbooks/lateral-movement.md` — Cross-VPC lateral movement containment

**SSM Automation Documents**:
- `ssm-documents/credential-compromise.json` — Automated credential rotation and session revocation
- `ssm-documents/isolate-instance.json` — EC2 isolation with forensic snapshot
- `ssm-documents/s3-lockdown.json` — Emergency S3 bucket policy lockdown

**Summary Reports**:
- `reports/architecture-profile.md` — Discovered architecture summary
- `reports/attack-coverage-matrix.md` — MITRE ATT&CK technique coverage map
- `reports/threat-assessment.md` — Prioritized threat scenarios for your environment

## Architecture

```
AWS Account (read-only) → Discovery Module → Bedrock (Claude) → S3 Bucket
       │                        │                    │              │
  EC2, VPC, IAM,          Architecture          Threat analysis,  playbooks/*.md
  S3, RDS, Lambda,         Profile              playbook gen      ssm-documents/*.json
  ECS, EKS, ELB,                                                  reports/
  API Gateway
```

**What Gets Deployed**:
- **S3 Bucket**: `ir-playbooks-{account}-{region}` — stores generated playbooks (deployed via CDK)
- That's it. No compute, no Lambda, no persistent services. Discovery and generation run locally.

## Prerequisites

- AWS CLI 2.31.13+ with configured credentials
- Python 3.10+ and Node.js 20+ (for CDK)
- Amazon Bedrock access with Claude model enabled
- Read-only permissions across discovery services + `bedrock:InvokeModel`

### Required IAM Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Discovery",
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*",
        "iam:List*",
        "iam:GetRole",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "iam:GetRolePolicy",
        "s3:ListAllMyBuckets",
        "s3:GetBucketPolicy",
        "s3:GetBucketAcl",
        "s3:GetBucketPublicAccessBlock",
        "rds:DescribeDBInstances",
        "rds:DescribeDBClusters",
        "dynamodb:ListTables",
        "dynamodb:DescribeTable",
        "lambda:ListFunctions",
        "lambda:GetFunction",
        "lambda:GetPolicy",
        "ecs:ListClusters",
        "ecs:DescribeClusters",
        "ecs:ListServices",
        "ecs:DescribeServices",
        "eks:ListClusters",
        "eks:DescribeCluster",
        "elasticloadbalancing:DescribeLoadBalancers",
        "elasticloadbalancing:DescribeListeners",
        "elasticloadbalancing:DescribeTargetGroups",
        "apigateway:GET"
      ],
      "Resource": "*"
    },
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.claude-*"
    }
  ]
}
```

## Quick Start

**Linux/macOS:**

```bash
cd security/ai-incident-response-playbook-builder

# Generate playbooks for your current AWS account
./build-playbooks.sh

# Specify output format and model
./build-playbooks.sh --output-format both --model-id anthropic.claude-3-5-sonnet-20241022-v2:0

# Include organization context (escalation contacts, Slack channels, etc.)
./build-playbooks.sh --org-context org-context.json
```

**Windows (PowerShell):**

```powershell
cd security\ai-incident-response-playbook-builder

# Generate playbooks for your current AWS account
.\build-playbooks.ps1

# Specify output format and model
.\build-playbooks.ps1 -OutputFormat Both -ModelId "anthropic.claude-3-5-sonnet-20241022-v2:0"

# Include organization context
.\build-playbooks.ps1 -OrgContext org-context.json
```

The script automatically:
1. ✅ Validates prerequisites (AWS CLI, Python, Node.js, credentials, Bedrock access)
2. 🚀 Deploys S3 bucket via CDK
3. 🔍 Discovers your AWS architecture (read-only API calls)
4. 🤖 Generates tailored playbooks via Amazon Bedrock
5. 📤 Uploads playbooks to S3 + saves locally to `./output/`

## Parameters

| Parameter | PowerShell | Bash | Default | Description |
|---|---|---|---|---|
| Output format | `-OutputFormat` | `--output-format` | `both` | `ssm`, `markdown`, or `both` |
| Model ID | `-ModelId` | `--model-id` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Bedrock model to use |
| Region | `-Region` | `--region` | Current configured region | AWS region to scan |
| Org context | `-OrgContext` | `--org-context` | None | Path to JSON with org-specific details |
| Output dir | `-OutputDir` | `--output-dir` | `./output` | Where to write generated files |

### Organization Context File

Create an optional `org-context.json` to embed team-specific details into playbooks:

```json
{
  "escalation_contacts": {
    "security_lead": "security-oncall@example.com",
    "incident_commander": "ic-oncall@example.com"
  },
  "communication_channels": {
    "slack": "#incident-response",
    "pagerduty_service": "P1234567"
  },
  "ticketing": {
    "system": "Jira",
    "project_key": "SEC",
    "create_url": "https://jira.example.com/secure/CreateIssue.jspa"
  },
  "environment_tags": {
    "production": "env:prod",
    "staging": "env:staging"
  }
}
```

## Relationship to AWS Security Incident Response

```
[AI Playbook Builder]              [AWS Security Incident Response]
     BEFORE                                  DURING
 ┌──────────────────┐                ┌──────────────────────┐
 │ Discover arch     │                │ Triage & investigate  │
 │ Generate plans    │──playbooks───▶│ Coordinate response   │
 │ Map to ATT&CK    │                │ Contain & remediate   │
 │ Drill & refine    │                │ Post-incident review  │
 └──────────────────┘                └──────────────────────┘
```

This tool generates the **preparation** artifacts. AWS Security Incident Response handles **active investigation and response**. Teams that enter an incident with pre-built, architecture-specific playbooks resolve incidents faster.

## Cost

**Per Run**: ~$0.50–$2.00 (Bedrock API calls only)
**Infrastructure**: S3 bucket storage — negligible (<$0.01/month for typical playbook output)

Cost scales with account complexity — more resources discovered means more playbooks generated, which means more Bedrock invocations.

## Cleanup

**Linux/macOS:**
```bash
cd infrastructure/cdk
npx cdk destroy --no-cli-pager
```

**Windows (PowerShell):**
```powershell
cd infrastructure\cdk
npx cdk destroy --no-cli-pager
```

## Compliance Value

Generated playbooks support evidence requirements for:
- **SOC 2** (CC7.4, CC7.5) — Defined response activities
- **PCI-DSS** (Req 12.10) — Incident response plan
- **HIPAA** (§164.308(a)(6)) — Security incident procedures
- **FedRAMP** (IR family) — IR planning and testing

## Project Structure

```
ai-incident-response-playbook-builder/
├── build-playbooks.ps1        # PowerShell deploy-and-run script
├── build-playbooks.sh         # Bash deploy-and-run script
├── README.md                  # This file
├── ARCHITECTURE.md            # Technical architecture details
├── infrastructure/
│   └── cdk/
│       ├── app.py             # CDK app entry point
│       ├── stack.py           # CDK stack (S3 bucket)
│       ├── cdk.json           # CDK configuration
│       └── requirements.txt   # CDK Python dependencies
└── src/
    ├── requirements.txt       # Python dependencies
    ├── discovery.py           # AWS architecture discovery module
    ├── generator.py           # Bedrock playbook generation module
    └── output.py              # Output formatting (SSM + markdown)
```

### Shared Scripts

This demo uses the shared scripts for prerequisite validation:

```
shared/
└── scripts/
    ├── check-prerequisites.ps1   # Shared prereq validation (Windows)
    └── check-prerequisites.sh    # Shared prereq validation (Linux/macOS)
```

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
