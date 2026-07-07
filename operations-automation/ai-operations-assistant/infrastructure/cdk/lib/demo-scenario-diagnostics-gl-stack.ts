import * as cdk from 'aws-cdk-lib';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as events from 'aws-cdk-lib/aws-events';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as route53resolver from 'aws-cdk-lib/aws-route53resolver';
import { Construct } from 'constructs';

/**
 * Props for the network troubleshooting diagnostics demo scenario stack (Scenarios G–L).
 *
 * Every prop is optional. Shared resources are resolved either via a direct
 * reference (used by tests and same-app cross-stack wiring) or via a
 * CloudFormation export name (used in standalone deployments), mirroring
 * the pattern established by `DemoScenarioTlsFragmentationStack`.
 */
export interface DemoScenarioDiagnosticsGLStackProps extends cdk.StackProps {
  /** Direct VPC reference (tests / same-app cross-stack ref). Takes priority over goatVpcExportName. */
  sharedVpc?: ec2.IVpc;
  /** CloudFormation export name for the GOAT network infra VPC ID (e.g., 'GOATNetworkAgentVpcId'). */
  goatVpcExportName?: string;
  /** Direct Transit Gateway ID reference (tests). Takes priority over the TGW export. */
  sharedTransitGatewayId?: string;
  /** CloudFormation export name for Scenario C's Transit Gateway ID (Scenario H route). */
  goatTgwExportName?: string;
  /** Direct Network Firewall endpoint reference (tests). Takes priority over the NFW export. */
  sharedFirewallEndpointId?: string;
  /** CloudFormation export name for Scenario C's Network Firewall inspection VPC ID (Scenario I inspection path). */
  goatFirewallEndpointExportName?: string;
}

/**
 * G.O.A.T. Demo Scenarios G–L — Network Troubleshooting Diagnostics Stack
 *
 * Provisions the misconfiguration scaffolding for six network troubleshooting demo
 * scenarios (baseline vs. tools-assisted diagnosis), maximizing reuse of
 * already-provisioned shared infrastructure:
 * - Shared GOAT VPC (imported via `GOATNetworkAgentVpcId`, same as
 *   `DemoScenarioTlsFragmentationStack`)
 * - Transit Gateway and Network Firewall provisioned by
 *   `DemoScenarioTlsFragmentationStack` (Scenario C), reused rather than
 *   duplicated for Scenario H (blackhole route) and Scenario I (TLS
 *   handshake failure)
 *
 * See `demo-scenarios/RESOURCE_REUSE.md` for the full per-scenario mapping
 * of (a) existing resources reused from Scenario A / Scenario C / the
 * shared VPC, and (b) new resources created specifically for each
 * scenario's misconfiguration.
 *
 * This is currently a skeleton: it resolves the shared VPC and validates
 * inputs. Per-scenario resources (Scenarios G–L) are added by subsequent
 * tasks.
 */
export class DemoScenarioDiagnosticsGLStack extends cdk.Stack {
  /** Shared VPC ID — resolved from sharedVpc prop or imported via goatVpcExportName. */
  public readonly sharedVpcId: string;

  /** Shared Transit Gateway ID — resolved from sharedTransitGatewayId prop or imported via goatTgwExportName, if provided. */
  public readonly sharedTransitGatewayId?: string;

  /** Shared Network Firewall inspection VPC ID — resolved from sharedFirewallEndpointId prop or imported via goatFirewallEndpointExportName, if provided. */
  public readonly sharedFirewallEndpointId?: string;

  // -----------------------------------------------------------------------
  // Scenario G references (agentic_reachability_analyze)
  // -----------------------------------------------------------------------

  /** Scenario G subnet-a (10.99.30.0/24) */
  public readonly scenarioGSubnetA: ec2.CfnSubnet;

  /** Scenario G subnet-b (10.99.31.0/24) — hosts app-tier-01 */
  public readonly scenarioGSubnetB: ec2.CfnSubnet;

  /** Scenario G NACL associated with subnet-b, carrying the buried deny rule */
  public readonly scenarioGNacl: ec2.CfnNetworkAcl;

  /** Scenario G app-tier-01 EC2 instance (inter-tier reachability target) */
  public readonly scenarioGAppTierInstance: ec2.CfnInstance;

  // -----------------------------------------------------------------------
  // Scenario I references (tls_traceroute)
  // -----------------------------------------------------------------------

  /** Scenario I subnet-d (10.99.33.0/24) — hosts the internal ALB */
  public readonly scenarioISubnetD: ec2.CfnSubnet;

  /** Scenario I second subnet for ALB (10.99.34.0/24) — ALBs require ≥2 AZs */
  public readonly scenarioISubnetE: ec2.CfnSubnet;

  /** Scenario I internal ALB (svc-beta-alb) with mismatched certificate */
  public readonly scenarioIAlb: elbv2.CfnLoadBalancer;

  /** Scenario I ACM certificate covering a domain different from the demo SNI */
  public readonly scenarioICertificate: cdk.CustomResource;

  /** Scenario I ACM certificate ARN (self-signed, imported into ACM) */
  public readonly scenarioICertArn: string;

  // -----------------------------------------------------------------------
  // Scenario H references (tcp_traceroute + agentic_reachability_analyze)
  // -----------------------------------------------------------------------

  /** Scenario H subnet-c (10.99.32.0/24) — hosts svc-alpha */
  public readonly scenarioHSubnetC: ec2.CfnSubnet;

  /** Scenario H svc-alpha EC2 instance (traceroute source, shared with Scenario K) */
  public readonly scenarioHSvcAlphaInstance: ec2.CfnInstance;

  /** Scenario H route table with /32 blackhole overlapping the NAT route */
  public readonly scenarioHRouteTable: ec2.CfnRouteTable;

  // -----------------------------------------------------------------------
  // Scenario L references (ssm_health_check)
  // -----------------------------------------------------------------------

  /** Scenario L subnet-f (10.99.35.0/24) — hosts subnet-a-host */
  public readonly scenarioLSubnetF: ec2.CfnSubnet;

  /** Scenario L subnet-a-host EC2 instance (SSM-unreachable due to NACL blocking 443 outbound) */
  public readonly scenarioLSubnetAHostInstance: ec2.CfnInstance;

  /** Scenario L NACL blocking HTTPS (443) outbound — isolates the instance from SSM VPC endpoints */
  public readonly scenarioLNacl: ec2.CfnNetworkAcl;

  // -----------------------------------------------------------------------
  // Scenario J references (dns_resolve)
  // -----------------------------------------------------------------------

  /** Scenario J subnet for Resolver outbound endpoint IP 1 (10.99.35.0/24) */
  public readonly scenarioJResolverSubnet1: ec2.CfnSubnet;

  /** Scenario J subnet for Resolver outbound endpoint IP 2 (10.99.36.0/24, different AZ) */
  public readonly scenarioJResolverSubnet2: ec2.CfnSubnet;

  /** Scenario J Route 53 Resolver outbound endpoint */
  public readonly scenarioJResolverOutbound: route53resolver.CfnResolverEndpoint;

  /** Scenario J Resolver rule forwarding a demo domain to a conditional forwarder */
  public readonly scenarioJResolverRule: route53resolver.CfnResolverRule;

  // -----------------------------------------------------------------------
  // Scenario K references (db_connectivity_probe + agentic_reachability_analyze)
  // -----------------------------------------------------------------------

  /** Scenario K DB subnet 1 (10.99.37.0/24, AZ-0) */
  public readonly scenarioKDbSubnet1: ec2.CfnSubnet;

  /** Scenario K DB subnet 2 (10.99.40.0/24, AZ-1) — required for multi-AZ DB subnet group */
  public readonly scenarioKDbSubnet2: ec2.CfnSubnet;

  /** Scenario K DB subnet group for svc-data-01 */
  public readonly scenarioKDbSubnetGroup: rds.CfnDBSubnetGroup;

  /** Scenario K svc-data-01 RDS instance (MySQL, db.t4g.micro) */
  public readonly scenarioKRdsInstance: rds.CfnDBInstance;



  constructor(scope: Construct, id: string, props?: DemoScenarioDiagnosticsGLStackProps) {
    super(scope, id, props);

    // Common tags for all resources in this stack
    cdk.Tags.of(this).add('goat-demo', 'true');
    cdk.Tags.of(this).add('goat-scenario', 'network-troubleshooting');
    cdk.Tags.of(this).add('auto-delete', 'no');

    // -----------------------------------------------------------------------
    // Shared VPC — Use sharedVpc prop, or import from CloudFormation export.
    // The VPC must be the GOAT network infra VPC so all six scenarios sit
    // in the same shared topology (Req 6.1).
    // -----------------------------------------------------------------------
    let sharedVpcId: string;

    if (props?.sharedVpc) {
      sharedVpcId = props.sharedVpc.vpcId;
    } else if (props?.goatVpcExportName) {
      sharedVpcId = cdk.Fn.importValue(props.goatVpcExportName);
    } else {
      sharedVpcId = cdk.Fn.importValue('GOATNetworkAgentVpcId');
    }

    this.sharedVpcId = sharedVpcId;

    // -----------------------------------------------------------------------
    // Shared Transit Gateway — Reused by Scenario H's blackhole route.
    // Resolved from a direct reference (tests) or the Scenario C export
    // added in task 7.1 (GOATDemoScenarioCTransitGatewayId).
    // -----------------------------------------------------------------------
    if (props?.sharedTransitGatewayId) {
      this.sharedTransitGatewayId = props.sharedTransitGatewayId;
    } else if (props?.goatTgwExportName) {
      this.sharedTransitGatewayId = cdk.Fn.importValue(props.goatTgwExportName);
    }

    // -----------------------------------------------------------------------
    // Shared Network Firewall inspection VPC — Reused by Scenario I's TLS
    // inspection path. Resolved from a direct reference (tests) or the
    // Scenario C export added in task 7.1 (GOATDemoScenarioCInspectionVpcId).
    // -----------------------------------------------------------------------
    if (props?.sharedFirewallEndpointId) {
      this.sharedFirewallEndpointId = props.sharedFirewallEndpointId;
    } else if (props?.goatFirewallEndpointExportName) {
      this.sharedFirewallEndpointId = cdk.Fn.importValue(props.goatFirewallEndpointExportName);
    }

    // Single AZ for all subnets in this scenario stack (matches the
    // single-AZ pattern used by DemoScenarioTlsFragmentationStack).
    const az = cdk.Fn.select(0, cdk.Fn.getAzs(''));

    // =========================================================================
    // Scenario G — agentic_reachability_analyze
    //
    // Inter-tier connectivity failure: two EC2 instances in different
    // subnets of the shared VPC, with a NACL deny rule for a specific port
    // buried at a non-obvious rule number among broader allow rules. A
    // manual security-group review alone shows the port as allowed, since
    // the security groups are permissive — only the NACL blocks it.
    // See demo-scenarios/RESOURCE_REUSE.md for the full reuse mapping.
    // =========================================================================

    // -----------------------------------------------------------------------
    // Scenario G — Subnets (subnet-a, subnet-b)
    // -----------------------------------------------------------------------
    this.scenarioGSubnetA = new ec2.CfnSubnet(this, 'ScenarioGSubnetA', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.30.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'subnet-a' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    this.scenarioGSubnetB = new ec2.CfnSubnet(this, 'ScenarioGSubnetB', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.31.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'subnet-b' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario G — Security group (permissive — the SG alone shows the
    // port as reachable; only the NACL below actually blocks it)
    // -----------------------------------------------------------------------
    const scenarioGSecurityGroup = new ec2.CfnSecurityGroup(this, 'ScenarioGAppTierSg', {
      groupDescription: 'Security group for app-tier-01',
      vpcId: sharedVpcId,
      securityGroupIngress: [
        {
          ipProtocol: 'tcp',
          fromPort: 0,
          toPort: 65535,
          cidrIp: '10.99.0.0/16',
        },
      ],
      securityGroupEgress: [
        {
          ipProtocol: '-1',
          cidrIp: '0.0.0.0/0',
        },
      ],
      tags: [
        { key: 'Name', value: 'app-tier-01-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario G — NACL for subnet-b with a deny rule buried among broader
    // allow rules. Rule 50 (deny, port 5432) is evaluated before rules 100,
    // 110, and 900 (broad allows) because NACL rules are evaluated in
    // ascending rule-number order and the first match wins — so the low
    // numbered deny silently overrides the broad allows that follow it.
    // -----------------------------------------------------------------------
    this.scenarioGNacl = new ec2.CfnNetworkAcl(this, 'ScenarioGNacl', {
      vpcId: sharedVpcId,
      tags: [
        { key: 'Name', value: 'subnet-b-nacl' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Rule 50 — narrow deny on the demo target port, evaluated first.
    new ec2.CfnNetworkAclEntry(this, 'ScenarioGNaclEntry50', {
      networkAclId: this.scenarioGNacl.attrId,
      ruleNumber: 50,
      protocol: 6, // TCP
      ruleAction: 'deny',
      egress: false,
      cidrBlock: '0.0.0.0/0',
      portRange: { from: 5432, to: 5432 },
    });

    // Rule 100 — broad allow, TCP high port range.
    new ec2.CfnNetworkAclEntry(this, 'ScenarioGNaclEntry100', {
      networkAclId: this.scenarioGNacl.attrId,
      ruleNumber: 100,
      protocol: 6, // TCP
      ruleAction: 'allow',
      egress: false,
      cidrBlock: '10.99.0.0/16',
      portRange: { from: 1024, to: 65535 },
    });

    // Rule 110 — broad allow, common application ports.
    new ec2.CfnNetworkAclEntry(this, 'ScenarioGNaclEntry110', {
      networkAclId: this.scenarioGNacl.attrId,
      ruleNumber: 110,
      protocol: 6, // TCP
      ruleAction: 'allow',
      egress: false,
      cidrBlock: '10.99.0.0/16',
      portRange: { from: 1, to: 1023 },
    });

    // Rule 900 — catch-all allow for the rest of the VPC CIDR.
    new ec2.CfnNetworkAclEntry(this, 'ScenarioGNaclEntry900', {
      networkAclId: this.scenarioGNacl.attrId,
      ruleNumber: 900,
      protocol: -1, // all protocols
      ruleAction: 'allow',
      egress: false,
      cidrBlock: '10.99.0.0/16',
    });

    // Default-allow egress so outbound (and return) traffic is unaffected —
    // the misconfiguration is inbound-only, matching a real-world scenario
    // where only inbound access review would need the diagnostic tool.
    new ec2.CfnNetworkAclEntry(this, 'ScenarioGNaclEntry900Egress', {
      networkAclId: this.scenarioGNacl.attrId,
      ruleNumber: 900,
      protocol: -1,
      ruleAction: 'allow',
      egress: true,
      cidrBlock: '0.0.0.0/0',
    });

    new ec2.CfnSubnetNetworkAclAssociation(this, 'ScenarioGNaclAssoc', {
      subnetId: this.scenarioGSubnetB.attrSubnetId,
      networkAclId: this.scenarioGNacl.attrId,
    });

    // -----------------------------------------------------------------------
    // Scenario G — app-tier-01 EC2 instance (inter-tier reachability
    // target), placed in subnet-b behind the buried-deny NACL.
    // -----------------------------------------------------------------------
    this.scenarioGAppTierInstance = new ec2.CfnInstance(this, 'ScenarioGAppTierInstance', {
      instanceType: 't3.micro',
      imageId: ec2.MachineImage.latestAmazonLinux2023().getImage(this).imageId,
      subnetId: this.scenarioGSubnetB.attrSubnetId,
      securityGroupIds: [scenarioGSecurityGroup.attrGroupId],
      tags: [
        { key: 'Name', value: 'app-tier-01' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario G — Stack outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'ScenarioGSubnetAId', {
      value: this.scenarioGSubnetA.attrSubnetId,
      description: 'Scenario G subnet-a ID',
    });

    new cdk.CfnOutput(this, 'ScenarioGSubnetBId', {
      value: this.scenarioGSubnetB.attrSubnetId,
      description: 'Scenario G subnet-b ID',
    });

    new cdk.CfnOutput(this, 'ScenarioGAppTierInstanceId', {
      value: this.scenarioGAppTierInstance.ref,
      description: 'Scenario G app-tier-01 instance ID',
    });

    // =========================================================================
    // Scenario H — tcp_traceroute (corroborated by agentic_reachability_analyze)
    //
    // External-endpoint-unreachable failure: an EC2 instance in a private
    // subnet with a NAT gateway present at the VPC level, but the subnet's
    // route table contains a more-specific /32 blackhole route for a demo
    // target IP that overlaps the default 0.0.0.0/0 NAT route. Because
    // longest-prefix-match routing evaluates the /32 before the /0, traffic
    // to the target is silently dropped despite the NAT route appearing
    // correct in a cursory review. The Transit Gateway is referenced by the
    // blackhole route to give the route table a plausible non-blackhole
    // appearance (a TGW attachment route looks normal).
    // See demo-scenarios/RESOURCE_REUSE.md for the full reuse mapping.
    // =========================================================================

    // -----------------------------------------------------------------------
    // Scenario H — Subnet (subnet-c)
    // -----------------------------------------------------------------------
    this.scenarioHSubnetC = new ec2.CfnSubnet(this, 'ScenarioHSubnetC', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.32.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'subnet-c' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario H — Route table with /32 blackhole
    //
    // The route table has two routes:
    //   1. 0.0.0.0/0 → Transit Gateway (appears to provide outbound via TGW/NAT)
    //   2. 198.51.100.42/32 → blackhole (silently drops traffic to this IP)
    //
    // Because longest-prefix-match wins, any traffic to 198.51.100.42 hits
    // the /32 blackhole before considering the /0 default route. The TGW
    // route makes the table look healthy at a glance.
    // -----------------------------------------------------------------------
    this.scenarioHRouteTable = new ec2.CfnRouteTable(this, 'ScenarioHRouteTable', {
      vpcId: sharedVpcId,
      tags: [
        { key: 'Name', value: 'subnet-c-rt' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Associate the route table with subnet-c.
    new ec2.CfnSubnetRouteTableAssociation(this, 'ScenarioHRtAssoc', {
      subnetId: this.scenarioHSubnetC.attrSubnetId,
      routeTableId: this.scenarioHRouteTable.ref,
    });

    // Default route via Transit Gateway — makes the table look like it has
    // a valid outbound path. The TGW ID is imported from Scenario C's export.
    const tgwId = this.sharedTransitGatewayId ??
      cdk.Fn.importValue(props?.goatTgwExportName ?? 'GOATDemoScenarioCTransitGatewayId');

    new ec2.CfnRoute(this, 'ScenarioHDefaultRoute', {
      routeTableId: this.scenarioHRouteTable.ref,
      destinationCidrBlock: '0.0.0.0/0',
      transitGatewayId: tgwId,
    });

    // /32 blackhole — the actual misconfiguration. Uses RFC 5737 TEST-NET-2
    // address (198.51.100.42) as the demo target IP. This route silently
    // drops traffic because longest-prefix-match evaluates it before /0.
    new ec2.CfnRoute(this, 'ScenarioHBlackholeRoute', {
      routeTableId: this.scenarioHRouteTable.ref,
      destinationCidrBlock: '198.51.100.42/32',
      // Route to TGW with no matching TGW route table entry for this /32.
      // Traffic is effectively blackholed at the TGW level (no matching route = drop).
      // CloudFormation requires at least one target property.
      transitGatewayId: tgwId,
    });

    // -----------------------------------------------------------------------
    // Scenario H — Security group for svc-alpha (permissive egress so the
    // blackhole is the only thing preventing connectivity)
    // -----------------------------------------------------------------------
    const scenarioHSecurityGroup = new ec2.CfnSecurityGroup(this, 'ScenarioHSvcAlphaSg', {
      groupDescription: 'Security group for svc-alpha',
      vpcId: sharedVpcId,
      securityGroupIngress: [
        {
          ipProtocol: 'tcp',
          fromPort: 0,
          toPort: 65535,
          cidrIp: '10.99.0.0/16',
        },
      ],
      securityGroupEgress: [
        {
          ipProtocol: '-1',
          cidrIp: '0.0.0.0/0',
        },
      ],
      tags: [
        { key: 'Name', value: 'svc-alpha-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario H — svc-alpha EC2 instance
    //
    // This instance is shared between:
    //   - Scenario H: source for tcp_traceroute
    //   - Scenario K: app tier for db_connectivity_probe
    //
    // Tagged with goat-network-traceroute-allowed=true for SSM-based
    // diagnostic action opt-in (Traceroute_Opt_In_Tag).
    // -----------------------------------------------------------------------
    this.scenarioHSvcAlphaInstance = new ec2.CfnInstance(this, 'ScenarioHSvcAlphaInstance', {
      instanceType: 't3.micro',
      imageId: ec2.MachineImage.latestAmazonLinux2023().getImage(this).imageId,
      subnetId: this.scenarioHSubnetC.attrSubnetId,
      securityGroupIds: [scenarioHSecurityGroup.attrGroupId],
      tags: [
        { key: 'Name', value: 'svc-alpha' },
        { key: 'goat-network-traceroute-allowed', value: 'true' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario H — Stack outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'ScenarioHSubnetCId', {
      value: this.scenarioHSubnetC.attrSubnetId,
      description: 'Scenario H subnet-c ID',
    });

    new cdk.CfnOutput(this, 'ScenarioHSvcAlphaInstanceId', {
      value: this.scenarioHSvcAlphaInstance.ref,
      description: 'Scenario H svc-alpha instance ID',
    });

    new cdk.CfnOutput(this, 'ScenarioHRouteTableId', {
      value: this.scenarioHRouteTable.ref,
      description: 'Scenario H route table ID',
    });

    // =========================================================================
    // Scenario I — tls_traceroute
    //
    // TLS handshake failure: an internal Application Load Balancer serving
    // HTTPS with a valid ACM certificate that covers a different domain
    // (*.internal.corp.example.com) than the SNI the demo client sends
    // (api.service.example.com). DescribeLoadBalancers and
    // DescribeListenerCertificates alone do not reveal the domain mismatch —
    // only tls_traceroute's active handshake probe surfaces the SNI failure.
    //
    // Reuses the Network Firewall inspection VPC from Scenario C for the
    // TLS inspection path (Req 6.2).
    // See demo-scenarios/RESOURCE_REUSE.md for the full reuse mapping.
    // =========================================================================

    // Resolve the Network Firewall inspection VPC ID for this scenario's
    // inspection path reference (imported from Scenario C stack outputs).
    if (!this.sharedFirewallEndpointId) {
      this.sharedFirewallEndpointId = cdk.Fn.importValue(
        props?.goatFirewallEndpointExportName ?? 'GOATDemoScenarioCInspectionVpcId',
      );
    }

    // Two AZs needed for ALB (ALBs require subnets in at least 2 AZs).
    const azSecondary = cdk.Fn.select(1, cdk.Fn.getAzs(''));

    // -----------------------------------------------------------------------
    // Scenario I — Subnets (subnet-d, subnet-e) for the internal ALB
    // -----------------------------------------------------------------------
    this.scenarioISubnetD = new ec2.CfnSubnet(this, 'ScenarioISubnetD', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.33.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'subnet-d' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    this.scenarioISubnetE = new ec2.CfnSubnet(this, 'ScenarioISubnetE', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.34.0/24',
      availabilityZone: azSecondary,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'subnet-e' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario I — Security group for the internal ALB
    // -----------------------------------------------------------------------
    const scenarioIAlbSg = new ec2.CfnSecurityGroup(this, 'ScenarioIAlbSg', {
      groupDescription: 'Security group for svc-beta-alb',
      vpcId: sharedVpcId,
      securityGroupIngress: [
        {
          ipProtocol: 'tcp',
          fromPort: 443,
          toPort: 443,
          cidrIp: '10.99.0.0/16',
        },
      ],
      securityGroupEgress: [
        {
          ipProtocol: '-1',
          cidrIp: '0.0.0.0/0',
        },
      ],
      tags: [
        { key: 'Name', value: 'svc-beta-alb-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario I — Self-signed ACM Certificate covering *.internal.corp.example.com
    //
    // The demo client will attempt a TLS handshake with SNI
    // api.service.example.com, which does NOT match this certificate's
    // domain. We use a Lambda-backed Custom Resource to generate a
    // self-signed certificate and import it into ACM so the certificate
    // is actually valid (not stuck in PENDING_VALIDATION).
    // The misconfiguration is the domain mismatch, not the validation state.
    // -----------------------------------------------------------------------

    // Lambda function that generates a self-signed cert and imports it into ACM.
    // Uses Python 3.12 runtime because the Node.js 20 Lambda runtime has a
    // symbol conflict with the bundled openssl binary (OPENSSL_1.1.1 not found).
    // Python Lambda runs on Amazon Linux 2023 with a working openssl.
    const scenarioICertLambda = new lambda.Function(this, 'ScenarioICertLambdaFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(2),
      logRetention: logs.RetentionDays.ONE_WEEK,
      code: lambda.Code.fromInline(`
import subprocess
import tempfile
import os
import boto3

def handler(event, context):
    acm = boto3.client('acm')
    request_type = event['RequestType']
    physical_id = event.get('PhysicalResourceId', '')

    if request_type == 'Delete':
        if physical_id and physical_id.startswith('arn:aws:acm:'):
            try:
                acm.delete_certificate(CertificateArn=physical_id)
            except Exception as e:
                print(f'Delete cert failed (may already be deleted): {e}')
        return {'PhysicalResourceId': physical_id or 'none'}

    # Generate self-signed cert using openssl (works in Python Lambda runtime)
    domain = '*.internal.corp.example.com'
    with tempfile.TemporaryDirectory() as tmpdir:
        key_file = os.path.join(tmpdir, 'key.pem')
        cert_file = os.path.join(tmpdir, 'cert.pem')

        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-keyout', key_file, '-out', cert_file,
            '-days', '3650', '-nodes',
            '-subj', f'/CN={domain}',
            '-addext', f'subjectAltName=DNS:{domain}'
        ], check=True, capture_output=True)

        with open(cert_file, 'r') as f:
            cert_pem = f.read()
        with open(key_file, 'r') as f:
            key_pem = f.read()

    response = acm.import_certificate(
        Certificate=cert_pem.encode(),
        PrivateKey=key_pem.encode()
    )
    cert_arn = response['CertificateArn']

    return {'PhysicalResourceId': cert_arn, 'Data': {'CertificateArn': cert_arn}}
`),
    });

    // Grant the Lambda permissions to import and delete certificates in ACM
    scenarioICertLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['acm:ImportCertificate', 'acm:DeleteCertificate'],
      resources: ['*'],
    }));

    // Custom Resource provider backed by the Lambda
    const scenarioICertProvider = new cr.Provider(this, 'ScenarioICertProvider', {
      onEventHandler: scenarioICertLambda,
      logRetention: logs.RetentionDays.ONE_WEEK,
    });

    // Custom Resource that triggers the Lambda to create the self-signed cert
    this.scenarioICertificate = new cdk.CustomResource(this, 'ScenarioICert', {
      serviceToken: scenarioICertProvider.serviceToken,
      properties: {
        // Change this value to force re-creation if needed
        Domain: '*.internal.corp.example.com',
      },
    });

    // Extract the certificate ARN from the custom resource output
    this.scenarioICertArn = this.scenarioICertificate.getAttString('CertificateArn');

    // Tag the custom resource for demo identification
    cdk.Tags.of(this.scenarioICertificate).add('Name', 'svc-beta-cert');
    cdk.Tags.of(this.scenarioICertificate).add('goat-demo', 'true');
    cdk.Tags.of(this.scenarioICertificate).add('goat-scenario', 'network-troubleshooting');
    cdk.Tags.of(this.scenarioICertificate).add('auto-delete', 'no');

    // -----------------------------------------------------------------------
    // Scenario I — Internal Application Load Balancer (svc-beta-alb)
    // -----------------------------------------------------------------------
    this.scenarioIAlb = new elbv2.CfnLoadBalancer(this, 'ScenarioIAlb', {
      name: 'svc-beta-alb',
      scheme: 'internal',
      type: 'application',
      securityGroups: [scenarioIAlbSg.attrGroupId],
      subnets: [
        this.scenarioISubnetD.attrSubnetId,
        this.scenarioISubnetE.attrSubnetId,
      ],
      tags: [
        { key: 'Name', value: 'svc-beta-alb' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario I — Target group (required for the HTTPS listener)
    // -----------------------------------------------------------------------
    const scenarioITargetGroup = new elbv2.CfnTargetGroup(this, 'ScenarioITargetGroup', {
      name: 'svc-beta-tg',
      protocol: 'HTTP',
      port: 80,
      vpcId: sharedVpcId,
      targetType: 'ip',
      healthCheckProtocol: 'HTTP',
      healthCheckPath: '/health',
      tags: [
        { key: 'Name', value: 'svc-beta-tg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario I — HTTPS Listener with the mismatched certificate
    //
    // The listener binds the ALB to port 443 using the ACM certificate
    // that covers *.internal.corp.example.com. Clients connecting with
    // SNI api.service.example.com will receive a TLS handshake failure
    // because the certificate's SAN does not cover that domain.
    // -----------------------------------------------------------------------
    new elbv2.CfnListener(this, 'ScenarioIListener', {
      loadBalancerArn: this.scenarioIAlb.ref,
      port: 443,
      protocol: 'HTTPS',
      certificates: [{ certificateArn: this.scenarioICertArn }],
      defaultActions: [
        {
          type: 'forward',
          targetGroupArn: scenarioITargetGroup.ref,
        },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario I — Stack outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'ScenarioIAlbDnsName', {
      value: this.scenarioIAlb.attrDnsName,
      description: 'Scenario I svc-beta-alb DNS name',
    });

    new cdk.CfnOutput(this, 'ScenarioIAlbArn', {
      value: this.scenarioIAlb.ref,
      description: 'Scenario I svc-beta-alb ARN',
    });

    new cdk.CfnOutput(this, 'ScenarioICertArn', {
      value: this.scenarioICertArn,
      description: 'Scenario I certificate ARN',
    });

    // =========================================================================
    // Scenario J — dns_resolve
    //
    // DNS split-horizon failure: a Route 53 Resolver outbound endpoint and
    // a resolver rule that forwards queries for a specific internal domain
    // (db.internal.corp.example.com) to a conditional-forwarder target IP
    // that returns a different address than what the VPC's default resolver
    // would return. The dns_resolve diagnostic tool compares the forwarded
    // answer against a direct VPC resolution, revealing the resolver rule as
    // the source of the discrepancy.
    //
    // The conditional-forwarder target IP (10.99.1.100) is a plausible
    // on-premises DNS server address that would return a cached/outdated
    // record. The domain name uses RFC 2606 .example.com to avoid any real
    // DNS collision.
    //
    // See demo-scenarios/RESOURCE_REUSE.md for the full reuse mapping.
    // =========================================================================

    // -----------------------------------------------------------------------
    // Scenario J — Two subnets in different AZs (required by Route 53
    // Resolver outbound endpoints, which need ≥2 IP addresses in ≥2 AZs)
    // -----------------------------------------------------------------------
    const az2 = cdk.Fn.select(1, cdk.Fn.getAzs(''));

    this.scenarioJResolverSubnet1 = new ec2.CfnSubnet(this, 'ScenarioJResolverSubnet1', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.35.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'resolver-ep-1' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    this.scenarioJResolverSubnet2 = new ec2.CfnSubnet(this, 'ScenarioJResolverSubnet2', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.36.0/24',
      availabilityZone: az2,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'resolver-ep-2' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario J — Security group for the Resolver outbound endpoint
    // (allows DNS traffic on UDP/TCP 53 outbound to the forwarder target)
    // -----------------------------------------------------------------------
    const scenarioJResolverSg = new ec2.CfnSecurityGroup(this, 'ScenarioJResolverSg', {
      groupDescription: 'Security group for resolver endpoint',
      vpcId: sharedVpcId,
      securityGroupIngress: [
        {
          ipProtocol: 'tcp',
          fromPort: 53,
          toPort: 53,
          cidrIp: '10.99.0.0/16',
        },
        {
          ipProtocol: 'udp',
          fromPort: 53,
          toPort: 53,
          cidrIp: '10.99.0.0/16',
        },
      ],
      securityGroupEgress: [
        {
          ipProtocol: '-1',
          cidrIp: '0.0.0.0/0',
        },
      ],
      tags: [
        { key: 'Name', value: 'resolver-ep-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario J — Route 53 Resolver outbound endpoint
    //
    // The outbound endpoint provides the egress path for forwarded DNS
    // queries. It requires ≥2 IP addresses in different AZs.
    // -----------------------------------------------------------------------
    this.scenarioJResolverOutbound = new route53resolver.CfnResolverEndpoint(this, 'ScenarioJResolverOutbound', {
      direction: 'OUTBOUND',
      ipAddresses: [
        { subnetId: this.scenarioJResolverSubnet1.attrSubnetId },
        { subnetId: this.scenarioJResolverSubnet2.attrSubnetId },
      ],
      securityGroupIds: [scenarioJResolverSg.attrGroupId],
      name: 'corp-dns-ep',
      tags: [
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario J — Resolver forwarding rule
    //
    // Forwards queries for db.internal.corp.example.com to a conditional
    // forwarder at 10.99.1.100 (a plausible on-premises DNS server IP
    // within the VPC CIDR). In a real scenario, this forwarder would return
    // a cached/outdated IP address for the domain, while the VPC's default
    // resolver would return the current/correct IP from a Route 53 private
    // hosted zone. The dns_resolve tool reveals this discrepancy.
    // -----------------------------------------------------------------------
    this.scenarioJResolverRule = new route53resolver.CfnResolverRule(this, 'ScenarioJResolverRule', {
      domainName: 'db.internal.corp.example.com',
      ruleType: 'FORWARD',
      resolverEndpointId: this.scenarioJResolverOutbound.attrResolverEndpointId,
      targetIps: [
        { ip: '10.99.1.100', port: '53' },
      ],
      name: 'corp-db-fwd',
      tags: [
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Associate the resolver rule with the shared VPC so that DNS queries
    // originating from instances in this VPC for the target domain are
    // forwarded through the outbound endpoint to the conditional forwarder.
    new route53resolver.CfnResolverRuleAssociation(this, 'ScenarioJRuleAssoc', {
      resolverRuleId: this.scenarioJResolverRule.attrResolverRuleId,
      vpcId: sharedVpcId,
    });

    // -----------------------------------------------------------------------
    // Scenario J — Stack outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'ScenarioJResolverEndpointId', {
      value: this.scenarioJResolverOutbound.attrResolverEndpointId,
      description: 'Scenario J Resolver outbound endpoint ID',
    });

    new cdk.CfnOutput(this, 'ScenarioJResolverRuleId', {
      value: this.scenarioJResolverRule.attrResolverRuleId,
      description: 'Scenario J Resolver rule ID',
    });

    new cdk.CfnOutput(this, 'ScenarioJForwardedDomain', {
      value: 'db.internal.corp.example.com',
      description: 'Scenario J forwarded domain name',
    });

    // =========================================================================
    // Scenario K — db_connectivity_probe + agentic_reachability_analyze
    //
    // Database connectivity failure: an RDS MySQL instance (svc-data-01) in
    // a DB subnet group with a NACL that DENIES ephemeral return ports
    // (1024-65535) on EGRESS at rule 50 — evaluated before the broader
    // allow rules at 100 and 900. The security group on the RDS instance
    // correctly allows inbound 3306 from the VPC CIDR, so SG-only analysis
    // would show the path as healthy. Only the NACL egress deny blocks the
    // TCP handshake completion (SYN-ACK on an ephemeral port cannot leave
    // the DB subnet).
    //
    // The app-tier instance (svc-alpha from Scenario H) acts as the client
    // attempting to connect to the RDS instance.
    // See demo-scenarios/RESOURCE_REUSE.md for the full reuse mapping.
    // =========================================================================

    // -----------------------------------------------------------------------
    // Scenario K — DB Subnets (two AZs required for DB subnet group)
    // -----------------------------------------------------------------------
    this.scenarioKDbSubnet1 = new ec2.CfnSubnet(this, 'ScenarioKDbSubnet1', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.37.0/24',
      availabilityZone: cdk.Fn.select(0, cdk.Fn.getAzs('')),
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'db-subnet-1' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    this.scenarioKDbSubnet2 = new ec2.CfnSubnet(this, 'ScenarioKDbSubnet2', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.40.0/24',
      availabilityZone: cdk.Fn.select(1, cdk.Fn.getAzs('')),
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'db-subnet-2' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario K — DB Subnet Group
    // -----------------------------------------------------------------------
    this.scenarioKDbSubnetGroup = new rds.CfnDBSubnetGroup(this, 'ScenarioKDbSubnetGroup', {
      dbSubnetGroupDescription: 'Subnet group for svc-data-01 (Scenario K)',
      subnetIds: [
        this.scenarioKDbSubnet1.attrSubnetId,
        this.scenarioKDbSubnet2.attrSubnetId,
      ],
      tags: [
        { key: 'Name', value: 'svc-data-01-subnet-group' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario K — Security group for svc-data-01 (RDS)
    //
    // Allows inbound MySQL (3306) from the VPC CIDR — this is intentional:
    // the SG looks healthy and would pass a security-group-only review.
    // The actual misconfiguration is at the NACL level (egress deny on
    // ephemeral ports blocks the TCP handshake return).
    // -----------------------------------------------------------------------
    const scenarioKDbSg = new ec2.CfnSecurityGroup(this, 'ScenarioKDbSg', {
      groupDescription: 'Security group for svc-data-01 RDS instance',
      vpcId: sharedVpcId,
      securityGroupIngress: [
        {
          ipProtocol: 'tcp',
          fromPort: 3306,
          toPort: 3306,
          cidrIp: '10.99.0.0/16',
        },
      ],
      securityGroupEgress: [
        {
          ipProtocol: '-1',
          cidrIp: '0.0.0.0/0',
        },
      ],
      tags: [
        { key: 'Name', value: 'svc-data-01-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario K — RDS MySQL instance (svc-data-01)
    //
    // Single-AZ, no public access, db.t4g.micro (minimal demo resource).
    // Uses the DEFAULT parameter group (no custom max_connections override).
    // The default max_connections for db.t4g.micro is ~85 (formula-based).
    //
    // The pool-saturator Lambda holds 90+ connections, exhausting the pool.
    // This makes the issue INVISIBLE from the AWS API — DescribeDBParameters
    // shows normal defaults, so the DevOps Agent cannot diagnose the problem
    // without using the db_connectivity_probe tool (which runs SHOW STATUS
    // from inside the VPC to discover Threads_connected >= max_connections).
    // -----------------------------------------------------------------------
    this.scenarioKRdsInstance = new rds.CfnDBInstance(this, 'ScenarioKRdsInstance', {
      dbInstanceIdentifier: 'svc-data-01',
      dbInstanceClass: 'db.t4g.micro',
      engine: 'mysql',
      masterUsername: 'admin',
      masterUserPassword: 'GoatDemoK2026!',
      allocatedStorage: '20',
      storageType: 'gp2',
      dbSubnetGroupName: this.scenarioKDbSubnetGroup.ref,
      // No custom parameter group — uses RDS default (max_connections ~85)
      vpcSecurityGroups: [scenarioKDbSg.attrGroupId],
      publiclyAccessible: false,
      multiAz: false,
      tags: [
        { key: 'Name', value: 'svc-data-01' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting-k' },
        { key: 'auto-delete', value: 'no' },
      ],
    });
    this.scenarioKRdsInstance.cfnOptions.deletionPolicy = cdk.CfnDeletionPolicy.DELETE;

    // -----------------------------------------------------------------------
    // Scenario K — NACL REMOVED (connection pool exhaustion scenario)
    //
    // The previous NACL-based misconfiguration (ephemeral-port egress deny)
    // has been removed. The DB subnets now revert to the VPC's default NACL
    // which allows all traffic. This ensures the network path from svc-alpha
    // to svc-data-01 is fully open — the failure now occurs at the
    // application layer (connection pool full) rather than network layer.
    // -----------------------------------------------------------------------

    // -----------------------------------------------------------------------
    // Scenario K — Pool Saturator Lambda (connection pool exhaustion client)
    //
    // A Lambda function that maintains 90 persistent MySQL connections to
    // svc-data-01, saturating the default max_connections (~85 for
    // db.t4g.micro). Triggered every 5 minutes via EventBridge.
    //
    // KEY DESIGN: No custom parameter group is used — the RDS instance has
    // normal default settings. The agent CANNOT detect this issue from AWS
    // APIs alone (DescribeDBParameters shows normal values). Only the
    // db_connectivity_probe tool can diagnose it by running SHOW STATUS
    // from inside the VPC and discovering Threads_connected >= max_connections.
    // -----------------------------------------------------------------------

    // IAM role for the Pool Saturator Lambda — VPC access + CloudWatch Logs
    const scenarioKPoolSaturatorRole = new iam.CfnRole(this, 'ScenarioKPoolSaturatorRole', {
      assumeRolePolicyDocument: {
        Version: '2012-10-17',
        Statement: [{
          Effect: 'Allow',
          Principal: { Service: 'lambda.amazonaws.com' },
          Action: 'sts:AssumeRole',
        }],
      },
      managedPolicyArns: [
        `arn:${cdk.Aws.PARTITION}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole`,
      ],
      policies: [
        {
          policyName: 'CloudWatchLogsAccess',
          policyDocument: {
            Version: '2012-10-17',
            Statement: [{
              Effect: 'Allow',
              Action: [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
              ],
              Resource: `arn:${cdk.Aws.PARTITION}:logs:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:*`,
            }],
          },
        },
      ],
      tags: [
        { key: 'Name', value: 'svc-data-sync-role' },
        { key: 'goat-demo', value: 'true' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Security group for the Pool Saturator Lambda — allows egress on 3306 to RDS
    const scenarioKPoolSaturatorSg = new ec2.CfnSecurityGroup(this, 'ScenarioKPoolSaturatorSg', {
      groupDescription: 'Security group for svc-data-sync-worker Lambda',
      vpcId: sharedVpcId,
      securityGroupEgress: [
        {
          ipProtocol: 'tcp',
          fromPort: 3306,
          toPort: 3306,
          destinationSecurityGroupId: scenarioKDbSg.attrGroupId,
        },
      ],
      tags: [
        { key: 'Name', value: 'svc-data-sync-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Pool Saturator Lambda function — Python 3.12, placeholder code
    // (actual handler logic is added in task 1.4)
    const scenarioKPoolSaturator = new lambda.CfnFunction(this, 'ScenarioKPoolSaturator', {
      functionName: 'svc-data-sync-worker',
      runtime: 'python3.12',
      handler: 'index.handler',
      memorySize: 128,
      timeout: 300,
      reservedConcurrentExecutions: 1,
      role: scenarioKPoolSaturatorRole.attrArn,
      vpcConfig: {
        subnetIds: [this.scenarioHSubnetC.attrSubnetId],
        securityGroupIds: [scenarioKPoolSaturatorSg.attrGroupId],
      },
      environment: {
        variables: {
          DB_ENDPOINT: this.scenarioKRdsInstance.attrEndpointAddress,
          DB_USERNAME: 'admin',
          DB_PASSWORD: 'GoatDemoK2026!',
          TARGET_CONNECTIONS: '90',
        },
      },
      code: {
        zipFile: [
          'import os, socket, struct, time, json, logging',
          '',
          'logger = logging.getLogger()',
          'logger.setLevel(logging.INFO)',
          'connections = []',
          '',
          'def handler(event, context):',
          '    endpoint = os.environ.get("DB_ENDPOINT", "")',
          '    username = os.environ.get("DB_USERNAME", "admin")',
          '    password = os.environ.get("DB_PASSWORD", "")',
          '    port = int(os.environ.get("DB_PORT", "3306"))',
          '    target = int(os.environ.get("TARGET_CONNECTIONS", "90"))',
          '',
          '    # Clean dead connections',
          '    alive = []',
          '    for s in connections:',
          '        try:',
          '            s.sendall(b"\\x01\\x00\\x00\\x00\\x0e")',
          '            alive.append(s)',
          '        except:',
          '            try: s.close()',
          '            except: pass',
          '    connections[:] = alive',
          '',
          '    # Open new connections until target reached',
          '    opened = 0',
          '    while len(connections) < target:',
          '        try:',
          '            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)',
          '            s.settimeout(5)',
          '            s.connect((endpoint, port))',
          '            # Read MySQL handshake',
          '            hdr = b""',
          '            while len(hdr) < 4: hdr += s.recv(4 - len(hdr))',
          '            plen = struct.unpack("<I", hdr[:3] + b"\\x00")[0]',
          '            payload = b""',
          '            while len(payload) < plen: payload += s.recv(plen - len(payload))',
          '            if payload[0] == 0xFF:',
          '                logger.info(f"Max retries at {len(connections)} conns (error packet)")',
          '                s.close()',
          '                break',
          '            # Send auth response (native password)',
          '            # Extract salt from handshake',
          '            null1 = payload.find(b"\\x00", 1)',
          '            salt1 = payload[null1+5:null1+13]',
          '            rest = payload[null1+13+1:]  # skip filler',
          '            # capabilities, charset, etc - skip to salt2',
          '            salt2 = rest[12:12+12] if len(rest) > 24 else b""',
          '            salt = salt1 + salt2',
          '            # Build HandshakeResponse41 with mysql_native_password',
          '            import hashlib',
          '            pwd_hash = hashlib.sha1(password.encode()).digest()',
          '            pwd_hash2 = hashlib.sha1(pwd_hash).digest()',
          '            xor_input = hashlib.sha1(salt + pwd_hash2).digest()',
          '            auth_resp = bytes(a ^ b for a, b in zip(pwd_hash, xor_input))',
          '            # Build packet',
          '            cap = 0x000FA68D',
          '            pkt = struct.pack("<IIB", cap, 16777216, 33)',
          '            pkt += b"\\x00" * 23',
          '            pkt += username.encode() + b"\\x00"',
          '            pkt += bytes([len(auth_resp)]) + auth_resp',
          '            pkt += b"\\x00"  # no db',
          '            pkt_hdr = struct.pack("<I", len(pkt))[:3] + b"\\x01"',
          '            s.sendall(pkt_hdr + pkt)',
          '            # Read response',
          '            rhdr = b""',
          '            while len(rhdr) < 4: rhdr += s.recv(4 - len(rhdr))',
          '            rlen = struct.unpack("<I", rhdr[:3] + b"\\x00")[0]',
          '            rbody = b""',
          '            while len(rbody) < rlen: rbody += s.recv(rlen - len(rbody))',
          '            if rbody[0] == 0x00:  # OK packet',
          '                connections.append(s)',
          '                opened += 1',
          '            elif rbody[0] == 0xFF:  # Error',
          '                ecode = struct.unpack("<H", rbody[1:3])[0]',
          '                if ecode == 1040:',
          '                    logger.info(f"Connection limit reached at {len(connections)} conns")',
          '                    s.close()',
          '                    break',
          '                logger.warning(f"Auth error {ecode}")',
          '                s.close()',
          '                break',
          '            else:',
          '                connections.append(s)',
          '                opened += 1',
          '        except Exception as e:',
          '            logger.error(f"Connection failed: {e}")',
          '            break',
          '',
          '    result = {"active": len(connections), "target": target, "opened": opened, "status": "ok"}',
          '    logger.info(f"DataSyncWorker: {result}")',
          '    return result',
        ].join('\n'),
      },
      tags: [
        { key: 'Name', value: 'svc-data-sync-worker' },
        { key: 'goat-demo', value: 'true' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // EventBridge rule — trigger Pool Saturator every 5 minutes to maintain saturation
    const scenarioKSaturatorSchedule = new events.CfnRule(this, 'ScenarioKSaturatorSchedule', {
      name: 'svc-data-sync-schedule',
      description: 'Scheduled data sync worker execution',
      scheduleExpression: 'rate(5 minutes)',
      state: 'ENABLED',
      targets: [{
        arn: scenarioKPoolSaturator.attrArn,
        id: 'DataSyncTarget',
      }],
    });

    // Permission for EventBridge to invoke the Pool Saturator Lambda
    new lambda.CfnPermission(this, 'ScenarioKSaturatorPermission', {
      action: 'lambda:InvokeFunction',
      functionName: scenarioKPoolSaturator.ref,
      principal: 'events.amazonaws.com',
      sourceArn: scenarioKSaturatorSchedule.attrArn,
    });

    // -----------------------------------------------------------------------
    // Scenario K — Stack outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'ScenarioKRdsEndpoint', {
      value: this.scenarioKRdsInstance.attrEndpointAddress,
      description: 'Scenario K svc-data-01 RDS endpoint address',
    });

    new cdk.CfnOutput(this, 'ScenarioKRdsInstanceId', {
      value: this.scenarioKRdsInstance.ref,
      description: 'Scenario K svc-data-01 RDS instance ID',
    });

    // =========================================================================
    // Scenario L — ssm_health_check
    //
    // SSM-unreachable instance: an EC2 instance with the correct IAM
    // instance profile (AmazonSSMManagedInstanceCore) but a restrictive
    // NACL on its subnet blocking HTTPS (port 443) outbound. Since SSM
    // Agent communicates with regional SSM VPC endpoints over HTTPS, the
    // NACL isolation prevents the instance from registering with SSM — but
    // DescribeVpcEndpoints alone cannot reveal the NACL-level block because
    // the endpoints exist and the security group allows 443.
    //
    // The instance does NOT carry the goat-network-traceroute-allowed tag
    // because ssm_health_check is an API-only action that does not require
    // SSM command execution opt-in.
    // See demo-scenarios/RESOURCE_REUSE.md for the full reuse mapping.
    // =========================================================================

    // -----------------------------------------------------------------------
    // Scenario L — Subnet (subnet-f)
    // -----------------------------------------------------------------------
    this.scenarioLSubnetF = new ec2.CfnSubnet(this, 'ScenarioLSubnetF', {
      vpcId: sharedVpcId,
      cidrBlock: '10.99.39.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'subnet-f' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario L — IAM instance profile with AmazonSSMManagedInstanceCore
    //
    // The instance profile is correct — the misconfiguration is at the
    // network layer (NACL), not the IAM layer. This makes the scenario
    // realistic: IAM checks pass, VPC endpoint checks pass, but the
    // instance still cannot reach SSM because of the subnet-level NACL.
    // -----------------------------------------------------------------------
    const scenarioLRole = new iam.CfnRole(this, 'ScenarioLInstanceRole', {
      assumeRolePolicyDocument: {
        Version: '2012-10-17',
        Statement: [{
          Effect: 'Allow',
          Principal: { Service: 'ec2.amazonaws.com' },
          Action: 'sts:AssumeRole',
        }],
      },
      managedPolicyArns: [
        `arn:${cdk.Aws.PARTITION}:iam::aws:policy/AmazonSSMManagedInstanceCore`,
      ],
      tags: [
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    const scenarioLInstanceProfile = new iam.CfnInstanceProfile(this, 'ScenarioLInstanceProfile', {
      roles: [scenarioLRole.ref],
    });

    // -----------------------------------------------------------------------
    // Scenario L — Security group (permissive — allows HTTPS outbound, so
    // the SG alone looks healthy; only the NACL actually blocks 443)
    // -----------------------------------------------------------------------
    const scenarioLSecurityGroup = new ec2.CfnSecurityGroup(this, 'ScenarioLHostSg', {
      groupDescription: 'Security group for subnet-a-host',
      vpcId: sharedVpcId,
      securityGroupIngress: [
        {
          ipProtocol: 'tcp',
          fromPort: 0,
          toPort: 65535,
          cidrIp: '10.99.0.0/16',
        },
      ],
      securityGroupEgress: [
        {
          ipProtocol: '-1',
          cidrIp: '0.0.0.0/0',
        },
      ],
      tags: [
        { key: 'Name', value: 'subnet-a-host-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario L — NACL blocking HTTPS (443) outbound
    //
    // Rule evaluation order:
    //   Rule 80 (deny, TCP 443, egress, 0.0.0.0/0) — blocks HTTPS out
    //   Rule 100 (allow, all TCP, egress, VPC CIDR) — normal VPC traffic
    //   Rule 900 (allow, all, egress, 0.0.0.0/0) — catch-all
    //
    // The deny at rule 80 fires before the broader allows because NACL
    // rules are evaluated lowest-number-first. Port 443 outbound to any
    // destination (including SSM VPC endpoint IPs within the VPC CIDR or
    // the public SSM endpoint) is dropped, isolating the instance from
    // the Systems Manager control plane.
    //
    // Inbound rules are permissive (allow all) so the scenario focuses
    // purely on the outbound HTTPS block affecting SSM agent registration.
    // -----------------------------------------------------------------------
    this.scenarioLNacl = new ec2.CfnNetworkAcl(this, 'ScenarioLNacl', {
      vpcId: sharedVpcId,
      tags: [
        { key: 'Name', value: 'subnet-f-nacl' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Rule 80 — deny HTTPS (443) outbound to all destinations.
    // This is the misconfiguration: SSM Agent requires 443 outbound to
    // reach regional SSM VPC endpoints (or the public SSM endpoint).
    new ec2.CfnNetworkAclEntry(this, 'ScenarioLNaclEntry80Egress', {
      networkAclId: this.scenarioLNacl.attrId,
      ruleNumber: 80,
      protocol: 6, // TCP
      ruleAction: 'deny',
      egress: true,
      cidrBlock: '0.0.0.0/0',
      portRange: { from: 443, to: 443 },
    });

    // Rule 100 — allow all TCP egress to VPC CIDR (normal inter-subnet).
    new ec2.CfnNetworkAclEntry(this, 'ScenarioLNaclEntry100Egress', {
      networkAclId: this.scenarioLNacl.attrId,
      ruleNumber: 100,
      protocol: 6, // TCP
      ruleAction: 'allow',
      egress: true,
      cidrBlock: '10.99.0.0/16',
      portRange: { from: 1, to: 65535 },
    });

    // Rule 900 — catch-all allow egress (evaluated after rule 80 deny).
    new ec2.CfnNetworkAclEntry(this, 'ScenarioLNaclEntry900Egress', {
      networkAclId: this.scenarioLNacl.attrId,
      ruleNumber: 900,
      protocol: -1,
      ruleAction: 'allow',
      egress: true,
      cidrBlock: '0.0.0.0/0',
    });

    // Inbound: allow all (no inbound restriction for this scenario).
    new ec2.CfnNetworkAclEntry(this, 'ScenarioLNaclEntry100Ingress', {
      networkAclId: this.scenarioLNacl.attrId,
      ruleNumber: 100,
      protocol: -1,
      ruleAction: 'allow',
      egress: false,
      cidrBlock: '0.0.0.0/0',
    });

    new ec2.CfnSubnetNetworkAclAssociation(this, 'ScenarioLNaclAssoc', {
      subnetId: this.scenarioLSubnetF.attrSubnetId,
      networkAclId: this.scenarioLNacl.attrId,
    });

    // -----------------------------------------------------------------------
    // Scenario L — subnet-a-host EC2 instance
    //
    // Correct IAM instance profile (AmazonSSMManagedInstanceCore) but NO
    // goat-network-traceroute-allowed tag — this is for ssm_health_check,
    // not for traceroute/connectivity probe actions.
    // -----------------------------------------------------------------------
    this.scenarioLSubnetAHostInstance = new ec2.CfnInstance(this, 'ScenarioLSubnetAHostInstance', {
      instanceType: 't3.micro',
      imageId: ec2.MachineImage.latestAmazonLinux2023().getImage(this).imageId,
      subnetId: this.scenarioLSubnetF.attrSubnetId,
      securityGroupIds: [scenarioLSecurityGroup.attrGroupId],
      iamInstanceProfile: scenarioLInstanceProfile.ref,
      tags: [
        { key: 'Name', value: 'subnet-a-host' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'network-troubleshooting' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Scenario L — Stack outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'ScenarioLSubnetFId', {
      value: this.scenarioLSubnetF.attrSubnetId,
      description: 'Scenario L subnet-f ID',
    });

    new cdk.CfnOutput(this, 'ScenarioLSubnetAHostInstanceId', {
      value: this.scenarioLSubnetAHostInstance.ref,
      description: 'Scenario L subnet-a-host instance ID',
    });

    // Scenario I, K resources are added by tasks 7.5, 7.7.
  }
}
