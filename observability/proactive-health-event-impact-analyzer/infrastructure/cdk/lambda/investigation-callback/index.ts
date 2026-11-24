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
      limit: 1,
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

// Journal paging: ListJournalRecordsRequest.limit accepts 1-100 (default 100).
// Cap at 3 pages so a very long investigation cannot make the callback loop unbounded.
const JOURNAL_PAGE_LIMIT = 100;
const MAX_JOURNAL_PAGES = 3;

// "\n---" already terminates every section regex in parseAgentAnalysis, so sections
// never bleed from one concatenated message into the next.
const MESSAGE_SEPARATOR = '\n\n---\n\n';
const SUMMARY_SECTION_RE = /## Summary/;
const FINDINGS_SECTION_RE = /## Key Findings/;
// Same alternation and row shape as the recommendations parsing in parseAgentAnalysis.
const RECS_HEADING_RE = /(?:### 4\.|## (?:Recommended )?Actions|What actions)/;
const RECS_ROW_RE = /\|\s*\*\*P\d+\*\*\s*\|/;

/** Which report sections a single assistant message contains. */
function reportSections(text: string): { summary: boolean; findings: boolean; recs: boolean } {
  return {
    summary: SUMMARY_SECTION_RE.test(text),
    findings: FINDINGS_SECTION_RE.test(text),
    recs: RECS_HEADING_RE.test(text) || RECS_ROW_RE.test(text),
  };
}

/**
 * A newer message that carries only a P1/P2 table (no heading) must still beat an older
 * heading + table, so give it a heading. Used only when several messages are merged.
 */
function withRecsHeading(text: string): string {
  if (RECS_HEADING_RE.test(text) || !RECS_ROW_RE.test(text)) return text;
  return text.replace(/^\|/m, '## Recommended Actions\n\n|');
}

/**
 * Merges assistant messages (newest first) into the single string parseAgentAnalysis expects.
 * Every section regex there takes the FIRST match, so concatenating the contributing messages
 * newest first gives "latest message that has the section wins" per section. A message
 * contributes only if it supplies a section no newer message supplied; narration-only
 * messages are left out so they cannot feed the impact keyword scan or the recs fallback.
 */
function mergeAnalysisMessages(messagesNewestFirst: string[]): string | null {
  if (messagesNewestFirst.length === 0) return null;
  const picked: string[] = [];
  let hasSummary = false;
  let hasFindings = false;
  let hasRecs = false;
  for (const text of messagesNewestFirst) {
    const s = reportSections(text);
    if ((s.summary && !hasSummary) || (s.findings && !hasFindings) || (s.recs && !hasRecs)) {
      picked.push(text);
      hasSummary = hasSummary || s.summary;
      hasFindings = hasFindings || s.findings;
      hasRecs = hasRecs || s.recs;
    }
    if (hasSummary && hasFindings && hasRecs) break; // older messages cannot add anything
  }
  if (picked.length === 0) return messagesNewestFirst[0]; // nothing structured: newest assistant text
  if (picked.length === 1) return picked[0];
  return picked.map(withRecsHeading).join(MESSAGE_SEPARATOR);
}

/**
 * Returns the text of an assistant journal record, or null when the record is not an
 * assistant message, has no text blocks (thinking/tool blocks are skipped) or is malformed.
 */
function assistantText(content: unknown): string | null {
  if (!content) return null;
  try {
    const parsed: MessageContent = typeof content === 'string' ? JSON.parse(content) : content as MessageContent;
    if (parsed.role !== 'assistant') return null;
    const text = parsed.content
      .filter((c) => c.type === 'text' && c.text)
      .map((c) => c.text!)
      .join('\n\n');
    return text || null;
  } catch (error) {
    console.warn('Skipping malformed journal record:', error);
    return null;
  }
}

/**
 * Retrieves the agent's analysis. The agent may send several assistant messages (for
 * example a corrected recommendations table after a sub-agent finishes), so read the newest
 * messages, page if needed, and merge them per section (see mergeAnalysisMessages).
 */
async function getAgentAnalysis(agentSpaceId: string, executionId: string): Promise<string | null> {
  try {
    const texts: string[] = []; // assistant text, newest first
    let nextToken: string | undefined;
    for (let page = 0; page < MAX_JOURNAL_PAGES; page++) {
      const response = await devOpsAgentClient.send(new ListJournalRecordsCommand({
        agentSpaceId,
        executionId,
        recordType: 'message',
        order: 'DESC',
        limit: JOURNAL_PAGE_LIMIT,
        ...(nextToken ? { nextToken } : {}),
      }));
      for (const record of response.records ?? []) {
        const text = assistantText(record.content);
        if (text) texts.push(text);
      }
      nextToken = response.nextToken;
      const covered = texts.map(reportSections);
      if (!nextToken || (covered.some(c => c.summary) && covered.some(c => c.findings) && covered.some(c => c.recs))) break;
    }
    return mergeAnalysisMessages(texts);
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

  // Decision 2 fail-safe: shown whenever the agent output cannot be summarized
  // safely, instead of leaking unvalidated or chain-of-thought prose.
  const SUMMARY_FAIL_SAFE =
    'The investigation output did not match the expected format and could not be ' +
    'summarized automatically. Open the investigation in the DevOps Agent console ' +
    'to review the full findings.';

  // Extract summary (## Summary section only — no paragraph-scavenging fallback).
  const summaryMatch = analysis.match(/## Summary\s*\n+([\s\S]*?)(?=\n##|\n---|\n\|)/);
  if (summaryMatch) {
    const candidate = summaryMatch[1].trim().replace(/\n/g, ' ').substring(0, 500);
    result.summary = isChainOfThought(candidate) ? SUMMARY_FAIL_SAFE : candidate;
  } else {
    // No ## Summary section at all — fail safe. The former "first paragraph > 50
    // chars" fallback is removed entirely: it had no content validation and was
    // the exact vector for leaked chain-of-thought narration.
    result.summary = SUMMARY_FAIL_SAFE;
  }

  // Decision 1: findings come ONLY from a real "## Key Findings" section. The
  // "## Answers to Your Questions" fallback and the whole-analysis fallback are
  // removed entirely — scavenging free-form sections for "- **Bold**: text"
  // bullets is exactly how Jira/Mitigation/Tracking/date meta-lines leaked into
  // findings. If "## Key Findings" is absent, findings stay empty; the
  // hasImpact/generic-finding fallback further below still produces a usable
  // notification from the (validated) summary.
  const findingsSection = analysis.match(/## Key Findings([\s\S]*?)(?=\n## (?!Key)|\n---)/);
  const contentToSearch = findingsSection?.[1] || '';

  // Explicit severities of ALL accepted findings, used to compute the overall severity.
  // Kept separate from result.findings, which is capped at 5 for display —
  // otherwise a CRITICAL workload listed 6th would be dropped and the overall
  // severity (and the resulting OpsItem) would be under-reported. A bullet without a
  // severity word adds nothing here, so it cannot raise the overall severity.
  const explicitSeverities: string[] = [];
  // Only TOP-LEVEL bullets (no leading indentation) are findings; indented
  // sub-bullets are explanation under a finding.
  const bulletFindings = contentToSearch.match(/^[-•*][ \t]+\*\*(.+?)\*\*[:\s]*(.+)/gm);
  if (bulletFindings) {
    let accepted = 0;
    bulletFindings.forEach((bullet) => {
      const match = bullet.match(/^[-•*][ \t]+\*\*(.+?)\*\*[:\s]*(.*)/);
      if (!match) return;
      // FR-B3: skip meta-label/date bullets (e.g. "- **Mitigation**: ...",
      // "- **2026-10-13**: ..."); these belong to other sections and must never
      // be treated as resource findings. Scanning continues for the rest.
      if (isMetaOrDateLabel(match[1])) return;
      // The agent writes the impact severity in the bullet detail
      // ("- **web-tier**: HIGH — ..."); use it, not the intake priority.
      const explicit = parseSeverity(match[2]);
      if (explicit) explicitSeverities.push(explicit);
      const severity = explicit || fallbackPriority;
      if (accepted < 5) {
        result.findings.push({
          description: `${match[1]}: ${match[2]}`.trim(),
          severity,
          affectedResources: [],
        });
        accepted++;
      }
    });
  }

  // Extract recommendations from priority tables or numbered lists.
  const recsSection = analysis.match(/(?:### 4\.|## (?:Recommended )?Actions|What actions)([\s\S]*?)(?=\n## (?!Rec)|\n---|\*\*No immediate)/);
  // FR-B4: when the heading itself is missing (e.g. a delegated plan whose table
  // landed outside any recognized section), the table-row pattern (P1/P2/...)
  // falls back to the whole analysis rather than leaving recommendations empty.
  // The numbered-list pattern is used only inside a real recommendations section
  // and is anchored to line starts: an unanchored pattern matches sentences that
  // end with a number (for example a region name such as us-east-1.) and would
  // turn a no-impact report into a recommendation.
  const recsText = recsSection?.[1] ?? analysis;

  const tableRows = recsText.match(/\|\s*\*\*P\d+\*\*\s*\|\s*(.+?)\s*\|/g);
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

  if (result.recommendations.length === 0 && recsSection) {
    const numbered = recsText.match(/^[ \t]*\d{1,2}\.[ \t]+(.+)$/gm);
    if (numbered) {
      for (const item of numbered.slice(0, 5)) {
        const itemMatch = item.match(/^[ \t]*\d{1,2}\.[ \t]+(.+)$/);
        if (itemMatch) {
          result.recommendations.push({
            description: itemMatch[1].trim(),
            priority: 'MEDIUM',
          });
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

  // If we have no findings but have recommendations, create a generic finding from the
  // summary. Not when the summary is the fail-safe text (rejected or missing summary):
  // that text is not a finding and must not show up as one.
  if (result.findings.length === 0 && result.hasImpact && result.summary !== SUMMARY_FAIL_SAFE) {
    result.findings.push({
      description: result.summary.substring(0, 200),
      severity: fallbackPriority,
      affectedResources: [],
    });
  }

  // Overall severity = the highest EXPLICIT severity across ALL accepted findings (the
  // agent's conclusion). The intake fallbackPriority is used only when findings exist
  // but none states a severity (including the generic finding above).
  result.overallSeverity = highestSeverity(explicitSeverities)
    ?? (result.findings.length > 0 ? fallbackPriority : null);

  // Invariant: the header/overall `priority` (buildOutput) is deliberately the max
  // across all findings' severities. No individual finding bullet shown in a
  // notification body may carry a severity ranked ABOVE that computed overall
  // value — otherwise the body would read as self-contradictory (e.g. a CRITICAL
  // bullet under a HIGH header). Clamp any outlier down to the overall severity.
  // (an unlabeled finding is displayed at fallbackPriority and may exceed the explicit
  // maximum; this pass clamps it down.)
  if (result.overallSeverity) {
    const cap = SEVERITY_RANK[result.overallSeverity] ?? -1;
    for (const finding of result.findings) {
      if ((SEVERITY_RANK[finding.severity] ?? -1) > cap) {
        finding.severity = result.overallSeverity;
      }
    }
  }

  return result;
}

function mapPriority(p: string): string {
  const map: Record<string, string> = { P1: 'CRITICAL', P2: 'HIGH', P3: 'MEDIUM', P4: 'LOW' };
  return map[p] || 'MEDIUM';
}

// Severity ranking used to pick the overall (highest) investigation severity.
const SEVERITY_RANK: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, MINIMAL: 0 };

// Case-insensitive leading first-person/process phrases, or any mention of the
// sub-agent/delegation vocabulary the skill now forbids in ## Summary. Matched
// against the extracted candidate text (not the whole analysis) so a legitimate
// summary that happens to discuss "mitigation" as a resource topic is not
// penalized unless it reads as narration.
//
// Two sub-patterns handle "I" specifically because contraction forms ("I've",
// "I'm", "I'll") and space-separated forms ("I now", "I have", "I confirmed")
// need different boundary handling after "i": contractions have no space before
// the suffix, space-separated forms require \s+ before the suffix. "let me" is
// deliberately UNANCHORED (not nested inside the leading ^\s*(...) group) because
// it legitimately appears as a later sentence in a candidate string (e.g. "I now
// have conclusive evidence. Let me synthesize the investigation.") — anchoring it
// to the start would miss exactly that case.
const CHAIN_OF_THOUGHT_PATTERN =
  /^\s*i['’]?(ve|m|ll)\b|^\s*i\s+(now|have|will|won['’]?t|confirmed)\b|\blet me\b|\b(sub-?agent|mitigation specialist|as its own artifact|won['’]?t duplicate|no new information)\b/i;

/** True when the candidate summary reads as process narration rather than a factual impact statement. */
function isChainOfThought(candidate: string): boolean {
  return CHAIN_OF_THOUGHT_PATTERN.test(candidate);
}

// Bold labels that are legitimate "- **Label**: value" bullets from OTHER
// sections (## Jira Tracking, ## Notification Routing) or stray meta-commentary —
// never a resource name. Matched against match[1] only.
const META_LABEL_DENYLIST =
  /^(mitigation|jira|tracking|impact|severity|root cause|action|issue key|reason for skip|next steps|status)$/i;

// A bold label that is actually a date/timestamp, e.g. "**2026-10-13**: ..." or
// "**July 29, 2027**: ...".
const DATE_LABEL_PATTERN =
  /^\d{4}-\d{2}-\d{2}|^(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d/i;

/** True when a "## Key Findings" bullet's bold label is meta-info or a date, not a resource name. */
function isMetaOrDateLabel(label: string): boolean {
  const trimmed = label.trim();
  return META_LABEL_DENYLIST.test(trimmed) || DATE_LABEL_PATTERN.test(trimmed);
}

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
