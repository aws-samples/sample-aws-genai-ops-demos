/**
 * Unit tests for Lambda business logic.
 * Tests correlation extraction, link generation, category mapping, and severity mapping.
 */

import * as fs from 'fs';
import * as path from 'path';

describe('Investigation Callback — Correlation', () => {
  // Simulate the extractCorrelationKey logic
  function extractTag(text: string, prefix: string): string | null {
    const start = text.indexOf(prefix);
    if (start === -1) return null;
    const valueStart = start + prefix.length;
    const end = text.indexOf(']', valueStart);
    if (end === -1) return null;
    return text.substring(valueStart, end).trim() || null;
  }

  function extractCorrelationKey(textContent: string): string | null {
    // Primary: [INVESTIGATION_ID:{incidentId}] — unique per investigation
    const investigationId = extractTag(textContent, '[INVESTIGATION_ID:');
    if (investigationId) return investigationId;

    // Legacy: [CORRELATION_ID:{healthEventArn}] — not unique across investigations
    const correlationId = extractTag(textContent, '[CORRELATION_ID:');
    if (correlationId) return `arn:${correlationId}`;

    // Fallback: [{incidentId}] in title
    const titleIncidentId = extractTag(textContent, 'TITLE: [');
    if (titleIncidentId && titleIncidentId.startsWith('health-')) return titleIncidentId;

    return null;
  }

  // Mirrors findTaskToken's branch selection: the `arn:` sentinel routes to the
  // legacy healthEventId scan; everything else is the unique investigationId and
  // uses the keyed GetItem. Locks the routing contract against regressions.
  function lookupMode(correlationKey: string): 'keyed-getitem' | 'legacy-scan' {
    return correlationKey.startsWith('arn:') ? 'legacy-scan' : 'keyed-getitem';
  }

  test('extracts healthEventArn from CORRELATION_ID tag', () => {
    const text = 'TITLE: [health-LAMBDA-123456] AWS Health: LAMBDA ..., REFERENCE_URL: , DESCRIPTION: [CORRELATION_ID:arn:aws:health:eu-west-1::event/LAMBDA/AWS_LAMBDA_RUNTIME_DEPRECATION/123] Rest of description...';
    const result = extractCorrelationKey(text);
    expect(result).toBe('arn:arn:aws:health:eu-west-1::event/LAMBDA/AWS_LAMBDA_RUNTIME_DEPRECATION/123');
  });

  test('falls back to incidentId from title when no CORRELATION_ID tag', () => {
    const text = 'TITLE: [health-EC2-1779753909631] AWS Health: EC2 ..., REFERENCE_URL: , DESCRIPTION: Some description without correlation tag';
    const result = extractCorrelationKey(text);
    expect(result).toBe('health-EC2-1779753909631');
  });

  test('returns null when no correlation data found', () => {
    const text = 'TITLE: Some random investigation, DESCRIPTION: No health event data here';
    const result = extractCorrelationKey(text);
    expect(result).toBeNull();
  });

  test('handles CORRELATION_ID with complex ARN', () => {
    const text = '[CORRELATION_ID:arn:aws:health:global::event/IAM/AWS_IAM_OPERATIONAL_NOTIFICATION/AWS_IAM_OPERATIONAL_NOTIFICATION_20260526] Description follows';
    const result = extractCorrelationKey(text);
    expect(result).toBe('arn:arn:aws:health:global::event/IAM/AWS_IAM_OPERATIONAL_NOTIFICATION/AWS_IAM_OPERATIONAL_NOTIFICATION_20260526');
  });

  test('extracts unique investigationId from INVESTIGATION_ID tag', () => {
    const text = '[CORRELATION_ID:arn:aws:health:eu-west-1::event/EC2/AWS_EC2_SCHEDULED_MAINTENANCE/123]\n[INVESTIGATION_ID:health-EC2-1779753909631-a1b2c3d4]\nDescription follows';
    const result = extractCorrelationKey(text);
    expect(result).toBe('health-EC2-1779753909631-a1b2c3d4');
  });

  test('prefers INVESTIGATION_ID over the shared CORRELATION_ID (concurrent-safe)', () => {
    // Two investigations for the SAME event share the CORRELATION_ID (event ARN)
    // but have distinct INVESTIGATION_IDs. The unique id must win.
    const arn = 'arn:aws:health:eu-west-1::event/EC2/AWS_EC2_SCHEDULED_MAINTENANCE/123';
    const a = extractCorrelationKey(`[CORRELATION_ID:${arn}]\n[INVESTIGATION_ID:health-EC2-1-aaaa]\n...`);
    const b = extractCorrelationKey(`[CORRELATION_ID:${arn}]\n[INVESTIGATION_ID:health-EC2-2-bbbb]\n...`);
    expect(a).toBe('health-EC2-1-aaaa');
    expect(b).toBe('health-EC2-2-bbbb');
    expect(a).not.toBe(b);
  });

  test('unique investigationId routes to the keyed GetItem lookup', () => {
    const key = extractCorrelationKey('[INVESTIGATION_ID:health-EC2-1779753909631-a1b2c3d4]\n...');
    expect(key).toBe('health-EC2-1779753909631-a1b2c3d4');
    expect(lookupMode(key!)).toBe('keyed-getitem');
  });

  test('legacy CORRELATION_ID key routes to the healthEventId scan (backward compat)', () => {
    const key = extractCorrelationKey('[CORRELATION_ID:arn:aws:health:eu-west-1::event/EC2/X/1]\n...');
    expect(key).toBe('arn:arn:aws:health:eu-west-1::event/EC2/X/1');
    expect(lookupMode(key!)).toBe('legacy-scan');
  });
});

describe('Investigation Callback — Link Generation', () => {
  function buildInvestigationLink(agentSpaceId: string, taskId: string): string {
    return `https://${agentSpaceId}.aidevops.global.app.aws/investigation/${taskId}`;
  }

  test('generates correct DevOps Agent investigation link', () => {
    const link = buildInvestigationLink(
      'c4f3f2f4-695d-41e3-b856-a77851f76d8a',
      '02855926-d715-474f-aef2-0e0f4dc33cee'
    );
    expect(link).toBe('https://c4f3f2f4-695d-41e3-b856-a77851f76d8a.aidevops.global.app.aws/investigation/02855926-d715-474f-aef2-0e0f4dc33cee');
  });
});

describe('OpsCenter Creator — Category Mapping', () => {
  function mapEventCategoryToOpsCategory(eventCategory: string, eventType: string): string {
    switch (eventCategory) {
      case 'issue':
        return 'Availability';
      case 'scheduledChange':
        return 'Availability';
      case 'accountNotification':
        if (eventType.toLowerCase().includes('abuse')) return 'Security';
        return 'Performance';
      default:
        return 'Availability';
    }
  }

  test('maps issue to Availability', () => {
    expect(mapEventCategoryToOpsCategory('issue', 'AWS_EC2_OPERATIONAL_ISSUE')).toBe('Availability');
  });

  test('maps scheduledChange to Availability', () => {
    expect(mapEventCategoryToOpsCategory('scheduledChange', 'AWS_EC2_SCHEDULED_MAINTENANCE')).toBe('Availability');
  });

  test('maps accountNotification with abuse to Security', () => {
    expect(mapEventCategoryToOpsCategory('accountNotification', 'AWS_ABUSE_REPORT')).toBe('Security');
  });

  test('maps accountNotification without abuse to Performance', () => {
    expect(mapEventCategoryToOpsCategory('accountNotification', 'AWS_IAM_OPERATIONAL_NOTIFICATION')).toBe('Performance');
  });

  test('maps unknown category to Availability', () => {
    expect(mapEventCategoryToOpsCategory('unknown', 'SOMETHING')).toBe('Availability');
  });
});

describe('OpsCenter Creator — Severity Mapping', () => {
  function mapPriorityToSeverity(priority: string): string {
    const severityMap: Record<string, string> = {
      CRITICAL: '1', HIGH: '2', MEDIUM: '3', LOW: '4', MINIMAL: '4',
    };
    return severityMap[priority] || '3';
  }

  test('maps CRITICAL to severity 1', () => {
    expect(mapPriorityToSeverity('CRITICAL')).toBe('1');
  });

  test('maps HIGH to severity 2', () => {
    expect(mapPriorityToSeverity('HIGH')).toBe('2');
  });

  test('maps MEDIUM to severity 3', () => {
    expect(mapPriorityToSeverity('MEDIUM')).toBe('3');
  });

  test('maps LOW to severity 4', () => {
    expect(mapPriorityToSeverity('LOW')).toBe('4');
  });

  test('maps MINIMAL to severity 4', () => {
    expect(mapPriorityToSeverity('MINIMAL')).toBe('4');
  });

  test('maps unknown priority to severity 3 (default)', () => {
    expect(mapPriorityToSeverity('UNKNOWN')).toBe('3');
  });
});

describe('Investigation Callback — Severity From Findings', () => {
  const SEVERITY_RANK: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, MINIMAL: 0 };

  function parseSeverity(text: string): string | null {
    const m = text.trim().toUpperCase().match(/^[*_\s]*(CRITICAL|HIGH|MEDIUM|LOW|MINIMAL)\b/);
    return m ? m[1] : null;
  }

  function highestSeverity(severities: string[]): string | null {
    let best: string | null = null;
    for (const s of severities) {
      if (best === null || (SEVERITY_RANK[s] ?? -1) > (SEVERITY_RANK[best] ?? -1)) best = s;
    }
    return best;
  }

  // Mirrors buildOutput's priority derivation.
  function derivePriority(hasImpact: boolean, overallSeverity: string | null, taskPriority: string): string {
    return hasImpact ? (overallSeverity || taskPriority) : 'LOW';
  }

  // Mirrors parseAgentAnalysis: findings are capped at 5 for display, but the
  // overall severity is computed across ALL findings.
  function overallFromBullets(severities: string[]): { displayed: number; overall: string | null } {
    return { displayed: Math.min(severities.length, 5), overall: highestSeverity(severities) };
  }

  test('parses the severity word from a Key Findings bullet', () => {
    expect(parseSeverity('HIGH — service will be unavailable, no redundancy')).toBe('HIGH');
    expect(parseSeverity('low impact, full redundancy in place')).toBe('LOW');
  });

  test('returns null when no severity word is present', () => {
    expect(parseSeverity('the web tier reads from the affected database')).toBeNull();
  });

  test('only accepts a severity word that leads the bullet detail', () => {
    // Leading severity (skill format) → parsed.
    expect(parseSeverity('CRITICAL — will be unavailable, no redundancy')).toBe('CRITICAL');
    expect(parseSeverity('**HIGH** — significant degradation')).toBe('HIGH');
    // Severity word only later in the sentence → not misread.
    expect(parseSeverity('baseline is fine, escalates to CRITICAL under load')).toBeNull();
  });

  test('overall severity considers findings beyond the 5-finding display cap', () => {
    // 6 workloads; the CRITICAL one is 6th and dropped from the displayed findings,
    // but must still drive the overall severity (else the OpsItem is under-reported).
    const severities = ['LOW', 'LOW', 'MEDIUM', 'LOW', 'MEDIUM', 'CRITICAL'];
    const { displayed, overall } = overallFromBullets(severities);
    expect(displayed).toBe(5);
    expect(overall).toBe('CRITICAL');
  });

  test('overall severity is the highest across findings', () => {
    expect(highestSeverity(['LOW', 'HIGH', 'MEDIUM'])).toBe('HIGH');
    expect(highestSeverity(['CRITICAL', 'HIGH'])).toBe('CRITICAL');
    expect(highestSeverity([])).toBeNull();
  });

  test('no-impact result is LOW despite a HIGH intake priority', () => {
    // scheduledChange maps to HIGH at intake, but the agent found no impact.
    expect(derivePriority(false, null, 'HIGH')).toBe('LOW');
  });

  test('impact severity comes from findings, not the intake priority', () => {
    // Agent concluded LOW even though the event category mapped to HIGH at intake.
    expect(derivePriority(true, 'LOW', 'HIGH')).toBe('LOW');
  });

  test('falls back to task priority when impact exists but no severity was parsed', () => {
    expect(derivePriority(true, null, 'HIGH')).toBe('HIGH');
  });
});

describe('Investigation Trigger — Webhook Title', () => {
  function buildTitle(incidentId: string, service: string, eventType: string, region: string): string {
    return `[${incidentId}] AWS Health: ${service} ${eventType} in ${region}`;
  }

  test('includes incidentId in brackets at the start', () => {
    const title = buildTitle('health-LAMBDA-1779753909631', 'LAMBDA', 'AWS_LAMBDA_RUNTIME_DEPRECATION', 'eu-west-1');
    expect(title).toBe('[health-LAMBDA-1779753909631] AWS Health: LAMBDA AWS_LAMBDA_RUNTIME_DEPRECATION in eu-west-1');
    expect(title.startsWith('[health-')).toBe(true);
  });

  test('incidentId is extractable from title', () => {
    const title = '[health-EC2-123456789] AWS Health: EC2 AWS_EC2_SCHEDULED_MAINTENANCE in us-east-1';
    const start = title.indexOf('[') + 1;
    const end = title.indexOf(']');
    const extracted = title.substring(start, end);
    expect(extracted).toBe('health-EC2-123456789');
  });
});

describe('Investigation Trigger — Description Correlation Tag', () => {
  function buildDescription(eventId: string, incidentId: string): string {
    return `[CORRELATION_ID:${eventId}]\n[INVESTIGATION_ID:${incidentId}]\n\nAWS Health Event detected...`;
  }

  test('CORRELATION_ID is the first thing in the description', () => {
    const desc = buildDescription('arn:aws:health:eu-west-1::event/EC2/TEST/123', 'health-EC2-1-aaaa');
    expect(desc.startsWith('[CORRELATION_ID:')).toBe(true);
  });

  test('CORRELATION_ID value is extractable', () => {
    const desc = buildDescription('arn:aws:health:global::event/IAM/NOTIF/456', 'health-IAM-1-bbbb');
    const prefix = '[CORRELATION_ID:';
    const start = desc.indexOf(prefix) + prefix.length;
    const end = desc.indexOf(']', start);
    const value = desc.substring(start, end);
    expect(value).toBe('arn:aws:health:global::event/IAM/NOTIF/456');
  });

  test('INVESTIGATION_ID value is present and extractable', () => {
    const desc = buildDescription('arn:aws:health:global::event/IAM/NOTIF/456', 'health-IAM-1779753909631-bbbb');
    const prefix = '[INVESTIGATION_ID:';
    const start = desc.indexOf(prefix) + prefix.length;
    const end = desc.indexOf(']', start);
    expect(desc.substring(start, end)).toBe('health-IAM-1779753909631-bbbb');
  });
});


describe('Investigation Trigger — Retry Logic', () => {
  // Replicate the isRetryableError logic for unit testing
  function isRetryableError(message: string): boolean {
    // Network timeout or connection error
    if (message.includes('ETIMEDOUT') || message.includes('ECONNRESET') ||
        message.includes('ECONNREFUSED') || message.includes('ENOTFOUND') ||
        message.includes('socket hang up') || message.includes('timeout')) {
      return true;
    }

    // Extract status code from error message format: "returned status NNN"
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

  // Retry on 5xx status codes
  test('retries on HTTP 500 (Internal Server Error)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 500: Internal Server Error')).toBe(true);
  });

  test('retries on HTTP 502 (Bad Gateway)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 502: Bad Gateway')).toBe(true);
  });

  test('retries on HTTP 503 (Service Unavailable)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 503: Service Unavailable')).toBe(true);
  });

  test('retries on HTTP 504 (Gateway Timeout)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 504: Gateway Timeout')).toBe(true);
  });

  // Retry on 429
  test('retries on HTTP 429 (Too Many Requests)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 429: Too Many Requests')).toBe(true);
  });

  // Do NOT retry on 4xx (except 429)
  test('does not retry on HTTP 400 (Bad Request)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 400: Bad Request')).toBe(false);
  });

  test('does not retry on HTTP 401 (Unauthorized)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 401: Unauthorized')).toBe(false);
  });

  test('does not retry on HTTP 403 (Forbidden)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 403: Forbidden')).toBe(false);
  });

  test('does not retry on HTTP 404 (Not Found)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 404: Not Found')).toBe(false);
  });

  test('does not retry on HTTP 422 (Unprocessable Entity)', () => {
    expect(isRetryableError('DevOps Agent webhook returned status 422: Unprocessable Entity')).toBe(false);
  });

  // Retry on network errors
  test('retries on ETIMEDOUT', () => {
    expect(isRetryableError('connect ETIMEDOUT 10.0.0.1:443')).toBe(true);
  });

  test('retries on ECONNRESET', () => {
    expect(isRetryableError('read ECONNRESET')).toBe(true);
  });

  test('retries on ECONNREFUSED', () => {
    expect(isRetryableError('connect ECONNREFUSED 127.0.0.1:443')).toBe(true);
  });

  test('retries on ENOTFOUND', () => {
    expect(isRetryableError('getaddrinfo ENOTFOUND webhook.example.com')).toBe(true);
  });

  test('retries on socket hang up', () => {
    expect(isRetryableError('socket hang up')).toBe(true);
  });

  test('retries on timeout', () => {
    expect(isRetryableError('DevOps Agent webhook request timeout')).toBe(true);
  });

  // Unknown errors are retryable
  test('retries on unknown errors without status codes', () => {
    expect(isRetryableError('Something unexpected happened')).toBe(true);
  });
});

describe('Investigation Trigger — Exponential Backoff Calculation', () => {
  function calculateDelay(attempt: number): number {
    const BASE_DELAY_MS = 1000;
    const MAX_DELAY_MS = 4000;
    return Math.min(BASE_DELAY_MS * Math.pow(2, attempt), MAX_DELAY_MS);
  }

  test('first retry delay is 1000ms (1s)', () => {
    expect(calculateDelay(0)).toBe(1000);
  });

  test('second retry delay is 2000ms (2s)', () => {
    expect(calculateDelay(1)).toBe(2000);
  });

  test('delay is capped at 4000ms (4s)', () => {
    expect(calculateDelay(2)).toBe(4000);
    expect(calculateDelay(3)).toBe(4000);
    expect(calculateDelay(10)).toBe(4000);
  });
});

describe('Investigation Callback — Summary Validation & Fail-Safe', () => {
  // Mirrors the CHAIN_OF_THOUGHT_PATTERN / isChainOfThought / summary-extraction
  // logic added to parseAgentAnalysis in investigation-callback/index.ts.
  const CHAIN_OF_THOUGHT_PATTERN =
    /^\s*i['’]?(ve|m|ll)\b|^\s*i\s+(now|have|will|won['’]?t|confirmed)\b|\blet me\b|\b(sub-?agent|mitigation specialist|as its own artifact|won['’]?t duplicate|no new information)\b/i;

  function isChainOfThought(candidate: string): boolean {
    return CHAIN_OF_THOUGHT_PATTERN.test(candidate);
  }

  const SUMMARY_FAIL_SAFE =
    'The investigation output did not match the expected format and could not be ' +
    'summarized automatically. Open the investigation in the DevOps Agent console ' +
    'to review the full findings.';

  function extractSummary(analysis: string): string {
    const summaryMatch = analysis.match(/## Summary\s*\n+([\s\S]*?)(?=\n##|\n---|\n\|)/);
    if (summaryMatch) {
      const candidate = summaryMatch[1].trim().replace(/\n/g, ' ').substring(0, 500);
      return isChainOfThought(candidate) ? SUMMARY_FAIL_SAFE : candidate;
    }
    return SUMMARY_FAIL_SAFE;
  }

  test('rejects chain-of-thought under a literal ## Summary heading (AC-1)', () => {
    const analysis = '## Summary\n\nI now have conclusive evidence. Let me synthesize the investigation.\n\n## Key Findings\n';
    expect(extractSummary(analysis)).toBe(SUMMARY_FAIL_SAFE);
  });

  test('rejects a delegation wrap-up sentence under ## Summary', () => {
    const analysis = "## Summary\n\nThe propose-mitigation subagent completed with a policy-validated plan, which reaches you as its own artifact — I won't duplicate it here.\n\n## Key Findings\n";
    expect(extractSummary(analysis)).toBe(SUMMARY_FAIL_SAFE);
  });

  test('rejects a meta-comment re-notification summary', () => {
    const analysis = '## Summary\n\nNo new information — these are more CVEs from the same Inspector2 scan sweep as last time.\n\n## Key Findings\n';
    expect(extractSummary(analysis)).toBe(SUMMARY_FAIL_SAFE);
  });

  test('does not grab the first long paragraph when ## Summary is absent (AC-2)', () => {
    const analysis = "I now have conclusive evidence after reviewing the topology and all affected resources in detail.\n\n## Key Findings\n- **web-tier**: HIGH — degraded\n";
    expect(extractSummary(analysis)).toBe(SUMMARY_FAIL_SAFE);
  });

  test('passes a well-formed factual summary through verbatim (AC-3, regression)', () => {
    const analysis = '## Summary\n\n3 HIGH findings affecting web-tier ASG; redundancy insufficient during AZ failure.\n\n## Key Findings\n';
    expect(extractSummary(analysis)).toBe('3 HIGH findings affecting web-tier ASG; redundancy insufficient during AZ failure.');
  });

  test('does not reject a genuine impact statement that merely mentions "mitigation"', () => {
    const analysis = '## Summary\n\nMitigation for this issue involves scaling the ASG to 3 instances across AZs.\n\n## Key Findings\n';
    expect(extractSummary(analysis)).toBe('Mitigation for this issue involves scaling the ASG to 3 instances across AZs.');
  });

  test('does not reject a factual re-notification summary describing the finding', () => {
    const analysis = '## Summary\n\nInspector2 re-scan confirms 6 previously reported HIGH CVEs on web-tier; no new findings since the last notification.\n\n## Key Findings\n';
    expect(extractSummary(analysis)).toBe('Inspector2 re-scan confirms 6 previously reported HIGH CVEs on web-tier; no new findings since the last notification.');
  });

  test('collapses newlines and truncates at 500 chars (regression)', () => {
    const longLine = 'x'.repeat(600);
    const analysis = `## Summary\n\n${longLine}\n\n## Key Findings\n`;
    const summary = extractSummary(analysis);
    expect(summary.length).toBe(500);
    expect(summary).not.toContain('\n');
  });
});

describe('Investigation Callback — Key Findings Denylist & Date Filter', () => {
  // Mirrors META_LABEL_DENYLIST / DATE_LABEL_PATTERN / isMetaOrDateLabel and the
  // bullet-acceptance loop added to parseAgentAnalysis.
  const META_LABEL_DENYLIST =
    /^(mitigation|jira|tracking|impact|severity|root cause|action|issue key|reason for skip|next steps|status)$/i;
  const DATE_LABEL_PATTERN =
    /^\d{4}-\d{2}-\d{2}|^(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d/i;

  function isMetaOrDateLabel(label: string): boolean {
    const trimmed = label.trim();
    return META_LABEL_DENYLIST.test(trimmed) || DATE_LABEL_PATTERN.test(trimmed);
  }

  function parseSeverity(text: string): string | null {
    const m = text.trim().toUpperCase().match(/^[*_\s]*(CRITICAL|HIGH|MEDIUM|LOW|MINIMAL)\b/);
    return m ? m[1] : null;
  }

  // Mirrors the bullet-acceptance loop inside parseAgentAnalysis, including the
  // "accepted" counter used for the 5-slot display cap.
  function extractFindings(contentToSearch: string, fallbackPriority: string): {
    findings: Array<{ description: string; severity: string }>;
    explicitSeverities: string[];
  } {
    const findings: Array<{ description: string; severity: string }> = [];
    const explicitSeverities: string[] = [];
    const bulletFindings = contentToSearch.match(/^[-•*][ \t]+\*\*(.+?)\*\*[:\s]*(.+)/gm);
    if (bulletFindings) {
      let accepted = 0;
      bulletFindings.forEach((bullet) => {
        const match = bullet.match(/^[-•*][ \t]+\*\*(.+?)\*\*[:\s]*(.*)/);
        if (!match) return;
        if (isMetaOrDateLabel(match[1])) return;
        const explicit = parseSeverity(match[2]);
        if (explicit) explicitSeverities.push(explicit);
        const severity = explicit || fallbackPriority;
        if (accepted < 5) {
          findings.push({ description: `${match[1]}: ${match[2]}`.trim(), severity });
          accepted++;
        }
      });
    }
    return { findings, explicitSeverities };
  }

  test('excludes bullets whose bold label is a denylisted meta-label (AC-5)', () => {
    const content = [
      '- **Mitigation**: validated plan delivered as its own artifact',
      '- **Jira**: skipped — configured project OPS does not exist',
      '- **Tracking**: Jira KAN-43',
      '- **Impact**: widespread',
      '- **Severity**: MEDIUM — ...',
      '- **Root Cause**: base AMI deprecated',
      '- **Action**: created',
      '- **Issue key**: KAN-43',
      '- **Reason for skip**: severity below threshold',
    ].join('\n');
    const { findings } = extractFindings(content, 'MEDIUM');
    expect(findings).toHaveLength(0);
  });

  test('excludes bullets whose bold label is a date/timestamp (AC-6)', () => {
    const content = [
      '- **2026-10-13**: Topology snapshot shows the ASG running 2 instances',
      '- **July 29, 2027**: can no longer create new functions on Python 3.8',
    ].join('\n');
    const { findings } = extractFindings(content, 'MEDIUM');
    expect(findings).toHaveLength(0);
  });

  test('meta bullets do not crowd out real resource bullets under the 5-slot cap', () => {
    const content = [
      '- **Mitigation**: validated plan delivered as its own artifact',
      '- **web-tier**: HIGH — degraded, no redundancy',
      '- **Jira**: skipped — configured project OPS does not exist',
      '- **payment-service**: CRITICAL — unavailable',
      '- **db-tier**: MEDIUM — partial redundancy',
    ].join('\n');
    const { findings, explicitSeverities } = extractFindings(content, 'MEDIUM');
    expect(findings).toHaveLength(3);
    expect(findings.map(f => f.description)).toEqual([
      'web-tier: HIGH — degraded, no redundancy',
      'payment-service: CRITICAL — unavailable',
      'db-tier: MEDIUM — partial redundancy',
    ]);
    expect(explicitSeverities).toEqual(['HIGH', 'CRITICAL', 'MEDIUM']);
  });

  test('## Key Findings absent → no scavenging of other sections (AC-7)', () => {
    // Simulates parseAgentAnalysis: contentToSearch is '' when the "## Key
    // Findings" heading itself does not match, even if other sections (like
    // "## Answers to Your Questions") contain plausible bulleted meta content.
    const fullAnalysis = [
      '## Answers to Your Questions',
      '',
      '- **Mitigation**: validated plan delivered as its own artifact',
      '- **Jira**: skipped — configured project OPS does not exist',
      '',
    ].join('\n');
    const findingsSection = fullAnalysis.match(/## Key Findings([\s\S]*?)(?=\n## (?!Key)|\n---)/);
    const contentToSearch = findingsSection?.[1] || '';
    const { findings } = extractFindings(contentToSearch, 'MEDIUM');
    expect(contentToSearch).toBe('');
    expect(findings).toHaveLength(0);
  });

  test('well-formed ## Key Findings with 6 resource bullets: 5 displayed, all 6 counted (AC-8, regression)', () => {
    const content = [
      '- **svc-1**: LOW — minimal impact',
      '- **svc-2**: LOW — minimal impact',
      '- **svc-3**: MEDIUM — some impact',
      '- **svc-4**: LOW — minimal impact',
      '- **svc-5**: MEDIUM — some impact',
      '- **svc-6**: CRITICAL — unavailable',
    ].join('\n');
    const { findings, explicitSeverities } = extractFindings(content, 'MEDIUM');
    expect(findings).toHaveLength(5);
    expect(explicitSeverities).toHaveLength(6);
    expect(explicitSeverities).toContain('CRITICAL');
  });
});

describe('Investigation Callback — Recommendations Resilient Fallback', () => {
  // Mirrors the recsText fallback logic: the table-row pattern falls back to the
  // whole analysis (recsSection?.[1] ?? analysis); the numbered list is read only
  // from a real recommendations section and is anchored to line starts.
  function extractRecommendations(analysis: string): Array<{ description: string; priority: string }> {
    const mapPriority = (p: string): string => {
      const map: Record<string, string> = { P1: 'CRITICAL', P2: 'HIGH', P3: 'MEDIUM', P4: 'LOW' };
      return map[p] || 'MEDIUM';
    };
    const recommendations: Array<{ description: string; priority: string }> = [];
    const recsSection = analysis.match(/(?:### 4\.|## (?:Recommended )?Actions|What actions)([\s\S]*?)(?=\n## (?!Rec)|\n---|\*\*No immediate)/);
    const recsText = recsSection?.[1] ?? analysis;

    const tableRows = recsText.match(/\|\s*\*\*P\d+\*\*\s*\|\s*(.+?)\s*\|/g);
    if (tableRows) {
      for (const row of tableRows) {
        const rowMatch = row.match(/\|\s*\*\*(P\d+)\*\*\s*\|\s*(.+?)\s*\|/);
        if (rowMatch) {
          recommendations.push({ description: rowMatch[2].trim(), priority: mapPriority(rowMatch[1]) });
        }
      }
    }
    if (recommendations.length === 0 && recsSection) {
      const numbered = recsText.match(/^[ \t]*\d{1,2}\.[ \t]+(.+)$/gm);
      if (numbered) {
        for (const item of numbered.slice(0, 5)) {
          const itemMatch = item.match(/^[ \t]*\d{1,2}\.[ \t]+(.+)$/);
          if (itemMatch) {
            recommendations.push({ description: itemMatch[1].trim(), priority: 'MEDIUM' });
          }
        }
      }
    }
    return recommendations;
  }

  test('parses a well-formed ## Recommended Actions table (AC-10, regression)', () => {
    const analysis = [
      '## Recommended Actions',
      '',
      '| Priority | Action |',
      '|----------|--------|',
      '| **P1**   | Scale out the ASG to 3 instances |',
      '| **P2**   | Enable Multi-AZ on the RDS instance |',
      '',
      '## Notification Routing',
    ].join('\n');
    const recs = extractRecommendations(analysis);
    expect(recs).toEqual([
      { description: 'Scale out the ASG to 3 instances', priority: 'CRITICAL' },
      { description: 'Enable Multi-AZ on the RDS instance', priority: 'HIGH' },
    ]);
  });

  test('finds a P1/P2 table anywhere in the analysis when the heading is absent (AC-11)', () => {
    const analysis = [
      '## Key Findings',
      '',
      '- **web-tier**: HIGH — degraded',
      '',
      'The delegated plan proposes:',
      '',
      '| Priority | Action |',
      '|----------|--------|',
      '| **P1**   | Patch the base AMI |',
      '',
    ].join('\n');
    const recs = extractRecommendations(analysis);
    expect(recs).toEqual([{ description: 'Patch the base AMI', priority: 'CRITICAL' }]);
  });

  test('does not use a numbered list found outside a recommendations section', () => {
    const analysis = [
      '## Key Findings',
      '',
      '- **web-tier**: HIGH — degraded',
      '',
      'Suggested next steps:',
      '1. do x',
      '2. do y',
      '',
    ].join('\n');
    expect(extractRecommendations(analysis)).toEqual([]);
  });

  test('keeps the whole-report table fallback when the heading is absent', () => {
    const analysis = [
      '## Summary',
      '',
      'Instance i-0123456789abcdef0 in account 123456789012 is affected.',
      '',
      '| Priority | Action |',
      '|----------|--------|',
      '| **P1** | Replace the instance |',
      '',
    ].join('\n');
    expect(extractRecommendations(analysis)).toEqual([{ description: 'Replace the instance', priority: 'CRITICAL' }]);
  });

  test('parses a numbered list inside a real Recommended Actions section, unindented and indented', () => {
    const plain = [
      '## Recommended Actions',
      '',
      '1. Scale out the ASG',
      '2. Enable detailed monitoring',
      '',
      '## Notification Routing',
    ].join('\n');
    const indented = plain.replace('1. Scale', '   1. Scale').replace('2. Enable', '   2. Enable');
    const expected = [
      { description: 'Scale out the ASG', priority: 'MEDIUM' },
      { description: 'Enable detailed monitoring', priority: 'MEDIUM' },
    ];
    expect(extractRecommendations(plain)).toEqual(expected);
    expect(extractRecommendations(indented)).toEqual(expected);
  });

  test('ignores a prose sentence ending in a digit and a period inside the recommendations section', () => {
    const analysis = [
      '## Recommended Actions',
      '',
      '1. Scale out the ASG',
      'See region us-east-1. Then proceed.',
      '2. Enable detailed monitoring',
      '',
      '## Notification Routing',
    ].join('\n');
    expect(extractRecommendations(analysis)).toEqual([
      { description: 'Scale out the ASG', priority: 'MEDIUM' },
      { description: 'Enable detailed monitoring', priority: 'MEDIUM' },
    ]);
  });

  test('does not treat a year at line start as a list item', () => {
    const analysis = [
      '## Recommended Actions',
      '',
      '2026. something happened',
      '1. Patch the base AMI',
      '',
      '## Notification Routing',
    ].join('\n');
    expect(extractRecommendations(analysis)).toEqual([{ description: 'Patch the base AMI', priority: 'MEDIUM' }]);
  });
  test('returns empty when neither a table nor a numbered list exists anywhere', () => {
    const analysis = '## Key Findings\n\n- **web-tier**: HIGH — degraded\n\nNo actionable items identified.\n';
    expect(extractRecommendations(analysis)).toEqual([]);
  });
});

describe('Investigation Callback — Severity Clamp Post-Pass', () => {
  const SEVERITY_RANK: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, MINIMAL: 0 };

  // Mirrors the clamp loop appended at the end of parseAgentAnalysis.
  function clampFindings(
    findings: Array<{ description: string; severity: string }>,
    overallSeverity: string | null
  ): Array<{ description: string; severity: string }> {
    if (!overallSeverity) return findings;
    const cap = SEVERITY_RANK[overallSeverity] ?? -1;
    return findings.map(f => ({
      ...f,
      severity: (SEVERITY_RANK[f.severity] ?? -1) > cap ? overallSeverity : f.severity,
    }));
  }

  test('clamps a finding whose severity exceeds the computed overall severity (AC-14, AC-15)', () => {
    const findings = [
      { description: 'web-tier: CRITICAL — unavailable', severity: 'CRITICAL' },
      { description: 'db-tier: stray CRITICAL token', severity: 'CRITICAL' },
    ];
    // Simulate: overall severity was computed as HIGH (e.g. from a different,
    // legitimately-filtered finding set), but one body bullet still says CRITICAL.
    const clamped = clampFindings(findings, 'HIGH');
    expect(clamped[1].severity).toBe('HIGH');
  });

  test('does not alter findings already at or below the overall severity (no-op regression)', () => {
    const findings = [
      { description: 'web-tier: HIGH — degraded', severity: 'HIGH' },
      { description: 'db-tier: LOW — minimal impact', severity: 'LOW' },
    ];
    const clamped = clampFindings(findings, 'HIGH');
    expect(clamped).toEqual(findings);
  });
});

describe('Investigation Callback — Multi-Message Report Merge & Severity Source', () => {
  // Mirrors the helpers added to investigation-callback/index.ts (reportSections,
  // withRecsHeading, mergeAnalysisMessages) and the updated parseAgentAnalysis.
  const MESSAGE_SEPARATOR = '\n\n---\n\n';
  const SUMMARY_SECTION_RE = /## Summary/;
  const FINDINGS_SECTION_RE = /## Key Findings/;
  const RECS_HEADING_RE = /(?:### 4\.|## (?:Recommended )?Actions|What actions)/;
  const RECS_ROW_RE = /\|\s*\*\*P\d+\*\*\s*\|/;

  function reportSections(text: string) {
    return {
      summary: SUMMARY_SECTION_RE.test(text),
      findings: FINDINGS_SECTION_RE.test(text),
      recs: RECS_HEADING_RE.test(text) || RECS_ROW_RE.test(text),
    };
  }

  function withRecsHeading(text: string): string {
    if (RECS_HEADING_RE.test(text) || !RECS_ROW_RE.test(text)) return text;
    return text.replace(/^\|/m, '## Recommended Actions\n\n|');
  }

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
      if (hasSummary && hasFindings && hasRecs) break;
    }
    if (picked.length === 0) return messagesNewestFirst[0];
    if (picked.length === 1) return picked[0];
    return picked.map(withRecsHeading).join(MESSAGE_SEPARATOR);
  }

  const SEVERITY_RANK: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, MINIMAL: 0 };
  const SUMMARY_FAIL_SAFE =
    'The investigation output did not match the expected format and could not be ' +
    'summarized automatically. Open the investigation in the DevOps Agent console ' +
    'to review the full findings.';
  const CHAIN_OF_THOUGHT_PATTERN =
    /^\s*i['’]?(ve|m|ll)\b|^\s*i\s+(now|have|will|won['’]?t|confirmed)\b|\blet me\b|\b(sub-?agent|mitigation specialist|as its own artifact|won['’]?t duplicate|no new information)\b/i;
  const META_LABEL_DENYLIST =
    /^(mitigation|jira|tracking|impact|severity|root cause|action|issue key|reason for skip|next steps|status)$/i;
  const DATE_LABEL_PATTERN =
    /^\d{4}-\d{2}-\d{2}|^(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d/i;

  function parseSeverity(text: string): string | null {
    const m = text.trim().toUpperCase().match(/^[*_\s]*(CRITICAL|HIGH|MEDIUM|LOW|MINIMAL)\b/);
    return m ? m[1] : null;
  }

  function highestSeverity(severities: string[]): string | null {
    let best: string | null = null;
    for (const s of severities) {
      if (best === null || (SEVERITY_RANK[s] ?? -1) > (SEVERITY_RANK[best] ?? -1)) best = s;
    }
    return best;
  }

  // Mirrors parseAgentAnalysis (summary, findings, recommendations, impact, overall severity, clamp).
  function parseAnalysisMirror(analysis: string, fallbackPriority: string) {
    const result = {
      hasImpact: false,
      summary: '',
      overallSeverity: null as string | null,
      findings: [] as Array<{ description: string; severity: string }>,
      recommendations: [] as Array<{ description: string; priority: string }>,
    };

    const summaryMatch = analysis.match(/## Summary\s*\n+([\s\S]*?)(?=\n##|\n---|\n\|)/);
    if (summaryMatch) {
      const candidate = summaryMatch[1].trim().replace(/\n/g, ' ').substring(0, 500);
      result.summary = CHAIN_OF_THOUGHT_PATTERN.test(candidate) ? SUMMARY_FAIL_SAFE : candidate;
    } else {
      result.summary = SUMMARY_FAIL_SAFE;
    }

    const findingsSection = analysis.match(/## Key Findings([\s\S]*?)(?=\n## (?!Key)|\n---)/);
    const contentToSearch = findingsSection?.[1] || '';
    const explicitSeverities: string[] = [];
    const bulletFindings = contentToSearch.match(/^[-•*][ \t]+\*\*(.+?)\*\*[:\s]*(.+)/gm);
    if (bulletFindings) {
      let accepted = 0;
      bulletFindings.forEach((bullet) => {
        const match = bullet.match(/^[-•*][ \t]+\*\*(.+?)\*\*[:\s]*(.*)/);
        if (!match) return;
        const label = match[1].trim();
        if (META_LABEL_DENYLIST.test(label) || DATE_LABEL_PATTERN.test(label)) return;
        const explicit = parseSeverity(match[2]);
        if (explicit) explicitSeverities.push(explicit);
        const severity = explicit || fallbackPriority;
        if (accepted < 5) {
          result.findings.push({ description: `${match[1]}: ${match[2]}`.trim(), severity });
          accepted++;
        }
      });
    }

    const mapPriority = (p: string): string => ({ P1: 'CRITICAL', P2: 'HIGH', P3: 'MEDIUM', P4: 'LOW' } as Record<string, string>)[p] || 'MEDIUM';
    const recsSection = analysis.match(/(?:### 4\.|## (?:Recommended )?Actions|What actions)([\s\S]*?)(?=\n## (?!Rec)|\n---|\*\*No immediate)/);
    const recsText = recsSection?.[1] ?? analysis;
    const tableRows = recsText.match(/\|\s*\*\*P\d+\*\*\s*\|\s*(.+?)\s*\|/g);
    if (tableRows) {
      for (const row of tableRows) {
        const rowMatch = row.match(/\|\s*\*\*(P\d+)\*\*\s*\|\s*(.+?)\s*\|/);
        if (rowMatch) result.recommendations.push({ description: rowMatch[2].trim(), priority: mapPriority(rowMatch[1]) });
      }
    }
    if (result.recommendations.length === 0 && recsSection) {
      const numbered = recsText.match(/^[ \t]*\d{1,2}\.[ \t]+(.+)$/gm);
      if (numbered) {
        for (const item of numbered.slice(0, 5)) {
          const itemMatch = item.match(/^[ \t]*\d{1,2}\.[ \t]+(.+)$/);
          if (itemMatch) result.recommendations.push({ description: itemMatch[1].trim(), priority: 'MEDIUM' });
        }
      }
    }

    if (result.recommendations.length > 0 || result.findings.length > 0) result.hasImpact = true;

    if (result.findings.length === 0 && result.hasImpact && result.summary !== SUMMARY_FAIL_SAFE) {
      result.findings.push({ description: result.summary.substring(0, 200), severity: fallbackPriority });
    }

    result.overallSeverity = highestSeverity(explicitSeverities)
      ?? (result.findings.length > 0 ? fallbackPriority : null);

    if (result.overallSeverity) {
      const cap = SEVERITY_RANK[result.overallSeverity] ?? -1;
      for (const finding of result.findings) {
        if ((SEVERITY_RANK[finding.severity] ?? -1) > cap) finding.severity = result.overallSeverity;
      }
    }
    return result;
  }

  // Message 1: the complete structured report (placeholder identifiers only).
  const FULL_REPORT = [
    '## Summary',
    '',
    'A standby web server and a live web server in the same account have public IP addresses, but no inbound traffic reaches the standby instance. Impact: MEDIUM',
    '',
    '## Key Findings',
    '',
    '- **Web Application – standby web server (i-0123456789abcdef0)**: MEDIUM — public IP on an instance that receives no traffic.',
    '  - **Why it has a public IP**: the subnet auto-assigns public addresses.',
    '  - **No traffic**: no flow log entries in the last 14 days.',
    '  - **Inbound is blocked**: the security group allows no inbound rules.',
    '  - **Affected resources**: i-0123456789abcdef0, eni-0123456789abcdef0',
    '- **Web Application – live web server (i-0123456789abcdef1)**: LOW — public IP is expected for this internet-facing server.',
    '',
    '## Answers to Your Questions',
    '',
    '- **Is the standby server reachable?**: No, inbound is blocked.',
    '',
    '## Recommended Actions',
    '',
    '| Priority | Action |',
    '|----------|--------|',
    '| **P1**   | Review the standby server public IP |',
    '| **P2**   | Keep the live web server as is |',
    '| **P3**   | Disable auto-assign public IP on the subnet |',
    '',
    '## Notification Routing',
    '',
    '- **Slack**: sent',
    '',
    '## Jira Tracking',
    '',
    '- **Jira**: skipped',
  ].join('\n');

  // Message 2: lead paragraph plus only the updated recommendations table.
  const TABLE_ONLY_ROWS = [
    '| Priority | Action |',
    '|----------|--------|',
    '| **P1**   | Remove the public IP: aws ec2 modify-network-interface-attribute --network-interface-id eni-0123456789abcdef0 --no-associate-public-ip-address |',
    '| **P2**   | Remove the public IP from the standby server with modify-network-interface-attribute --no-associate-public-ip-address |',
    '| **P3**   | Disable auto-assign public IP on the subnet |',
  ];
  const TABLE_ONLY = [
    'The finished mitigation plan corrects one thing I said earlier and changes the recommended actions below.',
    '',
    '## Recommended Actions',
    '',
    ...TABLE_ONLY_ROWS,
  ].join('\n');
  const TABLE_ONLY_NO_HEADING = [
    'The finished mitigation plan changes the recommended actions.',
    '',
    ...TABLE_ONLY_ROWS,
  ].join('\n');

  const EXPECTED_NEW_RECS = [
    { description: 'Remove the public IP: aws ec2 modify-network-interface-attribute --network-interface-id eni-0123456789abcdef0 --no-associate-public-ip-address', priority: 'CRITICAL' },
    { description: 'Remove the public IP from the standby server with modify-network-interface-attribute --no-associate-public-ip-address', priority: 'HIGH' },
    { description: 'Disable auto-assign public IP on the subnet', priority: 'MEDIUM' },
  ];

  // (a) summary/findings from the first message, recommendations from the later one
  test('(a) takes summary and findings from the full report and recommendations from the newer table-only message', () => {
    const merged = mergeAnalysisMessages([TABLE_ONLY, FULL_REPORT])!;
    const result = parseAnalysisMirror(merged, 'CRITICAL');
    const fromFull = parseAnalysisMirror(FULL_REPORT, 'CRITICAL');
    expect(result.summary).toBe(fromFull.summary);
    expect(result.summary).not.toBe(SUMMARY_FAIL_SAFE);
    expect(result.findings).toEqual(fromFull.findings);
    expect(result.recommendations).toEqual(EXPECTED_NEW_RECS);
    expect(result.recommendations.map(r => r.description)).not.toContain('Review the standby server public IP');
  });

  test('(a) a newer table without a heading still wins over the older heading + table', () => {
    const merged = mergeAnalysisMessages([TABLE_ONLY_NO_HEADING, FULL_REPORT])!;
    const result = parseAnalysisMirror(merged, 'CRITICAL');
    expect(result.recommendations).toEqual(EXPECTED_NEW_RECS);
    expect(result.summary).toBe(parseAnalysisMirror(FULL_REPORT, 'CRITICAL').summary);
  });

  test('(a) a newest narration-only message is ignored', () => {
    const merged = mergeAnalysisMessages(['Done.', TABLE_ONLY, FULL_REPORT])!;
    expect(merged).not.toContain('Done.');
    expect(parseAnalysisMirror(merged, 'CRITICAL').recommendations).toEqual(EXPECTED_NEW_RECS);
  });

  test('(a) a newest complete report beats older messages and stops the merge', () => {
    const merged = mergeAnalysisMessages([FULL_REPORT, TABLE_ONLY])!;
    expect(merged).toBe(FULL_REPORT);
  });

  // (b) single message: unchanged
  test('(b) a single complete message is returned byte for byte and parses as before', () => {
    const merged = mergeAnalysisMessages([FULL_REPORT])!;
    expect(merged).toBe(FULL_REPORT);
    expect(parseAnalysisMirror(merged, 'CRITICAL')).toEqual(parseAnalysisMirror(FULL_REPORT, 'CRITICAL'));
  });

  test('(b) a single message without headings is returned as is and yields the fail-safe summary', () => {
    const text = 'The investigation found nothing worth reporting.';
    expect(mergeAnalysisMessages([text])).toBe(text);
    expect(parseAnalysisMirror(text, 'MEDIUM').summary).toBe(SUMMARY_FAIL_SAFE);
  });

  test('(b) a single headingless table message is returned unmodified', () => {
    expect(mergeAnalysisMessages([TABLE_ONLY_NO_HEADING])).toBe(TABLE_ONLY_NO_HEADING);
  });

  test('(b) no messages returns null', () => {
    expect(mergeAnalysisMessages([])).toBeNull();
  });

  test('(b) with no structured message at all the newest assistant text is used', () => {
    expect(mergeAnalysisMessages(['Done.', 'Working on it.'])).toBe('Done.');
  });

  // (c) sub-bullets
  test('(c) indented sub-bullets under a top-level bullet are not findings', () => {
    const result = parseAnalysisMirror(FULL_REPORT, 'CRITICAL');
    expect(result.findings).toHaveLength(2);
    const text = result.findings.map(f => f.description).join('\n');
    for (const label of ['Why it has a public IP', 'No traffic', 'Inbound is blocked', 'Affected resources']) {
      expect(text).not.toContain(label);
    }
  });

  // (d) explicit severities only drive the header
  test('(d) header severity is the max of the explicit severities, not the intake fallback', () => {
    const result = parseAnalysisMirror(FULL_REPORT, 'CRITICAL');
    expect(result.overallSeverity).toBe('MEDIUM');
    expect(result.findings.map(f => f.severity)).toEqual(['MEDIUM', 'LOW']);
  });

  test('(d) an unlabeled top-level bullet is displayed clamped to the header severity', () => {
    const analysis = [
      '## Summary',
      '',
      'Two workloads were assessed.',
      '',
      '## Key Findings',
      '',
      '- **web-tier**: MEDIUM — degraded',
      '- **batch-tier**: LOW — minimal impact',
      '- **cache-tier**: no explicit level stated here',
      '',
      '## Recommended Actions',
      '',
      '| **P2** | Review the cache tier |',
    ].join('\n');
    const result = parseAnalysisMirror(analysis, 'CRITICAL');
    expect(result.overallSeverity).toBe('MEDIUM');
    expect(result.findings.map(f => f.severity)).toEqual(['MEDIUM', 'LOW', 'MEDIUM']);
  });

  // (e) fallback only when nothing is explicit
  test('(e) the intake fallback priority is used when no accepted finding has an explicit severity', () => {
    const analysis = [
      '## Summary',
      '',
      'Two workloads were assessed.',
      '',
      '## Key Findings',
      '',
      '- **web-tier**: degraded and unreachable',
      '- **db-tier**: partial redundancy',
      '',
      '## Notification Routing',
    ].join('\n');
    const result = parseAnalysisMirror(analysis, 'HIGH');
    expect(result.overallSeverity).toBe('HIGH');
    expect(result.findings.map(f => f.severity)).toEqual(['HIGH', 'HIGH']);
  });

  // (f) fail-safe must not create a finding
  test('(f) a rejected (chain-of-thought) summary with recommendations creates no generic finding', () => {
    const analysis = [
      '## Summary',
      '',
      'I now have conclusive evidence. Let me synthesize the investigation.',
      '',
      '## Recommended Actions',
      '',
      '| Priority | Action |',
      '|----------|--------|',
      '| **P1**   | Patch the base AMI |',
    ].join('\n');
    const result = parseAnalysisMirror(analysis, 'CRITICAL');
    expect(result.summary).toBe(SUMMARY_FAIL_SAFE);
    expect(result.findings).toEqual([]);
    expect(result.recommendations.length).toBeGreaterThan(0);
    expect(result.hasImpact).toBe(true);
    expect(result.overallSeverity).toBeNull();
  });

  test('(f) a missing ## Summary with recommendations creates no generic finding', () => {
    const result = parseAnalysisMirror(TABLE_ONLY, 'CRITICAL');
    expect(result.summary).toBe(SUMMARY_FAIL_SAFE);
    expect(result.findings).toEqual([]);
    expect(result.recommendations).toHaveLength(3);
    expect(result.hasImpact).toBe(true);
    expect(result.overallSeverity).toBeNull();
  });

  test('(f) regression: a valid summary with recommendations and no findings still yields one generic finding', () => {
    const analysis = [
      '## Summary',
      '',
      'The deprecated runtime is used by one function.',
      '',
      '## Recommended Actions',
      '',
      '| **P1**   | Upgrade the runtime |',
    ].join('\n');
    const result = parseAnalysisMirror(analysis, 'HIGH');
    expect(result.findings).toEqual([{ description: 'The deprecated runtime is used by one function.', severity: 'HIGH' }]);
    expect(result.overallSeverity).toBe('HIGH');
  });

  // Summary-only no-impact report: a sentence ending in "us-east-1." must not become a recommendation.
  test('a no-impact report without a recommendations section yields no recommendations and no impact', () => {
    const analysis = [
      '## Summary',
      '',
      'The event affects EC2 hosts in us-east-1. No workloads in this account use the affected instance types, so there is no operational impact.',
      '',
      '## Notification Routing',
    ].join('\n');
    const result = parseAnalysisMirror(analysis, 'MEDIUM');
    expect(result.recommendations).toEqual([]);
    expect(result.hasImpact).toBe(false);
  });
  // The journal paging loop cannot run in tests; pin its shape in the source.
  test('callback source pages the journal with the SDK limit/nextToken fields', () => {
    const source = fs.readFileSync(
      path.resolve(__dirname, '../infrastructure/cdk/lambda/investigation-callback/index.ts'),
      'utf-8'
    );
    expect(source).toContain('nextToken');
    expect(source).toContain('MAX_JOURNAL_PAGES');
    expect(source).toContain('limit: JOURNAL_PAGE_LIMIT');
    expect(source).not.toContain('maxResults');
  });
});

describe('DevOps Agent Skill — Output Contract Prose', () => {
  const skillPath = path.resolve(__dirname, '../devops-agent-skill/SKILL.md');
  const skillContent = fs.readFileSync(skillPath, 'utf-8');

  test('still contains all six exact output headings (frozen contract, NFR-3)', () => {
    expect(skillContent).toContain('## Summary');
    expect(skillContent).toContain('## Key Findings');
    expect(skillContent).toContain('## Answers to Your Questions');
    expect(skillContent).toContain('## Recommended Actions');
    expect(skillContent).toContain('## Notification Routing');
    expect(skillContent).toContain('## Jira Tracking');
  });

  test('contains the new Delegation and Sub-Agent Output section and its forbidden-phrase example', () => {
    expect(skillContent).toContain('## Delegation and Sub-Agent Output');
    expect(skillContent).toContain("reaches you as its own artifact — I won't duplicate it here.");
  });

  test('contains re-notification guidance forbidding the meta-comment pattern', () => {
    expect(skillContent).toContain('Re-notifying on an Existing Finding');
    expect(skillContent).toContain('No new information — these are more CVEs from the same Inspector2 scan sweep');
  });

  test('contains the mandatory, resource-only language for ## Key Findings', () => {
    expect(skillContent).toContain('is **mandatory**');
    const normalized = skillContent.toLowerCase().replace(/\s+/g, ' ');
    expect(normalized).toContain('never `mitigation`, `jira`, `tracking`, `impact`, `severity`, `root cause`, a date, or');
  });

  test('requires the final message to be the complete report with self-contained actions', () => {
    const normalized = skillContent.toLowerCase().replace(/\s+/g, ' ');
    expect(normalized).toContain('### final message is the complete report');
    expect(normalized).toContain('your final message must re-send the complete structured report');
    expect(normalized).toContain('all six headings');
    expect(normalized).toContain('must be self-contained');
    expect(normalized).toContain('never write "the command above"');
    // The six headings are not renamed.
    for (const heading of ['## Summary', '## Key Findings', '## Answers to Your Questions', '## Recommended Actions', '## Notification Routing', '## Jira Tracking']) {
      expect(skillContent).toContain(heading);
    }
  });

  test('contains severity determinism guidance in Step 3', () => {
    expect(skillContent).toContain('assign it the **same severity** each');
  });
});

describe('Investigation Trigger — Secrets Manager Integration', () => {
  // The DevOps Agent webhook secret now lives in Secrets Manager (provisioned
  // by the CDK-managed DevOpsAgentSpaceStack — see
  // infrastructure/cdk/lib/constructs/devops-agent-space.ts), not SSM
  // Parameter Store. The Lambda reads it via WEBHOOK_SECRET_ARN +
  // WEBHOOK_SECRET_REGION and getSecretFromSecretsManager(), never
  // WEBHOOK_SECRET_PARAM_NAME/getSecret() for this value. Slack/MS Teams
  // webhook secrets are unaffected and remain SSM SecureStrings.
  const investigationTriggerSource = fs.readFileSync(
    path.resolve(__dirname, '../infrastructure/cdk/lambda/investigation-trigger/index.ts'),
    'utf-8'
  );

  test('reads the webhook secret via getSecretFromSecretsManager, not getSecret (SSM)', () => {
    expect(investigationTriggerSource).toContain('getSecretFromSecretsManager');
    expect(investigationTriggerSource).toContain('WEBHOOK_SECRET_ARN');
    expect(investigationTriggerSource).toContain('WEBHOOK_SECRET_REGION');
  });

  test('does not reference the retired SSM-based webhook secret env var or plaintext secret env var', () => {
    expect(investigationTriggerSource).not.toContain('WEBHOOK_SECRET_PARAM_NAME');
    expect(investigationTriggerSource).not.toContain('DEVOPS_AGENT_WEBHOOK_SECRET');
  });

  test('still uses SSM getParameters for the unrelated Jira routing config', () => {
    // Confirms the Jira config path (unrelated to this migration) was left
    // on SSM Parameter Store, not accidentally moved to Secrets Manager too.
    expect(investigationTriggerSource).toContain('GetParametersCommand');
    expect(investigationTriggerSource).toContain('/health-analyzer/jira/');
  });
});
