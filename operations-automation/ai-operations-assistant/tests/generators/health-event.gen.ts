/**
 * fast-check arbitraries for Health Agent event objects.
 * Validates: Requirements 3.3
 */
import fc from 'fast-check';

/** Event type categories from the AWS Health API */
export const arbEventTypeCategory = fc.constantFrom<'issue' | 'accountNotification' | 'scheduledChange'>(
  'issue',
  'accountNotification',
  'scheduledChange',
);

/** Realistic AWS service names */
export const arbServiceName = fc.constantFrom(
  'EC2', 'S3', 'RDS', 'LAMBDA', 'DYNAMODB', 'ECS', 'EKS',
  'CLOUDFRONT', 'ROUTE53', 'IAM', 'SQS', 'SNS', 'KINESIS',
  'ELASTICACHE', 'REDSHIFT', 'SAGEMAKER', 'BEDROCK',
);

/** Realistic AWS region codes */
export const arbRegion = fc.constantFrom(
  'us-east-1', 'us-west-2', 'eu-west-1', 'eu-central-1',
  'ap-southeast-1', 'ap-northeast-1', 'ap-south-1',
  'sa-east-1', 'ca-central-1', 'us-east-2',
);

/** Health event status codes */
export const arbStatusCode = fc.constantFrom('open', 'closed', 'upcoming');

/** ISO timestamp within a reasonable window */
const arbTimestamp = fc
  .date({ min: new Date('2024-01-01'), max: new Date('2025-12-31') })
  .map((d) => d.toISOString());

/** A single AWS Health event */
export const arbHealthEvent = fc.record({
  arn: fc.string({ minLength: 20, maxLength: 80 }).map((s) => `arn:aws:health:us-east-1::event/${s}`),
  service: arbServiceName,
  eventTypeCode: fc.string({ minLength: 5, maxLength: 40 }),
  eventTypeCategory: arbEventTypeCategory,
  region: arbRegion,
  startTime: arbTimestamp,
  endTime: fc.option(arbTimestamp, { nil: undefined }),
  lastUpdatedTime: arbTimestamp,
  statusCode: arbStatusCode,
  eventScopeCode: fc.constantFrom('ACCOUNT_SPECIFIC', 'PUBLIC', 'NONE'),
});

/** HealthEventsParams matching the interface in types.ts */
export const arbHealthEventsParams = fc.record({
  region: fc.option(arbRegion, { nil: undefined }),
  service: fc.option(arbServiceName, { nil: undefined }),
  eventTypeCategory: fc.option(arbEventTypeCategory, { nil: undefined }),
  startTime: fc.option(arbTimestamp, { nil: undefined }),
  endTime: fc.option(arbTimestamp, { nil: undefined }),
});

/** AffectedEntitiesParams */
export const arbAffectedEntitiesParams = fc.record({
  eventArn: fc.string({ minLength: 20, maxLength: 80 }).map((s) => `arn:aws:health:us-east-1::event/${s}`),
});

/** EventDetailsParams */
export const arbEventDetailsParams = fc.record({
  eventArn: fc.string({ minLength: 20, maxLength: 80 }).map((s) => `arn:aws:health:us-east-1::event/${s}`),
});

/** A formatted health event response (what the Health Agent returns) */
export const arbHealthEventResponse = fc.record({
  success: fc.constant(true),
  domain: fc.constant('health'),
  data: fc.record({
    events: fc.array(arbHealthEvent, { minLength: 0, maxLength: 10 }),
  }),
  formattedText: fc.string({ minLength: 1, maxLength: 500 }),
  metadata: fc.record({
    sourceApi: fc.constantFrom(
      'health:DescribeEvents',
      'health:DescribeAffectedEntities',
      'health:DescribeEventDetails',
    ),
    queryTimestamp: arbTimestamp,
    dataFreshness: fc.constant('real-time'),
  }),
});
