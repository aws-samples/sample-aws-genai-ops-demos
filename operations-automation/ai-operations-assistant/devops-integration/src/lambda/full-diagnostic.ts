/**
 * Full Diagnostic Composite Workflow
 *
 * Orchestrates the complete diagnostic sequence:
 * start_capture → wait(duration) → stop_capture → transform_capture → diagnose_tcp_stream
 *
 * Accepts parameters: eni_ids (1-5), duration_minutes (1-10, default 2),
 * target_host (optional), analysis_focus (optional, default "general")
 *
 * Requirements: 4.1, 4.2, 4.3, 4.7, 4.8
 */

import type { FullDiagnosticParams, DiagnosticReport } from "../types/index";
import type { ErrorDescription } from "../types/errors";
import { invokeNetworkAgent, AgentProxyConfig } from "./agent-proxy";
import { formatDiagnosticReport } from "./report-formatter";

// ─── Result Types ───────────────────────────────────────────────────────────

/**
 * Result of the full diagnostic composite workflow.
 */
export interface FullDiagnosticResult {
  /** Overall status of the workflow */
  status: "completed" | "partial" | "failed";
  /** Capture ID for follow-up queries (present on success or partial) */
  capture_id?: string;
  /** List of steps that completed successfully */
  steps_completed: string[];
  /** Full diagnostic report (present on completed status) */
  diagnostic_report?: DiagnosticReport;
  /** Partial data from completed steps (present on partial status) */
  partial_data?: Record<string, unknown>;
  /** Error info identifying the failed step (present on failed/partial status) */
  error?: { failed_step: string; message: string };
}

/**
 * Progress callback type for streaming state transition updates.
 */
export type ProgressCallback = (step: string, status: string) => void;

// ─── Constants ──────────────────────────────────────────────────────────────

/** Maximum allowed duration in minutes */
const MAX_DURATION_MINUTES = 10;

/** Minimum allowed duration in minutes */
const MIN_DURATION_MINUTES = 1;

/** Default duration in minutes */
const DEFAULT_DURATION_MINUTES = 2;

/** Minimum allowed ENI count */
const MIN_ENI_COUNT = 1;

/** Maximum allowed ENI count */
const MAX_ENI_COUNT = 5;

/** Default analysis focus */
const DEFAULT_ANALYSIS_FOCUS = "general";

// ─── Validation ─────────────────────────────────────────────────────────────

/**
 * Validation result for full diagnostic parameters.
 */
export interface ValidationResult {
  valid: boolean;
  error?: string;
}

/**
 * Validates the full diagnostic parameters before any workflow step is executed.
 *
 * Rules:
 * - eni_ids must be an array with 1-5 items
 * - duration_minutes must be 1-10 (defaults to 2 if not provided)
 *
 * Exported separately for Property 11 testing.
 *
 * @param params - The full diagnostic parameters to validate
 * @returns ValidationResult indicating whether params are valid
 */
export function validateFullDiagnosticParams(params: FullDiagnosticParams): ValidationResult {
  // Validate eni_ids presence and type
  if (!params.eni_ids || !Array.isArray(params.eni_ids)) {
    return {
      valid: false,
      error: "eni_ids is required and must be an array of ENI identifiers.",
    };
  }

  // Validate eni_ids count: 1-5
  if (params.eni_ids.length < MIN_ENI_COUNT) {
    return {
      valid: false,
      error: `eni_ids must contain at least ${MIN_ENI_COUNT} ENI identifier. Received: ${params.eni_ids.length}.`,
    };
  }

  if (params.eni_ids.length > MAX_ENI_COUNT) {
    return {
      valid: false,
      error: `eni_ids must contain at most ${MAX_ENI_COUNT} ENI identifiers. Received: ${params.eni_ids.length}. Maximum allowed: ${MAX_ENI_COUNT}.`,
    };
  }

  // Validate duration_minutes if provided: must be 1-10
  if (params.duration_minutes !== undefined) {
    if (typeof params.duration_minutes !== "number" || !Number.isFinite(params.duration_minutes)) {
      return {
        valid: false,
        error: `duration_minutes must be a number between ${MIN_DURATION_MINUTES} and ${MAX_DURATION_MINUTES}.`,
      };
    }

    if (params.duration_minutes < MIN_DURATION_MINUTES) {
      return {
        valid: false,
        error: `duration_minutes must be at least ${MIN_DURATION_MINUTES}. Received: ${params.duration_minutes}.`,
      };
    }

    if (params.duration_minutes > MAX_DURATION_MINUTES) {
      return {
        valid: false,
        error: `duration_minutes must not exceed ${MAX_DURATION_MINUTES} (10-minute maximum duration constraint). Received: ${params.duration_minutes}. Maximum allowed: ${MAX_DURATION_MINUTES}.`,
      };
    }
  }

  return { valid: true };
}

// ─── Helpers ────────────────────────────────────────────────────────────────

/**
 * Default sleep implementation using setTimeout.
 * Creates a promise that resolves after the specified number of milliseconds.
 */
export function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Sleep function type for dependency injection in tests. */
export type SleepFn = (ms: number) => Promise<void>;

/**
 * Checks if a result from invokeNetworkAgent is an error.
 */
function isError(result: unknown): result is ErrorDescription {
  return (
    typeof result === "object" &&
    result !== null &&
    "code" in result &&
    "message" in result &&
    !("output" in result)
  );
}

/**
 * Emits a progress update within 5 seconds of a state transition.
 * Calls the callback immediately (synchronous notification).
 */
function emitProgress(
  onProgress: ProgressCallback | undefined,
  step: string,
  status: string
): void {
  if (onProgress) {
    onProgress(step, status);
  }
}

// ─── Main Orchestrator ──────────────────────────────────────────────────────

/**
 * Options for the full diagnostic workflow execution.
 */
export interface ExecuteFullDiagnosticOptions {
  /** Optional agent proxy configuration */
  config?: AgentProxyConfig;
  /** Optional progress callback for state transitions */
  onProgress?: ProgressCallback;
  /** Optional sleep function for dependency injection (testing) */
  sleepFn?: SleepFn;
}

/**
 * Executes the full diagnostic composite workflow.
 *
 * Orchestrates the following steps in sequence:
 * 1. start_capture - Begin traffic capture on specified ENIs
 * 2. wait - Wait for the specified duration
 * 3. stop_capture - Stop the traffic capture
 * 4. transform_capture - Transform captured data for analysis
 * 5. diagnose_tcp_stream - Analyze the transformed data
 *
 * Progress updates are streamed within 5 seconds of each state transition.
 * Returns partial results if transformation or analysis fails.
 * Returns error immediately if start_capture fails.
 *
 * @param params - The full diagnostic parameters
 * @param config - Optional agent proxy configuration (or options object)
 * @param onProgress - Optional progress callback for state transitions
 * @returns FullDiagnosticResult with status, capture_id, and report/error
 */
export async function executeFullDiagnostic(
  params: FullDiagnosticParams,
  config?: AgentProxyConfig,
  onProgress?: ProgressCallback,
  sleepFn?: SleepFn
): Promise<FullDiagnosticResult> {
  const sleep = sleepFn ?? defaultSleep;
  // ─── Step 0: Validate parameters ───────────────────────────────────────────
  const validation = validateFullDiagnosticParams(params);
  if (!validation.valid) {
    return {
      status: "failed",
      steps_completed: [],
      error: {
        failed_step: "validation",
        message: validation.error!,
      },
    };
  }

  const durationMinutes = params.duration_minutes ?? DEFAULT_DURATION_MINUTES;
  const analysisFocus = params.analysis_focus ?? DEFAULT_ANALYSIS_FOCUS;
  const stepsCompleted: string[] = [];

  // ─── Step 1: start_capture ─────────────────────────────────────────────────
  const startResult = await invokeNetworkAgent(
    "start_capture",
    {
      eni_ids: params.eni_ids,
      duration_minutes: durationMinutes,
      target_host: params.target_host,
    },
    config
  );

  if (isError(startResult)) {
    return {
      status: "failed",
      steps_completed: [],
      error: {
        failed_step: "start_capture",
        message: startResult.message,
      },
    };
  }

  // Extract capture_id from the agent response
  let captureId: string;
  try {
    const startData = JSON.parse(startResult.output);
    captureId = startData.capture_id ?? startData.captureId ?? startResult.sessionId;
  } catch {
    captureId = startResult.sessionId;
  }

  stepsCompleted.push("start_capture");
  emitProgress(onProgress, "capture_started", "in_progress");

  // ─── Step 2: Wait for capture duration ─────────────────────────────────────
  const durationMs = durationMinutes * 60 * 1000;
  await sleep(durationMs);

  // ─── Step 3: stop_capture ──────────────────────────────────────────────────
  const stopResult = await invokeNetworkAgent(
    "stop_capture",
    { capture_id: captureId },
    config
  );

  if (isError(stopResult)) {
    return {
      status: "partial",
      capture_id: captureId,
      steps_completed: stepsCompleted,
      partial_data: { capture_id: captureId },
      error: {
        failed_step: "stop_capture",
        message: stopResult.message,
      },
    };
  }

  stepsCompleted.push("stop_capture");
  emitProgress(onProgress, "capture_stopped", "in_progress");

  // ─── Step 4: transform_capture ─────────────────────────────────────────────
  const transformResult = await invokeNetworkAgent(
    "transform_capture",
    { capture_id: captureId },
    config
  );

  if (isError(transformResult)) {
    return {
      status: "partial",
      capture_id: captureId,
      steps_completed: stepsCompleted,
      partial_data: {
        capture_id: captureId,
        capture_metadata: {
          eni_ids: params.eni_ids,
          duration_minutes: durationMinutes,
          target_host: params.target_host,
        },
      },
      error: {
        failed_step: "transform_capture",
        message: transformResult.message,
      },
    };
  }

  stepsCompleted.push("transform_capture");
  emitProgress(onProgress, "transformation_in_progress", "in_progress");

  // Parse transform output for partial results
  let transformOutput: Record<string, unknown> = {};
  try {
    transformOutput = JSON.parse(transformResult.output);
  } catch {
    transformOutput = { raw_output: transformResult.output };
  }

  // ─── Step 5: diagnose_tcp_stream ───────────────────────────────────────────
  const diagnoseResult = await invokeNetworkAgent(
    "diagnose_tcp_stream",
    {
      capture_id: captureId,
      analysis_focus: analysisFocus,
    },
    config
  );

  if (isError(diagnoseResult)) {
    return {
      status: "partial",
      capture_id: captureId,
      steps_completed: stepsCompleted,
      partial_data: {
        capture_id: captureId,
        capture_metadata: {
          eni_ids: params.eni_ids,
          duration_minutes: durationMinutes,
          target_host: params.target_host,
        },
        transform_output: transformOutput,
      },
      error: {
        failed_step: "diagnose_tcp_stream",
        message: diagnoseResult.message,
      },
    };
  }

  stepsCompleted.push("diagnose_tcp_stream");
  emitProgress(onProgress, "analysis_complete", "completed");

  // ─── Format diagnostic report ──────────────────────────────────────────────
  const diagnosticReport = formatDiagnosticReport(diagnoseResult.output);

  return {
    status: "completed",
    capture_id: captureId,
    steps_completed: stepsCompleted,
    diagnostic_report: diagnosticReport,
  };
}
