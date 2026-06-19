# DevOps Agent Network Diagnostics — Suggested Queries

These queries demonstrate how to use the GOAT chat interface to invoke network diagnostic capabilities through the DevOps Agent integration. Each query triggers the DevOps Agent Tool Interface, which proxies requests to the GOAT Network Agent for packet-level analysis.

## Query 1: TLS Handshake Fragmentation Diagnosis

**Query:**

```
My application connections to the internal API endpoint are timing out intermittently.
Reachability Analyzer shows the path is reachable, but clients get connection resets
during TLS negotiation. Can you run a full network diagnostic on the ENIs attached to
my application's load balancer to check for TLS handshake issues?
```

**What it demonstrates:**

- Invokes the `full_diagnostic` action with `analysis_focus: "tls"`
- Shows how DevOps Agent discovers ENIs, initiates a traffic capture, transforms the pcap, and runs TLS-specific analysis
- Produces a Diagnostic Report identifying oversized Client Hello packets being fragmented and dropped by a Network Firewall
- Highlights the value over Reachability Analyzer: L3/L4 path analysis shows "reachable" but L7 inspection reveals TLS fragmentation causing drops

**Expected output includes:**

- Client Hello size in bytes (e.g., 2,847 bytes exceeding typical MTU)
- Key exchange algorithm (e.g., TLS 1.3 with X25519)
- Fragmentation evidence (fragment count and sizes)
- Middlebox behavior (Network Firewall dropping fragmented TLS packets)
- Recommended action: reduce cipher suite list or enable TCP segmentation offload

---

## Query 2: Connectivity Drops and TCP Reset Analysis

**Query:**

```
We're seeing intermittent connection drops between our microservices in the private subnet.
The connections work fine for a few minutes then suddenly reset. Can you capture traffic on
ENI eni-0abc1234def56789a and analyze what's causing the TCP resets?
```

**What it demonstrates:**

- Invokes `full_diagnostic` with `analysis_focus: "tcp_health"` targeting a specific ENI
- Shows TCP RST origin analysis identifying whether resets come from the client, server, or an intermediate network appliance
- Correlates reset timing with connection idle timeouts on a NAT Gateway or load balancer

**Expected output includes:**

- TCP RST origin (source IP and classification: client / server / intermediate device)
- Timing data in milliseconds relative to connection initiation (e.g., resets occur at ~350,000 ms = 350s idle timeout)
- Network appliance correlation (e.g., NAT Gateway idle timeout at 350 seconds)
- Confidence level: "high" (3+ corroborating indicators: consistent timing, RST source matches NAT GW IP, no application-layer close)
- Recommended action: reduce application keep-alive interval below 350 seconds or switch to NAT instance with configurable timeout

---

## Query 3: General Network Latency and Retransmission Investigation

**Query:**

```
Our database queries are experiencing high latency spikes. The RDS instance CPU is low and
queries return fast locally, so we suspect a network issue between the application tier and
the database. Run a 5-minute capture on the application ENIs and check for retransmissions
or packet loss.
```

**What it demonstrates:**

- Invokes `full_diagnostic` with `duration_minutes: 5` and `analysis_focus: "general"`
- Shows the composite workflow: ENI discovery → capture (5 min) → transform → TCP stream analysis
- Detects retransmissions, out-of-order packets, or zero-window conditions causing latency
- Provides elapsed time per phase so the user sees workflow progress

**Expected output includes:**

- Summary of affected TCP streams between application and RDS
- Retransmission rate and out-of-order packet count
- RTT distribution showing latency spikes
- Root cause indicators (e.g., micro-bursting causing buffer overflow at the hypervisor level)
- Comparison with Reachability Analyzer: path is reachable at L3/L4, but L7 analysis reveals packet loss under load
- Recommended actions: enable enhanced networking, check placement group configuration, or scale horizontally to reduce per-instance throughput

---

## Query 4: DNS Resolution Failures (Bonus)

**Query:**

```
Some of our Lambda functions are failing with DNS resolution errors for our private hosted zone.
The VPC DNS settings look correct. Can you capture traffic on the Lambda ENI and check what's
happening with DNS queries?
```

**What it demonstrates:**

- Invokes `full_diagnostic` with `analysis_focus: "dns"` and short `duration_minutes: 2`
- Captures DNS query/response pairs and identifies failures (NXDOMAIN, timeouts, truncation)
- Shows that even when VPC configuration appears correct, packet-level inspection can reveal DNS response truncation or resolver overload

**Expected output includes:**

- DNS query patterns and response codes
- Timing between query and response (or timeout detection)
- Identification of truncated UDP responses requiring TCP fallback
- Data sufficiency warning if capture is too short to observe the intermittent failure
- Recommended action: check Route 53 Resolver query logging, verify DHCP option set, or investigate DNS rate limiting

---

## Usage Notes

- These queries can be typed directly into the GOAT chat interface
- The DevOps Agent will automatically select the appropriate Network Agent actions
- For best results, ensure Scenario C (TLS fragmentation) is deployed when testing TLS queries
- Capture duration defaults to 2 minutes if not specified; increase for intermittent issues
- The integration respects a maximum of 3 concurrent captures per account
