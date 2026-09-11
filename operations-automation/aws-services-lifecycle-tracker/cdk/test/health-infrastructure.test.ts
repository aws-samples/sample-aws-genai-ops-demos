import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { DataStack } from '../lib/data-stack';
import { PipelineStack } from '../lib/pipeline-stack';

describe('Health Infrastructure - DynamoDB Table', () => {
  let dataTemplate: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new DataStack(app, 'TestDataStack');
    dataTemplate = Template.fromStack(stack);
  });

  test('aws-health-events table exists with correct partition key (event_arn) and sort key (event_type_category)', () => {
    dataTemplate.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'aws-health-events',
      KeySchema: Match.arrayWith([
        { AttributeName: 'event_arn', KeyType: 'HASH' },
        { AttributeName: 'event_type_category', KeyType: 'RANGE' },
      ]),
      AttributeDefinitions: Match.arrayWith([
        { AttributeName: 'event_arn', AttributeType: 'S' },
        { AttributeName: 'event_type_category', AttributeType: 'S' },
      ]),
    });
  });

  test('aws-health-events table uses PAY_PER_REQUEST billing mode', () => {
    dataTemplate.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'aws-health-events',
      BillingMode: 'PAY_PER_REQUEST',
    });
  });

  test('TTL is enabled on the ttl field', () => {
    dataTemplate.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'aws-health-events',
      TimeToLiveSpecification: {
        AttributeName: 'ttl',
        Enabled: true,
      },
    });
  });

  test('GSI service-index exists with PK service_name and SK start_time', () => {
    dataTemplate.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'aws-health-events',
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({
          IndexName: 'service-index',
          KeySchema: Match.arrayWith([
            { AttributeName: 'service_name', KeyType: 'HASH' },
            { AttributeName: 'start_time', KeyType: 'RANGE' },
          ]),
        }),
      ]),
    });
  });

  test('GSI status-index exists with PK status_code and SK start_time', () => {
    dataTemplate.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'aws-health-events',
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({
          IndexName: 'status-index',
          KeySchema: Match.arrayWith([
            { AttributeName: 'status_code', KeyType: 'HASH' },
            { AttributeName: 'start_time', KeyType: 'RANGE' },
          ]),
        }),
      ]),
    });
  });
});


describe('Pipeline stack - IAM and schedules (issue #139)', () => {
  let pipelineTemplate: Template;

  beforeAll(() => {
    const app = new cdk.App({ context: { 'aws:cdk:bundling-stacks': [] } }); // skip pip bundling in unit tests
    const dataStack = new DataStack(app, 'TestDataStack');
    const stack = new PipelineStack(app, 'TestPipelineStack', {
      lifecycleTable: dataStack.lifecycleTable,
      configTable: dataStack.configTable,
      stateTable: dataStack.stateTable,
      inventoryTable: dataStack.inventoryTable,
      actionPlanTable: dataStack.actionPlanTable,
      healthEventsTable: dataStack.healthEventsTable,
    });
    pipelineTemplate = Template.fromStack(stack);
  });

  test('pipeline function is a durable function with a live alias', () => {
    pipelineTemplate.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'aws-services-lifecycle-pipeline',
      Handler: 'pipeline.handler',
      DurableConfig: Match.objectLike({ ExecutionTimeout: 7200, RetentionPeriodInDays: 14 }),
    });
    pipelineTemplate.hasResourceProperties('AWS::Lambda::Alias', { Name: 'live' });
  });

  test('Health API permissions include all required actions', () => {
    pipelineTemplate.hasResourceProperties('AWS::IAM::ManagedPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Sid: 'HealthAPIAccess',
            Effect: 'Allow',
            Action: Match.arrayWith([
              'health:DescribeEvents',
              'health:DescribeEventDetails',
              'health:DescribeAffectedEntities',
              'health:DescribeEventTypes',
            ]),
            Resource: '*',
          }),
        ]),
      },
    });
  });

  test('configuration table gets no PutItem/DeleteItem/BatchWriteItem (issue #116 boundary)', () => {
    pipelineTemplate.hasResourceProperties('AWS::IAM::ManagedPolicy', {
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

  test('schedules target the API function with router payloads', () => {
    pipelineTemplate.hasResourceProperties('AWS::Scheduler::Schedule', {
      Name: 'aws-health-events-collection',
      ScheduleExpression: 'rate(1 hour)',
      Target: Match.objectLike({ Input: Match.stringLikeRegexp('collect_health_events') }),
    });
    pipelineTemplate.hasResourceProperties('AWS::Scheduler::Schedule', {
      Name: 'aws-services-lifecycle-weekly-refresh',
      ScheduleExpression: 'rate(7 days)',
      Target: Match.objectLike({ Input: Match.stringLikeRegexp('start_refresh') }),
    });
  });
});
