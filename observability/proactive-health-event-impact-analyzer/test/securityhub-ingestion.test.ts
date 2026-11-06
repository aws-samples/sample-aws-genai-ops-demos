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
            Severity: { Label: ['CRITICAL'] },
            Workflow: { Status: ['NEW'] },
            RecordState: ['ACTIVE'],
          },
        },
      },
    });
  });

  test('sets MAX_INVESTIGATIONS_PER_INVOCATION to the default of 10 on the Event Router', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Environment: {
        Variables: {
          MAX_INVESTIGATIONS_PER_INVOCATION: '10',
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
    severity === 'CRITICAL' &&
    f.Workflow?.Status === 'NEW' &&
    f.RecordState === 'ACTIVE'
  );
}

function aggregationKey(f: AsffFinding): string {
  return f.Resources?.[0]?.Id ?? f.Id;
}

function groupByAggregationKey(findings: AsffFinding[]): AsffFinding[][] {
  const groups: AsffFinding[][] = [];
  const groupIndexByKey = new Map<string, number>();
  for (const f of findings) {
    const key = aggregationKey(f);
    const existingIndex = groupIndexByKey.get(key);
    if (existingIndex === undefined) {
      groupIndexByKey.set(key, groups.length);
      groups.push([f]);
    } else {
      groups[existingIndex].push(f);
    }
  }
  return groups;
}

function normalizeFinding(event: SecurityHubEvent, f: AsffFinding): Record<string, unknown> {
  const category = 'issue';
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

function normalizeAggregatedFindings(
  event: SecurityHubEvent,
  group: AsffFinding[],
  key: string
): Record<string, unknown> {
  const representative = group[0];
  const base = normalizeFinding(event, representative);

  const dedupedResources = Array.from(
    new Map(
      group.flatMap(f => f.Resources ?? []).map(r => [
        r.Id,
        { resourceId: r.Id, tags: r.Tags ?? {}, status: representative.Workflow?.Status ?? 'ACTIVE' },
      ])
    ).values()
  );

  const title = representative.Title?.trim();
  const desc = representative.Description?.trim();
  const summary = title && desc ? `${title}. ${desc}` : title || desc || 'No description available';

  return {
    ...base,
    eventId: `securityhub-aggregate:${key}`,
    description: `${group.length} CRITICAL Security Hub finding(s) on ${key}. ${summary}${group.length > 1 ? ` (and ${group.length - 1} more)` : ''}`,
    affectedResources: dedupedResources,
    rawEvent: group,
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

  test('fixture carries at least one CRITICAL qualifying finding and at least one now-non-qualifying HIGH finding', () => {
    const findings = fixture.detail.findings ?? [];
    expect(findings.some(f => f.Severity?.Label === 'CRITICAL' && findingQualifies(f))).toBe(true);
    expect(findings.some(f => f.Severity?.Label === 'HIGH' && !findingQualifies(f))).toBe(true);
    expect(findings.some(f => !findingQualifies(f))).toBe(true);
  });

  test('keeps only NEW/ACTIVE/CRITICAL findings', () => {
    const findings = fixture.detail.findings ?? [];
    const qualifying = findings.filter(findingQualifies);
    // Fixture findings 1, 4, 5 are CRITICAL/NEW/ACTIVE.
    expect(qualifying.length).toBe(3);
    for (const f of qualifying) {
      expect(f.Severity?.Label).toBe('CRITICAL');
      expect(f.Workflow?.Status).toBe('NEW');
      expect(f.RecordState).toBe('ACTIVE');
    }
    expect(qualifying.some(f => f.Severity?.Label === 'HIGH')).toBe(false);
  });

  test('drops LOW severity', () => {
    expect(findingQualifies({ Id: 'x', Severity: { Label: 'LOW' }, Workflow: { Status: 'NEW' }, RecordState: 'ACTIVE' })).toBe(false);
  });

  test('drops non-NEW workflow status', () => {
    expect(findingQualifies({ Id: 'x', Severity: { Label: 'CRITICAL' }, Workflow: { Status: 'NOTIFIED' }, RecordState: 'ACTIVE' })).toBe(false);
  });

  test('drops ARCHIVED record state', () => {
    expect(findingQualifies({ Id: 'x', Severity: { Label: 'CRITICAL' }, Workflow: { Status: 'NEW' }, RecordState: 'ARCHIVED' })).toBe(false);
  });

  test('drops HIGH severity (Fix 2)', () => {
    expect(findingQualifies({ Id: 'x', Severity: { Label: 'HIGH' }, Workflow: { Status: 'NEW' }, RecordState: 'ACTIVE' })).toBe(false);
  });

  test('drops a qualifying-severity finding with no Id', () => {
    const f: AsffFinding = { Id: '', Severity: { Label: 'CRITICAL' }, Workflow: { Status: 'NEW' }, RecordState: 'ACTIVE' };
    const survives = findingQualifies(f) && !!f.Id;
    expect(survives).toBe(false);
  });
});

describe('Security Hub Ingestion — aggregation', () => {
  const fixture: SecurityHubEvent = JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, '../events/test-securityhub-findings-event.json'),
      'utf-8'
    )
  );
  const qualifying = (fixture.detail.findings ?? []).filter(findingQualifies);

  test("groups two qualifying findings on the same resource into one bucket", () => {
    const groups = groupByAggregationKey(qualifying);
    const lambdaArn = 'arn:aws:lambda:us-east-1:123456789012:function:LookupCIEventsLambdaFunctionName';
    const lambdaGroup = groups.find(g => aggregationKey(g[0]) === lambdaArn);
    expect(lambdaGroup).toBeDefined();
    expect(lambdaGroup!.length).toBe(2);
  });

  test('produces separate buckets for findings on different resources', () => {
    const groups = groupByAggregationKey(qualifying);
    // 3 qualifying findings: 2 on the Lambda ARN, 1 on the RDS ARN → 2 groups.
    expect(groups.length).toBe(2);
  });

  test("falls back to grouping by the finding's own Id when Resources[] is empty", () => {
    const f: AsffFinding = { Id: 'arn:aws:securityhub:us-east-1:123456789012:finding/no-resources', Resources: [] };
    expect(aggregationKey(f)).toBe(f.Id);
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
  const groups = groupByAggregationKey(qualifying);

  test('a multi-finding batch yields one normalized input per aggregate group', () => {
    const normalized = groups.map(g => normalizeAggregatedFindings(fixture, g, aggregationKey(g[0])));
    expect(normalized.length).toBe(2);
  });

  test('each normalized input has EXACTLY the contract key set (no extra keys, no priority)', () => {
    for (const g of groups) {
      const keys = Object.keys(normalizeAggregatedFindings(fixture, g, aggregationKey(g[0]))).sort();
      expect(keys).toEqual(CONTRACT_KEYS);
      expect(keys).not.toContain('priority');
    }
  });

  test('maps the ASFF fields onto the contract correctly', () => {
    const g = groups[0];
    const n = normalizeAggregatedFindings(fixture, g, aggregationKey(g[0])) as any;
    expect(n.service).toBe('SecurityHub');
    expect(n.sourceAccountId).toBe(fixture.account);
    expect(n.region).toBe(fixture.region);
    expect(n.startTime).toBeNull();
    expect(n.endTime).toBeNull();
    expect(n.availabilityZone).toBeNull();
    expect(n.affectedResources[0].resourceId).toBe(g[0].Resources![0].Id);
    expect(n.description.length).toBeGreaterThan(0);
  });

  test('category is always issue post-Fix-2 (no scheduledChange)', () => {
    for (const g of groups) {
      const n = normalizeAggregatedFindings(fixture, g, aggregationKey(g[0])) as any;
      expect(n.category).toBe('issue');
    }
  });

  test('eventId is the aggregation-key-prefixed form, stable across repeated calls', () => {
    const g = groups[0];
    const key = aggregationKey(g[0]);
    const n1 = normalizeAggregatedFindings(fixture, g, key) as any;
    const n2 = normalizeAggregatedFindings(fixture, g, key) as any;
    expect(n1.eventId).toBe(`securityhub-aggregate:${key}`);
    expect(n1.eventId).toBe(n2.eventId);
  });

  test('affectedResources is the deduplicated union across all findings in a multi-finding group', () => {
    const multiGroup = groups.find(g => g.length > 1)!;
    const key = aggregationKey(multiGroup[0]);
    const n = normalizeAggregatedFindings(fixture, multiGroup, key) as any;
    const expectedIds = Array.from(new Set(multiGroup.flatMap(f => (f.Resources ?? []).map(r => r.Id))));
    const actualIds = n.affectedResources.map((r: any) => r.resourceId);
    expect(new Set(actualIds)).toEqual(new Set(expectedIds));
    expect(actualIds.length).toBe(new Set(actualIds).size);
  });

  test('description mentions the group size when > 1 and omits it for a single-finding group', () => {
    const multiGroup = groups.find(g => g.length > 1)!;
    const singleGroup = groups.find(g => g.length === 1)!;
    const multiN = normalizeAggregatedFindings(fixture, multiGroup, aggregationKey(multiGroup[0])) as any;
    const singleN = normalizeAggregatedFindings(fixture, singleGroup, aggregationKey(singleGroup[0])) as any;
    expect(multiN.description).toContain('more');
    expect(singleN.description).not.toContain('more');
  });

  test('rawEvent is an array containing every finding in the group, not the single-finding shape', () => {
    const multiGroup = groups.find(g => g.length > 1)!;
    const n = normalizeAggregatedFindings(fixture, multiGroup, aggregationKey(multiGroup[0])) as any;
    expect(Array.isArray(n.rawEvent)).toBe(true);
    expect(n.rawEvent.length).toBe(multiGroup.length);
  });

  test('a size-1 group still receives the securityhub-aggregate: prefix, not the bare finding Id', () => {
    const singleGroup = groups.find(g => g.length === 1)!;
    const key = aggregationKey(singleGroup[0]);
    const n = normalizeAggregatedFindings(fixture, singleGroup, key) as any;
    expect(n.eventId).not.toBe(singleGroup[0].Id);
    expect(n.eventId).toBe(`securityhub-aggregate:${key}`);
  });

  test('the 14-key contract holds for an aggregate exactly as for a single finding', () => {
    const singleGroup = groups.find(g => g.length === 1)!;
    const key = aggregationKey(singleGroup[0]);
    const keys = Object.keys(normalizeAggregatedFindings(fixture, singleGroup, key)).sort();
    expect(keys).toEqual(CONTRACT_KEYS);
    expect(keys).not.toContain('priority');
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

describe('Security Hub Ingestion — numeric cap', () => {
  function makeFinding(id: string, resourceArn: string): AsffFinding {
    return {
      Id: id,
      Severity: { Label: 'CRITICAL' },
      Workflow: { Status: 'NEW' },
      RecordState: 'ACTIVE',
      Resources: [{ Id: resourceArn }],
    };
  }

  // Mirrors the cap-enforcement loop in handleSecurityHubEvent: iterate groups
  // in order, stop starting once `cap` executions have started, and report how
  // many groups were never reached.
  function runWithCap(groups: AsffFinding[][], cap: number): { started: number; skipped: number } {
    let started = 0;
    let skipped = 0;
    for (let i = 0; i < groups.length; i++) {
      if (started === cap) {
        skipped = groups.length - i;
        break;
      }
      started++;
    }
    return { started, skipped };
  }

  const threeGroups = groupByAggregationKey([
    makeFinding('f1', 'arn:aws:lambda:us-east-1:123456789012:function:One'),
    makeFinding('f2', 'arn:aws:lambda:us-east-1:123456789012:function:Two'),
    makeFinding('f3', 'arn:aws:lambda:us-east-1:123456789012:function:Three'),
  ]);

  test('a cap of 2 against 3 groups starts 2 and skips 1', () => {
    const { started, skipped } = runWithCap(threeGroups, 2);
    expect(started).toBe(2);
    expect(skipped).toBe(1);
  });

  test('a cap at or above the group count starts every group and skips none', () => {
    const { started, skipped } = runWithCap(threeGroups, 3);
    expect(started).toBe(3);
    expect(skipped).toBe(0);
  });
});

describe('Security Hub Ingestion — dedup key', () => {
  test('a multi-finding group on the same resource attempts exactly one dedup key, not one per finding', () => {
    const sameResourceFindings: AsffFinding[] = [
      { Id: 'f1', Severity: { Label: 'CRITICAL' }, Workflow: { Status: 'NEW' }, RecordState: 'ACTIVE', Resources: [{ Id: 'arn:aws:lambda:us-east-1:123456789012:function:Shared' }] },
      { Id: 'f2', Severity: { Label: 'CRITICAL' }, Workflow: { Status: 'NEW' }, RecordState: 'ACTIVE', Resources: [{ Id: 'arn:aws:lambda:us-east-1:123456789012:function:Shared' }] },
    ];
    const groups = groupByAggregationKey(sameResourceFindings);
    expect(groups.length).toBe(1);

    // Simulate the per-group loop: one dedup-check attempt per group.
    const attemptedKeys = groups.map(g => aggregationKey(g[0]));
    expect(attemptedKeys.length).toBe(1);
    expect(attemptedKeys[0]).toBe('arn:aws:lambda:us-east-1:123456789012:function:Shared');
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
