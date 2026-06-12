import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as networkfirewall from 'aws-cdk-lib/aws-networkfirewall';
import * as crypto from 'crypto';
import { Construct } from 'constructs';

/**
 * Props for the TLS Fragmentation demo scenario stack.
 * Requires the GOAT network infra VPC (via CloudFormation export or direct IVpc).
 */
export interface DemoScenarioTlsFragmentationStackProps extends cdk.StackProps {
  /** Direct VPC reference (from Scenario A or test). Takes priority over goatVpcExportName. */
  sharedVpc?: ec2.IVpc;
  /** CloudFormation export name for the GOAT network infra VPC ID (e.g., 'GOATNetworkAgentVpcId'). */
  goatVpcExportName?: string;
}

/**
 * G.O.A.T. Demo Scenario C — TLS Fragmentation Stack
 *
 * Provisions the centralized inspection architecture for demonstrating
 * Network Firewall TLS inspection failure with ML-KEM key exchange:
 * - Spoke subnets in shared/standalone VPC (10.99.13.0/24, 10.99.20.0/24)
 * - Inspection VPC (10.98.0.0/16) with NAT, firewall, and TGW subnets
 *
 * Subsequent tasks (3.2–3.5) add Transit Gateway, Network Firewall,
 * route tables, and EC2 instance to this scaffold.
 */
export class DemoScenarioTlsFragmentationStack extends cdk.Stack {
  // -----------------------------------------------------------------------
  // Spoke VPC references
  // -----------------------------------------------------------------------

  /** Spoke VPC ID — resolved from shared VPC (goat-demo-vpc owned by main GOAT stack) */
  public readonly spokeVpcId: string;

  /** Spoke private subnet (10.99.13.0/24) */
  public readonly spokePrivateSubnet: ec2.CfnSubnet;

  /** Spoke TGW attachment subnet (10.99.20.0/24) */
  public readonly spokeTgwSubnet: ec2.CfnSubnet;

  // -----------------------------------------------------------------------
  // Inspection VPC references
  // -----------------------------------------------------------------------

  /** Inspection VPC (10.98.0.0/16) */
  public readonly inspectionVpc: ec2.CfnVPC;

  /** Inspection NAT subnet (10.98.0.0/24) */
  public readonly inspectionNatSubnet: ec2.CfnSubnet;

  /** Inspection Firewall subnet (10.98.1.0/24) */
  public readonly inspectionFwSubnet: ec2.CfnSubnet;

  /** Inspection TGW subnet (10.98.2.0/24) */
  public readonly inspectionTgwSubnet: ec2.CfnSubnet;

  // -----------------------------------------------------------------------
  // Transit Gateway references
  // -----------------------------------------------------------------------

  /** Transit Gateway */
  public readonly transitGateway: ec2.CfnTransitGateway;

  /** TGW attachment for spoke VPC */
  public readonly spokeAttachment: ec2.CfnTransitGatewayAttachment;

  /** TGW attachment for inspection VPC (appliance mode) */
  public readonly inspectionAttachment: ec2.CfnTransitGatewayAttachment;

  /** TGW route table for spoke traffic */
  public readonly spokeTgwRouteTable: ec2.CfnTransitGatewayRouteTable;

  /** TGW route table for inspection traffic */
  public readonly inspectionTgwRouteTable: ec2.CfnTransitGatewayRouteTable;

  // -----------------------------------------------------------------------
  // Network Firewall references
  // -----------------------------------------------------------------------

  /** AWS Network Firewall */
  public readonly networkFirewall: networkfirewall.CfnFirewall;

  /** Network Firewall rule group */
  public readonly firewallRuleGroup: networkfirewall.CfnRuleGroup;

  /** Network Firewall policy */
  public readonly firewallPolicy: networkfirewall.CfnFirewallPolicy;

  // -----------------------------------------------------------------------
  // NAT Gateway and Internet Gateway references
  // -----------------------------------------------------------------------

  /** NAT Gateway in inspection NAT subnet */
  public readonly natGateway: ec2.CfnNatGateway;

  /** Elastic IP for NAT Gateway */
  public readonly natEip: ec2.CfnEIP;

  /** Internet Gateway attached to inspection VPC */
  public readonly internetGateway: ec2.CfnInternetGateway;

  constructor(scope: Construct, id: string, props?: DemoScenarioTlsFragmentationStackProps) {
    super(scope, id, props);

    // Common tags for all resources in this stack
    cdk.Tags.of(this).add('goat-demo', 'true');
    cdk.Tags.of(this).add('goat-scenario', 'tls-fragmentation');
    cdk.Tags.of(this).add('auto-delete', 'no');

    // Single AZ for all subnets in this scenario
    const az = cdk.Fn.select(0, cdk.Fn.getAzs(''));

    // -----------------------------------------------------------------------
    // Spoke VPC — Use sharedVpc prop, or import from CloudFormation export.
    // The VPC must be the GOAT network infra VPC so traffic mirroring works.
    // -----------------------------------------------------------------------
    let spokeVpcId: string;

    if (props?.sharedVpc) {
      spokeVpcId = props.sharedVpc.vpcId;
    } else if (props?.goatVpcExportName) {
      spokeVpcId = cdk.Fn.importValue(props.goatVpcExportName);
    } else {
      throw new Error(
        'DemoScenarioTlsFragmentationStack requires either sharedVpc or goatVpcExportName. ' +
        'The GOAT network infra must be deployed first (deploy-all.ps1).'
      );
    }

    this.spokeVpcId = spokeVpcId;

    // -----------------------------------------------------------------------
    // Spoke Subnets — Private and TGW attachment
    // -----------------------------------------------------------------------
    this.spokePrivateSubnet = new ec2.CfnSubnet(this, 'SpokePrivateSubnet', {
      vpcId: spokeVpcId,
      cidrBlock: '10.99.13.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-private' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    this.spokeTgwSubnet = new ec2.CfnSubnet(this, 'SpokeTgwSubnet', {
      vpcId: spokeVpcId,
      cidrBlock: '10.99.20.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-spoke-tgw' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Inspection VPC — 10.98.0.0/16
    // -----------------------------------------------------------------------
    this.inspectionVpc = new ec2.CfnVPC(this, 'InspectionVpc', {
      cidrBlock: '10.98.0.0/16',
      enableDnsSupport: true,
      enableDnsHostnames: true,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-inspection-vpc' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Inspection Subnets — NAT, Firewall, TGW (single AZ)
    // -----------------------------------------------------------------------
    this.inspectionNatSubnet = new ec2.CfnSubnet(this, 'InspectionNatSubnet', {
      vpcId: this.inspectionVpc.attrVpcId,
      cidrBlock: '10.98.0.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-nat' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    this.inspectionFwSubnet = new ec2.CfnSubnet(this, 'InspectionFwSubnet', {
      vpcId: this.inspectionVpc.attrVpcId,
      cidrBlock: '10.98.1.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-fw' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    this.inspectionTgwSubnet = new ec2.CfnSubnet(this, 'InspectionTgwSubnet', {
      vpcId: this.inspectionVpc.attrVpcId,
      cidrBlock: '10.98.2.0/24',
      availabilityZone: az,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-tgw' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Transit Gateway
    // -----------------------------------------------------------------------
    this.transitGateway = new ec2.CfnTransitGateway(this, 'TransitGateway', {
      defaultRouteTableAssociation: 'disable',
      defaultRouteTablePropagation: 'disable',
      tags: [
        { key: 'Name', value: 'goat-demo-tls-tgw' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Transit Gateway Attachments
    // -----------------------------------------------------------------------

    // Spoke VPC attachment (subnet 10.99.20.0/24)
    this.spokeAttachment = new ec2.CfnTransitGatewayAttachment(this, 'TgwAttachSpoke', {
      transitGatewayId: this.transitGateway.attrId,
      vpcId: this.spokeVpcId,
      subnetIds: [this.spokeTgwSubnet.attrSubnetId],
      tags: [
        { key: 'Name', value: 'goat-demo-tls-tgw-attach-spoke' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Inspection VPC attachment (subnet 10.98.2.0/24) with appliance mode enabled
    this.inspectionAttachment = new ec2.CfnTransitGatewayAttachment(this, 'TgwAttachInspection', {
      transitGatewayId: this.transitGateway.attrId,
      vpcId: this.inspectionVpc.attrVpcId,
      subnetIds: [this.inspectionTgwSubnet.attrSubnetId],
      tags: [
        { key: 'Name', value: 'goat-demo-tls-tgw-attach-insp' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });
    // Enable appliance mode on inspection attachment
    (this.inspectionAttachment as any).addPropertyOverride('Options.ApplianceModeSupport', 'enable');

    // -----------------------------------------------------------------------
    // Transit Gateway Route Tables
    // -----------------------------------------------------------------------

    // Spoke route table: default route 0.0.0.0/0 → inspection VPC attachment
    this.spokeTgwRouteTable = new ec2.CfnTransitGatewayRouteTable(this, 'TgwRouteTableSpoke', {
      transitGatewayId: this.transitGateway.attrId,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-tgw-rt-spoke' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Inspection route table: route 10.99.0.0/16 → spoke VPC attachment
    this.inspectionTgwRouteTable = new ec2.CfnTransitGatewayRouteTable(this, 'TgwRouteTableInspection', {
      transitGatewayId: this.transitGateway.attrId,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-tgw-rt-insp' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Transit Gateway Route Table Associations
    // -----------------------------------------------------------------------

    // Associate spoke attachment with spoke route table
    new ec2.CfnTransitGatewayRouteTableAssociation(this, 'TgwRtAssocSpoke', {
      transitGatewayAttachmentId: this.spokeAttachment.attrId,
      transitGatewayRouteTableId: this.spokeTgwRouteTable.attrTransitGatewayRouteTableId,
    });

    // Associate inspection attachment with inspection route table
    new ec2.CfnTransitGatewayRouteTableAssociation(this, 'TgwRtAssocInspection', {
      transitGatewayAttachmentId: this.inspectionAttachment.attrId,
      transitGatewayRouteTableId: this.inspectionTgwRouteTable.attrTransitGatewayRouteTableId,
    });

    // -----------------------------------------------------------------------
    // Transit Gateway Routes
    // -----------------------------------------------------------------------

    // Spoke RT: default route → inspection VPC attachment
    new ec2.CfnTransitGatewayRoute(this, 'TgwRouteSpokeDefault', {
      transitGatewayRouteTableId: this.spokeTgwRouteTable.attrTransitGatewayRouteTableId,
      destinationCidrBlock: '0.0.0.0/0',
      transitGatewayAttachmentId: this.inspectionAttachment.attrId,
    });

    // Inspection RT: route to spoke VPC CIDR → spoke VPC attachment
    new ec2.CfnTransitGatewayRoute(this, 'TgwRouteInspectionToSpoke', {
      transitGatewayRouteTableId: this.inspectionTgwRouteTable.attrTransitGatewayRouteTableId,
      destinationCidrBlock: '10.99.0.0/16',
      transitGatewayAttachmentId: this.spokeAttachment.attrId,
    });

    // -----------------------------------------------------------------------
    // Network Firewall — Rule Group, Policy, and Firewall
    // -----------------------------------------------------------------------

    // Stateful rule group with SNI-based pass rule for *.amazonaws.com
    // Uses Suricata-compatible rules string format (tls.sni keyword)
    this.firewallRuleGroup = new networkfirewall.CfnRuleGroup(this, 'NfwRuleGroup', {
      capacity: 100,
      ruleGroupName: 'goat-demo-tls-rules',
      type: 'STATEFUL',
      ruleGroup: {
        rulesSource: {
          rulesString: 'pass tls any any -> any any (tls.sni; content:".amazonaws.com"; endswith; nocase; msg:"Allow AWS API traffic"; sid:1; rev:1;)',
        },
        statefulRuleOptions: {
          ruleOrder: 'STRICT_ORDER',
        },
      },
      tags: [
        { key: 'Name', value: 'goat-demo-tls-rules' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Firewall policy with aws:drop_established default action
    this.firewallPolicy = new networkfirewall.CfnFirewallPolicy(this, 'NfwPolicy', {
      firewallPolicyName: 'goat-demo-tls-policy',
      firewallPolicy: {
        statelessDefaultActions: ['aws:forward_to_sfe'],
        statelessFragmentDefaultActions: ['aws:forward_to_sfe'],
        statefulDefaultActions: ['aws:drop_established'],
        statefulEngineOptions: {
          ruleOrder: 'STRICT_ORDER',
        },
        statefulRuleGroupReferences: [
          {
            resourceArn: this.firewallRuleGroup.attrRuleGroupArn,
            priority: 1,
          },
        ],
      },
      tags: [
        { key: 'Name', value: 'goat-demo-tls-policy' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Network Firewall in firewall subnet (10.98.1.0/24)
    this.networkFirewall = new networkfirewall.CfnFirewall(this, 'NetworkFirewall', {
      firewallName: 'goat-demo-tls-nfw',
      firewallPolicyArn: this.firewallPolicy.attrFirewallPolicyArn,
      vpcId: this.inspectionVpc.attrVpcId,
      subnetMappings: [
        { subnetId: this.inspectionFwSubnet.attrSubnetId },
      ],
      tags: [
        { key: 'Name', value: 'goat-demo-tls-nfw' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Network Firewall Logging — FLOW + ALERT to CloudWatch
    // -----------------------------------------------------------------------

    const flowLogGroup = new logs.CfnLogGroup(this, 'NfwFlowLogGroup', {
      logGroupName: '/aws/network-firewall/goat-demo-tls-flow',
      retentionInDays: 7,
    });
    flowLogGroup.cfnOptions.deletionPolicy = cdk.CfnDeletionPolicy.DELETE;

    const alertLogGroup = new logs.CfnLogGroup(this, 'NfwAlertLogGroup', {
      logGroupName: '/aws/network-firewall/goat-demo-tls-alert',
      retentionInDays: 7,
    });
    alertLogGroup.cfnOptions.deletionPolicy = cdk.CfnDeletionPolicy.DELETE;

    new networkfirewall.CfnLoggingConfiguration(this, 'NfwLogging', {
      firewallArn: this.networkFirewall.ref,
      loggingConfiguration: {
        logDestinationConfigs: [
          {
            logType: 'FLOW',
            logDestinationType: 'CloudWatchLogs',
            logDestination: {
              logGroup: '/aws/network-firewall/goat-demo-tls-flow',
            },
          },
          {
            logType: 'ALERT',
            logDestinationType: 'CloudWatchLogs',
            logDestination: {
              logGroup: '/aws/network-firewall/goat-demo-tls-alert',
            },
          },
        ],
      },
    });

    // -----------------------------------------------------------------------
    // NAT Gateway — In inspection NAT subnet (10.98.0.0/24)
    // -----------------------------------------------------------------------

    this.natEip = new ec2.CfnEIP(this, 'NatEip', {
      domain: 'vpc',
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-nat-eip' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    this.natGateway = new ec2.CfnNatGateway(this, 'NatGateway', {
      subnetId: this.inspectionNatSubnet.attrSubnetId,
      allocationId: this.natEip.attrAllocationId,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-nat-gw' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Internet Gateway — Attached to inspection VPC
    // -----------------------------------------------------------------------

    this.internetGateway = new ec2.CfnInternetGateway(this, 'InternetGateway', {
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-igw' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    new ec2.CfnVPCGatewayAttachment(this, 'IgwAttachment', {
      vpcId: this.inspectionVpc.attrVpcId,
      internetGatewayId: this.internetGateway.attrInternetGatewayId,
    });

    // -----------------------------------------------------------------------
    // Route Tables — Subnet routing for the inspection topology
    // -----------------------------------------------------------------------

    // Extract Network Firewall VPC endpoint ID from endpoint format "az:vpce-id"
    const nfwEndpointId = cdk.Fn.select(1, cdk.Fn.split(':', cdk.Fn.select(0, this.networkFirewall.attrEndpointIds)));

    // --- Spoke Private Subnet Route Table (10.99.13.0/24) → Transit Gateway ---
    const spokePrivateRt = new ec2.CfnRouteTable(this, 'SpokePrivateRouteTable', {
      vpcId: this.spokeVpcId,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-spoke-private-rt' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    const spokePrivateDefaultRoute = new ec2.CfnRoute(this, 'SpokePrivateDefaultRoute', {
      routeTableId: spokePrivateRt.attrRouteTableId,
      destinationCidrBlock: '0.0.0.0/0',
      transitGatewayId: this.transitGateway.attrId,
    });
    // Route must wait for the TGW attachment to be available
    spokePrivateDefaultRoute.addDependency(this.spokeAttachment);

    new ec2.CfnSubnetRouteTableAssociation(this, 'SpokePrivateRtAssoc', {
      subnetId: this.spokePrivateSubnet.attrSubnetId,
      routeTableId: spokePrivateRt.attrRouteTableId,
    });

    // --- Inspection TGW Subnet Route Table (10.98.2.0/24) → NFW Endpoint ---
    const inspTgwRt = new ec2.CfnRouteTable(this, 'InspTgwRouteTable', {
      vpcId: this.inspectionVpc.attrVpcId,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-tgw-rt' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    const inspTgwDefaultRoute = new ec2.CfnRoute(this, 'InspTgwDefaultRoute', {
      routeTableId: inspTgwRt.attrRouteTableId,
      destinationCidrBlock: '0.0.0.0/0',
      vpcEndpointId: nfwEndpointId,
    });
    // Route must wait for the NFW to be fully available
    inspTgwDefaultRoute.addDependency(this.networkFirewall);

    new ec2.CfnSubnetRouteTableAssociation(this, 'InspTgwRtAssoc', {
      subnetId: this.inspectionTgwSubnet.attrSubnetId,
      routeTableId: inspTgwRt.attrRouteTableId,
    });

    // --- Inspection FW Subnet Route Table (10.98.1.0/24) → NAT Gateway ---
    const inspFwRt = new ec2.CfnRouteTable(this, 'InspFwRouteTable', {
      vpcId: this.inspectionVpc.attrVpcId,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-fw-rt' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    new ec2.CfnRoute(this, 'InspFwDefaultRoute', {
      routeTableId: inspFwRt.attrRouteTableId,
      destinationCidrBlock: '0.0.0.0/0',
      natGatewayId: this.natGateway.ref,
    });

    // Return-path: after NFW processes spoke-bound return traffic (SYN-ACKs,
    // response data), it exits to the FW subnet. This route sends it to the
    // TGW which delivers it back to the spoke VPC via the inspection RT
    // (10.99.0.0/16 → spoke attachment).
    const inspFwReturnRoute = new ec2.CfnRoute(this, 'InspFwToSpokeViaTgw', {
      routeTableId: inspFwRt.attrRouteTableId,
      destinationCidrBlock: '10.99.0.0/16',
      transitGatewayId: this.transitGateway.attrId,
    });
    inspFwReturnRoute.addDependency(this.spokeAttachment);

    new ec2.CfnSubnetRouteTableAssociation(this, 'InspFwRtAssoc', {
      subnetId: this.inspectionFwSubnet.attrSubnetId,
      routeTableId: inspFwRt.attrRouteTableId,
    });

    // --- Inspection NAT Subnet Route Table (10.98.0.0/24) → Internet Gateway ---
    const inspNatRt = new ec2.CfnRouteTable(this, 'InspNatRouteTable', {
      vpcId: this.inspectionVpc.attrVpcId,
      tags: [
        { key: 'Name', value: 'goat-demo-tls-insp-nat-rt' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    new ec2.CfnRoute(this, 'InspNatDefaultRoute', {
      routeTableId: inspNatRt.attrRouteTableId,
      destinationCidrBlock: '0.0.0.0/0',
      gatewayId: this.internetGateway.attrInternetGatewayId,
    });

    // Return-path route: traffic coming back from the internet (via NAT)
    // destined for the spoke VPC (10.99.0.0/16) must go through the NFW
    // endpoint for symmetric inspection. Without this, return SYN-ACKs
    // from ECR bypass the firewall and the TLS handshake never completes.
    const inspNatReturnRoute = new ec2.CfnRoute(this, 'InspNatToSpokeViaNfw', {
      routeTableId: inspNatRt.attrRouteTableId,
      destinationCidrBlock: '10.99.0.0/16',
      vpcEndpointId: nfwEndpointId,
    });
    inspNatReturnRoute.addDependency(this.networkFirewall);

    new ec2.CfnSubnetRouteTableAssociation(this, 'InspNatRtAssoc', {
      subnetId: this.inspectionNatSubnet.attrSubnetId,
      routeTableId: inspNatRt.attrRouteTableId,
    });

    // -----------------------------------------------------------------------
    // EC2 Instance — TLS traffic generator in spoke private subnet
    // -----------------------------------------------------------------------

    // IAM role with SSM managed policy for Session Manager access
    const tlsSsmRole = new iam.CfnRole(this, 'TlsSsmRole', {
      roleName: 'goat-demo-tls-ssm-role',
      assumeRolePolicyDocument: {
        Version: '2012-10-17',
        Statement: [
          {
            Effect: 'Allow',
            Principal: { Service: 'ec2.amazonaws.com' },
            Action: 'sts:AssumeRole',
          },
        ],
      },
      managedPolicyArns: [
        'arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore',
      ],
      tags: [
        { key: 'Name', value: 'goat-demo-tls-ssm-role' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // Instance profile referencing the IAM role
    const tlsInstanceProfile = new iam.CfnInstanceProfile(this, 'TlsInstanceProfile', {
      roles: [tlsSsmRole.ref],
      instanceProfileName: 'goat-demo-tls-instance-profile',
    });

    // Security group for the TLS instance (all outbound allowed, no inbound)
    const tlsSecurityGroup = new ec2.CfnSecurityGroup(this, 'TlsInstanceSg', {
      groupDescription: 'Security group for goat-demo TLS fragmentation instance',
      vpcId: this.spokeVpcId,
      securityGroupEgress: [
        {
          ipProtocol: '-1',
          cidrIp: '0.0.0.0/0',
          description: 'Allow all outbound traffic',
        },
      ],
      tags: [
        { key: 'Name', value: 'goat-demo-tls-instance-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // UserData script: install a systemd service that runs the ML-KEM curl
    // loop reliably. Using a systemd service (instead of an inline while-loop)
    // ensures the traffic generator survives reboots, restarts on failure,
    // and does NOT block cloud-init's final stage. The service hits
    // ecr.<region>.amazonaws.com every 20s with the X25519MLKEM768 hybrid
    // key exchange, producing the ~1522-byte fragmented TLS Client Hello.
    const userDataScript = [
      '#!/bin/bash',
      'set -x',
      'REGION=${AWS::Region}',
      '',
      '# Write the curl loop script',
      'cat > /usr/local/bin/goat-tls-curl.sh <<EOF',
      '#!/bin/bash',
      'while true; do',
      '  curl -s -o /dev/null -w "%{http_code}\\n" --curves X25519MLKEM768:X25519 https://ecr.'+'$REGION'+'.amazonaws.com/ 2>/dev/null || true',
      '  sleep 20',
      'done',
      'EOF',
      'chmod +x /usr/local/bin/goat-tls-curl.sh',
      '',
      '# Create the systemd service',
      'cat > /etc/systemd/system/goat-tls-curl.service <<EOF',
      '[Unit]',
      'Description=G.O.A.T. TLS Fragmentation traffic generator (ML-KEM curl loop)',
      'After=network-online.target',
      'Wants=network-online.target',
      '',
      '[Service]',
      'Type=simple',
      'ExecStart=/usr/local/bin/goat-tls-curl.sh',
      'Restart=always',
      'RestartSec=10',
      '',
      '[Install]',
      'WantedBy=multi-user.target',
      'EOF',
      '',
      '# Enable and start the service',
      'systemctl daemon-reload',
      'systemctl enable goat-tls-curl.service',
      'systemctl start goat-tls-curl.service',
    ].join('\n');

    const userData = cdk.Fn.base64(cdk.Fn.sub(userDataScript));

    // Hash the raw script so any change to the UserData forces a new
    // instance logical ID (and therefore a CloudFormation replacement).
    // L1 CfnInstance does not auto-replace on UserData change, so without
    // this the new script would never run (cloud-init only executes
    // UserData at first boot).
    const userDataHash = crypto
      .createHash('sha256')
      .update(userDataScript)
      .digest('hex')
      .slice(0, 8);

    // Network interface for the instance (allows tagging the ENI)
    const tlsEni = new ec2.CfnNetworkInterface(this, 'TlsInstanceEni', {
      subnetId: this.spokePrivateSubnet.attrSubnetId,
      groupSet: [tlsSecurityGroup.attrGroupId],
      tags: [
        { key: 'Name', value: 'goat-demo-tls-instance' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
        { key: 'goat-network-capture-allowed', value: 'true' },
      ],
    });

    // EC2 instance — t3.micro, AL2023, in spoke private subnet.
    // Logical ID includes the UserData hash so script changes force a
    // fresh instance that re-runs cloud-init.
    const tlsInstance = new ec2.CfnInstance(this, `TlsInstance${userDataHash}`, {
      instanceType: 't3.micro',
      imageId: ec2.MachineImage.latestAmazonLinux2023().getImage(this).imageId,
      iamInstanceProfile: tlsInstanceProfile.ref,
      userData,
      networkInterfaces: [
        {
          deviceIndex: '0',
          networkInterfaceId: tlsEni.attrId,
        },
      ],
      tags: [
        { key: 'Name', value: 'goat-demo-tls-instance' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'tls-fragmentation' },
        { key: 'auto-delete', value: 'no' },
        { key: 'goat-network-capture-allowed', value: 'true' },
      ],
    });

    // Ensure instance waits for role and profile
    tlsInstance.addDependency(tlsInstanceProfile);

    // -----------------------------------------------------------------------
    // Stack Outputs — TLS Instance
    // -----------------------------------------------------------------------

    new cdk.CfnOutput(this, 'TlsInstanceId', {
      value: tlsInstance.ref,
      description: 'EC2 instance ID for TLS fragmentation demo',
    });

    new cdk.CfnOutput(this, 'TlsInstanceEniId', {
      value: tlsEni.attrId,
      description: 'Primary ENI ID of TLS fragmentation instance (for Network Agent capture)',
    });
  }
}
