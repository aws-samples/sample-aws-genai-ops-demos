"""
Rich tool descriptions for the six G.O.A.T. Network Agent diagnostic actions.

These string constants provide comprehensive guidance for AI agents (DevOps Agent,
GOAT Orchestration Agent, or custom integrations) on when and how to invoke each
diagnostic tool. They include parameter schemas, usage examples, conversational
prompts, decision-tree logic, follow-up investigation guidance, and documentation
of Reachability Analyzer invisible gaps.

The conversational intelligence lives here (in the tool descriptions consumed by
the calling agent), NOT in the Network Agent itself — the Network Agent remains a
stateless tool server.
"""

# ---------------------------------------------------------------------------
# Decision Tree — Diagnostic Workflow Sequencing
# ---------------------------------------------------------------------------

DIAGNOSTIC_DECISION_TREE = """
## Decision Tree: Diagnostic Workflow Sequencing

Use this decision tree to select the right tool(s) based on the user's problem
description. Work through the tree from top to bottom.

### Step 1: Pre-flight — SSM Health Check
WHEN the user's problem will require SSM-based diagnostics (traceroute, DNS,
db_connectivity_probe), ALWAYS start with `ssm_health_check` to confirm the
target instance is reachable via SSM.

- If `ssm_managed=false` → Stop. Help the user fix SSM prerequisites first.
- If `ping_status=ConnectionLost` → Warn. SSM agent is registered but offline.
- If `ping_status=Online` → Proceed to next step.

### Step 2: Static Configuration Analysis — Reachability Analyzer
WHEN the user suspects a routing, security group, NACL, or firewall rule issue:
- Use `agentic_reachability_analyze` to check the static network path.
- This identifies WHICH component blocks traffic (SG rule, NACL entry, route).
- Fast: no on-instance execution needed.

### Step 3: Runtime Path Verification — Traceroute
WHEN the configuration looks correct (RA says reachable) but the user still has
connectivity issues:
- Use `tcp_traceroute` to verify the actual packet path at runtime.
- Reveals transient issues: packet loss, latency spikes, asymmetric routing.
- Use `tls_traceroute` when TLS handshake failures are suspected.

### Step 4: DNS Investigation
WHEN the user reports "can't connect" but the hostname might resolve to the
wrong IP:
- Use `dns_resolve` to compare instance-side vs agent-side resolution.
- Detects split-horizon DNS, stale records, DHCP option set misconfiguration.

### Step 5: Database-Specific Connectivity
WHEN the user is troubleshooting application-to-database connectivity:
- Use `db_connectivity_probe` for comprehensive multi-layer diagnosis.
- Performs 6 diagnostic layers: DNS, instance state, network checks, connection
  test, connection pool status, and parameter group analysis.
- Identifies whether the issue is DNS, instance state, network-level, TLS-level,
  authentication, connection pool exhaustion, or parameter misconfiguration.
- ALWAYS use this tool when the user reports database connectivity problems,
  regardless of whether the suspected cause is network-level or application-level.

### Sequencing Rules
1. `ssm_health_check` → always first if SSM tools will be needed
2. `agentic_reachability_analyze` → use for static config analysis
3. `tcp_traceroute` / `tls_traceroute` → use for runtime verification
4. `dns_resolve` → use when DNS symptoms are reported
5. `db_connectivity_probe` → use for ALL database connectivity issues (network
   AND application-layer, including connection pool exhaustion)

### When to Combine Tools
- "App can't connect to database" → ssm_health_check → db_connectivity_probe
  → if TCP fails: agentic_reachability_analyze
- "Too many connections" / pool exhaustion → ssm_health_check →
  db_connectivity_probe (will detect pool saturation and recommend remediation)
- "RDS rejecting new connections" → ssm_health_check → db_connectivity_probe
  (checks instance state, pool status, and parameter group)
- "Intermittent timeouts to external endpoint" → ssm_health_check →
  tcp_traceroute → if path looks clear: dns_resolve
- "TLS errors connecting to internal service" → ssm_health_check →
  tls_traceroute → if TLS fails: check certificate/SNI mismatch
- "New deployment can't reach anything" → agentic_reachability_analyze first
  (no SSM needed) → identify blocking rule
"""

# ---------------------------------------------------------------------------
# Conversational Guidance — Expertise-Level Adaptation
# ---------------------------------------------------------------------------

CONVERSATIONAL_GUIDANCE = """
## Conversational Investigation Guidance

### Adapting to User Expertise Level

**Expert user signals** (provide instance IDs, ports, specific error messages):
- Skip basic questions and proceed directly to tool invocation.
- Explain findings concisely with resource IDs and specific fixes.

**Intermediate user signals** (mention service names, describe symptoms clearly):
- Ask 1-2 targeted questions to gather instance ID and destination.
- Explain what each tool checks before invoking.

**Novice user signals** (vague descriptions like "app is broken", "can't connect"):
- Ask clarifying questions to understand the problem space.
- Explain the multi-step plan before executing.
- Use plain language for results ("The firewall is blocking your traffic on
  port 5432" rather than "NACL rule 110 denies ingress TCP/5432").

### Progressive Result Presentation

1. **Before invoking**: Explain WHAT you're about to check and WHY.
   Example: "Let me first verify your instance's SSM agent is healthy, since
   we'll need it to run diagnostics from the instance itself."

2. **After each result**: Share findings immediately in plain language.
   Example: "Good news — the network path is clear at layers 3 and 4. The
   issue might be at the application layer. Want me to check the TLS handshake?"

3. **Multi-step plan**: When multiple tools are needed, explain upfront.
   Example: "To diagnose this, I'll need to: 1) check SSM agent health,
   2) test the network path, and 3) probe the database connection. This will
   take about 2-3 minutes. Ready to proceed?"

4. **Final summary**: Structure as:
   - What was checked (tools used, resources tested)
   - What was found (specific findings per tool)
   - Root cause explanation (in plain language)
   - Recommended fix (specific resource ID + configuration change)

### Handling Tool Failures Gracefully

- **Missing opt-in tag**: "Your instance needs the tag
  `goat-network-traceroute-allowed=true` to allow diagnostics. Would you like
  me to explain how to add it, or would you prefer to check the network path
  using Reachability Analyzer instead (which doesn't need the tag)?"

- **SSM not available**: "The instance isn't reachable via SSM. This could mean
  the SSM agent isn't installed, or the instance is in a private subnet without
  VPC endpoints for SSM. Want me to check the instance's network configuration?"

- **Concurrency limit**: "There's already a diagnostic running on that instance.
  SSM-based tools are limited to one per instance. Let me check the static
  network path with Reachability Analyzer while we wait — it doesn't use SSM."
"""

# ---------------------------------------------------------------------------
# Tool: ssm_health_check
# ---------------------------------------------------------------------------

SSM_HEALTH_CHECK_DESCRIPTION = """
## Tool: ssm_health_check

### Purpose
Verify whether an EC2 instance's SSM agent is healthy and reachable before
attempting to run SSM-based diagnostic scripts (traceroute, DNS resolve,
db_connectivity_probe). This is a pre-flight check — use it FIRST when
SSM-based tools will be needed.

### When to Use
- ALWAYS before running any SSM-based diagnostic tool on a new instance.
- When the user reports "SSM commands fail" or "can't connect via Session Manager".
- When another SSM-based tool returns an SSM execution error.
- To distinguish SSM connectivity issues from actual network problems.

### When NOT to Use
- For `agentic_reachability_analyze` (API-only, no SSM needed).
- If you've already confirmed SSM health for this instance in the current session.

### Parameter Schema
| Parameter    | Type   | Required | Constraints                    | Default |
|-------------|--------|----------|--------------------------------|---------|
| instance_id | string | Yes      | Pattern: `^i-[0-9a-f]{8,17}$` | —       |

### Usage Examples

```json
{
  "action": "ssm_health_check",
  "params": {
    "instance_id": "i-0abc123def456789a"
  }
}
```

### Response Interpretation

**SSM Managed + Online** → Instance is ready for SSM-based diagnostics.
**SSM Managed + ConnectionLost** → Agent registered but not responding. Check:
  - Is the instance running?
  - Are VPC endpoints for SSM accessible from the instance's subnet?
  - Is the SSM agent process running on the instance?
**Not SSM Managed** → The `diagnostic_hints` array suggests possible causes:
  - SSM agent not installed
  - Agent stopped or crashed
  - Missing VPC endpoints for private subnets
  - Instance profile missing `AmazonSSMManagedInstanceCore` policy

### Conversational Prompts

Before invoking, ask if needed:
- "Which instance are you trying to run diagnostics on?"
- "Do you have the instance ID? It starts with `i-` followed by hex characters."

After results, follow up:
- If Online: "SSM agent is healthy. Ready to run network diagnostics."
- If ConnectionLost: "The SSM agent was last seen at [time]. The instance may
  be stopped or have lost network connectivity to SSM endpoints."
- If not managed: "This instance isn't registered with SSM. [Present hints].
  Would you like help resolving this?"

### Notes
- No opt-in tag required (API-only, read-only operation).
- Does not count toward SSM concurrency limit.
- Does not execute anything on the instance.
"""

# ---------------------------------------------------------------------------
# Tool: agentic_reachability_analyze
# ---------------------------------------------------------------------------

AGENTIC_REACHABILITY_ANALYZE_DESCRIPTION = """
## Tool: agentic_reachability_analyze

### Purpose
Analyze whether network traffic can flow between two VPC resources by evaluating
the static network configuration (security groups, NACLs, route tables, firewalls).
Identifies the SPECIFIC blocking component when traffic cannot reach the destination.

### When to Use
- When the user suspects a security group, NACL, route table, or firewall rule
  is blocking traffic.
- To quickly identify which component in the path blocks connectivity.
- When you need to verify the network path WITHOUT running anything on instances.
- As a first-pass diagnostic before resorting to runtime tools (traceroute).

### When NOT to Use
- For transient/intermittent issues (use `tcp_traceroute` instead — RA is static).
- For latency measurement (RA doesn't measure timing, only reachability).
- For DNS issues (RA doesn't analyze DNS resolution).
- For IPv6 paths (RA supports IPv4 only).
- For cross-region paths (source and destination must be same region).
- For application-layer issues (TLS, HTTP, database auth).

### Scope and Resource Resolution Responsibility

This tool is fully generic and topology-agnostic — it contains no logic specific
to any particular demo scenario, customer topology, or predefined resource
layout, and works identically for any VPC configuration.

This tool accepts ONLY the native Reachability Analyzer resource types: instance,
ENI, internet gateway, transit gateway, transit gateway attachment, VPC endpoint,
VPC endpoint service, VPC peering connection, and VPN gateway (plus an IPv4
address as a destination-only value). It does NOT accept higher-level service
identifiers such as RDS DB instance names, ALB/NLB ARNs, Lambda function names,
or ECS task IDs. Resolving those higher-level identifiers down to their
underlying ENI (for example, via `rds:DescribeDBInstances` or
`elasticloadbalancingv2:DescribeLoadBalancers` to find the associated ENIs) is
the responsibility of the CALLING agent (DevOps Agent or GOAT orchestration
agent), not this tool. Ask the user for or resolve the underlying resource ID
before invoking this action if they only provide a service name.

### Parameter Schema
| Parameter        | Type   | Required | Constraints                                        | Default |
|-----------------|--------|----------|----------------------------------------------------|---------|
| source          | string | Yes      | VPC resource ID only (NOT IPv4). See formats below | —       |
| destination     | string | Yes      | VPC resource ID OR IPv4 address                    | —       |
| destination_port| int    | No       | Range: 1–65535                                     | 443     |
| protocol        | string | No       | `tcp` or `udp`                                     | `tcp`   |

**Valid source formats** (VPC resource IDs only — IPv4 NOT accepted as source):
- Instance: `i-0abc123def456789a`
- ENI: `eni-0abc123def456789a`
- Internet Gateway: `igw-0abc123def456789a`
- Transit Gateway: `tgw-0abc123def456789ab`
- TGW Attachment: `tgw-attach-0abc123def456789ab`
- VPC Endpoint: `vpce-0abc123def456789a`
- VPC Endpoint Service: `vpce-svc-0abc123def456789ab`
- VPC Peering: `pcx-0abc123def456789a`
- VPN Gateway: `vgw-0abc123def456789a`

**Valid destination formats** (VPC resource IDs + IPv4 addresses):
- All source formats above, PLUS:
- IPv4 address: `10.0.1.100`, `192.168.1.1`

### Usage Examples

```json
{
  "action": "agentic_reachability_analyze",
  "params": {
    "source": "i-0abc123def456789a",
    "destination": "i-0def456abc789012b",
    "destination_port": 5432,
    "protocol": "tcp"
  }
}
```

```json
{
  "action": "agentic_reachability_analyze",
  "params": {
    "source": "i-0abc123def456789a",
    "destination": "10.0.2.50",
    "destination_port": 443
  }
}
```

### Response Interpretation

**Reachable** (`reachable=true`):
- `path_components`: ordered list of components the traffic traverses.
- `limitations`: any RA platform limitations encountered (see below).
- Important: reachable at L3/L4 does NOT mean the application works. Check for
  RA-invisible gaps (see Follow-up Investigation Guidance).

**Not Reachable** (`reachable=false`):
- `blocking_component`: the specific component that blocks traffic.
  - `type`: security_group, network_acl, route_table, transit_gateway_route_table,
    network_firewall_rule, vpc_endpoint_policy, prefix_list, vpc_peering_connection
  - `resource_id`: the blocking resource's ID
  - `rule`: the specific rule/entry causing the block
- `explanation`: human-readable diagnosis.
- `remediation`: specific configuration change needed to fix.
- `limitations`: any RA platform limitations encountered.

### Conversational Prompts

Before invoking, ask if needed:
- "What's the source resource? I need the instance ID, ENI ID, or other VPC
  resource ID."
- "What's the destination? This can be another VPC resource ID or an IP address."
- "What port is the application using? (Default is 443/HTTPS)"
- "Is this TCP or UDP traffic?"

After results:
- If reachable: "The network path is clear at layers 3 and 4. If you're still
  having issues, it may be application-layer (TLS, DNS, or service config).
  Want me to check further?"
- If blocked: "I found the blocking component: [type] [resource_id] is denying
  traffic because of [rule]. To fix this, [remediation]. Want me to verify
  there are no other issues in the path?"

### Direct Connect Path Handling

**IMPORTANT**: Direct Connect virtual interfaces (`dxvif`) are NOT a native
Reachability Analyzer resource type. To analyze a path that traverses Direct
Connect:

1. Resolve the Direct Connect path to its associated VPN Gateway (`vgw-`) or
   Transit Gateway Attachment (`tgw-attach-`) BEFORE invoking this tool.
2. Use the VGW or TGW attachment ID as the source or destination parameter.
3. Understand the analysis boundary: RA analyzes the VPC-side path only —
   it CANNOT verify the physical DX connection, BGP peering, on-premises
   routing, VLAN tagging, or MACsec encryption.

When the result shows `reachable=true` to a VGW/TGW that terminates Direct
Connect virtual interfaces, proactively inform the user:
"The AWS-side path to the gateway is clear. However, Reachability Analyzer
cannot verify the Direct Connect physical layer, BGP peering, or on-premises
routing. Would you like me to check the Direct Connect virtual interface status
and BGP session health?"

Then call these APIs for DX-side verification:
- `directconnect:DescribeVirtualInterfaces` — vif state, VLAN, BGP ASN
- `directconnect:DescribeConnections` — connection state, bandwidth, MACsec
- `directconnect:DescribeLags` — LAG membership and member link health
- `directconnect:DescribeDirectConnectGatewayAssociations` — association state
- `directconnect:DescribeDirectConnectGatewayAttachments` — attachment state

### Reachability Analyzer Platform Limitations

The following limitations MUST be communicated to the user when relevant:

| Limitation | Impact |
|-----------|--------|
| **IPv4 only** | No IPv6 path analysis. For IPv6, use `tcp_traceroute` or manual SG/RT inspection. |
| **Same-region only** | Source and destination must be in the same AWS region. |
| **TGW Connect attachments** | RA analyzes to the Connect attachment but cannot verify the GRE tunnel or BGP session over the Connect peer. |
| **GWLB endpoint paths** | RA excludes the Gateway Load Balancer and its targets from path analysis. |
| **Network Firewall resource groups** | Rule groups referencing tag-based resource groups cause analysis failure. |
| **Network Firewall advanced rules** | Suricata rules, domain lists, TLS inspection, and rule options are NOT evaluated. |
| **Target health** | Load balancer target registration and health status not considered. |
| **BYOIP** | BYOIP address range advertisement state not considered. |

### Multi-Agent Orchestration Pattern

This tool returns structured `path_components` data (component type, resource
ID, availability zone, evaluation result) with enough detail for the calling
agent to perform targeted follow-up investigation on any intermediate
component. The Network Agent performs ONLY the path analysis — it does NOT
inspect the configuration of intermediate components (it does not call
`DescribeSecurityGroupRules`, `DescribeNetworkAcls`, `DescribeRuleGroup`, etc.
itself). It reports only the resource IDs and evaluation outcomes returned
directly by the Reachability Analyzer API. Detailed configuration inspection
is always delegated to the calling agent, which already holds the necessary
`Describe*`/read permissions.

This tool supports two identical consumer paths with the same inputs/outputs:
(a) **GOAT console path** — the GOAT orchestration agent calls this tool for
path analysis, then delegates configuration inspection to the DevOps Agent;
(b) **DevOps Agent direct path** — the DevOps Agent calls this tool directly
for path analysis, then performs configuration inspection itself using its own
permissions. Do not assume one calling pattern over the other — the tool
behaves identically either way.

### Follow-up Investigation Guidance

When `path_components` includes intermediate components, the Reachability Analyzer
CANNOT detect many real-world issues. For EACH component type in the path, use
the following APIs to check for RA-invisible gaps.

**Investigation priority**: WHEN `reachable=true` at layer 3/4 but the user still
reports connectivity issues, systematically walk through EACH component in
`path_components` against the gap categories below, prioritizing in this order:
(1) Network Firewall, (2) Load Balancers, (3) VPC Endpoints, (4) DNS/Resolution,
then the remaining component types as relevant to the path.

#### Network Firewall
APIs: `network-firewall:DescribeRuleGroup`, `network-firewall:DescribeFirewallPolicy`,
`network-firewall:DescribeTLSInspectionConfiguration`

RA-invisible gaps:
- Suricata rule-based blocking (IPS signatures, content matching, flow keywords)
- Domain allow/deny lists (FQDN filtering)
- TLS inspection policies and certificate issues
- Rule options and advanced Suricata syntax
- Tag-based resource group references in rule groups (causes RA failure)
- TLS ClientHello fragmentation blocking by stateful rules
- SNI-based domain filtering

#### Load Balancers
APIs: `elasticloadbalancingv2:DescribeTargetHealth`, `elasticloadbalancingv2:DescribeRules`,
`elasticloadbalancingv2:DescribeListenerCertificates`, `wafv2:GetWebACL`

RA-invisible gaps:
- Target group health status (unhealthy, draining, unused)
- ALB listener rule routing (host-based, path-based conditions)
- ALB WAF ACL rules blocking request patterns
- NLB cross-zone load balancing misconfiguration
- TLS certificate mismatch on HTTPS listeners (subject vs SNI)
- ALB/NLB idle timeout vs backend keep-alive mismatch
- Gateway Load Balancer targets (RA excludes GWLB targets entirely)

#### Transit Gateway
APIs: `ec2:DescribeTransitGatewayRouteTables`, `ec2:DescribeTransitGatewayAttachments`,
`ec2:SearchTransitGatewayRoutes`, `ec2:DescribeTransitGatewayConnectPeers`

RA-invisible gaps:
- Connect attachments (RA does not support them at all)
- Blackhole routes not caught by RA's shortest-path logic
- Inter-region peering attachment status and route propagation
- Appliance mode routing asymmetry
- TGW attachment association to wrong route table

#### VPN Gateway
APIs: `ec2:DescribeVpnConnections`

RA-invisible gaps:
- VPN tunnel status (UP/DOWN per tunnel)
- BGP session state and route count (propagated vs expected)
- Dead peer detection timeouts
- VPN MTU limitations (1500 vs 1400 byte jumbo frame issues)

#### Direct Connect
APIs: `directconnect:DescribeVirtualInterfaces` (vif state, VLAN, BGP ASN,
BGP peer status, advertised/received prefix counts),
`directconnect:DescribeConnections` (connection state, bandwidth, LAG
membership, MACsec key association), `directconnect:DescribeLags` (LAG
member link health), `directconnect:DescribeDirectConnectGatewayAssociations`
(DX gateway ↔ VGW/TGW association state and allowed prefixes),
`directconnect:DescribeDirectConnectGatewayAttachments` (attachment state)

RA-invisible gaps:
- Direct Connect connection state (`available` vs `down`, `rejected`,
  `deleted`, `ordering`) and bandwidth allocation
- Direct Connect virtual interface state and VLAN tag correctness
- Direct Connect BGP peer status and advertised/received prefix count
- MACsec encryption negotiation failures and key association state
- Direct Connect Gateway association state to the VGW/TGW
- LAG membership and member link health (when the connection is part of a LAG)

**Note**: Reachability Analyzer only analyzes the VPC-side path to the VPN
Gateway or Transit Gateway attachment that terminates a Direct Connect virtual
interface — it does NOT verify the DX physical connection, on-premises router
configuration, or any state on the customer side of the link. See "Direct
Connect Path Handling" above for the full analysis-boundary explanation and
the proactive user messaging to use when a path reaches a DX-terminating VGW/TGW.

#### NAT Gateway
APIs: `ec2:DescribeNatGateways`, CloudWatch `aws/NATGateway` metrics
(`ErrorPortAllocation`, `ConnectionEstablishedCount`)

RA-invisible gaps:
- Connection tracking table exhaustion (`ErrorPortAllocation` metric)
- Elastic IP association state (disassociated EIP = NAT fails)
- NAT gateway state (not `available` = traffic drops)
- Idle connection timeout (350s) causing dropped long-lived connections

#### VPC Endpoints
APIs: `ec2:DescribeVpcEndpoints`, `ec2:DescribeVpcEndpointConnections`

RA-invisible gaps:
- Endpoint policy JSON denying specific actions, resources, or principals
- Endpoint connection state (`pending-acceptance`, `rejected`)
- PrivateLink service permissions (allowed principals list)
- DNS private zone association missing (endpoint DNS not resolving)

#### VPC Peering
APIs: `ec2:DescribeVpcPeeringConnections`, `ec2:DescribeRouteTables`

RA-invisible gaps:
- Route table entries on the remote side pointing to wrong CIDR
- DNS resolution setting disabled (private hostnames not resolving across peering)
- CIDR overlap preventing route table entry creation
- Peering connection in `pending-acceptance` or `failed` state

#### Security Groups
APIs: `ec2:DescribeSecurityGroupRules`

RA-invisible gaps:
- SG allows TCP but application uses UDP (RA reports reachable for TCP)
- SG referencing another SG in a peered VPC without `allow remote` setting
- Stale security group rules referencing deleted peered VPC SGs

#### Network ACLs
APIs: `ec2:DescribeNetworkAcls`

RA-invisible gaps:
- Ephemeral port range blocking on outbound rules (return traffic for stateless NACL)
- Rule ordering where a broader allow precedes a specific deny

#### Route Tables
APIs: `ec2:DescribeRouteTables`

RA-invisible gaps:
- Blackhole routes from deleted peering/TGW attachments
- Most-specific-match (/32) overriding broader routes

#### DNS / Resolution
APIs: `route53resolver:ListResolverRules`, `ec2:DescribeDhcpOptions`,
`route53:ListHostedZonesByVPC`

RA-invisible gaps:
- Route 53 Resolver rules forwarding to unreachable on-prem DNS
- DHCP option set pointing to non-functional DNS
- Private hosted zone not associated with VPC
- Split-horizon DNS returning incorrect IPs

#### MTU and Fragmentation
RA-invisible gaps:
- Path MTU discovery failures (ICMP "need to fragment" blocked by NACLs)
- Jumbo frame (9001 bytes) through VPN/TGW supporting only 1500/8500
- TCP MSS clamping not applied on VPN connections

#### General Runtime State
APIs: `ec2:DescribeInstances`

RA-invisible gaps:
- Instance or ENI in `shutting-down` or `stopped` state
- Elastic IP reassigned to different instance
- Auto-assigned public IP released after instance stop/start
"""

# ---------------------------------------------------------------------------
# Tool: tcp_traceroute
# ---------------------------------------------------------------------------

TCP_TRACEROUTE_DESCRIPTION = """
## Tool: tcp_traceroute

### Purpose
Execute a TCP traceroute from a target EC2 instance to a remote destination,
revealing the hop-by-hop network path and identifying where connectivity failures
or latency spikes occur. Uses TCP SYN probes with incrementing TTL values.

### When to Use
- To verify the ACTUAL runtime packet path (not just static config).
- When `agentic_reachability_analyze` shows reachable but the user still has issues.
- To detect transient problems: packet loss, latency spikes, asymmetric routing.
- To identify WHERE in the path packets are being dropped.
- To confirm NAT gateway, transit gateway, or firewall path traversal at runtime.

### When NOT to Use
- For static configuration analysis (use `agentic_reachability_analyze` instead).
- When the instance doesn't have SSM agent (check with `ssm_health_check` first).
- For TLS/certificate issues (use `tls_traceroute` instead).
- For DNS resolution issues (use `dns_resolve` instead).

### Parameter Schema
| Parameter        | Type   | Required | Constraints                        | Default |
|-----------------|--------|----------|------------------------------------|---------|
| instance_id     | string | Yes      | Pattern: `^i-[0-9a-f]{8,17}$`     | —       |
| destination_host| string | Yes      | 1–253 characters, hostname or IPv4 | —       |
| destination_port| int    | No       | Range: 1–65535                     | 443     |
| max_hops        | int    | No       | Range: 1–30                        | 30      |
| probe_timeout   | int    | No       | Range: 1–5 (seconds)               | 2       |

### Usage Examples

```json
{
  "action": "tcp_traceroute",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "destination_host": "api.example.com",
    "destination_port": 443
  }
}
```

```json
{
  "action": "tcp_traceroute",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "destination_host": "10.0.2.50",
    "destination_port": 5432,
    "max_hops": 15,
    "probe_timeout": 3
  }
}
```

### Response Interpretation

- **destination_reached=true**: Full path visible, destination responded.
  - SYN-ACK → port is open and accepting connections.
  - RST → port is closed (destination reached but service not listening).
- **destination_reached=false**: Packets die somewhere in the path.
  - Look at the last responding hop to identify the boundary.
  - Consecutive `*` hops indicate a firewall or NACL dropping packets silently.
- **Hops with high RTT**: Latency spike at that hop (congestion, geographic distance).
- **Hops with `*`**: That device doesn't send ICMP Time Exceeded (normal for some routers).

### Conversational Prompts

Before invoking:
- "Which instance should I run the traceroute FROM?"
- "What destination are you trying to reach? (hostname or IP address)"
- "What port does the service use? (Default is 443/HTTPS)"

After results:
- If destination reached: "The path has [N] hops and the destination responded.
  Network connectivity looks good at the TCP level."
- If destination not reached: "Packets are being dropped after hop [N] ([IP]).
  This suggests [a firewall/NACL/routing issue] between [last responding hop]
  and the destination."
- If port closed (RST): "The destination is reachable but port [port] is closed.
  The service might not be running or listening on that port."

### Prerequisites
- Instance must have SSM agent running (`ssm_health_check` first).
- Instance must have tag `goat-network-traceroute-allowed=true`.
- Instance must be Linux (Windows not supported).
- Counts toward SSM concurrency limit (3 global, 1 per-instance).

### Notes
- Executes a Python script on the instance via SSM Run Command.
- Uses raw TCP SYN probes (requires CAP_NET_RAW — available on EC2 Linux).
- No packages installed, no artifacts left behind (EXIT trap cleanup).
- Typical execution time: 10–60 seconds depending on hop count and timeouts.
"""

# ---------------------------------------------------------------------------
# Tool: tls_traceroute
# ---------------------------------------------------------------------------

TLS_TRACEROUTE_DESCRIPTION = """
## Tool: tls_traceroute

### Purpose
Trace the TCP path to a destination AND validate the TLS handshake at the
destination. Determines whether a failure is at the network layer (routing,
firewall) or at the application layer (certificate error, TLS version mismatch,
middlebox interference).

### When to Use
- When the user reports TLS errors (certificate errors, handshake failures).
- To determine if a connectivity issue is network-level or TLS-level.
- When a middlebox (firewall, proxy) might be interfering with TLS.
- To verify certificate subject, issuer, and expiry from the client's perspective.
- When SNI mismatch is suspected (use `sni_override` to test different values).

### When NOT to Use
- For pure network path verification without TLS (use `tcp_traceroute`).
- For DNS issues (use `dns_resolve`).
- When destination port doesn't use TLS.
- For static config analysis (use `agentic_reachability_analyze`).

### Parameter Schema
| Parameter        | Type   | Required | Constraints                        | Default |
|-----------------|--------|----------|------------------------------------|---------|
| instance_id     | string | Yes      | Pattern: `^i-[0-9a-f]{8,17}$`     | —       |
| destination_host| string | Yes      | 1–253 characters, hostname or IPv4 | —       |
| destination_port| int    | No       | Range: 1–65535                     | 443     |
| max_hops        | int    | No       | Range: 1–30                        | 30      |
| probe_timeout   | int    | No       | Range: 1–5 (seconds)               | 2       |
| sni_override    | string | No       | 1–253 characters                   | —       |

### Usage Examples

```json
{
  "action": "tls_traceroute",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "destination_host": "internal-api.example.com",
    "destination_port": 443
  }
}
```

```json
{
  "action": "tls_traceroute",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "destination_host": "10.0.1.50",
    "destination_port": 8443,
    "sni_override": "api.example.com"
  }
}
```

### Response Interpretation

**TLS handshake_success=true**:
- `protocol_version`: negotiated TLS version (e.g., "TLSv1.3")
- `cipher_suite`: negotiated cipher
- `certificate_subject`: certificate CN/SAN — verify it matches expected hostname
- `certificate_issuer`: CA that issued the cert
- `certificate_not_after`: expiry date — flag if within 30 days
- `handshake_time_ms`: TLS handshake duration

**TLS handshake_success=false**:
- `error_type`: one of:
  - `certificate_verify_failed` → cert untrusted, expired, or wrong hostname
  - `handshake_timeout` → TLS negotiation took too long (middlebox interference?)
  - `protocol_error` → TLS version mismatch or cipher incompatibility
  - `connection_reset` → connection dropped during handshake (firewall blocking TLS?)
  - `unknown` → other error
- `error_detail`: specific error message (max 1024 chars)

**TLS skipped** (`tls=null`):
- `tls_skipped_reason=destination_unreachable`: TCP path blocked — fix network first.
- `tls_skipped_reason=dns_resolution_failed`: hostname can't be resolved.

### Conversational Prompts

Before invoking:
- "What service is having TLS issues? I need the hostname and port."
- "Is the client sending a specific SNI value that might differ from the hostname?"

After results:
- If TLS succeeds: "TLS handshake completed successfully. Certificate is issued
  to [subject] by [issuer], expires [date]. The connection looks healthy."
- If certificate_verify_failed: "The TLS handshake failed because [error_detail].
  The certificate subject is [subject] but the client is connecting to
  [destination_host]. This is likely an SNI/hostname mismatch."
- If destination unreachable: "The traceroute shows the destination isn't
  reachable at the TCP level. Let me check the network path with Reachability
  Analyzer."

### Prerequisites
Same as `tcp_traceroute` (SSM agent, opt-in tag, Linux, concurrency limit).
"""

# ---------------------------------------------------------------------------
# Tool: dns_resolve
# ---------------------------------------------------------------------------

DNS_RESOLVE_DESCRIPTION = """
## Tool: dns_resolve

### Purpose
Run DNS lookups from a target EC2 instance and compare the results against
agent-side resolution. Detects split-horizon DNS, stale records, misconfigured
DHCP option sets, and Route 53 Resolver forwarding issues.

### When to Use
- When the user reports "can connect by IP but not by hostname".
- When split-horizon DNS is suspected (instance resolves differently than expected).
- To verify Route 53 Resolver rule behavior from the instance's perspective.
- When DHCP option set DNS configuration might be incorrect.
- After a DNS change to verify propagation to the instance.

### When NOT to Use
- For network path issues (use `tcp_traceroute` or `agentic_reachability_analyze`).
- For TLS certificate issues (use `tls_traceroute`).
- For database connectivity (use `db_connectivity_probe`).

### Parameter Schema
| Parameter   | Type   | Required | Constraints                                    | Default |
|------------|--------|----------|------------------------------------------------|---------|
| instance_id| string | Yes      | Pattern: `^i-[0-9a-f]{8,17}$`                 | —       |
| hostname   | string | Yes      | 1–253 characters                               | —       |
| record_type| string | No       | One of: A, AAAA, CNAME, MX, TXT, SRV, PTR     | A       |

### Usage Examples

```json
{
  "action": "dns_resolve",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "hostname": "database.internal.example.com"
  }
}
```

```json
{
  "action": "dns_resolve",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "hostname": "api.partner.com",
    "record_type": "CNAME"
  }
}
```

### Response Interpretation

- **split_horizon_detected=true**: Instance resolves to a DIFFERENT IP than the
  agent. This means DNS answers differ depending on where you query from —
  common with Route 53 Resolver rules, conditional forwarders, or private hosted
  zones associated with specific VPCs.

- **split_horizon_detected=false**: Both resolve to the same IPs. DNS is consistent.

- **Instance resolution failed**: The instance can't resolve the hostname at all.
  Check the instance's DNS resolver address and DHCP option set.

Key fields:
- `resolver_address`: what DNS server the instance is using
- `instance_result`: IPs resolved from the instance
- `agent_result`: IPs resolved from the agent container

### Conversational Prompts

Before invoking:
- "What hostname are you trying to resolve?"
- "Which instance is having the DNS issue?"
- "What record type — A record (default), AAAA, CNAME, MX, or other?"

After results:
- If split-horizon: "I detected split-horizon DNS. The instance resolves
  [hostname] to [instance IPs] but the expected resolution is [agent IPs].
  This could be caused by a Route 53 Resolver rule or a conditional forwarder.
  Want me to investigate the resolver configuration?"
- If consistent: "DNS resolution is consistent — both resolve to [IPs]. The
  issue is likely not DNS-related. Want me to check the network path to that IP?"
- If instance fails: "The instance can't resolve [hostname]. Its DNS resolver
  is [address]. This could be a DHCP option set issue or the resolver might be
  unreachable."

### Prerequisites
Same as `tcp_traceroute` (SSM agent, opt-in tag, Linux, concurrency limit).
"""

# ---------------------------------------------------------------------------
# Tool: db_connectivity_probe
# ---------------------------------------------------------------------------

DB_CONNECTIVITY_PROBE_DESCRIPTION = """
## Tool: db_connectivity_probe

### Purpose
Comprehensive RDS troubleshooting diagnostic that performs multi-layer diagnosis
of database connectivity problems. Goes far beyond simple TCP/TLS checks to
diagnose network-level issues, connection pool exhaustion, authentication failures,
DNS resolution problems, parameter group misconfigurations, and instance state
issues. Returns a structured diagnostic report with categorized findings, enabling
correlation across multiple diagnosis layers to identify the root cause.

### When to Use
- When the user reports "application can't connect to the database" — regardless
  of whether the suspected cause is network-level or application-level.
- When connection pool exhaustion is suspected (e.g., "Too many connections"
  errors, MySQL error 1040, intermittent connection failures under load).
- When an RDS instance is rejecting new connections but appears healthy in the
  console.
- To determine WHICH layer is failing: DNS, instance state, network, connection
  pool, authentication, or parameter group misconfiguration.
- After confirming network path is clear (via RA or traceroute) but app still fails.
- To test connectivity to RDS, Aurora, or any MySQL/PostgreSQL-compatible endpoint.
- When the user asks to "diagnose connection pool issues" or "investigate why
  my RDS instance is rejecting connections".

### When NOT to Use
- For general network path issues not involving a database (use `tcp_traceroute`).
- For non-database services (use `tcp_traceroute` or `tls_traceroute`).
- When you only need DNS resolution (use `dns_resolve` for standalone DNS checks).

### Diagnostic Layers (6-Phase Analysis)

The tool performs these diagnostic phases sequentially:

| Phase | Layer                  | What It Checks                                          |
|-------|------------------------|---------------------------------------------------------|
| 1     | DNS Resolution         | Resolves endpoint hostname, reports IPs or failure      |
| 2     | Instance State         | DescribeDBInstances — checks RDS status (available,     |
|       |                        | stopped, storage-full, modifying, rebooting)            |
| 3     | Network Checks         | Security group rules, NACL rules, route table           |
|       |                        | verification for source → RDS path                      |
| 4     | Connection Test        | TCP connect → TLS handshake → protocol auth (sequential)|
| 5     | Connection Pool Status | SHOW STATUS LIKE 'Threads_connected', SHOW GLOBAL      |
|       |                        | VARIABLES LIKE 'max_connections', pool utilization %    |
| 6     | Parameter Group        | DescribeDBParameters — flags abnormally low             |
|       |                        | max_connections (< 50 for production instances)         |

Each phase runs independently. A failure in one phase does NOT prevent subsequent
phases from executing — the tool degrades gracefully and reports what it could
determine.

### Error Categorization Decision Tree

Use this decision tree to quickly identify the root cause category from the
tool's output or from error messages the user reports:

```
Error 1040 OR "Too many connections"
  └─→ ROOT CAUSE: Connection pool exhaustion
      • Check connection_pool_status in report
      • Look for Threads_connected ≈ max_connections
      • Remediation: increase max_connections, add RDS Proxy, reduce client concurrency

Timeout errors (connect_timeout, read_timeout)
  └─→ ROOT CAUSE: Network layer issue
      • Check network_checks in report
      • Follow up with agentic_reachability_analyze to find blocking rule
      • Remediation: fix SG/NACL/route table rules

"Connection refused" (errno 111, ECONNREFUSED)
  └─→ ROOT CAUSE: Instance not accepting connections
      • Check instance_state in report
      • Instance may be stopped, rebooting, or storage-full
      • Remediation: start instance, wait for reboot, resolve storage

"Access denied" (MySQL 1045, PostgreSQL 28P01)
  └─→ ROOT CAUSE: Authentication failure
      • Check connection_test.auth in report
      • Credentials may be wrong, user may not exist, or host not allowed
      • Remediation: verify credentials, check mysql.user / pg_hba.conf

DNS resolution failure (NXDOMAIN, SERVFAIL)
  └─→ ROOT CAUSE: DNS misconfiguration
      • Check dns_resolution in report
      • Endpoint hostname may be wrong or DNS unreachable
      • Remediation: verify endpoint spelling, check VPC DNS settings

Instance not in "available" state
  └─→ ROOT CAUSE: RDS instance state issue
      • Check instance_state in report
      • May be modifying, backing-up, storage-optimization, etc.
      • Remediation: wait for state transition or resolve underlying issue
```

### Parameter Schema
| Parameter   | Type   | Required | Constraints                    | Default |
|------------|--------|----------|--------------------------------|---------|
| instance_id| string | Yes      | Pattern: `^i-[0-9a-f]{8,17}$` | —       |
| endpoint   | string | Yes      | 1–253 characters               | —       |
| port       | int    | Yes      | Range: 1–65535                 | —       |
| engine     | string | No       | `mysql` or `postgresql`        | —       |

### Usage Examples

```json
{
  "action": "db_connectivity_probe",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "endpoint": "mydb.cluster-abc123.us-east-1.rds.amazonaws.com",
    "port": 5432,
    "engine": "postgresql"
  }
}
```

```json
{
  "action": "db_connectivity_probe",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "endpoint": "svc-data-01.comvupvqkrj2.us-east-1.rds.amazonaws.com",
    "port": 3306,
    "engine": "mysql"
  }
}
```

```json
{
  "action": "db_connectivity_probe",
  "params": {
    "instance_id": "i-0abc123def456789a",
    "endpoint": "10.0.3.50",
    "port": 3306,
    "engine": "mysql"
  }
}
```

### Response Interpretation

The diagnostic report contains six sections. Interpret each independently:

**dns_resolution** (Phase 1):
- `status: pass` + `resolved_ips`: hostname resolves correctly.
- `status: fail`: DNS cannot resolve the endpoint. Check VPC DNS settings,
  DHCP option sets, or endpoint spelling.

**instance_state** (Phase 2):
- `status: pass` + `db_instance_status: available`: RDS instance is healthy.
- `status: fail` + status not "available": instance is stopped, rebooting,
  storage-full, or in maintenance. Wait or resolve the underlying issue.
- `status: skipped`: could not call DescribeDBInstances (permissions issue).

**network_checks** (Phase 3):
- `status: pass`: security group, NACL, and route table all allow traffic.
- `status: fail`: identifies which component blocks traffic (SG rule, NACL
  entry, or missing route). Use `agentic_reachability_analyze` for details.
- `status: skipped`: could not evaluate network config (permissions).

**connection_test** (Phase 4):
- `tcp.connected=true`: port is open, network path clear.
- `tcp.connected=false`: network-level block or instance not listening.
- `tls.connected=true`: TLS negotiation successful.
- `auth.success=true`: database protocol responds correctly.

**connection_pool_status** (Phase 5) — Pool Health Assessment:

| Status      | Condition                            | Meaning                              |
|-------------|--------------------------------------|--------------------------------------|
| `healthy`   | utilization < 90%                    | Pool has capacity, no action needed  |
| `warning`   | 90% ≤ utilization < 100%            | Pool nearing capacity, monitor       |
| `exhausted` | utilization ≥ 100% OR error 1040    | Pool full, new connections rejected  |
| `unknown`   | Could not query pool metrics         | Connection failed before pool check  |

- `threads_connected`: current active connections to the database.
- `max_connections`: configured maximum from parameter group.
- `utilization_percent`: (threads_connected / max_connections) × 100.

**parameter_group_findings** (Phase 6):
- `status: ok`: no parameter issues detected.
- `status: warning` + `flagged_parameters`: identifies abnormally low settings.
  - `max_connections < 50` is flagged as abnormally low for production instances.
- `status: skipped`: could not retrieve parameter group info.

### Connection Pool Exhaustion — Detailed Interpretation

When `connection_pool_status.status` is `exhausted` or `warning`:

**Indicators of pool exhaustion:**
- `threads_connected` equals or exceeds `max_connections`
- Connection test fails with error 1040 ("Too many connections")
- `utilization_percent` ≥ 90%
- `parameter_group_findings` flags abnormally low `max_connections`

**Common causes:**
- Application connection pool size exceeds database `max_connections`
- Long-running queries holding connections open
- Connection leaks (connections not returned to pool)
- Deliberately restrictive parameter group (e.g., `max_connections=5`)
- Multiple application instances each opening their own pool

### Remediation Guidance — Connection Pool Exhaustion

When the tool identifies pool exhaustion as the root cause, present these
remediation options to the user (in priority order):

1. **Increase `max_connections` in the RDS parameter group**
   - Modify the custom parameter group to raise the limit.
   - Consider the instance class memory constraints (each connection consumes
     ~10-20 MB RAM depending on workload).
   - Apply changes: static parameters require reboot; dynamic ones apply
     immediately.

2. **Implement connection pooling with RDS Proxy**
   - RDS Proxy multiplexes application connections over fewer database connections.
   - Reduces connection overhead and handles connection reuse automatically.
   - Particularly effective for Lambda-based workloads with bursty connections.

3. **Reduce client-side concurrency**
   - Lower the application's connection pool max size.
   - Implement connection timeout and retry logic.
   - Add circuit breakers to prevent cascade failures during pool saturation.

4. **Identify and fix connection leaks**
   - Query `SHOW PROCESSLIST` to identify idle connections held too long.
   - Review application code for connections not properly closed/returned.
   - Set `wait_timeout` and `interactive_timeout` to reclaim idle connections.

### Conversational Prompts

Before invoking:
- "What's the database endpoint? (RDS hostname or IP address)"
- "What port? (MySQL default is 3306, PostgreSQL default is 5432)"
- "Is it MySQL or PostgreSQL?"
- "Which instance is the application connecting FROM?"
- "Are you seeing 'Too many connections' errors?" (suggests pool exhaustion)

After results — by root cause:

- **Pool exhaustion**: "The database connection pool is exhausted —
  [threads_connected] of [max_connections] connections are in use
  ([utilization]%). New connections are being rejected with error 1040. The
  parameter group has `max_connections` set to [value], which is abnormally low.
  I recommend: (1) increasing `max_connections` in the parameter group,
  (2) implementing RDS Proxy for connection pooling, or (3) reducing client
  concurrency. Want me to help with any of these?"

- **Network block**: "The instance can't reach the database port. This is a
  network issue — a security group, NACL, or route table is blocking traffic.
  Want me to run a reachability analysis to find the blocking rule?"

- **TLS failure**: "TCP connection works but TLS handshake failed: [error]. The
  database might require a specific CA certificate or TLS version."

- **Authentication failure**: "Network and TLS are fine but authentication
  failed. Check that the credentials are correct and the user has permissions
  to connect from this host."

- **Instance state issue**: "The RDS instance is currently in '[state]' status.
  It's not accepting connections until it returns to 'available'. [Explain what
  the state means and expected recovery time.]"

- **All phases pass**: "Full connectivity verified across all 6 diagnostic
  layers: DNS, instance state, network, connection, pool status, and parameter
  group. The issue might be application-level (specific query errors, schema
  permissions, connection string configuration)."

### Prerequisites
Same as `tcp_traceroute` (SSM agent, opt-in tag, Linux, concurrency limit).

### Notes
- The tool name remains `db_connectivity_probe` for backward compatibility with
  existing agent integrations and MCP tool calls.
- No new mandatory parameters — all enhanced functionality is additive.
- No database client libraries needed — uses raw socket protocol packets for
  connection test; uses boto3 (available on EC2) for AWS API calls.
- Each diagnostic phase runs independently with graceful degradation — if boto3
  calls fail due to permissions, those phases report "skipped" rather than
  failing the entire probe.
- When invoked against an endpoint with no connection pool issues, the tool
  still reports network-level findings correctly and does not error due to the
  absence of pool problems.
"""

# ---------------------------------------------------------------------------
# Consolidated exports for use by agent orchestration layer
# ---------------------------------------------------------------------------

TOOL_DESCRIPTIONS = {
    "ssm_health_check": SSM_HEALTH_CHECK_DESCRIPTION,
    "agentic_reachability_analyze": AGENTIC_REACHABILITY_ANALYZE_DESCRIPTION,
    "tcp_traceroute": TCP_TRACEROUTE_DESCRIPTION,
    "tls_traceroute": TLS_TRACEROUTE_DESCRIPTION,
    "dns_resolve": DNS_RESOLVE_DESCRIPTION,
    "db_connectivity_probe": DB_CONNECTIVITY_PROBE_DESCRIPTION,
}

# Full guidance documents (decision tree + conversational guidance)
AGENT_GUIDANCE = {
    "decision_tree": DIAGNOSTIC_DECISION_TREE,
    "conversational_guidance": CONVERSATIONAL_GUIDANCE,
}
