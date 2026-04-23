# Intelligent Site-to-Site VPN Tunnel Investigation with Amazon DevOps Agent

*Automated root-cause analysis and business-context enrichment for AWS Site-to-Site VPN failures — powered by Amazon DevOps Agent.*

## Overview

AWS Site-to-Site VPN tunnels fail for dozens of reasons — pre-shared key mismatches, IKE proposal incompatibilities, dead-peer-detection timeouts, BGP session drops, and subtle throughput degradation. When a tunnel goes down at 2 AM, an on-call engineer must sift through CloudWatch metrics, VPN tunnel logs, and IPsec configuration to find the root cause. That manual triage is slow, error-prone, and expensive.

This demo deploys a fully self-contained VPN environment — two VPCs, a Libreswan/GoBGP customer gateway on Amazon Linux 2023, per-tunnel CloudWatch alarms, and a throughput alarm — then lets you inject 10 realistic failure scenarios and watch Amazon DevOps Agent automatically investigate each one. The agent reads VPN tunnel logs, correlates CloudWatch metrics, identifies the root cause, and enriches its findings with business context from an MCP server that provides service dependency, cost impact, and compliance data.

What makes this demo unique: per-tunnel alarms ensure that even a single tunnel failure triggers the agent (not just full VPN outages), a metric-math throughput alarm catches performance degradation before a full outage, BGP routing scenarios exercise dynamic routing failures, and the MCP integration shows how the agent combines AWS telemetry with organizational context to produce actionable incident reports.

## At a Glance

- **Duration**: ~25 minutes total (Agent Space setup + infrastructure deployment)
- **Difficulty**: Intermediate
- **Audience**: DevOps engineers, SREs, cloud architects evaluating Amazon DevOps Agent
- **Key technologies**: AWS Site-to-Site VPN, CloudWatch, SNS, Lambda, CloudFormation, Libreswan, GoBGP, Amazon DevOps Agent, MCP (Model Context Protocol)
- **Cost**: ~$0.12/hr (VPN connection + 2× t3.micro instances + public IPv4 addresses + CloudWatch/Lambda/SNS)
- **Failure scenarios**: 10 total — 5 IKE scenarios + 3 BGP scenarios + 1 route withdrawal scenario + 1 throughput scenario (run last two with dedicated alarms enabled)
- **Routing modes**: BGP (dynamic routing)

## DevOps Agent Features Demonstrated

| Feature | Description |
|---|---|
| **Automated Investigation** | CloudWatch alarm → SNS → Lambda webhook → DevOps Agent automatically triages the incident |
| **MCP Integration** | Agent queries an MCP server for service dependencies, cost impact, and compliance status |
| **On-demand Chat** | Use the Operator App to ask the agent follow-up questions about any incident |
| **Per-tunnel Monitoring** | Individual TunnelState alarms per tunnel IP — single tunnel failure triggers investigation |
| **Throughput Monitoring** | Metric-math alarm detects performance degradation — agent investigates even when tunnels remain UP |
| **BGP Route Monitoring** | CloudWatch Logs metric filter detects BGP route withdrawals — agent investigates routing changes that don't affect tunnel state |

## Architecture

![Architecture](architecture.drawio.png)

- **Network layer**: Two VPCs created from scratch (no existing VPC dependencies). Cloud VPC (10.0.0.0/16) connects to On-Prem VPC (172.16.0.0/16) via a Site-to-Site VPN with two IPsec tunnels. The CGW instance runs Libreswan for IPsec and GoBGP for BGP on Amazon Linux 2023.
- **Monitoring layer**: Four CloudWatch alarms — two per-tunnel `TunnelState` alarms (using `TunnelIpAddress` dimension), one throughput alarm using metric math, and one route-withdrawn alarm using a CloudWatch Logs metric filter. All alarms publish to an SNS topic that triggers a Lambda function to send a webhook to DevOps Agent.
- **Intelligence layer**: DevOps Agent receives the webhook, investigates VPN tunnel logs and CloudWatch metrics, then queries the MCP server for business context (service dependencies, cost impact, compliance status) to produce a comprehensive incident report.

## Prerequisites

- **AWS CLI** v2.34.21+ with credentials configured
- **EC2 key pair** in your target region
- **bash** 4+ and **jq**
- No existing DevOps Agent Space needed — the setup script creates one

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/aws-samples/sample-aws-genai-ops-demos.git
cd sample-aws-genai-ops-demos/networking/vpn-tunnel-investigation-devops-agent
```

### 2. Set up DevOps Agent

Run the setup script to create IAM roles, an Agent Space, and configure the webhook:

```bash
bash scripts/setup-devops-agent.sh --region us-east-1
```

The script automates steps 1–4 and pauses at step 5 for you to create the webhook:

1. Creates IAM roles (`DevOpsAgentRole-AgentSpace` and `DevOpsAgentRole-WebappAdmin`)
2. Creates an Agent Space named `vpn-demo-agent-space`
3. Associates your AWS account with the Agent Space
4. Enables the Operator App with IAM auth
5. **Pauses** — the script prints an AWS DevOps Agent console URL and asks you to create a webhook:
   1. Open the **AWS DevOps Agent console** URL printed by the script
   2. Under **Webhooks**, click **Add**
   3. Copy the **Webhook URL** and **Webhook Secret** shown (save these — they won't be shown again)
   4. Paste them back into the terminal when prompted

Save the webhook URL and secret — you'll need them in the next step.

### 3. Deploy the VPN infrastructure

```bash
bash deploy.sh \
  --region us-east-1 \
  --key-pair my-key-pair \
  --key-file ~/.ssh/my-key.pem \
  --webhook-url 'https://your-webhook-url' \
  --webhook-secret 'your-webhook-secret'
```

| Flag | Required | Description |
|---|---|---|
| `--region` | Yes | AWS region |
| `--key-pair` | Yes | EC2 key pair name (must exist in the region) |
| `--key-file` | Yes | Path to the private key file for SSH access to the CGW |
| `--webhook-url` | Yes | DevOps Agent webhook URL from step 2 |
| `--webhook-secret` | Yes | DevOps Agent webhook secret from step 2 |
| `--stack-name` | No | CloudFormation stack name (default: vpn-devops-demo) |

The deploy script:
1. Creates the CloudFormation stack (2 VPCs, VPN connection, SNS topic, webhook Lambda)
2. SSHes into the CGW instance and configures Libreswan (IPsec) + GoBGP (BGP)
3. Installs inject/rollback/status/list scripts on the CGW
4. Creates 4 CloudWatch alarms (2 per-tunnel, 1 throughput, 1 route-withdrawn)
5. Starts baseline ping traffic for the throughput alarm

> **Console alternative**: You can deploy the infrastructure from the AWS Console instead of the CLI:
> 1. Go to **CloudFormation → Create stack → Upload a template file** → upload `vpn-demo.yaml`
> 2. Fill in the parameters: key pair name, webhook URL, webhook secret
> 3. Click **Create stack** and wait for it to complete
> 4. Then configure the CGW from your terminal:
>    ```bash
>    bash scripts/setup-cgw.sh <stack-name> <region> <key-file>
>    ```

### 4. Deploy the MCP Server

The MCP server gives DevOps Agent business context (service dependencies, cost impact, compliance status) during investigations. It runs as a Lambda function behind API Gateway.

**4a. Package and deploy:**

```bash
# Set your account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create an S3 bucket for the Lambda package
aws s3 mb s3://my-mcp-bucket-${AWS_ACCOUNT_ID} --region us-east-1

# Package and upload
cd mcp-server
zip app.zip app.py
aws s3 cp app.zip s3://my-mcp-bucket-${AWS_ACCOUNT_ID}/app.zip
cd ..

# Deploy the MCP server stack
aws cloudformation deploy \
  --template-file mcp-server/template.yaml \
  --stack-name vpn-devops-mcp-server \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    S3Bucket=my-mcp-bucket-${AWS_ACCOUNT_ID} \
    S3Key=app.zip \
  --region us-east-1
```

**4b. Get the endpoint URL and API key:**

```bash
# Endpoint URL
aws cloudformation describe-stacks \
  --stack-name vpn-devops-mcp-server \
  --query "Stacks[0].Outputs[?OutputKey=='McpEndpoint'].OutputValue" \
  --output text --region us-east-1

# API key
API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name vpn-devops-mcp-server \
  --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" \
  --output text --region us-east-1)
aws apigateway get-api-key --api-key "$API_KEY_ID" --include-value \
  --query 'value' --output text --region us-east-1
```

**4c. Register in DevOps Agent:**

MCP server registration and tool enablement are done in the **AWS DevOps Agent console** (not the Operator App).

1. Open the **AWS DevOps Agent console** → select your Agent Space → **Capabilities**
2. Under **MCP Server**, click **Add**
3. Click **Register** to register a new MCP server:
   - **Name**: `vpn-demo-mcp-server`
   - **Endpoint URL**: *(the endpoint URL from step 4b)*
   - **Authorization flow**: select **API Key**
   - **API Key Name**: `vpn-demo-mcp-key`
   - **API Key Header**: `x-api-key`
   - **API Key Value**: *(the API key from step 4b)*
4. After registration, click **Add** next to the registered server
5. Select all three tools: `get_service_dependencies`, `get_cost_impact`, `get_compliance_status`
6. Click **Save**

> **Note:** After saving, the console shows a webhook URL and secret for the MCP server. You do not need these for this demo — the alarm webhook from step 2 is the one that triggers investigations. The MCP server is called by the agent during investigations, not the other way around.

## How the Incident Detection Works

```
  Tunnel goes down or throughput drops
           │
           ▼
  ┌─────────────────────────┐
  │ CloudWatch Alarm fires  │  Per-tunnel: TunnelState < 1 (60s period, 1 eval)
  │                         │  Throughput: (m1+m2)*8/300 < 100 bps (300s, 1 eval)
  │                         │  Route-withdrawn: log metric filter on "WITHDRAWN" (60s, 1 eval)
  │                         │  treat-missing-data: breaching (tunnels/throughput), notBreaching (route)
  └──────────┬──────────────┘
             │
             ▼
  ┌─────────────────────────┐
  │ SNS Topic               │  vpn-demo-tunnel-alarm
  └──────────┬──────────────┘
             │
             ▼
  ┌─────────────────────────┐
  │ Lambda (webhook)        │  Sends HMAC-signed payload with incidentId,
  │ vpn-demo-webhook        │  priority, title, description, timestamp
  └──────────┬──────────────┘
             │
             ▼
  ┌─────────────────────────┐
  │ Amazon DevOps Agent     │  1. Reads VPN tunnel logs from CloudWatch
  │                         │  2. Checks VPN connection state & metrics
  │                         │  3. Queries MCP server for business context
  │                         │  4. Produces root-cause analysis
  └─────────────────────────┘
```

**Per-tunnel alarms** use the `TunnelIpAddress` dimension so that a single tunnel failure (while the other stays up) still triggers the agent. **Throughput alarm** uses metric math — `(TunnelDataIn + TunnelDataOut) * 8 / 300` — to detect performance degradation even when tunnels remain technically "up."

## Failure Scenarios

The `inject-failure.sh` script injects realistic failures **on the customer gateway (CGW) instance** via SSH. Each scenario modifies IPsec, BGP, or network configuration on the CGW to simulate a real-world failure. Configuration files are backed up before injection and every scenario supports rollback.

```bash
# Inject a failure
bash scripts/inject-failure.sh psk-mismatch vpn-devops-demo us-east-1 --key-file ~/.ssh/my-key.pem

# Rollback
bash scripts/inject-failure.sh psk-mismatch vpn-devops-demo us-east-1 --key-file ~/.ssh/my-key.pem --rollback

# Check IPsec/BGP status
bash scripts/inject-failure.sh status vpn-devops-demo us-east-1 --key-file ~/.ssh/my-key.pem

# List all scenarios
bash scripts/inject-failure.sh list
```

Replace `psk-mismatch` with any scenario name from the tables below. The stack name defaults to `vpn-devops-demo` if you didn't change it during deployment.

### IKE Scenarios (5)

These scenarios break the IPsec tunnel at the IKE negotiation layer. Each one produces a distinct error in the Site-to-Site VPN tunnel logs, and the agent identifies the specific root cause — not just "tunnel is down."

| # | Scenario | What It Simulates | What the Agent Finds |
|---|---|---|---|
| 1 | psk-mismatch | Customer rotates the pre-shared key on the CGW but forgets to update AWS | Reads VPN tunnel logs, identifies PSK mismatch as root cause |
| 2 | dpd-timeout | Firewall on CGW blocks IKE traffic (UDP 500/4500) | Identifies DPD timeout pattern, distinguishes from PSK or proposal issues |
| 3 | proposal-mismatch | CGW configured with an unsupported IKE proposal (wrong DH group) | Finds "No Proposal Match" in logs, identifies the specific incompatible parameter |
| 4 | traffic-selector | CGW changes its local subnet, excluding BGP tunnel IPs from the IPsec SA | IPsec stays up but BGP breaks — agent traces the root cause to traffic selector mismatch. Best demonstrated with **on-demand chat** |
| 5 | tunnel-down | CGW deliberately shuts down both IPsec tunnels | Identifies CGW-initiated tunnel teardown vs AWS-side failure |

### BGP Scenarios (3)

These scenarios break BGP routing while IPsec remains up — a subtler failure class. The agent must distinguish between "tunnel down" and "tunnel up but routing broken."

| # | Scenario | What It Simulates | What the Agent Finds |
|---|---|---|---|
| 6 | bgp-down | BGP daemon crashes or is stopped on the CGW | Finds BGP Cease/Peer Unconfigured notification, identifies customer-side BGP shutdown while IPsec remains healthy |
| 7 | bgp-asn-mismatch | CGW misconfigured with wrong ASN after a maintenance change | Finds "bad OPEN message - remote AS 65999, expected 65000", pinpoints the exact ASN mismatch |
| 8 | bgp-hold-timer | Firewall on CGW blocks BGP keepalives (TCP 179) | Finds Hold Timer Expired, correlates with missing keepalives, distinguishes from BGP daemon failure |

### Dedicated-Alarm Scenarios (run last — enable alarm before inject, disable after rollback)

These scenarios use specialized alarms that are deployed with actions **disabled** to avoid false triggers during other tests. Enable the alarm before injecting, disable after rollback.

| # | Scenario | What It Simulates | What the Agent Finds |
|---|---|---|---|
| 9 | bgp-route-withdraw | CGW stops advertising a network prefix (e.g., after a routing policy change) | Finds Route status WITHDRAWN in VPN logs, identifies the specific prefix removed, detects black hole condition if static routes persist |
| 10 | throughput-degradation | Network path degradation on CGW causing packet loss on VPN traffic | Throughput alarm fires while tunnels remain UP — agent investigates performance degradation, not just outages |

> **Note on bgp-route-withdraw**: If run after other BGP scenarios, the agent may link the alert to the prior investigation. If this happens, use **on-demand chat** in the Operator App to ask: "A BGP route was withdrawn for 172.16.0.0/16 — can you investigate?" This also demonstrates the on-demand chat feature.

> **Throughput alarm**: The throughput alarm is deployed with actions **disabled** by default. Most scenarios cause traffic to drop (triggering a noisy second alarm), so it should only be enabled when testing `throughput-degradation`. Enable it right before injecting, and disable it after rollback:
> ```bash
> # Enable before throughput test
> aws cloudwatch enable-alarm-actions --alarm-names vpn-demo-throughput-drop --region <region>
>
> # Disable after rollback
> aws cloudwatch disable-alarm-actions --alarm-names vpn-demo-throughput-drop --region <region>
> ```

> **Route-withdrawn alarm**: Similarly, the route-withdrawn alarm is deployed **disabled**. Other BGP scenarios (bgp-down, bgp-asn-mismatch) also cause route withdrawals as a side effect. Enable it only for `bgp-route-withdraw`:
> ```bash
> # Enable before route-withdraw test
> aws cloudwatch enable-alarm-actions --alarm-names vpn-demo-route-withdrawn --region <region>
>
> # Disable after rollback
> aws cloudwatch disable-alarm-actions --alarm-names vpn-demo-route-withdrawn --region <region>
> ```

> **Rollback**: Every scenario supports `--rollback`. The script backs up `/etc/ipsec.d/vpn-demo.conf`, `/etc/ipsec.d/vpn-demo.secrets`, and `/etc/gobgp.toml` to `/tmp/vpn-demo-backup/` before injection. Rollback restores from these backups or reverses iptables/tc rules.

## MCP Server (Service Context Provider)

The MCP server runs as a Lambda function behind API Gateway and implements the [Model Context Protocol](https://modelcontextprotocol.io/) (JSON-RPC 2.0). It provides three tools that give DevOps Agent business context during investigations:

| Tool | Input | Returns |
|---|---|---|
| `get_service_dependencies` | `resource_id` | Dependent services (payment-gateway, order-api, inventory-sync), criticality levels, on-call team, escalation contact, affected end users (~12,000 active sessions) |
| `get_cost_impact` | `resource_id`, `downtime_minutes` | Revenue loss ($4,200/min), transaction rate (847 txn/min), SLA breach status (threshold: 30 min, penalty: $50,000), annual availability SLA (99.95%) |
| `get_compliance_status` | `resource_id` | Compliance frameworks (PCI-DSS: 15 min reporting, SOC 2 Type II: 60 min reporting), data classification, incident response policy |

**Example investigation flow**: Alarm fires → Agent identifies tunnel1 PSK mismatch from VPN logs → Agent calls `get_service_dependencies` to find payment-gateway is CRITICAL → Agent calls `get_cost_impact` with 10 min downtime to calculate $42,000 revenue loss → Agent calls `get_compliance_status` to flag PCI-DSS 15-minute reporting requirement → Agent produces a comprehensive incident report with root cause, business impact, and recommended actions.

## Run the Demo

After completing the [Quick Start](#quick-start) deployment:

### 1. Pick a scenario and inject

```bash
bash scripts/inject-failure.sh psk-mismatch vpn-devops-demo us-east-1 --key-file ~/.ssh/my-key.pem
```

### 2. Watch the agent investigate

Open the Operator App. Within 1–3 minutes, the agent receives the alarm webhook and begins its investigation — reading VPN logs, checking metrics, querying the MCP server, and producing a root-cause analysis.

### 3. Rollback

```bash
bash scripts/inject-failure.sh psk-mismatch vpn-devops-demo us-east-1 --key-file ~/.ssh/my-key.pem --rollback
```

### 4. Repeat

Resolve the current investigation in the Operator App, wait for alarms to return to OK, then inject the next scenario.

> **Tip — Running multiple scenarios**: If you inject multiple failures back-to-back, DevOps Agent will correlate them as related incidents on the same VPN connection — which is correct production behavior, but may not produce a standalone RCA for each scenario. For clean, independent investigations, wait between scenarios.

## Project Structure

```
aws-site-to-site-vpn-devops-agent-demo/
├── deploy.sh                        # One-command deployment (CFN + CGW config + alarms)
├── vpn-demo.yaml                    # CloudFormation template
├── architecture.drawio              # Editable architecture diagram (draw.io)
├── architecture.drawio.png          # Architecture diagram image
├── cgw-scripts/                     # Installed on CGW at /opt/vpn-demo/
│   ├── inject                       # Inject a failure scenario
│   ├── rollback                     # Reverse a failure scenario
│   ├── status                       # Show IPsec, VTI, and BGP state
│   └── list                         # List available scenarios
├── scripts/
│   ├── setup-devops-agent.sh        # Create Agent Space + IAM roles + webhook
│   ├── setup-cgw.sh                 # Configure CGW (for Console-deployed stacks)
│   ├── inject-failure.sh            # SSH wrapper to run inject/rollback from your laptop
│   └── cleanup.sh                   # Delete alarms + CloudFormation stack
├── mcp-server/
│   ├── app.py                       # MCP server Lambda (JSON-RPC 2.0)
│   └── template.yaml                # MCP server CloudFormation
└── README.md
```

| File | Description |
|---|---|
| **deploy.sh** | End-to-end deployment: creates the CloudFormation stack, SSHes into the CGW to configure Libreswan (IPsec) and GoBGP (BGP), creates 4 CloudWatch alarms (2 per-tunnel, 1 throughput, 1 route-withdrawn with metric filter), starts baseline ping traffic, and installs the cgw-scripts. |
| **vpn-demo.yaml** | CloudFormation template that creates two VPCs (cloud 10.0.0.0/16 + on-prem 172.16.0.0/16), a Site-to-Site VPN with tunnel logging, an SNS topic, and a conditional webhook Lambda that sends HMAC-signed payloads to DevOps Agent. Installs Libreswan, GoBGP, iptables-nft, and iproute-tc via UserData. |
| **cgw-scripts/inject** | Runs on the CGW. Backs up IPsec/BGP config, then injects one of 10 failure scenarios. |
| **cgw-scripts/rollback** | Runs on the CGW. Reverses the injected failure — restores config backups, removes iptables/tc rules, restarts services as needed. |
| **cgw-scripts/status** | Runs on the CGW. Prints IPsec tunnel status, VTI interface state, tunnel reachability, and GoBGP neighbor/route table. |
| **cgw-scripts/list** | Runs on the CGW. Prints all 10 scenarios grouped by category with usage examples. |
| **scripts/setup-devops-agent.sh** | Creates the IAM roles (AgentSpace + Operator App), creates an Agent Space, associates your AWS account, enables the Operator App, and prompts you to create a webhook in the console. |
| **scripts/setup-cgw.sh** | Standalone CGW configuration script — same as deploy.sh post-CFN steps. Use this if you deployed the CloudFormation stack via the AWS Console instead of deploy.sh. |
| **scripts/inject-failure.sh** | SSH wrapper that runs inject/rollback/status on the CGW from your laptop. Looks up the CGW IP from CloudFormation outputs automatically. |
| **scripts/cleanup.sh** | Deletes the 4 CloudWatch alarms, the metric filter, and the CloudFormation stack. |
| **mcp-server/app.py** | MCP server implementing JSON-RPC 2.0 with 3 tools: get_service_dependencies (dependent services, on-call team), get_cost_impact (revenue loss, SLA breach status), get_compliance_status (PCI-DSS/SOC 2 reporting requirements). |
| **mcp-server/template.yaml** | CloudFormation template for the MCP server: Lambda function, API Gateway REST API with API key authentication, usage plan. |

## Cost Estimate

| Resource | Hourly Cost |
|---|---|
| VPN connection (1.25 Gbps) | $0.05 |
| 2× t3.micro instances | $0.03 |
| Public IPv4 addresses (4) | $0.02 |
| CloudWatch alarms (4) | < $0.01 |
| Lambda, SNS, CloudWatch Logs | < $0.01 |
| **Total** | **~$0.12/hr** |

> **Tip**: Run `bash scripts/cleanup.sh vpn-devops-demo us-east-1` when done to avoid ongoing charges.

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| SSH not connecting to CGW | Instance not ready or wrong key | Wait 2-3 minutes after stack creation for the instance to boot. Verify the key file matches the key pair used during deployment. The CGW instance is in the simulated on-prem VPC but runs in AWS — it has internet access via its own Internet Gateway. |
| Tunnels not establishing | Libreswan config or PSK issue | Run `inject-failure.sh status <stack> <region> --key-file <path>` to check IPsec status. Verify security group allows UDP 500/4500 inbound. |
| Alarm not firing | Alarm not created or wrong dimensions | Verify alarms exist: `aws cloudwatch describe-alarms --alarm-name-prefix vpn-demo --region <region>`. Check the VPN ID and tunnel IP dimensions match. |
| BGP not establishing | GoBGP not installed or wrong ASN | Run `inject-failure.sh status <stack> <region> --key-file <path>` to check BGP summary. Verify `--routing bgp` was used during deployment. |
| GoBGP not installed | Deployed with `--routing static` | Redeploy with `--routing bgp` to enable GoBGP configuration. |
| Webhook not triggering agent | Lambda not subscribed to SNS or wrong URL/secret | Check the Lambda function `vpn-demo-webhook` exists (only created when `--webhook-url` is provided). Verify SNS subscription is confirmed. |
| Throughput alarm not firing | No baseline traffic or wrong metric math | Verify ping traffic is running on the CGW: `inject-failure.sh status <stack> <region> --key-file <path>`. The alarm uses `(m1+m2)*8/300 < 100 bps` with 1 evaluation period. |
| Route-withdrawn alarm not firing | Metric filter not created or alarm actions disabled | Verify the metric filter exists: `aws logs describe-metric-filters --log-group-name /vpn-demo/tunnel-logs --region <region>`. Ensure alarm actions are enabled: `aws cloudwatch enable-alarm-actions --alarm-names vpn-demo-route-withdrawn --region <region>`. |
| MCP server returning 403 | Missing or invalid API key | Retrieve the API key value using the `ApiKeyId` output and verify it matches what's registered in DevOps Agent. |

## Cleanup

```bash
# Delete alarms and CloudFormation stack
bash scripts/cleanup.sh vpn-devops-demo us-east-1

# Delete the MCP server stack
aws cloudformation delete-stack --stack-name vpn-devops-mcp-server --region us-east-1

# (Optional) Delete the Agent Space
aws devops-agent delete-agent-space --agent-space-id <id> --region us-east-1

# (Optional) Delete IAM roles created by setup script
aws iam detach-role-policy --role-name DevOpsAgentRole-AgentSpace \
  --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy
aws iam delete-role-policy --role-name DevOpsAgentRole-AgentSpace \
  --policy-name AllowCreateServiceLinkedRoles
aws iam delete-role --role-name DevOpsAgentRole-AgentSpace

aws iam detach-role-policy --role-name DevOpsAgentRole-WebappAdmin \
  --policy-arn arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy
aws iam delete-role --role-name DevOpsAgentRole-WebappAdmin
```

## Contributing

See [CONTRIBUTING](../../CONTRIBUTING.md) for more information.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
