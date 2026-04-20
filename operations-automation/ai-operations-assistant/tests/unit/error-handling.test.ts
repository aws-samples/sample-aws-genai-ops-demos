/**
 * Unit tests for error handling edge cases
 * Pure logic tests — no DOM or React dependencies.
 *
 * Validates: Requirements 3.4, 4.4, 5.4, 6.4, 12.3
 */
import { describe, it, expect } from 'vitest';

// ---------------------------------------------------------------------------
// 1. Exponential backoff helper (mirrors ChatInterface constants)
// ---------------------------------------------------------------------------
const INITIAL_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;
const BACKOFF_MULTIPLIER = 2;

/** Compute the backoff delay for the Nth retry (0-indexed). */
function computeBackoff(attempt: number): number {
  const raw = INITIAL_BACKOFF_MS * Math.pow(BACKOFF_MULTIPLIER, attempt);
  return Math.min(raw, MAX_BACKOFF_MS);
}

describe('Exponential backoff', () => {
  it('starts at 1 s', () => {
    expect(computeBackoff(0)).toBe(1_000);
  });

  it('doubles each attempt: 1s → 2s → 4s → 8s → 16s', () => {
    expect(computeBackoff(1)).toBe(2_000);
    expect(computeBackoff(2)).toBe(4_000);
    expect(computeBackoff(3)).toBe(8_000);
    expect(computeBackoff(4)).toBe(16_000);
  });

  it('caps at 30 s', () => {
    // 2^5 * 1000 = 32000 → capped to 30000
    expect(computeBackoff(5)).toBe(30_000);
    expect(computeBackoff(10)).toBe(30_000);
    expect(computeBackoff(100)).toBe(30_000);
  });
});

// ---------------------------------------------------------------------------
// 2. Token expiry detection
// ---------------------------------------------------------------------------

/** Base64url-encode a string (no padding). */
function b64url(str: string): string {
  return Buffer.from(str)
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

/** Build a minimal JWT-like token with the given `exp` (epoch seconds). */
function buildToken(exp: number): string {
  const header = b64url(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  const payload = b64url(JSON.stringify({ sub: 'user-1', exp }));
  return `${header}.${payload}.signature`;
}

/** Return true when the token's `exp` claim is in the past. */
function isTokenExpired(token: string): boolean {
  try {
    const payloadB64 = token.split('.')[1];
    if (!payloadB64) return true;
    const json = Buffer.from(payloadB64, 'base64').toString('utf-8');
    const { exp } = JSON.parse(json) as { exp?: number };
    if (typeof exp !== 'number') return true;
    return exp < Math.floor(Date.now() / 1000);
  } catch {
    return true;
  }
}

describe('Token expiry detection', () => {
  it('detects an expired token (exp in the past)', () => {
    const pastExp = Math.floor(Date.now() / 1000) - 3600; // 1 hour ago
    expect(isTokenExpired(buildToken(pastExp))).toBe(true);
  });

  it('detects a valid token (exp in the future)', () => {
    const futureExp = Math.floor(Date.now() / 1000) + 3600; // 1 hour from now
    expect(isTokenExpired(buildToken(futureExp))).toBe(false);
  });

  it('treats a malformed token as expired', () => {
    expect(isTokenExpired('not-a-jwt')).toBe(true);
    expect(isTokenExpired('')).toBe(true);
  });

  it('treats a token without exp claim as expired', () => {
    const header = b64url(JSON.stringify({ alg: 'none' }));
    const payload = b64url(JSON.stringify({ sub: 'user-1' })); // no exp
    const token = `${header}.${payload}.sig`;
    expect(isTokenExpired(token)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3. DynamoDB error formatting
// ---------------------------------------------------------------------------

/** Format a DynamoDB error so the message includes the operation name. */
function formatDynamoError(
  operation: string,
  error: Error | string,
): string {
  const msg = typeof error === 'string' ? error : error.message;
  return `DynamoDB ${operation} failed: ${msg}`;
}

describe('DynamoDB error formatting', () => {
  it('includes the operation name in the message', () => {
    const result = formatDynamoError('PutItem', new Error('ConditionalCheckFailed'));
    expect(result).toContain('PutItem');
    expect(result).toContain('ConditionalCheckFailed');
  });

  it('works with string errors', () => {
    const result = formatDynamoError('GetItem', 'Timeout');
    expect(result).toBe('DynamoDB GetItem failed: Timeout');
  });

  it('includes operation for various DynamoDB operations', () => {
    for (const op of ['PutItem', 'GetItem', 'UpdateItem', 'DeleteItem', 'Query']) {
      const result = formatDynamoError(op, 'some error');
      expect(result).toContain(op);
    }
  });
});

// ---------------------------------------------------------------------------
// 4. Malformed SSE fallback
// ---------------------------------------------------------------------------

/**
 * Parse an SSE-formatted chunk. If the text contains `data:` lines, extract
 * and concatenate their payloads. Otherwise return the raw text as-is.
 *
 * This mirrors the ChatInterface SSE parsing logic.
 */
function parseSSEChunk(chunk: string): string {
  const lines = chunk.split('\n');
  const dataLines: string[] = [];
  let hasSSE = false;

  for (const line of lines) {
    if (line.startsWith('data:')) {
      hasSSE = true;
      const data = line.slice(5).trim();
      if (data !== '[DONE]') {
        dataLines.push(data);
      }
    }
  }

  // If no SSE framing detected, return the raw text
  if (!hasSSE) {
    return chunk;
  }

  return dataLines.join('');
}

describe('Malformed SSE fallback', () => {
  it('parses valid SSE data lines', () => {
    const chunk = 'data: Hello\ndata: World\n';
    expect(parseSSEChunk(chunk)).toBe('HelloWorld');
  });

  it('ignores [DONE] sentinel', () => {
    const chunk = 'data: Hello\ndata: [DONE]\n';
    expect(parseSSEChunk(chunk)).toBe('Hello');
  });

  it('returns non-SSE text as-is', () => {
    const plain = 'This is just plain text with no SSE framing';
    expect(parseSSEChunk(plain)).toBe(plain);
  });

  it('returns raw text for malformed responses', () => {
    const malformed = '{"error":"internal server error"}';
    expect(parseSSEChunk(malformed)).toBe(malformed);
  });

  it('handles empty input', () => {
    expect(parseSSEChunk('')).toBe('');
  });

  it('handles mixed SSE and non-SSE lines', () => {
    // When at least one `data:` line exists, only data payloads are extracted
    const mixed = ':comment\ndata: payload\nrandom noise\n';
    expect(parseSSEChunk(mixed)).toBe('payload');
  });
});
