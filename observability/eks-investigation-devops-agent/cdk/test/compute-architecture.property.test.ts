/**
 * Feature: cfn-to-cdk-migration, Property 2: Architecture-dependent configuration mapping
 *
 * Generate random architecture values from {arm64, amd64}, synthesize compute
 * stack, verify AMI type is AL2023_ARM_64_STANDARD or AL2023_x86_64_STANDARD
 * respectively.
 *
 * Validates: Requirements 4.2
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Template } from 'aws-cdk-lib/assertions';
import { ComputeStack } from '../lib/compute-stack';

// ---------------------------------------------------------------------------
// Helper: synthesize ComputeStack with a given architecture
// ---------------------------------------------------------------------------
function synthesizeComputeStack(architecture: string): Template {
  const app = new cdk.App();

  // Create a VPC to extract real subnet/SG references
  const supportStack = new cdk.Stack(app, 'SupportStack', {
    env: { region: 'us-east-1', account: '123456789012' },
  });

  const vpc = new ec2.Vpc(supportStack, 'Vpc', {
    maxAzs: 2,
    natGateways: 1,
    subnetConfiguration: [
      { cidrMask: 24, name: 'Public', subnetType: ec2.SubnetType.PUBLIC },
      { cidrMask: 24, name: 'PrivateCompute', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    ],
  });

  const sg = new ec2.SecurityGroup(supportStack, 'EksSg', {
    vpc,
    allowAllOutbound: false,
  });

  const stack = new ComputeStack(app, 'TestComputeStack', {
    env: { region: 'us-east-1', account: '123456789012' },
    environment: 'dev',
    projectName: 'devops-agent-eks',
    privateComputeSubnets: vpc.selectSubnets({ subnetGroupName: 'PrivateCompute' }).subnets,
    eksSecurityGroup: sg,
    nodeInstanceType: architecture === 'arm64' ? 't4g.medium' : 't3.medium',
    nodeArchitecture: architecture,
    nodeDesiredCapacity: 2,
  });

  return Template.fromStack(stack);
}

// ---------------------------------------------------------------------------
// Property test
// ---------------------------------------------------------------------------
describe('Property 2: Architecture-dependent configuration mapping (ComputeStack)', () => {
  it('EKS node group AMI type matches the architecture parameter', () => {
    const archArb = fc.constantFrom('arm64', 'amd64');

    fc.assert(
      fc.property(archArb, (architecture) => {
        const template = synthesizeComputeStack(architecture);
        const expectedAmiType = architecture === 'arm64'
          ? 'AL2023_ARM_64_STANDARD'
          : 'AL2023_x86_64_STANDARD';

        template.hasResourceProperties('AWS::EKS::Nodegroup', {
          AmiType: expectedAmiType,
        });
      }),
      { numRuns: 100 },
    );
  });

  it('EKS node group name includes arm or amd based on architecture', () => {
    const archArb = fc.constantFrom('arm64', 'amd64');

    fc.assert(
      fc.property(archArb, (architecture) => {
        const template = synthesizeComputeStack(architecture);
        const expectedSuffix = architecture === 'arm64' ? 'arm' : 'amd';

        const resources = template.toJSON().Resources;
        const nodeGroups = Object.values(resources as Record<string, any>).filter(
          (r: any) => r.Type === 'AWS::EKS::Nodegroup',
        );

        expect(nodeGroups).toHaveLength(1);
        const ngName = (nodeGroups[0] as any).Properties.NodegroupName;
        expect(ngName).toContain(`-${expectedSuffix}-node-group`);
      }),
      { numRuns: 100 },
    );
  });
});
