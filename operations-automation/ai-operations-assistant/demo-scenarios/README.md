# G.O.A.T. Demo Scenarios

Pre-built provisioning scripts that create controlled sets of AWS resources for demonstrating G.O.A.T.'s cross-domain correlation capabilities. Each scenario generates data across multiple agent domains (Cost Explorer, Health Dashboard, Support Cases, Trusted Advisor, CUR) so you can showcase real multi-agent orchestration with a single query.

## Prerequisites

- **AWS CLI v2** configured with valid credentials (`aws sts get-caller-identity`)
- **AWS account** with permissions to create EC2, RDS, EBS, VPC, DynamoDB, Elastic IP, Transit Gateway, and Network Firewall resources
- **AWS Business or Enterprise Support plan** (optional) â€” required for Support case creation; scripts skip gracefully without one
- **G.O.A.T. deployed** â€” run the main deployment first so agents are available to query the demo resources

## CDK Deployment (Recommended)

A unified CDK-based deployment script provisions all scenario resources via CloudFormation stacks and creates Support cases imperatively. This is the recommended approach â€” it's idempotent, handles dependencies, and integrates with the existing GOAT VPC for traffic mirroring.

**Deploy a single scenario:**
```powershell
cd operations-automation\ai-operations-assistant\demo-scenarios
.\deploy-demo-scenarios.ps1 -Scenario connectivity
```

**Deploy all scenarios:**
```powershell
.\deploy-demo-scenarios.ps1 -Scenario all
```

**Available scenarios:**
| Parameter | What it deploys |
|-----------|----------------|
| `all` | Scenario A (account health) + B (CloudWatch incident) + C (TLS fragmentation) + Gâ€“L (network troubleshooting diagnostics) |
| `account-health` | Scenario A CDK stack + Support case |
| `cloudwatch-incident` | Support case only (no infrastructure) |
| `connectivity` | Scenario C CDK stack (Transit Gateway, Network Firewall, EC2) + Support case |
| `network-troubleshooting` | Scenario Gâ€“L CDK stack (network diagnostic misconfigurations) + two-phase evaluation guidance |

**Bash equivalent:**
```bash
./deploy-demo-scenarios.sh --scenario connectivity
```

The CDK scripts check for existing Support cases with the same subject before creating new ones, avoiding duplicates on re-run.

> **Note:** The original CLI scripts (`setup-scenario-*.ps1/.sh`) remain available as an alternative. Both approaches produce identically tagged resources discoverable by the cleanup script.

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

- **Cost Explorer Agent** â€” Reports costs for the EC2 instances, RDS instance, and EBS volume
- **CUR Agent** â€” Shows line-item cost breakdowns for all billable resources
- **Trusted Advisor Agent** â€” Flags the unattached EBS volume and unassociated Elastic IP as optimization opportunities
- **Support Agent** â€” Returns the resolved demo Support case
- **Health Agent** â€” Reports any active health events in the region

### Setup Instructions

**macOS / Linux:**
```bash
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x setup-scenario-account-health.sh
./setup-scenario-account-health.sh
```

**Windows (PowerShell):**
```powershell
cd operations-automation\ai-operations-assistant\demo-scenarios
.\setup-scenario-account-health.ps1
```

The script is idempotent â€” safe to re-run after partial failures. Existing resources are detected and skipped.

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

No AWS resources are created â€” only a Support case. Zero cost.

### Expected Agent Correlations

When you ask G.O.A.T. about the April 1st incident, the orchestration agent should correlate:

- **Health Agent** â€” Returns the real CloudWatch planned lifecycle event from April 1, 2026
- **Support Agent** â€” Returns the demo Support case describing monitoring gaps and missing alarms
- **Cost Explorer Agent** â€” Shows CloudWatch costs around the incident timeframe

### Important: Health Event Dependency

Scenario B correlates with the **real CloudWatch planned lifecycle event from April 1, 2026** visible in the AWS Health Dashboard. The Health agent queries both account-specific events and public service events to find this correlation. If the event has aged out of the Health API retention window, the Health agent correlation will not be available, though the Support case will still be created.

### Setup Instructions

**macOS / Linux:**
```bash
cd operations-automation/ai-operations-assistant/demo-scenarios
chmod +x setup-scenario-cloudwatch-incident.sh
./setup-scenario-cloudwatch-incident.sh
```

**Windows (PowerShell):**
```powershell
cd operations-automation\ai-operations-assistant\demo-scenarios
.\setup-scenario-cloudwatch-incident.ps1
```

### Suggested Demo Queries

| Query | What It Demonstrates |
|-------|---------------------|
| **"We had monitoring gaps on April 1st â€” was there an AWS issue?"** | Cross-domain incident correlation (Health + Support + Cost) |
| "I had a CloudWatch problem in April. Was it linked to a health event or a support case?" | Cross-domain correlation between Health events and Support cases |
| "Were there any CloudWatch health events recently?" | Health agent returning the real Apr 1 event |
| "Show me support cases related to CloudWatch" | Support agent returning the monitoring gaps case |
| "What happened with our monitoring on April 1st?" | Multi-agent investigation of a specific incident |

### Cost Note

Scenario B creates no billable AWS resources â€” only a resolved Support case. Zero cost.

## Scenario C: TLS Fragmentation Reproduction Scenario

Reproduces the AWS Network Firewall + Amazon Linux 2023 OpenSSL ML-KEM TLS Client Hello fragmentation issue. The scenario provisions an EC2 instance (AL2023, t3.micro) behind an AWS Network Firewall with the legacy `drop established` configuration, routed via a Transit Gateway. The instance's stock curl with ML-KEM (X25519MLKEM768) generates 1522-byte TLS Client Hello messages that fragment across multiple TCP segments â€” enabling a cross-domain correlation demo across the Network and Support agents.

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| `goat-demo-tls-private` | Subnet (10.99.13.0/24) | Private subnet for the test EC2 instance (in shared GOAT VPC) |
| `goat-demo-tls-spoke-tgw` | Subnet (10.99.20.0/24) | TGW attachment subnet in the spoke VPC |
| Inspection VPC | VPC (10.98.0.0/16) | Dedicated VPC hosting the Network Firewall |
| Transit Gateway | Transit Gateway | Routes traffic from spoke VPC through inspection VPC |
| AWS Network Firewall | Network Firewall | STRICT mode rules that drop fragmented Client Hello |
| NAT Gateway | NAT Gateway | Internet egress from the inspection VPC |
| EC2 instance | t3.micro (AL2023) | Runs curl loop to ECR with ML-KEM key exchange |
| Support case | Resolved case | Describes the TLS fragmentation connectivity issue |

All resources are tagged with `goat-demo=true` and `goat-scenario=connectivity`.

### Setup Instructions

**Windows (PowerShell):**
```powershell
cd operations-automation\ai-operations-assistant\demo-scenarios
.\deploy-demo-scenarios.ps1 -Scenario connectivity
```

**macOS / Linux:**
```bash
cd operations-automation/ai-operations-assistant/demo-scenarios
./deploy-demo-scenarios.sh --scenario connectivity
```

The CDK deployment takes ~5-7 minutes (Network Firewall provisioning is the main wait). Once complete, it prints a summary with the EC2 instance ID, ENI, and suggested queries.

> **Note:** The EC2 instance's UserData starts a background curl loop that hits `ecr.<region>.amazonaws.com` with ML-KEM key exchange every **30 seconds** (~2 requests/minute). This means traffic is automatically flowing â€” you don't need to generate it manually. A 2-minute capture will contain ~4 TLS Client Hello frames with the fragmentation signature, which is enough for the agent to detect the pattern.

### Reproducing the TLS Fragmentation

The setup script launches an EC2 instance that runs a curl loop in its UserData, automatically generating the fragmented traffic. The setup summary prints the instance ID and ENI â€” use the ENI for packet capture in the G.O.A.T. app.

**Getting the ENI** (if you missed the summary output):

**PowerShell:**
```powershell
# Find the scenario EC2 instance
$instanceId = aws ec2 describe-instances --filters "Name=tag:goat-scenario,Values=connectivity" "Name=instance-state-name,Values=running" --query "Reservations[].Instances[].InstanceId" --output text --no-cli-pager

# Get its primary ENI
$eniId = aws ec2 describe-instances --instance-ids $instanceId --query "Reservations[].Instances[].NetworkInterfaces[0].NetworkInterfaceId" --output text --no-cli-pager

Write-Host "Instance: $instanceId"
Write-Host "ENI: $eniId"
```

**Bash (macOS / Linux):**
```bash
# Find the scenario EC2 instance
instance_id=$(aws ec2 describe-instances \
  --filters "Name=tag:goat-scenario,Values=connectivity" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" --output text --no-cli-pager)

# Get its primary ENI
eni_id=$(aws ec2 describe-instances --instance-ids "$instance_id" \
  --query "Reservations[].Instances[].NetworkInterfaces[0].NetworkInterfaceId" --output text --no-cli-pager)

echo "Instance: $instance_id"
echo "ENI: $eni_id"
```

**Generating additional TLS traffic manually** (via SSM â€” no SSH needed):

**PowerShell:**
```powershell
# Single curl to ECR with ML-KEM key exchange (produces 1522-byte Client Hello)
aws ssm send-command --instance-ids $instanceId --document-name "AWS-RunShellScript" --parameters 'commands=["curl --curves X25519MLKEM768:X25519 -v https://ecr.us-east-1.amazonaws.com/ 2>&1 | head -20"]' --output json --no-cli-pager

# Burst of 10 curls to generate more pcap data for the capture
aws ssm send-command --instance-ids $instanceId --document-name "AWS-RunShellScript" --parameters 'commands=["for i in $(seq 1 10); do curl --curves X25519MLKEM768:X25519 -s -o /dev/null https://ecr.us-east-1.amazonaws.com/ ; done; echo Done"]' --output json --no-cli-pager
```

**Bash (macOS / Linux):**
```bash
# Single curl to ECR with ML-KEM key exchange (produces 1522-byte Client Hello)
aws ssm send-command --instance-ids "$instance_id" --document-name "AWS-RunShellScript" \
  --parameters 'commands=["curl --curves X25519MLKEM768:X25519 -v https://ecr.us-east-1.amazonaws.com/ 2>&1 | head -20"]' \
  --output json --no-cli-pager

# Burst of 10 curls to generate more pcap data for the capture
aws ssm send-command --instance-ids "$instance_id" --document-name "AWS-RunShellScript" \
  --parameters 'commands=["for i in $(seq 1 10); do curl --curves X25519MLKEM768:X25519 -s -o /dev/null https://ecr.us-east-1.amazonaws.com/ ; done; echo Done"]' \
  --output json --no-cli-pager
```

You'll see `} [1522 bytes data]` in the TLS Client Hello output â€” this 1522-byte handshake is what fragments across TCP segments.

**Using the ENI in the G.O.A.T. app** â€” after starting a capture, the traffic mirror will collect all packets from this ENI. Then transform and query:

1. In the G.O.A.T. app: `Capture traffic from eni-xxxx` (replace with your ENI)
2. Wait 1-2 minutes for traffic to accumulate, then: `stop my capture`
3. Transform the raw data: `transform my capture`
4. Analyze: `show TLS Client Hello sizes`

### Why the Network Firewall Drops the Connection

Amazon Linux 2023's OpenSSL enables post-quantum key exchange (ML-KEM / X25519MLKEM768) by default. This produces a ~1522-byte TLS Client Hello that exceeds the 1460-byte TCP MSS.

The Network Firewall is configured with **`aws:drop_established`** stateful default action and `STRICT_ORDER` rule evaluation â€” the legacy configuration widely deployed for TLS domain filtering. The pass rule (`pass tls ... content:".amazonaws.com"; endswith;`) relies on SNI extraction from the Client Hello.

When the Client Hello fragments across two TCP segments, the firewall cannot extract the SNI from the first segment alone (the SNI field spans the boundary). The pass rule never matches, and the default `drop_established` action drops the connection.

**Resolution**: Switch the firewall policy's stateful default action from `aws:drop_established` to **`aws:drop_established_app_layer`**, which reassembles multi-packet TLS Client Hello messages before rule evaluation.

### Suggested Demo Queries

After setup completes, open the G.O.A.T. app and try:

| Query | What It Demonstrates |
|-------|---------------------|
| **"Capture traffic from eni-xxx and analyze the TLS handshake"** | Full Network Agent capture â†’ transform â†’ TLS analysis |
| **"Our instance in goat-demo-vpc is failing to establish HTTPS connections to ECR (endpoint: ecr.us-east-1.amazonaws.com on port 443). The connection is routed through the TGW and the Network Firewall in the inspection VPC but it is dropped."** | Cross-domain troubleshooting with full context (Network + Support) |
| "Why is the EC2 instance failing to connect to ECR?" | Cross-domain correlation (Network + Support) |
| "Show TLS Client Hello sizes" | Network Agent pcap query revealing 1527-byte fragmented record |
| "Diagnose the TCP exchange to ECR" | TCP stream health report |
| "Investigate support case case-xxx and capture traffic if relevant" | Support case-driven capture workflow |

Use the ENI and support case IDs from the setup script's summary output.

### Applying the Fix â€” Switch NFW to `aws:drop_established_app_layer`

If the SYN black-hole persists or you want to demonstrate the fix working, switch the Network Firewall stateful default action from `aws:drop_established` to `aws:drop_established_app_layer`:

```bash
# Get the current firewall policy ARN
POLICY_ARN=$(aws network-firewall describe-firewall-policy \
  --firewall-policy-name goat-demo-tls-policy \
  --query "FirewallPolicyResponse.FirewallPolicyArn" --output text)

UPDATE_TOKEN=$(aws network-firewall describe-firewall-policy \
  --firewall-policy-name goat-demo-tls-policy \
  --query "UpdateToken" --output text)

# Update the stateful default action to drop_established_app_layer
aws network-firewall update-firewall-policy \
  --firewall-policy-arn "$POLICY_ARN" \
  --update-token "$UPDATE_TOKEN" \
  --firewall-policy '{
    "StatelessDefaultActions": ["aws:forward_to_sfe"],
    "StatelessFragmentDefaultActions": ["aws:forward_to_sfe"],
    "StatefulDefaultActions": ["aws:drop_established_app_layer"],
    "StatefulEngineOptions": {"RuleOrder": "STRICT_ORDER"},
    "StatefulRuleGroupReferences": [{"ResourceArn": "'"$(aws network-firewall describe-rule-group --rule-group-name goat-demo-tls-rules --type STATEFUL --query "RuleGroupResponse.RuleGroupArn" --output text)"'", "Priority": 1}]
  }'
```

```powershell
# PowerShell equivalent
$policyArn = aws network-firewall describe-firewall-policy `
  --firewall-policy-name goat-demo-tls-policy `
  --query "FirewallPolicyResponse.FirewallPolicyArn" --output text

$updateToken = aws network-firewall describe-firewall-policy `
  --firewall-policy-name goat-demo-tls-policy `
  --query "UpdateToken" --output text

$ruleGroupArn = aws network-firewall describe-rule-group `
  --rule-group-name goat-demo-tls-rules --type STATEFUL `
  --query "RuleGroupResponse.RuleGroupArn" --output text

$policy = @{
  StatelessDefaultActions = @("aws:forward_to_sfe")
  StatelessFragmentDefaultActions = @("aws:forward_to_sfe")
  StatefulDefaultActions = @("aws:drop_established_app_layer")
  StatefulEngineOptions = @{RuleOrder = "STRICT_ORDER"}
  StatefulRuleGroupReferences = @(@{ResourceArn = $ruleGroupArn; Priority = 1})
} | ConvertTo-Json -Depth 4 -Compress

aws network-firewall update-firewall-policy `
  --firewall-policy-arn $policyArn `
  --update-token $updateToken `
  --firewall-policy $policy
```

After applying the fix, the TLS instance should successfully connect to ECR. Run another capture to confirm the handshake completes.

### Cost Note

| Resource | Approximate Cost |
|----------|-----------------|
| EC2 t3.micro | ~$0.01/hr (~$7/month) |
| AWS Network Firewall | ~$0.395/hr (~$285/month) |
| NAT Gateway | ~$0.045/hr (~$32/month) |
| Transit Gateway | ~$0.05/hr (~$36/month) |

**Run the cleanup script promptly after your demo to avoid ongoing charges â€” the Network Firewall alone costs ~$285/month.**

## Scenario G: Inter-Tier Connectivity Failure (`agentic_reachability_analyze`)

Provisions two EC2 instances in separate subnets within the shared GOAT VPC, with a NACL deny rule buried at a non-obvious rule number among broader allow rules. A manual security-group review shows the target port as allowed â€” the deny is only visible at the NACL layer, at a rule number that precedes the explicit allows.

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| `subnet-a` | Subnet (shared GOAT VPC) | Source-side subnet for reachability test |
| `subnet-b` | Subnet (shared GOAT VPC) | Target-side subnet hosting the inter-tier instance |
| `app-tier-01` | EC2 t3.micro | Inter-tier target instance |
| NACL | Network ACL on `subnet-b` | Contains a deny rule at rule number 50 for a specific port, buried among allow rules at 100, 110, 900 |

All resources are tagged with `goat-demo=true` and `goat-scenario=network-troubleshooting-g`.

### Expected Agent Correlations

When the DevOps Agent is asked to diagnose connectivity between the source and target instances, it should use `agentic_reachability_analyze` to identify the NACL deny rule as the blocking component â€” something that `DescribeSecurityGroups` and `DescribeNetworkAcls` alone won't reveal without careful rule-number ordering analysis.

### Setup Instructions

```powershell
cd operations-automation\ai-operations-assistant\demo-scenarios
.\deploy-demo-scenarios.ps1 -Scenario network-troubleshooting
```

```bash
cd operations-automation/ai-operations-assistant/demo-scenarios
./deploy-demo-scenarios.sh --scenario network-troubleshooting
```

### Suggested Demo Queries

| Query | What It Demonstrates |
|-------|---------------------|
| **"Why can't app-tier-01 receive traffic on port 3306 from the source instance in subnet-a?"** | Reachability Analyzer identifies the buried NACL deny |
| "Analyze reachability from i-xxx to i-yyy on port 3306" | Direct `agentic_reachability_analyze` invocation |
| "The security group allows port 3306 but connections time out â€” what's blocking it?" | Cross-layer correlation (SG allows, NACL denies) |

### Two-Phase Evaluation

| Phase | Method | Expected Outcome |
|-------|--------|-----------------|
| **(a) Baseline** | Agent uses only `DescribeSecurityGroups`, `DescribeNetworkAcls`, `DescribeRouteTables` | Agent sees the SG allows port 3306 and may list NACL rules, but struggle to identify the buried deny at rule 50 takes precedence over allow at rule 100 |
| **(b) Tools-Assisted** | Agent invokes `agentic_reachability_analyze` with source and destination instance IDs | Reachability Analyzer returns the exact blocking component (NACL rule 50 deny) with the full path traversal, pinpointing root cause in seconds |

### Cost Note

| Resource | Approximate Cost |
|----------|-----------------|
| 1x EC2 t3.micro (`app-tier-01`) | ~$0.01/hr (~$7/month) |
| Subnets, NACLs | No additional cost |

---

## Scenario H: External Endpoint Unreachable (`tcp_traceroute`)

Provisions an EC2 instance in a private subnet with a NAT gateway present, but a route table containing a more-specific `/32` blackhole route for the target IP that overlaps the default `0.0.0.0/0` NAT route. The Transit Gateway from Scenario C is reused for path traversal.

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| `subnet-c` | Subnet (shared GOAT VPC) | Private subnet for the traceroute source |
| `svc-alpha` | EC2 t3.micro | Source instance (tagged `goat-network-traceroute-allowed=true`) â€” shared with Scenario K |
| Route table | Custom route table on `subnet-c` | Contains a `/32` blackhole route to demo target IP, overlapping the `0.0.0.0/0` NAT route |
| Transit Gateway attachment | TGW attachment (reused from Scenario C) | Imported via `GOATDemoScenarioCTransitGatewayId` export |

All resources are tagged with `goat-demo=true` and `goat-scenario=network-troubleshooting-h`.

### Expected Agent Correlations

When asked why the instance cannot reach the external endpoint, the agent should use `tcp_traceroute` from `svc-alpha` to the target IP. The traceroute will show packets being blackholed after the first hop, despite the NAT gateway being correctly configured. The corroborating `agentic_reachability_analyze` call will identify the `/32` blackhole route as the blocking component.

### Setup Instructions

Same as Scenario G â€” all Scenario Gâ€“L resources are deployed together:

```powershell
.\deploy-demo-scenarios.ps1 -Scenario network-troubleshooting
```

### Suggested Demo Queries

| Query | What It Demonstrates |
|-------|---------------------|
| **"Run a traceroute from svc-alpha to 203.0.113.50 on port 443"** | `tcp_traceroute` reveals packets drop after first hop |
| "Why can't instance i-xxx reach 203.0.113.50?" | Agent correlates traceroute failure with route table analysis |
| "Analyze reachability from i-xxx to 203.0.113.50 port 443" | `agentic_reachability_analyze` identifies the `/32` blackhole route |

### Two-Phase Evaluation

| Phase | Method | Expected Outcome |
|-------|--------|-----------------|
| **(a) Baseline** | Agent uses only `DescribeRouteTables`, `DescribeNatGateways`, `DescribeTransitGatewayRouteTables` | Agent sees the NAT gateway and `0.0.0.0/0` route, concludes egress should work. The `/32` blackhole is listed but its precedence over the default route requires careful longest-prefix-match reasoning |
| **(b) Tools-Assisted** | Agent invokes `tcp_traceroute` from `svc-alpha` to the target IP | Traceroute shows `* * *` (no response) after the first hop, proving packets never reach the NAT gateway. Combined with `agentic_reachability_analyze`, the blackhole route is identified definitively |

### Cost Note

| Resource | Approximate Cost |
|----------|-----------------|
| 1x EC2 t3.micro (`svc-alpha`) | ~$0.01/hr (~$7/month) |
| Transit Gateway (reused from Scenario C) | No additional cost |
| Subnets, route tables | No additional cost |

---

## Scenario I: TLS Handshake Failure (`tls_traceroute`)

Provisions an internal Application Load Balancer with a valid ACM certificate that covers a different domain than the SNI the demo client sends. The Network Firewall inspection VPC from Scenario C is reused. `DescribeLoadBalancers` and `DescribeListenerCertificates` show a healthy ALB with a valid cert â€” the domain mismatch is only observable by performing the TLS handshake.

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| `subnet-d` | Subnet (shared GOAT VPC) | Subnet hosting the ALB |
| `svc-beta-alb` | Internal ALB | HTTPS listener with mismatched ACM cert |
| Target group | ALB target group | Placeholder target for the ALB listener |
| ACM certificate | Certificate for `*.internal.example.com` | Mismatches the demo SNI of `api.prod.example.com` |
| Network Firewall (reused) | Imported from Scenario C | Inspection path via `GOATDemoScenarioCInspectionVpcId` export |

All resources are tagged with `goat-demo=true` and `goat-scenario=network-troubleshooting-i`.

### Expected Agent Correlations

When asked about the TLS failure, the agent uses `tls_traceroute` from a source instance to the ALB endpoint with `sni_override=api.prod.example.com`. The tool reports a TLS handshake failure with a certificate domain mismatch â€” information that no Describe* API can provide.

### Setup Instructions

Same as Scenario G â€” deployed with the `network-troubleshooting` scenario parameter.

### Suggested Demo Queries

| Query | What It Demonstrates |
|-------|---------------------|
| **"Run a TLS traceroute from i-xxx to svc-beta-alb.internal on port 443 with SNI api.prod.example.com"** | `tls_traceroute` reveals cert domain mismatch |
| "Why is the TLS handshake failing to our internal ALB?" | Agent identifies certificate â†” SNI mismatch |
| "Check the TLS certificate presented by svc-beta-alb" | Direct TLS inspection |

### Two-Phase Evaluation

| Phase | Method | Expected Outcome |
|-------|--------|-----------------|
| **(a) Baseline** | Agent uses only `DescribeLoadBalancers`, `DescribeListeners`, `DescribeListenerCertificates`, `DescribeCertificate` | Agent sees a valid ACM cert attached to the ALB listener â€” reports healthy config. Cannot determine that the cert's domain doesn't match what clients send as SNI |
| **(b) Tools-Assisted** | Agent invokes `tls_traceroute` with `sni_override=api.prod.example.com` | Tool performs the actual TLS handshake and reports the server presented `*.internal.example.com` while the client requested `api.prod.example.com` â€” pinpointing the mismatch |

### Cost Note

| Resource | Approximate Cost |
|----------|-----------------|
| Internal ALB | ~$0.0225/hr (~$16/month) |
| ACM certificate | Free (AWS-managed) |
| Network Firewall (reused) | No additional cost |

---

## Scenario J: DNS Split-Horizon Failure (`dns_resolve`)

Provisions a Route 53 Resolver outbound endpoint and a resolver rule that forwards a demo domain to a conditional forwarder IP that returns a stale/incorrect answer different from what the VPC resolver provides directly.

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| `subnet-e` | Subnet (shared GOAT VPC) | Subnet for the Resolver outbound endpoint |
| Route 53 Resolver outbound endpoint | Resolver endpoint | Forwards queries for the demo domain |
| Resolver rule | Forwarding rule | Forwards `internal.corp.example.com` to a stale conditional forwarder IP |

All resources are tagged with `goat-demo=true` and `goat-scenario=network-troubleshooting-j`.

### Expected Agent Correlations

When asked about DNS resolution inconsistencies, the agent uses `dns_resolve` from an instance in the VPC to compare what the VPC resolver returns versus what the conditional forwarder returns. The tool reveals the split-horizon discrepancy â€” the forwarding rule targets a stale IP that returns an outdated A record.

### Setup Instructions

Same as Scenario G â€” deployed with the `network-troubleshooting` scenario parameter.

### Suggested Demo Queries

| Query | What It Demonstrates |
|-------|---------------------|
| **"Resolve internal.corp.example.com from instance i-xxx and compare with the expected IP"** | `dns_resolve` shows stale forwarder response |
| "Why is our app connecting to the wrong backend for internal.corp.example.com?" | Agent identifies DNS split-horizon discrepancy |
| "Check DNS resolution for internal.corp.example.com â€” is it returning the correct IP?" | Direct DNS comparison |

### Two-Phase Evaluation

| Phase | Method | Expected Outcome |
|-------|--------|-----------------|
| **(a) Baseline** | Agent uses only `DescribeResolverRules`, `DescribeResolverEndpoints`, Route 53 resource record lookups | Agent sees the forwarding rule exists and the endpoint is healthy. Cannot determine that the target forwarder returns a stale IP without actually querying it |
| **(b) Tools-Assisted** | Agent invokes `dns_resolve` with `hostname=internal.corp.example.com` from the instance | Tool performs the actual DNS query from inside the VPC and returns the resolved IP, which differs from the expected value â€” proving the conditional forwarder is returning stale data |

### Cost Note

| Resource | Approximate Cost |
|----------|-----------------|
| Route 53 Resolver endpoint (2 ENIs) | ~$0.25/hr (~$180/month) |
| Resolver rule | No additional cost |

**The Resolver endpoint is the most expensive resource in this scenario. Clean up promptly after the demo.**

---

## Scenario K: Connection Pool Exhaustion (`db_connectivity_probe`)

Provisions an RDS MySQL instance configured with a restrictive `max_connections=5` parameter group, paired with a Lambda function that maintains 6 persistent connections to saturate the connection pool. The `svc-alpha` EC2 instance from Scenario H is reused as the app-tier client. Network connectivity is fully open — the failure occurs at the application layer when new connection attempts are rejected with MySQL error 1040 ("Too many connections").

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| `svc-data-01` | RDS db.t4g.micro (MySQL 8.0) | Database instance with restrictive parameter group |
| Custom Parameter Group | RDS DB Parameter Group (`max_connections=5`) | Deliberately low connection limit to trigger pool exhaustion |
| Pool Saturator Lambda | Lambda (Python 3.12, 128MB, 300s timeout) | Maintains 6 persistent MySQL connections to saturate the pool |
| EventBridge Rule | Scheduled rule (every 5 minutes) | Keeps the Lambda warm and connections alive |
| IAM Role | Lambda execution role | VPC access and RDS connectivity permissions |
| Security Group | Lambda security group | Allows outbound to RDS on port 3306 |
| DB subnet group | Subnet group | Spans subnets for RDS placement |
| `svc-alpha` (reused) | EC2 t3.micro from Scenario H | App-tier client for the database probe |

All resources are tagged with `goat-demo=true` and `goat-scenario=network-troubleshooting-k`.

### Expected Agent Correlations

When asked about database connectivity failures, the enhanced `db_connectivity_probe` performs multi-layer diagnosis from `svc-alpha` to the RDS endpoint:

- **Network checks pass** — Security groups and route tables correctly allow traffic on port 3306
- **Connection pool exhaustion detected** — `Threads_connected=6` vs `max_connections=5` (120% utilization)
- **Parameter group flagged** — `max_connections=5` identified as abnormally low (threshold < 50)
- **Remediation provided** — Increase `max_connections` in the parameter group, implement connection pooling (e.g., RDS Proxy), or reduce client concurrency

### Setup Instructions

Same as Scenario G — deployed with the `network-troubleshooting` scenario parameter.

### Suggested Demo Queries

| Query | What It Demonstrates |
|-------|---------------------|
| **"Use the rds troubleshooting tool to investigate why my application can't connect to the database"** | Full multi-layer `db_connectivity_probe` diagnosis with pool exhaustion detection |
| "Why is my RDS instance rejecting new connections?" | Agent identifies pool saturation as root cause |
| "Diagnose connection pool issues on svc-data-01" | Direct pool status check revealing `Threads_connected` ≥ `max_connections` |

### Two-Phase Evaluation

| Phase | Method | Expected Outcome |
|-------|--------|-----------------|
| **(a) Baseline** | Agent uses only `DescribeDBInstances`, `DescribeSecurityGroups`, `DescribeNetworkAcls` | Agent sees a healthy RDS instance in "available" state with correct security group rules allowing port 3306. Network path appears fully functional — no obvious blocking component |
| **(b) Tools-Assisted** | Agent invokes `db_connectivity_probe` from `svc-alpha` to the RDS endpoint on port 3306 | Probe performs comprehensive diagnosis: network checks pass, but connection test fails with MySQL error 1040 ("Too many connections"). Pool status shows `Threads_connected=6` / `max_connections=5` (exhausted). Parameter group analysis flags `max_connections=5` as abnormally low. Remediation steps recommend increasing `max_connections`, using RDS Proxy, or reducing client concurrency |

### Cost Note

| Resource | Approximate Cost |
|----------|-----------------|
| 1x RDS db.t4g.micro (MySQL) | ~$0.016/hr (~$12/month) |
| Pool Saturator Lambda (128MB, invoked every 5min) | ~$0.01/month |
| `svc-alpha` (shared with Scenario H) | No additional cost |
| EventBridge rule, IAM role, security group | No additional cost |

---

## Scenario L: SSM-Unreachable Instance (`ssm_health_check`)

Provisions an EC2 instance with the correct IAM instance profile for SSM but with a restrictive NACL that blocks HTTPS (port 443) outbound to the SSM VPC endpoint IPs. The instance appears correctly configured in IAM terms but the SSM agent cannot communicate with the service.

### What Gets Created

| Resource | Type | Purpose |
|----------|------|---------|
| `subnet-f` | Subnet (shared GOAT VPC) | Subnet for the SSM-unreachable instance |
| `subnet-a-host` | EC2 t3.micro | Instance with correct IAM profile but no traceroute opt-in tag |
| NACL | Network ACL on `subnet-f` | Blocks HTTPS (443) outbound to SSM VPC endpoint IPs |

All resources are tagged with `goat-demo=true` and `goat-scenario=network-troubleshooting-l`.

### Expected Agent Correlations

When asked why SSM commands fail against the instance, the agent uses `ssm_health_check` which reports `ssm_managed=false` or `ping_status=ConnectionLost` with `diagnostic_hints` suggesting network-path issues to SSM endpoints. This is information that `DescribeInstances` (which shows the correct IAM role) alone cannot surface.

### Setup Instructions

Same as Scenario G â€” deployed with the `network-troubleshooting` scenario parameter.

### Suggested Demo Queries

| Query | What It Demonstrates |
|-------|---------------------|
| **"Check SSM health for instance i-xxx"** | `ssm_health_check` shows agent unreachable |
| "Why can't I run SSM commands on subnet-a-host?" | Agent identifies network-level SSM connectivity issue |
| "The instance has the right IAM role but SSM shows it as unmanaged" | Cross-layer diagnosis (IAM correct, network blocks SSM) |

### Two-Phase Evaluation

| Phase | Method | Expected Outcome |
|-------|--------|-----------------|
| **(a) Baseline** | Agent uses only `DescribeInstances`, `DescribeIamInstanceProfileAssociations`, `DescribeVpcEndpoints` | Agent sees the correct IAM profile attached and VPC endpoints exist. Concludes SSM should work. Cannot determine that the NACL blocks HTTPS to the endpoint IPs without tracing the actual network path |
| **(b) Tools-Assisted** | Agent invokes `ssm_health_check` with the instance ID | Tool reports `ssm_managed=false` or `ping_status=ConnectionLost` with diagnostic hints pointing to network-path issues (NACL/route table blocking HTTPS to SSM endpoints). Root cause identified without manually inspecting every NACL rule |

### Cost Note

| Resource | Approximate Cost |
|----------|-----------------|
| 1x EC2 t3.micro (`subnet-a-host`) | ~$0.01/hr (~$7/month) |
| Subnets, NACLs | No additional cost |

---

## Scenario Gâ€“L Combined Cost Summary

| Scenario | Key Billable Resources | Approximate Monthly Cost |
|----------|----------------------|--------------------------|
| G | 1x EC2 t3.micro | ~$7 |
| H | 1x EC2 t3.micro (shared with K) | ~$7 |
| I | 1x Internal ALB | ~$16 |
| J | Route 53 Resolver endpoint (2 ENIs) | ~$180 |
| K | 1x RDS db.t4g.micro + Pool Saturator Lambda | ~$12 |
| L | 1x EC2 t3.micro | ~$7 |
| **Total (new resources only)** | | **~$229/month** |

Transit Gateway, NAT Gateway, and Network Firewall costs are attributed to Scenario C (already deployed). **The Route 53 Resolver endpoint is the single largest cost item. Clean up promptly after demos.**

---

## Cleanup

A single cleanup script removes all demo resources from all scenarios. It finds resources by the `goat-demo=true` tag and deletes them in dependency order.

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
2. AWS Network Firewall and rule groups
3. Transit Gateway attachments and route tables
4. Transit Gateway
5. RDS instances (delete, skip final snapshot)
6. DB subnet groups
7. EBS volumes
8. Elastic IPs (release)
9. NAT Gateways
10. Subnets
11. VPCs (inspection VPC only â€” shared GOAT VPC is owned by CDK)
12. Support cases (already resolved â€” cannot be deleted via API)

### Handling Partial Cleanup

If cleanup encounters errors (e.g., an RDS instance is still deleting), re-run the script. It handles already-deleted resources gracefully and continues processing remaining resources.

## Support Plan Behavior

Both provisioning scripts detect whether your account has an active AWS Business or Enterprise Support plan:

- **With Support plan**: Creates a Support case, adds a demo-purpose communication, and immediately resolves it
- **Without Support plan**: Prints a yellow warning and skips Support case creation entirely â€” all other resources are still created

The demo works without a Support plan, but the Support Cases agent domain will not have demo data to correlate.

## Tagging Convention

Every demo resource receives four tags for identification and cleanup:

| Tag Key | Value | Purpose |
|---------|-------|---------|
| `goat-demo` | `true` | Identifies all demo resources for cleanup |
| `goat-scenario` | `a`, `b`, or `connectivity` | Identifies which scenario created the resource |
| `Name` | `goat-demo-<descriptive>` | Human-readable name in AWS Console |
| `auto-delete` | `no` | Prevents automated cleanup policies from removing resources prematurely |

## Script Reference

| Script | Platform | Purpose |
|--------|----------|---------|
| `deploy-demo-scenarios.ps1` | PowerShell | **CDK deployment** â€” deploy scenarios via CloudFormation (recommended) |
| `deploy-demo-scenarios.sh` | Bash | **CDK deployment** â€” deploy scenarios via CloudFormation (recommended) |
| `deploy-demo-scenarios.ps1 -Scenario network-troubleshooting` | PowerShell | Deploy Scenario Gâ€“L (network diagnostic troubleshooting misconfigurations) |
| `deploy-demo-scenarios.sh --Scenario network-troubleshooting` | Bash | Deploy Scenario Gâ€“L (network diagnostic troubleshooting misconfigurations) |
| `setup-scenario-account-health.sh` | Bash | CLI provisioning â€” Account Health Check resources |
| `setup-scenario-account-health.ps1` | PowerShell | CLI provisioning â€” Account Health Check resources |
| `setup-scenario-cloudwatch-incident.sh` | Bash | CLI provisioning â€” CloudWatch Incident Correlation resources |
| `setup-scenario-cloudwatch-incident.ps1` | PowerShell | CLI provisioning â€” CloudWatch Incident Correlation resources |
| `setup-scenario-tls-fragmentation.sh` | Bash | CLI provisioning â€” Connectivity Scenario resources (legacy, use CDK instead) |
| `setup-scenario-tls-fragmentation.ps1` | PowerShell | CLI provisioning â€” Connectivity Scenario resources (legacy, use CDK instead) |
| `cleanup-scenarios.sh` | Bash | Remove all demo resources (both CDK and CLI) |
| `cleanup-scenarios.ps1` | PowerShell | Remove all demo resources (both CDK and CLI) |

PowerShell and Bash versions of each script produce identical AWS resources. Choose whichever matches your operating system.

## Troubleshooting

### "AWS credentials not configured"
Run `aws configure` or set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` environment variables. Verify with `aws sts get-caller-identity`.

### "Failed to create VPC" or subnet errors
Check your account's VPC limit in the region. The default limit is 5 VPCs per region. Delete unused VPCs or request a limit increase.

### RDS instance takes a long time
RDS instance creation takes 5-10 minutes. The script does not wait â€” it reports the identifier and continues. The instance will be available by the time you start your demo.

### Support case creation fails
Ensure your account has an active AWS Business or Enterprise Support plan. Without one, the script skips case creation with a warning â€” this is expected behavior.

### Cleanup fails for RDS
RDS deletion can take several minutes. If the cleanup script reports an error, wait a few minutes and re-run it. The script handles already-deleted resources gracefully.

### Trusted Advisor findings don't appear immediately
Trusted Advisor checks run on a schedule. After creating Scenario A resources, it may take up to 24 hours for the "Underutilized EBS Volumes" and "Unassociated Elastic IP" findings to appear. You can manually refresh checks in the Trusted Advisor console.

### Cost Explorer data not showing
Cost Explorer data has a 24-48 hour delay. If you just created resources, cost data will appear the next day. For immediate demos, focus on Trusted Advisor and Support Cases domains.
