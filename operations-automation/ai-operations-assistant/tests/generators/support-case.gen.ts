/**
 * fast-check arbitraries for Support Agent case objects.
 * Validates: Requirements 4.3
 */
import fc from 'fast-check';

/** Support case severity codes */
export const arbSeverity = fc.constantFrom('low', 'normal', 'high', 'urgent', 'critical');

/** Support case status values */
export const arbCaseStatus = fc.constantFrom(
  'unassigned', 'work-in-progress', 'pending-customer-action',
  'customer-action-completed', 'resolved', 'reopened',
);

/** ISO date string */
const arbISODate = fc
  .date({ min: new Date('2023-01-01'), max: new Date('2025-12-31') })
  .map((d) => d.toISOString());

/** AWS service code for support cases */
const arbServiceCode = fc.constantFrom(
  'amazon-ec2', 'amazon-s3', 'amazon-rds', 'aws-lambda',
  'amazon-dynamodb', 'amazon-ecs', 'amazon-bedrock',
  'amazon-cloudfront', 'amazon-vpc', 'general-info',
);

/** A single support case */
export const arbSupportCase = fc.record({
  caseId: fc.stringMatching(/^case-[0-9a-f]{8}$/),
  displayId: fc.stringMatching(/^[0-9]{10}$/),
  subject: fc.string({ minLength: 5, maxLength: 120 }),
  status: arbCaseStatus,
  serviceCode: arbServiceCode,
  severityCode: arbSeverity,
  categoryCode: fc.constantFrom('general-guidance', 'system-impaired', 'production-system-impaired'),
  timeCreated: arbISODate,
  recentCommunications: fc.record({
    communications: fc.array(
      fc.record({
        body: fc.string({ minLength: 10, maxLength: 300 }),
        submittedBy: fc.string({ minLength: 3, maxLength: 40 }),
        timeCreated: arbISODate,
      }),
      { minLength: 0, maxLength: 5 },
    ),
  }),
  language: fc.constantFrom('en', 'ja', 'zh'),
});

/** A formatted support case response (what the Support Agent returns) */
export const arbSupportCaseResponse = fc.record({
  success: fc.constant(true),
  domain: fc.constant('support'),
  data: fc.record({
    cases: fc.array(arbSupportCase, { minLength: 0, maxLength: 10 }),
  }),
  formattedText: fc.string({ minLength: 1, maxLength: 500 }),
  metadata: fc.record({
    sourceApi: fc.constantFrom(
      'support:DescribeCases',
      'support:DescribeCommunications',
      'support:SearchCases',
    ),
    queryTimestamp: arbISODate,
    dataFreshness: fc.constant('real-time'),
  }),
});
