# SaaS Status MCP Server for AWS DevOps Agent
*Give AWS DevOps Agent eyes on your upstream SaaS dependencies during an investigation, so it stops chasing internal infrastructure for an outage that isn't yours*

## Overview

AWS DevOps Agent is a powerful investigation tool — it can dig through pods, logs, databases, and security groups in seconds. But when the real root cause is upstream (Snowflake down, Datadog degraded, a third-party API on fire), that signal lives outside AWS. This MCP server bridges that gap, giving the agent real-time visibility into SaaS health without leaving the investigation flow.

Today that gap costs operators 15-30 minutes of internal deep-diving before someone manually checks external status pages and finds the issue was upstream all along.

This demo closes that gap with a small, remote MCP server hosted on Amazon Bedrock AgentCore. It exposes SaaS status page data as MCP tools that DevOps Agent can call mid-investigation. Most major SaaS providers run on Atlassian Statuspage.io with the same public `/api/v2/*` contract, so one generic client covers Snowflake, Datadog, GitHub, PagerDuty, and a dozen others without provider-specific code.

The core question the server answers: **"Is anything happening right now on my upstream dependencies?"**

## At a Glance

- **Duration**: ~10 min deployment + ~10 min demo
- **Difficulty**: Beginner
- **Target Audience**: SREs, DevOps Engineers, Platform Engineers, TAMs/SAs demoing DevOps Agent
- **Key Technologies**: Amazon Bedrock AgentCore Runtime, Python 3.12, MCP (streamable-http), Statuspage.io public API, AWS CDK (Python)
- **Estimated Cost**: ~$2-5/month at demo usage levels — see [Estimated Cost Breakdown](#estimated-cost-breakdown)

## Business Value

- **Faster root cause identification**: cuts time-to-detect-external-cause from ~25 minutes of manual checking to under 2 minutes of autonomous correlation.
- **Fewer wasted internal investigations**: the agent stops treating an upstream outage as an internal infrastructure problem.
- **Reusable across any customer**: the provider registry is a JSON config, so any DevOps Agent customer can point the same server at their own list of SaaS dependencies with no code changes.
- **Extends DevOps Agent's reach**: pairs natively with the agent's investigation workflow — correlate infrastructure signals with upstream SaaS health in a single conversation, without leaving the agent.

## What You'll See

1. A sample customer application is set up as depending on Snowflake (analytics) and Datadog (monitoring).
2. A CloudWatch alarm fires — API latency exceeds 5 seconds.
3. AWS DevOps Agent starts an autonomous investigation and calls `check_all_dependencies` for Snowflake and Datadog.
4. The server reports an active incident on Snowflake with impact `major`.
5. The agent reports: *"Root cause identified: upstream dependency Snowflake is experiencing a major incident. No internal infrastructure issue detected. Monitor https://status.snowflake.com for resolution."*
6. Mean time to (correct) root cause drops from ~25 minutes of manual digging to ~2 minutes of autonomous correlation.

## Target Providers

28 providers are included out of the box. 80%+ of major SaaS providers run Atlassian Statuspage.io, so this one client covers all of them via the same `/api/v2/*` contract:

| Provider | Status Page | API Base |
|----------|-------------|----------|
| Snowflake | status.snowflake.com | status.snowflake.com/api/v2 |
| Datadog | status.datadoghq.com | status.datadoghq.com/api/v2 |
| MongoDB Atlas | status.cloud.mongodb.com | status.cloud.mongodb.com/api/v2 |
| PagerDuty | status.pagerduty.com | status.pagerduty.com/api/v2 |
| Splunk | status.splunk.com | status.splunk.com/api/v2 |
| New Relic | status.newrelic.com | status.newrelic.com/api/v2 |
| GitHub | www.githubstatus.com | www.githubstatus.com/api/v2 |
| GitLab | status.gitlab.com | status.gitlab.com/api/v2 |
| ServiceNow | status.servicenow.com | status.servicenow.com/api/v2 |
| Atlassian/Jira | status.atlassian.com | status.atlassian.com/api/v2 |
| Grafana Cloud | status.grafana.com | status.grafana.com/api/v2 |
| Dynatrace | status.dynatrace.com | status.dynatrace.com/api/v2 |

Adding a new provider is a JSON entry in the registry — no code change, and no redeploy (the server reads the registry from S3; run `refresh-providers` to push an update).

### Updating the provider registry

The provider registry (`agent/providers.json`) is the source-controlled seed. On first deploy it is uploaded to S3, and the running server reads it via a conditional GET — so you can update the live registry without touching code or redeploying.

**1. Edit `agent/providers.json`** — add or remove an entry following the existing pattern:

```json
{
  "name": "pagerduty",
  "display_name": "PagerDuty",
  "statuspage_url": "https://status.pagerduty.com"
}
```

**2. Push the update to S3:**

**Windows (PowerShell):**
```powershell
.\scripts\refresh-providers.ps1
```

**macOS/Linux:**
```bash
./scripts/refresh-providers.sh
```

The script uploads `agent/providers.json` to `s3://saas-status-mcp-<account>-<region>/config/providers.json` and confirms how many providers are now live. The running server picks up the change within its poll interval (default 60 seconds) — no restart, no CDK, no zip.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full breakdown of components, data flow, IAM roles, CDK stacks, and design decisions.

```
┌──────────────────┐        ┌─────────────────────────┐        ┌────────────────────┐
│  AWS DevOps      │  MCP   │  AgentCore Runtime      │ HTTPS  │  Statuspage.io     │
│  Agent           │───────>│  (saas-status-mcp)      │───────>│  Public APIs       │
│  (Investigation) │        │                         │        │  (no auth needed)  │
└──────────────────┘        └─────────────────────────┘        └────────────────────┘
                                      │
                                      │ Config
                                      ▼
                            ┌─────────────────────┐
                            │  Provider Registry  │
                            │  (providers.json)   │
                            └─────────────────────┘
```

The server is a single stateless Python MCP server, deployed to Amazon Bedrock AgentCore Runtime over the `streamable-http` transport. It reads its saas providers registry from S3 (a conditional GET, so the list can be updated without a redeploy), fans requests out to the relevant Statuspage.io public endpoints (no authentication required), and returns structured results back to DevOps Agent as MCP tool responses. There is no database and no persisted state — every call is a fresh read.

## Prerequisites

- AWS CLI v2.31.13+ with configured credentials
- Python 3.12+
- Node.js 20+ (for the CDK CLI)
- AgentCore available in your target region ([check availability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html))
- An existing AWS DevOps Agent Space to register this server against (see the [DevOps Agent EKS investigation demo](../eks-investigation-devops-agent/README.md) if you need to stand one up)

## Quick Start

Two IaC options — CDK (default) or Terraform. Both produce the same result.

**CDK — Windows (PowerShell):**
```powershell
cd observability\saas-status-mcp
.\deploy-all.ps1
```

**CDK — macOS/Linux:**
```bash
cd observability/saas-status-mcp
./deploy-all.sh
```

**Terraform — Windows (PowerShell):**
```powershell
.\deploy-all-terraform.ps1
```

**Terraform — macOS/Linux:**
```bash
./deploy-all-terraform.sh
```

Each script:
1. Validates prerequisites
2. Packages and uploads the MCP server to S3
3. Deploys the runtime infrastructure (AgentCore Runtime + IAM role)
4. Generates `local-proxy/mcp.json` — ready-to-use Kiro config with the runtime ARN pre-filled
5. Offers to register the MCP server with your DevOps Agent Space

**Time**: ~10 minutes.

## MCP Tools Exposed

Four provider-agnostic tools, focused on answering "is anything happening right now?":

### `list_providers`
Returns every provider configured in the registry (name, display name, status page URL). No external call — reads local config.

```json
// output
{
  "providers": [
    { "name": "snowflake", "display_name": "Snowflake", "url": "https://status.snowflake.com" },
    { "name": "datadog", "display_name": "Datadog", "url": "https://status.datadoghq.com" }
  ]
}
```

### `get_service_status`
Current overall status for one provider. A quick, lightweight check — useful as a first-pass test or to confirm a new provider works.

```json
// input
{ "provider": "snowflake" }

// output
{
  "provider": "snowflake",
  "status": "degraded_performance",
  "description": "Degraded Performance",
  "last_updated": "2026-07-06T15:30:00Z",
  "url": "https://status.snowflake.com"
}
```

### `get_active_events`
The core investigation tool. Retrieves all currently active events for a provider by calling both the unresolved incidents and active scheduled maintenances endpoints, then merging and normalizing the results.

```json
// input
{ "provider": "mongodb" }

// output (real-world example: MongoDB Atlas unresolved incident)
{
  "provider": "mongodb",
  "events": [
    {
      "event_type": "incident",
      "id": "7g5qmxgkc2y4",
      "name": "Impaired Cluster Operations - AWS me-central-1 and AWS me-south-1",
      "status": "monitoring",
      "impact": "major",
      "created_at": "2026-03-01T13:48:14Z",
      "updated_at": "2026-06-03T15:46:56Z",
      "started_at": "2026-03-01T13:48:14Z",
      "resolved_at": null,
      "scheduled_for": null,
      "scheduled_until": null,
      "shortlink": "https://stspg.io/mg7m971rdhw8",
      "affected_components": [
        { "name": "Cloud Services - AWS me-central-1", "status": "degraded_performance" },
        { "name": "Cloud Services - AWS me-south-1", "status": "degraded_performance" }
      ],
      "latest_update": {
        "status": "monitoring",
        "body": "We are continuing to monitor cluster operations in these regions.",
        "created_at": "2026-06-03T15:46:56Z"
      }
    }
  ],
  "total_active": 1
}
```

When no events are active (the common case):
```json
{ "provider": "snowflake", "events": [], "total_active": 0 }
```

Optional `include_history=true` parameter returns full update history per event. Default is `false` to keep DevOps Agent context lean.

### `check_all_dependencies`
Bulk check across up to 10 providers in one call, run in parallel internally. Returns status and active event count per provider for quick triage.

```json
// input
{ "providers": ["snowflake", "datadog", "mongodb"] }

// output
{
  "results": [
    { "provider": "snowflake", "status": "operational", "active_events": 0 },
    { "provider": "datadog", "status": "operational", "active_events": 0 },
    { "provider": "mongodb", "status": "degraded_performance", "active_events": 1 }
  ],
  "any_degraded": true,
  "degraded_providers": ["mongodb"]
}
```

## Consuming the server

The runtime is protected by IAM (SigV4) — it is not a plain public HTTP endpoint. Callers invoke it through the `bedrock-agentcore:InvokeAgentRuntime` API, signing requests with AWS credentials. There are two consumers:

### Register with AWS DevOps Agent (intended consumer)

DevOps Agent runs inside AWS and assumes an IAM role, so it invokes the runtime natively — no proxy. Registration is a property of the Agent Space, so cross-region is fine: the runtime can live in one region (e.g. `eu-west-3`) while the Agent Space and its registration live in another (e.g. `eu-west-1`).

**Automated (recommended).** Create your Agent Space in the DevOps Agent console first, then run:

**Windows (PowerShell):**
```powershell
.\scripts\setup-devops-agent.ps1
```

**macOS/Linux:**
```bash
./scripts/setup-devops-agent.sh
```

The script prompts for your Agent Space ARN (open the DevOps Agent console and from your space click **Actions > Copy ARN**), auto-detects the runtime ARN and endpoint from the CloudFormation stack, creates the SigV4 signing IAM role, registers the MCP server, and enables the four tools on your space. It is idempotent — safe to re-run. `deploy-all` also offers to run it at the end.

**Manual.** If you register through the console instead, these are the exact field values (SigV4 authorization config):

| Field | Value |
|-------|-------|
| **Endpoint / URL** | `https://bedrock-agentcore.<runtime-region>.amazonaws.com/runtimes/<url-encoded-runtime-arn>/invocations?qualifier=DEFAULT` (the `RuntimeEndpoint` stack output — copy it verbatim) |
| **AWS Region** | the **runtime's** region (e.g. `eu-west-3`) — this is the SigV4 signing region, not the Agent Space region |
| **Service Name** | `bedrock-agentcore` |
| **Role** | the SigV4 signing role below |
| **Custom Headers** | none |
| **Tools** | `list_providers`, `get_service_status`, `get_active_events`, `check_all_dependencies` |

The endpoint places the **URL-encoded runtime ARN** in the path (`:` -> `%3A`, `/` -> `%2F`) followed by `/invocations?qualifier=DEFAULT`. Use the `RuntimeEndpoint` stack output directly rather than building it by hand.

**IAM signing role.** DevOps Agent assumes this role to sign the call. Trust policy (`<space-region>` is your Agent Space's region):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "aidevops.amazonaws.com" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": { "aws:SourceAccount": "<account-id>" },
      "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:<space-region>:<account-id>:service/*" }
    }
  }]
}
```

Permission policy (allow invoking this runtime):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeAgentRuntime",
    "Resource": [
      "arn:aws:bedrock-agentcore:<runtime-region>:<account-id>:runtime/<runtime-id>",
      "arn:aws:bedrock-agentcore:<runtime-region>:<account-id>:runtime/<runtime-id>/*"
    ]
  }]
}
```

### Test locally from Kiro (optional)

Local MCP clients can't SigV4-sign, so use the bundled bridge (`local-proxy/proxy.py`) — a stdio MCP server that signs each call with your AWS credentials and forwards to the runtime.

`deploy-all` generates `local-proxy/mcp.json` with your runtime ARN and region pre-filled. To connect Kiro to the deployed MCP Server on Bedrock AgentCore Runtime:

```bash
pip install -r local-proxy/requirements.txt
```

Then merge `local-proxy/mcp.json` into your Kiro `mcp.json`. The file is gitignored since it contains your account-specific runtime ARN.

## Estimated Cost Breakdown

All costs approximate, `us-east-1` pricing. This server makes lightweight outbound HTTPS calls and holds no state, so it's inexpensive to run.

| Resource | Usage | $/month |
|----------|-------|---------|
| AgentCore Runtime | Low invocation volume (demo-level, a few hundred calls) | $1-3 |
| CloudWatch Logs | Structured JSON request logs | <$1 |
| Data transfer | Small JSON responses from Statuspage.io | <$1 |
| **Total** | | **~$2-5/month** |

No Bedrock model costs — this server exposes tools only, it does not call an LLM itself. Statuspage.io's public API requires no API key and has no per-call charge. Production cost scales with investigation volume via AgentCore Runtime request/duration pricing.

## Project Structure

```
saas-status-mcp/
├── README.md                     # This file
├── deploy-all.ps1                # One-command deploy (Windows)
├── deploy-all.sh                 # One-command deploy (macOS/Linux)
├── refresh-providers.ps1         # Update the live provider registry (no redeploy)
├── refresh-providers.sh
├── agent/                        # MCP server (packaged flat to AgentCore Runtime)
│   ├── main.py                   # FastMCP entry point — 4 @mcp.tool functions
│   ├── tools.py                  # Tool implementations
│   ├── statuspage_client.py      # Async HTTP client for the Statuspage.io API
│   ├── config.py                 # Provider registry loader (S3 conditional read)
│   ├── providers.json            # Provider registry seed (uploaded to S3 on deploy)
│   └── requirements.txt
├── scripts/
│   ├── setup-devops-agent.ps1    # Register the MCP server with a DevOps Agent Space
│   ├── setup-devops-agent.sh
│   ├── refresh-providers.ps1     # Update the live provider registry (no redeploy)
│   └── refresh-providers.sh
├── local-proxy/                  # SigV4 stdio bridge for local MCP clients (Kiro)
│   ├── proxy.py
│   └── requirements.txt
├── tests/
│   ├── test_tools.py             # Unit tests (mocked Statuspage.io responses)
│   ├── invoke_test.py            # Invoke the deployed runtime end-to-end
│   └── fixtures/                 # Mock API responses
└── infrastructure/
    ├── cdk/
    │   ├── app.py                # CDK app entry point (tracking + region suffix)
    │   ├── stack.py              # CDK stack: AgentCore Runtime + IAM role
    │   ├── registration_stack.py # CDK stack: DevOps Agent registration
    │   ├── cdk.json
    │   └── requirements.txt
    └── terraform/
        ├── main.tf               # AgentCore Runtime + IAM role
        ├── registration.tf       # DevOps Agent registration
        ├── variables.tf
        ├── outputs.tf
        └── terraform.tfvars.example
```

The provider registry lives in `agent/providers.json` as the source-controlled seed. On deploy it is uploaded to `s3://<bucket>/config/providers.json`, and the running server reads it from S3 via a conditional GET. To add or change providers on a live server, edit the registry and run `refresh-providers` — no redeploy needed.

## CDK Stacks

| Stack | Region | Purpose | Key Resources |
|-------|--------|---------|---------------|
| `SaasStatusMcpStack-{region}` | Runtime region | MCP server hosting | AgentCore Runtime, Runtime IAM role, CloudWatch log group |
| `SaasStatusMcpRegistrationStack-{space-region}` | Agent Space region | DevOps Agent registration | SigV4 signing role, DevOps Agent Service, Association |

The registration stack is optional — only deployed when you run `setup-devops-agent`. The Terraform path (`infrastructure/terraform/`) deploys the same resources without CDK.

## Cleanup

**CDK — Windows (PowerShell):**
```powershell
cd infrastructure\cdk
npx cdk destroy SaasStatusMcpRegistrationStack-<space-region> --no-cli-pager
npx cdk destroy SaasStatusMcpStack-<runtime-region> --no-cli-pager
```

**CDK — macOS/Linux:**
```bash
cd infrastructure/cdk
npx cdk destroy SaasStatusMcpRegistrationStack-<space-region>
npx cdk destroy SaasStatusMcpStack-<runtime-region>
```

**Terraform:**
```bash
cd infrastructure/terraform
terraform destroy -auto-approve
```

Also delete the S3 bucket (`saas-status-mcp-<account>-<region>`) manually — neither CDK nor Terraform manages it.

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
