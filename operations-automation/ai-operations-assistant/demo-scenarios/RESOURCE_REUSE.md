# Resource Reuse Documentation

This document describes, per network troubleshooting demo scenario (G–L), which existing resources are reused from the shared GOAT VPC, Scenario A, and Scenario C, and which new resources are created specifically for each scenario's misconfiguration.

## Scenario G — Inter-Tier Connectivity Failure (`agentic_reachability_analyze`)

**Reused resources:**
- Shared GOAT VPC (`GOATNetworkAgentVpcId` CloudFormation export)

**New resources created:**
- `subnet-a` and `subnet-b` (two subnets in the shared VPC)
- `app-tier-01` EC2 instance (inter-tier target)
- NACL with a buried deny rule at a non-obvious rule number

## Scenario H — External Endpoint Unreachable (`tcp_traceroute`)

**Reused resources:**
- Shared GOAT VPC (`GOATNetworkAgentVpcId` CloudFormation export)
- Transit Gateway from Scenario C (`GOATDemoScenarioCTransitGatewayId` export)

**New resources created:**
- `svc-alpha` EC2 instance (tagged `goat-network-traceroute-allowed=true`)
- `subnet-c` subnet
- Route table with a `/32` blackhole route overlapping the default NAT route

## Scenario I — TLS Handshake Failure (`tls_traceroute`)

**Reused resources:**
- Shared GOAT VPC (`GOATNetworkAgentVpcId` CloudFormation export)
- Network Firewall inspection VPC from Scenario C (`GOATDemoScenarioCInspectionVpcId` export)

**New resources created:**
- Internal ALB (`svc-beta-alb`)
- Target group
- ACM certificate covering a domain mismatched with the demo SNI
- `subnet-d` subnet

## Scenario J — DNS Split-Horizon Failure (`dns_resolve`)

**Reused resources:**
- Shared GOAT VPC (`GOATNetworkAgentVpcId` CloudFormation export)

**New resources created:**
- Route 53 Resolver outbound endpoint
- Resolver rule forwarding a demo domain to a stale conditional forwarder
- `subnet-e` subnet

## Scenario K — Database Connection Timeout (`db_connectivity_probe`)

**Reused resources:**
- Shared GOAT VPC (`GOATNetworkAgentVpcId` CloudFormation export)
- `svc-alpha` EC2 instance (shared with Scenario H as app tier)

**New resources created:**
- `svc-data-01` RDS instance (smallest supported instance class)
- DB subnet group
- NACL with ephemeral-port (1024–65535) outbound deny rule

## Scenario L — SSM-Unreachable Instance (`ssm_health_check`)

**Reused resources:**
- Shared GOAT VPC (`GOATNetworkAgentVpcId` CloudFormation export)

**New resources created:**
- `subnet-f` subnet
- `subnet-a-host` EC2 instance (correct IAM profile, no traceroute opt-in tag)
- NACL blocking HTTPS (443) outbound to SSM VPC endpoint IPs
