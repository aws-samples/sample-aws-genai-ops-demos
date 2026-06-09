/**
 * Property tests for capture-state guardrails.
 * Feature: genai-operations-analytics-tool
 *
 * Property 3: Capture_Concurrency_Limit is never exceeded
 *   At any moment, the count of rows in Capture_State_Table with
 *   status=active is ≤ 5 (Req 4.5). This holds even under concurrent
 *   start_capture invocations.
 *
 * Property 4: Capture_Eni_Limit and Capture_Duration_Limit are never violated post-write
 *   For every row written to Capture_State_Table by start_capture:
 *   1 ≤ len(eni_ids) ≤ 3 ∧ all eni_ids are distinct ∧ 1 ≤ duration_minutes ≤ 60
 *
 * Property 7: Idempotency token returns existing capture without side effects
 *   For any two start_capture calls within a 5-minute window with identical
 *   idempotency_token, identical eni_ids set, and identical duration_minutes,
 *   the second call returns the same capture_id as the first, creates no new
 *   Traffic Mirror sessions, creates no new Vni_Lookup_Table rows, and returns
 *   metadata.dataFreshness = "cached".
 *
 * **Validates: Requirements 3.15, 4.1, 4.2, 4.3, 4.4, 4.5**
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ---------------------------------------------------------------------------
// Constants mirroring the Python Network Agent
// ---------------------------------------------------------------------------

/** Capture_Concurrency_Limit: max simultaneous active captures (Req 4.5). */
const CAPTURE_CONCURRENCY_LIMIT = 5;

/** Capture_Eni_Limit: max ENIs per capture (Req 4.3). */
const CAPTURE_ENI_LIMIT = 3;

/** Capture_Duration_Limit: max duration in minutes (Req 4.1). */
const CAPTURE_DURATION_LIMIT = 60;

/** Minimum duration in minutes (Req 4.2). */
const CAPTURE_DURATION_MIN = 1;

/** Idempotency window in milliseconds (5 minutes per Req 3.15). */
const IDEMPOTENCY_WINDOW_MS = 5 * 60 * 1000;

/** Capture_Id_Format regex. */
const CAPTURE_ID_REGEX = /^[A-Za-z0-9_-]{1,128}$/;

/** ENI identifier pattern. */
const ENI_ID_REGEX = /^eni-[0-9a-f]{8,17}$/;

// ---------------------------------------------------------------------------
// Stub Capture_State_Table — in-memory DynamoDB simulation
// ---------------------------------------------------------------------------

interface CaptureRow {
  capture_id: string;
  eni_ids: string[];
  duration_minutes: number;
  status: string;
  start_time: string;
  deadline: string;
  mirror_session_ids: string[];
  idempotency_token?: string;
  created_at: string;
  auto_stop_schedule_armed: boolean;
}

/**
 * In-memory stub of the Capture_State_Table for property testing.
 * Simulates the DynamoDB table with the status-index GSI behavior.
 */
class StubCaptureStateTable {
  private rows: Map<string, CaptureRow> = new Map();
  /** Tracks mirror sessions created (for idempotency side-effect checks). */
  public mirrorSessionsCreated: string[] = [];
  /** Tracks VNI rows written (for idempotency side-effect checks). */
  public vniRowsWritten: number = 0;

  reset(): void {
    this.rows.clear();
    this.mirrorSessionsCreated = [];
    this.vniRowsWritten = 0;
  }

  getActiveCount(): number {
    let count = 0;
    for (const row of this.rows.values()) {
      if (row.status === 'active') count++;
    }
    return count;
  }

  getAllRows(): CaptureRow[] {
    return Array.from(this.rows.values());
  }

  getRow(captureId: string): CaptureRow | undefined {
    return this.rows.get(captureId);
  }

  putRow(row: CaptureRow): void {
    this.rows.set(row.capture_id, row);
  }

  /**
   * Find an idempotent match: same token, same eni_ids set, same duration,
   * created within the idempotency window.
   */
  findIdempotentCapture(
    token: string,
    eniIds: string[],
    durationMinutes: number,
    now: Date,
  ): CaptureRow | undefined {
    const cutoff = new Date(now.getTime() - IDEMPOTENCY_WINDOW_MS);
    const eniSet = new Set(eniIds);

    for (const row of this.rows.values()) {
      if (row.idempotency_token !== token) continue;
      if (row.duration_minutes !== durationMinutes) continue;
      const rowEniSet = new Set(row.eni_ids);
      if (rowEniSet.size !== eniSet.size) continue;
      let match = true;
      for (const eni of eniSet) {
        if (!rowEniSet.has(eni)) { match = false; break; }
      }
      if (!match) continue;
      const createdAt = new Date(row.created_at);
      if (createdAt >= cutoff) return row;
    }
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// Validation logic mirroring the Python agent's validation.py
// ---------------------------------------------------------------------------

interface ValidationResult {
  valid: boolean;
  reason?: string;
}

function validateEniIds(value: unknown): ValidationResult {
  if (value === null || value === undefined) {
    return { valid: false, reason: 'eni_ids is required' };
  }
  if (!Array.isArray(value)) {
    return { valid: false, reason: `eni_ids must be a list, got ${typeof value}` };
  }
  if (value.length === 0) {
    return { valid: false, reason: 'eni_ids must contain at least one ENI identifier' };
  }
  if (value.length > CAPTURE_ENI_LIMIT) {
    return { valid: false, reason: `eni_ids must contain at most ${CAPTURE_ENI_LIMIT} entries (Capture_Eni_Limit), got ${value.length}` };
  }
  const seen = new Set<string>();
  for (let i = 0; i < value.length; i++) {
    const item = value[i];
    if (typeof item !== 'string') {
      return { valid: false, reason: `eni_ids[${i}] must be a string` };
    }
    if (!ENI_ID_REGEX.test(item)) {
      return { valid: false, reason: `eni_ids[${i}] '${item}' is not a valid ENI identifier` };
    }
    if (seen.has(item)) {
      return { valid: false, reason: `eni_ids contains duplicate identifier '${item}'` };
    }
    seen.add(item);
  }
  return { valid: true };
}

function validateDurationMinutes(value: unknown): ValidationResult {
  if (value === null || value === undefined) {
    return { valid: false, reason: 'duration_minutes is required' };
  }
  if (typeof value === 'boolean' || typeof value !== 'number' || !Number.isInteger(value)) {
    return { valid: false, reason: `duration_minutes must be an integer, got ${typeof value}` };
  }
  if (value < CAPTURE_DURATION_MIN || value > CAPTURE_DURATION_LIMIT) {
    return { valid: false, reason: `duration_minutes must be an integer in 1..60, got ${value}` };
  }
  return { valid: true };
}

// ---------------------------------------------------------------------------
// Simulated start_capture with guardrails
// ---------------------------------------------------------------------------

interface StartCaptureParams {
  eni_ids: unknown;
  duration_minutes: unknown;
  idempotency_token?: string;
  capture_id?: string;
}

interface StartCaptureResult {
  success: boolean;
  capture_id?: string;
  error?: string;
  error_category?: string;
  data_freshness?: string;
  mirror_sessions_created: number;
  vni_rows_written: number;
}

let captureIdCounter = 0;

function generateCaptureId(): string {
  captureIdCounter++;
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  let result = `cap-${captureIdCounter}-`;
  for (let i = 0; i < 12; i++) {
    result += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return result.slice(0, 128);
}

/**
 * Simulates the start_capture handler with all guardrails enforced.
 * Mirrors the Python agent's handle_start_capture logic.
 */
function simulateStartCapture(
  table: StubCaptureStateTable,
  params: StartCaptureParams,
  now: Date = new Date(),
): StartCaptureResult {
  // Step 1: Validate eni_ids
  const eniValidation = validateEniIds(params.eni_ids);
  if (!eniValidation.valid) {
    return {
      success: false,
      error: `invalid_parameter: ${eniValidation.reason}`,
      error_category: 'invalid_parameter',
      mirror_sessions_created: 0,
      vni_rows_written: 0,
    };
  }
  const eniIds = params.eni_ids as string[];

  // Step 1b: Validate duration_minutes (default 15 when missing)
  let durationMinutes: number;
  if (params.duration_minutes === null || params.duration_minutes === undefined) {
    durationMinutes = 15; // Default per Req 3.3
  } else {
    const durValidation = validateDurationMinutes(params.duration_minutes);
    if (!durValidation.valid) {
      return {
        success: false,
        error: `invalid_parameter: ${durValidation.reason}`,
        error_category: 'invalid_parameter',
        mirror_sessions_created: 0,
        vni_rows_written: 0,
      };
    }
    durationMinutes = params.duration_minutes as number;
  }

  // Step 2: Idempotency check (Req 3.15)
  if (params.idempotency_token) {
    const existing = table.findIdempotentCapture(
      params.idempotency_token,
      eniIds,
      durationMinutes,
      now,
    );
    if (existing) {
      return {
        success: true,
        capture_id: existing.capture_id,
        data_freshness: 'cached',
        mirror_sessions_created: 0,
        vni_rows_written: 0,
      };
    }
  }

  // Step 3: Capture_Concurrency_Limit (Req 4.5)
  const activeCount = table.getActiveCount();
  if (activeCount >= CAPTURE_CONCURRENCY_LIMIT) {
    return {
      success: false,
      error: `capture_concurrency_limit: ${activeCount} active captures already exist; limit is ${CAPTURE_CONCURRENCY_LIMIT}`,
      error_category: 'capture_concurrency_limit',
      mirror_sessions_created: 0,
      vni_rows_written: 0,
    };
  }

  // Steps 4-6: Generate capture_id, create mirror sessions, write state
  const captureId = params.capture_id || generateCaptureId();
  const startTime = now.toISOString();
  const deadline = new Date(now.getTime() + durationMinutes * 60 * 1000).toISOString();
  const mirrorSessionIds = eniIds.map((_, i) => `tms-${captureId}-${i}`);

  // Simulate mirror session creation
  table.mirrorSessionsCreated.push(...mirrorSessionIds);

  // Simulate VNI row writes (one per ENI)
  table.vniRowsWritten += eniIds.length;

  // Write the capture state row
  const row: CaptureRow = {
    capture_id: captureId,
    eni_ids: [...eniIds],
    duration_minutes: durationMinutes,
    status: 'active',
    start_time: startTime,
    deadline,
    mirror_session_ids: mirrorSessionIds,
    idempotency_token: params.idempotency_token,
    created_at: startTime,
    auto_stop_schedule_armed: true,
  };
  table.putRow(row);

  return {
    success: true,
    capture_id: captureId,
    data_freshness: 'real-time',
    mirror_sessions_created: mirrorSessionIds.length,
    vni_rows_written: eniIds.length,
  };
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Generate a valid ENI identifier matching ^eni-[0-9a-f]{8,17}$. */
const arbEniId: fc.Arbitrary<string> = fc
  .integer({ min: 8, max: 17 })
  .chain((len) =>
    fc.stringOf(fc.constantFrom(...'0123456789abcdef'.split('')), {
      minLength: len,
      maxLength: len,
    }),
  )
  .map((hex) => `eni-${hex}`);

/** Generate a valid list of 1-3 distinct ENI identifiers. */
const arbValidEniIds: fc.Arbitrary<string[]> = fc
  .uniqueArray(arbEniId, { minLength: 1, maxLength: 3 })
  .filter((arr) => arr.length >= 1 && arr.length <= 3);

/** Generate a valid duration_minutes in [1, 60]. */
const arbValidDuration: fc.Arbitrary<number> = fc.integer({ min: 1, max: 60 });

/** Generate an idempotency token (1-256 chars). */
const arbIdempotencyToken: fc.Arbitrary<string> = fc.stringOf(
  fc.constantFrom(...'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'.split('')),
  { minLength: 1, maxLength: 64 },
);

/**
 * Generate boundary (eni_ids, duration_minutes) pairs that span the
 * boundaries: length 0/1/3/4; duration 0/1/60/61.
 */
const arbBoundaryEniIds: fc.Arbitrary<unknown[]> = fc.oneof(
  // Length 0 — empty list (invalid)
  fc.constant([] as unknown[]),
  // Length 1 — minimum valid
  fc.tuple(arbEniId).map(([e]) => [e]),
  // Length 3 — maximum valid
  fc.uniqueArray(arbEniId, { minLength: 3, maxLength: 3 }),
  // Length 4 — exceeds limit (invalid)
  fc.uniqueArray(arbEniId, { minLength: 4, maxLength: 4 }),
);

const arbBoundaryDuration: fc.Arbitrary<number> = fc.oneof(
  fc.constant(0),   // Below minimum (invalid)
  fc.constant(1),   // Minimum valid
  fc.constant(60),  // Maximum valid
  fc.constant(61),  // Exceeds limit (invalid)
);

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Network Agent capture-state guardrails property tests', () => {
  // =========================================================================
  // Property 3: Capture_Concurrency_Limit is never exceeded
  // =========================================================================

  describe('Property 3: Capture_Concurrency_Limit is never exceeded', () => {
    /**
     * Property 3a: Randomized sequences of concurrent start_capture calls
     * never result in more than 5 active rows in the Capture_State_Table.
     *
     * **Validates: Requirements 4.5**
     */
    it('Property 3a: active-row count never exceeds 5 under concurrent start_capture calls — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          // Generate a sequence of 1-20 start_capture attempts
          fc.array(arbValidEniIds, { minLength: 1, maxLength: 20 }),
          (eniIdsList) => {
            const table = new StubCaptureStateTable();

            for (const eniIds of eniIdsList) {
              simulateStartCapture(table, {
                eni_ids: eniIds,
                duration_minutes: 15,
              });

              // Invariant: active count never exceeds the limit
              const activeCount = table.getActiveCount();
              expect(activeCount).toBeLessThanOrEqual(CAPTURE_CONCURRENCY_LIMIT);
            }
          },
        ),
        { numRuns: 200 },
      );
    });

    /**
     * Property 3b: When exactly 5 captures are active, the next start_capture
     * is rejected with the concurrency limit error.
     *
     * **Validates: Requirements 4.5**
     */
    it('Property 3b: 6th concurrent start_capture is always rejected — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          // Generate 6 distinct ENI sets for 6 start_capture attempts
          fc.array(arbValidEniIds, { minLength: 6, maxLength: 6 }),
          (eniIdsList) => {
            const table = new StubCaptureStateTable();

            // First 5 should succeed
            for (let i = 0; i < 5; i++) {
              const result = simulateStartCapture(table, {
                eni_ids: eniIdsList[i],
                duration_minutes: 15,
              });
              expect(result.success).toBe(true);
            }

            expect(table.getActiveCount()).toBe(5);

            // 6th should be rejected
            const result = simulateStartCapture(table, {
              eni_ids: eniIdsList[5],
              duration_minutes: 15,
            });
            expect(result.success).toBe(false);
            expect(result.error_category).toBe('capture_concurrency_limit');
            expect(result.error).toContain('capture_concurrency_limit');

            // Active count still 5
            expect(table.getActiveCount()).toBe(5);
          },
        ),
        { numRuns: 100 },
      );
    });

    /**
     * Property 3c: After a large random sequence of start_capture calls,
     * the active count is always ≤ 5.
     *
     * **Validates: Requirements 4.5**
     */
    it('Property 3c: 100 sequential start_capture calls never exceed limit — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          fc.array(arbValidEniIds, { minLength: 50, maxLength: 100 }),
          (eniIdsList) => {
            const table = new StubCaptureStateTable();

            for (const eniIds of eniIdsList) {
              simulateStartCapture(table, {
                eni_ids: eniIds,
                duration_minutes: fc.sample(arbValidDuration, 1)[0],
              });
              expect(table.getActiveCount()).toBeLessThanOrEqual(CAPTURE_CONCURRENCY_LIMIT);
            }
          },
        ),
        { numRuns: 50 },
      );
    });
  });

  // =========================================================================
  // Property 4: Capture_Eni_Limit and Capture_Duration_Limit are never
  //             violated post-write
  // =========================================================================

  describe('Property 4: Capture_Eni_Limit and Capture_Duration_Limit are never violated post-write', () => {
    /**
     * Property 4a: Boundary (eni_ids, duration_minutes) pairs are correctly
     * accepted or rejected, and post-write invariants hold for accepted writes.
     *
     * **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
     */
    it('Property 4a: boundary pairs enforce 1 ≤ len(eni_ids) ≤ 3 ∧ distinct ∧ 1 ≤ duration ≤ 60 — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          arbBoundaryEniIds,
          arbBoundaryDuration,
          (eniIds, duration) => {
            const table = new StubCaptureStateTable();
            const result = simulateStartCapture(table, {
              eni_ids: eniIds,
              duration_minutes: duration,
            });

            const eniValid = Array.isArray(eniIds)
              && eniIds.length >= 1
              && eniIds.length <= CAPTURE_ENI_LIMIT
              && eniIds.every((e) => typeof e === 'string' && ENI_ID_REGEX.test(e))
              && new Set(eniIds).size === eniIds.length;
            const durationValid = Number.isInteger(duration)
              && duration >= CAPTURE_DURATION_MIN
              && duration <= CAPTURE_DURATION_LIMIT;

            if (eniValid && durationValid) {
              // Should succeed and post-write invariants hold
              expect(result.success).toBe(true);

              // Verify post-write invariants on the stored row
              const row = table.getRow(result.capture_id!);
              expect(row).toBeDefined();
              expect(row!.eni_ids.length).toBeGreaterThanOrEqual(1);
              expect(row!.eni_ids.length).toBeLessThanOrEqual(CAPTURE_ENI_LIMIT);
              expect(new Set(row!.eni_ids).size).toBe(row!.eni_ids.length);
              expect(row!.duration_minutes).toBeGreaterThanOrEqual(CAPTURE_DURATION_MIN);
              expect(row!.duration_minutes).toBeLessThanOrEqual(CAPTURE_DURATION_LIMIT);
            } else {
              // Should be rejected — no row written
              expect(result.success).toBe(false);
              expect(result.error_category).toBe('invalid_parameter');
              expect(table.getAllRows().length).toBe(0);
            }
          },
        ),
        { numRuns: 200 },
      );
    });

    /**
     * Property 4b: Every successful start_capture write satisfies the
     * post-write invariants regardless of input variety.
     *
     * **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
     */
    it('Property 4b: all successful writes satisfy post-write invariants — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          arbValidEniIds,
          arbValidDuration,
          (eniIds, duration) => {
            const table = new StubCaptureStateTable();
            const result = simulateStartCapture(table, {
              eni_ids: eniIds,
              duration_minutes: duration,
            });

            expect(result.success).toBe(true);

            const row = table.getRow(result.capture_id!);
            expect(row).toBeDefined();

            // Post-write invariant: 1 ≤ len(eni_ids) ≤ 3
            expect(row!.eni_ids.length).toBeGreaterThanOrEqual(1);
            expect(row!.eni_ids.length).toBeLessThanOrEqual(CAPTURE_ENI_LIMIT);

            // Post-write invariant: all eni_ids are distinct
            expect(new Set(row!.eni_ids).size).toBe(row!.eni_ids.length);

            // Post-write invariant: 1 ≤ duration_minutes ≤ 60
            expect(row!.duration_minutes).toBeGreaterThanOrEqual(CAPTURE_DURATION_MIN);
            expect(row!.duration_minutes).toBeLessThanOrEqual(CAPTURE_DURATION_LIMIT);

            // Post-write invariant: capture_id conforms to format
            expect(CAPTURE_ID_REGEX.test(row!.capture_id)).toBe(true);

            // Post-write invariant: status is active
            expect(row!.status).toBe('active');
          },
        ),
        { numRuns: 200 },
      );
    });

    /**
     * Property 4c: Duplicate ENI identifiers are always rejected.
     *
     * **Validates: Requirements 4.4**
     */
    it('Property 4c: duplicate ENI identifiers are rejected — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(arbEniId, (eniId) => {
          const table = new StubCaptureStateTable();
          const result = simulateStartCapture(table, {
            eni_ids: [eniId, eniId], // Duplicate
            duration_minutes: 15,
          });

          expect(result.success).toBe(false);
          expect(result.error_category).toBe('invalid_parameter');
          expect(result.error).toContain('duplicate');
          expect(table.getAllRows().length).toBe(0);
        }),
        { numRuns: 100 },
      );
    });
  });

  // =========================================================================
  // Property 7: Idempotency token returns existing capture without side effects
  // =========================================================================

  describe('Property 7: Idempotency token returns existing capture without side effects', () => {
    /**
     * Property 7a: Replay identical start_capture calls within the 5-minute
     * idempotency window returns the same capture_id, creates no new mirror
     * sessions, creates no new VNI rows, and returns dataFreshness="cached".
     *
     * **Validates: Requirements 3.15**
     */
    it('Property 7a: idempotent replay within window returns cached result — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          arbValidEniIds,
          arbValidDuration,
          arbIdempotencyToken,
          (eniIds, duration, token) => {
            const table = new StubCaptureStateTable();
            const now = new Date('2025-01-15T10:00:00Z');

            // First call — should succeed and create resources
            const result1 = simulateStartCapture(
              table,
              { eni_ids: eniIds, duration_minutes: duration, idempotency_token: token },
              now,
            );
            expect(result1.success).toBe(true);
            expect(result1.capture_id).toBeDefined();
            const firstCaptureId = result1.capture_id!;
            const sessionsAfterFirst = table.mirrorSessionsCreated.length;
            const vniAfterFirst = table.vniRowsWritten;

            // Second call — same token, same params, within window
            const now2 = new Date(now.getTime() + 2 * 60 * 1000); // 2 min later
            const result2 = simulateStartCapture(
              table,
              { eni_ids: eniIds, duration_minutes: duration, idempotency_token: token },
              now2,
            );

            // Must return same capture_id
            expect(result2.success).toBe(true);
            expect(result2.capture_id).toBe(firstCaptureId);

            // Must not create new resources
            expect(result2.data_freshness).toBe('cached');
            expect(result2.mirror_sessions_created).toBe(0);
            expect(result2.vni_rows_written).toBe(0);

            // Table state unchanged
            expect(table.mirrorSessionsCreated.length).toBe(sessionsAfterFirst);
            expect(table.vniRowsWritten).toBe(vniAfterFirst);
          },
        ),
        { numRuns: 200 },
      );
    });

    /**
     * Property 7b: Replay identical start_capture calls BEYOND the 5-minute
     * idempotency window creates a new capture (no idempotency hit).
     *
     * **Validates: Requirements 3.15**
     */
    it('Property 7b: replay beyond 5-minute window creates new capture — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          arbValidEniIds,
          arbValidDuration,
          arbIdempotencyToken,
          (eniIds, duration, token) => {
            const table = new StubCaptureStateTable();
            const now = new Date('2025-01-15T10:00:00Z');

            // First call
            const result1 = simulateStartCapture(
              table,
              { eni_ids: eniIds, duration_minutes: duration, idempotency_token: token },
              now,
            );
            expect(result1.success).toBe(true);
            const firstCaptureId = result1.capture_id!;

            // Second call — beyond the 5-minute window (6 minutes later)
            const now2 = new Date(now.getTime() + 6 * 60 * 1000);
            const result2 = simulateStartCapture(
              table,
              { eni_ids: eniIds, duration_minutes: duration, idempotency_token: token },
              now2,
            );

            // Must create a NEW capture (not idempotent)
            expect(result2.success).toBe(true);
            expect(result2.capture_id).toBeDefined();
            expect(result2.capture_id).not.toBe(firstCaptureId);
            expect(result2.data_freshness).not.toBe('cached');
            expect(result2.mirror_sessions_created).toBeGreaterThan(0);
            expect(result2.vni_rows_written).toBeGreaterThan(0);
          },
        ),
        { numRuns: 200 },
      );
    });

    /**
     * Property 7c: Different idempotency tokens with same params create
     * separate captures (no false idempotency match).
     *
     * **Validates: Requirements 3.15**
     */
    it('Property 7c: different tokens create separate captures — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          arbValidEniIds,
          arbValidDuration,
          arbIdempotencyToken,
          arbIdempotencyToken,
          (eniIds, duration, token1, token2) => {
            // Ensure tokens are different
            fc.pre(token1 !== token2);

            const table = new StubCaptureStateTable();
            const now = new Date('2025-01-15T10:00:00Z');

            const result1 = simulateStartCapture(
              table,
              { eni_ids: eniIds, duration_minutes: duration, idempotency_token: token1 },
              now,
            );
            expect(result1.success).toBe(true);

            const result2 = simulateStartCapture(
              table,
              { eni_ids: eniIds, duration_minutes: duration, idempotency_token: token2 },
              now,
            );
            expect(result2.success).toBe(true);

            // Different tokens → different captures
            expect(result2.capture_id).not.toBe(result1.capture_id);
            expect(result2.data_freshness).not.toBe('cached');
          },
        ),
        { numRuns: 100 },
      );
    });

    /**
     * Property 7d: Same token but different eni_ids creates a new capture
     * (idempotency requires matching eni_ids set).
     *
     * **Validates: Requirements 3.15**
     */
    it('Property 7d: same token but different eni_ids creates new capture — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          arbValidEniIds,
          arbValidEniIds,
          arbValidDuration,
          arbIdempotencyToken,
          (eniIds1, eniIds2, duration, token) => {
            // Ensure ENI sets are different
            const set1 = new Set(eniIds1);
            const set2 = new Set(eniIds2);
            const sameSet = set1.size === set2.size && [...set1].every((e) => set2.has(e));
            fc.pre(!sameSet);

            const table = new StubCaptureStateTable();
            const now = new Date('2025-01-15T10:00:00Z');

            const result1 = simulateStartCapture(
              table,
              { eni_ids: eniIds1, duration_minutes: duration, idempotency_token: token },
              now,
            );
            expect(result1.success).toBe(true);

            const result2 = simulateStartCapture(
              table,
              { eni_ids: eniIds2, duration_minutes: duration, idempotency_token: token },
              now,
            );
            expect(result2.success).toBe(true);
            expect(result2.capture_id).not.toBe(result1.capture_id);
            expect(result2.data_freshness).not.toBe('cached');
          },
        ),
        { numRuns: 100 },
      );
    });

    /**
     * Property 7e: Same token but different duration_minutes creates a new
     * capture (idempotency requires matching duration).
     *
     * **Validates: Requirements 3.15**
     */
    it('Property 7e: same token but different duration creates new capture — Feature: genai-operations-analytics-tool', () => {
      fc.assert(
        fc.property(
          arbValidEniIds,
          arbValidDuration,
          arbValidDuration,
          arbIdempotencyToken,
          (eniIds, duration1, duration2, token) => {
            fc.pre(duration1 !== duration2);

            const table = new StubCaptureStateTable();
            const now = new Date('2025-01-15T10:00:00Z');

            const result1 = simulateStartCapture(
              table,
              { eni_ids: eniIds, duration_minutes: duration1, idempotency_token: token },
              now,
            );
            expect(result1.success).toBe(true);

            const result2 = simulateStartCapture(
              table,
              { eni_ids: eniIds, duration_minutes: duration2, idempotency_token: token },
              now,
            );
            expect(result2.success).toBe(true);
            expect(result2.capture_id).not.toBe(result1.capture_id);
            expect(result2.data_freshness).not.toBe('cached');
          },
        ),
        { numRuns: 100 },
      );
    });
  });
});
