# AI Incident Response Playbook Builder — Steering Document

## Demo Metadata

| Field | Value |
|---|---|
| **Demo Name** | AI Incident Response Playbook Builder |
| **Pillar** | Security |
| **Pattern** | Deploy-and-run (`build-playbooks.ps1` / `build-playbooks.sh`) |
| **Primary AWS Service** | Amazon Bedrock |
| **Supporting Services** | EC2, VPC, IAM, S3, RDS, DynamoDB, Lambda, ECS, EKS, ELB, API Gateway, Systems Manager |
| **Repository Path** | `security/ai-incident-response-playbook-builder/` |
| **Status** | In Development |
| **Estimated Effort** | 6–8 weeks, 1 engineer |

---

## Press Release

**AWS Announces AI Incident Response Playbook Builder — Generate Architecture-Aware Security Playbooks in Minutes, Not Months**

*Sydney, Australia*

Today, Amazon Web Services (AWS) announced the AI Incident Response Playbook Builder, an open-source tool that automatically analyzes a customer's AWS environment and generates tailored incident response playbooks — complete with step-by-step containment, eradication, and recovery procedures — mapped to the MITRE ATT&CK framework.

Organizations with tested incident response playbooks contain breaches faster and at significantly lower cost- yet most security teams never write them. The work is manual, requires deep knowledge of both the threat landscape and the specific environment, and gets deprioritized against the urgency of daily operations. The result: when an incident strikes, responders improvise under pressure.

For example, the SANS 2023 Incident Response Survey found that 71% of organizations without documented playbooks reported "ad hoc" response during incidents, leading to longer containment times and repeated mistakes. Organizations with pre-built, tested playbooks resolved incidents in half the time.


The AI Incident Response Playbook Builder eliminates this gap. With a single command, the tool discovers an account's VPCs, public-facing endpoints, IAM roles, data stores, and compute resources, then uses Amazon Bedrock to generate playbooks for the threat scenarios most likely to target that specific architecture. Outputs are delivered as executable AWS Systems Manager (SSM) Automation documents and human-readable markdown — ready for review, drill, and real-world use.

"Every security leader I talk to has the same problem: they know they need playbooks, they know what good looks like, but they can't find the cycles to write them. This tool turns weeks of specialized work into a five-minute automated run. It doesn't replace your team's judgment — it gives them a 90% starting point so they can focus on the last 10% that requires human expertise."

AWS Security Incident Response helps customers investigate and respond during active incidents. The AI Incident Response Playbook Builder addresses the critical preparation phase that happens *before* an incident — making the two deeply complementary. Customers who prepare with generated playbooks can respond faster and more consistently when Security Incident Response engages.

The AI Incident Response Playbook Builder is available today as an open-source, deploy-and-run solution on GitHub.

**Getting Started**: Clone the repository and run `build-playbooks.sh` (or `build-playbooks.ps1`) with your AWS credentials. The tool requires read-only access to describe resources and `bedrock:InvokeModel` permission. No persistent infrastructure is deployed.

---

## Frequently Asked Questions

### Customer FAQ

**Q: What specific problem does this solve?**

Three compounding problems:

1. **Playbook creation is expensive.** Writing a single high-quality incident response playbook takes a senior security engineer 2–4 days. Most organizations need 8–15 playbooks to cover their primary threat scenarios. That's 4–12 weeks of specialized labor.
2. **Generic templates don't work.** Off-the-shelf playbook templates tell you to "isolate the affected instance" without knowing whether your architecture uses auto-scaling groups behind an ALB, standalone EC2 instances, or containerized workloads on EKS.
3. **Playbooks rot.** Even when teams write good playbooks, architectures evolve. Re-running this tool after architecture changes keeps playbooks current.

**Q: Who is this for?**

- Security engineers who know they need playbooks but can't justify the time investment
- Cloud architects who want to validate that their architecture has response coverage
- DevSecOps teams building security automation into their pipelines
- Compliance teams that need to demonstrate IR preparedness for SOC 2, PCI-DSS, HIPAA, or FedRAMP audits
- Startups and mid-size companies without a dedicated IR planning function

**Q: How does it work?**

A three-stage pipeline:

1. **Discover** — Read-only API calls to inventory the account: VPCs, subnets, public endpoints, IAM roles and policies, data stores, and compute resources. Output is a structured architecture profile.
2. **Threat Model & Generate** — The architecture profile is sent to Amazon Bedrock. The model identifies which threat scenarios are most probable and highest-impact for this specific architecture. For each scenario, it generates a playbook with detection indicators, containment steps (with specific AWS CLI commands), eradication procedures, recovery steps, post-incident checklist, and MITRE ATT&CK technique IDs.
3. **Output** — Playbooks are written as SSM Automation documents (JSON, directly importable into Systems Manager) and markdown (for team review and tabletop exercises). A summary report maps all playbooks to ATT&CK techniques in a coverage matrix.

**Q: What threat scenarios does it cover?**

Scenarios are dynamically selected based on the discovered architecture:

| Threat Scenario | Triggered When Architecture Includes |
|---|---|
| Compromised IAM credentials | IAM users with long-lived access keys, roles with broad permissions |
| Data exfiltration from S3 | S3 buckets with sensitive data patterns, cross-account access |
| Crypto mining | EC2 instances with compute-heavy types, ECS/EKS pods |
| Ransomware (EBS/S3) | EBS without snapshots, S3 without versioning or Object Lock |
| Unauthorized public exposure | Security groups with 0.0.0.0/0 ingress, public subnets |
| Lateral movement | Multiple VPCs with peering, broad security group rules |
| Compromised Lambda function | Lambda with VPC access, high-privilege execution roles |
| Container escape | EKS/ECS with privileged containers or host networking |

The tool typically generates 6–12 playbooks per account.

**Q: How does this relate to AWS Security Incident Response?**

They form a complete lifecycle:

- **AI Playbook Builder** (BEFORE): Discover architecture → Generate plans → Map to ATT&CK → Drill & refine
- **AWS Security Incident Response** (DURING): Triage & investigate → Coordinate response → Contain & remediate → Post-incident review

The Playbook Builder generates the plans. Security Incident Response executes and coordinates when a real incident occurs.

**Q: What is the deployment model?**

Deploy-and-run with zero persistent infrastructure. Execute `build-playbooks.sh`, it runs against your current AWS credentials, and writes output files locally. Nothing is deployed to your account.

**Q: What permissions does it need?**

- **Discovery (read-only):** `ec2:Describe*`, `iam:List*`, `iam:Get*`, `s3:ListAllMyBuckets`, `s3:GetBucketPolicy`, `s3:GetBucketAcl`, `rds:Describe*`, `dynamodb:ListTables`, `dynamodb:DescribeTable`, `lambda:ListFunctions`, `lambda:GetFunction`, `ecs:List*`, `ecs:Describe*`, `eks:List*`, `eks:Describe*`, `elasticloadbalancing:Describe*`, `apigateway:GET`
- **Generation:** `bedrock:InvokeModel` for the configured foundation model

No write permissions required. An example IAM policy is in the repository.

**Q: What Bedrock models does it support?**

Defaults to Anthropic Claude 3.5 Sonnet on Amazon Bedrock. Configurable via parameter for Claude 3 Opus (higher quality, slower) or Claude 3 Haiku (faster, lower cost).

**Q: Is the output production-ready?**

The output is a high-quality starting point. Recommended workflow: Generate → Review → Customize → Test (tabletop exercises) → Adopt → Refresh periodically.

SSM Automation documents are generated with approval gates by default — human confirmation required before destructive actions.

**Q: How does this help with compliance?**

Multiple frameworks require documented, tested IR procedures:
- **SOC 2** (CC7.4, CC7.5) — Defined response activities
- **PCI-DSS** (Req 12.10) — Incident response plan tested annually
- **HIPAA** (§164.308(a)(6)) — Security incident procedures
- **FedRAMP** (IR family) — IR planning, training, and testing

**Q: Does the tool send my architecture data outside my AWS account?**

Architecture data is sent only to Amazon Bedrock within your AWS account and region. Bedrock's data privacy commitments apply: inputs/outputs are not used for model training, not shared with model providers, encrypted in transit and at rest. No data leaves AWS.

---
