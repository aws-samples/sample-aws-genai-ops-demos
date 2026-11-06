import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { DevOpsAgentSpaceStack } from '../infrastructure/cdk/lib/devops-agent-space-stack';

function createStack(deployEnvironment: 'production' | 'staging' = 'production'): DevOpsAgentSpaceStack {
  const app = new cdk.App();
  return new DevOpsAgentSpaceStack(app, 'TestAgentSpaceStack', {
    env: { account: '123456789012', region: 'us-east-1' },
    projectName: 'health-event-analyzer',
    deployEnvironment,
  });
}

describe('DevOpsAgentSpaceStack', () => {
  let template: Template;

  beforeAll(() => {
    template = Template.fromStack(createStack('production'));
  });

  test('creates exactly one AWS::DevOpsAgent::AgentSpace with operator app enabled', () => {
    template.resourceCountIs('AWS::DevOpsAgent::AgentSpace', 1);
    template.hasResourceProperties('AWS::DevOpsAgent::AgentSpace', {
      Name: 'health-event-analyzer',
      OperatorApp: {
        Iam: {
          OperatorAppRoleArn: Match.anyValue(),
        },
      },
    });
  });

  test('creates the AWS account monitor association scoped to serviceId "aws"', () => {
    template.hasResourceProperties('AWS::DevOpsAgent::Association', {
      ServiceId: 'aws',
      Configuration: {
        Aws: {
          AccountId: '123456789012',
          AccountType: 'monitor',
          AssumableRoleArn: Match.anyValue(),
        },
      },
    });
  });

  test('AgentSpaceRole and OperatorRole are project-prefixed and trust aidevops.amazonaws.com', () => {
    template.hasResourceProperties('AWS::IAM::Role', {
      RoleName: 'health-event-analyzer-AgentSpaceRole',
      AssumeRolePolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Effect: 'Allow',
            Principal: { Service: 'aidevops.amazonaws.com' },
            Action: 'sts:AssumeRole',
          }),
        ]),
      },
    });
    template.hasResourceProperties('AWS::IAM::Role', {
      RoleName: 'health-event-analyzer-OperatorRole',
    });
  });

  test('AgentSpaceRole uses the AIDevOpsAgentAccessPolicy managed policy; OperatorRole uses AIDevOpsOperatorAppAccessPolicy', () => {
    const roles = template.findResources('AWS::IAM::Role');
    const roleValues = Object.values(roles) as any[];

    const findByName = (name: string) => roleValues.find((r) => r.Properties?.RoleName === name);

    const agentSpaceRole = findByName('health-event-analyzer-AgentSpaceRole');
    const operatorRole = findByName('health-event-analyzer-OperatorRole');
    expect(agentSpaceRole).toBeDefined();
    expect(operatorRole).toBeDefined();

    const arnContains = (managedPolicyArns: any[], suffix: string) =>
      managedPolicyArns.some((arn) => JSON.stringify(arn).includes(suffix));

    expect(arnContains(agentSpaceRole.Properties.ManagedPolicyArns, 'AIDevOpsAgentAccessPolicy')).toBe(true);
    expect(arnContains(operatorRole.Properties.ManagedPolicyArns, 'AIDevOpsOperatorAppAccessPolicy')).toBe(true);
  });

  test('creates a Secrets Manager secret for the webhook HMAC key with DESTROY removal policy', () => {
    template.hasResource('AWS::SecretsManager::Secret', {
      DeletionPolicy: 'Delete',
      UpdateReplacePolicy: 'Delete',
    });
  });

  test('creates the webhook provisioner custom resource depending on the Agent Space', () => {
    template.hasResource('Custom::DevOpsAgentWebhook', {
      DependsOn: Match.arrayWith([Match.stringLikeRegexp('SpaceAgentSpace')]),
    });
  });

  test('webhook provisioner Lambda role is scoped to specific aidevops actions (no full aidevops:* wildcard)', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies) as any[];

    let found = false;
    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        if (stmt.Sid === 'DevOpsAgentWebhookLifecycle') {
          found = true;
          const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
          expect(actions).toEqual(
            expect.arrayContaining([
              'aidevops:RegisterService',
              'aidevops:ListServices',
              'aidevops:AssociateService',
              'aidevops:DisassociateService',
              'aidevops:ListAssociations',
            ])
          );
          expect(actions).not.toContain('aidevops:*');
        }
      }
    }
    expect(found).toBe(true);
  });

  test('outputs AgentSpaceId, AgentSpaceArn, WebhookUrl, WebhookSecretArn, AgentSpaceRoleArn, OperatorRoleArn', () => {
    template.hasOutput('AgentSpaceId', {});
    template.hasOutput('AgentSpaceArn', {});
    template.hasOutput('WebhookUrl', {});
    template.hasOutput('WebhookSecretArn', {});
    template.hasOutput('AgentSpaceRoleArn', {});
    template.hasOutput('OperatorRoleArn', {});
  });

  test('webhook provisioner Lambda uses the Node.js 24 runtime', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Runtime: 'nodejs24.x',
      Handler: 'index.handler',
    });
  });
});

describe('DevOpsAgentSpaceStack - log retention matches deployment environment', () => {
  test('production webhook provisioner log group uses 90-day retention', () => {
    const template = Template.fromStack(createStack('production'));
    template.hasResourceProperties('AWS::Logs::LogGroup', {
      RetentionInDays: 90,
    });
  });

  test('staging webhook provisioner log group uses 14-day retention', () => {
    const template = Template.fromStack(createStack('staging'));
    template.hasResourceProperties('AWS::Logs::LogGroup', {
      RetentionInDays: 14,
    });
  });
});
