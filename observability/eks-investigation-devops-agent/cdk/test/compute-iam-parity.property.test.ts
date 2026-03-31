/**
 * Feature: cfn-to-cdk-migration, Property 3: Security-sensitive configuration parity (IAM)
 *
 * Verify IRSA role trust policies and attached policy statements match the
 * original CloudFormation template (eks-cluster.yaml).
 *
 * Validates: Requirements 4.4, 12.2
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Template } from 'aws-cdk-lib/assertions';
import { ComputeStack } from '../lib/compute-stack';

// ---------------------------------------------------------------------------
// Reference model — IAM policies from original CloudFormation eks-cluster.yaml
// ---------------------------------------------------------------------------

/** Expected managed policies per cluster/node role */
const CLUSTER_ROLE_MANAGED_POLICIES = [
  'AmazonEKSClusterPolicy',
  'AmazonEKSVPCResourceController',
];

const NODE_ROLE_MANAGED_POLICIES = [
  'AmazonEKSWorkerNodePolicy',
  'AmazonEKS_CNI_Policy',
  'AmazonEC2ContainerRegistryReadOnly',
  'AmazonSSMManagedInstanceCore',
];

/** Expected inline policy actions per IRSA role */
interface IrsaPolicyRef {
  roleSuffix: string;
  policyName: string;
  statements: { sid: string; actions: string[] }[];
}

const IRSA_POLICIES: IrsaPolicyRef[] = [
  {
    roleSuffix: 'payment-processor-role',
    policyName: 'PaymentProcessorPolicy',
    statements: [
      { sid: 'SecretsManagerAccess', actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'] },
      { sid: 'KMSDecrypt', actions: ['kms:Decrypt', 'kms:DescribeKey'] },
      { sid: 'SQSAccess', actions: ['sqs:SendMessage', 'sqs:GetQueueUrl', 'sqs:GetQueueAttributes'] },
      { sid: 'CloudWatchLogsAccess', actions: ['logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'] },
    ],
  },
  {
    roleSuffix: 'merchant-gateway-role',
    policyName: 'MerchantGatewayPolicy',
    statements: [
      { sid: 'CloudWatchLogsAccess', actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'] },
    ],
  },
  {
    roleSuffix: 'webhook-service-role',
    policyName: 'WebhookServicePolicy',
    statements: [
      { sid: 'SQSAccess', actions: ['sqs:ReceiveMessage', 'sqs:DeleteMessage', 'sqs:GetQueueUrl', 'sqs:GetQueueAttributes', 'sqs:ChangeMessageVisibility'] },
      { sid: 'SecretsManagerAccess', actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'] },
      { sid: 'KMSDecrypt', actions: ['kms:Decrypt', 'kms:DescribeKey'] },
      { sid: 'CloudWatchLogsAccess', actions: ['logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'] },
    ],
  },
  {
    roleSuffix: 'fluent-bit-role',
    policyName: 'FluentBitCloudWatchPolicy',
    statements: [
      { sid: 'CloudWatchLogsWrite', actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams', 'logs:DescribeLogGroups'] },
    ],
  },
];

// ---------------------------------------------------------------------------
// Helper: synthesize ComputeStack
// ---------------------------------------------------------------------------
function synthesizeComputeStack(environment: string, projectName: string): Template {
  const app = new cdk.App();

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
    environment,
    projectName,
    privateComputeSubnets: vpc.selectSubnets({ subnetGroupName: 'PrivateCompute' }).subnets,
    eksSecurityGroup: sg,
    nodeInstanceType: 't4g.medium',
    nodeArchitecture: 'arm64',
    nodeDesiredCapacity: 2,
  });

  return Template.fromStack(stack);
}

// ---------------------------------------------------------------------------
// Helper: find IAM roles by name pattern in synthesized template
// ---------------------------------------------------------------------------
function findRolesByNamePattern(template: Template, pattern: string): any[] {
  const resources = template.toJSON().Resources;
  return Object.values(resources as Record<string, any>).filter(
    (r: any) =>
      r.Type === 'AWS::IAM::Role' &&
      typeof r.Properties?.RoleName === 'string' &&
      r.Properties.RoleName.includes(pattern),
  );
}

function getManagedPolicyNames(role: any): string[] {
  const arns: any[] = role.Properties?.ManagedPolicyArns ?? [];
  return arns.map((arn: any) => {
    // Plain string ARN: arn:aws:iam::aws:policy/AmazonEKSClusterPolicy
    if (typeof arn === 'string') {
      const parts = arn.split('/');
      return parts[parts.length - 1];
    }
    // CDK Fn::Join ARN: { "Fn::Join": ["", ["arn:", {"Ref":"AWS::Partition"}, ":iam::aws:policy/PolicyName"]] }
    if (arn?.['Fn::Join']) {
      const segments: any[] = arn['Fn::Join'][1] ?? [];
      const lastSegment = segments[segments.length - 1];
      if (typeof lastSegment === 'string' && lastSegment.includes(':iam::aws:policy/')) {
        return lastSegment.split('/').pop() ?? '';
      }
    }
    return '';
  }).filter(Boolean);
}

// ---------------------------------------------------------------------------
// Helper: find inline policies attached to a role
// ---------------------------------------------------------------------------
function findInlinePolicies(template: Template, roleNamePattern: string): any[] {
  const resources = template.toJSON().Resources;
  const policies: any[] = [];

  // Check for inline policies on the role itself (Policies property)
  for (const [, resource] of Object.entries(resources as Record<string, any>)) {
    if (resource.Type !== 'AWS::IAM::Role') continue;
    if (typeof resource.Properties?.RoleName !== 'string') continue;
    if (!resource.Properties.RoleName.includes(roleNamePattern)) continue;

    const inlinePolicies = resource.Properties?.Policies ?? [];
    policies.push(...inlinePolicies);
  }

  // Also check standalone AWS::IAM::Policy resources that reference the role
  for (const [, resource] of Object.entries(resources as Record<string, any>)) {
    if (resource.Type !== 'AWS::IAM::Policy') continue;
    const policyDoc = resource.Properties?.PolicyDocument;
    const policyName = resource.Properties?.PolicyName;
    if (policyDoc && policyName) {
      // Check if this policy is attached to our target role
      const roles = resource.Properties?.Roles ?? [];
      for (const roleRef of roles) {
        const ref = roleRef?.Ref;
        if (ref) {
          // Look up the role by logical ID
          const role = resources[ref];
          if (role?.Properties?.RoleName?.includes(roleNamePattern)) {
            policies.push({ PolicyName: policyName, PolicyDocument: policyDoc });
          }
        }
      }
    }
  }

  return policies;
}

// ---------------------------------------------------------------------------
// Property tests
// ---------------------------------------------------------------------------
describe('Property 3: Security-sensitive configuration parity (ComputeStack IAM)', () => {
  it('EKS cluster role has the correct managed policies', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');

    fc.assert(
      fc.property(envArb, (environment) => {
        const template = synthesizeComputeStack(environment, 'devops-agent-eks');
        const clusterRoles = findRolesByNamePattern(template, 'eks-cluster-role');
        expect(clusterRoles).toHaveLength(1);

        const policyNames = getManagedPolicyNames(clusterRoles[0]);
        for (const expected of CLUSTER_ROLE_MANAGED_POLICIES) {
          expect(policyNames).toContain(expected);
        }
        expect(policyNames).toHaveLength(CLUSTER_ROLE_MANAGED_POLICIES.length);
      }),
      { numRuns: 100 },
    );
  });

  it('EKS node role has the correct managed policies', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');

    fc.assert(
      fc.property(envArb, (environment) => {
        const template = synthesizeComputeStack(environment, 'devops-agent-eks');
        const nodeRoles = findRolesByNamePattern(template, 'eks-node-role');
        expect(nodeRoles).toHaveLength(1);

        const policyNames = getManagedPolicyNames(nodeRoles[0]);
        for (const expected of NODE_ROLE_MANAGED_POLICIES) {
          expect(policyNames).toContain(expected);
        }
        expect(policyNames).toHaveLength(NODE_ROLE_MANAGED_POLICIES.length);
      }),
      { numRuns: 100 },
    );
  });

  it('each IRSA role has the correct inline policy statements', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');

    fc.assert(
      fc.property(envArb, (environment) => {
        const template = synthesizeComputeStack(environment, 'devops-agent-eks');

        for (const irsaRef of IRSA_POLICIES) {
          const policies = findInlinePolicies(template, irsaRef.roleSuffix);
          expect(policies.length).toBeGreaterThanOrEqual(1);

          // Find the matching policy by name
          const matchingPolicy = policies.find(
            (p: any) => p.PolicyName === irsaRef.policyName,
          );
          expect(matchingPolicy).toBeDefined();

          const statements = matchingPolicy.PolicyDocument.Statement;

          // Verify each expected statement exists with correct actions
          for (const expectedStmt of irsaRef.statements) {
            const found = statements.find(
              (s: any) => s.Sid === expectedStmt.sid,
            );
            expect(found).toBeDefined();

            const actualActions = Array.isArray(found.Action)
              ? found.Action.sort()
              : [found.Action];
            const expectedActions = [...expectedStmt.actions].sort();
            expect(actualActions).toEqual(expectedActions);
            expect(found.Effect).toBe('Allow');
          }

          // Verify statement count matches
          expect(statements).toHaveLength(irsaRef.statements.length);
        }
      }),
      { numRuns: 100 },
    );
  });

  it('all IRSA roles use sts:AssumeRoleWithWebIdentity federation', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');

    fc.assert(
      fc.property(envArb, (environment) => {
        const template = synthesizeComputeStack(environment, 'devops-agent-eks');

        for (const irsaRef of IRSA_POLICIES) {
          const roles = findRolesByNamePattern(template, irsaRef.roleSuffix);
          expect(roles).toHaveLength(1);

          const assumePolicy = roles[0].Properties.AssumeRolePolicyDocument;
          expect(assumePolicy).toBeDefined();

          const statements = assumePolicy.Statement;
          expect(statements).toHaveLength(1);

          const stmt = statements[0];
          expect(stmt.Action).toBe('sts:AssumeRoleWithWebIdentity');
          expect(stmt.Effect).toBe('Allow');

          // Verify the principal is a Federated principal (OIDC)
          expect(stmt.Principal?.Federated).toBeDefined();
        }
      }),
      { numRuns: 100 },
    );
  });

  it('cluster and node roles use correct service principals', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');

    fc.assert(
      fc.property(envArb, (environment) => {
        const template = synthesizeComputeStack(environment, 'devops-agent-eks');

        // Cluster role: eks.amazonaws.com
        const clusterRoles = findRolesByNamePattern(template, 'eks-cluster-role');
        const clusterAssumeDoc = clusterRoles[0].Properties.AssumeRolePolicyDocument;
        const clusterPrincipal = clusterAssumeDoc.Statement[0].Principal.Service;
        expect(clusterPrincipal).toBe('eks.amazonaws.com');

        // Node role: ec2.amazonaws.com
        const nodeRoles = findRolesByNamePattern(template, 'eks-node-role');
        const nodeAssumeDoc = nodeRoles[0].Properties.AssumeRolePolicyDocument;
        const nodePrincipal = nodeAssumeDoc.Statement[0].Principal.Service;
        expect(nodePrincipal).toBe('ec2.amazonaws.com');
      }),
      { numRuns: 100 },
    );
  });
});
