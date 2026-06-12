# Architecture - AI Incident Response Playbook Builder

## Overview

This demo implements automated incident response playbook generation using Amazon Bedrock with architecture-aware threat modeling. The tool discovers an AWS account's infrastructure via read-only API calls, identifies the most likely threat scenarios for that specific architecture, and generates tailored playbooks as both SSM Automation documents and markdown. No infrastructure is deployed — it runs locally and writes output files.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Local Execution                              │
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────────┐  │
│  │  Discovery    │───▶│  Generator   │───▶│  Output Formatter     │  │
│  │  (discovery.py)│   │ (generator.py)│   │  (output.py)          │  │
│  └──────┬───────┘    └──────┬───────┘    └───────────┬───────────┘  │
│         │                   │                        │              │
│         │ Read-only         │ InvokeModel            │ Local files  │
│         │ API calls         │                        │              │
└─────────┼───────────────────┼────────────────────────┼──────────────┘
          │                   │                        │
          ▼                   ▼                        ▼
┌──────────────────┐  ┌──────────────┐    ┌───────────────────────┐
│  AWS Account     │  │  Amazon      │    │  ./output/            │
│  (EC2, VPC, IAM, │  │  Bedrock     │    │  ├── playbooks/*.md   │
│   S3, RDS, Lambda│  │  (Claude)    │    │  ├── ssm-documents/   │
│   ECS, EKS, ELB, │  │              │    │  │   └── *.json       │
│   API Gateway)   │  │              │    │  └── reports/         │
└──────────────────┘  └──────────────┘    │      ├── arch-profile │
                                          │      ├── coverage-mtx │
                                          │      └── threat-assess│
                                          └───────────────────────┘
```

## Components

### 1. Discovery Module (`src/discovery.py`)

Performs read-only API calls to build a structured architecture profile.

**Services Scanned**:

| Category | Services | Key Data Collected |
|---|---|---|
| Network | VPC, Subnets, Security Groups, NACLs | Public/private topology, ingress rules, internet gateways |
| Compute | EC2, Lambda, ECS, EKS | Instance types, public IPs, execution roles, container configs |
| Data | S3, RDS, DynamoDB | Bucket policies, public access blocks, encryption status, backups |
| Identity | IAM Roles, Users, Policies | Overprivileged roles, long-lived access keys, cross-account trust |
| Endpoints | ALB, API Gateway, CloudFront | Public-facing listeners, WAF association, TLS configuration |

**Output**: JSON architecture profile containing:
```json
{
  "account_id": "123456789012",
  "region": "us-east-1",
  "scan_timestamp": "2026-04-29T09:30:00Z",
  "network": { "vpcs": [...], "public_subnets": [...], "security_groups": [...] },
  "compute": { "ec2_instances": [...], "lambda_functions": [...], "ecs_clusters": [...] },
  "data_stores": { "s3_buckets": [...], "rds_instances": [...], "dynamodb_tables": [...] },
  "identity": { "iam_roles": [...], "iam_users": [...], "access_keys": [...] },
  "endpoints": { "load_balancers": [...], "api_gateways": [...] },
  "risk_indicators": {
    "public_endpoints_count": 5,
    "overprivileged_roles": ["AdminRole"],
    "unencrypted_buckets": ["legacy-data"],
    "long_lived_access_keys": 3
  }
}
```

### 2. Generator Module (`src/generator.py`)

Sends the architecture profile to Amazon Bedrock and orchestrates playbook generation.

**Two-Phase Generation**:

1. **Threat Assessment** — Single Bedrock call with the full architecture profile. The model identifies and prioritizes 6–12 threat scenarios based on the discovered attack surface. Each scenario includes: threat name, description, likelihood (based on architecture), MITRE ATT&CK technique IDs, and affected resources.

2. **Playbook Generation** — One Bedrock call per threat scenario. Each call receives the architecture profile + the specific threat scenario and produces a structured playbook with: detection indicators, containment steps (with AWS CLI commands), eradication procedures, recovery steps, post-incident checklist, and MITRE ATT&CK mapping.

**Prompt Structure**:
```
System: You are an AWS security incident response expert.
        Generate playbooks with specific AWS CLI commands
        referencing the actual resources discovered.
        Map all procedures to MITRE ATT&CK techniques.

User:   [Architecture Profile JSON]
        [Threat Scenario]
        [Organization Context (optional)]
        [Output Format Instructions]
```

**Model Configuration**:
- Default: `anthropic.claude-3-5-sonnet-20241022-v2:0`
- Max tokens: 4096 per playbook
- Temperature: 0.2 (low creativity, high consistency)

### 3. Output Module (`src/output.py`)

Formats generated playbooks into two output types.

**Markdown Playbooks** (`output/playbooks/*.md`):
```markdown
# Incident Response Playbook: Compromised IAM Credentials

## MITRE ATT&CK Mapping
- T1078 — Valid Accounts
- T1528 — Steal Application Access Token

## Severity: HIGH
## Affected Resources: AdminRole, DeployRole, ci-cd-user

## Detection Indicators
- CloudTrail: Unusual API calls from unfamiliar IP ranges
- GuardDuty: UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration
...

## Phase 1: Containment
### Step 1.1: Disable compromised credentials
```bash
aws iam update-access-key --access-key-id AKIA... --status Inactive --user-name ci-cd-user
```
...
```

**SSM Automation Documents** (`output/ssm-documents/*.json`):
```json
{
  "schemaVersion": "0.3",
  "description": "Automated response: Compromised IAM Credentials",
  "assumeRole": "{{ AutomationAssumeRole }}",
  "parameters": {
    "AutomationAssumeRole": { "type": "String" },
    "CompromisedPrincipal": { "type": "String" }
  },
  "mainSteps": [
    {
      "name": "DisableAccessKeys",
      "action": "aws:executeAwsApi",
      "inputs": {
        "Service": "iam",
        "Api": "UpdateAccessKey",
        "UserName": "{{ CompromisedPrincipal }}",
        "AccessKeyId": "{{ AccessKeyId }}",
        "Status": "Inactive"
      }
    }
  ]
}
```

**MITRE ATT&CK Coverage Matrix** (`output/reports/attack-coverage-matrix.md`):
```
| ATT&CK Technique | ID     | Playbook                    | Coverage |
|------------------|--------|-----------------------------|----------|
| Valid Accounts   | T1078  | credential-compromise.md    | ✅       |
| Data from S3     | T1530  | data-exfiltration-s3.md     | ✅       |
| Resource Hijack  | T1496  | cryptomining.md             | ✅       |
```

## Data Flow

### Phase 1: Discovery (~10–30 seconds)

```
build-playbooks.sh
  └─▶ python src/discovery.py --region us-east-1
        ├── ec2:DescribeVpcs, DescribeSubnets, DescribeSecurityGroups, DescribeInstances
        ├── iam:ListRoles, ListUsers, GetPolicy, GetRolePolicy
        ├── s3:ListAllMyBuckets, GetBucketPolicy, GetBucketAcl
        ├── rds:DescribeDBInstances, DescribeDBClusters
        ├── dynamodb:ListTables, DescribeTable
        ├── lambda:ListFunctions, GetFunction
        ├── ecs:ListClusters, DescribeClusters, ListServices
        ├── eks:ListClusters, DescribeCluster
        ├── elasticloadbalancing:DescribeLoadBalancers, DescribeListeners
        └── apigateway:GetRestApis, GetResources
        ──▶ architecture-profile.json
```

### Phase 2: Threat Assessment (~15–30 seconds)

```
python src/generator.py --phase assess
  └─▶ bedrock:InvokeModel (Claude)
        Input:  architecture-profile.json
        Output: threat-assessment.json
                [
                  {
                    "threat": "Compromised IAM Credentials",
                    "likelihood": "HIGH",
                    "attack_techniques": ["T1078", "T1528"],
                    "affected_resources": ["AdminRole", "ci-cd-user"],
                    "rationale": "3 long-lived access keys, 1 overprivileged role"
                  },
                  ...
                ]
```

### Phase 3: Playbook Generation (~1–5 minutes)

```
python src/generator.py --phase generate
  └─▶ For each threat scenario:
        bedrock:InvokeModel (Claude)
          Input:  architecture-profile.json + threat scenario + org context
          Output: structured playbook (markdown + SSM JSON)
```

### Phase 4: Output Assembly (~1–2 seconds)

```
python src/output.py
  └─▶ Write output/
        ├── playbooks/*.md           (human-readable playbooks)
        ├── ssm-documents/*.json     (executable SSM Automation docs)
        └── reports/
            ├── architecture-profile.md
            ├── attack-coverage-matrix.md
            └── threat-assessment.md
```

## Security Design

### IAM Permissions (Least Privilege)

- **Discovery**: Read-only `Describe*`, `List*`, `Get*` across scanned services
- **Generation**: `bedrock:InvokeModel` scoped to `anthropic.claude-*` models
- **No write permissions**: The tool never modifies your AWS account

### Data Handling

- **Architecture data** is sent to Amazon Bedrock within your AWS account and region
- **Bedrock privacy guarantees apply**: Inputs/outputs are not used for model training, not shared with model providers
- **No external network calls**: All communication is AWS API calls over HTTPS
- **Local output only**: Generated files are written to the local filesystem
- **No secrets collected**: Discovery captures resource metadata, not data contents or credentials

### Generated SSM Documents

- SSM Automation documents are generated with `approveBeforeExecution` steps by default
- Human approval is required before destructive actions (key deactivation, security group changes)
- Documents reference parameterized resource identifiers, not hardcoded ARNs

## Threat Scenario Selection Logic

Bedrock selects threat scenarios based on architecture indicators:

| Architecture Indicator | Triggers Scenario |
|---|---|
| IAM users with long-lived access keys | Compromised IAM Credentials |
| S3 buckets with permissive policies or no encryption | Data Exfiltration from S3 |
| EC2 instances with compute-heavy types + public IPs | Crypto Mining |
| EBS without snapshots, S3 without versioning | Ransomware |
| Security groups with 0.0.0.0/0 ingress | Unauthorized Public Exposure |
| Multiple VPCs with peering, broad SG rules | Lateral Movement |
| Lambda with VPC access + high-privilege roles | Compromised Lambda Function |
| EKS/ECS with privileged containers | Container Escape |

Scenarios are not hardcoded — the model evaluates the full architecture profile and may identify additional scenarios based on unique combinations of resources.

## Scalability Considerations

### Account Complexity

| Account Size | Resources | Playbooks Generated | Runtime |
|---|---|---|---|
| Small (<50 resources) | Single VPC, few services | 4–6 | ~2 min |
| Medium (50–200) | Multi-VPC, mixed services | 6–10 | ~3–5 min |
| Large (200+) | Complex multi-VPC, many services | 8–12 | ~5–8 min |

### Bedrock API Usage

- **Threat assessment**: 1 invocation (~2K input tokens, ~1K output tokens)
- **Per playbook**: 1 invocation (~3K input tokens, ~4K output tokens)
- **Typical total**: 7–13 invocations per run
- **Cost**: ~$0.50–$2.00 per run

### Rate Limiting

- Discovery uses boto3 with default retry configuration
- Bedrock calls are sequential (one playbook at a time) to stay within default throughput limits
- No parallel API calls to avoid throttling

## Extension Points

### Custom Threat Scenarios
- Add organization-specific threat scenarios via the `--org-context` parameter
- Include industry-specific compliance requirements (HIPAA, PCI-DSS)

### Output Formats
- Post-process markdown to HTML or PDF
- Convert SSM documents to Terraform or CloudFormation
- Integrate with ticketing systems (Jira, ServiceNow)

### CI/CD Integration
```bash
# Run monthly via cron or CI pipeline
./build-playbooks.sh --output-dir s3://my-bucket/playbooks/$(date +%Y-%m)
```

### Playbook Drift Detection
- Compare current run output against previous run
- Alert when architecture changes invalidate existing playbooks
- Track coverage gaps over time

## Why Deploy-and-Run?

This architecture uses a local execution model for several reasons:

- **Zero infrastructure**: Nothing deployed to the AWS account — no Lambda, no S3, no state to manage
- **Immediate value**: Run once, get playbooks. No setup, no teardown
- **Safe**: Read-only access. Cannot modify your environment
- **Portable**: Run from any machine with AWS credentials — laptop, CI runner, bastion host
- **Cost-effective**: Pay only for Bedrock API calls (~$0.50–$2.00 per run)

## Why Bedrock?

- **Data privacy**: Architecture data stays within your AWS account and region
- **No external dependencies**: No third-party API keys or accounts required
- **Model choice**: Swap models via parameter (Claude Sonnet, Opus, Haiku)
- **Enterprise ready**: IAM-based access control, CloudTrail logging, VPC endpoints available

## Relationship to AWS Security Services

| Service | Phase | What It Does | How This Tool Complements |
|---|---|---|---|
| **AWS Security Incident Response** | During | Triage, investigate, coordinate | Playbook Builder generates the plans SIR executes |
| **Amazon GuardDuty** | Detection | Identifies threats | Playbooks reference GuardDuty finding types as detection indicators |
| **AWS Security Hub** | Aggregation | Centralizes findings | Architecture profile aligns with Security Hub resource inventory |
| **AWS Systems Manager** | Execution | Runs automation documents | SSM documents generated by this tool are directly importable |
