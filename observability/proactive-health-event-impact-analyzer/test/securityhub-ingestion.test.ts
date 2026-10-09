/**
 * Tests for the Security Hub findings ingestion feature.
 *
 * Two concerns:
 *  1. CDK assertions on the synthesized template — the new EventBridge rule,
 *     the dedup DynamoDB table, and the scoped IAM grant.
 *  2. Router-logic unit tests — the per-finding filter, the normalizer, and the
 *     execution-name builder.
 *
 * The Router-logic tests MUST NOT import the event-router handler module: it
 * imports @aws-sdk/client-sfn and @aws-sdk/client-dynamodb, neither of which is
 * installed in infrastructure/cdk/node_modules, so importing it would break
 * module resolution and regress the whole suite. Instead, the pure logic is
 * mirrored inline here — exactly as lambda-logic.test.ts mirrors
 * extractCorrelationKey / mapCategoryToPriority.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { HealthEventAnalyzerStack } from '../infrastructure/cdk/lib/health-event-analyzer-stack';

// ─── CDK assertions ─────────────────────────────────────────────────────────

function createTestStack(id: string): HealthEventAnalyzerStack {
  const app = new cdk.App({ context: { environment: 'production' } });
  return new HealthEventAnalyzerStack(app, id, {
    env: { account: '123456789012', region: 'us-east-1' },
    devOpsAgentWebhookUrl: 'https://example.com/webhook',
    devOpsAgentWebhookSecretArn:
      'arn:aws:secretsmanager:us-east-1:123456789012:secret:test-webhook-secret-abc123',
  });
}

describe('Security Hub Ingestion — CDK resources', () => {
  let template: Template;

  beforeAll(() => {
    template = Template.fromStack(createTestStack('SecurityHubCdkStack'));
  });

  test('creates an EventBridge rule for aws.securityhub findings with the nested detail filter', () => {
    template.hasResourceProperties('AWS::Events::Rule', {
      EventPattern: {
        source: ['aws.securityhub'],
        'detail-type': ['Security Hub Findings - Imported'],
        detail: {
          findings: {
            Severity: { Label: ['HIGH', 'CRITICAL'] },
            Workflow: { Status: ['NEW'] },
            RecordState: ['ACTIVE'],
          },
        },
      },
    });
  });

  test('the Security Hub rule targets the Event Router Lambda', () => {
    // The rule must have at least one Lambda target with the long async-retry
    // convention used by the other rules in this stack.
    const rules = template.findResources('AWS::Events::Rule', {
      Properties: {
        EventPattern: {
          source: ['aws.securityhub'],
        },
      },
    });
    const ruleValues = Object.values(rules);
    expect(ruleValues.length).toBe(1);
    const targets = (ruleValues[0] as any).Properties?.Targets;
    expect(Array.isArray(targets)).toBe(true);
    expect(targets.length).toBeGreaterThanOrEqual(1);
    expect(targets[0].RetryPolicy?.MaximumRetryAttempts).toBe(185);
  });

  test('creates the dedup DynamoDB table (PK findingId, TTL ttl, PAY_PER_REQUEST)', () => {
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'health-analyzer-securityhub-dedup',
      KeySchema: [{ AttributeName: 'findingId', KeyType: 'HASH' }],
      TimeToLiveSpecification: { AttributeName: 'ttl', Enabled: true },
      BillingMode: 'PAY_PER_REQUEST',
    });
  });

  test('grants the Event Router dynamodb:PutItem scoped to the dedup table ARN (not wildcard)', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const policyValues = Object.values(policies);

    let foundScoped = false;
    let foundWildcard = false;

    for (const policy of policyValues) {
      const statements = (policy as any).Properties?.PolicyDocument?.Statement;
      if (!Array.isArray(statements)) continue;
      for (const stmt of statements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        if (actions.includes('dynamodb:PutItem')) {
          if (stmt.Resource === '*') {
            foundWildcard = true;
          } else {
            foundScoped = true;
          }
        }
      }
    }

    expect(foundScoped).toBe(true);
    expect(foundWildcard).toBe(false);
  });

  test('keeps the existing Health EventBridge rule intact', () => {
    template.hasResourceProperties('AWS::Events::Rule', {
      EventPattern: {
        source: ['aws.health'],
        'detail-type': ['AWS Health Event', 'AWS Health Abuse Event'],
      },
    });
  });
});

// ─── Router logic (mirrored inline — see file header) ─────────────────────────

interface AsffFinding {
  Id: string;
  Severity?: { Label?: string };
  Workflow?: { Status?: string };
  RecordState?: string;
  Types?: string[];
  GeneratorId?: string;
  Title?: string;
  Description?: string;
  Resources?: Array<{ Id: string; Tags?: Record<string, string>; Region?: string; Type?: string }>;
  AwsAccountId?: string;
}

interface SecurityHubEvent {
  source: string;
  account: string;
  region: string;
  detail: { findings?: AsffFinding[] };
}

const CONTRACT_KEYS = [
  'eventId',
  'service',
  'eventType',
  'category',
  'region',
  'availabilityZone',
  'startTime',
  'endTime',
  'status',
  'description',
  'affectedResources',
  'sourceAccountId',
  'rawEvent',
  'ingestedAt',
].sort();

function findingQualifies(f: AsffFinding): boolean {
  const severity = f.Severity?.Label;
  return (
    (severity === 'HIGH' || severity === 'CRITICAL') &&
    f.Workflow?.Status === 'NEW' &&
    f.RecordState === 'ACTIVE'
  );
}

function normalizeFinding(event: SecurityHubEvent, f: AsffFinding): Record<string, unknown> {
  const category = f.Severity?.Label === 'CRITICAL' ? 'issue' : 'scheduledChange';
  const title = f.Title?.trim();
  const desc = f.Description?.trim();
  const description =
    title && desc ? `${title}. ${desc}` : title || desc || 'Security Hub finding (no description provided)';
  return {
    eventId: f.Id,
    service: 'SecurityHub',
    eventType: f.Types?.[0] ?? f.GeneratorId ?? 'SecurityHubFinding',
    category,
    region: event.region,
    availabilityZone: null,
    startTime: null,
    endTime: null,
    status: f.Workflow?.Status ?? f.RecordState ?? 'NEW',
    description,
    affectedResources: (f.Resources ?? []).map(r => ({
      resourceId: r.Id,
      tags: r.Tags ?? {},
      status: f.Workflow?.Status ?? 'ACTIVE',
    })),
    sourceAccountId: event.account,
    rawEvent: f,
    ingestedAt: new Date().toISOString(),
  };
}

function buildExecutionName(findingId: string): string {
  const prefix = 'securityhub-';
  const timestamp = `-${Date.now()}`;
  const suffix = `-${crypto.randomBytes(4).toString('hex')}`;
  const budget = 80 - prefix.length - timestamp.length - suffix.length;
  const sanitized = findingId.replace(/[^0-9A-Za-z-_]/g, '-').slice(0, Math.max(0, budget));
  return `${prefix}${sanitized}${timestamp}${suffix}`;
}

describe('Security Hub Ingestion — per-finding filter', () => {
  const fixture: SecurityHubEvent = JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, '../events/test-securityhub-findings-event.json'),
      'utf-8'
    )
  );

  test('fixture carries at least one CRITICAL and one HIGH qualifying finding plus a non-qualifying one', () => {
    const findings = fixture.detail.findings ?? [];
    expect(findings.some(f => f.Severity?.Label === 'CRITICAL')).toBe(true);
    expect(findings.some(f => f.Severity?.Label === 'HIGH')).toBe(true);
    expect(findings.some(f => !findingQualifies(f))).toBe(true);
  });

  test('keeps only NEW/ACTIVE/HIGH/CRITICAL findings', () => {
    const findings = fixture.detail.findings ?? [];
    const qualifying = findings.filter(findingQualifies);
    expect(qualifying.length).toBe(2);
    for (const f of qualifying) {
      expect(['HIGH', 'CRITICAL']).toContain(f.Severity?.Label);
      expect(f.Workflow?.Status).toBe('NEW');
      expect(f.RecordState).toBe('ACTIVE');
    }
  });

  test('drops LOW severity', () => {
    expect(findingQualifies({ Id: 'x', Severity: { Label: 'LOW' }, Workflow: { Status: 'NEW' }, RecordState: 'ACTIVE' })).toBe(false);
  });

  test('drops non-NEW workflow status', () => {
    expect(findingQualifies({ Id: 'x', Severity: { Label: 'CRITICAL' }, Workflow: { Status: 'NOTIFIED' }, RecordState: 'ACTIVE' })).toBe(false);
  });

  test('drops ARCHIVED record state', () => {
    expect(findingQualifies({ Id: 'x', Severity: { Label: 'HIGH' }, Workflow: { Status: 'NEW' }, RecordState: 'ARCHIVED' })).toBe(false);
  });
});

describe('Security Hub Ingestion — normalizer', () => {
  const fixture: SecurityHubEvent = JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, '../events/test-securityhub-findings-event.json'),
      'utf-8'
    )
  );
  const qualifying = (fixture.detail.findings ?? []).filter(findingQualifies);

  test('a multi-finding batch yields one normalized input per qualifying finding', () => {
    const normalized = qualifying.map(f => normalizeFinding(fixture, f));
    expect(normalized.length).toBe(2);
  });

  test('each normalized input has EXACTLY the contract key set (no extra keys, no priority)', () => {
    for (const f of qualifying) {
      const keys = Object.keys(normalizeFinding(fixture, f)).sort();
      expect(keys).toEqual(CONTRACT_KEYS);
      expect(keys).not.toContain('priority');
    }
  });

  test('maps the ASFF fields onto the contract correctly', () => {
    const f = qualifying[0];
    const n = normalizeFinding(fixture, f) as any;
    expect(n.eventId).toBe(f.Id);
    expect(n.service).toBe('SecurityHub');
    expect(n.sourceAccountId).toBe(fixture.account);
    expect(n.region).toBe(fixture.region);
    expect(n.startTime).toBeNull();
    expect(n.endTime).toBeNull();
    expect(n.availabilityZone).toBeNull();
    expect(n.affectedResources[0].resourceId).toBe(f.Resources![0].Id);
    expect(n.description.length).toBeGreaterThan(0);
  });

  test('severity maps to the intake category hint (CRITICAL→issue, HIGH→scheduledChange)', () => {
    const critical = qualifying.find(f => f.Severity?.Label === 'CRITICAL')!;
    const high = qualifying.find(f => f.Severity?.Label === 'HIGH')!;
    expect((normalizeFinding(fixture, critical) as any).category).toBe('issue');
    expect((normalizeFinding(fixture, high) as any).category).toBe('scheduledChange');
  });

  test('rawEvent carries the single finding, not the whole batch', () => {
    const f = qualifying[0];
    const n = normalizeFinding(fixture, f) as any;
    expect(n.rawEvent.Id).toBe(f.Id);
    expect(n.rawEvent.findings).toBeUndefined();
  });

  test('a zero-qualifying batch yields zero normalized inputs', () => {
    const empty: SecurityHubEvent = {
      source: 'aws.securityhub',
      account: '123456789012',
      region: 'us-east-1',
      detail: {
        findings: [
          { Id: 'a', Severity: { Label: 'LOW' }, Workflow: { Status: 'NEW' }, RecordState: 'ACTIVE' },
          { Id: 'b', Severity: { Label: 'HIGH' }, Workflow: { Status: 'NEW' }, RecordState: 'ARCHIVED' },
        ],
      },
    };
    const qualifyingCount = (empty.detail.findings ?? []).filter(findingQualifies).length;
    expect(qualifyingCount).toBe(0);
  });
});

describe('Security Hub Ingestion — execution name', () => {
  test('is securityhub-prefixed, SFN-name-legal, and <=80 chars', () => {
    const longArn =
      'arn:aws:securityhub:us-east-1:123456789012:subscription/aws-foundational-security-best-practices/v/1.0.0/Lambda.5/finding/11111111-aaaa-2222-bbbb-333333333333';
    const name = buildExecutionName(longArn);
    expect(name).toMatch(/^securityhub-[0-9A-Za-z_-]+$/);
    expect(name.length).toBeLessThanOrEqual(80);
    expect(name.startsWith('securityhub-')).toBe(true);
  });

  test('produces distinct names across calls (uniqueness within a batch)', () => {
    const id = 'arn:aws:securityhub:us-east-1:123456789012:finding/abc';
    const a = buildExecutionName(id);
    const b = buildExecutionName(id);
    expect(a).not.toBe(b);
  });
});
