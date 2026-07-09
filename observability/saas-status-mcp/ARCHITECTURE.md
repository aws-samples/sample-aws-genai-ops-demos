# Architecture — SaaS Status MCP Server

## Overview

This MCP server bridges AWS DevOps Agent's internal investigation capabilities with the external SaaS health signals that live outside AWS. It runs as a stateless Python server hosted on Amazon Bedrock AgentCore Runtime, exposing four MCP tools that DevOps Agent can call mid-investigation to correlate infrastructure signals with upstream dependency status.

---

## High-Level Architecture

```
┌──────────────────┐        ┌─────────────────────────┐        ┌────────────────────┐
│  AWS DevOps      │  MCP   │  AgentCore Runtime      │ HTTPS  │  Statuspage.io     │
│  Agent           │───────>│  (saas-status-mcp)      │───────>│  Public APIs       │
│  (Investigation) │        │                         │        │  (no auth needed)  │
└──────────────────┘        └─────────────────────────┘        └────────────────────┘
                                      │
                                      │ Conditional GET (ETag)
                                      ▼
                            ┌─────────────────────┐
                            │  S3: providers.json  │
                            │  (live registry)    │
                            └─────────────────────┘
```

### Request flow

1. DevOps Agent is investigating an alert and decides to check upstream dependencies.
2. It invokes the MCP server via the `bedrock-agentcore:InvokeAgentRuntime` API, signing with SigV4.
3. AgentCore Runtime routes the call to the MCP server process over `streamable-http` on port 8000.
4. The server fans out concurrent HTTPS requests to the relevant Statuspage.io public API endpoints.
5. Results are normalized and returned as structured JSON to DevOps Agent.

---

## Components

### AgentCore Runtime

The hosting layer. AgentCore Runtime manages the container lifecycle, IAM authentication, and the MCP protocol transport so the server code has no AWS SDK calls in the hot path — it only does outbound HTTP.

- **Transport**: `streamable-http` (stateless, required by AgentCore Runtime)
- **Network mode**: `PUBLIC` — the runtime makes outbound calls to public Statuspage.io endpoints; no VPC needed
- **Entrypoint**: `main.py` via `FastMCP`
- **Runtime environment**: Python 3.13

### MCP Server (`agent/`)

| File | Responsibility |
|------|---------------|
| `main.py` | FastMCP app definition; declares the four `@mcp.tool` functions; binds to `0.0.0.0:8000` |
| `tools.py` | Tool implementations; `check_all_dependencies` fans requests out with `asyncio.gather` |
| `statuspage_client.py` | Async HTTP client (`httpx`) for the Statuspage.io `/api/v2/*` contract |
| `config.py` | S3-backed provider registry with ETag-based conditional GET — avoids reloading unless the file changes |
| `providers.json` | Source-controlled seed registry (28 providers); uploaded to S3 on first deploy |

### Provider Registry (S3)

The registry is a JSON array of `{name, display_name, statuspage_url}` objects. It is stored in S3 at `s3://saas-status-mcp-<account>-<region>/config/providers.json` and read by the server at startup and then re-checked every 60 seconds via a conditional GET (using the `ETag` and `If-None-Match` headers). If the object has not changed, S3 returns a `304 Not Modified` and the server keeps its cached copy — zero read cost on steady state.

This design allows operators to update the live provider list by pushing a new `providers.json` to S3 (via `refresh-providers.ps1/.sh`) without touching code or redeploying.

### IAM

| Role | Principal | Permissions |
|------|-----------|-------------|
| `SaasStatusMcpRuntimeRole` | `bedrock-agentcore.amazonaws.com` | `s3:GetObject` on deployment bucket; `logs:PutLogEvents` on `/aws/bedrock-agentcore/runtimes/*` |
| SigV4 signing role (registration stack) | `aidevops.amazonaws.com` | `bedrock-agentcore:InvokeAgentRuntime` on the runtime ARN |

DevOps Agent assumes the signing role when invoking the runtime. The runtime itself assumes the runtime role to read from S3 and write logs.

### CloudWatch Logs

Structured JSON logs from the server are written to `/aws/bedrock-agentcore/runtimes/*` with a 14-day retention policy. The log group is torn down on stack destroy (`RemovalPolicy.DESTROY`).

---

## CDK Stacks

| Stack | Deployed to | Purpose |
|-------|-------------|---------|
| `SaasStatusMcpStack-{region}` | Runtime region | AgentCore Runtime, runtime IAM role, CloudWatch log group |
| `SaasStatusMcpRegistrationStack-{space-region}` | Agent Space region | SigV4 signing role, DevOps Agent Service, Association |

The registration stack is optional and only deployed when you run `setup-devops-agent`. The two stacks can target different regions — the runtime ARN is exported from the main stack and imported by the registration stack.

---

## Design Decisions

### Stateless by design

There is no database and no persisted state. Every tool call is a fresh read from Statuspage.io. This keeps the server simple, eliminates stale-data bugs, and makes horizontal scaling trivial — AgentCore Runtime can spin up multiple instances without coordination.

### Single Statuspage.io client covers 80%+ of providers

Most major SaaS vendors (Snowflake, Datadog, GitHub, MongoDB, PagerDuty, etc.) run on Atlassian Statuspage.io, which exposes a uniform public REST API at `/api/v2/status.json`, `/api/v2/incidents/unresolved.json`, and `/api/v2/scheduled-maintenances/active.json`. One generic client handles all of them — no provider-specific code, and adding a new provider is a JSON entry in the registry with no code change.

### Parallel fan-out in `check_all_dependencies`

`asyncio.gather` is used to fire all provider requests concurrently. For a 10-provider bulk check, wall-clock time is the max of individual response times rather than their sum — typically under 2 seconds.

### ETag-based config caching

The provider registry is polled every 60 seconds using `If-None-Match` / `ETag` headers. On steady state (no registry change) S3 returns `304 Not Modified` with no body — avoiding both unnecessary data transfer and stale-config latency without a cache invalidation mechanism.

### SigV4 authentication at the runtime boundary

The AgentCore Runtime endpoint is not a public HTTP API. All callers must sign requests with `bedrock-agentcore:InvokeAgentRuntime`. The MCP server code itself is unaware of authentication — IAM is enforced at the runtime layer. Local clients (e.g. Kiro) use `local-proxy/proxy.py`, a stdio-to-SigV4-HTTP bridge that signs requests with the local AWS credentials.

---

## Local Development (Kiro)

```
┌──────────────┐  stdio  ┌─────────────────┐  SigV4/HTTPS  ┌───────────────────────┐
│  Kiro MCP    │────────>│  local-proxy/   │─────────────>│  AgentCore Runtime    │
│  client      │         │  proxy.py       │               │  (deployed)           │
└──────────────┘         └─────────────────┘               └───────────────────────┘
```

The proxy bridges the stdio transport expected by local MCP clients to the SigV4-signed HTTPS transport required by AgentCore Runtime. `deploy-all` generates `local-proxy/mcp.json` with the runtime ARN and region pre-filled — this file is gitignored since it contains account-specific values.
