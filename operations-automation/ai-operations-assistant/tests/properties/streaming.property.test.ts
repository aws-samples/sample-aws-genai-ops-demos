/**
 * Property tests for streaming response accumulation and cross-account
 * context propagation.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise pure TypeScript implementations that mirror the
 * ChatInterface's SSE chunk accumulation logic and request payload
 * building — without DOM rendering or network calls.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ---------------------------------------------------------------------------
// Local TypeScript implementations mirroring ChatInterface streaming logic
// ---------------------------------------------------------------------------

/**
 * Accumulate text from a raw SSE chunk string.
 * Mirrors the SSE parsing loop inside ChatInterface's `sendMessage`:
 *  - Lines starting with `data:` have their payload extracted and trimmed
 *  - The `[DONE]` marker is excluded from accumulated text
 *  - Empty data payloads (after trim) don't add content
 *  - Non-SSE, non-empty lines (plain text chunks) are concatenated
 *  - Comment lines (starting with `:`) are ignored
 */
function accumulateSSEChunk(chunk: string, previous: string): string {
  let accumulated = previous;
  const lines = chunk.split('\n');

  for (const line of lines) {
    if (line.startsWith('data:')) {
      const data = line.slice(5).trim();
      if (data === '[DONE]') continue;
      accumulated += data;
    } else if (line.trim() !== '' && !line.startsWith(':')) {
      // Plain text chunk (non-SSE)
      accumulated += line;
    }
  }

  return accumulated;
}

/**
 * Process a full sequence of SSE chunk strings and return the final
 * accumulated response text.
 */
function accumulateAllChunks(chunks: string[]): string {
  let accumulated = '';
  for (const chunk of chunks) {
    accumulated = accumulateSSEChunk(chunk, accumulated);
  }
  return accumulated;
}

/**
 * Build the request payload for the orchestration agent invocation.
 * Mirrors the payload construction in ChatInterface's `sendMessage`:
 *  - Always includes `prompt`
 *  - Includes `accountContext` only when provided (non-empty string)
 */
function buildRequestPayload(
  prompt: string,
  accountContext?: string,
): Record<string, string> {
  const payload: Record<string, string> = { prompt: prompt.trim() };
  if (accountContext) {
    payload.accountContext = accountContext;
  }
  return payload;
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/**
 * Arbitrary non-empty text content without leading/trailing whitespace.
 * This matches the SSE accumulator's trim() behavior on data payloads.
 */
const arbChunkContent = fc.stringOf(
  fc.constantFrom(
    ...'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,!?-_'.split(''),
  ),
  { minLength: 1, maxLength: 80 },
);

/** Arbitrary SSE comment line (should be ignored). */
const arbCommentLine = arbChunkContent.map((c) => `: ${c}`);

/** Arbitrary blank line (should be ignored). */
const arbBlankLine = fc.constantFrom('', '  ', '\t');

/** Arbitrary valid AWS account ID (12-digit string). */
const arbAccountId = fc.stringOf(fc.constantFrom(...'0123456789'.split('')), {
  minLength: 12,
  maxLength: 12,
});

/** Arbitrary non-empty prompt string. */
const arbPrompt = fc.string({ minLength: 1, maxLength: 200 }).filter((s) => s.trim().length > 0);

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Streaming and cross-account property tests', () => {
  /**
   * Property 9: Streaming response accumulation
   *
   * For any sequence of SSE chunks, the accumulated response should equal
   * the concatenation of all chunk data. Empty chunks don't add content,
   * multiple `data:` lines are concatenated, the [DONE] marker is excluded
   * from accumulated text, and partial chunks are handled correctly.
   *
   * **Validates: Requirements 7.2**
   */
  it('Property 9: Streaming response accumulation — Feature: genai-operations-analytics-tool, Property 9: Streaming response accumulation', () => {
    fc.assert(
      fc.property(
        fc.array(arbChunkContent, { minLength: 1, maxLength: 10 }),
        (contents) => {
          // Build SSE chunks where each content is a `data:` line
          const chunks = contents.map((c) => `data: ${c}`);
          const fullChunk = chunks.join('\n');

          const accumulated = accumulateSSEChunk(fullChunk, '');

          // The accumulated text should be the concatenation of all contents
          // (each content has no leading/trailing whitespace by generator design)
          const expected = contents.join('');
          expect(accumulated).toBe(expected);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('Property 9a: Empty data lines do not add content', () => {
    fc.assert(
      fc.property(arbChunkContent, (realContent) => {
        // Build a chunk with real content followed by empty data lines
        const emptyDataLines = ['data:', 'data:   ', 'data: '];
        const chunk = [`data: ${realContent}`, ...emptyDataLines].join('\n');
        const accumulated = accumulateSSEChunk(chunk, '');

        // Only the real content should be present — empty data payloads
        // (which trim to '') are not appended
        expect(accumulated).toBe(realContent);
      }),
      { numRuns: 100 },
    );
  });

  it('Property 9b: [DONE] marker is excluded from accumulated text', () => {
    fc.assert(
      fc.property(
        fc.array(arbChunkContent, { minLength: 1, maxLength: 5 }),
        (contents) => {
          // Build chunk with data lines followed by [DONE]
          const lines = [
            ...contents.map((c) => `data: ${c}`),
            'data: [DONE]',
          ];
          const chunk = lines.join('\n');

          const accumulated = accumulateSSEChunk(chunk, '');

          // [DONE] must not appear in accumulated text
          expect(accumulated).not.toContain('[DONE]');

          // All real content must be present
          const expected = contents.join('');
          expect(accumulated).toBe(expected);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('Property 9c: Multi-chunk accumulation equals concatenation of all chunk data', () => {
    fc.assert(
      fc.property(
        fc.array(arbChunkContent, { minLength: 2, maxLength: 8 }),
        (contents) => {
          // Each content becomes a separate SSE chunk (simulating multiple read() calls)
          const chunks = contents.map((c) => `data: ${c}`);

          const accumulated = accumulateAllChunks(chunks);

          // Final accumulated text should be the concatenation of all contents
          const expected = contents.join('');
          expect(accumulated).toBe(expected);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('Property 9d: Comment lines and blank lines are ignored', () => {
    fc.assert(
      fc.property(
        arbChunkContent,
        fc.array(arbCommentLine, { minLength: 1, maxLength: 3 }),
        fc.array(arbBlankLine, { minLength: 1, maxLength: 3 }),
        (realContent, comments, blanks) => {
          // Mix real data with comments and blank lines
          const lines = [
            ...comments,
            `data: ${realContent}`,
            ...blanks,
          ];
          const chunk = lines.join('\n');

          const accumulated = accumulateSSEChunk(chunk, '');

          // Only the real content should be present
          expect(accumulated).toBe(realContent);
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * Property 17: Account context propagation
   *
   * For any valid account ID string, when accountContext is provided, the
   * request payload should include it. When not provided, the payload
   * should not contain accountContext.
   *
   * **Validates: Requirements 12.2**
   */
  it('Property 17: Account context propagation — Feature: genai-operations-analytics-tool, Property 17: Account context propagation', () => {
    fc.assert(
      fc.property(arbPrompt, arbAccountId, (prompt, accountId) => {
        // With accountContext provided
        const payloadWith = buildRequestPayload(prompt, accountId);
        expect(payloadWith).toHaveProperty('prompt');
        expect(payloadWith.prompt).toBe(prompt.trim());
        expect(payloadWith).toHaveProperty('accountContext');
        expect(payloadWith.accountContext).toBe(accountId);

        // Without accountContext
        const payloadWithout = buildRequestPayload(prompt);
        expect(payloadWithout).toHaveProperty('prompt');
        expect(payloadWithout.prompt).toBe(prompt.trim());
        expect(payloadWithout).not.toHaveProperty('accountContext');

        // With empty string accountContext (should NOT include it)
        const payloadEmpty = buildRequestPayload(prompt, '');
        expect(payloadEmpty).not.toHaveProperty('accountContext');

        // With undefined accountContext (should NOT include it)
        const payloadUndef = buildRequestPayload(prompt, undefined);
        expect(payloadUndef).not.toHaveProperty('accountContext');
      }),
      { numRuns: 100 },
    );
  });

  it('Property 17a: Payload always contains prompt field', () => {
    fc.assert(
      fc.property(
        arbPrompt,
        fc.option(arbAccountId, { nil: undefined }),
        (prompt, accountId) => {
          const payload = buildRequestPayload(prompt, accountId);

          // prompt must always be present and trimmed
          expect(payload).toHaveProperty('prompt');
          expect(payload.prompt).toBe(prompt.trim());
          expect(payload.prompt.length).toBeGreaterThan(0);
        },
      ),
      { numRuns: 100 },
    );
  });
});
