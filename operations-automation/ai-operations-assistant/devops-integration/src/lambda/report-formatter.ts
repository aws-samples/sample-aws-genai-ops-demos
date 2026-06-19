/**
 * Diagnostic Report Formatter
 *
 * Transforms Network Agent raw responses into the structured DiagnosticReport
 * format optimized for LLM reasoning by DevOps Agent.
 *
 * Enforces size constraints:
 * - summary: ≤500 characters
 * - affected_streams: ≤50 entries
 * - root_cause_indicators: ≤10 entries
 * - recommended_actions: ≤10 entries
 * - raw_evidence: ≤20 entries
 *
 * Requirements: 3.1, 3.4, 3.5
 */

import type { DiagnosticReport } from "../types/index";
import type {
  StreamInfo,
  RootCause,
  EvidenceRef,
  DataSufficiencyWarning,
  TlsDetails,
  ConnectionDropDetails,
} from "../types/report";

// ─── Size Constraint Constants ──────────────────────────────────────────────

const MAX_SUMMARY_LENGTH = 500;
const MAX_AFFECTED_STREAMS = 50;
const MAX_ROOT_CAUSE_INDICATORS = 10;
const MAX_RECOMMENDED_ACTIONS = 10;
const MAX_RAW_EVIDENCE = 20;

/** Maximum total serialized JSON size for a diagnostic report (32,000 characters). */
export const MAX_REPORT_SIZE = 32_000;

// ─── Exported Helper Functions ──────────────────────────────────────────────

/**
 * Truncates a summary string to the maximum allowed length.
 * Appends "..." if truncation occurs.
 */
export function truncateSummary(summary: string): string {
  if (!summary) {
    return "";
  }
  if (summary.length <= MAX_SUMMARY_LENGTH) {
    return summary;
  }
  return summary.slice(0, MAX_SUMMARY_LENGTH - 3) + "...";
}

/**
 * Enforces array size limits by taking only the first N entries.
 * Returns a new array with at most `limit` elements.
 */
export function enforceArrayLimits<T>(array: T[], limit: number): T[] {
  if (!Array.isArray(array)) {
    return [];
  }
  return array.slice(0, limit);
}

/**
 * Calculates confidence level based on the number of independent root cause indicators.
 * - ≥3 indicators → "high"
 * - 2 indicators → "medium"
 * - 1 or 0 indicators → "low"
 */
export function calculateConfidenceLevel(
  indicatorCount: number
): "high" | "medium" | "low" {
  if (indicatorCount >= 3) {
    return "high";
  }
  if (indicatorCount === 2) {
    return "medium";
  }
  return "low";
}

/**
 * Enforces the 32,000 character total report size limit.
 *
 * If the serialized JSON exceeds MAX_REPORT_SIZE, truncates raw_evidence entries
 * oldest-first (sorted by timestamp ascending, removed from the front) until the
 * report fits within the limit. Sets a truncation_notice field indicating how many
 * entries were omitted.
 *
 * Other sections (summary, root_cause_indicators, etc.) are never truncated.
 *
 * @param report - A DiagnosticReport object to constrain
 * @returns The report, potentially with raw_evidence entries removed and truncation_notice set
 */
export function enforceReportSizeLimit(report: DiagnosticReport): DiagnosticReport {
  let serialized = JSON.stringify(report);

  if (serialized.length <= MAX_REPORT_SIZE) {
    return report;
  }

  // Sort raw_evidence by timestamp ascending (oldest first) for removal
  const sortedEvidence = [...report.raw_evidence].sort((a, b) => {
    return a.timestamp.localeCompare(b.timestamp);
  });

  const originalCount = sortedEvidence.length;
  let truncatedEvidence = [...sortedEvidence];
  let omittedCount = 0;

  // Remove oldest entries one at a time until under the limit
  while (truncatedEvidence.length > 0) {
    truncatedEvidence.shift(); // Remove oldest entry
    omittedCount++;

    const candidate: DiagnosticReport = {
      ...report,
      raw_evidence: truncatedEvidence,
      truncation_notice: `${omittedCount} raw_evidence entries omitted due to 32,000 character report size limit`,
    };

    serialized = JSON.stringify(candidate);
    if (serialized.length <= MAX_REPORT_SIZE) {
      return candidate;
    }
  }

  // Edge case: even with empty raw_evidence the report exceeds 32K
  // Return with all evidence removed
  return {
    ...report,
    raw_evidence: [],
    truncation_notice: `${originalCount} raw_evidence entries omitted due to 32,000 character report size limit`,
  };
}

// ─── TLS and Connection Drop Extraction ─────────────────────────────────────

/**
 * Extracts TLS-specific diagnostic details from raw data.
 * Returns undefined if the required fields are missing or invalid.
 *
 * Expected raw structure:
 * - client_hello_size_bytes: number
 * - key_exchange_algorithm: string
 * - fragmentation: { fragment_count: number, fragment_sizes: number[] }
 * - middlebox_behavior: { action: "drop"|"reset"|"modification", appliance_type?: string }
 *
 * Requirement 3.2
 */
export function extractTlsDetails(raw: unknown): TlsDetails | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }

  const obj = raw as Record<string, unknown>;

  // Validate client_hello_size_bytes
  if (typeof obj.client_hello_size_bytes !== "number") {
    return undefined;
  }

  // Validate key_exchange_algorithm
  if (typeof obj.key_exchange_algorithm !== "string") {
    return undefined;
  }

  // Validate fragmentation
  if (typeof obj.fragmentation !== "object" || obj.fragmentation === null) {
    return undefined;
  }
  const frag = obj.fragmentation as Record<string, unknown>;
  if (typeof frag.fragment_count !== "number") {
    return undefined;
  }
  if (!Array.isArray(frag.fragment_sizes)) {
    return undefined;
  }
  const fragmentSizes = frag.fragment_sizes.filter(
    (s): s is number => typeof s === "number"
  );
  if (fragmentSizes.length === 0 && frag.fragment_sizes.length > 0) {
    return undefined;
  }

  // Validate middlebox_behavior
  if (typeof obj.middlebox_behavior !== "object" || obj.middlebox_behavior === null) {
    return undefined;
  }
  const mb = obj.middlebox_behavior as Record<string, unknown>;
  const validActions = ["drop", "reset", "modification"];
  if (!validActions.includes(mb.action as string)) {
    return undefined;
  }

  return {
    client_hello_size_bytes: obj.client_hello_size_bytes,
    key_exchange_algorithm: obj.key_exchange_algorithm,
    fragmentation: {
      fragment_count: frag.fragment_count,
      fragment_sizes: fragmentSizes,
    },
    middlebox_behavior: {
      action: mb.action as "drop" | "reset" | "modification",
      appliance_type: typeof mb.appliance_type === "string" ? mb.appliance_type : undefined,
    },
  };
}

/**
 * Extracts connection drop diagnostic details from raw data.
 * Returns undefined if the required fields are missing or invalid.
 *
 * Expected raw structure:
 * - rst_origin: { source_ip: string, origin_classification: "client"|"server"|"intermediate_device" }
 * - timing_ms: number
 * - appliance_correlation: { appliance_type: string, behavior_description: string }
 *
 * Requirement 3.3
 */
export function extractConnectionDropDetails(raw: unknown): ConnectionDropDetails | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }

  const obj = raw as Record<string, unknown>;

  // Validate rst_origin
  if (typeof obj.rst_origin !== "object" || obj.rst_origin === null) {
    return undefined;
  }
  const rst = obj.rst_origin as Record<string, unknown>;
  if (typeof rst.source_ip !== "string") {
    return undefined;
  }
  const validClassifications = ["client", "server", "intermediate_device"];
  if (!validClassifications.includes(rst.origin_classification as string)) {
    return undefined;
  }

  // Validate timing_ms
  if (typeof obj.timing_ms !== "number") {
    return undefined;
  }

  // Validate appliance_correlation
  if (typeof obj.appliance_correlation !== "object" || obj.appliance_correlation === null) {
    return undefined;
  }
  const ac = obj.appliance_correlation as Record<string, unknown>;
  if (typeof ac.appliance_type !== "string") {
    return undefined;
  }
  if (typeof ac.behavior_description !== "string") {
    return undefined;
  }

  return {
    rst_origin: {
      source_ip: rst.source_ip,
      origin_classification: rst.origin_classification as "client" | "server" | "intermediate_device",
    },
    timing_ms: obj.timing_ms,
    appliance_correlation: {
      appliance_type: ac.appliance_type,
      behavior_description: ac.behavior_description,
    },
  };
}

/**
 * Detects whether the capture data is sufficient for conclusive analysis.
 * Returns a DataSufficiencyWarning if data is insufficient, undefined otherwise.
 *
 * Conditions for insufficiency:
 * - relevant_packet_count < 10
 * - capture duration shorter than one connection lifecycle (estimated from capture metadata)
 *
 * Requirement 3.6
 */
export function detectDataSufficiency(rawData: Record<string, unknown>): DataSufficiencyWarning | undefined {
  const relevantPacketCount = typeof rawData.relevant_packet_count === "number"
    ? rawData.relevant_packet_count
    : undefined;

  const captureDurationMs = typeof rawData.capture_duration_ms === "number"
    ? rawData.capture_duration_ms
    : undefined;

  const analysisFocus = typeof rawData.analysis_focus === "string"
    ? rawData.analysis_focus
    : "general";

  // Determine minimum connection lifecycle duration based on protocol
  // TLS handshake requires more time than a basic TCP handshake
  const isTlsFocused = analysisFocus === "tls";
  const minLifecycleDurationMs = isTlsFocused ? 5000 : 3000; // 5s for TLS, 3s for TCP
  const recommendedMinutes = isTlsFocused ? 5 : 2;

  const packetInsufficient = relevantPacketCount !== undefined && relevantPacketCount < 10;
  const durationInsufficient = captureDurationMs !== undefined && captureDurationMs < minLifecycleDurationMs;

  if (!packetInsufficient && !durationInsufficient) {
    return undefined;
  }

  // Build reason text
  const reasons: string[] = [];
  if (packetInsufficient) {
    reasons.push(
      `Only ${relevantPacketCount} relevant packets captured (minimum 10 required for conclusive analysis)`
    );
  }
  if (durationInsufficient) {
    reasons.push(
      `Capture duration (${captureDurationMs}ms) is shorter than one complete connection lifecycle for the target protocol`
    );
  }

  // Build required traffic patterns
  const requiredPatterns: string[] = [];
  if (isTlsFocused) {
    requiredPatterns.push("Complete TLS handshake");
    requiredPatterns.push("TLS Client Hello and Server Hello exchange");
    requiredPatterns.push("Certificate exchange");
  } else {
    requiredPatterns.push("TCP three-way handshake");
    requiredPatterns.push("At least one complete request-response cycle");
  }
  requiredPatterns.push("Connection teardown (FIN or RST)");

  return {
    reason: reasons.join("; "),
    relevant_packet_count: relevantPacketCount ?? 0,
    recommended_duration_minutes: recommendedMinutes,
    required_traffic_patterns: requiredPatterns,
  };
}

// ─── Internal Helpers ───────────────────────────────────────────────────────

/**
 * Generates a comparison statement explaining what packet analysis reveals
 * beyond what VPC Reachability Analyzer provides at L3/L4.
 */
function generateComparisonText(rootCauses: RootCause[]): string {
  if (!rootCauses || rootCauses.length === 0) {
    return (
      "VPC Reachability Analyzer confirms L3/L4 path connectivity. " +
      "No additional application-layer issues were identified by packet analysis."
    );
  }

  const categories = new Set(rootCauses.map((rc) => rc.category));

  if (categories.has("tls")) {
    return (
      "VPC Reachability Analyzer confirms L3/L4 path connectivity, but packet analysis " +
      "reveals TLS Client Hello fragmentation causing connection drops at the network firewall."
    );
  }

  if (categories.has("connectivity")) {
    return (
      "VPC Reachability Analyzer confirms L3/L4 path connectivity, but packet analysis " +
      "reveals unexpected TCP RST packets originating from intermediate network appliances, " +
      "indicating connection termination beyond basic routing issues."
    );
  }

  if (categories.has("performance")) {
    return (
      "VPC Reachability Analyzer confirms L3/L4 path connectivity, but packet analysis " +
      "reveals significant latency spikes and retransmission patterns indicative of " +
      "application-layer performance degradation not visible at the network path level."
    );
  }

  if (categories.has("dns")) {
    return (
      "VPC Reachability Analyzer confirms L3/L4 path connectivity, but packet analysis " +
      "reveals DNS resolution anomalies affecting service discovery that are invisible " +
      "to path-level analysis."
    );
  }

  if (categories.has("tcp_health")) {
    return (
      "VPC Reachability Analyzer confirms L3/L4 path connectivity, but packet analysis " +
      "reveals TCP health issues including abnormal window scaling, excessive retransmissions, " +
      "or connection state anomalies not detectable through path analysis alone."
    );
  }

  return (
    "VPC Reachability Analyzer confirms L3/L4 path connectivity, but packet analysis " +
    "reveals application-layer issues that are not visible through path connectivity checks alone."
  );
}

/**
 * Safely parses raw input from the Network Agent.
 * Handles string JSON, already-parsed objects, null, and undefined.
 */
function parseRawInput(raw: unknown): Record<string, unknown> {
  if (raw === null || raw === undefined) {
    return {};
  }

  if (typeof raw === "string") {
    if (raw.trim() === "") {
      return {};
    }
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed === "object" && parsed !== null) {
        return parsed as Record<string, unknown>;
      }
      return {};
    } catch {
      return {};
    }
  }

  if (typeof raw === "object") {
    return raw as Record<string, unknown>;
  }

  return {};
}

/**
 * Validates and extracts a StreamInfo array from raw data.
 * Skips invalid entries.
 */
function extractStreams(raw: unknown): StreamInfo[] {
  if (!Array.isArray(raw)) {
    return [];
  }

  return raw.filter((item): item is StreamInfo => {
    if (typeof item !== "object" || item === null) {
      return false;
    }
    const obj = item as Record<string, unknown>;
    return (
      typeof obj.source_ip === "string" &&
      typeof obj.destination_ip === "string" &&
      typeof obj.source_port === "number" &&
      typeof obj.destination_port === "number" &&
      typeof obj.protocol === "string" &&
      typeof obj.description === "string" &&
      typeof obj.packet_count === "number"
    );
  });
}

/**
 * Validates and extracts a RootCause array from raw data.
 * Skips invalid entries.
 */
function extractRootCauses(raw: unknown): RootCause[] {
  if (!Array.isArray(raw)) {
    return [];
  }

  const validConfidenceLevels = ["high", "medium", "low"];
  const validCategories = [
    "tls",
    "tcp_health",
    "dns",
    "connectivity",
    "performance",
    "general",
  ];

  return raw.filter((item): item is RootCause => {
    if (typeof item !== "object" || item === null) {
      return false;
    }
    const obj = item as Record<string, unknown>;
    return (
      typeof obj.description === "string" &&
      validConfidenceLevels.includes(obj.confidence_level as string) &&
      validCategories.includes(obj.category as string) &&
      Array.isArray(obj.evidence_refs)
    );
  });
}

/**
 * Validates and extracts an EvidenceRef array from raw data.
 * Skips invalid entries.
 */
function extractEvidence(raw: unknown): EvidenceRef[] {
  if (!Array.isArray(raw)) {
    return [];
  }

  return raw.filter((item): item is EvidenceRef => {
    if (typeof item !== "object" || item === null) {
      return false;
    }
    const obj = item as Record<string, unknown>;
    return (
      typeof obj.type === "string" &&
      typeof obj.description === "string" &&
      typeof obj.location === "string" &&
      typeof obj.timestamp === "string"
    );
  });
}

/**
 * Validates and extracts recommended actions from raw data.
 * Skips non-string entries.
 */
function extractRecommendedActions(raw: unknown): string[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter((item): item is string => typeof item === "string" && item.length > 0);
}

/**
 * Extracts DataSufficiencyWarning if present and valid.
 */
function extractDataSufficiency(raw: unknown): DataSufficiencyWarning | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }
  const obj = raw as Record<string, unknown>;
  if (
    typeof obj.reason === "string" &&
    typeof obj.relevant_packet_count === "number" &&
    typeof obj.recommended_duration_minutes === "number" &&
    Array.isArray(obj.required_traffic_patterns)
  ) {
    return {
      reason: obj.reason,
      relevant_packet_count: obj.relevant_packet_count,
      recommended_duration_minutes: obj.recommended_duration_minutes,
      required_traffic_patterns: obj.required_traffic_patterns.filter(
        (p): p is string => typeof p === "string"
      ),
    };
  }
  return undefined;
}

// ─── Main Formatter Function ────────────────────────────────────────────────

/**
 * Transforms raw Network Agent output into a properly constrained DiagnosticReport.
 *
 * Handles:
 * - Empty/null inputs gracefully (returns a "no data" report)
 * - Missing fields in raw input (uses defaults)
 * - Invalid/malformed data (skips invalid entries)
 *
 * @param rawInput - Raw Network Agent output (parsed JSON object or string)
 * @returns A fully constrained DiagnosticReport object
 */
export function formatDiagnosticReport(rawInput: unknown): DiagnosticReport {
  const data = parseRawInput(rawInput);

  // Handle empty/null input - return a "no data" report
  if (Object.keys(data).length === 0) {
    return {
      summary: "No diagnostic data available. The Network Agent returned no results.",
      affected_streams: [],
      root_cause_indicators: [],
      recommended_actions: [],
      raw_evidence: [],
      confidence_level: "low",
      comparison_with_reachability_analyzer:
        "VPC Reachability Analyzer confirms L3/L4 path connectivity. " +
        "No additional application-layer issues were identified by packet analysis.",
    };
  }

  // Extract and validate fields
  const summary = truncateSummary(
    typeof data.summary === "string" ? data.summary : "Diagnostic analysis completed."
  );

  const affectedStreams = enforceArrayLimits(
    extractStreams(data.affected_streams),
    MAX_AFFECTED_STREAMS
  );

  let rootCauseIndicators = enforceArrayLimits(
    extractRootCauses(data.root_cause_indicators),
    MAX_ROOT_CAUSE_INDICATORS
  );

  const recommendedActions = enforceArrayLimits(
    extractRecommendedActions(data.recommended_actions),
    MAX_RECOMMENDED_ACTIONS
  );

  const rawEvidence = enforceArrayLimits(
    extractEvidence(data.raw_evidence),
    MAX_RAW_EVIDENCE
  );

  // Calculate overall confidence based on indicator count
  const confidenceLevel = calculateConfidenceLevel(rootCauseIndicators.length);

  // Generate comparison text based on findings
  const comparisonText = generateComparisonText(rootCauseIndicators);

  // Attach TLS details to root causes with category "tls" (Requirement 3.2)
  const hasTlsCauses = rootCauseIndicators.some((rc) => rc.category === "tls");
  if (hasTlsCauses && data.tls_details) {
    const tlsDetails = extractTlsDetails(data.tls_details);
    if (tlsDetails) {
      rootCauseIndicators = rootCauseIndicators.map((rc) =>
        rc.category === "tls" ? { ...rc, tls_details: tlsDetails } : rc
      );
    }
  }

  // Attach connection drop details to root causes with category "connectivity" (Requirement 3.3)
  const hasConnectivityCauses = rootCauseIndicators.some((rc) => rc.category === "connectivity");
  if (hasConnectivityCauses && data.connection_drop_details) {
    const connectionDropDetails = extractConnectionDropDetails(data.connection_drop_details);
    if (connectionDropDetails) {
      rootCauseIndicators = rootCauseIndicators.map((rc) =>
        rc.category === "connectivity" ? { ...rc, connection_drop_details: connectionDropDetails } : rc
      );
    }
  }

  // Detect data sufficiency from raw data fields (Requirement 3.6)
  const detectedSufficiency = detectDataSufficiency(data);

  // Extract previously computed data sufficiency warning if present in input
  const existingDataSufficiency = extractDataSufficiency(data.data_sufficiency);

  // Build the report
  const report: DiagnosticReport = {
    summary,
    affected_streams: affectedStreams,
    root_cause_indicators: rootCauseIndicators,
    recommended_actions: recommendedActions,
    raw_evidence: rawEvidence,
    confidence_level: confidenceLevel,
    comparison_with_reachability_analyzer: comparisonText,
  };

  // Set data sufficiency: prefer detected warning, fall back to existing (Requirement 3.6)
  if (detectedSufficiency) {
    report.data_sufficiency = detectedSufficiency;
  } else if (existingDataSufficiency) {
    report.data_sufficiency = existingDataSufficiency;
  }

  // Handle healthy traffic (Requirement 3.7): no anomalies detected
  if (rootCauseIndicators.length === 0 && rawEvidence.length > 0) {
    if (!data.summary || report.summary === "Diagnostic analysis completed.") {
      report.summary = "No anomalies detected. Captured traffic shows healthy network behavior with expected protocol patterns.";
    }
  }

  // Check for truncation notice from input or if we truncated evidence
  if (typeof data.truncation_notice === "string") {
    report.truncation_notice = data.truncation_notice;
  }

  // Enforce the 32,000 character total report size limit
  return enforceReportSizeLimit(report);
}
