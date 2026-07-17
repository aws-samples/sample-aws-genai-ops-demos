import * as crypto from 'crypto';
import * as https from 'https';
import * as url from 'url';
import { DynamoDBClient, PutItemCommand, GetItemCommand } from '@aws-sdk/client-dynamodb';
import { SSMClient, GetParametersCommand } from '@aws-sdk/client-ssm';
import { getSecret } from '../lib/secrets-cache';

const dynamoClient = new DynamoDBClient({});
const ssmClient = new SSMClient({});
const DEFAULT_WEBHOOK_URL = process.env.DEVOPS_AGENT_WEBHOOK_URL!;
const WEBHOOK_SECRET_PARAM_NAME = process.env.WEBHOOK_SECRET_PARAM_NAME!;
const TASK_TOKEN_TABLE = process.env.TASK_TOKEN_TABLE!;
const AGENT_SPACES_TABLE = process.env.AGENT_SPACES_TABLE || '';

// SSM Parameter Store paths for the optional Jira routing config. The
// trigger Lambda reads these and inlines the values into the prompt sent
// to AWS DevOps Agent. We do this here (not in the agent) because the
// agent's session policy strips its role's `ssm:GetParameter` permission,
// so the agent itself can't reliably read the params at runtime.
const SSM_PARAM_JIRA_PROJECT_KEY = '/health-analyzer/jira/projectKey';
const SSM_PARAM_JIRA_ISSUE_TYPE = '/health-analyzer/jira/issueType';
const SSM_PARAM_JIRA_SITE_URL = '/health-analyzer/jira/siteUrl';

interface JiraConfig {
  projectKey: string;
  issueType: string;
  siteUrl: string;
}

interface HealthEvent {
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
  sourceAccountId?: string;
  ingestedAt: string;
}

interface TriggerInput {
  taskToken: string;
  healthEvent: HealthEvent;
}

interface AgentSpaceConfig {
  webhookUrl: string;
  webhookSecret: string;
  spaceName?: string;
}

export const handler = async (event: TriggerInput): Promise<void> => {
  const { taskToken, healthEvent } = event;

  console.log('Triggering DevOps Agent investigation for:', JSON.stringify(healthEvent, null, 2));

  // Resolve the correct agent space based on source account (hybrid routing)
  const agentSpace = await resolveAgentSpace(healthEvent.sourceAccountId);

  console.log(`Using agent space: ${agentSpace.spaceName || 'default'} (account: ${healthEvent.sourceAccountId || 'local'})`);

  // Build the investigation request for DevOps Agent webhook
  const incidentId = `health-${healthEvent.service}-${Date.now()}`;

  const maintenanceWindow = healthEvent.startTime && healthEvent.endTime
    ? `${healthEvent.startTime} to ${healthEvent.endTime}`
    : 'Not specified';

  const affectedResourceIds = healthEvent.affectedResources.map(r => r.resourceId);

  // Resolve the optional Jira routing config from SSM. Failures (parameters
  // missing, AccessDenied) yield `null` here — the prompt simply omits the
  // [JIRA_CONFIG:...] tag in that case, and the skill's Step 6 falls
  // through to the "skip" branch.
  const jiraConfig = await loadJiraConfig();

  const webhookPayload = {
    eventType: 'incident',
    incidentId,
    action: 'created' as const,
    priority: mapCategoryToPriority(healthEvent.category),
    title: `[${incidentId}] AWS Health: ${healthEvent.service} ${healthEvent.eventType} in ${healthEvent.region}`,
    description: buildDescription(healthEvent, maintenanceWindow, jiraConfig),
    timestamp: new Date().toISOString(),
    service: healthEvent.service,
    data: {
      metadata: {
        region: healthEvent.region,
        availabilityZone: healthEvent.availabilityZone,
        environment: 'production',
        sourceAccountId: healthEvent.sourceAccountId,
      },
      healthEvent: {
        eventId: healthEvent.eventId,
        category: healthEvent.category,
        maintenanceWindow,
        affectedResources: affectedResourceIds,
        status: healthEvent.status,
      },
    },
  };

  // Store the task token in DynamoDB keyed by incident ID
  // so the callback Lambda can resume Step Functions when investigation completes
  const ttl = Math.floor(Date.now() / 1000) + 3600; // 1 hour TTL

  await dynamoClient.send(new PutItemCommand({
    TableName: TASK_TOKEN_TABLE,
    Item: {
      investigationId: { S: incidentId },
      taskToken: { S: taskToken },
      healthEventId: { S: healthEvent.eventId },
      sourceAccountId: { S: healthEvent.sourceAccountId || 'local' },
      createdAt: { S: new Date().toISOString() },
      ttl: { N: ttl.toString() },
    },
  }));

  console.log(`Stored task token for investigation: ${incidentId}`);

  // Send webhook to the resolved DevOps Agent space with retry logic
  await sendWebhookWithRetry(agentSpace.webhookUrl, agentSpace.webhookSecret, webhookPayload);

  console.log(`DevOps Agent investigation triggered: ${incidentId}`);
  // Step Functions will wait for the callback via task token
};

/**
 * Resolves the correct DevOps Agent space for the given source account.
 *
 * Hybrid routing strategy:
 * 1. If an account-specific agent space is configured in the routing table, use it
 * 2. Otherwise, fall back to the default shared agent space
 *
 * This allows organizations to use a single shared space for most accounts
 * while supporting per-account overrides for teams that need isolation.
 */
async function resolveAgentSpace(sourceAccountId?: string): Promise<AgentSpaceConfig> {
  // Retrieve the default webhook secret from SSM via the cached secrets module
  const defaultSecret = await getSecret(WEBHOOK_SECRET_PARAM_NAME);

  // If no agent spaces table configured or no source account, use default
  if (!AGENT_SPACES_TABLE || !sourceAccountId) {
    return {
      webhookUrl: DEFAULT_WEBHOOK_URL,
      webhookSecret: defaultSecret,
      spaceName: 'default (shared)',
    };
  }

  try {
    // Look up account-specific agent space configuration
    const response = await dynamoClient.send(new GetItemCommand({
      TableName: AGENT_SPACES_TABLE,
      Key: { accountId: { S: sourceAccountId } },
    }));

    if (response.Item && response.Item.webhookUrl?.S && response.Item.webhookSecret?.S) {
      console.log(`Found account-specific agent space for ${sourceAccountId}`);
      return {
        webhookUrl: response.Item.webhookUrl.S,
        webhookSecret: response.Item.webhookSecret.S,
        spaceName: response.Item.spaceName?.S || `account-${sourceAccountId}`,
      };
    }
  } catch (error) {
    console.warn(`Failed to look up agent space for account ${sourceAccountId}, using default:`, error);
  }

  // Fall back to default shared space
  return {
    webhookUrl: DEFAULT_WEBHOOK_URL,
    webhookSecret: defaultSecret,
    spaceName: 'default (shared)',
  };
}

function mapCategoryToPriority(category: string): 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'MINIMAL' {
  switch (category) {
    case 'issue':
      return 'CRITICAL';
    case 'scheduledChange':
      return 'HIGH';
    case 'accountNotification':
      return 'MEDIUM';
    default:
      return 'MEDIUM';
  }
}

function buildDescription(healthEvent: HealthEvent, maintenanceWindow: string, jiraConfig: JiraConfig | null): string {
  const resourceList = healthEvent.affectedResources
    .map(r => `  - ${r.resourceId} (${r.status})`)
    .join('\n');

  const accountContext = healthEvent.sourceAccountId
    ? `Source Account: ${healthEvent.sourceAccountId}\n`
    : '';

  // Inline the Jira routing config when configured. The skill's Step 6
  // parses this tag instead of calling ssm:GetParameter at runtime (the
  // agent's session policy denies SSM reads on its role).
  const jiraTag = jiraConfig
    ? `[JIRA_CONFIG:${JSON.stringify(jiraConfig)}]\n\n`
    : '';

  return `[CORRELATION_ID:${healthEvent.eventId}]
${jiraTag}
AWS Health Event detected. Please investigate the impact on our workloads.

${accountContext}Service: ${healthEvent.service}
Event Type: ${healthEvent.eventType}
Category: ${healthEvent.category}
Region: ${healthEvent.region}
Availability Zone: ${healthEvent.availabilityZone || 'N/A'}
Maintenance Window: ${maintenanceWindow}
Status: ${healthEvent.status}

Description: ${healthEvent.description}

Affected Resources:
${resourceList}

Please analyze:
1. Which workloads in the topology are affected by this event?
2. What is the blast radius considering resource dependencies?
3. Are there redundancy mechanisms (multi-AZ, auto-scaling) that mitigate the impact?
4. What actions should be taken before the maintenance window?`;
}

/**
 * Reads the optional Jira routing config from SSM Parameter Store. We do
 * this in the trigger Lambda (not the agent) because the agent's session
 * policy strips its role's `ssm:GetParameter` permission, so the agent
 * itself can't read these at investigation time.
 *
 * Returns `null` when any of the three params are missing, the call is
 * denied, or any other failure occurs. The prompt then omits the
 * [JIRA_CONFIG:...] tag and the skill's Step 6 falls through to "skip".
 */
async function loadJiraConfig(): Promise<JiraConfig | null> {
  try {
    const resp = await ssmClient.send(new GetParametersCommand({
      Names: [
        SSM_PARAM_JIRA_PROJECT_KEY,
        SSM_PARAM_JIRA_ISSUE_TYPE,
        SSM_PARAM_JIRA_SITE_URL,
      ],
    }));
    const byName = new Map<string, string>();
    for (const p of resp.Parameters || []) {
      if (p.Name && p.Value) byName.set(p.Name, p.Value);
    }
    const projectKey = byName.get(SSM_PARAM_JIRA_PROJECT_KEY);
    const issueType = byName.get(SSM_PARAM_JIRA_ISSUE_TYPE);
    const siteUrl = byName.get(SSM_PARAM_JIRA_SITE_URL);
    if (!projectKey || !issueType || !siteUrl) {
      console.log(`Jira config incomplete in SSM (have: ${[...byName.keys()].join(', ') || 'none'}); skipping Jira tag.`);
      return null;
    }
    return { projectKey, issueType, siteUrl };
  } catch (error) {
    console.warn('Failed to read Jira config from SSM, skipping Jira tag:', error);
    return null;
  }
}

/**
 * Sends a webhook request with inline retry logic.
 * Retries up to 2 times (3 total attempts) with exponential backoff (1s base, 4s max).
 * Retries on: 5xx status codes, 429 (Too Many Requests), and network timeouts.
 * Does NOT retry on 4xx client errors (except 429).
 */
async function sendWebhookWithRetry(webhookUrl: string, webhookSecret: string, payload: object): Promise<void> {
  const MAX_RETRIES = 2;
  const BASE_DELAY_MS = 1000;
  const MAX_DELAY_MS = 4000;

  let lastError: Error | undefined;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      await sendWebhook(webhookUrl, webhookSecret, payload);
      return; // Success
    } catch (error: unknown) {
      lastError = error instanceof Error ? error : new Error(String(error));

      // Determine if we should retry
      const shouldRetry = isRetryableError(lastError);

      if (!shouldRetry || attempt >= MAX_RETRIES) {
        // Non-retryable error or exhausted retries — propagate immediately
        throw lastError;
      }

      // Exponential backoff: 1s, 2s (capped at 4s)
      const delay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt), MAX_DELAY_MS);
      console.warn(
        `Webhook attempt ${attempt + 1} failed (${lastError.message}), retrying in ${delay}ms...`
      );
      await sleep(delay);
    }
  }

  // Should not reach here, but just in case
  throw lastError ?? new Error('Webhook failed after retries');
}

/**
 * Determines if an error from sendWebhook is retryable.
 * Retryable: 5xx status codes, 429, and network/timeout errors.
 * Non-retryable: 4xx client errors (except 429).
 */
function isRetryableError(error: Error): boolean {
  const message = error.message;

  // Network timeout or connection error (no HTTP status code in message)
  if (message.includes('ETIMEDOUT') || message.includes('ECONNRESET') ||
      message.includes('ECONNREFUSED') || message.includes('ENOTFOUND') ||
      message.includes('socket hang up') || message.includes('timeout')) {
    return true;
  }

  // Extract status code from our error message format: "returned status NNN"
  const statusMatch = message.match(/returned status (\d+)/);
  if (statusMatch) {
    const statusCode = parseInt(statusMatch[1], 10);
    // Retry on 5xx and 429
    if (statusCode >= 500 || statusCode === 429) {
      return true;
    }
    // Do not retry on other 4xx
    return false;
  }

  // Unknown errors (network-level) — treat as retryable
  return true;
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function sendWebhook(webhookUrl: string, webhookSecret: string, payload: object): Promise<void> {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const timestamp = new Date().toISOString();

    // Generate HMAC signature
    const hmac = crypto.createHmac('sha256', webhookSecret);
    hmac.update(`${timestamp}:${body}`, 'utf8');
    const signature = hmac.digest('base64');

    const parsedUrl = new url.URL(webhookUrl);

    const options: https.RequestOptions = {
      hostname: parsedUrl.hostname,
      path: parsedUrl.pathname,
      method: 'POST',
      timeout: 10000, // 10-second timeout threshold
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
        'x-amzn-event-timestamp': timestamp,
        'x-amzn-event-signature': signature,
      },
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode === 200) {
          console.log('Webhook accepted:', data);
          resolve();
        } else {
          reject(new Error(`DevOps Agent webhook returned status ${res.statusCode}: ${data}`));
        }
      });
    });

    req.on('timeout', () => {
      req.destroy();
      reject(new Error('DevOps Agent webhook request timeout'));
    });

    req.on('error', reject);
    req.write(body);
    req.end();
  });
}
