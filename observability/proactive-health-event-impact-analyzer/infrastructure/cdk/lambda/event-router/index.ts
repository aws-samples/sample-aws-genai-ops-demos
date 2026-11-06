import { EventBridgeEvent, Context } from 'aws-lambda';
import { SFNClient, StartExecutionCommand } from '@aws-sdk/client-sfn';
import { DynamoDBClient, PutItemCommand } from '@aws-sdk/client-dynamodb';
import * as crypto from 'crypto';

const sfnClient = new SFNClient({});
const dynamoClient = new DynamoDBClient({});
const STATE_MACHINE_ARN = process.env.STATE_MACHINE_ARN!;
const FINDING_DEDUP_TABLE = process.env.FINDING_DEDUP_TABLE!;

// Dedup TTL for Security Hub findings. Deliberately longer than the 1h
// task-token TTL because Security Hub re-imports the same finding repeatedly
// over a span of hours; 6h is long enough to absorb those re-imports yet short
// enough that a genuinely re-raised finding re-investigates after the window.
const DEDUP_TTL_SECONDS = 21600; // 6h

// Caps the number of investigations started per Router invocation (i.e. per
// aggregate group, post-dedup) so a single large Security Hub batch cannot
// fan out an unbounded number of Step Functions executions. Parsed once at
// module load; defaults to 10 when unset or unparseable (non-finite or <= 0).
const MAX_INVESTIGATIONS_PER_INVOCATION = (() => {
  const raw = process.env.MAX_INVESTIGATIONS_PER_INVOCATION;
  const parsed = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 10;
})();

interface HealthEventDetail {
  eventArn: string;
  service: string;
  eventTypeCode: string;
  eventTypeCategory: string;
  region: string;
  availabilityZone?: string;
  startTime?: string;
  endTime?: string;
  lastUpdatedTime?: string;
  statusCode?: string;
  eventScopeCode?: string;
  eventDescription?: Array<{ language: string; latestDescription: string }>;
  affectedEntities?: Array<{
    entityValue: string;
    tags?: Record<string, string>;
    status?: string;
  }>;
}

// Minimal ASFF (AWS Security Finding Format) shape — only the fields the
// normalizer reads. Every field is optional/defensive except Id, which ASFF
// always provides and which the per-finding filter guarantees before use.
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

interface SecurityHubDetail {
  findings?: AsffFinding[];
}

// The normalized internal event contract shared by BOTH ingestion paths. This
// is exactly what the Health branch emits today and what the state machine's
// TriggerInvestigation / Investigation Trigger consume. Keys must match the
// Health branch byte-for-byte (no extra keys, no `priority`).
interface WorkflowInput {
  eventId: string;
  service: string;
  eventType: string;
  category: string;
  region: string;
  availabilityZone: string | null;
  startTime: string | null;
  endTime: string | null;
  status: string;
  description: string;
  affectedResources: Array<{
    resourceId: string;
    tags: Record<string, string>;
    status: string;
  }>;
  sourceAccountId: string;
  rawEvent: unknown;
  ingestedAt: string;
}

export const handler = async (
  event: EventBridgeEvent<string, HealthEventDetail | SecurityHubDetail>,
  context: Context
): Promise<
  | { statusCode: number; executionArn: string }
  | { statusCode: number; startedExecutions: string[] }
> => {
  // Dispatch on event source BEFORE any normalization. aws.securityhub and
  // aws.health are disjoint sources, so no event is ever handled by both
  // branches. The Health branch below is byte-identical to its prior behavior.
  if (event.source === 'aws.securityhub') {
    return handleSecurityHubEvent(
      event as EventBridgeEvent<string, SecurityHubDetail>,
      context
    );
  }

  return handleHealthEvent(
    event as EventBridgeEvent<'AWS Health Event', HealthEventDetail>,
    context
  );
};

/**
 * Existing AWS Health ingestion path — behavior is unchanged. Normalizes a
 * single Health event and starts exactly one Step Functions execution with a
 * `health-` execution-name prefix.
 */
async function handleHealthEvent(
  event: EventBridgeEvent<'AWS Health Event', HealthEventDetail>,
  context: Context
): Promise<{ statusCode: number; executionArn: string }> {
  console.log('Received Health event:', JSON.stringify(event, null, 2));

  const detail = event.detail;

  // Normalize the event into a structured payload for the workflow
  const workflowInput = {
    eventId: detail.eventArn,
    service: detail.service,
    eventType: detail.eventTypeCode,
    category: detail.eventTypeCategory,
    region: event.region || detail.region,
    availabilityZone: detail.availabilityZone || null,
    startTime: detail.startTime || null,
    endTime: detail.endTime || null,
    status: detail.statusCode || 'unknown',
    description: extractDescription(detail.eventDescription),
    affectedResources: (detail.affectedEntities || []).map(entity => ({
      resourceId: entity.entityValue,
      tags: entity.tags || {},
      status: entity.status || 'unknown',
    })),
    sourceAccountId: event.account,
    rawEvent: event,
    ingestedAt: new Date().toISOString(),
  };

  // Start the Step Functions execution
  const executionName = `health-${Date.now()}-${context.awsRequestId.slice(0, 8)}`;

  const command = new StartExecutionCommand({
    stateMachineArn: STATE_MACHINE_ARN,
    name: executionName,
    input: JSON.stringify(workflowInput),
  });

  const response = await sfnClient.send(command);

  console.log(`Started execution: ${response.executionArn}`);

  return {
    statusCode: 200,
    executionArn: response.executionArn!,
  };
}

/**
 * Resource-ARN-based aggregation key for a finding: the first entry in
 * `Resources[]`, falling back to the finding's own Id when `Resources[]` is
 * empty (defensive; ASFF normally always includes at least one resource).
 * Findings sharing an aggregation key are grouped into a single investigation.
 */
function aggregationKey(f: AsffFinding): string {
  return f.Resources?.[0]?.Id ?? f.Id;
}

/**
 * Security Hub ingestion path. A single `Security Hub Findings - Imported`
 * event can carry multiple findings; this handler groups qualifying findings
 * by aggregation key (one Step Functions execution per impacted resource, not
 * per finding) and enforces a numeric cap on investigations started per
 * invocation.
 *
 * The EventBridge rule pattern is only a COARSE batch-admission gate: array
 * content filtering matches when ANY element of `detail.findings[]` satisfies
 * the leaf matchers, so a matched batch can still contain non-qualifying
 * findings. The per-finding filter below is therefore the AUTHORITATIVE gate —
 * only NEW/ACTIVE/CRITICAL findings ever start an execution.
 */
async function handleSecurityHubEvent(
  event: EventBridgeEvent<string, SecurityHubDetail>,
  _context: Context
): Promise<{ statusCode: number; startedExecutions: string[] }> {
  console.log('Received Security Hub event:', JSON.stringify(event, null, 2));

  const findings = event.detail.findings ?? [];
  const startedExecutions: string[] = [];

  // Step 1: authoritative per-finding filter (re-applied; see the rule
  // comment), including the defensive `!f.Id` skip so a finding with no Id
  // never reaches aggregationKey.
  const qualifying: AsffFinding[] = [];
  for (const f of findings) {
    const severity = f.Severity?.Label;
    const qualifies =
      severity === 'CRITICAL' &&
      f.Workflow?.Status === 'NEW' &&
      f.RecordState === 'ACTIVE';
    if (!qualifies) {
      console.debug(
        `Skipping non-qualifying finding (severity=${severity}, status=${f.Workflow?.Status}, recordState=${f.RecordState})`
      );
      continue;
    }

    // ASFF always provides Id; defensively skip (not fatal) if absent so one
    // malformed finding cannot fail the whole batch.
    if (!f.Id) {
      console.warn('Skipping qualifying finding with no Id');
      continue;
    }

    qualifying.push(f);
  }

  // Step 2: group qualifying findings by aggregation key, preserving
  // first-seen order both for group iteration and for each group's
  // representative (the first finding placed into that group).
  const groups: AsffFinding[][] = [];
  const groupIndexByKey = new Map<string, number>();
  for (const f of qualifying) {
    const key = aggregationKey(f);
    const existingIndex = groupIndexByKey.get(key);
    if (existingIndex === undefined) {
      groupIndexByKey.set(key, groups.length);
      groups.push([f]);
    } else {
      groups[existingIndex].push(f);
    }
  }

  // Step 3: iterate groups in order, until the cap is reached.
  let skippedCount = 0;
  for (let i = 0; i < groups.length; i++) {
    if (startedExecutions.length === MAX_INVESTIGATIONS_PER_INVOCATION) {
      skippedCount = groups.length - i;
      break;
    }

    const group = groups[i];
    const key = aggregationKey(group[0]);

    // Dedup via conditional write-if-absent, now keyed on the aggregation key
    // (a resource ARN, or fallback finding Id) rather than a single finding's
    // Id, so a resource with several qualifying findings in this batch only
    // ever attempts one conditional write. Written BEFORE StartExecution so
    // concurrent duplicate deliveries resolve deterministically (exactly one
    // writer wins). The write-before-start order means a crash between the
    // two at worst drops one investigation within the TTL window — preferable
    // to emitting a duplicate investigation.
    try {
      await dynamoClient.send(new PutItemCommand({
        TableName: FINDING_DEDUP_TABLE,
        Item: {
          findingId: { S: key },
          createdAt: { S: new Date().toISOString() },
          ttl: { N: String(Math.floor(Date.now() / 1000) + DEDUP_TTL_SECONDS) },
        },
        ConditionExpression: 'attribute_not_exists(findingId)',
      }));
      console.log(`Dedup key written for aggregation key: ${key} (${group.length} finding(s))`);
    } catch (error: unknown) {
      if (isConditionalCheckFailed(error)) {
        // Another delivery already claimed this aggregation key — skip (not
        // fatal, and not counted toward the cap).
        console.info(`Aggregation key already in-flight/recently investigated, skipping: ${key}`);
        continue;
      }
      // Any other DynamoDB error on a qualifying group is rethrown so the
      // invocation fails and the async-retry/DLQ path engages. Re-delivery is
      // idempotent: already-written dedup keys make re-processed groups a
      // no-op.
      throw error;
    }

    const workflowInput = normalizeAggregatedFindings(event, group, key);
    const executionName = buildExecutionName(key);

    const response = await sfnClient.send(new StartExecutionCommand({
      stateMachineArn: STATE_MACHINE_ARN,
      name: executionName,
      input: JSON.stringify(workflowInput),
    }));

    console.log(`Started security finding execution: ${response.executionArn}`);
    startedExecutions.push(response.executionArn!);
  }

  // Step 4: a single summary warning if the cap caused any groups to be
  // skipped — not an error, since hitting the cap is not a failure.
  if (skippedCount > 0) {
    console.warn(
      `Investigation cap reached (${MAX_INVESTIGATIONS_PER_INVOCATION}): skipping ${skippedCount} further group(s) in this invocation.`
    );
  }

  // Step 5: zero surviving groups is a success, not an error (a batch may
  // legitimately contain only non-qualifying findings) — no throw, no DLQ.
  console.log(`Security Hub event processed: ${startedExecutions.length} execution(s) started`);
  return {
    statusCode: 200,
    startedExecutions,
  };
}

/**
 * Normalizes a single ASFF finding into the internal WorkflowInput contract —
 * EXACTLY the same key set the Health branch emits (AC-4). MUST NOT set
 * `priority` (that is produced downstream by the Investigation Callback).
 */
export function normalizeFinding(
  event: EventBridgeEvent<string, SecurityHubDetail>,
  f: AsffFinding
): WorkflowInput {
  // Only CRITICAL findings reach this point (see the per-finding filter in
  // handleSecurityHubEvent); 'issue' is the fixed severity-derived intake
  // hint, matching mapCategoryToPriority's CRITICAL→'issue' mapping.
  const category = 'issue';

  // Description must be non-empty (the Health branch guarantees this too).
  const title = f.Title?.trim();
  const desc = f.Description?.trim();
  const description =
    (title && desc ? `${title}. ${desc}` : title || desc || 'Security Hub finding (no description provided)');

  return {
    eventId: f.Id,
    // Constant literal rather than f.ProductName: a stable, predictable value
    // that is safe inside the execution-name prefix and the webhook title.
    service: 'SecurityHub',
    eventType: f.Types?.[0] ?? f.GeneratorId ?? 'SecurityHubFinding',
    category,
    // EventBridge envelope region (NOT per-resource Resources[].Region, which
    // may vary); matches the Health branch's event.region preference.
    region: event.region,
    availabilityZone: null,
    // Findings have no maintenance window; leaving these null makes the
    // (cosmetic) "Maintenance Window: Not specified" line downstream, which the
    // skill's security path is instructed to ignore.
    startTime: null,
    endTime: null,
    status: f.Workflow?.Status ?? f.RecordState ?? 'NEW',
    description,
    affectedResources: (f.Resources ?? []).map(r => ({
      resourceId: r.Id,
      tags: r.Tags ?? {},
      status: f.Workflow?.Status ?? 'ACTIVE',
    })),
    // Envelope account is authoritative for multi-account agent-space routing.
    sourceAccountId: event.account,
    // The SINGLE finding, not the whole batch: downstream only needs this
    // finding's context, and carrying the full batch into every per-finding
    // execution would bloat the payload redundantly.
    rawEvent: f,
    ingestedAt: new Date().toISOString(),
  };
}

/**
 * Normalizes a GROUP of ASFF findings (all sharing one aggregation key) into
 * the internal WorkflowInput contract. Implemented as a thin wrapper over
 * `normalizeFinding`: it computes a correctly-shaped single-finding result
 * for the group's representative (the first finding placed into the group),
 * then overrides exactly the four fields that differ for an aggregate
 * (`eventId`, `description`, `affectedResources`, `rawEvent`). This override
 * is applied unconditionally, including when the group has exactly one
 * member — there is no size-based short-circuit back to a bare
 * `normalizeFinding` result.
 */
export function normalizeAggregatedFindings(
  event: EventBridgeEvent<string, SecurityHubDetail>,
  group: AsffFinding[],
  aggregationKey: string
): WorkflowInput {
  const representative = group[0];
  const base = normalizeFinding(event, representative);

  // Deduplicated union of every Resources[] entry across the whole group,
  // keyed by resource Id so a resource referenced by multiple findings in the
  // group appears exactly once.
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
  const summary = (title && desc ? `${title}. ${desc}` : title || desc || 'No description available');

  return {
    ...base,
    // Deterministic and stable per aggregation key, independent of batch
    // ordering across re-deliveries; prefixed so it never collides with the
    // Health path's raw eventArn-based eventIds.
    eventId: `securityhub-aggregate:${aggregationKey}`,
    description: `${group.length} CRITICAL Security Hub finding(s) on ${aggregationKey}. ${summary}${group.length > 1 ? ` (and ${group.length - 1} more)` : ''}`,
    affectedResources: dedupedResources,
    // The group of findings (array), not a single finding and not the whole
    // original batch (which may include findings destined for other groups
    // or filtered out entirely).
    rawEvent: group,
  };
}

/**
 * Builds a Step Functions execution name for a finding. Names must be ≤80 chars
 * and match [0-9A-Za-z-_]; the ASFF Id is a long ARN with illegal characters,
 * so it is sanitized and truncated. The `securityhub-` prefix (distinct from
 * the Health path's `health-`) is for observability only — it has no bearing on
 * callback correlation, which uses [INVESTIGATION_ID].
 */
export function buildExecutionName(findingId: string): string {
  const prefix = 'securityhub-';
  const timestamp = `-${Date.now()}`;
  const suffix = `-${crypto.randomBytes(4).toString('hex')}`; // 8 hex chars
  const budget = 80 - prefix.length - timestamp.length - suffix.length;
  const sanitized = findingId.replace(/[^0-9A-Za-z-_]/g, '-').slice(0, Math.max(0, budget));
  return `${prefix}${sanitized}${timestamp}${suffix}`;
}

function isConditionalCheckFailed(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    (error as { name?: string }).name === 'ConditionalCheckFailedException'
  );
}

function extractDescription(
  descriptions?: Array<{ language: string; latestDescription: string }>
): string {
  if (!descriptions || descriptions.length === 0) {
    return 'No description available';
  }

  // Prefer English description
  const english = descriptions.find(d => d.language === 'en_US' || d.language === 'en');
  return (english || descriptions[0]).latestDescription;
}
