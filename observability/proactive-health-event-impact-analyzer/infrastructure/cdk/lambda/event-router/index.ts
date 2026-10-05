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
 * Security Hub ingestion path. A single `Security Hub Findings - Imported`
 * event can carry multiple findings; this handler fans each qualifying finding
 * out into its OWN Step Functions execution carrying the same normalized
 * contract the Health path produces.
 *
 * The EventBridge rule pattern is only a COARSE batch-admission gate: array
 * content filtering matches when ANY element of `detail.findings[]` satisfies
 * the leaf matchers, so a matched batch can still contain non-qualifying
 * findings. The per-finding filter below is therefore the AUTHORITATIVE gate —
 * only NEW/ACTIVE/HIGH/CRITICAL findings ever start an execution.
 */
async function handleSecurityHubEvent(
  event: EventBridgeEvent<string, SecurityHubDetail>,
  _context: Context
): Promise<{ statusCode: number; startedExecutions: string[] }> {
  console.log('Received Security Hub event:', JSON.stringify(event, null, 2));

  const findings = event.detail.findings ?? [];
  const startedExecutions: string[] = [];

  // Process findings sequentially — a batch is small, and sequential conditional
  // writes + StartExecution keeps the dedup/idempotency reasoning simple.
  for (const f of findings) {
    // Authoritative per-finding filter (re-applied; see the rule comment).
    const severity = f.Severity?.Label;
    const qualifies =
      (severity === 'HIGH' || severity === 'CRITICAL') &&
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

    // Dedup via conditional write-if-absent. Written BEFORE StartExecution so
    // concurrent duplicate deliveries resolve deterministically (exactly one
    // writer wins). The write-before-start order means a crash between the two
    // at worst drops one investigation within the TTL window — preferable to
    // emitting a duplicate investigation.
    try {
      await dynamoClient.send(new PutItemCommand({
        TableName: FINDING_DEDUP_TABLE,
        Item: {
          findingId: { S: f.Id },
          createdAt: { S: new Date().toISOString() },
          ttl: { N: String(Math.floor(Date.now() / 1000) + DEDUP_TTL_SECONDS) },
        },
        ConditionExpression: 'attribute_not_exists(findingId)',
      }));
    } catch (error: unknown) {
      if (isConditionalCheckFailed(error)) {
        // Another delivery already claimed this finding — skip (not fatal).
        console.info(`Finding already in-flight/recently investigated, skipping: ${f.Id}`);
        continue;
      }
      // Any other DynamoDB error on a qualifying finding is rethrown so the
      // invocation fails and the async-retry/DLQ path engages. Re-delivery is
      // idempotent: already-written dedup keys make re-processed findings a
      // no-op.
      throw error;
    }

    const workflowInput = normalizeFinding(event, f);
    const executionName = buildExecutionName(f.Id);

    const response = await sfnClient.send(new StartExecutionCommand({
      stateMachineArn: STATE_MACHINE_ARN,
      name: executionName,
      input: JSON.stringify(workflowInput),
    }));

    console.log(`Started security finding execution: ${response.executionArn}`);
    startedExecutions.push(response.executionArn!);
  }

  // Zero surviving findings is a success, not an error (a batch may legitimately
  // contain only non-qualifying findings) — no throw, no DLQ.
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
  // `category` here is a severity-derived INTAKE priority hint, not a Health
  // event category. mapCategoryToPriority (investigation-trigger) is left
  // unchanged: CRITICAL→'issue' (→CRITICAL intake), HIGH→'scheduledChange'
  // (→HIGH intake). The final OpsItem/notification priority comes from the
  // agent's own severities parsed in the Callback.
  const category = f.Severity?.Label === 'CRITICAL' ? 'issue' : 'scheduledChange';

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
