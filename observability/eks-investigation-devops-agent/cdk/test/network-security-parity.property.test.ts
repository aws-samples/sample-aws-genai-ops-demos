/**
 * Feature: cfn-to-cdk-migration, Property 3: Security-sensitive configuration parity
 *
 * Compare CDK-synthesized security group rules against reference model
 * extracted from the original CloudFormation vpc.yaml. Verify protocol, port range,
 * source/destination match for all ingress and egress rules.
 *
 * Validates: Requirements 2.4, 12.2
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { NetworkStack } from '../lib/network-stack';

// ---------------------------------------------------------------------------
// Reference model — security group rules from original CloudFormation vpc.yaml
// ---------------------------------------------------------------------------

interface RuleRef {
  protocol: string;       // 'tcp' | '-1' (all)
  fromPort?: number;
  toPort?: number;
  peer: 'anyIpv4' | 'self' | 'albSg' | 'eksSg' | 'dbSg' | 'computeSubnet' | 'localhost';
}

interface SgRulesRef {
  ingress: RuleRef[];
  egress: RuleRef[];
}

/** Expected rules per security group, derived from vpc.yaml */
const REFERENCE_RULES: Record<string, SgRulesRef> = {
  alb: {
    ingress: [
      { protocol: 'tcp', fromPort: 443, toPort: 443, peer: 'anyIpv4' },
    ],
    egress: [
      { protocol: 'tcp', fromPort: 8080, toPort: 8080, peer: 'eksSg' },
    ],
  },
  eks: {
    ingress: [
      { protocol: 'tcp', fromPort: 8080, toPort: 8080, peer: 'albSg' },
      { protocol: '-1', peer: 'self' },
    ],
    egress: [
      { protocol: 'tcp', fromPort: 5432, toPort: 5432, peer: 'dbSg' },
      { protocol: 'tcp', fromPort: 443, toPort: 443, peer: 'anyIpv4' },
      { protocol: '-1', peer: 'self' },
    ],
  },
  database: {
    ingress: [
      { protocol: 'tcp', fromPort: 5432, toPort: 5432, peer: 'eksSg' },
      // 2 CIDR-based rules for compute subnets
      { protocol: 'tcp', fromPort: 5432, toPort: 5432, peer: 'computeSubnet' },
      { protocol: 'tcp', fromPort: 5432, toPort: 5432, peer: 'computeSubnet' },
    ],
    egress: [
      { protocol: '-1', peer: 'localhost' },
    ],
  },
};

// ---------------------------------------------------------------------------
// Helper: synthesize NetworkStack with given environment/project params
// ---------------------------------------------------------------------------
function synthesizeNetworkStack(environment: string, projectName: string): Template {
  const app = new cdk.App();
  const stack = new NetworkStack(app, 'TestNetworkStack', {
    env: { region: 'us-east-1', account: '123456789012' },
    environment,
    projectName,
  });
  return Template.fromStack(stack);
}

// ---------------------------------------------------------------------------
// Helper: extract security group rules from synthesized template
// ---------------------------------------------------------------------------
interface SynthRule {
  protocol: string;
  fromPort?: number;
  toPort?: number;
  peerType: 'cidr' | 'sg';
  peerValue: string; // CIDR or SG logical ID
}

function extractIngressRules(template: Template, sgLogicalIdSubstring: string): SynthRule[] {
  const rules: SynthRule[] = [];
  const resources = template.toJSON().Resources;

  for (const [, resource] of Object.entries(resources as Record<string, any>)) {
    if (resource.Type !== 'AWS::EC2::SecurityGroup') continue;

    const logicalId = findLogicalId(resources, resource);
    if (!logicalId?.includes(sgLogicalIdSubstring)) continue;

    const ingressRules = resource.Properties?.SecurityGroupIngress ?? [];
    for (const rule of ingressRules) {
      rules.push(parseRule(rule));
    }
  }

  // Also check standalone SecurityGroupIngress resources
  for (const [, resource] of Object.entries(resources as Record<string, any>)) {
    if (resource.Type !== 'AWS::EC2::SecurityGroupIngress') continue;
    const groupId = resource.Properties?.GroupId;
    if (!groupId) continue;

    const targetRef = resolveRef(groupId);
    if (targetRef && targetRef.includes(sgLogicalIdSubstring)) {
      rules.push(parseRule(resource.Properties));
    }
  }

  return rules;
}

function extractEgressRules(template: Template, sgLogicalIdSubstring: string): SynthRule[] {
  const rules: SynthRule[] = [];
  const resources = template.toJSON().Resources;

  for (const [, resource] of Object.entries(resources as Record<string, any>)) {
    if (resource.Type !== 'AWS::EC2::SecurityGroupEgress') continue;
    const groupId = resource.Properties?.GroupId;
    if (!groupId) continue;

    const targetRef = resolveRef(groupId);
    if (targetRef && targetRef.includes(sgLogicalIdSubstring)) {
      rules.push(parseRule(resource.Properties));
    }
  }

  return rules;
}

function parseRule(props: any): SynthRule {
  const protocol = String(props.IpProtocol ?? '-1');
  const rule: SynthRule = {
    protocol,
    peerType: 'cidr',
    peerValue: '',
  };

  if (props.FromPort !== undefined) rule.fromPort = Number(props.FromPort);
  if (props.ToPort !== undefined) rule.toPort = Number(props.ToPort);

  if (props.CidrIp || props.CidrIpv4) {
    rule.peerType = 'cidr';
    rule.peerValue = props.CidrIp || props.CidrIpv4;
  } else if (props.SourceSecurityGroupId || props.DestinationSecurityGroupId) {
    rule.peerType = 'sg';
    const sgRef = props.SourceSecurityGroupId || props.DestinationSecurityGroupId;
    rule.peerValue = resolveRef(sgRef) ?? 'unknown';
  }

  return rule;
}

function resolveRef(value: any): string | undefined {
  if (typeof value === 'string') return value;
  if (value?.['Fn::GetAtt']?.[0]) return value['Fn::GetAtt'][0];
  if (value?.Ref) return value.Ref;
  return undefined;
}

function findLogicalId(resources: Record<string, any>, target: any): string | undefined {
  for (const [id, res] of Object.entries(resources)) {
    if (res === target) return id;
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Property test
// ---------------------------------------------------------------------------
describe('Property 3: Security-sensitive configuration parity (NetworkStack)', () => {
  it('ALB security group has exactly the expected ingress and egress rules', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');
    const projectArb = fc.constant('devops-agent-eks');

    fc.assert(
      fc.property(envArb, projectArb, (environment, projectName) => {
        const template = synthesizeNetworkStack(environment, projectName);

        // ALB ingress: 1 rule — HTTPS from 0.0.0.0/0
        const albIngress = extractIngressRules(template, 'AlbSecurityGroup');
        const httpsIngress = albIngress.filter(
          r => r.protocol === 'tcp' && r.fromPort === 443 && r.toPort === 443
            && r.peerType === 'cidr' && r.peerValue === '0.0.0.0/0',
        );
        expect(httpsIngress).toHaveLength(1);
        expect(albIngress).toHaveLength(1);

        // ALB egress: 1 rule — port 8080 to EKS SG
        const albEgress = extractEgressRules(template, 'AlbSecurityGroup');
        const eksEgress = albEgress.filter(
          r => r.protocol === 'tcp' && r.fromPort === 8080 && r.toPort === 8080
            && r.peerType === 'sg' && r.peerValue.includes('EksSecurityGroup'),
        );
        expect(eksEgress).toHaveLength(1);
        expect(albEgress).toHaveLength(1);
      }),
      { numRuns: 100 },
    );
  });

  it('EKS security group has exactly the expected ingress and egress rules', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');
    const projectArb = fc.constant('devops-agent-eks');

    fc.assert(
      fc.property(envArb, projectArb, (environment, projectName) => {
        const template = synthesizeNetworkStack(environment, projectName);

        // EKS ingress: 2 rules — ALB on 8080, self all-traffic
        const eksIngress = extractIngressRules(template, 'EksSecurityGroup');
        const albRule = eksIngress.filter(
          r => r.protocol === 'tcp' && r.fromPort === 8080 && r.toPort === 8080
            && r.peerType === 'sg' && r.peerValue.includes('AlbSecurityGroup'),
        );
        expect(albRule).toHaveLength(1);

        const selfIngressRule = eksIngress.filter(
          r => r.protocol === '-1'
            && r.peerType === 'sg' && r.peerValue.includes('EksSecurityGroup'),
        );
        expect(selfIngressRule).toHaveLength(1);
        expect(eksIngress).toHaveLength(2);

        // EKS egress: 3 rules — RDS 5432, HTTPS 443, self all-traffic
        const eksEgress = extractEgressRules(template, 'EksSecurityGroup');
        const rdsEgress = eksEgress.filter(
          r => r.protocol === 'tcp' && r.fromPort === 5432 && r.toPort === 5432
            && r.peerType === 'sg' && r.peerValue.includes('DatabaseSecurityGroup'),
        );
        expect(rdsEgress).toHaveLength(1);

        const httpsEgress = eksEgress.filter(
          r => r.protocol === 'tcp' && r.fromPort === 443 && r.toPort === 443
            && r.peerType === 'cidr' && r.peerValue === '0.0.0.0/0',
        );
        expect(httpsEgress).toHaveLength(1);

        const selfEgressRule = eksEgress.filter(
          r => r.protocol === '-1'
            && r.peerType === 'sg' && r.peerValue.includes('EksSecurityGroup'),
        );
        expect(selfEgressRule).toHaveLength(1);
        expect(eksEgress).toHaveLength(3);
      }),
      { numRuns: 100 },
    );
  });

  it('Database security group has exactly the expected ingress and egress rules', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');
    const projectArb = fc.constant('devops-agent-eks');

    fc.assert(
      fc.property(envArb, projectArb, (environment, projectName) => {
        const template = synthesizeNetworkStack(environment, projectName);

        // Database ingress: 3 rules — EKS SG on 5432, 2x compute subnet CIDRs on 5432
        const dbIngress = extractIngressRules(template, 'DatabaseSecurityGroup');
        const eksSgRule = dbIngress.filter(
          r => r.protocol === 'tcp' && r.fromPort === 5432 && r.toPort === 5432
            && r.peerType === 'sg' && r.peerValue.includes('EksSecurityGroup'),
        );
        expect(eksSgRule).toHaveLength(1);

        const cidrRules = dbIngress.filter(
          r => r.protocol === 'tcp' && r.fromPort === 5432 && r.toPort === 5432
            && r.peerType === 'cidr',
        );
        expect(cidrRules).toHaveLength(2);

        expect(dbIngress).toHaveLength(3);

        // Database egress: 1 rule — localhost only (deny-all pattern)
        const dbEgress = extractEgressRules(template, 'DatabaseSecurityGroup');
        const localhostRule = dbEgress.filter(
          r => r.protocol === '-1'
            && r.peerType === 'cidr' && r.peerValue === '127.0.0.1/32',
        );
        expect(localhostRule).toHaveLength(1);
        expect(dbEgress).toHaveLength(1);
      }),
      { numRuns: 100 },
    );
  });

  it('all three security groups use allowAllOutbound: false (no default egress)', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');

    fc.assert(
      fc.property(envArb, (environment) => {
        const template = synthesizeNetworkStack(environment, 'devops-agent-eks');
        const resources = template.toJSON().Resources;

        // Verify no SG has a default 0.0.0.0/0 all-traffic egress rule
        // that CDK would add if allowAllOutbound were true
        for (const [logicalId, resource] of Object.entries(resources as Record<string, any>)) {
          if (resource.Type !== 'AWS::EC2::SecurityGroup') continue;
          const egress = resource.Properties?.SecurityGroupEgress ?? [];
          for (const rule of egress) {
            // A default CDK egress rule would be protocol -1, cidr 0.0.0.0/0
            if (rule.IpProtocol === '-1' && rule.CidrIp === '0.0.0.0/0') {
              fail(`Security group ${logicalId} has default allow-all egress`);
            }
          }
        }
      }),
      { numRuns: 100 },
    );
  });
});
