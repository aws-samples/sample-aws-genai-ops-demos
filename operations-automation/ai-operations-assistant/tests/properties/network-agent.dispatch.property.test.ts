/**
 * Property tests for Network Agent dispatch and envelope shape.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise a pure TypeScript implementation that mirrors the
 * Python Network Agent's dispatch and response envelope logic so it can
 * be verified with fast-check without calling boto3.
 *
 * Property 1: Action dispatch is total and pure on input
 *   For every JSON payload p, the entrypoint either dispatches to a
 *   registered handler with p or returns a well-formed error envelope
 *   with success=false and domain="network". The entrypoint never raises
 *   an unhandled exception.
 *
 * Property 10: Response envelope shape is invariant
 *   Every Network_Agent response satisfies the JSON schema:
 *   - success: bool, domain: "network", data: object, formattedText: string,
 *   - metadata: { sourceApi: string, queryTimestamp: ISO 8601,
 *     dataFreshness: "real-time" | "near-real-time" | "cached" },
 *   - when success=false, error: non-empty string.
 *
 * **Validates: Requirements 1.7, 1.8, 1.9, 5.22**
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ---------------------------------------------------------------------------
// Local TypeScript implementation mirroring the Python Network Agent logic
// ---------------------------------------------------------------------------

/** The set of valid dataFreshness values per Req 1.7 / 5.22. */
const VALID_DATA_FRESHNESS = new Set(['real-time', 'near-real-time', 'cached']);

/** The registered action names from the Network Agent ACTIONS dict. */
const REGISTERED_ACTIONS = new Set([
  'list_enis',
  'start_capture',
  'stop_capture',
  'list_captures',
  'transform_capture',
  'get_capture_progress',
  'query_pcap',
  'search_fragmented_packets',
  'correlate_tcp_streams',
  'detect_retransmissions',
  'check_tls_hello_size',
  'get_conversation_stats',
  'reconstruct_tcp_handshake',
  'classify_tcp_resets',
  'detect_out_of_order_packets',
  'detect_zero_window',
  'analyze_tcp_options',
  'get_rtt_distribution',
  'get_request_response_latency',
  'diagnose_tcp_stream',
]);

/** Response envelope shape as returned by the Network Agent. */
interface NetworkAgentResponse {
  success: boolean;
  domain: string;
  data: Record<string, unknown>;
  formattedText: string;
  metadata: {
    sourceApi: string;
    queryTimestamp: string;
    dataFreshness: string;
    [key: string]: unknown;
  };
  error?: string;
}

/**
 * Build the uniform Network Agent response envelope.
 * Mirrors `build_response` in `agents/network-agent/main.py` (Req 1.7).
 */
function buildResponse(opts: {
  success: boolean;
  data?: Record<string, unknown>;
  formattedText?: string;
  sourceApi?: string;
  dataFreshness?: string;
  error?: string;
}): NetworkAgentResponse {
  const response: NetworkAgentResponse = {
    success: opts.success,
    domain: 'network',
    data: opts.data ?? {},
    formattedText: opts.formattedText ?? '',
    metadata: {
      sourceApi: opts.sourceApi ?? 'agentcore:Invoke',
      queryTimestamp: new Date().toISOString(),
      dataFreshness: opts.dataFreshness ?? 'real-time',
    },
  };
  if (opts.error !== undefined) {
    response.error = opts.error;
  }
  return response;
}

/**
 * Simulate a handler that always succeeds with domain-specific data.
 * Mirrors the pattern of real handlers returning build_response(success=True, ...).
 */
function stubHandler(_params: Record<string, unknown>): NetworkAgentResponse {
  return buildResponse({
    success: true,
    data: { stub: true },
    formattedText: 'Stub handler executed successfully.',
    sourceApi: 'stub:Action',
    dataFreshness: 'real-time',
  });
}

/**
 * Simulate a handler that raises an exception.
 * Used to verify Req 1.9 — exceptions are caught and converted to error envelopes.
 */
function throwingHandler(_params: Record<string, unknown>): NetworkAgentResponse {
  throw new Error('Simulated handler failure');
}

/**
 * Dispatch logic mirroring the Python Network Agent's @app.entrypoint.
 * Handles:
 * - Non-dict payloads → error envelope (Req 1.8)
 * - Missing/empty action → error envelope (Req 1.8)
 * - Unknown action → error envelope (Req 1.8)
 * - Handler exceptions → error envelope (Req 1.9)
 * - Successful dispatch → handler result (Req 1.7)
 */
function dispatch(
  payload: unknown,
  handlers: Map<string, (params: Record<string, unknown>) => NetworkAgentResponse>,
): NetworkAgentResponse {
  // Handle non-string, non-object payloads
  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload);
    } catch {
      return buildResponse({
        success: false,
        formattedText: 'Network Agent could not parse the request payload.',
        error: 'invalid_payload: failed to parse JSON string',
      });
    }
  }

  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    return buildResponse({
      success: false,
      formattedText: 'Network Agent received a payload that is not a JSON object.',
      error: `invalid_payload: expected JSON object, got ${payload === null ? 'null' : Array.isArray(payload) ? 'array' : typeof payload}`,
    });
  }

  const payloadObj = payload as Record<string, unknown>;
  const action = payloadObj['action'];
  const params = (payloadObj['params'] as Record<string, unknown>) ?? {};

  // Req 1.8 — missing, empty, or non-string action
  if (!action || typeof action !== 'string') {
    return buildResponse({
      success: false,
      formattedText: "Network Agent could not dispatch: the 'action' field is missing or empty.",
      error: "unknown_action: missing or empty 'action' field",
    });
  }

  const handler = handlers.get(action);
  if (!handler) {
    return buildResponse({
      success: false,
      formattedText: `Network Agent does not recognize the action '${action}'.`,
      error: `unknown_action: '${action}' is not a registered action`,
    });
  }

  // Req 1.9 — catch handler exceptions
  try {
    return handler(typeof params === 'object' && params !== null && !Array.isArray(params)
      ? params as Record<string, unknown>
      : {});
  } catch (exc) {
    return buildResponse({
      success: false,
      formattedText: `Network Agent action '${action}' failed with an unexpected error: ${exc}`,
      error: `handler_exception: action='${action}' message='${exc}'`,
    });
  }
}

// ---------------------------------------------------------------------------
// Envelope validation
// ---------------------------------------------------------------------------

/**
 * Validate that a response conforms to the Network Agent envelope schema.
 * Returns an array of violation descriptions; empty means valid.
 */
function validateEnvelope(response: unknown): string[] {
  const violations: string[] = [];

  if (response === null || typeof response !== 'object') {
    violations.push('Response is not an object');
    return violations;
  }

  const r = response as Record<string, unknown>;

  // success: bool
  if (typeof r['success'] !== 'boolean') {
    violations.push(`'success' must be boolean, got ${typeof r['success']}`);
  }

  // domain: "network"
  if (r['domain'] !== 'network') {
    violations.push(`'domain' must be "network", got "${r['domain']}"`);
  }

  // data: object
  if (r['data'] === null || typeof r['data'] !== 'object' || Array.isArray(r['data'])) {
    violations.push(`'data' must be a non-null object, got ${r['data'] === null ? 'null' : Array.isArray(r['data']) ? 'array' : typeof r['data']}`);
  }

  // formattedText: string
  if (typeof r['formattedText'] !== 'string') {
    violations.push(`'formattedText' must be string, got ${typeof r['formattedText']}`);
  }

  // metadata: object with required fields
  if (r['metadata'] === null || typeof r['metadata'] !== 'object' || Array.isArray(r['metadata'])) {
    violations.push(`'metadata' must be a non-null object`);
  } else {
    const meta = r['metadata'] as Record<string, unknown>;

    if (typeof meta['sourceApi'] !== 'string') {
      violations.push(`'metadata.sourceApi' must be string, got ${typeof meta['sourceApi']}`);
    }

    if (typeof meta['queryTimestamp'] !== 'string') {
      violations.push(`'metadata.queryTimestamp' must be string, got ${typeof meta['queryTimestamp']}`);
    } else {
      // Validate ISO 8601 format
      const ts = meta['queryTimestamp'] as string;
      const parsed = Date.parse(ts);
      if (isNaN(parsed)) {
        violations.push(`'metadata.queryTimestamp' is not a valid ISO 8601 timestamp: "${ts}"`);
      }
    }

    if (typeof meta['dataFreshness'] !== 'string') {
      violations.push(`'metadata.dataFreshness' must be string, got ${typeof meta['dataFreshness']}`);
    } else if (!VALID_DATA_FRESHNESS.has(meta['dataFreshness'] as string)) {
      violations.push(
        `'metadata.dataFreshness' must be one of ${[...VALID_DATA_FRESHNESS].join(', ')}, got "${meta['dataFreshness']}"`,
      );
    }
  }

  // when success=false, error must be a non-empty string
  if (r['success'] === false) {
    if (typeof r['error'] !== 'string' || (r['error'] as string).length === 0) {
      violations.push(`When success=false, 'error' must be a non-empty string`);
    }
  }

  return violations;
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Arbitrary that produces deeply nested objects to stress the dispatch. */
const arbDeepObject: fc.Arbitrary<unknown> = fc.letrec((tie) => ({
  tree: fc.oneof(
    { depthSize: 'small' },
    fc.constant(null),
    fc.boolean(),
    fc.integer(),
    fc.double({ noNaN: true, noDefaultInfinity: true }),
    fc.string({ maxLength: 200 }),
    fc.array(tie('tree'), { maxLength: 3 }),
    fc.dictionary(fc.string({ maxLength: 20 }), tie('tree'), { maxKeys: 4 }),
  ),
})).tree;

/** Arbitrary that produces oversized strings (up to 10KB). */
const arbOversizedString = fc.string({ minLength: 1000, maxLength: 10000 });

/** Arbitrary that produces valid action names from the registered set. */
const arbValidAction = fc.constantFrom(...REGISTERED_ACTIONS);

/** Arbitrary that produces invalid action names (not in the registered set). */
const arbInvalidAction = fc.string({ minLength: 1, maxLength: 100 }).filter(
  (s) => !REGISTERED_ACTIONS.has(s),
);

/**
 * Arbitrary that produces arbitrary JSON payloads — the full space of
 * inputs the dispatch function must handle without crashing.
 */
const arbArbitraryPayload: fc.Arbitrary<unknown> = fc.oneof(
  // Null / undefined / primitives
  fc.constant(null),
  fc.constant(undefined),
  fc.boolean(),
  fc.integer(),
  fc.double({ noNaN: true, noDefaultInfinity: true }),
  fc.string({ maxLength: 500 }),
  // Arrays (not valid payloads)
  fc.array(fc.anything(), { maxLength: 5 }),
  // Empty object
  fc.constant({}),
  // Object with missing action
  fc.record({ params: fc.anything() }),
  // Object with empty action
  fc.record({ action: fc.constant(''), params: fc.anything() }),
  // Object with non-string action
  fc.record({ action: fc.oneof(fc.integer(), fc.boolean(), fc.constant(null), fc.array(fc.string())) }),
  // Object with unknown action
  fc.record({ action: arbInvalidAction, params: fc.anything() }),
  // Object with valid action and arbitrary params
  fc.record({ action: arbValidAction, params: arbDeepObject }),
  // Object with valid action and oversized string params
  fc.record({ action: arbValidAction, params: fc.record({ data: arbOversizedString }) }),
  // Deeply nested object
  arbDeepObject,
  // JSON string wrapping an object
  fc.record({ action: arbValidAction, params: fc.anything() }).map((obj) => JSON.stringify(obj)),
  // Malformed JSON string
  fc.string({ minLength: 1, maxLength: 200 }).map((s) => `{invalid json: ${s}`),
);

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Network Agent dispatch and envelope shape property tests', () => {
  // Build a handler map with stubs for all registered actions
  const handlers = new Map<string, (params: Record<string, unknown>) => NetworkAgentResponse>();
  for (const action of REGISTERED_ACTIONS) {
    handlers.set(action, stubHandler);
  }

  /**
   * Property 1: Action dispatch is total and pure on input
   *
   * For every JSON payload p, the entrypoint either dispatches to a
   * registered handler or returns a well-formed error envelope with
   * success=false and domain="network". The entrypoint never raises
   * an unhandled exception.
   *
   * **Validates: Requirements 1.7, 1.8, 1.9**
   */
  it('Property 1: dispatch never throws — all payloads produce a valid envelope — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbArbitraryPayload, (payload) => {
        // The dispatch must never throw
        let response: NetworkAgentResponse;
        try {
          response = dispatch(payload, handlers);
        } catch (e) {
          // If dispatch throws, the property is violated
          expect.fail(`Dispatch threw an unhandled exception for payload: ${JSON.stringify(payload)}: ${e}`);
          return;
        }

        // The response must always have domain="network"
        expect(response.domain).toBe('network');

        // The response must be a valid envelope
        const violations = validateEnvelope(response);
        expect(violations).toEqual([]);
      }),
      { numRuns: 200 },
    );
  });

  /**
   * Property 1 (continued): Handler exceptions are caught and converted
   * to error envelopes (Req 1.9).
   *
   * **Validates: Requirements 1.9**
   */
  it('Property 1: handler exceptions produce valid error envelopes — Feature: genai-operations-analytics-tool', () => {
    // Replace one action with a throwing handler
    const throwingHandlers = new Map(handlers);
    throwingHandlers.set('list_enis', throwingHandler);

    fc.assert(
      fc.property(
        fc.record({
          action: fc.constant('list_enis'),
          params: fc.anything(),
        }),
        (payload) => {
          const response = dispatch(payload, throwingHandlers);

          // Must not throw — the exception is caught
          expect(response.success).toBe(false);
          expect(response.domain).toBe('network');
          expect(typeof response.error).toBe('string');
          expect(response.error!.length).toBeGreaterThan(0);

          const violations = validateEnvelope(response);
          expect(violations).toEqual([]);
        },
      ),
      { numRuns: 50 },
    );
  });

  /**
   * Property 10: Response envelope shape is invariant
   *
   * Every Network_Agent response satisfies the JSON schema:
   * - success: bool, domain: "network", data: object, formattedText: string,
   * - metadata: { sourceApi: string, queryTimestamp: ISO 8601,
   *   dataFreshness ∈ {"real-time", "near-real-time", "cached"} },
   * - when success=false, error: non-empty string.
   *
   * **Validates: Requirements 1.7, 1.9, 5.22**
   */
  it('Property 10: every response from every action passes envelope schema validation — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbArbitraryPayload, (payload) => {
        const response = dispatch(payload, handlers);
        const violations = validateEnvelope(response);
        expect(violations).toEqual([]);
      }),
      { numRuns: 200 },
    );
  });

  /**
   * Property 10 (continued): Envelope shape holds for all three
   * dataFreshness values across different response paths.
   *
   * **Validates: Requirements 1.7, 5.22**
   */
  it('Property 10: dataFreshness is always one of the three accepted values — Feature: genai-operations-analytics-tool', () => {
    // Build handlers that return each freshness value
    const freshnessHandlers = new Map<string, (params: Record<string, unknown>) => NetworkAgentResponse>();
    const freshnessValues = ['real-time', 'near-real-time', 'cached'];
    let idx = 0;
    for (const action of REGISTERED_ACTIONS) {
      const freshness = freshnessValues[idx % freshnessValues.length];
      freshnessHandlers.set(action, (_params) =>
        buildResponse({
          success: true,
          data: { action },
          formattedText: `Action ${action} completed.`,
          sourceApi: `test:${action}`,
          dataFreshness: freshness,
        }),
      );
      idx++;
    }

    fc.assert(
      fc.property(arbValidAction, (action) => {
        const payload = { action, params: {} };
        const response = dispatch(payload, freshnessHandlers);

        expect(response.success).toBe(true);
        expect(VALID_DATA_FRESHNESS.has(response.metadata.dataFreshness)).toBe(true);

        const violations = validateEnvelope(response);
        expect(violations).toEqual([]);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 1 + 10: Unknown actions produce valid error envelopes (Req 1.8).
   *
   * **Validates: Requirements 1.8**
   */
  it('Property 1+10: unknown actions produce valid error envelopes with success=false — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbInvalidAction, (action) => {
        const payload = { action, params: {} };
        const response = dispatch(payload, handlers);

        expect(response.success).toBe(false);
        expect(response.domain).toBe('network');
        expect(typeof response.error).toBe('string');
        expect(response.error!.length).toBeGreaterThan(0);
        expect(response.error).toContain('unknown_action');

        const violations = validateEnvelope(response);
        expect(violations).toEqual([]);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 1 + 10: Missing or empty action field produces valid error
   * envelopes (Req 1.8).
   *
   * **Validates: Requirements 1.8**
   */
  it('Property 1+10: missing or empty action produces valid error envelopes — Feature: genai-operations-analytics-tool', () => {
    const missingActionPayloads: unknown[] = [
      {},
      { params: { foo: 'bar' } },
      { action: '' },
      { action: null },
      { action: 0 },
      { action: false },
      { action: [] },
      { action: {} },
    ];

    for (const payload of missingActionPayloads) {
      const response = dispatch(payload, handlers);

      expect(response.success).toBe(false);
      expect(response.domain).toBe('network');
      expect(typeof response.error).toBe('string');
      expect(response.error!.length).toBeGreaterThan(0);

      const violations = validateEnvelope(response);
      expect(violations).toEqual([]);
    }
  });

  /**
   * Property 10: buildResponse always produces a valid envelope regardless
   * of input combinations.
   *
   * **Validates: Requirements 1.7, 5.22**
   */
  it('Property 10: buildResponse helper always produces valid envelopes — Feature: genai-operations-analytics-tool', () => {
    const arbBuildResponseOpts = fc.record({
      success: fc.boolean(),
      data: fc.option(fc.dictionary(fc.string({ maxLength: 20 }), fc.anything()), { nil: undefined }),
      formattedText: fc.option(fc.string({ maxLength: 500 }), { nil: undefined }),
      sourceApi: fc.option(fc.string({ maxLength: 100 }), { nil: undefined }),
      dataFreshness: fc.option(fc.constantFrom('real-time', 'near-real-time', 'cached'), { nil: undefined }),
      error: fc.option(fc.string({ minLength: 1, maxLength: 200 }), { nil: undefined }),
    });

    fc.assert(
      fc.property(arbBuildResponseOpts, (opts) => {
        // When success=false, ensure error is provided
        const adjustedOpts = { ...opts };
        if (!adjustedOpts.success && !adjustedOpts.error) {
          adjustedOpts.error = 'test_error: generated for property test';
        }

        const response = buildResponse(adjustedOpts as {
          success: boolean;
          data?: Record<string, unknown>;
          formattedText?: string;
          sourceApi?: string;
          dataFreshness?: string;
          error?: string;
        });

        const violations = validateEnvelope(response);
        expect(violations).toEqual([]);
      }),
      { numRuns: 200 },
    );
  });
});
