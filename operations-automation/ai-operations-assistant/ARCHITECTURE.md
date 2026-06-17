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
│ API  ││      ││      ││      ││      ││  VPC Traffic Mirroring       │
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
| Network | EC2, DynamoDB, Athena, S3, EventBridge Scheduler, Step Functions | 21 actions (see below) |

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
└───────────────────────────┼───────────────────────┼──────────────────┘
                            │                       │
                            ▼                       ▼
┌───────────────────────────────────┐  ┌───────────────────────────────┐
│   Capture Infrastructure          │  │   Query Infrastructure        │
│                                   │  │                               │
│   Traffic Mirror Sessions (EC2)   │  │   Athena (pcap_logs table)    │
│   NLB → Collector Instance        │  │   Glue Database/Table         │
│   VXLAN splitter (scapy)          │  │   S3 (Parquet data)           │
│   S3 uploader (inotifywait)       │  │                               │
│   Step Functions (transform)      │  │                               │
│   DynamoDB (state + VNI lookup)   │  │                               │
│   EventBridge Scheduler (auto-stop)│  │                               │
└───────────────────────────────────┘  └───────────────────────────────┘
```

**Capture data flow:**
1. `start_capture` → creates Traffic Mirror sessions on 1–3 ENIs
2. Mirrored VXLAN packets → NLB → Collector instance (private subnet)
3. Scapy splitter demuxes by VNI → per-capture pcap files on disk
4. inotifywait uploader → S3 `raw/<capture_id>/`
5. `transform_capture` → Step Functions → tshark converts pcap → Parquet → S3 `parquet/`
6. Glue partition registered → Athena queryable
7. Pcap Query Actions run SQL against `pcap_logs` with automatic `capture_id` predicate injection

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
```

**Stack dependency graph:**
```
Auth ─────────────────────────────────────────────────┐
Data ─────────────────────────────────────────────────┤
                                                      │
CostInfra → CostRuntime ──────────────────────────────┤
HealthInfra → HealthRuntime ──────────────────────────┤
SupportInfra → SupportRuntime ────────────────────────┤
TAInfra → TARunt ime ─────────────────────────────────┤
CURInfra → CURRuntime ───────────────────────────────┤
NetworkData? → NetworkInfra → NetworkRuntime ──────────┤
                                                      │
OrchInfra → OrchRuntime (receives all sub-agent ARNs) ┤
                                                      │
                                      Frontend ◄──────┘
```

## Demo Scenario C — Connectivity (Network Firewall TLS Fragmentation)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    GOAT Network Agent VPC (10.99.0.0/16)                 │
│                                                                         │
│  ┌─────────────────────────┐   ┌──────────────────────────────────────┐│
│  │ App Subnet (10.99.13/24)│   │ Collector Subnet (10.99.0/24)        ││
│  │                         │   │  [PRIVATE_ISOLATED + VPC Endpoints]  ││
│  │  EC2 (t3.micro, AL2023) │   │  EC2 (t3.small) + NLB               ││
│  │  curl --curves MLKEM    │   │  scapy splitter + S3 uploader        ││
│  │  → TGW → NFW → ECR     │   │  Traffic Mirror Target               ││
│  └────────────┬────────────┘   └──────────────────────────────────────┘│
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
| **Total (full demo with network scenario)** | **~$48–90/month** |
| **Total (core only, no network scenario)** | **~$8–48/month** |

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
