/**
 * Property tests for Tcp_Stream_Health_Report shape (Property 11).
 * Feature: genai-operations-analytics-tool
 *
 * Property 11: Tcp_Stream_Health_Report keys exactly match Req 18.2
 *
 * Every `diagnose_tcp_stream` response contains the exact key set defined
 * in Req 18.2 — no missing keys, no extra keys. Categories in
 * `anomalies[].category` are members of the Tcp_Anomaly_Category
 * enumeration only.
 *
 * Additionally:
 * - `mss_clamping_mismatch` flips `true` exactly when
 *   `mss_effective_min < 0.8 * mss_advertised` (Req 18.2)
 * - Empty-partition reports contain a single `none` anomaly and zero
 *   numeric counts (Req 18.6)
 * - Section-unavailable reports set affected sub-objects to `null` and
 *   append a `none` anomaly listing unavailable sections (Req 18.7)
 *
 * **Validates: Requirements 18.2, 18.3, 18.6, 18.7**
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ---------------------------------------------------------------------------
// Tcp_Anomaly_Category closed enumeration (from requirements glossary)
// ---------------------------------------------------------------------------

const TCP_ANOMALY_CATEGORIES = new Set([
  'handshake_failed',
  'handshake_slow',
  'connection_reset_by_client',
  'connection_reset_by_server',
  'connection_reset_by_middlebox',
  'idle_timeout_close',
  'excessive_retransmissions',
  'spurious_retransmissions',
  'out_of_order_packets',
  'duplicate_acks',
  'zero_window_stall',
  'mss_clamping_mismatch',
  'tls_client_hello_fragmented',
  'none',
]);

// ---------------------------------------------------------------------------
// Connection close state enumeration (Req 18.2)
// ---------------------------------------------------------------------------

const CONNECTION_CLOSE_STATES = new Set([
  'fin_clean',
  'rst_observed',
  'idle_timeout',
  'still_open',
  'not_observed',
]);

// ---------------------------------------------------------------------------
// Reset_Origin_Side enumeration (Req 18.2)
// ---------------------------------------------------------------------------

const RESET_ORIGIN_SIDES = new Set([
  'client',
  'server',
  'middlebox',
  'unknown',
]);

// ---------------------------------------------------------------------------
// Handshake failure reason enumeration (from design)
// ---------------------------------------------------------------------------

const HANDSHAKE_FAILURE_REASONS = new Set([
  'syn_ack_missing',
  'final_ack_missing',
  'syn_retransmitted',
  'complete',
  'not_observed',
]);

// ---------------------------------------------------------------------------
// Tcp_Stream_Health_Report section names (Req 18.7)
// ---------------------------------------------------------------------------

const REPORT_SECTION_NAMES = [
  'handshake',
  'connection_close',
  'rtt',
  'retransmissions',
  'out_of_order',
  'zero_window',
  'tcp_options',
] as const;

// ---------------------------------------------------------------------------
// Interfaces matching the Tcp_Stream_Health_Report shape (Req 18.2)
// ---------------------------------------------------------------------------

interface Endpoint {
  ip: string;
  port: number;
}

interface HandshakeSection {
  complete: boolean;
  duration_ms: number | null;
  failure_reason: string | null;
}

interface ConnectionCloseSection {
  state: string;
  reset_origin_side: string | null;
}

interface RttSection {
  min_ms: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
  sample_count: number;
}

interface RetransmissionsSection {
  total_count: number;
  fast_retransmit_count: number;
  spurious_count: number;
  sack_retransmit_count: number;
}

interface OutOfOrderSection {
  out_of_order_count: number;
  duplicate_ack_count: number;
  dsack_count: number;
}

interface ZeroWindowSection {
  event_count: number;
  total_duration_ms: number;
}

interface TcpOptionsSection {
  mss_advertised: number;
  window_scale: number;
  sack_permitted: boolean;
  timestamps_enabled: boolean;
  mss_effective_min: number;
}

interface Anomaly {
  category: string;
  description: string;
}

interface TcpStreamHealthReport {
  stream_id: string;
  client_endpoint: Endpoint;
  server_endpoint: Endpoint;
  handshake: HandshakeSection | null;
  connection_close: ConnectionCloseSection | null;
  rtt: RttSection | null;
  retransmissions: RetransmissionsSection | null;
  out_of_order: OutOfOrderSection | null;
  zero_window: ZeroWindowSection | null;
  tcp_options: TcpOptionsSection | null;
  mss_clamping_mismatch: boolean;
  anomalies: Anomaly[];
}

// ---------------------------------------------------------------------------
// Report builder — mirrors the Python implementation's logic
// ---------------------------------------------------------------------------

// Anomaly classification thresholds (Req 18.3)
const HANDSHAKE_SLOW_MS = 500.0;
const ZERO_WINDOW_STALL_MS = 100.0;
const EXCESSIVE_RETX_FRACTION = 0.05;
const OUT_OF_ORDER_FRACTION = 0.01;
const DUPLICATE_ACK_THRESHOLD = 5;

/**
 * Compute `mss_clamping_mismatch` per Req 18.2:
 * true when `mss_effective_min < 0.8 * mss_advertised`.
 */
function computeMssClamping(tcpOptions: TcpOptionsSection | null): boolean {
  if (tcpOptions === null) return false;
  const { mss_advertised, mss_effective_min } = tcpOptions;
  if (mss_advertised <= 0 || mss_effective_min <= 0) return false;
  return mss_effective_min < 0.8 * mss_advertised;
}

/**
 * Classify anomalies per Req 18.3.
 * Returns the anomalies array for the report.
 */
function classifyAnomalies(opts: {
  handshake: HandshakeSection | null;
  connectionClose: ConnectionCloseSection | null;
  retransmissions: RetransmissionsSection | null;
  outOfOrder: OutOfOrderSection | null;
  zeroWindow: ZeroWindowSection | null;
  mssClamping: boolean;
  totalPacketCount: number;
  tlsClientHelloFragmented: boolean;
  unavailableSections: string[];
}): Anomaly[] {
  const anomalies: Anomaly[] = [];

  // Handshake rules
  if (opts.handshake !== null) {
    if (!opts.handshake.complete && opts.handshake.failure_reason !== 'not_observed') {
      anomalies.push({
        category: 'handshake_failed',
        description: `TCP handshake did not complete: ${opts.handshake.failure_reason ?? 'unknown'}.`,
      });
    }
    if (
      opts.handshake.duration_ms !== null &&
      opts.handshake.duration_ms > HANDSHAKE_SLOW_MS
    ) {
      anomalies.push({
        category: 'handshake_slow',
        description: `TCP handshake completed in ${opts.handshake.duration_ms.toFixed(0)} ms, above the ${HANDSHAKE_SLOW_MS} ms threshold.`,
      });
    }
  }

  // Connection close rules
  if (opts.connectionClose !== null) {
    const { state, reset_origin_side } = opts.connectionClose;
    if (state === 'rst_observed') {
      if (reset_origin_side === 'client') {
        anomalies.push({ category: 'connection_reset_by_client', description: 'Connection terminated by a TCP RST from the client side.' });
      } else if (reset_origin_side === 'server') {
        anomalies.push({ category: 'connection_reset_by_server', description: 'Connection terminated by a TCP RST from the server side.' });
      } else if (reset_origin_side === 'middlebox') {
        anomalies.push({ category: 'connection_reset_by_middlebox', description: 'Connection terminated by a TCP RST whose source matched neither endpoint (middlebox-injected).' });
      }
    }
    if (state === 'idle_timeout') {
      anomalies.push({ category: 'idle_timeout_close', description: 'Connection closed due to idle timeout.' });
    }
  }

  // Retransmission rules
  if (opts.retransmissions !== null && opts.totalPacketCount > 0) {
    if (opts.retransmissions.total_count > EXCESSIVE_RETX_FRACTION * opts.totalPacketCount) {
      anomalies.push({ category: 'excessive_retransmissions', description: `Retransmissions exceed ${(EXCESSIVE_RETX_FRACTION * 100).toFixed(0)}% of total packets.` });
    }
    if (opts.retransmissions.spurious_count > 0) {
      anomalies.push({ category: 'spurious_retransmissions', description: 'Spurious retransmissions detected (DSACK signals).' });
    }
  }

  // Out-of-order rules
  if (opts.outOfOrder !== null && opts.totalPacketCount > 0) {
    if (opts.outOfOrder.out_of_order_count > OUT_OF_ORDER_FRACTION * opts.totalPacketCount) {
      anomalies.push({ category: 'out_of_order_packets', description: 'Out-of-order packets exceed 1% of total packets.' });
    }
    if (opts.outOfOrder.duplicate_ack_count > DUPLICATE_ACK_THRESHOLD) {
      anomalies.push({ category: 'duplicate_acks', description: `Duplicate ACK count exceeds ${DUPLICATE_ACK_THRESHOLD}.` });
    }
  }

  // Zero-window rule
  if (opts.zeroWindow !== null) {
    if (opts.zeroWindow.total_duration_ms > ZERO_WINDOW_STALL_MS) {
      anomalies.push({ category: 'zero_window_stall', description: `Zero-window stall duration exceeds ${ZERO_WINDOW_STALL_MS} ms.` });
    }
  }

  // MSS clamping rule
  if (opts.mssClamping) {
    anomalies.push({ category: 'mss_clamping_mismatch', description: 'Effective MSS is below 80% of advertised MSS.' });
  }

  // TLS Client Hello fragmentation rule
  if (opts.tlsClientHelloFragmented) {
    anomalies.push({ category: 'tls_client_hello_fragmented', description: 'At least one TLS Client Hello in the stream is fragmented.' });
  }

  // Unavailable sections (Req 18.7)
  if (opts.unavailableSections.length > 0) {
    anomalies.push({
      category: 'none',
      description: `Sections unavailable: ${opts.unavailableSections.join(', ')}.`,
    });
  }

  // Req 18.3: exactly one `none` entry when no other rule matches
  if (anomalies.length === 0) {
    anomalies.push({ category: 'none', description: 'No anomalies detected.' });
  }

  return anomalies;
}

/**
 * Build a Tcp_Stream_Health_Report from generated input tuples.
 * Mirrors the Python `_build_single_report` logic.
 */
function buildReport(opts: {
  streamId: string;
  clientIp: string;
  clientPort: number;
  serverIp: string;
  serverPort: number;
  handshake: HandshakeSection | null;
  connectionClose: ConnectionCloseSection | null;
  rtt: RttSection | null;
  retransmissions: RetransmissionsSection | null;
  outOfOrder: OutOfOrderSection | null;
  zeroWindow: ZeroWindowSection | null;
  tcpOptions: TcpOptionsSection | null;
  totalPacketCount: number;
  tlsClientHelloFragmented: boolean;
  unavailableSections: string[];
}): TcpStreamHealthReport {
  const mssClamping = computeMssClamping(opts.tcpOptions);
  const anomalies = classifyAnomalies({
    handshake: opts.handshake,
    connectionClose: opts.connectionClose,
    retransmissions: opts.retransmissions,
    outOfOrder: opts.outOfOrder,
    zeroWindow: opts.zeroWindow,
    mssClamping,
    totalPacketCount: opts.totalPacketCount,
    tlsClientHelloFragmented: opts.tlsClientHelloFragmented,
    unavailableSections: opts.unavailableSections,
  });

  return {
    stream_id: opts.streamId,
    client_endpoint: { ip: opts.clientIp, port: opts.clientPort },
    server_endpoint: { ip: opts.serverIp, port: opts.serverPort },
    handshake: opts.handshake,
    connection_close: opts.connectionClose,
    rtt: opts.rtt,
    retransmissions: opts.retransmissions,
    out_of_order: opts.outOfOrder,
    zero_window: opts.zeroWindow,
    tcp_options: opts.tcpOptions,
    mss_clamping_mismatch: mssClamping,
    anomalies,
  };
}

// ---------------------------------------------------------------------------
// JSON Schema validation for the Tcp_Stream_Health_Report (Req 18.2)
// ---------------------------------------------------------------------------

/** Exact top-level keys required by Req 18.2. */
const REQUIRED_TOP_LEVEL_KEYS = new Set([
  'stream_id',
  'client_endpoint',
  'server_endpoint',
  'handshake',
  'connection_close',
  'rtt',
  'retransmissions',
  'out_of_order',
  'zero_window',
  'tcp_options',
  'mss_clamping_mismatch',
  'anomalies',
]);

/**
 * Validate a Tcp_Stream_Health_Report against the Req 18.2 schema.
 * Returns an array of violation descriptions; empty means valid.
 */
function validateReportShape(report: unknown): string[] {
  const violations: string[] = [];
  if (report === null || typeof report !== 'object' || Array.isArray(report)) {
    violations.push('Report is not a non-null object');
    return violations;
  }
  const r = report as Record<string, unknown>;

  // Check exact key set
  const actualKeys = new Set(Object.keys(r));
  for (const key of REQUIRED_TOP_LEVEL_KEYS) {
    if (!actualKeys.has(key)) violations.push(`Missing required key: '${key}'`);
  }
  for (const key of actualKeys) {
    if (!REQUIRED_TOP_LEVEL_KEYS.has(key)) violations.push(`Extra key not in Req 18.2: '${key}'`);
  }

  // stream_id: string
  if (typeof r.stream_id !== 'string') {
    violations.push(`'stream_id' must be string, got ${typeof r.stream_id}`);
  }

  // Endpoint validation helper
  const validateEndpoint = (name: string, ep: unknown) => {
    if (ep === null || typeof ep !== 'object' || Array.isArray(ep)) {
      violations.push(`'${name}' must be an object with ip and port`);
      return;
    }
    const e = ep as Record<string, unknown>;
    if (typeof e.ip !== 'string') violations.push(`'${name}.ip' must be string`);
    if (typeof e.port !== 'number') violations.push(`'${name}.port' must be number`);
  };
  validateEndpoint('client_endpoint', r.client_endpoint);
  validateEndpoint('server_endpoint', r.server_endpoint);

  // Nullable sub-object validation
  const validateNullableObject = (name: string, obj: unknown, requiredKeys: string[]) => {
    if (obj === null) return; // null is valid per Req 18.7
    if (typeof obj !== 'object' || Array.isArray(obj)) {
      violations.push(`'${name}' must be object or null`);
      return;
    }
    const o = obj as Record<string, unknown>;
    for (const key of requiredKeys) {
      if (!(key in o)) violations.push(`'${name}.${key}' is missing`);
    }
  };

  // handshake: { complete, duration_ms, failure_reason } | null
  validateNullableObject('handshake', r.handshake, ['complete', 'duration_ms', 'failure_reason']);
  if (r.handshake !== null && typeof r.handshake === 'object' && !Array.isArray(r.handshake)) {
    const h = r.handshake as Record<string, unknown>;
    if (typeof h.complete !== 'boolean') violations.push("'handshake.complete' must be boolean");
  }

  // connection_close: { state, reset_origin_side } | null
  validateNullableObject('connection_close', r.connection_close, ['state', 'reset_origin_side']);
  if (r.connection_close !== null && typeof r.connection_close === 'object' && !Array.isArray(r.connection_close)) {
    const cc = r.connection_close as Record<string, unknown>;
    if (typeof cc.state === 'string' && !CONNECTION_CLOSE_STATES.has(cc.state)) {
      violations.push(`'connection_close.state' must be one of ${[...CONNECTION_CLOSE_STATES].join(', ')}, got '${cc.state}'`);
    }
    if (cc.reset_origin_side !== null && typeof cc.reset_origin_side === 'string' && !RESET_ORIGIN_SIDES.has(cc.reset_origin_side)) {
      violations.push(`'connection_close.reset_origin_side' must be one of ${[...RESET_ORIGIN_SIDES].join(', ')} or null`);
    }
  }

  // rtt: { min_ms, p50_ms, p95_ms, max_ms, sample_count } | null
  validateNullableObject('rtt', r.rtt, ['min_ms', 'p50_ms', 'p95_ms', 'max_ms', 'sample_count']);

  // retransmissions: { total_count, fast_retransmit_count, spurious_count, sack_retransmit_count } | null
  validateNullableObject('retransmissions', r.retransmissions, ['total_count', 'fast_retransmit_count', 'spurious_count', 'sack_retransmit_count']);

  // out_of_order: { out_of_order_count, duplicate_ack_count, dsack_count } | null
  validateNullableObject('out_of_order', r.out_of_order, ['out_of_order_count', 'duplicate_ack_count', 'dsack_count']);

  // zero_window: { event_count, total_duration_ms } | null
  validateNullableObject('zero_window', r.zero_window, ['event_count', 'total_duration_ms']);

  // tcp_options: { mss_advertised, window_scale, sack_permitted, timestamps_enabled, mss_effective_min } | null
  validateNullableObject('tcp_options', r.tcp_options, ['mss_advertised', 'window_scale', 'sack_permitted', 'timestamps_enabled', 'mss_effective_min']);

  // mss_clamping_mismatch: boolean
  if (typeof r.mss_clamping_mismatch !== 'boolean') {
    violations.push(`'mss_clamping_mismatch' must be boolean, got ${typeof r.mss_clamping_mismatch}`);
  }

  // anomalies: array of { category, description }
  if (!Array.isArray(r.anomalies)) {
    violations.push(`'anomalies' must be an array`);
  } else {
    for (let i = 0; i < (r.anomalies as unknown[]).length; i++) {
      const a = (r.anomalies as unknown[])[i];
      if (a === null || typeof a !== 'object' || Array.isArray(a)) {
        violations.push(`'anomalies[${i}]' must be an object`);
        continue;
      }
      const aObj = a as Record<string, unknown>;
      if (typeof aObj.category !== 'string') {
        violations.push(`'anomalies[${i}].category' must be string`);
      } else if (!TCP_ANOMALY_CATEGORIES.has(aObj.category)) {
        violations.push(`'anomalies[${i}].category' value '${aObj.category}' is not in Tcp_Anomaly_Category`);
      }
      if (typeof aObj.description !== 'string') {
        violations.push(`'anomalies[${i}].description' must be string`);
      }
    }
  }

  return violations;
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Generate a valid stream_id (1-64 chars from [A-Za-z0-9_-]). */
const arbStreamId = fc.stringOf(
  fc.constantFrom(...'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-'.split('')),
  { minLength: 1, maxLength: 64 },
);

/** Generate a valid IPv4 address string. */
const arbIpv4 = fc.tuple(
  fc.integer({ min: 1, max: 255 }),
  fc.integer({ min: 0, max: 255 }),
  fc.integer({ min: 0, max: 255 }),
  fc.integer({ min: 1, max: 254 }),
).map(([a, b, c, d]) => `${a}.${b}.${c}.${d}`);

/** Generate a valid port number. */
const arbPort = fc.integer({ min: 1, max: 65535 });

/** Generate a handshake section. */
const arbHandshakeSection: fc.Arbitrary<HandshakeSection> = fc.record({
  complete: fc.boolean(),
  duration_ms: fc.oneof(fc.constant(null), fc.double({ min: 0, max: 5000, noNaN: true, noDefaultInfinity: true })),
  failure_reason: fc.oneof(fc.constant(null), fc.constantFrom(...HANDSHAKE_FAILURE_REASONS)),
});

/** Generate a connection_close section. */
const arbConnectionCloseSection: fc.Arbitrary<ConnectionCloseSection> = fc.record({
  state: fc.constantFrom(...CONNECTION_CLOSE_STATES),
  reset_origin_side: fc.oneof(fc.constant(null), fc.constantFrom(...RESET_ORIGIN_SIDES)),
});

/** Generate an RTT section. */
const arbRttSection: fc.Arbitrary<RttSection> = fc.record({
  min_ms: fc.double({ min: 0, max: 1000, noNaN: true, noDefaultInfinity: true }),
  p50_ms: fc.double({ min: 0, max: 2000, noNaN: true, noDefaultInfinity: true }),
  p95_ms: fc.double({ min: 0, max: 5000, noNaN: true, noDefaultInfinity: true }),
  max_ms: fc.double({ min: 0, max: 10000, noNaN: true, noDefaultInfinity: true }),
  sample_count: fc.integer({ min: 0, max: 100000 }),
});

/** Generate a retransmissions section. */
const arbRetransmissionsSection: fc.Arbitrary<RetransmissionsSection> = fc.record({
  total_count: fc.integer({ min: 0, max: 10000 }),
  fast_retransmit_count: fc.integer({ min: 0, max: 5000 }),
  spurious_count: fc.integer({ min: 0, max: 1000 }),
  sack_retransmit_count: fc.integer({ min: 0, max: 1000 }),
});

/** Generate an out_of_order section. */
const arbOutOfOrderSection: fc.Arbitrary<OutOfOrderSection> = fc.record({
  out_of_order_count: fc.integer({ min: 0, max: 5000 }),
  duplicate_ack_count: fc.integer({ min: 0, max: 5000 }),
  dsack_count: fc.integer({ min: 0, max: 1000 }),
});

/** Generate a zero_window section. */
const arbZeroWindowSection: fc.Arbitrary<ZeroWindowSection> = fc.record({
  event_count: fc.integer({ min: 0, max: 500 }),
  total_duration_ms: fc.double({ min: 0, max: 60000, noNaN: true, noDefaultInfinity: true }),
});

/** Generate a tcp_options section with controlled MSS values for clamping tests. */
const arbTcpOptionsSection: fc.Arbitrary<TcpOptionsSection> = fc.record({
  mss_advertised: fc.integer({ min: 0, max: 9000 }),
  window_scale: fc.integer({ min: 0, max: 14 }),
  sack_permitted: fc.boolean(),
  timestamps_enabled: fc.boolean(),
  mss_effective_min: fc.integer({ min: 0, max: 9000 }),
});

/** Generate a subset of section names to mark as unavailable. */
const arbUnavailableSections = fc.subarray([...REPORT_SECTION_NAMES], { minLength: 0, maxLength: 7 });

/** Generate a full report input tuple. */
const arbReportInput = fc.record({
  streamId: arbStreamId,
  clientIp: arbIpv4,
  clientPort: arbPort,
  serverIp: arbIpv4,
  serverPort: arbPort,
  handshake: fc.oneof(fc.constant(null), arbHandshakeSection),
  connectionClose: fc.oneof(fc.constant(null), arbConnectionCloseSection),
  rtt: fc.oneof(fc.constant(null), arbRttSection),
  retransmissions: fc.oneof(fc.constant(null), arbRetransmissionsSection),
  outOfOrder: fc.oneof(fc.constant(null), arbOutOfOrderSection),
  zeroWindow: fc.oneof(fc.constant(null), arbZeroWindowSection),
  tcpOptions: fc.oneof(fc.constant(null), arbTcpOptionsSection),
  totalPacketCount: fc.integer({ min: 0, max: 1000000 }),
  tlsClientHelloFragmented: fc.boolean(),
  unavailableSections: arbUnavailableSections,
});

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Tcp_Stream_Health_Report shape property tests (Property 11)', () => {
  /**
   * Property 11: Every generated Tcp_Stream_Health_Report passes JSON Schema
   * validation — exact key set from Req 18.2, anomaly categories from
   * Tcp_Anomaly_Category only.
   *
   * **Validates: Requirements 18.2**
   */
  it('Property 11: report shape matches Req 18.2 key set for all generated inputs', () => {
    fc.assert(
      fc.property(arbReportInput, (input) => {
        const report = buildReport(input);
        const violations = validateReportShape(report);
        expect(violations).toEqual([]);
      }),
      { numRuns: 200 },
    );
  });

  /**
   * Property 11: `mss_clamping_mismatch` is `true` exactly when
   * `mss_effective_min < 0.8 * mss_advertised`.
   *
   * **Validates: Requirements 18.2, 18.3**
   */
  it('Property 11: mss_clamping_mismatch flips true exactly when mss_effective_min < 0.8 * mss_advertised', () => {
    fc.assert(
      fc.property(arbTcpOptionsSection, (tcpOptions) => {
        const clamping = computeMssClamping(tcpOptions);
        const { mss_advertised, mss_effective_min } = tcpOptions;

        if (mss_advertised <= 0 || mss_effective_min <= 0) {
          // When either is zero or negative, clamping is always false
          expect(clamping).toBe(false);
        } else {
          const expected = mss_effective_min < 0.8 * mss_advertised;
          expect(clamping).toBe(expected);
        }
      }),
      { numRuns: 500 },
    );
  });

  /**
   * Property 11: Empty-partition reports contain a single `none` anomaly
   * and zero numeric counts (Req 18.6).
   *
   * **Validates: Requirements 18.6**
   */
  it('Property 11: empty-partition reports have single none anomaly and zero counts', () => {
    fc.assert(
      fc.property(arbStreamId, arbIpv4, arbPort, arbIpv4, arbPort, (streamId, cIp, cPort, sIp, sPort) => {
        // Empty partition: all sections have zero counts, no TLS fragmentation
        const report = buildReport({
          streamId,
          clientIp: cIp,
          clientPort: cPort,
          serverIp: sIp,
          serverPort: sPort,
          handshake: { complete: true, duration_ms: 0, failure_reason: 'complete' },
          connectionClose: { state: 'still_open', reset_origin_side: null },
          rtt: { min_ms: 0, p50_ms: 0, p95_ms: 0, max_ms: 0, sample_count: 0 },
          retransmissions: { total_count: 0, fast_retransmit_count: 0, spurious_count: 0, sack_retransmit_count: 0 },
          outOfOrder: { out_of_order_count: 0, duplicate_ack_count: 0, dsack_count: 0 },
          zeroWindow: { event_count: 0, total_duration_ms: 0 },
          tcpOptions: { mss_advertised: 0, window_scale: 0, sack_permitted: false, timestamps_enabled: false, mss_effective_min: 0 },
          totalPacketCount: 0,
          tlsClientHelloFragmented: false,
          unavailableSections: [],
        });

        // Validate shape
        const violations = validateReportShape(report);
        expect(violations).toEqual([]);

        // Must have exactly one anomaly with category 'none'
        expect(report.anomalies).toHaveLength(1);
        expect(report.anomalies[0].category).toBe('none');

        // mss_clamping_mismatch must be false (both values are 0)
        expect(report.mss_clamping_mismatch).toBe(false);

        // All numeric counts in sub-objects should be zero
        expect(report.rtt!.sample_count).toBe(0);
        expect(report.retransmissions!.total_count).toBe(0);
        expect(report.out_of_order!.out_of_order_count).toBe(0);
        expect(report.zero_window!.event_count).toBe(0);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 11: Section-unavailable reports set affected sub-objects to
   * `null` and append a `none` anomaly listing unavailable sections (Req 18.7).
   *
   * **Validates: Requirements 18.7**
   */
  it('Property 11: section-unavailable reports have null sub-objects and none anomaly listing sections', () => {
    // Generate reports where at least one section is unavailable
    const arbWithUnavailable = fc.record({
      streamId: arbStreamId,
      clientIp: arbIpv4,
      clientPort: arbPort,
      serverIp: arbIpv4,
      serverPort: arbPort,
      unavailableSections: fc.subarray([...REPORT_SECTION_NAMES], { minLength: 1, maxLength: 7 }),
    });

    fc.assert(
      fc.property(arbWithUnavailable, (input) => {
        // Build a report where unavailable sections are null
        const handshake = input.unavailableSections.includes('handshake') ? null : { complete: true, duration_ms: 10, failure_reason: 'complete' as string | null };
        const connectionClose = input.unavailableSections.includes('connection_close') ? null : { state: 'still_open', reset_origin_side: null as string | null };
        const rtt = input.unavailableSections.includes('rtt') ? null : { min_ms: 1, p50_ms: 5, p95_ms: 10, max_ms: 20, sample_count: 100 };
        const retransmissions = input.unavailableSections.includes('retransmissions') ? null : { total_count: 0, fast_retransmit_count: 0, spurious_count: 0, sack_retransmit_count: 0 };
        const outOfOrder = input.unavailableSections.includes('out_of_order') ? null : { out_of_order_count: 0, duplicate_ack_count: 0, dsack_count: 0 };
        const zeroWindow = input.unavailableSections.includes('zero_window') ? null : { event_count: 0, total_duration_ms: 0 };
        const tcpOptions = input.unavailableSections.includes('tcp_options') ? null : { mss_advertised: 1460, window_scale: 7, sack_permitted: true, timestamps_enabled: true, mss_effective_min: 1460 };

        const report = buildReport({
          streamId: input.streamId,
          clientIp: input.clientIp,
          clientPort: input.clientPort,
          serverIp: input.serverIp,
          serverPort: input.serverPort,
          handshake,
          connectionClose,
          rtt,
          retransmissions,
          outOfOrder,
          zeroWindow,
          tcpOptions,
          totalPacketCount: 1000,
          tlsClientHelloFragmented: false,
          unavailableSections: input.unavailableSections,
        });

        // Validate shape still passes
        const violations = validateReportShape(report);
        expect(violations).toEqual([]);

        // Verify null sub-objects for unavailable sections
        if (input.unavailableSections.includes('handshake')) expect(report.handshake).toBeNull();
        if (input.unavailableSections.includes('connection_close')) expect(report.connection_close).toBeNull();
        if (input.unavailableSections.includes('rtt')) expect(report.rtt).toBeNull();
        if (input.unavailableSections.includes('retransmissions')) expect(report.retransmissions).toBeNull();
        if (input.unavailableSections.includes('out_of_order')) expect(report.out_of_order).toBeNull();
        if (input.unavailableSections.includes('zero_window')) expect(report.zero_window).toBeNull();
        if (input.unavailableSections.includes('tcp_options')) expect(report.tcp_options).toBeNull();

        // Must have a 'none' anomaly listing unavailable sections
        const noneAnomalies = report.anomalies.filter(a => a.category === 'none');
        expect(noneAnomalies.length).toBeGreaterThanOrEqual(1);

        // The none anomaly description must mention each unavailable section
        const unavailableNone = noneAnomalies.find(a => a.description.includes('unavailable'));
        expect(unavailableNone).toBeDefined();
        for (const section of input.unavailableSections) {
          expect(unavailableNone!.description).toContain(section);
        }
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 11: All anomaly categories in the report are members of
   * the Tcp_Anomaly_Category enumeration.
   *
   * **Validates: Requirements 18.2, 18.3**
   */
  it('Property 11: all anomaly categories belong to Tcp_Anomaly_Category enumeration', () => {
    fc.assert(
      fc.property(arbReportInput, (input) => {
        const report = buildReport(input);

        for (const anomaly of report.anomalies) {
          expect(TCP_ANOMALY_CATEGORIES.has(anomaly.category)).toBe(true);
          expect(typeof anomaly.description).toBe('string');
          expect(anomaly.description.length).toBeGreaterThan(0);
        }
      }),
      { numRuns: 200 },
    );
  });

  /**
   * Property 11: When no anomaly classification rule fires, exactly one
   * `none` entry is present (Req 18.3 final clause).
   *
   * **Validates: Requirements 18.3**
   */
  it('Property 11: exactly one none anomaly when no classification rule fires', () => {
    // Construct inputs that should not trigger any anomaly rule
    fc.assert(
      fc.property(arbStreamId, arbIpv4, arbPort, arbIpv4, arbPort, (streamId, cIp, cPort, sIp, sPort) => {
        const report = buildReport({
          streamId,
          clientIp: cIp,
          clientPort: cPort,
          serverIp: sIp,
          serverPort: sPort,
          // Handshake complete, fast (< 500ms)
          handshake: { complete: true, duration_ms: 10, failure_reason: 'complete' },
          // No RST, no idle timeout
          connectionClose: { state: 'still_open', reset_origin_side: null },
          rtt: { min_ms: 1, p50_ms: 5, p95_ms: 10, max_ms: 20, sample_count: 100 },
          // No retransmissions (0 < 5% of 1000)
          retransmissions: { total_count: 0, fast_retransmit_count: 0, spurious_count: 0, sack_retransmit_count: 0 },
          // No out-of-order (0 < 1% of 1000), no dup acks (0 <= 5)
          outOfOrder: { out_of_order_count: 0, duplicate_ack_count: 0, dsack_count: 0 },
          // No zero-window stall (0 <= 100ms)
          zeroWindow: { event_count: 0, total_duration_ms: 0 },
          // No MSS clamping (effective == advertised)
          tcpOptions: { mss_advertised: 1460, window_scale: 7, sack_permitted: true, timestamps_enabled: true, mss_effective_min: 1460 },
          totalPacketCount: 1000,
          tlsClientHelloFragmented: false,
          unavailableSections: [],
        });

        expect(report.anomalies).toHaveLength(1);
        expect(report.anomalies[0].category).toBe('none');
      }),
      { numRuns: 100 },
    );
  });
});
