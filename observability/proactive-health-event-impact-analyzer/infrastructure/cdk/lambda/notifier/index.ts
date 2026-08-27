import { SNSClient, PublishCommand } from '@aws-sdk/client-sns';
import { DynamoDBClient, ScanCommand, GetItemCommand } from '@aws-sdk/client-dynamodb';
import {
  AccountClient,
  GetContactInformationCommand,
  GetAlternateContactCommand,
  AlternateContactType,
} from '@aws-sdk/client-account';
import * as https from 'https';
import * as url from 'url';
import { getSecret } from '../lib/secrets-cache';

const snsClient = new SNSClient({});
const dynamoClient = new DynamoDBClient({});
const accountClient = new AccountClient({});
const SNS_TOPIC_ARN = process.env.SNS_TOPIC_ARN!;
const SLACK_WEBHOOK_PARAM_NAME = process.env.SLACK_WEBHOOK_PARAM_NAME || '';
const MSTEAMS_WEBHOOK_PARAM_NAME = process.env.MSTEAMS_WEBHOOK_PARAM_NAME || '';
const TEAMS_TABLE = process.env.TEAMS_TABLE!;
const ENABLE_DEFAULT_ROUTING = process.env.ENABLE_DEFAULT_ROUTING === 'true';

interface Finding {
  description: string;
  severity: string;
  affectedResources: string[];
  owningTeam?: string;
}

interface Recommendation {
  description: string;
  priority: string;
}

interface InvestigationResult {
  investigationStatus: 'IMPACT_DETECTED' | 'NO_IMPACT';
  summary: string;
  rootCause: string | null;
  priority: string;
  findings: Finding[];
  recommendations: Recommendation[];
  teamsToNotify?: string[];
  sourceAccountId?: string;
  investigationLink?: string | null;
  opsItemId?: string;
  opsItemUrl?: string;
}

/**
 * Input from Step Functions.
 * The workflow now passes the investigation result along with OpsItem info.
 */
interface NotifierInput {
  investigationResult: InvestigationResult;
  opsItemId?: string;
  opsItemUrl?: string;
}

interface TeamConfig {
  teamId: string;
  teamName: string;
  email?: string;
  slackWebhookUrl?: string;
  slackChannel?: string;
  msTeamsWebhookUrl?: string;
  notifyOn: string[];
}

interface AlternateContactInfo {
  type: string;
  name: string | null;
  email: string | null;
  phone: string | null;
  title: string | null;
}

interface DefaultContactsResult {
  rootEmail: string | null;
  alternateContacts: AlternateContactInfo[];
  notifiedEmails: string[];
}

interface NotificationResult {
  defaultChannel: { sns: boolean; slack: boolean; msTeams: boolean };
  teamNotifications: Array<{
    teamId: string;
    channels: string[];
    success: boolean;
  }>;
  defaultRouting?: DefaultContactsResult;
}

export const handler = async (event: NotifierInput | InvestigationResult): Promise<NotificationResult> => {
  // Support both the new format (from OpsCenter workflow) and legacy format (direct invocation)
  let investigationResult: InvestigationResult;
  if ('investigationResult' in event && event.investigationResult) {
    // New format: { investigationResult: {...}, opsItemId, opsItemUrl }
    const input = event as NotifierInput;
    investigationResult = {
      ...input.investigationResult,
      opsItemId: input.opsItemId || input.investigationResult.opsItemId || '',
      opsItemUrl: input.opsItemUrl || input.investigationResult.opsItemUrl || '',
    };
  } else {
    // Legacy format: direct InvestigationResult (backward compatible)
    investigationResult = event as InvestigationResult;
  }

  console.log('Routing notifications for investigation result:', JSON.stringify(investigationResult, null, 2));
  if (investigationResult.opsItemUrl) {
    console.log(`OpsItem link available: ${investigationResult.opsItemUrl}`);
  }

  const result: NotificationResult = {
    defaultChannel: { sns: false, slack: false, msTeams: false },
    teamNotifications: [],
  };

  // Determine which teams to notify from investigation findings
  const teamsFromFindings = extractTeamsFromFindings(investigationResult);
  const teamsFromAgent = investigationResult.teamsToNotify || [];
  const allTeamIds = [...new Set([...teamsFromFindings, ...teamsFromAgent])];

  console.log(`Teams to notify: ${allTeamIds.join(', ') || '(none identified — using default)'}`);

  // Load team configurations from DynamoDB
  const teamConfigs = await loadTeamConfigs(allTeamIds);

  // Route notifications to each team
  for (const config of teamConfigs) {
    if (!shouldNotifyTeam(config, investigationResult.priority)) {
      console.log(`Skipping ${config.teamId} — severity ${investigationResult.priority} not in their preferences`);
      continue;
    }

    const teamFindings = filterFindingsForTeam(investigationResult.findings, config.teamId);
    const teamResult = await notifyTeam(config, investigationResult, teamFindings);
    result.teamNotifications.push(teamResult);
  }

  // Always send to default channel (catch-all for unrouted or as summary)
  const shouldSendDefault = teamConfigs.length === 0 || allTeamIds.length === 0;

  if (shouldSendDefault) {
    // No team routing configured — use default routing fallback
    if (ENABLE_DEFAULT_ROUTING) {
      console.log('No team routing found. Falling back to default routing (root email + alternate contacts)');
      result.defaultRouting = await resolveAndNotifyDefaultContacts(investigationResult);
    }
    result.defaultChannel.sns = await sendSnsNotification(investigationResult);
    result.defaultChannel.slack = await sendDefaultSlackNotification(investigationResult);
    result.defaultChannel.msTeams = await sendDefaultMsTeamsNotification(investigationResult);
  } else {
    result.defaultChannel.sns = await sendSnsSummary(investigationResult, teamConfigs);
    result.defaultChannel.slack = await sendDefaultSlackNotification(investigationResult);
    result.defaultChannel.msTeams = await sendDefaultMsTeamsNotification(investigationResult);
  }

  return result;
};

function extractTeamsFromFindings(result: InvestigationResult): string[] {
  const teams: Set<string> = new Set();
  for (const finding of result.findings) {
    if (finding.owningTeam) {
      teams.add(finding.owningTeam.toLowerCase().replace(/\s+/g, '-'));
    }
  }
  return Array.from(teams);
}

async function loadTeamConfigs(teamIds: string[]): Promise<TeamConfig[]> {
  if (teamIds.length === 0) return [];
  const configs: TeamConfig[] = [];

  for (const teamId of teamIds) {
    try {
      const response = await dynamoClient.send(new GetItemCommand({
        TableName: TEAMS_TABLE,
        Key: { teamId: { S: teamId } },
      }));

      if (response.Item) {
        configs.push({
          teamId: response.Item.teamId.S!,
          teamName: response.Item.teamName?.S || teamId,
          email: response.Item.email?.S,
          slackWebhookUrl: response.Item.slackWebhookUrl?.S,
          slackChannel: response.Item.slackChannel?.S,
          msTeamsWebhookUrl: response.Item.msTeamsWebhookUrl?.S,
          notifyOn: response.Item.notifyOn?.SS || ['CRITICAL', 'HIGH', 'MEDIUM'],
        });
      } else {
        console.warn(`No config found for team ${teamId} — will use default channels`);
      }
    } catch (error) {
      console.error(`Failed to load config for team ${teamId}:`, error);
    }
  }
  return configs;
}

function shouldNotifyTeam(config: TeamConfig, priority: string): boolean {
  return config.notifyOn.includes(priority);
}

function filterFindingsForTeam(findings: Finding[], teamId: string): Finding[] {
  return findings.filter(f =>
    f.owningTeam?.toLowerCase().replace(/\s+/g, '-') === teamId
  );
}

async function notifyTeam(
  config: TeamConfig,
  result: InvestigationResult,
  teamFindings: Finding[]
): Promise<{ teamId: string; channels: string[]; success: boolean }> {
  const channels: string[] = [];
  let success = true;
  const findingsToSend = teamFindings.length > 0 ? teamFindings : result.findings;

  if (config.email) {
    try {
      await snsClient.send(new PublishCommand({
        TopicArn: SNS_TOPIC_ARN,
        Subject: formatSubject(result, config.teamName),
        Message: formatTeamEmail(result, findingsToSend, config.teamName),
        MessageAttributes: {
          team: { DataType: 'String', StringValue: config.teamId },
          priority: { DataType: 'String', StringValue: result.priority },
        },
      }));
      channels.push('email');
    } catch (error) {
      console.error(`Failed to send email to team ${config.teamId}:`, error);
      success = false;
    }
  }

  if (config.slackWebhookUrl) {
    try {
      const message = formatTeamSlackMessage(result, findingsToSend, config.teamName);
      await sendSlackMessage(config.slackWebhookUrl, message);
      channels.push('slack');
    } catch (error) {
      console.error(`Failed to send Slack to team ${config.teamId}:`, error);
      success = false;
    }
  }

  if (config.msTeamsWebhookUrl) {
    try {
      const card = formatMsTeamsAdaptiveCard(result, findingsToSend, config.teamName);
      await sendMsTeamsMessage(config.msTeamsWebhookUrl, card);
      channels.push('msteams');
    } catch (error) {
      console.error(`Failed to send MS Teams to team ${config.teamId}:`, error);
      success = false;
    }
  }

  console.log(`Notified team ${config.teamId} via: ${channels.join(', ')}`);
  return { teamId: config.teamId, channels, success };
}

function formatSubject(result: InvestigationResult, teamName: string): string {
  const emoji = getPriorityEmoji(result.priority);
  return `${emoji} [${result.priority}] Health Event Impact — ${teamName}`;
}

function formatTeamEmail(result: InvestigationResult, findings: Finding[], teamName: string): string {
  const lines: string[] = [];
  lines.push(`Health Event Impact — Team: ${teamName}`);
  lines.push('='.repeat(55));
  lines.push('');
  lines.push(`Priority: ${result.priority}`);
  lines.push(`Summary: ${result.summary}`);
  lines.push('');

  if (result.rootCause) {
    lines.push(`Root Cause: ${result.rootCause}`);
    lines.push('');
  }

  if (findings.length > 0) {
    lines.push(`Your Affected Resources:`);
    lines.push('-'.repeat(30));
    for (const finding of findings) {
      lines.push(`  [${finding.severity}] ${finding.description}`);
      if (finding.affectedResources.length > 0) {
        lines.push(`    Resources: ${finding.affectedResources.join(', ')}`);
      }
      lines.push('');
    }
  }

  if (result.recommendations.length > 0) {
    lines.push('Recommended Actions:');
    lines.push('-'.repeat(30));
    result.recommendations.forEach((rec, i) => {
      lines.push(`  ${i + 1}. [${rec.priority}] ${rec.description}`);
    });
  }

  lines.push('');
  lines.push('---');
  lines.push('Assessment by AWS DevOps Agent using application topology.');
  if (result.investigationLink) {
    lines.push(`Review full investigation: ${result.investigationLink}`);
  } else {
    lines.push('Review full investigation in the DevOps Agent console.');
  }
  if (result.opsItemUrl) {
    lines.push(`Track in OpsCenter: ${result.opsItemUrl}`);
  }
  return lines.join('\n');
}

function formatTeamSlackMessage(result: InvestigationResult, findings: Finding[], teamName: string): object {
  const emoji = getPriorityEmoji(result.priority);
  const color = getPriorityColor(result.priority);

  const findingsText = findings
    .map(f => `• *[${f.severity}]* ${f.description}${f.affectedResources.length > 0 ? `\n  Resources: \`${f.affectedResources.join('`, `')}\`` : ''}`)
    .join('\n');

  const recommendationsText = result.recommendations
    .map((r, i) => `${i + 1}. [${r.priority}] ${r.description}`)
    .join('\n');

  return {
    blocks: [
      {
        type: 'header',
        text: { type: 'plain_text', text: `${emoji} Health Event Impact — ${teamName}` },
      },
      {
        type: 'section',
        text: { type: 'mrkdwn', text: `*Priority:* ${result.priority}\n*Summary:* ${result.summary}` },
      },
    ],
    attachments: [
      {
        color,
        blocks: [
          ...(findingsText ? [{
            type: 'section',
            text: { type: 'mrkdwn', text: `*Your Affected Resources:*\n${findingsText}` },
          }] : []),
          ...(recommendationsText ? [{
            type: 'section',
            text: { type: 'mrkdwn', text: `*Recommended Actions:*\n${recommendationsText}` },
          }] : []),
          {
            type: 'context',
            elements: [{ type: 'mrkdwn', text: buildSlackContextText(result) }],
          },
        ],
      },
    ],
    investigationLink: result.investigationLink || '',
    opsItemUrl: result.opsItemUrl || '',
  };
}

/**
 * Builds the Slack context text with links to investigation and OpsItem.
 */
function buildSlackContextText(result: InvestigationResult): string {
  const parts: string[] = ['_Assessment by AWS DevOps Agent_'];

  if (result.investigationLink) {
    parts.push(`<${result.investigationLink}|View Investigation>`);
  }
  if (result.opsItemUrl) {
    parts.push(`<${result.opsItemUrl}|Track in OpsCenter>`);
  }

  if (parts.length === 1) {
    // No links available
    parts.push('Review full investigation in console');
  }

  return parts.join(' • ');
}

async function sendSnsNotification(result: InvestigationResult): Promise<boolean> {
  try {
    await snsClient.send(new PublishCommand({
      TopicArn: SNS_TOPIC_ARN,
      Subject: `${getPriorityEmoji(result.priority)} [${result.priority}] Health Event Impact Detected`,
      Message: formatTeamEmail(result, result.findings, 'All Teams'),
      MessageAttributes: { priority: { DataType: 'String', StringValue: result.priority } },
    }));
    return true;
  } catch (error) {
    console.error('Failed to send default SNS notification:', error);
    return false;
  }
}

async function sendSnsSummary(result: InvestigationResult, teams: TeamConfig[]): Promise<boolean> {
  const teamList = teams.map(t => t.teamName).join(', ');
  try {
    await snsClient.send(new PublishCommand({
      TopicArn: SNS_TOPIC_ARN,
      Subject: `${getPriorityEmoji(result.priority)} [${result.priority}] Health Event — ${teams.length} teams notified`,
      Message: `Summary: ${result.summary}\n\nTeams notified: ${teamList}\n\nSee DevOps Agent console for full investigation details.`,
      MessageAttributes: { priority: { DataType: 'String', StringValue: result.priority } },
    }));
    return true;
  } catch (error) {
    console.error('Failed to send SNS summary:', error);
    return false;
  }
}

async function sendDefaultSlackNotification(result: InvestigationResult): Promise<boolean> {
  if (!SLACK_WEBHOOK_PARAM_NAME) return false;
  try {
    const slackWebhookUrl = await getSecret(SLACK_WEBHOOK_PARAM_NAME);
    if (!slackWebhookUrl) return false;
    const message = formatTeamSlackMessage(result, result.findings, 'All Teams');
    await sendSlackMessage(slackWebhookUrl, message);
    return true;
  } catch (error) {
    console.error('Failed to send default Slack notification:', error);
    return false;
  }
}

function getPriorityEmoji(priority: string): string {
  const emojis: Record<string, string> = { CRITICAL: '🚨', HIGH: '⚠️', MEDIUM: '🔶', LOW: '🔵', MINIMAL: '✅' };
  return emojis[priority] || '❓';
}

function getPriorityColor(priority: string): string {
  const colors: Record<string, string> = { CRITICAL: '#dc3545', HIGH: '#fd7e14', MEDIUM: '#ffc107', LOW: '#0dcaf0', MINIMAL: '#198754' };
  return colors[priority] || '#6c757d';
}

function sendSlackMessage(webhookUrl: string, message: object): Promise<void> {
  const parsedUrl = new url.URL(webhookUrl);

  // Detect if this is a Workflow Trigger (/triggers/) vs Incoming Webhook (/services/)
  const isWorkflowTrigger = parsedUrl.pathname.includes('/triggers/');
  let payload: object;

  if (isWorkflowTrigger) {
    // Slack Workflow triggers expect flat key-value pairs
    payload = formatWorkflowTriggerPayload(message);
  } else {
    // Standard incoming webhook accepts blocks/attachments
    payload = message;
  }

  const postData = JSON.stringify(payload);

  // Inline retry logic: 2 retries, exponential backoff 1s–4s
  const MAX_RETRIES = 2;
  const BASE_DELAY_MS = 1000;
  const MAX_DELAY_MS = 4000;

  async function attempt(retryCount: number): Promise<void> {
    try {
      await doSlackRequest(parsedUrl, postData);
    } catch (error: any) {
      const statusCode = error.statusCode as number | undefined;
      // Do not retry 4xx errors (except 429 Too Many Requests)
      if (statusCode && statusCode >= 400 && statusCode < 500 && statusCode !== 429) {
        throw error;
      }
      if (retryCount >= MAX_RETRIES) {
        throw error;
      }
      const delay = Math.min(BASE_DELAY_MS * Math.pow(2, retryCount), MAX_DELAY_MS);
      console.warn(`Slack webhook attempt ${retryCount + 1} failed (status: ${statusCode || 'network error'}), retrying in ${delay}ms...`);
      await sleep(delay);
      return attempt(retryCount + 1);
    }
  }

  return attempt(0);
}

function doSlackRequest(parsedUrl: url.URL, postData: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: parsedUrl.hostname,
      path: parsedUrl.pathname,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(postData) },
      timeout: 10000,
    };
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode === 200 || res.statusCode === 202) { resolve(); }
        else {
          const err: any = new Error(`Slack webhook returned status ${res.statusCode}: ${data}`);
          err.statusCode = res.statusCode;
          reject(err);
        }
      });
    });
    req.on('timeout', () => {
      req.destroy();
      const err: any = new Error('Slack webhook request timed out');
      err.statusCode = undefined;
      reject(err);
    });
    req.on('error', reject);
    req.write(postData);
    req.end();
  });
}

/**
 * Formats the notification payload for Slack Workflow triggers.
 * Workflow triggers expect flat key-value pairs that map to workflow variables.
 */
function formatWorkflowTriggerPayload(message: any): object {
  // Extract relevant info from the blocks/attachments format
  let priority = '';
  let summary = '';
  let findings = '';
  let recommendations = '';
  let teamName = '';

  // Parse from blocks format
  if (message.blocks) {
    for (const block of message.blocks) {
      if (block.type === 'header' && block.text?.text) {
        teamName = block.text.text;
      }
      if (block.type === 'section' && block.text?.text) {
        const text = block.text.text;
        const priorityMatch = text.match(/\*Priority:\*\s*(\w+)/);
        const summaryMatch = text.match(/\*Summary:\*\s*(.+)/);
        if (priorityMatch) priority = priorityMatch[1];
        if (summaryMatch) summary = summaryMatch[1];
      }
    }
  }

  // Parse from attachments
  if (message.attachments) {
    for (const attachment of message.attachments) {
      if (attachment.blocks) {
        for (const block of attachment.blocks) {
          if (block.type === 'section' && block.text?.text) {
            if (block.text.text.includes('Affected Resources')) {
              findings = block.text.text.replace('*Your Affected Resources:*\n', '');
            }
            if (block.text.text.includes('Recommended Actions')) {
              recommendations = block.text.text.replace('*Recommended Actions:*\n', '');
            }
          }
        }
      }
    }
  }

  return {
    title: teamName || 'Health Event Impact Alert',
    priority: priority || 'UNKNOWN',
    summary: summary || 'Health event detected',
    findings: findings || 'See investigation details',
    recommendations: recommendations || 'Review in DevOps Agent console',
    investigation_link: (message as any).investigationLink || '',
    opsitem_link: (message as any).opsItemUrl || '',
  };
}

// ─── Microsoft Teams Integration ────────────────────────────────────────────

async function sendDefaultMsTeamsNotification(result: InvestigationResult): Promise<boolean> {
  if (!MSTEAMS_WEBHOOK_PARAM_NAME) return false;
  try {
    const msTeamsWebhookUrl = await getSecret(MSTEAMS_WEBHOOK_PARAM_NAME);
    if (!msTeamsWebhookUrl) return false;
    const card = formatMsTeamsAdaptiveCard(result, result.findings, 'All Teams');
    await sendMsTeamsMessage(msTeamsWebhookUrl, card);
    return true;
  } catch (error) {
    console.error('Failed to send default MS Teams notification:', error);
    return false;
  }
}

function formatMsTeamsAdaptiveCard(result: InvestigationResult, findings: Finding[], teamName: string): object {
  const priorityColor = getMsTeamsPriorityColor(result.priority);
  const emoji = getPriorityEmoji(result.priority);

  const findingsText = findings
    .map(f => `- **[${f.severity}]** ${f.description}${f.affectedResources.length > 0 ? `\n  Resources: \`${f.affectedResources.join('`, `')}\`` : ''}`)
    .join('\n');

  const recommendationsText = result.recommendations
    .map((r, i) => `${i + 1}. **[${r.priority}]** ${r.description}`)
    .join('\n');

  // Adaptive Card format for Microsoft Teams (via Power Automate / Workflows)
  const card: any = {
    type: 'message',
    attachments: [
      {
        contentType: 'application/vnd.microsoft.card.adaptive',
        contentUrl: null,
        content: {
          '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json',
          type: 'AdaptiveCard',
          version: '1.4',
          body: [
            {
              type: 'Container',
              style: priorityColor === '#dc3545' ? 'attention' : priorityColor === '#fd7e14' ? 'warning' : 'default',
              items: [
                {
                  type: 'TextBlock',
                  text: `${emoji} Health Event Impact — ${teamName}`,
                  weight: 'Bolder',
                  size: 'Large',
                  wrap: true,
                },
              ],
            },
            {
              type: 'FactSet',
              facts: [
                { title: 'Priority', value: result.priority },
                { title: 'Summary', value: result.summary },
                ...(result.rootCause ? [{ title: 'Root Cause', value: result.rootCause }] : []),
              ],
            },
            {
              type: 'TextBlock',
              text: '**Affected Resources:**',
              weight: 'Bolder',
              spacing: 'Medium',
              wrap: true,
            },
            {
              type: 'TextBlock',
              text: findingsText || 'No specific resources identified',
              wrap: true,
            },
            {
              type: 'TextBlock',
              text: '**Recommended Actions:**',
              weight: 'Bolder',
              spacing: 'Medium',
              wrap: true,
            },
            {
              type: 'TextBlock',
              text: recommendationsText || 'No recommendations',
              wrap: true,
            },
          ],
          actions: [],
        },
      },
    ],
  };

  // Add investigation link button if available
  if (result.investigationLink) {
    card.attachments[0].content.actions.push({
      type: 'Action.OpenUrl',
      title: 'View Investigation in DevOps Agent',
      url: result.investigationLink,
    });
  }

  // Add OpsItem link button if available
  if (result.opsItemUrl) {
    card.attachments[0].content.actions.push({
      type: 'Action.OpenUrl',
      title: 'Track in OpsCenter',
      url: result.opsItemUrl,
    });
  }

  return card;
}

function sendMsTeamsMessage(webhookUrl: string, message: object): Promise<void> {
  const parsedUrl = new url.URL(webhookUrl);
  const postData = JSON.stringify(message);

  // Inline retry logic: 2 retries, exponential backoff 1s–4s
  const MAX_RETRIES = 2;
  const BASE_DELAY_MS = 1000;
  const MAX_DELAY_MS = 4000;

  async function attempt(retryCount: number): Promise<void> {
    try {
      await doMsTeamsRequest(parsedUrl, postData);
    } catch (error: any) {
      const statusCode = error.statusCode as number | undefined;
      // Do not retry 4xx errors (except 429 Too Many Requests)
      if (statusCode && statusCode >= 400 && statusCode < 500 && statusCode !== 429) {
        throw error;
      }
      if (retryCount >= MAX_RETRIES) {
        throw error;
      }
      const delay = Math.min(BASE_DELAY_MS * Math.pow(2, retryCount), MAX_DELAY_MS);
      console.warn(`MS Teams webhook attempt ${retryCount + 1} failed (status: ${statusCode || 'network error'}), retrying in ${delay}ms...`);
      await sleep(delay);
      return attempt(retryCount + 1);
    }
  }

  return attempt(0);
}

function doMsTeamsRequest(parsedUrl: url.URL, postData: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: parsedUrl.hostname,
      path: parsedUrl.pathname + (parsedUrl.search || ''),
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData),
      },
      timeout: 10000,
    };
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode === 200 || res.statusCode === 202) {
          resolve();
        } else {
          const err: any = new Error(`MS Teams webhook returned status ${res.statusCode}: ${data}`);
          err.statusCode = res.statusCode;
          reject(err);
        }
      });
    });
    req.on('timeout', () => {
      req.destroy();
      const err: any = new Error('MS Teams webhook request timed out');
      err.statusCode = undefined;
      reject(err);
    });
    req.on('error', reject);
    req.write(postData);
    req.end();
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function getMsTeamsPriorityColor(priority: string): string {
  const colors: Record<string, string> = {
    CRITICAL: '#dc3545',
    HIGH: '#fd7e14',
    MEDIUM: '#ffc107',
    LOW: '#0dcaf0',
    MINIMAL: '#198754',
  };
  return colors[priority] || '#6c757d';
}

/**
 * Default routing fallback: resolves contacts from the AWS Account API
 * and sends notifications to the root email and alternate contacts.
 * This is used when no team routing configuration or contacts are provided.
 *
 * Multi-account support: If the Health event originated from a different account
 * (Organizations view), the AccountId parameter is used to fetch that account's
 * alternate contacts from the management account.
 */
async function resolveAndNotifyDefaultContacts(result: InvestigationResult): Promise<DefaultContactsResult> {
  const defaultResult: DefaultContactsResult = {
    rootEmail: null,
    alternateContacts: [],
    notifiedEmails: [],
  };

  const sourceAccountId = result.sourceAccountId;
  // Only use AccountId parameter for cross-account calls (from management account to member accounts)
  // When sourceAccountId matches the current account (or is not set), call without AccountId
  const currentAccountId = process.env.AWS_ACCOUNT_ID || '';
  const isCrossAccount = sourceAccountId && sourceAccountId !== currentAccountId;
  const accountParam = isCrossAccount ? { AccountId: sourceAccountId } : {};
  const accountLabel = isCrossAccount ? `member account ${sourceAccountId}` : 'local account';

  console.log(`Resolving default contacts for ${accountLabel}`);

  // Fetch primary contact information
  try {
    const contactInfo = await accountClient.send(new GetContactInformationCommand(accountParam));
    if (contactInfo.ContactInformation) {
      defaultResult.rootEmail = contactInfo.ContactInformation.FullName || 'Account Owner';
      console.log(`Primary contact resolved: ${defaultResult.rootEmail}`);
    }
  } catch (error: any) {
    if (error.name === 'AccessDeniedException') {
      console.warn(`No permission to read contact information for ${accountLabel} (account:GetContactInformation)`);
    } else {
      console.error(`Failed to fetch primary contact information for ${accountLabel}:`, error.message);
    }
  }

  // Fetch alternate contacts (Operations, Security, Billing)
  const contactTypes = [
    AlternateContactType.OPERATIONS,
    AlternateContactType.SECURITY,
    AlternateContactType.BILLING,
  ];

  for (const contactType of contactTypes) {
    try {
      const response = await accountClient.send(
        new GetAlternateContactCommand({
          AlternateContactType: contactType,
          ...accountParam,
        })
      );

      if (response.AlternateContact) {
        const contact: AlternateContactInfo = {
          type: contactType,
          name: response.AlternateContact.Name || null,
          email: response.AlternateContact.EmailAddress || null,
          phone: response.AlternateContact.PhoneNumber || null,
          title: response.AlternateContact.Title || null,
        };
        defaultResult.alternateContacts.push(contact);

        // Send email notification to each alternate contact with an email
        if (contact.email) {
          try {
            await snsClient.send(new PublishCommand({
              TopicArn: SNS_TOPIC_ARN,
              Subject: formatDefaultRoutingSubject(result, contact.type),
              Message: formatDefaultRoutingEmail(result, contact, sourceAccountId),
              MessageAttributes: {
                priority: { DataType: 'String', StringValue: result.priority },
                routingType: { DataType: 'String', StringValue: 'default-fallback' },
                contactType: { DataType: 'String', StringValue: contact.type },
                ...(sourceAccountId ? { sourceAccountId: { DataType: 'String', StringValue: sourceAccountId } } : {}),
              },
            }));
            defaultResult.notifiedEmails.push(contact.email);
            console.log(`Default routing: notified ${contact.type} contact at ${contact.email}`);
          } catch (publishError) {
            console.error(`Failed to notify ${contact.type} contact:`, publishError);
          }
        }
      }
    } catch (error: any) {
      if (error.name === 'ResourceNotFoundException') {
        console.log(`No alternate contact configured for type: ${contactType} (${accountLabel})`);
      } else if (error.name === 'AccessDeniedException') {
        console.warn(`No permission to read alternate contact: ${contactType} (${accountLabel})`);
      } else {
        console.error(`Failed to fetch alternate contact ${contactType} for ${accountLabel}:`, error.message);
      }
    }
  }

  console.log(`Default routing complete: ${defaultResult.notifiedEmails.length} contact(s) notified for ${accountLabel}`);
  return defaultResult;
}

function formatDefaultRoutingSubject(result: InvestigationResult, contactType: string): string {
  const emoji = getPriorityEmoji(result.priority);
  return `${emoji} [${result.priority}] Health Event Impact — Default Routing (${contactType})`;
}

function formatDefaultRoutingEmail(result: InvestigationResult, contact: AlternateContactInfo, sourceAccountId?: string): string {
  const lines: string[] = [];
  lines.push(`AWS Health Event Impact — Default Routing`);
  lines.push('='.repeat(55));
  lines.push('');
  lines.push(`NOTE: This notification was sent via DEFAULT ROUTING because no team`);
  lines.push(`routing configuration was found for the affected resources.`);
  lines.push('');
  lines.push(`Recipient: ${contact.name || 'Unknown'} (${contact.type} Contact)`);
  if (sourceAccountId) {
    lines.push(`Source Account: ${sourceAccountId}`);
  }
  lines.push(`Priority: ${result.priority}`);
  lines.push(`Summary: ${result.summary}`);
  lines.push('');

  if (result.rootCause) {
    lines.push(`Root Cause: ${result.rootCause}`);
    lines.push('');
  }

  if (result.findings.length > 0) {
    lines.push('Affected Resources:');
    lines.push('-'.repeat(30));
    for (const finding of result.findings) {
      lines.push(`  [${finding.severity}] ${finding.description}`);
      if (finding.affectedResources.length > 0) {
        lines.push(`    Resources: ${finding.affectedResources.join(', ')}`);
      }
      lines.push('');
    }
  }

  if (result.recommendations.length > 0) {
    lines.push('Recommended Actions:');
    lines.push('-'.repeat(30));
    result.recommendations.forEach((rec, i) => {
      lines.push(`  ${i + 1}. [${rec.priority}] ${rec.description}`);
    });
  }

  lines.push('');
  lines.push('---');
  lines.push('ACTION REQUIRED: Configure team routing in the health-analyzer-teams');
  lines.push('DynamoDB table to enable targeted notifications for your teams.');
  lines.push('');
  lines.push('Assessment by AWS DevOps Agent using application topology.');
  if (result.investigationLink) {
    lines.push(`Review full investigation: ${result.investigationLink}`);
  } else {
    lines.push('Review full investigation in the DevOps Agent console.');
  }
  if (result.opsItemUrl) {
    lines.push(`Track in OpsCenter: ${result.opsItemUrl}`);
  }
  return lines.join('\n');
}

