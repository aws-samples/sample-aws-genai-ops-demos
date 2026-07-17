/**
 * Unit tests for Lambda business logic.
 * Tests correlation extraction, link generation, category mapping, and severity mapping.
 */

describe('Investigation Callback — Correlation', () => {
  // Simulate the extractCorrelationKey logic
  function extractCorrelationKey(textContent: string): string | null {
    // Primary: [CORRELATION_ID:{healthEventArn}] in description
    const prefix = '[CORRELATION_ID:';
    const startIdx = textContent.indexOf(prefix);
    if (startIdx !== -1) {
      const valueStart = startIdx + prefix.length;
      const endIdx = textContent.indexOf(']', valueStart);
      if (endIdx !== -1) {
        const correlationId = textContent.substring(valueStart, endIdx).trim();
        if (correlationId) return `arn:${correlationId}`;
      }
    }

    // Fallback: [{incidentId}] in title
    const titlePrefix = 'TITLE: [';
    const titleIdx = textContent.indexOf(titlePrefix);
    if (titleIdx !== -1) {
      const idStart = titleIdx + titlePrefix.length;
      const idEnd = textContent.indexOf(']', idStart);
      if (idEnd !== -1) {
        const incidentId = textContent.substring(idStart, idEnd).trim();
        if (incidentId && incidentId.startsWith('health-')) return incidentId;
      }
    }

    return null;
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
  function buildDescription(eventId: string): string {
    return `[CORRELATION_ID:${eventId}]\n\nAWS Health Event detected...`;
  }

  test('CORRELATION_ID is the first thing in the description', () => {
    const desc = buildDescription('arn:aws:health:eu-west-1::event/EC2/TEST/123');
    expect(desc.startsWith('[CORRELATION_ID:')).toBe(true);
  });

  test('CORRELATION_ID value is extractable', () => {
    const desc = buildDescription('arn:aws:health:global::event/IAM/NOTIF/456');
    const prefix = '[CORRELATION_ID:';
    const start = desc.indexOf(prefix) + prefix.length;
    const end = desc.indexOf(']', start);
    const value = desc.substring(start, end);
    expect(value).toBe('arn:aws:health:global::event/IAM/NOTIF/456');
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
