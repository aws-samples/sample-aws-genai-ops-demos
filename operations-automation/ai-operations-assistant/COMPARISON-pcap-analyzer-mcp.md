# Comparison: AWS sample-pcap-analyzer-mcp vs GOAT Network Agent MCP

## Overview

| Aspect | sample-pcap-analyzer-mcp | GOAT Network Agent MCP |
|--------|--------------------------|------------------------|
| **Purpose** | Local/cloud pcap file analysis using tshark | Remote network diagnostics + live probing via SSM |
| **Approach** | Wireshark/tshark protocol dissection | Custom Python scripts + boto3 AWS APIs on remote EC2 |
| **Packet capture** | Live capture via tshark on local machine | VPC Traffic Mirroring (NLB → EC2 collector) |
| **Where it runs** | Locally (stdio) OR Lambda behind AgentCore Gateway | Lambda behind API Gateway (SigV4) + DevOps Agent |
| **Storage** | PCAP files on local disk or S3 | S3 (raw pcap) → Parquet → SQL query |
| **Tools count** | 31 tools (8 categories) | 20 tools (diagnostics + packet analysis) |
| **Analysis engine** | tshark CLI (display filters, protocol dissectors) | Custom Python (socket probes, boto3 API calls) |
| **MCP protocol** | Standard MCP stdio or AgentCore Gateway | Custom REST API adapted for DevOps Agent SigV4 |
| **Auth** | Cognito OAuth2 + IAM via AgentCore | SigV4 via DevOps Agent service association |
| **Dependencies** | Requires tshark installed (or Lambda layer) | No external dependencies — Python stdlib + boto3 |
| **Real-time probing** | No — analyzes existing captures only | Yes — live TCP, TLS, DNS, MySQL probes from instances |

## What They Have That We Don't

1. **tshark-powered deep protocol dissection** — full Wireshark protocol decoder library (BGP, HTTP/2, QUIC, etc.)
2. **Standard MCP stdio protocol** — works with any MCP client (Claude Desktop, Cursor, Kiro, Q Developer)
3. **Published on PyPI** — `uvx awslabs.pcap-analyzer-mcp-server@latest` (one command install)
4. **31 specialized analysis tools** — more granular categories:
   - TLS cipher analysis
   - TCP zero window detection
   - Congestion indicators
   - Security threat detection
   - Application response times
   - Network quality metrics (jitter, packet loss)
5. **Local live capture** — can capture directly on developer's machine
6. **AgentCore Gateway integration** — official AgentCore deployment pattern with OAuth2/Cognito

## What We Have That They Don't

1. **Remote execution on EC2 via SSM** — runs diagnostics FROM the application's perspective (inside VPC)
2. **Active network probing tools**:
   - `tcp_traceroute` — finds where packets drop
   - `tls_traceroute` — diagnoses certificate/handshake mismatches
   - `dns_resolve` — detects split-horizon, stale records
   - `db_connectivity_probe` — 6-layer RDS diagnosis (DNS → instance → network → connection → pool → params)
3. **VPC Reachability Analyzer** — `agentic_reachability_analyze` uses AWS Network Insights to find SG/NACL blocking
4. **Connection pool exhaustion detection** — real-time MySQL SHOW STATUS queries from inside VPC
5. **SSM health check** — diagnoses why SSM agent is unreachable
6. **Traffic Mirror-based capture** — passive capture without touching the instance (no tcpdump install needed)
7. **Integrated demo scenarios** — 7 purposely-broken scenarios (G–L + C) demonstrating each tool's value
8. **Two-phase evaluation framework** — demonstrates what agent can/cannot diagnose without tools vs with tools
9. **DevOps Agent native integration** — designed specifically for the AWS DevOps Agent

## Tool-by-Tool Comparison

### Packet Capture

| Capability | Their Tool | Our Tool |
|-----------|-----------|----------|
| Start capture | `start_packet_capture` (tshark on local/Lambda) | `start_capture` (VPC Traffic Mirror) |
| Stop capture | `stop_packet_capture` | `stop_capture` |
| List captures | `list_captured_files` | `list_captures` |
| Capture status | `get_capture_status` | `get_capture_progress` |

### Protocol Analysis

| Capability | Their Tool | Our Tool |
|-----------|-----------|----------|
| General analysis | `analyze_pcap_file` | `query_pcap` (SQL against Parquet) |
| HTTP extraction | `extract_http_requests` | `query_pcap` with SQL filter |
| TCP retransmissions | `analyze_tcp_retransmissions` | `detect_retransmissions` |
| TLS handshakes | `analyze_tls_handshakes` | `check_tls_hello_size` |
| TCP streams | — | `correlate_tcp_streams` |
| Fragmentation | — | `search_fragmented_packets` |
| Traffic stats | `generate_traffic_timeline` | `get_conversation_stats` |

### Network Diagnostics (We have, they don't)

| Tool | Purpose |
|------|---------|
| `tcp_traceroute` | TCP traceroute from EC2 to destination |
| `tls_traceroute` | TLS traceroute with SNI validation |
| `dns_resolve` | DNS resolution from inside VPC |
| `db_connectivity_probe` | 6-layer RDS troubleshooting |
| `agentic_reachability_analyze` | VPC Reachability Analyzer |
| `ssm_health_check` | SSM agent connectivity check |

### Deep Protocol Analysis (They have, we don't)

| Tool | Purpose |
|------|---------|
| `analyze_tls_alerts` | TLS alert message analysis |
| `extract_tls_cipher_analysis` | Cipher suite negotiation issues |
| `analyze_tcp_zero_window` | Flow control issues |
| `analyze_tcp_window_scaling` | Window scaling mechanisms |
| `analyze_congestion_indicators` | Network congestion metrics |
| `analyze_dns_resolution_issues` | DNS query patterns |
| `analyze_security_threats` | Threat detection |
| `analyze_network_topology` | Routing topology |
| `analyze_protocol_anomalies` | Malformed packets |
| `analyze_sni_mismatches` | SNI/certificate correlation |

## Complementary Value

The two projects are **complementary**, not competing:

- **Their strength**: Deep offline protocol analysis (post-incident forensics, deep-dive into pcap files)
- **Our strength**: Live remote diagnostics (real-time troubleshooting from the application's perspective)

A combined workflow would look like:
1. User reports connectivity issue → DevOps Agent invokes our `tcp_traceroute` / `db_connectivity_probe`
2. Need deeper analysis → Our `start_capture` collects traffic via mirror
3. Deep protocol analysis → Their tshark-based tools dissect the captured pcap

## Architecture Differences

### Their Architecture (AgentCore Gateway)
```
User → AgentCore Gateway (OAuth2) → Lambda (tshark layer) → S3 (pcap files)
```

### Our Architecture (DevOps Agent MCP)
```
User → DevOps Agent → API Gateway (SigV4) → Lambda → SSM → EC2 instances (live probes)
                                                     → S3 → Parquet → SQL queries
```

## Summary

| | sample-pcap-analyzer-mcp | GOAT Network Agent |
|---|---|---|
| **Think of it as** | "Wireshark as MCP" | "Remote network diagnostics platform" |
| **Best for** | Post-incident pcap analysis | Live troubleshooting from app perspective |
| **Unique value** | Deep protocol dissection | Real-time probing + connection pool diagnosis |
| **Deployment** | Local stdio or AgentCore | DevOps Agent SigV4 integration |
