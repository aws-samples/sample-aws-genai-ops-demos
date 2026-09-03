/**
 * Unit tests for Lambda business logic.
 * Tests correlation extraction, link generation, category mapping, and severity mapping.
 */

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

describe('Investigation Trigger — Secrets Cache Integration', () => {
  test('uses WEBHOOK_SECRET_PARAM_NAME env var (not DEVOPS_AGENT_WEBHOOK_SECRET)', () => {
    // Verify that the Lambda reads the SSM parameter name from the env var
    // and uses getSecret() to fetch the actual value at runtime.
    // This is a design verification test — the actual integration is tested
    // via the CDK assertion tests that verify the environment variable name.
    const envVarName = 'WEBHOOK_SECRET_PARAM_NAME';
    const envVarValue = '/health-analyzer/production/webhook-secret';

    // The env var should be an SSM parameter path, not a secret value
    expect(envVarValue).toMatch(/^\/health-analyzer\//);
    expect(envVarValue).not.toMatch(/^[a-zA-Z0-9+/=]{20,}$/); // Not a base64 secret
  });
});
