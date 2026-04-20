# G.O.A.T. Demo Scenarios

Pre-built provisioning scripts that create controlled sets of AWS resources for demonstrating G.O.A.T.'s cross-domain correlation capabilities. Each scenario generates data across multiple agent domains (Cost Explorer, Health Dashboard, Support Cases, Trusted Advisor, CUR) so you can showcase real multi-agent orchestration with a single query.

## Prerequisites

- **AWS CLI v2** configured with valid credentials (`aws sts get-caller-identity`)
- **AWS account** with permissions to create EC2, RDS, EBS, VPC, DynamoDB, and Elastic IP resources
- **AWS Business or Enterprise Support plan** (optional) — required for Support case creation; scripts skip gracefully without one
- **G.O.A.T. deployed** — run the main deployment first so agents are available to query the demo resources

## Scenario A: Full Account Health Check

Creates resources that generate data across all five agent domains, enabling a comprehensive account health check demo.

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| `goat-demo-vpc` | VPC (10.99.0.0/16) | Dedicated network for demo resources |
| `goat-demo-subnet-1`, `goat-demo-subnet-2` | Subnets in 2 AZs | Required for RDS subnet group |
| `goat-demo-db-subnet-group` | DB Subnet Group | Spans both subnets for RDS |
| `goat-demo-instance-1` | EC2 t3.micro | Generates Cost Explorer + CUR data |
| `goat-demo-instance-2` | EC2 t3.micro | Generates Cost Explorer + CUR data |
| `goat-demo-db` | RDS db.t3.micro (MySQL) | Generates Cost Explorer + CUR data |
| `goat-demo-ebs-unused` | EBS gp2 10GB (unattached) | Triggers Trusted Advisor "Underutilized EBS Volumes" finding |
| `goat-demo-eip-unused` | Elastic IP (unassociated) | Triggers Trusted Advisor "Unassociated Elastic IP" finding |
| Support case | Resolved case | Generates Support Cases domain data |

### Expected Agent Correlations

When you ask G.O.A.T. for a health check, the orchestration agent should invoke multiple sub-agents and correlate findings:

- **Cost Explorer Agent** — Reports costs for the EC2 instances, RDS instance, and EBS volume
- **CUR Agent** — Shows line-item cost breakdowns for all billable resources
- **Trusted Advisor Agent** — Flags the unattached EBS volume and unassociated Elastic IP as optimization opportunities
- **Support Agent** — Returns the resolved demo Support case
- **Health Agent** — Reports any active health events in the region

### Setup Instructions

**macOS / Linux:**
```bash
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x setup-scenario-a.sh
./setup-scenario-a.sh
```

**Windows (PowerShell):**
```powershell
cd operations-automation\ai-operations-assistant\demo-scenarios
.\setup-scenario-a.ps1
```

The script is idempotent — safe to re-run after partial failures. Existing resources are detected and skipped.

### Suggested Demo Queries

After setup completes, open the G.O.A.T. chat interface and try these queries:

| Query | What It Demonstrates |
|-------|---------------------|
| **"Give me a complete health check of my AWS account"** | Full cross-domain correlation across all 5 agents |
| "What are my top cost drivers this month?" | Cost Explorer + CUR agent correlation |
| "Are there any Trusted Advisor recommendations I should address?" | Trusted Advisor findings for unused EBS and unassociated EIP |
| "Show me my recent support cases" | Support agent returning the resolved demo case |
| "Do I have any idle or underutilized resources?" | Trusted Advisor + Cost correlation |

### Cost Note

Scenario A creates billable AWS resources. Approximate costs while resources are running:

| Resource | Approximate Cost |
|----------|-----------------|
| 2x EC2 t3.micro | ~$0.02/hr (~$15/month) |
| 1x RDS db.t3.micro | ~$0.017/hr (~$12/month) |
| 1x EBS 10GB gp2 | ~$1/month |
| 1x Elastic IP (unassociated) | ~$0.005/hr (~$3.60/month) |

**Run the cleanup script promptly after your demo to avoid ongoing charges.**

## Scenario B: CloudWatch Apr 1 Incident Correlation

Creates resources that correlate with the real CloudWatch health event from April 1, 2026, enabling a cross-domain incident investigation demo.

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| Support case | Resolved case referencing CloudWatch monitoring gaps on Apr 1 | Correlates with the Health event |

No AWS resources are created — only a Support case. Zero cost.

### Expected Agent Correlations

When you ask G.O.A.T. about the April 1st incident, the orchestration agent should correlate:

- **Health Agent** — Returns the real CloudWatch planned lifecycle event from April 1, 2026
- **Support Agent** — Returns the demo Support case describing monitoring gaps and missing alarms
- **Cost Explorer Agent** — Shows CloudWatch costs around the incident timeframe

### Important: Health Event Dependency

Scenario B correlates with the **real CloudWatch planned lifecycle event from April 1, 2026** visible in the AWS Health Dashboard. The Health agent queries both account-specific events and public service events to find this correlation. If the event has aged out of the Health API retention window, the Health agent correlation will not be available, though the Support case will still be created.

### Setup Instructions

**macOS / Linux:**
```bash
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x setup-scenario-b.sh
./setup-scenario-b.sh
```

**Windows (PowerShell):**
```powershell
cd operations-automation\ai-operations-assistant\demo-scenarios
.\setup-scenario-b.ps1
```

### Suggested Demo Queries

| Query | What It Demonstrates |
|-------|---------------------|
| **"We had monitoring gaps on April 1st — was there an AWS issue?"** | Cross-domain incident correlation (Health + Support + Cost) |
| "I had a CloudWatch problem in April. Was it linked to a health event or a support case?" | Cross-domain correlation between Health events and Support cases |
| "Were there any CloudWatch health events recently?" | Health agent returning the real Apr 1 event |
| "Show me support cases related to CloudWatch" | Support agent returning the monitoring gaps case |
| "What happened with our monitoring on April 1st?" | Multi-agent investigation of a specific incident |

### Cost Note

Scenario B creates no billable AWS resources — only a resolved Support case. Zero cost.

## Cleanup

A single cleanup script removes all demo resources from both scenarios. It finds resources by the `goat-demo=true` tag and deletes them in dependency order.

**macOS / Linux:**
```bash
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x cleanup-scenarios.sh
./cleanup-scenarios.sh
```

**Windows (PowerShell):**
```powershell
cd operations-automation\ai-operations-assistant\demo-scenarios
.\cleanup-scenarios.ps1
```

### Cleanup Order

Resources are deleted in this order to handle dependencies:

1. EC2 instances (terminate)
2. RDS instances (delete, skip final snapshot)
3. DB subnet groups
4. EBS volumes
5. Elastic IPs (release)
6. DynamoDB tables
7. Subnets
8. VPCs

Support cases are not cleaned up — they are already resolved and cannot be deleted via API.

### Handling Partial Cleanup

If cleanup encounters errors (e.g., an RDS instance is still deleting), re-run the script. It handles already-deleted resources gracefully and continues processing remaining resources.

## Support Plan Behavior

Both provisioning scripts detect whether your account has an active AWS Business or Enterprise Support plan:

- **With Support plan**: Creates a Support case, adds a demo-purpose communication, and immediately resolves it
- **Without Support plan**: Prints a yellow warning and skips Support case creation entirely — all other resources are still created

The demo works without a Support plan, but the Support Cases agent domain will not have demo data to correlate.

## Tagging Convention

Every demo resource receives four tags for identification and cleanup:

| Tag Key | Value | Purpose |
|---------|-------|---------|
| `goat-demo` | `true` | Identifies all demo resources for cleanup |
| `goat-scenario` | `a` or `b` | Identifies which scenario created the resource |
| `Name` | `goat-demo-<descriptive>` | Human-readable name in AWS Console |
| `auto-delete` | `no` | Prevents automated cleanup policies from removing resources prematurely |

## Script Reference

| Script | Platform | Purpose |
|--------|----------|---------|
| `setup-scenario-a.sh` | Bash | Provision Scenario A resources |
| `setup-scenario-a.ps1` | PowerShell | Provision Scenario A resources |
| `setup-scenario-b.sh` | Bash | Provision Scenario B resources |
| `setup-scenario-b.ps1` | PowerShell | Provision Scenario B resources |
| `cleanup-scenarios.sh` | Bash | Remove all demo resources |
| `cleanup-scenarios.ps1` | PowerShell | Remove all demo resources |

PowerShell and Bash versions of each script produce identical AWS resources. Choose whichever matches your operating system.

## Troubleshooting

### "AWS credentials not configured"
Run `aws configure` or set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` environment variables. Verify with `aws sts get-caller-identity`.

### "Failed to create VPC" or subnet errors
Check your account's VPC limit in the region. The default limit is 5 VPCs per region. Delete unused VPCs or request a limit increase.

### RDS instance takes a long time
RDS instance creation takes 5-10 minutes. The script does not wait — it reports the identifier and continues. The instance will be available by the time you start your demo.

### Support case creation fails
Ensure your account has an active AWS Business or Enterprise Support plan. Without one, the script skips case creation with a warning — this is expected behavior.

### Cleanup fails for RDS
RDS deletion can take several minutes. If the cleanup script reports an error, wait a few minutes and re-run it. The script handles already-deleted resources gracefully.

### Trusted Advisor findings don't appear immediately
Trusted Advisor checks run on a schedule. After creating Scenario A resources, it may take up to 24 hours for the "Underutilized EBS Volumes" and "Unassociated Elastic IP" findings to appear. You can manually refresh checks in the Trusted Advisor console.

### Cost Explorer data not showing
Cost Explorer data has a 24-48 hour delay. If you just created resources, cost data will appear the next day. For immediate demos, focus on Trusted Advisor and Support Cases domains.
