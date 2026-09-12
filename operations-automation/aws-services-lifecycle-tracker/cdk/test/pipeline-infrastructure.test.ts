import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { DataStack } from '../lib/data-stack';
import { PipelineStack } from '../lib/pipeline-stack';

describe('Data stack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    template = Template.fromStack(new DataStack(app, 'TestDataStack'));
  });

  test('creates the five backend tables and no Health events table', () => {
    for (const name of ['aws-services-lifecycle', 'service-extraction-config', 'service-extraction-state', 'aws-account-inventory', 'deprecation-action-plans']) {
      template.hasResourceProperties('AWS::DynamoDB::Table', { TableName: name });
    }
    template.resourceCountIs('AWS::DynamoDB::Table', 5);
  });
});

describe('Pipeline stack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App({ context: { 'aws:cdk:bundling-stacks': [] } }); // skip pip bundling in unit tests
    const dataStack = new DataStack(app, 'TestDataStack');
    const stack = new PipelineStack(app, 'TestPipelineStack', {
      lifecycleTable: dataStack.lifecycleTable,
      configTable: dataStack.configTable,
      stateTable: dataStack.stateTable,
      inventoryTable: dataStack.inventoryTable,
      actionPlanTable: dataStack.actionPlanTable,
    });
    template = Template.fromStack(stack);
  });

  test('pipeline function is a durable function with a live alias', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'aws-services-lifecycle-pipeline',
      Handler: 'pipeline.handler',
      Runtime: 'python3.14',
      DurableConfig: Match.objectLike({ ExecutionTimeout: 7200, RetentionPeriodInDays: 14 }),
    });
    template.hasResourceProperties('AWS::Lambda::Alias', { Name: 'live' });
  });

  test('API function shares the bundle and runtime', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'aws-services-lifecycle-api',
      Handler: 'api.handler',
      Runtime: 'python3.14',
    });
  });

  test('Health access is limited to the two read calls the scan cross-check needs', () => {
    template.hasResourceProperties('AWS::IAM::ManagedPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Sid: 'HealthAPIAccess',
            Effect: 'Allow',
            Action: ['health:DescribeEvents', 'health:DescribeAffectedEntities'],
            Resource: '*',
          }),
        ]),
      },
    });
  });

  test('Extended Support pricing needs Price List + EC2 instance-type reads only (#142)', () => {
    template.hasResourceProperties('AWS::IAM::ManagedPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Sid: 'ExtendedSupportPricing',
            Action: ['pricing:GetProducts', 'ec2:DescribeInstanceTypes'],
            Resource: '*',
          }),
        ]),
      },
    });
  });

  test('configuration table gets no PutItem/DeleteItem/BatchWriteItem (issue #116 boundary)', () => {
    template.hasResourceProperties('AWS::IAM::ManagedPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Sid: 'DynamoDBConfigReadAndUpdate',
            Action: Match.not(Match.arrayWith(['dynamodb:PutItem'])),
          }),
        ]),
      },
    });
  });

  test('only the weekly refresh schedule remains and it targets the API function', () => {
    template.resourceCountIs('AWS::Scheduler::Schedule', 1);
    template.hasResourceProperties('AWS::Scheduler::Schedule', {
      Name: 'aws-services-lifecycle-weekly-refresh',
      ScheduleExpression: 'rate(7 days)',
      Target: Match.objectLike({ Input: Match.stringLikeRegexp('start_refresh') }),
    });
  });
});

describe('Spoke stack (#144)', () => {
  test('one read-only role trusting the hub pipeline role, with ExternalId when given', () => {
    const { SpokeStack } = require('../lib/spoke-stack');
    const { SCANNER_READ_ACTIONS, HEALTH_READ_ACTIONS } = require('../lib/scan-permissions');
    const app = new cdk.App();
    const template = Template.fromStack(new SpokeStack(app, 'TestSpoke', { hubAccountId: '111111111111', externalId: 'secret-1' }));
    template.resourceCountIs('AWS::IAM::Role', 1);
    template.resourceCountIs('AWS::Lambda::Function', 0);
    template.hasResourceProperties('AWS::IAM::Role', {
      RoleName: 'LifecycleTrackerScanRole',
      AssumeRolePolicyDocument: {
        Statement: [Match.objectLike({
          Principal: { AWS: Match.objectLike({ 'Fn::Join': Match.anyValue() }) },
          Condition: {
            ArnEquals: { 'aws:PrincipalArn': Match.objectLike({ 'Fn::Join': Match.anyValue() }) },
            StringEquals: { 'sts:ExternalId': 'secret-1' },
          },
        })],
      },
    });
    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: { Statement: [Match.objectLike({ Action: [...SCANNER_READ_ACTIONS, ...HEALTH_READ_ACTIONS], Resource: '*' })] },
    });
  });

  test('hub pipeline role is named and may assume the spoke role only', () => {
    const app = new cdk.App({ context: { 'aws:cdk:bundling-stacks': [] } });
    const dataStack = new DataStack(app, 'TestDataStack2');
    const template = Template.fromStack(new PipelineStack(app, 'TestPipelineStack2', {
      lifecycleTable: dataStack.lifecycleTable, configTable: dataStack.configTable, stateTable: dataStack.stateTable,
      inventoryTable: dataStack.inventoryTable, actionPlanTable: dataStack.actionPlanTable,
    }));
    template.hasResourceProperties('AWS::IAM::Role', { RoleName: 'aws-services-lifecycle-pipeline-role' });
    template.hasResourceProperties('AWS::IAM::ManagedPolicy', {
      PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({
        Sid: 'HubAndSpokeScan', Action: 'sts:AssumeRole',
        Resource: Match.objectLike({ 'Fn::Join': Match.arrayWith([Match.arrayWith([Match.stringLikeRegexp('role/LifecycleTrackerScanRole')])]) }),
      })]) },
    });
  });
});

describe('Org stack (#144)', () => {
  const { OrgStack } = require('../lib/org-stack');

  test('service-managed StackSet rolls the spoke role to the org minus the hub', () => {
    const app = new cdk.App();
    const stack = new OrgStack(app, 'TestOrg', {
      env: { account: '111111111111', region: 'eu-central-1' },
      hubAccountId: '111111111111',
      targetOuIds: ['r-abcd', 'ou-abcd-12345678'],
    });
    const template = Template.fromStack(stack);
    template.resourceCountIs('AWS::CloudFormation::StackSet', 1);
    template.hasResourceProperties('AWS::CloudFormation::StackSet', {
      PermissionModel: 'SERVICE_MANAGED',
      AutoDeployment: { Enabled: true, RetainStacksOnAccountRemoval: false },
      StackInstancesGroup: [{
        DeploymentTargets: { OrganizationalUnitIds: ['r-abcd', 'ou-abcd-12345678'], AccountFilterType: 'DIFFERENCE', Accounts: ['111111111111'] },
        Regions: ['eu-central-1'],
      }],
    });
    // The embedded template is the SpokeStack: plain IAM, no CDK bootstrap dependency
    const ss = template.findResources('AWS::CloudFormation::StackSet');
    const body = JSON.parse(Object.values(ss)[0].Properties.TemplateBody);
    const types = Object.values(body.Resources as Record<string, { Type: string }>).map((r) => r.Type).sort();
    expect(types).toEqual(['AWS::IAM::Policy', 'AWS::IAM::Role']);
    expect(body.Rules).toBeUndefined();
    expect(body.Parameters).toBeUndefined();
  });

  test('rejects targets that are not root / OU ids', () => {
    expect(() => new OrgStack(new cdk.App(), 'BadOrg', { hubAccountId: '111111111111', targetOuIds: ['123456789012'] }))
      .toThrow(/not an organization root or OU id/);
  });
});
