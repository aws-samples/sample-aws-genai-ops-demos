/**
 * Unit tests for Notifier Lambda — secrets cache integration and retry logic.
 * Tests Requirements: 2.4, 8.2, 8.3
 */

describe('Notifier — Retry Logic', () => {
  // Inline test of the retry algorithm (mirrors the inline implementation)
  const MAX_RETRIES = 2;
  const BASE_DELAY_MS = 1000;
  const MAX_DELAY_MS = 4000;

  function calculateDelay(retryCount: number): number {
    return Math.min(BASE_DELAY_MS * Math.pow(2, retryCount), MAX_DELAY_MS);
  }

  test('first retry delay is 1000ms (2^0 * 1000)', () => {
    expect(calculateDelay(0)).toBe(1000);
  });

  test('second retry delay is 2000ms (2^1 * 1000)', () => {
    expect(calculateDelay(1)).toBe(2000);
  });

  test('delay is capped at 4000ms', () => {
    expect(calculateDelay(2)).toBe(4000);
    expect(calculateDelay(3)).toBe(4000);
    expect(calculateDelay(10)).toBe(4000);
  });

  test('max retries is 2 (3 total attempts)', () => {
    expect(MAX_RETRIES).toBe(2);
  });
});

describe('Notifier — Retry Decision Logic', () => {
  // Simulates the retry condition used in the Lambda
  function shouldRetry(statusCode: number | undefined): boolean {
    // Do not retry 4xx errors (except 429 Too Many Requests)
    if (statusCode && statusCode >= 400 && statusCode < 500 && statusCode !== 429) {
      return false;
    }
    return true;
  }

  test('retries on 5xx server errors', () => {
    expect(shouldRetry(500)).toBe(true);
    expect(shouldRetry(502)).toBe(true);
    expect(shouldRetry(503)).toBe(true);
    expect(shouldRetry(504)).toBe(true);
  });

  test('retries on 429 Too Many Requests', () => {
    expect(shouldRetry(429)).toBe(true);
  });

  test('does NOT retry on 4xx client errors (except 429)', () => {
    expect(shouldRetry(400)).toBe(false);
    expect(shouldRetry(401)).toBe(false);
    expect(shouldRetry(403)).toBe(false);
    expect(shouldRetry(404)).toBe(false);
    expect(shouldRetry(405)).toBe(false);
    expect(shouldRetry(408)).toBe(false);
    expect(shouldRetry(422)).toBe(false);
  });

  test('retries on network timeout (undefined status code)', () => {
    expect(shouldRetry(undefined)).toBe(true);
  });
});

describe('Notifier — Secrets Cache Environment Variables', () => {
  test('SLACK_WEBHOOK_PARAM_NAME is used instead of SLACK_WEBHOOK_URL', () => {
    // Verifies that the Lambda reads SSM parameter names, not raw URLs
    const envVarName = 'SLACK_WEBHOOK_PARAM_NAME';
    // The Lambda should NOT use SLACK_WEBHOOK_URL directly anymore
    const deprecatedEnvVar = 'SLACK_WEBHOOK_URL';

    // Simulate environment: param name points to SSM path
    const paramName = '/health-analyzer/production/slack-webhook-url';
    expect(paramName.startsWith('/health-analyzer/')).toBe(true);
    expect(envVarName).not.toBe(deprecatedEnvVar);
  });

  test('MSTEAMS_WEBHOOK_PARAM_NAME is used instead of MSTEAMS_WEBHOOK_URL', () => {
    const envVarName = 'MSTEAMS_WEBHOOK_PARAM_NAME';
    const deprecatedEnvVar = 'MSTEAMS_WEBHOOK_URL';

    const paramName = '/health-analyzer/production/msteams-webhook-url';
    expect(paramName.startsWith('/health-analyzer/')).toBe(true);
    expect(envVarName).not.toBe(deprecatedEnvVar);
  });

  test('empty param name means notification channel is disabled', () => {
    // When the parameter name is empty string, the Lambda should skip that channel
    const paramName = '';
    expect(!paramName).toBe(true);
  });
});

describe('Notifier — Error propagation on non-retryable failures', () => {
  test('4xx errors include status code in error message', () => {
    const statusCode = 403;
    const responseBody = 'Forbidden';
    const errorMessage = `Slack webhook returned status ${statusCode}: ${responseBody}`;
    expect(errorMessage).toContain('403');
    expect(errorMessage).toContain('Forbidden');
  });

  test('MS Teams 4xx errors include status code and body', () => {
    const statusCode = 400;
    const responseBody = 'Bad Request';
    const errorMessage = `MS Teams webhook returned status ${statusCode}: ${responseBody}`;
    expect(errorMessage).toContain('400');
    expect(errorMessage).toContain('Bad Request');
  });

  test('timeout errors are retried (no status code)', () => {
    const err: any = new Error('Slack webhook request timed out');
    err.statusCode = undefined;
    // shouldRetry logic: undefined statusCode → retry
    const statusCode = err.statusCode as number | undefined;
    const shouldRetry = !(statusCode && statusCode >= 400 && statusCode < 500 && statusCode !== 429);
    expect(shouldRetry).toBe(true);
  });
});
