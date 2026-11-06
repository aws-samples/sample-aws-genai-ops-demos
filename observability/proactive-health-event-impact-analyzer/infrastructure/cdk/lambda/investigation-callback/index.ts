import { EventBridgeEvent } from 'aws-lambda';
import { DynamoDBClient, GetItemCommand, ScanCommand, DeleteItemCommand } from '@aws-sdk/client-dynamodb';
import { SFNClient, SendTaskSuccessCommand, SendTaskFailureCommand } from '@aws-sdk/client-sfn';
import {
  DevOpsAgentClient,
  ListJournalRecordsCommand,
} from '@aws-sdk/client-devops-agent';

const dynamoClient = new DynamoDBClient({});
const sfnClient = new SFNClient({});
// The Agent Space (and therefore the aidevops endpoint we query for journal
// records) may live in a different Region than this Lambda. DEVOPS_AGENT_REGION is
// set by CDK to the resolved Agent Space region; when it is absent the SDK falls
// back to the Lambda's own Region, which is correct for same-region deployments.
const devOpsAgentClient = new DevOpsAgentClient(
  process.env.DEVOPS_AGENT_REGION ? { region: process.env.DEVOPS_AGENT_REGION } : {}
);
const TASK_TOKEN_TABLE = process.env.TASK_TOKEN_TABLE!;

// ─── Interfaces ─────────────────────────────────────────────────────────────

/**
 * Real DevOps Agent EventBridge event detail structure.
 * Ref: https://docs.aws.amazon.com/devopsagent/latest/userguide/integrating-devops-agent-into-event-driven-applications-using-amazon-eventbridge-devops-agent-events-detail-reference.html
 */
interface DevOpsAgentEventDetail {
  version: string;
  metadata: {
    agent_space_id: string;
    task_id: string;
    execution_id: string;
  };
  data: {
    task_type: string;
    priority: string;
    status: string;
    created_at: string;
    updated_at: string;
    summary_record_id?: string;
  };
}

interface MessageContent {
  id: string;
  role: 'user' | 'assistant';
  content: Array<{
    text?: string;
    thinking?: string;
    type: string;
  }>;
}

// ─── Handler ────────────────────────────────────────────────────────────────

export const handler = async (
  event: EventBridgeEvent<string, DevOpsAgentEventDetail>
): Promise<void> => {
  console.log('Received DevOps Agent event:', JSON.stringify(event, null, 2));

  const detailType = event['detail-type'];
  const detail = event.detail;
  const { agent_space_id, task_id, execution_id } = detail.metadata;

  if (!execution_id) {
    console.warn('No execution_id found in event metadata, skipping');
    return;
  }

  console.log(`Processing ${detailType} for task ${task_id}, execution ${execution_id}`);

  // Step 1: Retrieve the first journal message to find our incidentId for correlation
  const firstMessage = await getFirstJournalMessage(agent_space_id, execution_id);

  if (!firstMessage) {
    console.log('No journal messages found — cannot correlate with our workflow');
    return;
  }

  // Step 2: Extract our incidentId from the first message (the webhook payload we sent)
  const correlationKey = extractCorrelationKey(firstMessage);

  if (!correlationKey) {
    console.log('Could not extract correlation key from first journal message — may not be a Health event investigation');
    return;
  }

  console.log(`Correlated to our investigation: ${correlationKey}`);

  // Step 3: Look up the task token from DynamoDB
  const taskTokenRecord = await findTaskToken(correlationKey);

  if (!taskTokenRecord) {
    console.log(`No task token found for ${correlationKey} — token may have expired`);
    return;
  }

  const taskToken = taskTokenRecord.taskToken;

  try {
    if (detailType === 'Investigation Completed') {
      // Step 4: Retrieve the agent's analysis from the last journal message
      const agentAnalysis = await getAgentAnalysis(agent_space_id, execution_id);

      // Build the investigation link
      const investigationLink = `https://${agent_space_id}.aidevops.global.app.aws/investigation/${task_id}`;

      // Build the output for Step Functions
      const output = buildOutput(detail, agentAnalysis, investigationLink);

      await sfnClient.send(new SendTaskSuccessCommand({
        taskToken,
        output: JSON.stringify(output),
      }));
      console.log(`Task success sent for investigation ${correlationKey}`);
    } else {
      // Investigation failed, timed out, or was cancelled
      const errorMessage = `Investigation ${detailType.toLowerCase()} (task: ${task_id})`;
      await sfnClient.send(new SendTaskFailureCommand({
        taskToken,
        error: detailType.replace(/\s+/g, ''),
        cause: errorMessage,
      }));
      console.log(`Task failure sent for investigation ${correlationKey}: ${errorMessage}`);
    }

    // Clean up the exact token record we resolved
    await deleteTaskToken(taskTokenRecord.investigationId);
  } catch (error) {
    console.error(`Failed to send task response for ${correlationKey}:`, error);
    throw error;
  }
};

// ─── DevOps Agent API ───────────────────────────────────────────────────────

/**
 * Retrieves the first journal message (our webhook payload) for correlation.
 * The first message with role "user" contains the incident description we sent.
 */
async function getFirstJournalMessage(agentSpaceId: string, executionId: string): Promise<MessageContent | null> {
  try {
    const response = await devOpsAgentClient.send(new ListJournalRecordsCommand({
      agentSpaceId,
      executionId,
      recordType: 'message',
      order: 'ASC',
      maxResults: 1,
    }));

    if (response.records && response.records.length > 0) {
      const content = response.records[0].content;
      if (!content) return null;
      // Content is a document type — may be string or object
      const parsed: MessageContent = typeof content === 'string' ? JSON.parse(content) : content as unknown as MessageContent;
      return parsed;
    }
  } catch (error) {
    console.error('Failed to retrieve first journal message:', error);
  }
  return null;
}

/**
 * Retrieves the agent's analysis (last assistant message) with findings and recommendations.
 */
async function getAgentAnalysis(agentSpaceId: string, executionId: string): Promise<string | null> {
  try {
    const response = await devOpsAgentClient.send(new ListJournalRecordsCommand({
      agentSpaceId,
      executionId,
      recordType: 'message',
      order: 'DESC',
      maxResults: 5,
    }));

    if (response.records && response.records.length > 0) {
      // Find the last assistant message
      for (const record of response.records) {
        const content = record.content;
        if (!content) continue;
        const parsed: MessageContent = typeof content === 'string' ? JSON.parse(content) : content as unknown as MessageContent;
        if (parsed.role === 'assistant') {
          // Extract the text content (skip thinking blocks)
          const textParts = parsed.content
            .filter((c) => c.type === 'text' && c.text)
            .map((c) => c.text!);
          return textParts.join('\n\n');
        }
      }
    }
  } catch (error) {
    console.error('Failed to retrieve agent analysis:', error);
  }
  return null;
}

// ─── Correlation ────────────────────────────────────────────────────────────

/** Extracts the value of a `[PREFIX:value]` tag from text, or null if absent/empty. */
function extractTag(text: string, prefix: string): string | null {
  const start = text.indexOf(prefix);
  if (start === -1) return null;
  const valueStart = start + prefix.length;
  const end = text.indexOf(']', valueStart);
  if (end === -1) return null;
  return text.substring(valueStart, end).trim() || null;
}

/**
 * Extracts the correlation key from the first journal message.
 *
 * The Investigation Trigger embeds correlation data in three places:
 * 1. Description: [INVESTIGATION_ID:{incidentId}]  — unique per investigation
 * 2. Description: [CORRELATION_ID:{healthEventArn}] — shared across investigations for one event
 * 3. Title: [{incidentId}] AWS Health: ...
 *
 * Strategy (most-specific first):
 * - Primary: incidentId from [INVESTIGATION_ID:...] → keyed lookup by investigationId (unique).
 * - Legacy: healthEventArn from [CORRELATION_ID:...] → scan by healthEventId (NOT unique; first match).
 * - Fallback: incidentId from title [{incidentId}] → lookup by investigationId.
 *
 * The healthEventArn is shared by every investigation spawned for the same Health event,
 * so it cannot disambiguate concurrent investigations. INVESTIGATION_ID is preferred
 * because it maps 1:1 to a token-table row.
 */
function extractCorrelationKey(message: MessageContent): string | null {
  if (message.role !== 'user') {
    return null;
  }

  const textContent = message.content
    .filter(c => c.type === 'text' && c.text)
    .map(c => c.text!)
    .join(' ');

  // Primary: [INVESTIGATION_ID:{incidentId}] — unique per investigation.
  const investigationId = extractTag(textContent, '[INVESTIGATION_ID:');
  if (investigationId) {
    console.log(`Correlation via INVESTIGATION_ID tag: ${investigationId}`);
    return investigationId;
  }

  // Legacy: [CORRELATION_ID:{healthEventArn}] — kept for rows created before
  // INVESTIGATION_ID tagging. Returns an `arn:`-prefixed key handled by the scan path.
  const correlationId = extractTag(textContent, '[CORRELATION_ID:');
  if (correlationId) {
    console.log(`Correlation via CORRELATION_ID tag: ${correlationId}`);
    return `arn:${correlationId}`;
  }

  // Fallback: [{incidentId}] in title (format: TITLE: [{incidentId}] AWS Health: ...)
  const titleIncidentId = extractTag(textContent, 'TITLE: [');
  if (titleIncidentId && titleIncidentId.startsWith('health-')) {
    console.log(`Correlation via title incidentId: ${titleIncidentId}`);
    return titleIncidentId;
  }

  console.warn('No correlation key found in message text');
  return null;
}

// ─── Output Builder ─────────────────────────────────────────────────────────

/**
 * Builds the Step Functions output from the DevOps Agent event and analysis.
 * Parses the agent's markdown analysis to extract structured findings.
 */
function buildOutput(
  detail: DevOpsAgentEventDetail,
  agentAnalysis: string | null,
  investigationLink: string
): object {
  // The task priority (derived from the Health event category at intake) is only a
  // fallback here. The reported severity should reflect the agent's own conclusion.
  const taskPriority = detail.data.priority || 'MEDIUM';

  if (!agentAnalysis) {
    // Degraded case: the investigation completed but we could not retrieve its
    // analysis. This is intentionally NOT downgraded to LOW — we keep the task
    // priority so the missing-analysis case still surfaces an OpsItem for a human
    // to review, rather than being silently skipped by the workflow's LOW gate.
    return {
      investigationStatus: 'NO_IMPACT',
      summary: 'Investigation completed but no analysis available',
      rootCause: null,
      priority: taskPriority,
      findings: [],
      recommendations: [],
      investigationLink,
    };
  }

  // Parse the agent's markdown analysis to extract structured data
  const parsed = parseAgentAnalysis(agentAnalysis, taskPriority);

  // Severity = the highest per-workload severity the agent assigned in
  // "## Key Findings". Fall back to the task priority only when impact exists but
  // no severity word was parsed. No impact → LOW, so the workflow's priority gate
  // routes it to the no-OpsItem branch.
  const priority = parsed.hasImpact
    ? (parsed.overallSeverity || taskPriority)
    : 'LOW';

  return {
    investigationStatus: parsed.hasImpact ? 'IMPACT_DETECTED' : 'NO_IMPACT',
    summary: parsed.summary,
    rootCause: parsed.rootCause,
    priority,
    findings: parsed.findings,
    recommendations: parsed.recommendations,
    investigationLink,
  };
}

/**
 * Parses the agent's markdown analysis into structured findings and recommendations.
 * The agent produces rich markdown with headers, tables, and bullet points.
 */
function parseAgentAnalysis(analysis: string, fallbackPriority: string): {
  hasImpact: boolean;
  summary: string;
  rootCause: string | null;
  overallSeverity: string | null;
  findings: Array<{ description: string; severity: string; affectedResources: string[]; owningTeam?: string }>;
  recommendations: Array<{ description: string; priority: string }>;
} {
  const result = {
    hasImpact: false,
    summary: '',
    rootCause: null as string | null,
    overallSeverity: null as string | null,
    findings: [] as Array<{ description: string; severity: string; affectedResources: string[]; owningTeam?: string }>,
    recommendations: [] as Array<{ description: string; priority: string }>,
  };

  // Extract summary (## Summary section or first meaningful paragraph)
  const summaryMatch = analysis.match(/## Summary\s*\n+([\s\S]*?)(?=\n##|\n---|\n\|)/);
  if (summaryMatch) {
    result.summary = summaryMatch[1].trim().replace(/\n/g, ' ').substring(0, 500);
  } else {
    const firstParagraph = analysis.split('\n\n').find(p => p.length > 50 && !p.startsWith('#'));
    result.summary = firstParagraph?.trim().replace(/\n/g, ' ').substring(0, 500) || 'Investigation completed';
  }

  // Extract key findings from bullet points or table rows
  const findingsSection = analysis.match(/## Key Findings([\s\S]*?)(?=\n## (?!Key)|\n---)/);
  const answersSection = analysis.match(/## Answers to Your Questions([\s\S]*?)$/);
  const contentToSearch = findingsSection?.[1] || answersSection?.[1] || analysis;

  // Severities of ALL findings, used to compute the overall severity. Kept
  // separate from result.findings, which is capped at 5 for display — otherwise
  // a CRITICAL workload listed 6th would be dropped and the overall severity
  // (and the resulting OpsItem) would be under-reported.
  const allFindingSeverities: string[] = [];
  const bulletFindings = contentToSearch.match(/[-•*]\s+\*\*(.+?)\*\*[:\s]*(.+)/g);
  if (bulletFindings) {
    bulletFindings.forEach((bullet, i) => {
      const match = bullet.match(/[-•*]\s+\*\*(.+?)\*\*[:\s]*(.*)/);
      if (!match) return;
      // The agent writes the impact severity in the bullet detail
      // ("- **web-tier**: HIGH — ..."); use it, not the intake priority.
      const severity = parseSeverity(match[2]) || fallbackPriority;
      allFindingSeverities.push(severity);
      if (i < 5) {
        result.findings.push({
          description: `${match[1]}: ${match[2]}`.trim(),
          severity,
          affectedResources: [],
        });
      }
    });
  }

  // Extract recommendations from priority tables or numbered lists
  const recsSection = analysis.match(/(?:### 4\.|## (?:Recommended )?Actions|What actions)([\s\S]*?)(?=\n## (?!Rec)|\n---|\*\*No immediate)/);
  if (recsSection) {
    const tableRows = recsSection[1].match(/\|\s*\*\*P\d+\*\*\s*\|\s*(.+?)\s*\|/g);
    if (tableRows) {
      for (const row of tableRows) {
        const rowMatch = row.match(/\|\s*\*\*(P\d+)\*\*\s*\|\s*(.+?)\s*\|/);
        if (rowMatch) {
          result.recommendations.push({
            description: rowMatch[2].trim(),
            priority: mapPriority(rowMatch[1]),
          });
        }
      }
    }

    if (result.recommendations.length === 0) {
      const numbered = recsSection[1].match(/\d+\.\s+(.+)/g);
      if (numbered) {
        for (const item of numbered.slice(0, 5)) {
          const itemMatch = item.match(/\d+\.\s+(.+)/);
          if (itemMatch) {
            result.recommendations.push({
              description: itemMatch[1].trim(),
              priority: 'MEDIUM',
            });
          }
        }
      }
    }
  }

  // Determine if there's actual impact
  const noImpactIndicators = [
    'no workloads',
    'no operational impact',
    'no current operational impact',
    'not used by any',
    'no immediate operational',
  ];

  const hasImpactIndicators = [
    'will be unavailable',
    'service disruption',
    'degradation expected',
    'instances will be stopped',
    'blast radius',
    'affected workloads',
  ];

  const lowerAnalysis = analysis.toLowerCase();
  const noImpactScore = noImpactIndicators.filter(i => lowerAnalysis.includes(i)).length;
  const hasImpactScore = hasImpactIndicators.filter(i => lowerAnalysis.includes(i)).length;

  // If it's a security issue with recommendations, treat as impact (create OpsItem)
  if (result.recommendations.length > 0 || result.findings.length > 0) {
    result.hasImpact = true;
  }
  if (noImpactScore > hasImpactScore && noImpactScore >= 2) {
    result.hasImpact = result.recommendations.length > 0;
  }

  // If we have no findings but have recommendations, create a generic finding
  if (result.findings.length === 0 && result.hasImpact) {
    result.findings.push({
      description: result.summary.substring(0, 200),
      severity: fallbackPriority,
      affectedResources: [],
    });
    allFindingSeverities.push(fallbackPriority);
  }

  // Overall severity = the highest severity across ALL findings (the agent's conclusion).
  result.overallSeverity = highestSeverity(allFindingSeverities);

  return result;
}

function mapPriority(p: string): string {
  const map: Record<string, string> = { P1: 'CRITICAL', P2: 'HIGH', P3: 'MEDIUM', P4: 'LOW' };
  return map[p] || 'MEDIUM';
}

// Severity ranking used to pick the overall (highest) investigation severity.
const SEVERITY_RANK: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, MINIMAL: 0 };

/**
 * Extracts the impact-severity word the agent wrote in a "## Key Findings"
 * bullet (e.g. "- **web-tier**: HIGH — ..."). Returns null when none is present.
 */
function parseSeverity(text: string): string | null {
  // Per the skill's "## Key Findings" format the severity leads the bullet detail
  // ("<SEVERITY> — <statement>"), so only accept a severity word at the start
  // (after optional markdown/whitespace). This avoids picking up a severity word
  // later in the sentence (e.g. "LOW baseline, escalates to CRITICAL under load").
  const m = text.trim().toUpperCase().match(/^[*_\s]*(CRITICAL|HIGH|MEDIUM|LOW|MINIMAL)\b/);
  return m ? m[1] : null;
}

/** Returns the highest-ranked severity in the list, or null if the list is empty. */
function highestSeverity(severities: string[]): string | null {
  let best: string | null = null;
  for (const s of severities) {
    if (best === null || (SEVERITY_RANK[s] ?? -1) > (SEVERITY_RANK[best] ?? -1)) {
      best = s;
    }
  }
  return best;
}

// ─── DynamoDB Operations ────────────────────────────────────────────────────

async function findTaskToken(correlationKey: string): Promise<{ taskToken: string; investigationId: string } | null> {
  // Preferred path: the key IS the unique investigationId (token table partition
  // key) → a single, unambiguous GetItem. No Scan, no first-match ambiguity.
  if (!correlationKey.startsWith('arn:')) {
    const { Item } = await dynamoClient.send(new GetItemCommand({
      TableName: TASK_TOKEN_TABLE,
      Key: { investigationId: { S: correlationKey } },
    }));
    if (Item?.taskToken?.S) {
      return { taskToken: Item.taskToken.S, investigationId: correlationKey };
    }
    return null;
  }

  // Legacy fallback for rows written before INVESTIGATION_ID tagging: scan by
  // healthEventId. This is NOT unique across concurrent investigations for the
  // same Health event and can only return an arbitrary match — new investigations
  // never reach this path.
  const healthEventArn = correlationKey.replace('arn:', '');
  console.log(`Searching DynamoDB by healthEventId (legacy fallback): ${healthEventArn}`);
  const { Items } = await dynamoClient.send(new ScanCommand({
    TableName: TASK_TOKEN_TABLE,
    FilterExpression: 'healthEventId = :arn',
    ExpressionAttributeValues: {
      ':arn': { S: healthEventArn },
    },
  }));
  if (Items && Items.length > 0 && Items[0].investigationId?.S) {
    return {
      taskToken: Items[0].taskToken.S!,
      investigationId: Items[0].investigationId.S,
    };
  }
  return null;
}

/**
 * Deletes the token row by its exact investigationId (the one resolved by
 * findTaskToken). Deleting the precise row we just consumed avoids the previous
 * re-scan, which could delete a different row than the one that was resolved
 * when multiple rows shared a healthEventId.
 */
async function deleteTaskToken(investigationId: string): Promise<void> {
  if (!investigationId) return;
  await dynamoClient.send(new DeleteItemCommand({
    TableName: TASK_TOKEN_TABLE,
    Key: { investigationId: { S: investigationId } },
  }));
}
