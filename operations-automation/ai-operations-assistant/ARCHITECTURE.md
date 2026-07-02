# G.O.A.T. — Architecture

## High-Level Design

G.O.A.T. uses a **hybrid multi-agent architecture** where a single orchestration agent routes natural language questions to six specialized sub-agents, each responsible for one operational domain. The orchestration agent handles intent classification, cross-domain correlation, and response formatting; the sub-agents handle AWS API calls and domain-specific logic.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CloudFront + S3 (Frontend)                        │
│                    React + Cloudscape + Vite (SPA)                       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ Cognito Auth + SigV4
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              Orchestration Agent (Strands Agent SDK)                     │
│         Amazon Nova Pro / Claude Opus — intent routing, correlation      │
│                                                                         │
│   @tool query_cost_data       → Cost Agent                              │
│   @tool query_health_events   → Health Agent                            │
│   @tool query_support_cases   → Support Agent                           │
│   @tool query_trusted_advisor → TA Agent                                │
│   @tool query_cur_data        → CUR Agent                               │
│   @tool query_network_pcap    → Network Agent                           │
│   @tool prepare_capture_confirmation                                    │
│   @tool investigate_support_case (multi-agent workflow)                  │
└───┬──────┬──────┬──────┬──────┬──────┬──────────────────────────────────┘
    │      │      │      │      │      │
    ▼      ▼      ▼      ▼      ▼      ▼
┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────────────────────────────┐
│ Cost ││Health││Support││  TA  ││ CUR  ││         Network Agent        │
│Agent ││Agent ││ Agent ││Agent ││Agent ││                              │
│      ││      ││      ││      ││      ││  ENI Inventory               │
│ CE   ││ PHD  ││ Supp ││  TA  ││Athena││  Capture Lifecycle           │
│ COH  ││ API  ││ API  ││ API  ││ CUR  ││  Pcap Query (20 actions)     │
│ API  ││      ││      ││      ││      ││  Network Diagnostics (6)     │
│      ││      ││      ││      ││      ││  VPC Traffic Mirroring       │
└──────┘└──────┘└──────┘└──────┘└──────┘└──────────────────────────────┘
```

## Component Architecture

### Frontend Layer

| Component | Technology | Purpose |
|-----------|-----------|---------|
| SPA | React 18 + Cloudscape Design System | Chat UI, prompt templates, conversation history |
| Auth | Cognito User Pool + Identity Pool | USER_PASSWORD_AUTH → ID token → Identity Pool → temporary AWS credentials |
| Hosting | CloudFront + S3 | Static website distribution |
| Agent invoke | `@aws-sdk/client-bedrock-agentcore` | SigV4-signed streaming invoke of the orchestration runtime |

### Orchestration Layer

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Runtime | Bedrock AgentCore (containerized) | Hosts the orchestration agent as an HTTP service |
| Agent framework | Strands Agent SDK | Tool-use loop with foundation model (Nova Pro or Claude Opus) |
| Sub-agent invoke | `bedrock-agentcore:InvokeAgentRuntime` | Calls each sub-agent's runtime ARN with JSON payloads |
| State | DynamoDB (Conversations table) | Persists `Capture_Conversation_Context` for anaphor resolution |

### Sub-Agent Layer (×6)

Each sub-agent follows the same pattern:
- **Container**: Python 3.13, `bedrock-agentcore` SDK, built as ARM64 Docker image via CodeBuild
- **Entry point**: `BedrockAgentCoreApp` with a sync `@app.entrypoint` handler
- **Dispatch**: Dictionary-based action routing (`{"action": "...", "params": {...}}`)
- **No LLM**: Sub-agents are deterministic handlers — no foundation model calls

| Agent | AWS APIs | Key Actions |
|-------|----------|-------------|
| Cost | Cost Explorer, Cost Optimization Hub | `get_cost_summary`, `get_service_costs`, `get_daily_costs` |
| Health | AWS Health (PHD) | `describe_events`, `describe_event_details` |
| Support | AWS Support | `describe_cases`, `describe_communications`, `search_cases` |
| Trusted Advisor | Trusted Advisor v2 | `list_checks`, `list_recommendations` |
| CUR | Athena (Cost and Usage Report) | `query_cur_data`, `get_resource_costs`, `analyze_usage_patterns` |
| Network | EC2, SSM, DynamoDB, Athena, S3, EventBridge Scheduler, Step Functions | 27 actions total — 21 packet capture/analysis actions exposed via MCP, plus 6 network diagnostics actions (direct-invoke only, see below) |

### Network Agent — Extended Architecture

The Network Agent is the most complex sub-agent, providing on-demand VPC packet capture and deep TCP/TLS analysis:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Network Agent                                 │
│                                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │ ENI         │  │ Capture      │  │ Pcap Query Actions (14)    │ │
│  │ Inventory   │  │ Lifecycle    │  │                            │ │
│  │             │  │              │  │ query_pcap                 │ │
│  │ list_enis   │  │ start_capture│  │ search_fragmented_packets  │ │
│  │ reverse_dns │  │ stop_capture │  │ correlate_tcp_streams      │ │
│  │             │  │ list_captures│  │ detect_retransmissions     │ │
│  │             │  │ transform    │  │ check_tls_hello_size       │ │
│  │             │  │ get_progress │  │ get_conversation_stats     │ │
│  │             │  │              │  │ reconstruct_tcp_handshake  │ │
│  │             │  │              │  │ classify_tcp_resets        │ │
│  │             │  │              │  │ detect_out_of_order        │ │
│  │             │  │              │  │ detect_zero_window         │ │
│  │             │  │              │  │ analyze_tcp_options        │ │
│  │             │  │              │  │ get_rtt_distribution       │ │
│  │             │  │              │  │ get_request_response_lat   │ │
│  │             │  │              │  │ diagnose_tcp_stream        │ │
│  └─────────────┘  └──────┬───────┘  └────────────┬───────────────┘ │
│                           │                       │                  │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Network Diagnostics (6) — direct-invoke only, not yet in       ││
│  │  MCP tools/list or the orchestration agent's NETWORK_AGENT_ACTIONS ││
│  │                                                                  ││
│  │  SSM-based (opt-in tag required)  │  API-only (no tag required) ││
│  │  tcp_traceroute                   │  agentic_reachability_analyze││
│  │  tls_traceroute                   │  ssm_health_check            ││
│  │  dns_resolve                      │                              ││
│  │  db_connectivity_probe            │                              ││
│  └────────────────────────┬──────────────────────────┬─────────────┘│
└───────────────────────────┼───────────────────────┼─────────────────┘
                            │                       │
                            ▼                       ▼
┌───────────────────────────────────┐  ┌───────────────────────────────┐  ┌────────────────────────────┐
│   Capture Infrastructure          │  │   Query Infrastructure        │  │  Diagnostics Infrastructure│
│                                   │  │                               │  │                            │
│   Traffic Mirror Sessions (EC2)   │  │   Athena (pcap_logs table)    │  │  SSM Run Command (scripts)│
│   NLB → Collector Instance        │  │   Glue Database/Table         │  │  VPC Reachability Analyzer │
│   VXLAN splitter (scapy)          │  │   S3 (Parquet data)           │  │  SSM DescribeInstanceInfo  │
│   S3 uploader (inotifywait)       │  │                               │  │  Concurrency limiter       │
│   Step Functions (transform)      │  │                               │  │  (3 global, 1 per-instance)│
│   DynamoDB (state + VNI lookup)   │  │                               │  │                            │
│   EventBridge Scheduler (auto-stop)│  │                               │  │                            │
└───────────────────────────────────┘  └───────────────────────────────┘  └────────────────────────────┘
```

**Capture data flow:**
1. `start_capture` → creates Traffic Mirror sessions on 1–3 ENIs
2. Mirrored VXLAN packets → NLB → Collector instance (private subnet)
3. Scapy splitter demuxes by VNI → per-capture pcap files on disk
4. inotifywait uploader → S3 `raw/<capture_id>/`
5. `transform_capture` → Step Functions → tshark converts pcap → Parquet → S3 `parquet/`
6. Glue partition registered → Athena queryable
7. Pcap Query Actions run SQL against `pcap_logs` with automatic `capture_id` predicate injection

**Network diagnostics data flow (SSM-based tools — `tcp_traceroute`, `tls_traceroute`, `dns_resolve`, `db_connectivity_probe`):**
1. Handler validates params and checks the `goat-network-traceroute-allowed=true` opt-in tag via `ec2:DescribeInstances` (Windows instances are rejected)
2. Concurrency limiter reserves a slot (max 3 global, 1 per-instance)
3. A zero-dependency Python script (stdlib only, Python 3.6+ compatible) is generated from a template and sent via `ssm:SendCommand`
4. Script runs in `/tmp` with an `EXIT` trap for cleanup, writes a marker line + JSON result to stdout
5. `ssm_executor.py` polls `ssm:GetCommandInvocation` (every 2s, up to 65 polls) and parses the JSON after the marker
6. Concurrency slot released; result returned in the standard `build_response()` envelope

**Network diagnostics data flow (API-only tools):**
- `agentic_reachability_analyze`: creates a Network Insights Path, starts and polls an analysis (every 5s, up to 120s), reports `path_components` (reachable) or a `blocking_component` + remediation (blocked), plus a `limitations` array for known Reachability Analyzer gaps (TGW Connect, GWLB endpoints); best-effort deletes the path afterward
- `ssm_health_check`: queries `ssm:DescribeInstanceInformation`; falls back to `ec2:DescribeInstances` to distinguish "not SSM-managed" from "instance doesn't exist"

**Note:** The 6 diagnostics actions are registered in the Network Agent's `ACTIONS` dispatch table and can be invoked directly (e.g., via the AgentCore SDK's `InvokeAgentRuntime`), but are **not yet wired into** the GOAT orchestration agent's `query_network_pcap` tool allowlist or the DevOps Agent MCP `tools/list` schema — both currently expose only the original 21 packet-capture/pcap-analysis actions.

### DevOps Agent Integration (MCP)

The Network Agent is also exposed to the **AWS DevOps Agent** via a native MCP (Model Context Protocol) server. This is a **SEPARATE** integration path from the GOAT chat application — the GOAT frontend uses the Strands Agent SDK and AgentCore runtimes directly, while DevOps Agent connects via JSON-RPC 2.0 over streamable HTTP with SigV4 authentication.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          AWS DevOps Agent                                 │
│                        (MCP Client, SigV4)                               │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ JSON-RPC 2.0 over HTTPS
                               │ POST / (MCP messages)
                               │ GET /health (monitoring)
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│            API Gateway (IAM Auth, execute-api:Invoke)                     │
│                                                                          │
│   POST /  →  Integration Lambda (MCP Handler)                            │
│   GET /health  →  Integration Lambda (Health Check)                      │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                   Integration Lambda (MCP Handler)                        │
│                   Code.fromAsset('dist/') — esbuild bundled              │
│                                                                          │
│  ┌────────────────────┐                                                  │
│  │  JSON-RPC 2.0      │                                                  │
│  │  Router            │──→ initialize   → protocolVersion, capabilities  │
│  │                    │──→ tools/list   → 21 MCP tool definitions        │
│  │                    │──→ tools/call   → adapter → processInvocation    │
│  │                    │──→ ping         → empty result                   │
│  │                    │──→ notifications/initialized → HTTP 204           │
│  └────────────────────┘                                                  │
│           │                                                              │
│           ▼ (tools/call only)                                            │
│  ┌────────────────────────────────────────────────────────────┐          │
│  │  tools/call Adapter                                        │          │
│  │  params.name → action_name                                 │          │
│  │  params.arguments → parameters                             │          │
│  │  Mcp-Session-Id header → session_id (idempotency)          │          │
│  └────────────────────────┬───────────────────────────────────┘          │
│                           │                                              │
│                           ▼                                              │
│  ┌────────────────────────────────────────────────────────────┐          │
│  │  processInvocation (existing business logic)                │          │
│  │  validateRequest → checkAuthorization → checkRateLimit* →  │          │
│  │  generateIdempotencyToken → invokeNetworkAgent → format    │          │
│  │  * Rate limiter fails open (try-catch wrapper)             │          │
│  └────────────────────────────────────────────────────────────┘          │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ @aws-sdk/client-bedrock-agentcore
                               │ InvokeAgentRuntimeCommand
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    Network Agent Runtime (AgentCore)                      │
│              NETWORK_AGENT_ARN env var → InvokeAgentRuntime               │
└──────────────────────────────────────────────────────────────────────────┘
```

**MCP protocol flow:**
1. DevOps Agent sends `initialize` → receives server capabilities (protocol version `2024-11-05`)
2. DevOps Agent sends `tools/list` → receives 21 MCP tool definitions (23 in registry minus 2 hidden)
3. DevOps Agent sends `tools/call` with tool name and arguments → adapter maps to `processInvocation` → Network Agent executes via `InvokeAgentRuntimeCommand` → response wrapped as `CallToolResult`

**Hidden tools** (not exposed via `tools/list`):
- `full_diagnostic` — composite action not supported by the Network Agent runtime directly
- `cleanup_orphaned_sessions` — maintenance utility without a Network Agent counterpart

**Agent proxy:**
- Uses `@aws-sdk/client-bedrock-agentcore` with `InvokeAgentRuntimeCommand` (NOT `@aws-sdk/client-bedrock-agent-runtime` which is standard Bedrock Agents)
- Payload format: `{"action": "action_name", "params": {...}}`
- `NETWORK_AGENT_ARN` env var provides the runtime ARN

**Registration:**
- The `GOATDevOpsIntegration` CDK stack includes an `AWS::DevOpsAgent::Service` CloudFormation resource with type `mcpserversigv4`
- This auto-registers the MCP server endpoint with DevOps Agent at deploy time — no manual CLI step required
- The IAM role trusts `aidevops.amazonaws.com` with confused deputy protection (`aws:SourceAccount` + `aws:SourceArn` conditions)

**Tool descriptions:**
- Enriched workflow-aware descriptions (no `[Category:]` prefix)
- `start_capture`, `stop_capture`, `transform_capture` include confirmation prompts ("IMPORTANT: Before calling this tool, you MUST stop and ask the user for explicit confirmation")
- `query_pcap` uses `sql` parameter (not `query`)

**Rate limiting:**
- Rate limiter wrapped in try-catch — fails open if DynamoDB is unavailable
- The Network Agent has its own rate limiting as a backstop
- `AUTHORIZED_ROLE_ARNS=*` — API Gateway IAM auth is the real access gate

**Key differences from GOAT chat path:**

| Aspect | GOAT Chat App | DevOps Agent (MCP) |
|--------|---------------|-------------------|
| Protocol | Strands Agent SDK (AgentCore invoke) | JSON-RPC 2.0 over streamable HTTP |
| Auth | Cognito → Identity Pool → SigV4 | IAM SigV4 (`aidevops.amazonaws.com` role) |
| Discovery | Orchestration agent knows sub-agent ARNs | MCP `tools/list` returns 21 tool definitions |
| Invocation | `{"action": "...", "params": {...}}` | `{"method": "tools/call", "params": {"name": "...", "arguments": {...}}}` |
| Response | `DevOpsAgentResponse` envelope | `CallToolResult` (JSON-RPC 2.0 result) |
| Session | DynamoDB conversation context | `Mcp-Session-Id` header |
| Agent SDK | `@aws-sdk/client-bedrock-agentcore` | `@aws-sdk/client-bedrock-agentcore` (same) |
| Access gate | Cognito group membership | API Gateway IAM auth (`AUTHORIZED_ROLE_ARNS=*`) |

## Infrastructure (CDK)

All infrastructure is defined in TypeScript CDK with a base-class pattern:

```
infrastructure/cdk/
├── bin/app.ts                      # Main CDK app (19 stacks)
├── bin/demo-scenarios-app.ts       # Demo scenario stacks
├── lib/
│   ├── base-infra-stack.ts         # Base: ECR, S3, CodeBuild, IAM
│   ├── base-runtime-stack.ts       # Base: source upload, build trigger, AgentCore runtime
│   ├── auth-stack.ts               # Cognito User Pool + Identity Pool
│   ├── data-stack.ts               # DynamoDB tables (conversations)
│   ├── frontend-stack.ts           # S3 + CloudFront + deploy
│   ├── [domain]-infra-stack.ts     # Per-agent infra (×7)
│   ├── [domain]-runtime-stack.ts   # Per-agent runtime (×7)
│   ├── network-infra-stack.ts      # Extended: VPC, collector, NLB, DDB, Glue, SFN
│   └── network-data-stack.ts       # Network data bucket (conditional)
└── collector/                      # Collector bootstrap + bundled wheels

devops-integration/
├── dist/                                   # esbuild output (Code.fromAsset target)
├── src/
│   ├── constructs/
│   │   └── agent-integration-template.ts  # Reusable CDK construct (MCP server)
│   ├── lambda/                             # MCP handler, tools-call adapter, session mgr, agent-proxy
│   ├── schemas/                            # Action schemas + MCP descriptions (21 exposed tools)
│   └── types/                              # Shared interfaces (MCP types, errors)
├── infrastructure/cdk/
│   └── lib/devops-integration-stack.ts     # Stack using the template construct
└── docs/
    └── AGENT-INTEGRATION-GUIDE.md          # Integration guide for new agents
```

**Stack dependency graph:**
```
Auth ─────────────────────────────────────────────────┐
Data ─────────────────────────────────────────────────┤
                                                      │
CostInfra → CostRuntime ──────────────────────────────┤
HealthInfra → HealthRuntime ──────────────────────────┤
SupportInfra → SupportRuntime ────────────────────────┤
TAInfra → TARuntime ──────────────────────────────────┤
CURInfra → CURRuntime ───────────────────────────────┤
NetworkData? → NetworkInfra → NetworkRuntime ──────────┤
                                                      │
OrchInfra → OrchRuntime (receives all sub-agent ARNs) ┤
                                                      │
GOATDevOpsIntegration (MCP server for DevOps Agent) ──┤
                                                      │
                                      Frontend ◄──────┘
```

## Demo Scenario C — Connectivity (Network Firewall TLS Fragmentation)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    GOAT Network Agent VPC (10.99.0.0/16)                 │
│                                                                         │
│  ┌─────────────────────────────┐   ┌──────────────────────────────────┐│
│  │ App Subnet (10.99.13/24)    │   │ Collector Subnet (10.99.0/24)    ││
│  │                             │   │  [PRIVATE_ISOLATED + VPC Endpts] ││
│  │  EC2 (t3.micro, AL2023)     │   │  EC2 (t3.small) + NLB           ││
│  │  curl --curves MLKEM        │   │  scapy splitter + S3 uploader   ││
│  │  → TGW → NFW → ECR         │   │  Traffic Mirror Target           ││
│  └────────────┬────────────────┘   └──────────────────────────────────┘│
└───────────────┼─────────────────────────────────────────────────────────┘
                │ TGW (appliance mode)
                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                Security VPC (10.98.0.0/16)                               │
│                                                                         │
│  TGW Subnet → NFW (STRICT_ORDER, drop_established) → NAT → IGW → ECR  │
│               Pass rule: tls.sni endswith .amazonaws.com                 │
│               ↓ Drops connection: Client Hello > MSS (fragmented)       │
└─────────────────────────────────────────────────────────────────────────┘
```

**Why connections fail:** The ML-KEM (X25519MLKEM768) TLS Client Hello is ~1500-2000 bytes — larger than the 1460-byte TCP MSS on the TGW path. TCP splits it across multiple segments. The Network Firewall's SNI-based pass rule can only read the first segment and can't extract the full SNI, so `aws:drop_established` drops the connection.

## Security Model

| Boundary | Mechanism |
|----------|-----------|
| User → Frontend | Cognito USER_PASSWORD_AUTH + refresh tokens (no plaintext password storage) |
| Frontend → AgentCore | Cognito Identity Pool → temporary STS credentials → SigV4 |
| Orchestration → Sub-agents | AgentCore workload identity (IAM-scoped per runtime) |
| DevOps Agent → MCP endpoint | IAM SigV4 via `aidevops.amazonaws.com` trusted role + confused deputy protection |
| Capture write actions | Cognito group `GOATNetworkCaptureUsers` authorization gate |
| Pcap SQL | Hand-rolled shape validator + predicate injector (no raw SQL passthrough) |
| Collector | Private isolated subnet, no internet, only VPC endpoints (S3/DDB/SSM) |

## Cost Estimate

| Component | Monthly cost (us-east-1) |
|-----------|------------------------|
| AgentCore runtimes (7 agents, idle) | ~$0 (pay-per-invoke) |
| Bedrock model inference | $2–40 depending on usage |
| DynamoDB (on-demand) | < $1 |
| S3 (pcap storage) | < $1 |
| CloudFront + S3 (frontend) | < $1 |
| Collector EC2 (t3.small, always-on) | ~$15 |
| NLB (always-on) | ~$16 |
| VPC Interface Endpoints (3× SSM) | ~$22 |
| Network Firewall (demo scenario) | ~$25 |
| NAT Gateway (demo scenario) | ~$33 |
| Transit Gateway (demo scenario) | ~$36 |
| DevOps Integration (API GW + Lambda) | < $1 (pay-per-invoke) |
| **Total (full demo with network scenario)** | **~$48–90/month** |
| **Total (core only, no network scenario)** | **~$8–48/month** |

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
