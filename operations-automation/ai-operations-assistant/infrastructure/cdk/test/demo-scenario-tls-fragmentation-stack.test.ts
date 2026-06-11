import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { DemoScenarioTlsFragmentationStack } from '../lib/demo-scenario-tls-fragmentation-stack';

/**
 * CDK assertion tests for Scenario C — TLS Fragmentation Stack.
 *
 * Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 5.13, 3.3, 3.4
 */
describe('DemoScenarioTlsFragmentationStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new DemoScenarioTlsFragmentationStack(app, 'TestStack', {
      env: { account: '123456789012', region: 'us-east-1' },
      // No sharedVpc — tests standalone mode
    });
    template = Template.fromStack(stack);
  });

  // -------------------------------------------------------------------------
  // 1. Inspection VPC with CIDR 10.98.0.0/16
  // -------------------------------------------------------------------------
  test('creates inspection VPC with CIDR 10.98.0.0/16', () => {
    template.hasResourceProperties('AWS::EC2::VPC', {
      CidrBlock: '10.98.0.0/16',
      EnableDnsSupport: true,
      EnableDnsHostnames: true,
    });
  });

  // -------------------------------------------------------------------------
  // 2. Spoke VPC (standalone mode — CIDR 10.99.0.0/16)
  // -------------------------------------------------------------------------
  test('creates standalone spoke VPC with CIDR 10.99.0.0/16 when no sharedVpc provided', () => {
    template.hasResourceProperties('AWS::EC2::VPC', {
      CidrBlock: '10.99.0.0/16',
      EnableDnsSupport: true,
      EnableDnsHostnames: true,
    });
  });

  // -------------------------------------------------------------------------
  // 3. Spoke subnets: 10.99.13.0/24 and 10.99.20.0/24
  // -------------------------------------------------------------------------
  test('creates spoke private subnet 10.99.13.0/24', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', {
      CidrBlock: '10.99.13.0/24',
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-private' }),
      ]),
    });
  });

  test('creates spoke TGW subnet 10.99.20.0/24', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', {
      CidrBlock: '10.99.20.0/24',
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-spoke-tgw' }),
      ]),
    });
  });

  // -------------------------------------------------------------------------
  // 4. Inspection subnets: 10.98.0.0/24, 10.98.1.0/24, 10.98.2.0/24
  // -------------------------------------------------------------------------
  test('creates inspection NAT subnet 10.98.0.0/24', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', {
      CidrBlock: '10.98.0.0/24',
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-insp-nat' }),
      ]),
    });
  });

  test('creates inspection firewall subnet 10.98.1.0/24', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', {
      CidrBlock: '10.98.1.0/24',
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-insp-fw' }),
      ]),
    });
  });

  test('creates inspection TGW subnet 10.98.2.0/24', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', {
      CidrBlock: '10.98.2.0/24',
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-insp-tgw' }),
      ]),
    });
  });

  // -------------------------------------------------------------------------
  // 5. Transit Gateway with defaultRouteTableAssociation/Propagation disabled
  // -------------------------------------------------------------------------
  test('creates Transit Gateway with default RT association and propagation disabled', () => {
    template.hasResourceProperties('AWS::EC2::TransitGateway', {
      DefaultRouteTableAssociation: 'disable',
      DefaultRouteTablePropagation: 'disable',
    });
  });

  // -------------------------------------------------------------------------
  // 6. TGW attachments (2 total, one with appliance mode)
  // -------------------------------------------------------------------------
  test('creates two Transit Gateway attachments', () => {
    const attachments = template.findResources('AWS::EC2::TransitGatewayAttachment');
    expect(Object.keys(attachments).length).toBe(2);
  });

  test('creates inspection TGW attachment with appliance mode enabled', () => {
    template.hasResourceProperties('AWS::EC2::TransitGatewayAttachment', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-tgw-attach-insp' }),
      ]),
    });
    // Verify appliance mode via property override
    const attachments = template.findResources('AWS::EC2::TransitGatewayAttachment', {
      Properties: {
        Tags: Match.arrayWith([
          Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-tgw-attach-insp' }),
        ]),
        Options: Match.objectLike({
          ApplianceModeSupport: 'enable',
        }),
      },
    });
    expect(Object.keys(attachments).length).toBe(1);
  });

  test('creates spoke TGW attachment', () => {
    template.hasResourceProperties('AWS::EC2::TransitGatewayAttachment', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-tgw-attach-spoke' }),
      ]),
    });
  });

  // -------------------------------------------------------------------------
  // 7. Network Firewall exists (firewallName: goat-demo-tls-nfw)
  // -------------------------------------------------------------------------
  test('creates Network Firewall with correct name', () => {
    template.hasResourceProperties('AWS::NetworkFirewall::Firewall', {
      FirewallName: 'goat-demo-tls-nfw',
    });
  });

  // -------------------------------------------------------------------------
  // 8. Network Firewall rule group with STATEFUL type and STRICT_ORDER
  // -------------------------------------------------------------------------
  test('creates NFW rule group with STATEFUL type and STRICT_ORDER', () => {
    template.hasResourceProperties('AWS::NetworkFirewall::RuleGroup', {
      Type: 'STATEFUL',
      RuleGroupName: 'goat-demo-tls-rules',
      RuleGroup: Match.objectLike({
        StatefulRuleOptions: {
          RuleOrder: 'STRICT_ORDER',
        },
      }),
    });
  });

  test('NFW rule group contains SNI pass rule for *.amazonaws.com', () => {
    template.hasResourceProperties('AWS::NetworkFirewall::RuleGroup', {
      RuleGroup: Match.objectLike({
        RulesSource: {
          StatefulRules: Match.arrayWith([
            Match.objectLike({
              Action: 'PASS',
              Header: Match.objectLike({
                Protocol: 'TLS',
              }),
              RuleOptions: Match.arrayWith([
                Match.objectLike({
                  Keyword: 'sni',
                  Settings: ['*.amazonaws.com'],
                }),
              ]),
            }),
          ]),
        },
      }),
    });
  });

  // -------------------------------------------------------------------------
  // 9. Firewall policy with aws:drop_established
  // -------------------------------------------------------------------------
  test('creates firewall policy with aws:drop_established default action', () => {
    template.hasResourceProperties('AWS::NetworkFirewall::FirewallPolicy', {
      FirewallPolicyName: 'goat-demo-tls-policy',
      FirewallPolicy: Match.objectLike({
        StatefulDefaultActions: ['aws:drop_established'],
        StatefulEngineOptions: {
          RuleOrder: 'STRICT_ORDER',
        },
      }),
    });
  });

  // -------------------------------------------------------------------------
  // 10. NAT Gateway exists
  // -------------------------------------------------------------------------
  test('creates NAT Gateway', () => {
    template.hasResourceProperties('AWS::EC2::NatGateway', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-insp-nat-gw' }),
      ]),
    });
  });

  // -------------------------------------------------------------------------
  // 11. Internet Gateway exists
  // -------------------------------------------------------------------------
  test('creates Internet Gateway', () => {
    template.hasResourceProperties('AWS::EC2::InternetGateway', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-insp-igw' }),
      ]),
    });
  });

  test('attaches Internet Gateway to inspection VPC', () => {
    template.hasResource('AWS::EC2::VPCGatewayAttachment', {});
  });

  // -------------------------------------------------------------------------
  // 12. EC2 instance with t3.micro
  // -------------------------------------------------------------------------
  test('creates EC2 instance with t3.micro', () => {
    template.hasResourceProperties('AWS::EC2::Instance', {
      InstanceType: 't3.micro',
    });
  });

  // -------------------------------------------------------------------------
  // 13. IAM role with AmazonSSMManagedInstanceCore
  // -------------------------------------------------------------------------
  test('creates IAM role with AmazonSSMManagedInstanceCore policy', () => {
    template.hasResourceProperties('AWS::IAM::Role', {
      ManagedPolicyArns: Match.arrayWith([
        'arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore',
      ]),
    });
  });

  test('creates instance profile referencing the IAM role', () => {
    template.hasResource('AWS::IAM::InstanceProfile', {});
  });

  // -------------------------------------------------------------------------
  // 14. CloudWatch log groups for flow and alert
  // -------------------------------------------------------------------------
  test('creates CloudWatch log group for NFW flow logs', () => {
    template.hasResourceProperties('AWS::Logs::LogGroup', {
      LogGroupName: '/aws/network-firewall/goat-demo-tls-flow',
    });
  });

  test('creates CloudWatch log group for NFW alert logs', () => {
    template.hasResourceProperties('AWS::Logs::LogGroup', {
      LogGroupName: '/aws/network-firewall/goat-demo-tls-alert',
    });
  });

  test('configures NFW logging to CloudWatch log groups', () => {
    template.hasResourceProperties('AWS::NetworkFirewall::LoggingConfiguration', {
      LoggingConfiguration: Match.objectLike({
        LogDestinationConfigs: Match.arrayWith([
          Match.objectLike({
            LogType: 'FLOW',
            LogDestinationType: 'CloudWatchLogs',
            LogDestination: { logGroup: '/aws/network-firewall/goat-demo-tls-flow' },
          }),
          Match.objectLike({
            LogType: 'ALERT',
            LogDestinationType: 'CloudWatchLogs',
            LogDestination: { logGroup: '/aws/network-firewall/goat-demo-tls-alert' },
          }),
        ]),
      }),
    });
  });

  // -------------------------------------------------------------------------
  // 15. Route tables (4 total)
  // -------------------------------------------------------------------------
  test('creates 4 route tables for the inspection topology', () => {
    const routeTables = template.findResources('AWS::EC2::RouteTable');
    expect(Object.keys(routeTables).length).toBe(4);
  });

  test('creates spoke private route table', () => {
    template.hasResourceProperties('AWS::EC2::RouteTable', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-spoke-private-rt' }),
      ]),
    });
  });

  test('creates inspection TGW route table', () => {
    template.hasResourceProperties('AWS::EC2::RouteTable', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-insp-tgw-rt' }),
      ]),
    });
  });

  test('creates inspection firewall route table', () => {
    template.hasResourceProperties('AWS::EC2::RouteTable', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-insp-fw-rt' }),
      ]),
    });
  });

  test('creates inspection NAT route table', () => {
    template.hasResourceProperties('AWS::EC2::RouteTable', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Name', Value: 'goat-demo-tls-insp-nat-rt' }),
      ]),
    });
  });

  // -------------------------------------------------------------------------
  // 16. Tags verification: goat-demo=true, goat-scenario=tls-fragmentation, auto-delete=no
  // -------------------------------------------------------------------------
  test('inspection VPC has goat-demo and goat-scenario tags', () => {
    template.hasResourceProperties('AWS::EC2::VPC', {
      CidrBlock: '10.98.0.0/16',
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
        Match.objectLike({ Key: 'goat-scenario', Value: 'tls-fragmentation' }),
      ]),
    });
  });

  test('inspection VPC has auto-delete tag', () => {
    template.hasResourceProperties('AWS::EC2::VPC', {
      CidrBlock: '10.98.0.0/16',
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'auto-delete', Value: 'no' }),
      ]),
    });
  });

  test('Transit Gateway has goat-demo and goat-scenario tags', () => {
    template.hasResourceProperties('AWS::EC2::TransitGateway', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
        Match.objectLike({ Key: 'goat-scenario', Value: 'tls-fragmentation' }),
      ]),
    });
  });

  test('Transit Gateway has auto-delete tag', () => {
    template.hasResourceProperties('AWS::EC2::TransitGateway', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'auto-delete', Value: 'no' }),
      ]),
    });
  });

  test('NAT Gateway has goat-demo and goat-scenario tags', () => {
    template.hasResourceProperties('AWS::EC2::NatGateway', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
        Match.objectLike({ Key: 'goat-scenario', Value: 'tls-fragmentation' }),
      ]),
    });
  });

  test('NAT Gateway has auto-delete tag', () => {
    template.hasResourceProperties('AWS::EC2::NatGateway', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'auto-delete', Value: 'no' }),
      ]),
    });
  });

  test('Internet Gateway has goat-demo and goat-scenario tags', () => {
    template.hasResourceProperties('AWS::EC2::InternetGateway', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
        Match.objectLike({ Key: 'goat-scenario', Value: 'tls-fragmentation' }),
      ]),
    });
  });

  test('Internet Gateway has auto-delete tag', () => {
    template.hasResourceProperties('AWS::EC2::InternetGateway', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'auto-delete', Value: 'no' }),
      ]),
    });
  });

  test('Network Firewall has goat-demo and goat-scenario tags', () => {
    template.hasResourceProperties('AWS::NetworkFirewall::Firewall', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
        Match.objectLike({ Key: 'goat-scenario', Value: 'tls-fragmentation' }),
      ]),
    });
  });

  test('Network Firewall has auto-delete tag', () => {
    template.hasResourceProperties('AWS::NetworkFirewall::Firewall', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'auto-delete', Value: 'no' }),
      ]),
    });
  });

  // -------------------------------------------------------------------------
  // 17. EC2/ENI tags include goat-network-capture-allowed=true
  // -------------------------------------------------------------------------
  test('EC2 instance has goat-network-capture-allowed tag', () => {
    template.hasResourceProperties('AWS::EC2::Instance', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-network-capture-allowed', Value: 'true' }),
      ]),
    });
  });

  test('EC2 instance has goat-demo tag', () => {
    template.hasResourceProperties('AWS::EC2::Instance', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
      ]),
    });
  });

  test('EC2 instance has goat-scenario tag', () => {
    template.hasResourceProperties('AWS::EC2::Instance', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-scenario', Value: 'tls-fragmentation' }),
      ]),
    });
  });

  test('EC2 instance has auto-delete tag', () => {
    template.hasResourceProperties('AWS::EC2::Instance', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'auto-delete', Value: 'no' }),
      ]),
    });
  });

  test('ENI has goat-network-capture-allowed tag', () => {
    template.hasResourceProperties('AWS::EC2::NetworkInterface', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-network-capture-allowed', Value: 'true' }),
      ]),
    });
  });

  test('ENI has goat-demo tag', () => {
    template.hasResourceProperties('AWS::EC2::NetworkInterface', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
      ]),
    });
  });

  test('ENI has goat-scenario tag', () => {
    template.hasResourceProperties('AWS::EC2::NetworkInterface', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'goat-scenario', Value: 'tls-fragmentation' }),
      ]),
    });
  });

  test('ENI has auto-delete tag', () => {
    template.hasResourceProperties('AWS::EC2::NetworkInterface', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'auto-delete', Value: 'no' }),
      ]),
    });
  });

  // -------------------------------------------------------------------------
  // 18. Stack outputs (TlsInstanceId, TlsInstanceEniId)
  // -------------------------------------------------------------------------
  test('exports TlsInstanceId output', () => {
    template.hasOutput('TlsInstanceId', {
      Description: 'EC2 instance ID for TLS fragmentation demo',
    });
  });

  test('exports TlsInstanceEniId output', () => {
    template.hasOutput('TlsInstanceEniId', {
      Description: 'Primary ENI ID of TLS fragmentation instance (for Network Agent capture)',
    });
  });

  // -------------------------------------------------------------------------
  // Additional: EC2 UserData contains curl loop
  // -------------------------------------------------------------------------
  test('EC2 instance has UserData configured', () => {
    template.hasResourceProperties('AWS::EC2::Instance', {
      UserData: Match.anyValue(),
    });
  });

  // -------------------------------------------------------------------------
  // Additional: TGW route tables exist
  // -------------------------------------------------------------------------
  test('creates two TGW route tables', () => {
    const tgwRouteTables = template.findResources('AWS::EC2::TransitGatewayRouteTable');
    expect(Object.keys(tgwRouteTables).length).toBe(2);
  });

  // -------------------------------------------------------------------------
  // Additional: Route table associations exist
  // -------------------------------------------------------------------------
  test('creates route table subnet associations', () => {
    const associations = template.findResources('AWS::EC2::SubnetRouteTableAssociation');
    expect(Object.keys(associations).length).toBe(4);
  });
});
