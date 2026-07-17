import { EventBridgeEvent } from 'aws-lambda';
import { DynamoDBClient, ScanCommand, DeleteItemCommand } from '@aws-sdk/client-dynamodb';
import { SFNClient, SendTaskSuccessCommand, SendTaskFailureCommand } from '@aws-sdk/client-sfn';
import {
  DevOpsAgentClient,
  ListJournalRecordsCommand,
} from '@aws-sdk/client-devops-agent';

const dynamoClient = new DynamoDBClient({});
const sfnClient = new SFNClient({});
const devOpsAgentClient = new DevOpsAgentClient({});
const TASK_TOKEN_TABLE = process.env.TASK_TOKEN_TABLE!;
const AWS_REGION = process.env.AWS_REGION || 'eu-west-1';

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

    // Clean up the token record
    await deleteTaskToken(correlationKey);
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

/**
 * Extracts the correlation key from the first journal message.
 *
 * The Investigation Trigger embeds correlation data in two places:
 * 1. Description starts with: [CORRELATION_ID:{healthEventArn}]
 * 2. Title starts with: [{incidentId}] AWS Health: ...
 *
 * Strategy:
 * - Primary: extract healthEventArn from [CORRELATION_ID:...] → search DynamoDB by healthEventId
 * - Fallback: extract incidentId from title [{incidentId}] → search DynamoDB by investigationId
 */
function extractCorrelationKey(message: MessageContent): string | null {
  if (message.role !== 'user') {
    return null;
  }

  const textContent = message.content
    .filter(c => c.type === 'text' && c.text)
    .map(c => c.text!)
    .join(' ');

  // Primary: [CORRELATION_ID:{healthEventArn}] in description
  const prefix = '[CORRELATION_ID:';
  const startIdx = textContent.indexOf(prefix);
  if (startIdx !== -1) {
    const valueStart = startIdx + prefix.length;
    const endIdx = textContent.indexOf(']', valueStart);
    if (endIdx !== -1) {
      const correlationId = textContent.substring(valueStart, endIdx).trim();
      if (correlationId) {
        console.log(`Correlation via CORRELATION_ID tag: ${correlationId}`);
        return `arn:${correlationId}`;
      }
    }
  }

  // Fallback: [{incidentId}] in title (format: TITLE: [{incidentId}] AWS Health: ...)
  const titlePrefix = 'TITLE: [';
  const titleIdx = textContent.indexOf(titlePrefix);
  if (titleIdx !== -1) {
    const idStart = titleIdx + titlePrefix.length;
    const idEnd = textContent.indexOf(']', idStart);
    if (idEnd !== -1) {
      const incidentId = textContent.substring(idStart, idEnd).trim();
      if (incidentId && incidentId.startsWith('health-')) {
        console.log(`Correlation via title incidentId: ${incidentId}`);
        return incidentId;
      }
    }
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
  const priority = detail.data.priority || 'MEDIUM';

  if (!agentAnalysis) {
    return {
      investigationStatus: 'NO_IMPACT',
      summary: 'Investigation completed but no analysis available',
      rootCause: null,
      priority,
      findings: [],
      recommendations: [],
      investigationLink,
    };
  }

  // Parse the agent's markdown analysis to extract structured data
  const parsed = parseAgentAnalysis(agentAnalysis, priority);

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
function parseAgentAnalysis(analysis: string, priority: string): {
  hasImpact: boolean;
  summary: string;
  rootCause: string | null;
  findings: Array<{ description: string; severity: string; affectedResources: string[]; owningTeam?: string }>;
  recommendations: Array<{ description: string; priority: string }>;
} {
  const result = {
    hasImpact: false,
    summary: '',
    rootCause: null as string | null,
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

  const bulletFindings = contentToSearch.match(/[-•*]\s+\*\*(.+?)\*\*[:\s]*(.+)/g);
  if (bulletFindings) {
    for (const bullet of bulletFindings.slice(0, 5)) {
      const match = bullet.match(/[-•*]\s+\*\*(.+?)\*\*[:\s]*(.*)/);
      if (match) {
        result.findings.push({
          description: `${match[1]}: ${match[2]}`.trim(),
          severity: priority,
          affectedResources: [],
        });
      }
    }
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
      severity: priority,
      affectedResources: [],
    });
  }

  return result;
}

function mapPriority(p: string): string {
  const map: Record<string, string> = { P1: 'CRITICAL', P2: 'HIGH', P3: 'MEDIUM', P4: 'LOW' };
  return map[p] || 'MEDIUM';
}

// ─── DynamoDB Operations ────────────────────────────────────────────────────

async function findTaskToken(correlationKey: string): Promise<{ taskToken: string; healthEventId: string } | null> {
  // If it's an ARN-based lookup, search by healthEventId
  if (correlationKey.startsWith('arn:')) {
    const healthEventArn = correlationKey.replace('arn:', '');
    console.log(`Searching DynamoDB by healthEventId: ${healthEventArn}`);
    const { Items } = await dynamoClient.send(new ScanCommand({
      TableName: TASK_TOKEN_TABLE,
      FilterExpression: 'healthEventId = :arn',
      ExpressionAttributeValues: {
        ':arn': { S: healthEventArn },
      },
    }));

    if (Items && Items.length > 0) {
      return {
        taskToken: Items[0].taskToken.S!,
        healthEventId: Items[0].healthEventId?.S || '',
      };
    }
    return null;
  }

  // Direct lookup by investigationId
  const { Items } = await dynamoClient.send(new ScanCommand({
    TableName: TASK_TOKEN_TABLE,
    FilterExpression: 'investigationId = :id',
    ExpressionAttributeValues: {
      ':id': { S: correlationKey },
    },
  }));

  if (Items && Items.length > 0) {
    return {
      taskToken: Items[0].taskToken.S!,
      healthEventId: Items[0].healthEventId?.S || '',
    };
  }

  return null;
}

async function deleteTaskToken(correlationKey: string): Promise<void> {
  if (correlationKey.startsWith('arn:')) {
    const healthEventArn = correlationKey.replace('arn:', '');
    const { Items } = await dynamoClient.send(new ScanCommand({
      TableName: TASK_TOKEN_TABLE,
      FilterExpression: 'healthEventId = :arn',
      ExpressionAttributeValues: {
        ':arn': { S: healthEventArn },
      },
    }));
    if (Items && Items.length > 0 && Items[0].investigationId?.S) {
      await dynamoClient.send(new DeleteItemCommand({
        TableName: TASK_TOKEN_TABLE,
        Key: { investigationId: { S: Items[0].investigationId.S } },
      }));
    }
    return;
  }

  await dynamoClient.send(new DeleteItemCommand({
    TableName: TASK_TOKEN_TABLE,
    Key: { investigationId: { S: correlationKey } },
  }));
}
