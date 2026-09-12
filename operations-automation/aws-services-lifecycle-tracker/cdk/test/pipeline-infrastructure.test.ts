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
