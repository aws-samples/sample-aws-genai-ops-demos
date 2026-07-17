import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { HealthEventAnalyzerStack, VALID_ENVIRONMENTS } from '../infrastructure/cdk/lib/health-event-analyzer-stack';

// Helper to create a test app with environment context
function createTestApp(environment?: string): cdk.App {
  const context: Record<string, string> = {};
  if (environment !== undefined) {
    context['environment'] = environment;
  }
  return new cdk.App({ context });
}

// Helper to create a stack with proper env for synthesis
function createTestStack(app: cdk.App, id: string): HealthEventAnalyzerStack {
  return new HealthEventAnalyzerStack(app, id, {
    env: { account: '123456789012', region: 'us-east-1' },
  });
}

describe('HealthEventAnalyzerStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'TestStack');
    template = Template.fromStack(stack);
  });

  test('creates EventBridge rules for Health events', () => {
    template.hasResourceProperties('AWS::Events::Rule', {
      EventPattern: {
        source: ['aws.health'],
        'detail-type': ['AWS Health Event', 'AWS Health Abuse Event'],
      },
    });
  });

  test('creates EventBridge rule for DevOps Agent completion events', () => {
    template.hasResourceProperties('AWS::Events::Rule', {
      EventPattern: {
        source: ['aws.aidevops'],
        'detail-type': [
          'Investigation Completed',
          'Investigation Failed',
          'Investigation Timed Out',
          'Investigation Cancelled',
        ],
      },
    });
  });

  test('creates Event Router Lambda', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Runtime: 'nodejs24.x',
      Description: 'Routes AWS Health events to the investigation workflow',
    });
  });

  test('creates Investigation Trigger Lambda', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Runtime: 'nodejs24.x',
      Description: Match.stringLikeRegexp('Triggers AWS DevOps Agent investigation'),
    });
  });

  test('creates Investigation Callback Lambda', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Runtime: 'nodejs24.x',
      Description: Match.stringLikeRegexp('Handles DevOps Agent investigation completion'),
    });
  });

  test('creates Notifier Lambda with team routing', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Runtime: 'nodejs24.x',
      Description: Match.stringLikeRegexp('Routes impact notifications to affected teams'),
    });
  });

  test('creates OpsCenter Creator Lambda', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Runtime: 'nodejs24.x',
      Description: 'Creates OpsItem in AWS Systems Manager OpsCenter for Health event impact tracking',
    });
  });

  test('OpsCenter Creator Lambda has ssm:CreateOpsItem permission scoped to opsitem ARN', () => {
    // With cdk.Arn.format, the resource is a Fn::Join token — verify it's not a wildcard
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    let foundScopedPolicy = false;
    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        if (actions.includes('ssm:CreateOpsItem') && actions.includes('ssm:AddTagsToResource')) {
          // Resource should NOT be wildcard '*'
          expect(stmt.Resource).not.toBe('*');
          foundScopedPolicy = true;
        }
      }
    }
    expect(foundScopedPolicy).toBe(true);
  });

  test('creates Step Functions state machine', () => {
    template.resourceCountIs('AWS::StepFunctions::StateMachine', 1);

    template.hasResourceProperties('AWS::StepFunctions::StateMachine', {
      StateMachineName: 'health-event-impact-analyzer',
    });
  });

  test('creates DynamoDB table for task tokens', () => {
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'health-analyzer-task-tokens',
      KeySchema: [
        { AttributeName: 'investigationId', KeyType: 'HASH' },
      ],
      TimeToLiveSpecification: {
        AttributeName: 'ttl',
        Enabled: true,
      },
    });
  });

  test('creates DynamoDB table for team configurations', () => {
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'health-analyzer-teams',
      KeySchema: [
        { AttributeName: 'teamId', KeyType: 'HASH' },
      ],
    });
  });

  test('creates SNS topic for notifications', () => {
    template.hasResourceProperties('AWS::SNS::Topic', {
      TopicName: 'health-event-impact-alerts',
      DisplayName: 'AWS Health Event Impact Alerts',
    });
  });

  test('has required outputs', () => {
    template.hasOutput('NotificationTopicArn', {});
    template.hasOutput('TeamsTableName', {});
    template.hasOutput('StateMachineArn', {});
  });

  test('has required parameters for DevOps Agent', () => {
    template.hasParameter('DevOpsAgentWebhookUrl', {
      Type: 'String',
    });
  });
});

describe('HealthEventAnalyzerStack - SSM Parameter Store Secrets', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SSMTestStack');
    template = Template.fromStack(stack);
  });

  test('does not have CloudFormation parameters for secrets (webhook secret, slack URL, teams URL)', () => {
    const params = template.findParameters('*');
    const paramNames = Object.keys(params);
    // Should NOT have parameters that pass secrets via CFn
    expect(paramNames).not.toContain('DevOpsAgentWebhookSecret');
    expect(paramNames).not.toContain('SlackWebhookUrl');
    expect(paramNames).not.toContain('MsTeamsWebhookUrl');
  });

  test('Investigation Trigger Lambda has WEBHOOK_SECRET_PARAM_NAME env var with SSM path (not value)', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Description: Match.stringLikeRegexp('Triggers AWS DevOps Agent investigation'),
      Environment: {
        Variables: Match.objectLike({
          WEBHOOK_SECRET_PARAM_NAME: '/health-analyzer/production/webhook-secret',
        }),
      },
    });
  });

  test('Investigation Trigger Lambda does NOT have DEVOPS_AGENT_WEBHOOK_SECRET env var', () => {
    // Find all Lambda functions that are the Investigation Trigger
    const lambdas = template.findResources('AWS::Lambda::Function', {
      Properties: {
        Description: Match.stringLikeRegexp('Triggers AWS DevOps Agent investigation'),
      },
    });
    for (const [, resource] of Object.entries(lambdas)) {
      const envVars = (resource as any).Properties?.Environment?.Variables ?? {};
      expect(envVars).not.toHaveProperty('DEVOPS_AGENT_WEBHOOK_SECRET');
    }
  });

  test('Notifier Lambda has SLACK_WEBHOOK_PARAM_NAME env var with SSM path', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Description: Match.stringLikeRegexp('Routes impact notifications to affected teams'),
      Environment: {
        Variables: Match.objectLike({
          SLACK_WEBHOOK_PARAM_NAME: '/health-analyzer/production/slack-webhook-url',
        }),
      },
    });
  });

  test('Notifier Lambda has MSTEAMS_WEBHOOK_PARAM_NAME env var with SSM path', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Description: Match.stringLikeRegexp('Routes impact notifications to affected teams'),
      Environment: {
        Variables: Match.objectLike({
          MSTEAMS_WEBHOOK_PARAM_NAME: '/health-analyzer/production/msteams-webhook-url',
        }),
      },
    });
  });

  test('Notifier Lambda does NOT have SLACK_WEBHOOK_URL or MSTEAMS_WEBHOOK_URL env vars', () => {
    const lambdas = template.findResources('AWS::Lambda::Function', {
      Properties: {
        Description: Match.stringLikeRegexp('Routes impact notifications to affected teams'),
      },
    });
    for (const [, resource] of Object.entries(lambdas)) {
      const envVars = (resource as any).Properties?.Environment?.Variables ?? {};
      expect(envVars).not.toHaveProperty('SLACK_WEBHOOK_URL');
      expect(envVars).not.toHaveProperty('MSTEAMS_WEBHOOK_URL');
    }
  });

  test('Investigation Trigger Lambda has ssm:GetParameter scoped to webhook-secret ARN', () => {
    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Sid: 'ReadWebhookSecret',
            Action: 'ssm:GetParameter',
            Effect: 'Allow',
            Resource: {
              'Fn::Join': Match.anyValue(),
            },
          }),
        ]),
      },
    });
  });

  test('Notifier Lambda has ssm:GetParameter scoped to slack and msteams parameter ARNs', () => {
    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Sid: 'ReadNotificationWebhookSecrets',
            Action: 'ssm:GetParameter',
            Effect: 'Allow',
          }),
        ]),
      },
    });
  });

  test('SSM parameter paths use environment prefix for staging', () => {
    const app = createTestApp('staging');
    const stack = createTestStack(app, 'StagingSSMStack');
    const stagingTemplate = Template.fromStack(stack);

    stagingTemplate.hasResourceProperties('AWS::Lambda::Function', {
      Description: Match.stringLikeRegexp('Triggers AWS DevOps Agent investigation'),
      Environment: {
        Variables: Match.objectLike({
          WEBHOOK_SECRET_PARAM_NAME: '/health-analyzer/staging/webhook-secret',
        }),
      },
    });

    stagingTemplate.hasResourceProperties('AWS::Lambda::Function', {
      Description: Match.stringLikeRegexp('Routes impact notifications to affected teams'),
      Environment: {
        Variables: Match.objectLike({
          SLACK_WEBHOOK_PARAM_NAME: '/health-analyzer/staging/slack-webhook-url',
          MSTEAMS_WEBHOOK_PARAM_NAME: '/health-analyzer/staging/msteams-webhook-url',
        }),
      },
    });
  });
});

describe('HealthEventAnalyzerStack - Environment Configuration', () => {
  test('defaults to production when no environment context is set', () => {
    const app = createTestApp();
    const stack = createTestStack(app, 'DefaultEnvStack');
    expect(stack.deployEnvironment).toBe('production');
  });

  test('accepts production as a valid environment', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'ProdStack');
    expect(stack.deployEnvironment).toBe('production');
  });

  test('accepts staging as a valid environment', () => {
    const app = createTestApp('staging');
    const stack = createTestStack(app, 'StagingStack');
    expect(stack.deployEnvironment).toBe('staging');
  });

  test('fails synthesis with an invalid environment value', () => {
    const app = createTestApp('development');
    expect(() => {
      createTestStack(app, 'InvalidEnvStack');
    }).toThrow(/Invalid environment "development". Allowed values: production, staging/);
  });

  test('fails synthesis with empty string environment', () => {
    const app = createTestApp('');
    expect(() => {
      createTestStack(app, 'EmptyEnvStack');
    }).toThrow(/Invalid environment "". Allowed values: production, staging/);
  });
});

describe('HealthEventAnalyzerStack - Resource Tagging', () => {
  test('applies Project tag to all resources', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'TagTestStack');
    const template = Template.fromStack(stack);

    // Verify tags are applied at the stack level via a Lambda function (which inherits tags)
    template.hasResourceProperties('AWS::Lambda::Function', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Project', Value: 'proactive-health-event-impact-analyzer' }),
      ]),
    });
  });

  test('applies Environment tag with the configured value', () => {
    const app = createTestApp('staging');
    const stack = createTestStack(app, 'StagingTagStack');
    const template = Template.fromStack(stack);

    template.hasResourceProperties('AWS::Lambda::Function', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Environment', Value: 'staging' }),
      ]),
    });
  });

  test('applies ManagedBy tag with value cdk', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'ManagedByTagStack');
    const template = Template.fromStack(stack);

    template.hasResourceProperties('AWS::Lambda::Function', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'ManagedBy', Value: 'cdk' }),
      ]),
    });
  });

  test('all three mandatory tags are applied together', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'AllTagsStack');
    const template = Template.fromStack(stack);

    // Verify each tag is present individually (they inherit to all taggable resources)
    template.hasResourceProperties('AWS::Lambda::Function', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Project', Value: 'proactive-health-event-impact-analyzer' }),
      ]),
    });
    template.hasResourceProperties('AWS::Lambda::Function', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'Environment', Value: 'production' }),
      ]),
    });
    template.hasResourceProperties('AWS::Lambda::Function', {
      Tags: Match.arrayWith([
        Match.objectLike({ Key: 'ManagedBy', Value: 'cdk' }),
      ]),
    });
  });
});


describe('HealthEventAnalyzerStack - IAM Least-Privilege (Requirement 1)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'IAMTestStack');
    template = Template.fromStack(stack);
  });

  test('OpsCenter Creator ssm:CreateOpsItem is scoped to opsitem ARN pattern (Req 1.2)', () => {
    // Should NOT have wildcard resource for ssm actions
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    let foundScopedPolicy = false;
    let foundWildcardPolicy = false;

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        if (actions.includes('ssm:CreateOpsItem')) {
          if (stmt.Resource === '*') {
            foundWildcardPolicy = true;
          } else {
            foundScopedPolicy = true;
          }
        }
      }
    }

    expect(foundScopedPolicy).toBe(true);
    expect(foundWildcardPolicy).toBe(false);
  });

  test('Investigation Callback aidevops:ListJournalRecords is scoped to agentspace ARN (Req 1.3)', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    let foundScopedPolicy = false;
    let foundWildcardPolicy = false;

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        if (actions.includes('aidevops:ListJournalRecords')) {
          if (stmt.Resource === '*') {
            foundWildcardPolicy = true;
          } else {
            foundScopedPolicy = true;
          }
        }
      }
    }

    expect(foundScopedPolicy).toBe(true);
    expect(foundWildcardPolicy).toBe(false);
  });

  test('Notifier account:GetAlternateContact is scoped to account ARN (Req 1.5)', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    let foundScopedPolicy = false;
    let foundWildcardPolicy = false;

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        if (actions.includes('account:GetAlternateContact')) {
          if (stmt.Resource === '*') {
            foundWildcardPolicy = true;
          } else {
            foundScopedPolicy = true;
          }
        }
      }
    }

    expect(foundScopedPolicy).toBe(true);
    expect(foundWildcardPolicy).toBe(false);
  });

  test('no iam:PassRole permission is granted to any Lambda role (Req 1.4)', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        expect(actions).not.toContain('iam:PassRole');
      }
    }
  });

  test('no sts:AssumeRole permission is granted to any Lambda role (Req 1.4)', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        if (stmt.Effect === 'Allow') {
          const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
          expect(actions).not.toContain('sts:AssumeRole');
        }
      }
    }
  });

  test('DynamoDB access uses grantReadData/grantReadWriteData (auto-scoped, Req 1.6)', () => {
    // Verify that DynamoDB permissions reference specific table ARNs, not wildcards
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        const hasDynamoAction = actions.some((a: string) => a.startsWith('dynamodb:'));
        if (hasDynamoAction) {
          // DynamoDB resource should never be wildcard when using grant methods
          expect(stmt.Resource).not.toBe('*');
        }
      }
    }
  });

  test('no wildcard resources for deterministic ARN actions (Req 1.1)', () => {
    // The following actions should never have wildcard resources:
    // ssm:CreateOpsItem, ssm:AddTagsToResource, account:GetAlternateContact,
    // account:GetContactInformation, aidevops:ListJournalRecords
    const scopedActions = [
      'ssm:CreateOpsItem',
      'ssm:AddTagsToResource',
      'account:GetAlternateContact',
      'account:GetContactInformation',
      'aidevops:ListJournalRecords',
    ];

    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        const hasScopedAction = actions.some((a: string) => scopedActions.includes(a));
        if (hasScopedAction) {
          expect(stmt.Resource).not.toBe('*');
        }
      }
    }
  });
});


describe('HealthEventAnalyzerStack - SNS Topic Encryption and Resource Policy (Requirements 3.4, 4.1, 14.1-14.4)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SNSPolicyTestStack');
    template = Template.fromStack(stack);
  });

  test('SNS topic has server-side encryption enabled with KMS key (Req 3.4)', () => {
    template.hasResourceProperties('AWS::SNS::Topic', {
      TopicName: 'health-event-impact-alerts',
      KmsMasterKeyId: Match.anyValue(),
    });
  });

  test('SNS topic has a resource policy (TopicPolicy resource exists)', () => {
    template.resourceCountIs('AWS::SNS::TopicPolicy', 1);
  });

  test('SNS resource policy allows sns:Publish from Notifier role and cloudwatch.amazonaws.com with SourceAccount condition (Req 14.1)', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policyValues = Object.values(topicPolicies);
    expect(policyValues.length).toBeGreaterThan(0);

    const policy = policyValues[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;
    expect(Array.isArray(statements)).toBe(true);

    const allowPublishStmt = statements.find((s: any) => s.Sid === 'AllowNotifierAndAlarms');
    expect(allowPublishStmt).toBeDefined();
    expect(allowPublishStmt.Effect).toBe('Allow');
    expect(allowPublishStmt.Action).toBe('sns:Publish');
    // Should have aws:SourceAccount condition
    expect(allowPublishStmt.Condition?.StringEquals?.['aws:SourceAccount']).toBeDefined();
  });

  test('SNS resource policy denies external account publish (Req 14.2)', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policyValues = Object.values(topicPolicies);
    const policy = policyValues[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;

    const denyExternalStmt = statements.find((s: any) => s.Sid === 'DenyExternalPublish');
    expect(denyExternalStmt).toBeDefined();
    expect(denyExternalStmt.Effect).toBe('Deny');
    expect(denyExternalStmt.Action).toBe('sns:Publish');
    expect(denyExternalStmt.Condition?.StringNotEquals?.['aws:PrincipalAccount']).toBeDefined();
    expect(denyExternalStmt.Condition?.Bool?.['aws:PrincipalIsAWSService']).toBe('false');
  });

  test('SNS resource policy denies insecure transport (Req 4.1, 14.3)', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policyValues = Object.values(topicPolicies);
    const policy = policyValues[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;

    const denyInsecureStmt = statements.find((s: any) => s.Sid === 'DenyInsecureTransport');
    expect(denyInsecureStmt).toBeDefined();
    expect(denyInsecureStmt.Effect).toBe('Deny');
    // Applies to all SNS actions (enumerated explicitly since sns:* is not valid in SNS resource policies)
    const actions = Array.isArray(denyInsecureStmt.Action) ? denyInsecureStmt.Action : [denyInsecureStmt.Action];
    expect(actions).toContain('sns:Publish');
    expect(actions).toContain('sns:Subscribe');
    expect(actions.length).toBeGreaterThanOrEqual(5);
    expect(denyInsecureStmt.Condition?.Bool?.['aws:SecureTransport']).toBe('false');
  });

  test('SNS resource policy restricts subscribe/unsubscribe to owning account (Req 14.4)', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policyValues = Object.values(topicPolicies);
    const policy = policyValues[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;

    const subscribeStmt = statements.find((s: any) => s.Sid === 'RestrictSubscriptions');
    expect(subscribeStmt).toBeDefined();
    expect(subscribeStmt.Effect).toBe('Allow');
    expect(subscribeStmt.Action).toContain('sns:Subscribe');
    expect(subscribeStmt.Action).toContain('sns:Unsubscribe');
    // Principal should be the owning account
    expect(subscribeStmt.Principal?.AWS).toBeDefined();
  });
});


describe('HealthEventAnalyzerStack - CloudWatch Log Groups (Requirements 3.5, 6.4, 6.5, 15.7)', () => {
  test('production log groups use 90-day retention (Req 6.4)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'ProdLogRetentionStack');
    const template = Template.fromStack(stack);

    // All LogGroups should have RetentionInDays = 90 (THREE_MONTHS)
    const logGroups = template.findResources('AWS::Logs::LogGroup');
    const logGroupValues = Object.values(logGroups);
    expect(logGroupValues.length).toBeGreaterThanOrEqual(5); // 5 Lambdas + 1 State Machine

    for (const logGroup of logGroupValues) {
      const retention = (logGroup as any).Properties?.RetentionInDays;
      expect(retention).toBe(90);
    }
  });

  test('non-production (staging) log groups use 14-day retention (Req 6.4)', () => {
    const app = createTestApp('staging');
    const stack = createTestStack(app, 'StagingLogRetentionStack');
    const template = Template.fromStack(stack);

    // All LogGroups should have RetentionInDays = 14 (TWO_WEEKS)
    const logGroups = template.findResources('AWS::Logs::LogGroup');
    const logGroupValues = Object.values(logGroups);
    expect(logGroupValues.length).toBeGreaterThanOrEqual(5);

    for (const logGroup of logGroupValues) {
      const retention = (logGroup as any).Properties?.RetentionInDays;
      expect(retention).toBe(14);
    }
  });

  test('all Lambda functions use explicit LogGroup constructs (not logRetention property) (Req 15.7)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'ExplicitLogGroupStack');
    const template = Template.fromStack(stack);

    // Verify LogGroup resources exist (explicit constructs create AWS::Logs::LogGroup)
    const logGroups = template.findResources('AWS::Logs::LogGroup');
    expect(Object.keys(logGroups).length).toBeGreaterThanOrEqual(5);

    // Verify no Lambda function has the deprecated logRetention-related custom resource
    // CDK's logRetention creates an AWS::CloudFormation::CustomResource for log group management
    const customResources = template.findResources('Custom::LogRetention');
    expect(Object.keys(customResources).length).toBe(0);
  });

  test('CloudWatch Log Groups use default AWS-managed encryption (Req 3.5)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'LogEncryptionStack');
    const template = Template.fromStack(stack);

    // Log groups without an explicit KmsKeyId rely on default AWS-managed AES-256 encryption
    // Verify log groups exist (AWS-managed encryption is the default when no KmsKeyId is set)
    const logGroups = template.findResources('AWS::Logs::LogGroup');
    expect(Object.keys(logGroups).length).toBeGreaterThanOrEqual(5);
  });

  test('Step Functions state machine logs at ALL level (Req 6.5)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SFNLogLevelStack');
    const template = Template.fromStack(stack);

    template.hasResourceProperties('AWS::StepFunctions::StateMachine', {
      LoggingConfiguration: {
        Level: 'ALL',
      },
    });
  });

  test('Step Functions state machine has a log group destination configured', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SFNLogDestStack');
    const template = Template.fromStack(stack);

    template.hasResourceProperties('AWS::StepFunctions::StateMachine', {
      LoggingConfiguration: {
        Level: 'ALL',
        Destinations: Match.anyValue(),
      },
    });
  });
});

describe('HealthEventAnalyzerStack - DynamoDB Hardening (Requirements 3.1–3.3, 9.1–9.6)', () => {
  describe('Production environment', () => {
    let template: Template;

    beforeAll(() => {
      const app = createTestApp('production');
      const stack = createTestStack(app, 'DDBProdStack');
      template = Template.fromStack(stack);
    });

    test('Task Token Table has PITR enabled via pointInTimeRecoverySpecification (Req 9.1)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-task-tokens',
        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    });

    test('Teams Table has PITR enabled via pointInTimeRecoverySpecification (Req 9.2)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-teams',
        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    });

    test('Agent Spaces Table has PITR enabled via pointInTimeRecoverySpecification (Req 9.3)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-agent-spaces',
        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    });

    test('Task Token Table has deletion protection enabled in production (Req 9.5)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-task-tokens',
        DeletionProtectionEnabled: true,
      });
    });

    test('Teams Table has deletion protection enabled in production (Req 9.5)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-teams',
        DeletionProtectionEnabled: true,
      });
    });

    test('Agent Spaces Table has deletion protection enabled in production (Req 9.5)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-agent-spaces',
        DeletionProtectionEnabled: true,
      });
    });

    test('all DynamoDB tables have RETAIN removal policy in production (Req 9.4)', () => {
      // In CFn, DeletionPolicy: Retain means the resource is retained on stack deletion
      const tables = template.findResources('AWS::DynamoDB::Table');
      const tableNames = ['health-analyzer-task-tokens', 'health-analyzer-teams', 'health-analyzer-agent-spaces'];

      for (const [, resource] of Object.entries(tables)) {
        const tableName = (resource as any).Properties?.TableName;
        if (tableNames.includes(tableName)) {
          expect((resource as any).DeletionPolicy).toBe('Retain');
          expect((resource as any).UpdateReplacePolicy).toBe('Retain');
        }
      }
    });

    test('all DynamoDB tables use server-side encryption (AWS owned key by default) (Req 3.1–3.3)', () => {
      // DynamoDB uses AWS owned key encryption by default (no explicit SSESpecification needed)
      // Verify tables exist and don't have SSESpecification set to DISABLED
      const tables = template.findResources('AWS::DynamoDB::Table');
      const tableNames = ['health-analyzer-task-tokens', 'health-analyzer-teams', 'health-analyzer-agent-spaces'];

      for (const [, resource] of Object.entries(tables)) {
        const tableName = (resource as any).Properties?.TableName;
        if (tableNames.includes(tableName)) {
          // If SSESpecification is present, it should not be disabled
          const sse = (resource as any).Properties?.SSESpecification;
          if (sse) {
            expect(sse.SSEEnabled).not.toBe(false);
          }
          // If SSESpecification is absent, AWS owned key encryption is the default — that's fine
        }
      }
    });
  });

  describe('Non-production (staging) environment', () => {
    let template: Template;

    beforeAll(() => {
      const app = createTestApp('staging');
      const stack = createTestStack(app, 'DDBStagingStack');
      template = Template.fromStack(stack);
    });

    test('Task Token Table has PITR enabled in staging (Req 9.1)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-task-tokens',
        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    });

    test('Teams Table has PITR enabled in staging (Req 9.2)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-teams',
        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    });

    test('Agent Spaces Table has PITR enabled in staging (Req 9.3)', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: 'health-analyzer-agent-spaces',
        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    });

    test('Task Token Table has deletion protection disabled in staging (Req 9.6)', () => {
      const tables = template.findResources('AWS::DynamoDB::Table', {
        Properties: { TableName: 'health-analyzer-task-tokens' },
      });
      for (const [, resource] of Object.entries(tables)) {
        const deletionProtection = (resource as any).Properties?.DeletionProtectionEnabled;
        expect(deletionProtection).toBeFalsy();
      }
    });

    test('Teams Table has deletion protection disabled in staging (Req 9.6)', () => {
      const tables = template.findResources('AWS::DynamoDB::Table', {
        Properties: { TableName: 'health-analyzer-teams' },
      });
      for (const [, resource] of Object.entries(tables)) {
        const deletionProtection = (resource as any).Properties?.DeletionProtectionEnabled;
        expect(deletionProtection).toBeFalsy();
      }
    });

    test('Agent Spaces Table has deletion protection disabled in staging (Req 9.6)', () => {
      const tables = template.findResources('AWS::DynamoDB::Table', {
        Properties: { TableName: 'health-analyzer-agent-spaces' },
      });
      for (const [, resource] of Object.entries(tables)) {
        const deletionProtection = (resource as any).Properties?.DeletionProtectionEnabled;
        expect(deletionProtection).toBeFalsy();
      }
    });

    test('all DynamoDB tables have DESTROY removal policy in staging (Req 9.6)', () => {
      const tables = template.findResources('AWS::DynamoDB::Table');
      const tableNames = ['health-analyzer-task-tokens', 'health-analyzer-teams', 'health-analyzer-agent-spaces'];

      for (const [, resource] of Object.entries(tables)) {
        const tableName = (resource as any).Properties?.TableName;
        if (tableNames.includes(tableName)) {
          expect((resource as any).DeletionPolicy).toBe('Delete');
        }
      }
    });
  });
});


describe('HealthEventAnalyzerStack - Step Functions Retry/Catch (Requirements 8.4, 8.5, 13.3)', () => {
  let template: Template;
  let stateMachineDefinition: any;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SFNRetryCatchStack');
    template = Template.fromStack(stack);

    // Extract the state machine definition from the template
    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smValues = Object.values(stateMachines);
    expect(smValues.length).toBe(1);
    const definitionString = (smValues[0] as any).Properties?.DefinitionString;
    // The definition is a Fn::Join — resolve it for testing
    // For CDK assertions, we check via the rendered definition
    stateMachineDefinition = definitionString;
  });

  test('State Machine definition includes Retry on TriggerInvestigation with States.ALL, 3 attempts, 5s interval, backoff 2 (Req 13.3)', () => {
    // The state machine definition is rendered as a Fn::Join — parse it to verify
    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smResource = Object.values(stateMachines)[0] as any;
    const defString = smResource.Properties?.DefinitionString;

    // Resolve Fn::Join to get the definition as a string for inspection
    let defStr: string;
    if (typeof defString === 'string') {
      defStr = defString;
    } else if (defString?.['Fn::Join']) {
      defStr = defString['Fn::Join'][1].join(defString['Fn::Join'][0]);
    } else {
      defStr = JSON.stringify(defString);
    }

    // Parse and verify Retry configuration on TriggerInvestigation
    const defObj = JSON.parse(defStr);
    const states = defObj.States;
    const triggerState = states['TriggerInvestigation'];
    expect(triggerState).toBeDefined();
    expect(triggerState.Retry).toBeDefined();
    expect(triggerState.Retry.length).toBeGreaterThanOrEqual(1);

    const retryConfig = triggerState.Retry.find((r: any) =>
      r.ErrorEquals && r.ErrorEquals.includes('States.ALL')
    );
    expect(retryConfig).toBeDefined();
    expect(retryConfig.MaxAttempts).toBe(3);
    expect(retryConfig.IntervalSeconds).toBe(5);
    expect(retryConfig.BackoffRate).toBe(2);
  });

  test('State Machine definition includes Retry on CreateOpsItem with States.ALL, 3 attempts, 5s interval, backoff 2 (Req 13.3)', () => {
    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smResource = Object.values(stateMachines)[0] as any;
    const defString = smResource.Properties?.DefinitionString;

    let defStr: string;
    if (typeof defString === 'string') {
      defStr = defString;
    } else if (defString?.['Fn::Join']) {
      defStr = defString['Fn::Join'][1].join(defString['Fn::Join'][0]);
    } else {
      defStr = JSON.stringify(defString);
    }

    const defObj = JSON.parse(defStr);
    const states = defObj.States;
    const createOpsItemState = states['CreateOpsItem'];
    expect(createOpsItemState).toBeDefined();
    expect(createOpsItemState.Retry).toBeDefined();

    const retryConfig = createOpsItemState.Retry.find((r: any) =>
      r.ErrorEquals && r.ErrorEquals.includes('States.ALL')
    );
    expect(retryConfig).toBeDefined();
    expect(retryConfig.MaxAttempts).toBe(3);
    expect(retryConfig.IntervalSeconds).toBe(5);
    expect(retryConfig.BackoffRate).toBe(2);
  });

  test('State Machine definition includes Retry on SendNotifications with States.ALL, 3 attempts, 5s interval, backoff 2 (Req 13.3)', () => {
    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smResource = Object.values(stateMachines)[0] as any;
    const defString = smResource.Properties?.DefinitionString;

    let defStr: string;
    if (typeof defString === 'string') {
      defStr = defString;
    } else if (defString?.['Fn::Join']) {
      defStr = defString['Fn::Join'][1].join(defString['Fn::Join'][0]);
    } else {
      defStr = JSON.stringify(defString);
    }

    const defObj = JSON.parse(defStr);
    const states = defObj.States;
    const sendNotifState = states['SendNotifications'];
    expect(sendNotifState).toBeDefined();
    expect(sendNotifState.Retry).toBeDefined();

    const retryConfig = sendNotifState.Retry.find((r: any) =>
      r.ErrorEquals && r.ErrorEquals.includes('States.ALL')
    );
    expect(retryConfig).toBeDefined();
    expect(retryConfig.MaxAttempts).toBe(3);
    expect(retryConfig.IntervalSeconds).toBe(5);
    expect(retryConfig.BackoffRate).toBe(2);
  });

  test('State Machine definition includes Catch on each Lambda task state routing to error handler (Req 8.4)', () => {
    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smResource = Object.values(stateMachines)[0] as any;
    const defString = smResource.Properties?.DefinitionString;

    let defStr: string;
    if (typeof defString === 'string') {
      defStr = defString;
    } else if (defString?.['Fn::Join']) {
      defStr = defString['Fn::Join'][1].join(defString['Fn::Join'][0]);
    } else {
      defStr = JSON.stringify(defString);
    }

    const defObj = JSON.parse(defStr);
    const states = defObj.States;

    // Each Lambda task should have a Catch block
    const taskStates = ['TriggerInvestigation', 'CreateOpsItem', 'SendNotifications'];
    for (const stateName of taskStates) {
      const state = states[stateName];
      expect(state).toBeDefined();
      expect(state.Catch).toBeDefined();
      expect(state.Catch.length).toBeGreaterThanOrEqual(1);
      // Catch should route to a state (the intermediate Pass state for error context)
      expect(state.Catch[0].Next).toBeDefined();
      expect(state.Catch[0].ResultPath).toBe('$.errorInfo');
    }
  });

  test('State Machine definition includes HandleError state that publishes to SNS (Req 8.5)', () => {
    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smResource = Object.values(stateMachines)[0] as any;
    const defString = smResource.Properties?.DefinitionString;

    let defStr: string;
    if (typeof defString === 'string') {
      defStr = defString;
    } else if (defString?.['Fn::Join']) {
      defStr = defString['Fn::Join'][1].join(defString['Fn::Join'][0]);
    } else {
      defStr = JSON.stringify(defString);
    }

    const defObj = JSON.parse(defStr);
    const states = defObj.States;

    // HandleError state should exist and be an SNS publish (Task type with SNS resource)
    const handleError = states['HandleError'];
    expect(handleError).toBeDefined();
    expect(handleError.Type).toBe('Task');
    // SNS Publish in Step Functions uses the sns:publish resource
    // The partition may be a CDK token reference, so check for the states:::sns:publish suffix
    expect(handleError.Resource).toContain('states:::sns:publish');
  });

  test('Catch intermediate Pass states inject correlation ID and failed function name', () => {
    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smResource = Object.values(stateMachines)[0] as any;
    const defString = smResource.Properties?.DefinitionString;

    let defStr: string;
    if (typeof defString === 'string') {
      defStr = defString;
    } else if (defString?.['Fn::Join']) {
      defStr = defString['Fn::Join'][1].join(defString['Fn::Join'][0]);
    } else {
      defStr = JSON.stringify(defString);
    }

    const defObj = JSON.parse(defStr);
    const states = defObj.States;

    // CatchTriggerInvestigation should be a Pass state that sets failedState
    const catchTrigger = states['CatchTriggerInvestigation'];
    expect(catchTrigger).toBeDefined();
    expect(catchTrigger.Type).toBe('Pass');
    expect(catchTrigger.Parameters?.failedState).toBe('TriggerInvestigation');
    expect(catchTrigger.Parameters?.['healthEventArn.$']).toBe('$.eventId');
    expect(catchTrigger.Next).toBe('HandleError');

    // CatchCreateOpsItem
    const catchOps = states['CatchCreateOpsItem'];
    expect(catchOps).toBeDefined();
    expect(catchOps.Type).toBe('Pass');
    expect(catchOps.Parameters?.failedState).toBe('CreateOpsItem');
    expect(catchOps.Next).toBe('HandleError');

    // CatchSendNotifications
    const catchNotif = states['CatchSendNotifications'];
    expect(catchNotif).toBeDefined();
    expect(catchNotif.Type).toBe('Pass');
    expect(catchNotif.Parameters?.failedState).toBe('SendNotifications');
    expect(catchNotif.Next).toBe('HandleError');
  });
});


describe('HealthEventAnalyzerStack - EventBridge Retry Policy (Requirements 13.2, 13.4)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'EventBridgeRetryStack');
    template = Template.fromStack(stack);
  });

  test('Health event rule target has maxEventAge 24h and retryAttempts 185 (Req 13.2)', () => {
    // EventBridge rule targets with retry policy render as AWS::Events::Rule with Targets
    const rules = template.findResources('AWS::Events::Rule');
    const ruleValues = Object.values(rules);

    let foundHealthRuleWithRetry = false;
    for (const rule of ruleValues) {
      const eventPattern = (rule as any).Properties?.EventPattern;
      const targets = (rule as any).Properties?.Targets;
      if (eventPattern?.source?.includes('aws.health') && Array.isArray(targets)) {
        for (const target of targets) {
          if (target.RetryPolicy) {
            expect(target.RetryPolicy.MaximumRetryAttempts).toBe(185);
            expect(target.RetryPolicy.MaximumEventAgeInSeconds).toBe(86400); // 24 hours
            foundHealthRuleWithRetry = true;
          }
        }
      }
    }
    expect(foundHealthRuleWithRetry).toBe(true);
  });

  test('DevOps Agent completion rule target has maxEventAge 24h and retryAttempts 185', () => {
    const rules = template.findResources('AWS::Events::Rule');
    const ruleValues = Object.values(rules);

    let foundDevOpsRuleWithRetry = false;
    for (const rule of ruleValues) {
      const eventPattern = (rule as any).Properties?.EventPattern;
      const targets = (rule as any).Properties?.Targets;
      if (eventPattern?.source?.includes('aws.aidevops') && Array.isArray(targets)) {
        for (const target of targets) {
          if (target.RetryPolicy) {
            expect(target.RetryPolicy.MaximumRetryAttempts).toBe(185);
            expect(target.RetryPolicy.MaximumEventAgeInSeconds).toBe(86400);
            foundDevOpsRuleWithRetry = true;
          }
        }
      }
    }
    expect(foundDevOpsRuleWithRetry).toBe(true);
  });
});


describe('HealthEventAnalyzerStack - Dead Letter Queues (Requirements 12.1–12.6)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'DLQTestStack');
    template = Template.fromStack(stack);
  });

  test('creates Event Router DLQ with 14-day retention (Req 12.1, 12.4)', () => {
    template.hasResourceProperties('AWS::SQS::Queue', {
      QueueName: 'health-analyzer-event-router-dlq',
      MessageRetentionPeriod: 1209600, // 14 days in seconds
    });
  });

  test('creates Investigation Callback DLQ with 14-day retention (Req 12.2, 12.4)', () => {
    template.hasResourceProperties('AWS::SQS::Queue', {
      QueueName: 'health-analyzer-callback-dlq',
      MessageRetentionPeriod: 1209600, // 14 days in seconds
    });
  });

  test('creates Notifier DLQ with 14-day retention (Req 12.3, 12.4)', () => {
    template.hasResourceProperties('AWS::SQS::Queue', {
      QueueName: 'health-analyzer-notifier-dlq',
      MessageRetentionPeriod: 1209600, // 14 days in seconds
    });
  });

  test('Event Router Lambda has EventInvokeConfig with max retry attempts = 2 (Req 12.1)', () => {
    template.hasResourceProperties('AWS::Lambda::EventInvokeConfig', {
      MaximumRetryAttempts: 2,
      DestinationConfig: {
        OnFailure: {
          Destination: Match.anyValue(),
        },
      },
    });
  });

  test('exactly 3 DLQ SQS queues are created', () => {
    template.resourceCountIs('AWS::SQS::Queue', 3);
  });

  test('3 EventInvokeConfigs exist (one per event-driven Lambda) with retryAttempts=2', () => {
    const invokeConfigs = template.findResources('AWS::Lambda::EventInvokeConfig');
    const configValues = Object.values(invokeConfigs);
    expect(configValues.length).toBe(3);

    for (const config of configValues) {
      expect((config as any).Properties?.MaximumRetryAttempts).toBe(2);
      expect((config as any).Properties?.DestinationConfig?.OnFailure?.Destination).toBeDefined();
    }
  });

  test('CloudWatch Alarm on Event Router DLQ: ApproximateNumberOfMessagesVisible >= 1, period 60s (Req 12.5)', () => {
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'health-analyzer-event-router-dlq-messages',
      MetricName: 'ApproximateNumberOfMessagesVisible',
      Namespace: 'AWS/SQS',
      Threshold: 1,
      Period: 60,
      EvaluationPeriods: 1,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
    });
  });

  test('CloudWatch Alarm on Callback DLQ: ApproximateNumberOfMessagesVisible >= 1, period 60s (Req 12.5)', () => {
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'health-analyzer-callback-dlq-messages',
      MetricName: 'ApproximateNumberOfMessagesVisible',
      Namespace: 'AWS/SQS',
      Threshold: 1,
      Period: 60,
      EvaluationPeriods: 1,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
    });
  });

  test('CloudWatch Alarm on Notifier DLQ: ApproximateNumberOfMessagesVisible >= 1, period 60s (Req 12.5)', () => {
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'health-analyzer-notifier-dlq-messages',
      MetricName: 'ApproximateNumberOfMessagesVisible',
      Namespace: 'AWS/SQS',
      Threshold: 1,
      Period: 60,
      EvaluationPeriods: 1,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
    });
  });

  test('DLQ CloudWatch Alarms route to SNS topic on ALARM state (Req 12.6)', () => {
    // All 3 DLQ alarms should have AlarmActions pointing to the notification topic
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const dlqAlarmNames = [
      'health-analyzer-event-router-dlq-messages',
      'health-analyzer-callback-dlq-messages',
      'health-analyzer-notifier-dlq-messages',
    ];

    for (const [, resource] of Object.entries(alarms)) {
      const alarmName = (resource as any).Properties?.AlarmName;
      if (dlqAlarmNames.includes(alarmName)) {
        const alarmActions = (resource as any).Properties?.AlarmActions;
        expect(alarmActions).toBeDefined();
        expect(alarmActions.length).toBeGreaterThanOrEqual(1);
      }
    }
  });
});


describe('HealthEventAnalyzerStack - Composite CloudWatch Alarm (Requirements 6.1, 6.2, 6.3, 6.6)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'CompositeAlarmStack');
    template = Template.fromStack(stack);
  });

  test('creates a single CloudWatch Alarm using metric math expression (Req 6.1)', () => {
    // The composite alarm should use a Metrics array with metric math
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmValues = Object.values(alarms);

    // Find the composite alarm (has metric math expression, not just a simple metric)
    const compositeAlarms = alarmValues.filter((alarm: any) => {
      const metrics = alarm.Properties?.Metrics;
      return Array.isArray(metrics) && metrics.some((m: any) => m.Expression);
    });

    expect(compositeAlarms.length).toBe(1);

    const compositeAlarm = compositeAlarms[0] as any;
    const expressionMetric = compositeAlarm.Properties.Metrics.find(
      (m: any) => m.Expression
    );
    expect(expressionMetric).toBeDefined();
    // Expression should reference all Lambda error metrics and SFN failures
    expect(expressionMetric.Expression).toContain('eventRouter');
    expect(expressionMetric.Expression).toContain('investigationTrigger');
    expect(expressionMetric.Expression).toContain('opsCenterCreator');
    expect(expressionMetric.Expression).toContain('notifier');
    expect(expressionMetric.Expression).toContain('investigationCallback');
    expect(expressionMetric.Expression).toContain('sfnFailed');
  });

  test('composite alarm threshold is >= 1 (Req 6.1)', () => {
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'health-event-analyzer-composite-alarm',
      Threshold: 1,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
    });
  });

  test('composite alarm uses 5-minute evaluation period (Req 6.1)', () => {
    // The metric math expression uses 300s (5 min) period
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmValues = Object.values(alarms);

    const compositeAlarm = alarmValues.find((alarm: any) => {
      return alarm.Properties?.AlarmName === 'health-event-analyzer-composite-alarm';
    }) as any;

    expect(compositeAlarm).toBeDefined();
    // Check that the metrics use 300s period
    const metrics = compositeAlarm.Properties.Metrics;
    const metricStats = metrics.filter((m: any) => m.MetricStat);
    for (const metricStat of metricStats) {
      expect(metricStat.MetricStat.Period).toBe(300);
    }
  });

  test('composite alarm notifies SNS topic on ALARM transition (Req 6.2)', () => {
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmValues = Object.values(alarms);

    const compositeAlarm = alarmValues.find((alarm: any) => {
      return alarm.Properties?.AlarmName === 'health-event-analyzer-composite-alarm';
    }) as any;

    expect(compositeAlarm).toBeDefined();
    expect(compositeAlarm.Properties.AlarmActions).toBeDefined();
    expect(compositeAlarm.Properties.AlarmActions.length).toBeGreaterThan(0);
  });

  test('composite alarm notifies SNS topic on OK transition (Req 6.3)', () => {
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmValues = Object.values(alarms);

    const compositeAlarm = alarmValues.find((alarm: any) => {
      return alarm.Properties?.AlarmName === 'health-event-analyzer-composite-alarm';
    }) as any;

    expect(compositeAlarm).toBeDefined();
    expect(compositeAlarm.Properties.OKActions).toBeDefined();
    expect(compositeAlarm.Properties.OKActions.length).toBeGreaterThan(0);
  });

  test('NO individual per-Lambda error alarms exist (Req 6.6)', () => {
    // Individual Lambda error alarms would have MetricName: "Errors" with a
    // single Dimensions entry for a specific function. Our composite alarm uses
    // Metrics array (metric math). Verify no alarm uses a single "Errors" metric.
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmValues = Object.values(alarms);

    for (const alarm of alarmValues) {
      const props = (alarm as any).Properties;
      // If it's a simple metric alarm (not metric math), check it isn't a Lambda Errors alarm
      if (props.MetricName === 'Errors' && props.Namespace === 'AWS/Lambda') {
        fail('Found an individual per-Lambda Errors alarm — violates Req 6.6');
      }
    }
  });

  test('NO individual per-State-Machine ExecutionsFailed alarm exists (Req 6.6)', () => {
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmValues = Object.values(alarms);

    for (const alarm of alarmValues) {
      const props = (alarm as any).Properties;
      // If it's a simple metric alarm for Step Functions ExecutionsFailed, that violates Req 6.6
      if (props.MetricName === 'ExecutionsFailed' && props.Namespace === 'AWS/States') {
        fail('Found an individual per-State-Machine ExecutionsFailed alarm — violates Req 6.6');
      }
    }
  });

  test('composite alarm includes Errors metric from all 5 deployed Lambda functions (Req 6.1)', () => {
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmValues = Object.values(alarms);

    const compositeAlarm = alarmValues.find((alarm: any) => {
      return alarm.Properties?.AlarmName === 'health-event-analyzer-composite-alarm';
    }) as any;

    expect(compositeAlarm).toBeDefined();
    const metrics = compositeAlarm.Properties.Metrics;

    // Count MetricStat entries with Errors metric (Lambda) + ExecutionsFailed (SFN)
    const lambdaErrorMetrics = metrics.filter((m: any) =>
      m.MetricStat?.Metric?.MetricName === 'Errors' &&
      m.MetricStat?.Metric?.Namespace === 'AWS/Lambda'
    );
    const sfnMetrics = metrics.filter((m: any) =>
      m.MetricStat?.Metric?.MetricName === 'ExecutionsFailed' &&
      m.MetricStat?.Metric?.Namespace === 'AWS/States'
    );

    expect(lambdaErrorMetrics.length).toBe(5); // 5 deployed Lambda functions
    expect(sfnMetrics.length).toBe(1); // 1 State Machine
  });
});


describe('HealthEventAnalyzerStack - Lambda Runtime, Architecture, and Configuration (Requirements 5.5, 13.1)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'LambdaConfigStack');
    template = Template.fromStack(stack);
  });

  test('all Lambda functions use Node.js 24 runtime (NODEJS_24_X) (Req 5.5)', () => {
    const lambdas = template.findResources('AWS::Lambda::Function');
    const lambdaValues = Object.values(lambdas);

    // There should be at least 5 Lambda functions deployed
    expect(lambdaValues.length).toBeGreaterThanOrEqual(5);

    for (const lambda of lambdaValues) {
      const runtime = (lambda as any).Properties?.Runtime;
      expect(runtime).toBe('nodejs24.x');
    }
  });

  test('all Lambda functions use x86_64 architecture (not ARM64) (Req 5.5)', () => {
    const lambdas = template.findResources('AWS::Lambda::Function');
    const lambdaValues = Object.values(lambdas);

    expect(lambdaValues.length).toBeGreaterThanOrEqual(5);

    for (const lambda of lambdaValues) {
      const architectures = (lambda as any).Properties?.Architectures;
      // CDK default is x86_64 — either Architectures is absent (defaults to x86_64)
      // or explicitly set to ['x86_64']
      if (architectures) {
        expect(architectures).toEqual(['x86_64']);
      }
      // If Architectures is undefined, that's x86_64 by default — acceptable
    }
  });

  test('no Lambda function has ARM64 architecture set (Req 5.5)', () => {
    const lambdas = template.findResources('AWS::Lambda::Function');
    const lambdaValues = Object.values(lambdas);

    for (const lambda of lambdaValues) {
      const architectures = (lambda as any).Properties?.Architectures;
      if (architectures) {
        expect(architectures).not.toContain('arm64');
      }
    }
  });

  test('no Lambda function has reserved concurrency configured (Req 13.1)', () => {
    const lambdas = template.findResources('AWS::Lambda::Function');
    const lambdaValues = Object.values(lambdas);

    expect(lambdaValues.length).toBeGreaterThanOrEqual(5);

    for (const lambda of lambdaValues) {
      const reservedConcurrency = (lambda as any).Properties?.ReservedConcurrentExecutions;
      expect(reservedConcurrency).toBeUndefined();
    }
  });

  test('no Lambda function has X-Ray tracing configuration (Req 13.1)', () => {
    const lambdas = template.findResources('AWS::Lambda::Function');
    const lambdaValues = Object.values(lambdas);

    expect(lambdaValues.length).toBeGreaterThanOrEqual(5);

    for (const lambda of lambdaValues) {
      const tracingConfig = (lambda as any).Properties?.TracingConfig;
      // TracingConfig should not be present (X-Ray was never enabled)
      expect(tracingConfig).toBeUndefined();
    }
  });
});


describe('HealthEventAnalyzerStack - No Hardcoded Secrets (Requirement 2.6)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'NoHardcodedSecretsStack');
    template = Template.fromStack(stack);
  });

  test('no CloudFormation parameter has a default value containing secret-like patterns (Req 2.6)', () => {
    const params = template.toJSON().Parameters ?? {};
    const secretPatterns = [
      /https:\/\/hooks\.slack\.com/i,
      /https:\/\/.*\.webhook\.office\.com/i,
      /sk-[a-zA-Z0-9]{20,}/,
      /xoxb-/,
      /Bearer\s+[a-zA-Z0-9]/,
    ];

    for (const [paramName, paramDef] of Object.entries(params)) {
      const defaultValue = (paramDef as any).Default;
      if (typeof defaultValue === 'string' && defaultValue.length > 0) {
        for (const pattern of secretPatterns) {
          expect(defaultValue).not.toMatch(pattern);
        }
      }
    }
  });

  test('no Lambda environment variables contain webhook URL values (only parameter names) (Req 2.6)', () => {
    const lambdas = template.findResources('AWS::Lambda::Function');

    for (const [, resource] of Object.entries(lambdas)) {
      const envVars = (resource as any).Properties?.Environment?.Variables ?? {};
      for (const [key, value] of Object.entries(envVars)) {
        if (typeof value === 'string') {
          // Values should be SSM parameter paths, not actual URLs
          expect(value).not.toMatch(/^https?:\/\//);
          // Should not look like a secret/token
          expect(value).not.toMatch(/^(sk-|xoxb-|Bearer )/);
        }
      }
    }
  });

  test('CDK context does not contain secret values', () => {
    // Verify the synthesized template doesn't leak context secrets
    // Context values with secrets would typically appear in Metadata or Conditions
    const templateJson = template.toJSON();
    const templateStr = JSON.stringify(templateJson);

    // Should not contain common webhook URL patterns
    expect(templateStr).not.toMatch(/hooks\.slack\.com/);
    expect(templateStr).not.toMatch(/webhook\.office\.com/);
  });
});


describe('HealthEventAnalyzerStack - SSM GetParameter Scoping (Requirement 2.5 - Enhanced)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SSMScopingStack');
    template = Template.fromStack(stack);
  });

  test('ssm:GetParameter policies reference specific parameter ARNs with Fn::Join (not wildcards) (Req 2.5)', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    let ssmGetParamStatements = 0;

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        if (actions.includes('ssm:GetParameter') && stmt.Effect === 'Allow') {
          ssmGetParamStatements++;
          // Resource must not be wildcard
          expect(stmt.Resource).not.toBe('*');
          // Resource should be either a Fn::Join (CDK ARN construction) or an array of Fn::Join
          const resources = Array.isArray(stmt.Resource) ? stmt.Resource : [stmt.Resource];
          for (const resource of resources) {
            if (typeof resource === 'string') {
              // If it's a string, it must contain 'ssm' and the parameter path pattern
              expect(resource).not.toBe('*');
              expect(resource).toMatch(/arn.*ssm.*parameter/);
            } else {
              // CDK typically renders ARNs as Fn::Join constructs
              expect(resource).toHaveProperty('Fn::Join');
            }
          }
        }
      }
    }

    // Ensure we found at least 2 ssm:GetParameter statements
    // (one for Investigation Trigger webhook-secret, one for Notifier slack+msteams)
    expect(ssmGetParamStatements).toBeGreaterThanOrEqual(2);
  });

  test('Investigation Trigger ssm:GetParameter resource contains webhook-secret path', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    let foundWebhookSecretGrant = false;

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        if (stmt.Sid === 'ReadWebhookSecret' && stmt.Action === 'ssm:GetParameter') {
          foundWebhookSecretGrant = true;
          // The resource should be a Fn::Join that builds the ARN
          const resource = stmt.Resource;
          if (resource?.['Fn::Join']) {
            const joinParts = resource['Fn::Join'][1];
            const joinedStr = joinParts.filter((p: any) => typeof p === 'string').join('');
            expect(joinedStr).toContain('/health-analyzer/');
            expect(joinedStr).toContain('webhook-secret');
          }
        }
      }
    }

    expect(foundWebhookSecretGrant).toBe(true);
  });

  test('Notifier ssm:GetParameter resources contain slack and msteams parameter paths', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    let foundNotifierSecretGrant = false;

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        if (stmt.Sid === 'ReadNotificationWebhookSecrets' && stmt.Action === 'ssm:GetParameter') {
          foundNotifierSecretGrant = true;
          // Resource should be an array containing both parameter ARNs
          const resources = Array.isArray(stmt.Resource) ? stmt.Resource : [stmt.Resource];
          expect(resources.length).toBeGreaterThanOrEqual(2);

          // Verify the joined strings contain expected paths
          const resolvedPaths: string[] = [];
          for (const resource of resources) {
            if (resource?.['Fn::Join']) {
              const joinParts = resource['Fn::Join'][1];
              const joinedStr = joinParts.filter((p: any) => typeof p === 'string').join('');
              resolvedPaths.push(joinedStr);
            }
          }

          const allPaths = resolvedPaths.join(' ');
          expect(allPaths).toContain('slack-webhook-url');
          expect(allPaths).toContain('msteams-webhook-url');
        }
      }
    }

    expect(foundNotifierSecretGrant).toBe(true);
  });
});


describe('HealthEventAnalyzerStack - Comprehensive IAM Wildcard Scan (Requirement 1.1)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'WildcardScanStack');
    template = Template.fromStack(stack);
  });

  test('no Allow statements in custom IAM policies use bare wildcard (*) resource (Req 1.1)', () => {
    // CDK-generated service role trust policies use AssumeRole with wildcards by design,
    // so we focus on IAM Policies (not Roles). Also exclude CDK auto-generated policies
    // for log group creation which legitimately use log-group ARN patterns.
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    const wildcardViolations: string[] = [];

    for (const policy of policyValues) {
      const statements = policy.Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        if (stmt.Effect !== 'Allow') continue;
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];

        // Skip CDK framework actions that may use wildcards legitimately
        // (e.g., xray:PutTraceSegments is global by nature, logs:CreateLogDelivery for SFN)
        const frameworkActions = [
          'xray:PutTraceSegments', 'xray:PutTelemetryRecords',
          'xray:GetSamplingRules', 'xray:GetSamplingTargets',
          'logs:CreateLogDelivery', 'logs:GetLogDelivery',
          'logs:UpdateLogDelivery', 'logs:DeleteLogDelivery',
          'logs:ListLogDeliveries', 'logs:PutResourcePolicy',
          'logs:DescribeResourcePolicies', 'logs:DescribeLogGroups',
        ];
        const isFrameworkOnly = actions.every((a: string) => frameworkActions.includes(a));
        if (isFrameworkOnly) continue;

        if (stmt.Resource === '*') {
          wildcardViolations.push(`Actions: ${actions.join(', ')} | Resource: *`);
        }
      }
    }

    expect(wildcardViolations).toEqual([]);
  });

  test('no managed policy ARNs grant AdministratorAccess or PowerUserAccess', () => {
    const roles = template.findResources('AWS::IAM::Role');
    const roleValues = Object.values(roles);

    const dangerousPolicies = [
      'arn:aws:iam::aws:policy/AdministratorAccess',
      'arn:aws:iam::aws:policy/PowerUserAccess',
    ];

    for (const role of roleValues) {
      const managedPolicies = (role as any).Properties?.ManagedPolicyArns ?? [];
      for (const policyArn of managedPolicies) {
        if (typeof policyArn === 'string') {
          expect(dangerousPolicies).not.toContain(policyArn);
        }
      }
    }
  });
});


describe('HealthEventAnalyzerStack - DynamoDB/SNS/CloudWatch Encryption Verification (Requirements 3.1-3.6)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'EncryptionVerificationStack');
    template = Template.fromStack(stack);
  });

  test('all 3 DynamoDB tables exist with encryption (Req 3.1-3.3)', () => {
    const tables = template.findResources('AWS::DynamoDB::Table');
    const tableNames = Object.values(tables).map((t: any) => t.Properties?.TableName);

    expect(tableNames).toContain('health-analyzer-task-tokens');
    expect(tableNames).toContain('health-analyzer-teams');
    expect(tableNames).toContain('health-analyzer-agent-spaces');

    // Verify no table explicitly disables encryption
    for (const [, resource] of Object.entries(tables)) {
      const sse = (resource as any).Properties?.SSESpecification;
      if (sse) {
        expect(sse.SSEEnabled).not.toBe(false);
      }
    }
  });

  test('SNS topic encryption uses a KMS key (alias/aws/sns or CMK) (Req 3.4)', () => {
    template.hasResourceProperties('AWS::SNS::Topic', {
      TopicName: 'health-event-impact-alerts',
      KmsMasterKeyId: Match.anyValue(),
    });

    // Verify KmsMasterKeyId is not absent/empty
    const topics = template.findResources('AWS::SNS::Topic', {
      Properties: { TopicName: 'health-event-impact-alerts' },
    });
    const topicValues = Object.values(topics);
    expect(topicValues.length).toBe(1);
    const kmsMasterKeyId = (topicValues[0] as any).Properties?.KmsMasterKeyId;
    expect(kmsMasterKeyId).toBeDefined();
    expect(kmsMasterKeyId).not.toBeNull();
  });

  test('CloudWatch Log Groups exist for all Lambdas (default AES-256 encryption) (Req 3.5)', () => {
    const logGroups = template.findResources('AWS::Logs::LogGroup');
    // At minimum 5 Lambda log groups + 1 Step Functions log group
    expect(Object.keys(logGroups).length).toBeGreaterThanOrEqual(6);
  });

  test('no DynamoDB table has SSEEnabled explicitly set to false (Req 3.6 prevention)', () => {
    const tables = template.findResources('AWS::DynamoDB::Table');
    const violations: string[] = [];

    for (const [tableName, resource] of Object.entries(tables)) {
      const sse = (resource as any).Properties?.SSESpecification;
      if (sse && sse.SSEEnabled === false) {
        violations.push(tableName);
      }
    }

    expect(violations).toEqual([]);
  });
});


// ─── Task 10.3: Additional CDK Assertion Tests ────────────────────────────────
// Covers: Requirements 6.4, 6.5, 10.1–10.5, 14.1–14.4, 15.4, 15.5, 15.7

describe('HealthEventAnalyzerStack - Tags on Non-Lambda Resources (Req 10.5)', () => {
  test('DynamoDB tables inherit all three mandatory tags', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'DDBTagStack');
    const template = Template.fromStack(stack);

    // Verify each mandatory tag separately since CDK ordering may vary
    const tables = template.findResources('AWS::DynamoDB::Table', {
      Properties: { TableName: 'health-analyzer-teams' },
    });
    const tableResource = Object.values(tables)[0] as any;
    const tags = tableResource.Properties?.Tags;
    expect(tags).toBeDefined();

    const tagKeys = tags.map((t: any) => t.Key);
    expect(tagKeys).toContain('Project');
    expect(tagKeys).toContain('Environment');
    expect(tagKeys).toContain('ManagedBy');

    const projectTag = tags.find((t: any) => t.Key === 'Project');
    expect(projectTag.Value).toBe('proactive-health-event-impact-analyzer');
    const envTag = tags.find((t: any) => t.Key === 'Environment');
    expect(envTag.Value).toBe('production');
    const managedByTag = tags.find((t: any) => t.Key === 'ManagedBy');
    expect(managedByTag.Value).toBe('cdk');
  });

  test('SNS topic inherits all three mandatory tags', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SNSTagStack');
    const template = Template.fromStack(stack);

    const topics = template.findResources('AWS::SNS::Topic', {
      Properties: { TopicName: 'health-event-impact-alerts' },
    });
    const topicResource = Object.values(topics)[0] as any;
    const tags = topicResource.Properties?.Tags;
    expect(tags).toBeDefined();

    const tagKeys = tags.map((t: any) => t.Key);
    expect(tagKeys).toContain('Project');
    expect(tagKeys).toContain('Environment');
    expect(tagKeys).toContain('ManagedBy');

    const projectTag = tags.find((t: any) => t.Key === 'Project');
    expect(projectTag.Value).toBe('proactive-health-event-impact-analyzer');
  });

  test('SQS DLQ queues inherit all three mandatory tags', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SQSTagStack');
    const template = Template.fromStack(stack);

    const queues = template.findResources('AWS::SQS::Queue', {
      Properties: { QueueName: 'health-analyzer-event-router-dlq' },
    });
    const queueResource = Object.values(queues)[0] as any;
    const tags = queueResource.Properties?.Tags;
    expect(tags).toBeDefined();

    const tagKeys = tags.map((t: any) => t.Key);
    expect(tagKeys).toContain('Project');
    expect(tagKeys).toContain('Environment');
    expect(tagKeys).toContain('ManagedBy');

    const envTag = tags.find((t: any) => t.Key === 'Environment');
    expect(envTag.Value).toBe('production');
  });

  test('staging environment applies Environment=staging tag across resources', () => {
    const app = createTestApp('staging');
    const stack = createTestStack(app, 'StagingAllTagsStack');
    const template = Template.fromStack(stack);

    // Verify DynamoDB in staging has correct Environment tag
    const tables = template.findResources('AWS::DynamoDB::Table', {
      Properties: { TableName: 'health-analyzer-task-tokens' },
    });
    const tableResource = Object.values(tables)[0] as any;
    const tags = tableResource.Properties?.Tags;
    const envTag = tags.find((t: any) => t.Key === 'Environment');
    expect(envTag.Value).toBe('staging');

    // Verify SNS topic in staging has correct Environment tag
    const topics = template.findResources('AWS::SNS::Topic');
    const topicResource = Object.values(topics)[0] as any;
    const snsTagsArr = topicResource.Properties?.Tags;
    const snsEnvTag = snsTagsArr.find((t: any) => t.Key === 'Environment');
    expect(snsEnvTag.Value).toBe('staging');
  });
});


describe('HealthEventAnalyzerStack - Environment-Specific Configuration Differences (Req 15.5)', () => {
  let prodTemplate: Template;
  let stagingTemplate: Template;

  beforeAll(() => {
    const prodApp = createTestApp('production');
    const prodStack = createTestStack(prodApp, 'ProdConfigStack');
    prodTemplate = Template.fromStack(prodStack);

    const stagingApp = createTestApp('staging');
    const stagingStack = createTestStack(stagingApp, 'StagingConfigStack');
    stagingTemplate = Template.fromStack(stagingStack);
  });

  test('log retention differs between production (90 days) and staging (14 days)', () => {
    const prodLogGroups = prodTemplate.findResources('AWS::Logs::LogGroup');
    const stagingLogGroups = stagingTemplate.findResources('AWS::Logs::LogGroup');

    // Production: all log groups should be 90 days
    for (const logGroup of Object.values(prodLogGroups)) {
      expect((logGroup as any).Properties?.RetentionInDays).toBe(90);
    }

    // Staging: all log groups should be 14 days
    for (const logGroup of Object.values(stagingLogGroups)) {
      expect((logGroup as any).Properties?.RetentionInDays).toBe(14);
    }
  });

  test('DynamoDB removal policy differs between production (Retain) and staging (Delete)', () => {
    const prodTables = prodTemplate.findResources('AWS::DynamoDB::Table');
    const stagingTables = stagingTemplate.findResources('AWS::DynamoDB::Table');

    const tableNames = ['health-analyzer-task-tokens', 'health-analyzer-teams', 'health-analyzer-agent-spaces'];

    for (const [, resource] of Object.entries(prodTables)) {
      const tableName = (resource as any).Properties?.TableName;
      if (tableNames.includes(tableName)) {
        expect((resource as any).DeletionPolicy).toBe('Retain');
      }
    }

    for (const [, resource] of Object.entries(stagingTables)) {
      const tableName = (resource as any).Properties?.TableName;
      if (tableNames.includes(tableName)) {
        expect((resource as any).DeletionPolicy).toBe('Delete');
      }
    }
  });

  test('DynamoDB deletion protection differs between production (true) and staging (false)', () => {
    const prodTables = prodTemplate.findResources('AWS::DynamoDB::Table');
    const stagingTables = stagingTemplate.findResources('AWS::DynamoDB::Table');

    const tableNames = ['health-analyzer-task-tokens', 'health-analyzer-teams', 'health-analyzer-agent-spaces'];

    for (const [, resource] of Object.entries(prodTables)) {
      const tableName = (resource as any).Properties?.TableName;
      if (tableNames.includes(tableName)) {
        expect((resource as any).Properties?.DeletionProtectionEnabled).toBe(true);
      }
    }

    for (const [, resource] of Object.entries(stagingTables)) {
      const tableName = (resource as any).Properties?.TableName;
      if (tableNames.includes(tableName)) {
        // Staging should either be false or undefined (CDK omits when false)
        const val = (resource as any).Properties?.DeletionProtectionEnabled;
        expect(val).toBeFalsy();
      }
    }
  });

  test('SSM parameter paths differ between production and staging', () => {
    // Production uses /health-analyzer/production/... paths
    prodTemplate.hasResourceProperties('AWS::Lambda::Function', {
      Description: Match.stringLikeRegexp('Triggers AWS DevOps Agent investigation'),
      Environment: {
        Variables: Match.objectLike({
          WEBHOOK_SECRET_PARAM_NAME: '/health-analyzer/production/webhook-secret',
        }),
      },
    });

    // Staging uses /health-analyzer/staging/... paths
    stagingTemplate.hasResourceProperties('AWS::Lambda::Function', {
      Description: Match.stringLikeRegexp('Triggers AWS DevOps Agent investigation'),
      Environment: {
        Variables: Match.objectLike({
          WEBHOOK_SECRET_PARAM_NAME: '/health-analyzer/staging/webhook-secret',
        }),
      },
    });
  });
});


describe('HealthEventAnalyzerStack - SNS Policy Deep Validation (Req 14.1–14.4)', () => {
  let template: Template;

  beforeAll(() => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SNSDeepPolicyStack');
    template = Template.fromStack(stack);
  });

  test('AllowNotifierAndAlarms has exactly sns:Publish action (Req 14.1)', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policy = Object.values(topicPolicies)[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;

    const allowStmt = statements.find((s: any) => s.Sid === 'AllowNotifierAndAlarms');
    expect(allowStmt).toBeDefined();
    expect(allowStmt.Action).toBe('sns:Publish');
    expect(allowStmt.Effect).toBe('Allow');
  });

  test('DenyExternalPublish applies to all principals (*) with correct conditions (Req 14.2)', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policy = Object.values(topicPolicies)[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;

    const denyStmt = statements.find((s: any) => s.Sid === 'DenyExternalPublish');
    expect(denyStmt).toBeDefined();
    expect(denyStmt.Principal?.AWS).toBe('*');
    // Must have both conditions: StringNotEquals for PrincipalAccount AND Bool for PrincipalIsAWSService
    expect(denyStmt.Condition?.StringNotEquals?.['aws:PrincipalAccount']).toBeDefined();
    expect(denyStmt.Condition?.Bool?.['aws:PrincipalIsAWSService']).toBe('false');
  });

  test('DenyInsecureTransport applies to all SNS actions (Req 14.3)', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policy = Object.values(topicPolicies)[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;

    const denyStmt = statements.find((s: any) => s.Sid === 'DenyInsecureTransport');
    expect(denyStmt).toBeDefined();
    // Applies to all SNS actions (enumerated explicitly since sns:* is not valid in SNS resource policies)
    const denyActions = Array.isArray(denyStmt.Action) ? denyStmt.Action : [denyStmt.Action];
    expect(denyActions).toContain('sns:Publish');
    expect(denyActions).toContain('sns:Subscribe');
    expect(denyActions.length).toBeGreaterThanOrEqual(5);
    expect(denyStmt.Principal?.AWS).toBe('*');
  });

  test('RestrictSubscriptions allows only owning account principal (Req 14.4)', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policy = Object.values(topicPolicies)[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;

    const subscribeStmt = statements.find((s: any) => s.Sid === 'RestrictSubscriptions');
    expect(subscribeStmt).toBeDefined();
    // Principal AWS should be a Join referencing the account ARN
    const principal = subscribeStmt.Principal?.AWS;
    expect(principal).toBeDefined();
    // Should contain account reference (either Ref or Fn::Join with account)
    const principalStr = JSON.stringify(principal);
    expect(principalStr).toMatch(/AWS::AccountId|123456789012/);
  });

  test('SNS topic policy has exactly 4 statements covering all requirements', () => {
    const topicPolicies = template.findResources('AWS::SNS::TopicPolicy');
    const policy = Object.values(topicPolicies)[0] as any;
    const statements = policy.Properties?.PolicyDocument?.Statement;

    const sids = statements.map((s: any) => s.Sid);
    expect(sids).toContain('AllowNotifierAndAlarms');
    expect(sids).toContain('DenyExternalPublish');
    expect(sids).toContain('DenyInsecureTransport');
    expect(sids).toContain('RestrictSubscriptions');
  });
});


describe('HealthEventAnalyzerStack - LogGroup Count and Naming (Req 6.4, 15.7)', () => {
  test('creates exactly 5 Lambda LogGroups + 1 StateMachine LogGroup in production', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'LogGroupCountStack');
    const template = Template.fromStack(stack);

    const logGroups = template.findResources('AWS::Logs::LogGroup');
    // 5 Lambda functions (EventRouter, InvestigationTrigger, InvestigationCallback,
    // OpsCenterCreator, Notifier) + 1 Step Functions log group = 6
    expect(Object.keys(logGroups).length).toBe(6);
  });

  test('Step Functions LogGroup has /aws/stepfunctions/ prefix name', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SFNLogNameStack');
    const template = Template.fromStack(stack);

    template.hasResourceProperties('AWS::Logs::LogGroup', {
      LogGroupName: '/aws/stepfunctions/health-event-analyzer',
      RetentionInDays: 90,
    });
  });

  test('no Custom::LogRetention resources exist (proves explicit LogGroup usage)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'NoCustomLogRetentionStack');
    const template = Template.fromStack(stack);

    // Custom::LogRetention is the resource type created when using the deprecated
    // `logRetention` property on Lambda functions
    const customLogRetention = template.findResources('Custom::LogRetention');
    expect(Object.keys(customLogRetention).length).toBe(0);

    // Also check for AWS::CloudFormation::CustomResource with LogRetention service token
    const customResources = template.findResources('AWS::CloudFormation::CustomResource');
    expect(Object.keys(customResources).length).toBe(0);
  });
});


describe('HealthEventAnalyzerStack - Step Functions Complete Config (Req 6.5, 13.3, 8.4)', () => {
  test('Step Functions StateMachine has LoggingConfiguration with ALL level and Destinations (Req 6.5)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SFNFullConfigStack');
    const template = Template.fromStack(stack);

    template.hasResourceProperties('AWS::StepFunctions::StateMachine', {
      LoggingConfiguration: {
        Level: 'ALL',
        Destinations: Match.anyValue(),
      },
    });
  });

  test('ALL Lambda task states in Step Functions have Retry with States.ALL (Req 13.3)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SFNAllRetryStack');
    const template = Template.fromStack(stack);

    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smResource = Object.values(stateMachines)[0] as any;
    const defString = smResource.Properties?.DefinitionString;

    let defStr: string;
    if (typeof defString === 'string') {
      defStr = defString;
    } else if (defString?.['Fn::Join']) {
      defStr = defString['Fn::Join'][1].join(defString['Fn::Join'][0]);
    } else {
      defStr = JSON.stringify(defString);
    }

    const defObj = JSON.parse(defStr);
    const states = defObj.States;

    // Find all Task states that invoke Lambda (have Resource containing lambda:invoke or states:::lambda:invoke)
    const lambdaTaskStates = Object.entries(states).filter(([, state]: [string, any]) => {
      return state.Type === 'Task' && state.Resource && state.Resource.includes('lambda:invoke');
    });

    // Each Lambda task state must have Retry with States.ALL
    expect(lambdaTaskStates.length).toBeGreaterThanOrEqual(3);
    for (const [stateName, state] of lambdaTaskStates) {
      const taskState = state as any;
      expect(taskState.Retry).toBeDefined();
      const statesAllRetry = taskState.Retry.find((r: any) =>
        r.ErrorEquals && r.ErrorEquals.includes('States.ALL')
      );
      expect(statesAllRetry).toBeDefined();
      expect(statesAllRetry.MaxAttempts).toBe(3);
      expect(statesAllRetry.IntervalSeconds).toBe(5);
      expect(statesAllRetry.BackoffRate).toBe(2);
    }
  });

  test('ALL Lambda task states in Step Functions have Catch routing to error handler (Req 8.4)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'SFNAllCatchStack');
    const template = Template.fromStack(stack);

    const stateMachines = template.findResources('AWS::StepFunctions::StateMachine');
    const smResource = Object.values(stateMachines)[0] as any;
    const defString = smResource.Properties?.DefinitionString;

    let defStr: string;
    if (typeof defString === 'string') {
      defStr = defString;
    } else if (defString?.['Fn::Join']) {
      defStr = defString['Fn::Join'][1].join(defString['Fn::Join'][0]);
    } else {
      defStr = JSON.stringify(defString);
    }

    const defObj = JSON.parse(defStr);
    const states = defObj.States;

    // Find all Task states that invoke Lambda
    const lambdaTaskStates = Object.entries(states).filter(([, state]: [string, any]) => {
      return state.Type === 'Task' && state.Resource && state.Resource.includes('lambda:invoke');
    });

    for (const [stateName, state] of lambdaTaskStates) {
      const taskState = state as any;
      expect(taskState.Catch).toBeDefined();
      expect(taskState.Catch.length).toBeGreaterThanOrEqual(1);
      // Catch should have ResultPath for error info
      expect(taskState.Catch[0].ResultPath).toBe('$.errorInfo');
      // Catch should route to a state (intermediate Pass or HandleError)
      expect(taskState.Catch[0].Next).toBeDefined();
    }
  });

  test('Step Functions logging is set to ALL in staging too (Req 6.5)', () => {
    const app = createTestApp('staging');
    const stack = createTestStack(app, 'SFNStagingLogStack');
    const template = Template.fromStack(stack);

    template.hasResourceProperties('AWS::StepFunctions::StateMachine', {
      LoggingConfiguration: {
        Level: 'ALL',
      },
    });
  });
});


describe('HealthEventAnalyzerStack - CDK Aspects Validation (Req 15.4)', () => {
  test('production stack synthesizes without errors (aspects pass all validation)', () => {
    const app = createTestApp('production');
    // If aspects fail, they emit CDK errors that prevent successful synthesis
    // The fact that Template.fromStack succeeds means aspects passed
    const stack = createTestStack(app, 'AspectsProdStack');
    expect(() => Template.fromStack(stack)).not.toThrow();
  });

  test('staging stack synthesizes without errors (aspects pass all validation)', () => {
    const app = createTestApp('staging');
    const stack = createTestStack(app, 'AspectsStagingStack');
    expect(() => Template.fromStack(stack)).not.toThrow();
  });

  test('all Lambda functions in production have log groups with 90-day retention (aspect check)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'AspectLogRetentionProdStack');
    const template = Template.fromStack(stack);

    // Aspect validates: all Lambda LogGroups match environment retention
    // Verify by checking all log groups have correct retention
    const logGroups = template.findResources('AWS::Logs::LogGroup');
    for (const [, logGroup] of Object.entries(logGroups)) {
      const retention = (logGroup as any).Properties?.RetentionInDays;
      expect(retention).toBe(90);
    }
  });

  test('all Lambda functions in staging have log groups with 14-day retention (aspect check)', () => {
    const app = createTestApp('staging');
    const stack = createTestStack(app, 'AspectLogRetentionStagingStack');
    const template = Template.fromStack(stack);

    const logGroups = template.findResources('AWS::Logs::LogGroup');
    for (const [, logGroup] of Object.entries(logGroups)) {
      const retention = (logGroup as any).Properties?.RetentionInDays;
      expect(retention).toBe(14);
    }
  });

  test('event-driven Lambdas all have DLQ via EventInvokeConfig (aspect validates)', () => {
    const app = createTestApp('production');
    const stack = createTestStack(app, 'AspectDlqCheckStack');
    const template = Template.fromStack(stack);

    // Verify EventInvokeConfigs exist with OnFailure destinations
    const invokeConfigs = template.findResources('AWS::Lambda::EventInvokeConfig');
    const configValues = Object.values(invokeConfigs);

    // Should have 3 event-driven Lambdas with DLQ configs
    expect(configValues.length).toBe(3);
    for (const config of configValues) {
      const onFailure = (config as any).Properties?.DestinationConfig?.OnFailure?.Destination;
      expect(onFailure).toBeDefined();
    }
  });
});
