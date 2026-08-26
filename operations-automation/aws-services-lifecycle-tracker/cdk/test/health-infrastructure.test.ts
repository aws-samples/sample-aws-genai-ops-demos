import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { DataStack } from '../lib/data-stack';
import { AWSServicesLifecycleTrackerScheduler } from '../lib/scheduler-stack';
import { AWSServicesLifecycleTrackerInfraStack } from '../lib/infra-stack';

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

describe('Health Infrastructure - IAM Permissions', () => {
  let infraTemplate: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new AWSServicesLifecycleTrackerInfraStack(app, 'TestInfraStack');
    infraTemplate = Template.fromStack(stack);
  });

  test('Health API permissions include all required actions', () => {
    infraTemplate.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
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

  test('DynamoDB permissions include aws-health-events table', () => {
    infraTemplate.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Sid: 'DynamoDBAccess',
            Effect: 'Allow',
            Action: Match.arrayWith([
              'dynamodb:GetItem',
              'dynamodb:PutItem',
              'dynamodb:UpdateItem',
              'dynamodb:DeleteItem',
              'dynamodb:Query',
              'dynamodb:Scan',
              'dynamodb:BatchGetItem',
              'dynamodb:BatchWriteItem',
            ]),
            Resource: Match.arrayWith([
              {
                'Fn::Join': [
                  '',
                  Match.arrayWith([
                    Match.stringLikeRegexp('.*:table/aws-health-events'),
                  ]),
                ],
              },
            ]),
          }),
        ]),
      },
    });
  });
});

describe('Health Infrastructure - EventBridge Schedule', () => {
  let schedulerTemplate: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new AWSServicesLifecycleTrackerScheduler(app, 'TestSchedulerStack', {
      agentRuntimeArn: 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test-agent',
    });
    schedulerTemplate = Template.fromStack(stack);
  });

  test('Health collection schedule exists with rate(5 minutes)', () => {
    schedulerTemplate.hasResourceProperties('AWS::Scheduler::Schedule', {
      Name: 'aws-health-events-collection',
      ScheduleExpression: 'rate(5 minutes)',
    });
  });

  test('Health schedule targets AgentCore with collect_health_events action', () => {
    schedulerTemplate.hasResourceProperties('AWS::Scheduler::Schedule', {
      Name: 'aws-health-events-collection',
      Target: Match.objectLike({
        Arn: 'arn:aws:scheduler:::aws-sdk:bedrockagentcore:invokeAgentRuntime',
      }),
    });
  });
});
