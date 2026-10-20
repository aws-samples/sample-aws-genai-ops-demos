// API service for the AI Lambda Runtime Migration admin UI (v1.1).
//
// Every call goes over HTTPS to the HTTP API deployed by the Api stack, with
// the Cognito ID token in the Authorization header (validated by the API's
// JWT authorizer). The browser holds no AWS credentials — this replaces the
// old SigV4 browser-side runtime invokes.
//
//   POST /actions            -> router actions (list_functions / get_stats / settings)
//   POST /refresh            -> start (or adopt) the durable Discover + Assess pipeline
//   GET  /refresh/{arn+}     -> pipeline execution status for polling / re-attach
//   GET  /scans              -> recent scan executions (Activity log)
//   POST /migrate-batch      -> start ONE migration batch (the upgrade branch is pushed per function)
//   GET  /migrate-run/{arn+} -> one batch execution's status + phases
//   GET  /migrate-runs       -> recent batch executions (Steps card re-attach, Activity log)
import { getIdToken } from './auth';

const apiUrl: string = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

// --- Types -----------------------------------------------------------------

// 'MIGRATED' is inferred when Trusted Advisor stops reporting a function; the
// other values are set by the scan.
type MigrationStatus = 'DISCOVERED' | 'ASSESSED' | 'SKIPPED' | 'MIGRATED';

// Phase-2 migration-run axis (the mig# item enum), distinct from the Phase-1
// migration_status above. A function with no mig# item reads as NOT_STARTED.
// REJECTED = triaged out before any build (no repo tag, source/branch missing,
// unsupported runtime, access denied): neutral, re-migratable, NOT a failure.
// FAILED = a real failure (build could not start, transform failed, push
// failed, timed out). NO_CHANGES = AWS Transform ran and found nothing to
// change: neutral, re-migratable. BRANCH_READY = the upgrade branch is pushed
// to the repository (migration_branch names it); the flow ends there.
// Unknown-value guard: every consumer goes through constants.migrationRunConfig(),
// which accepts any string, so a status this build does not know renders with
// its raw value and never throws.
export type MigrationRunStatus = 'NOT_STARTED' | 'QUEUED' | 'IN_PROGRESS' | 'REJECTED' | 'FAILED' | 'NO_CHANGES' | 'BRANCH_READY';

// One row of the scoped inventory. FLAT (unlike the lifecycle tracker's nested
// affected_resource_details): one Lambda function = one row, keyed on item_key.
export interface MigrationFunction {
  item_key: string;            // fn#<account>#<region>#<arn>
  function_arn: string;
  account_id?: string;         // present only when multi-account is deployed
  account_name?: string;
  region: string;
  runtime: string;
  alert_status?: string;       // Trusted Advisor red/yellow/green
  deprecation_date?: string;
  days_to_deprecation?: number;
  avg_daily_invokes?: number;
  invocation_count_14d?: number;
  package_type?: string;       // 'Zip' | 'Image'
  memory_size?: number;
  timeout?: number;
  handler?: string;
  tags?: Record<string, string>;
  layers?: string[];
  migration_status?: MigrationStatus;
  first_seen_at?: string;
  resolved_at?: string;
  // Nova prioritization (Discover phase)
  priority_score?: number;
  priority_label?: string;     // Critical / High / Medium / Low
  priority_reasoning?: string;
  priority_base?: number;      // rule-based base score
  priority_adjustment?: number;  // Nova adjustment in [-15, 15]
  // Nova assessment (Assess phase)
  migration_complexity?: string;   // Low / Medium / High
  assessment_summary?: string;
  migration_risks?: string;        // backend scalar (a single risks string, not a list)
  deprecated_apis?: string[];
  breaking_changes?: string[];
  dependency_issues?: string[];
  lines_of_code?: number;
  num_source_files?: number;
  num_manifests?: number;
  source_truncated?: boolean;  // MAX_CODE_CHARS cut the source or a manifest; the assessment is partial
  assessed_at?: string;
  // Phase-2 migration run (merged from the mig# item by list_functions). This
  // is a distinct axis from migration_status above; default NOT_STARTED.
  migration_run_status?: MigrationRunStatus;
  migration_failure_code?: string;
  migration_failure_detail?: string;
  migration_branch?: string;         // the pushed upgrade branch (BRANCH_READY)
  migration_repo?: string;           // the repository clone URL the build pushed to
  migration_diff_summary?: string;   // git diff --stat of the upgrade branch
  migration_build_id?: string;
  migration_target?: string;         // the family target the run used (fixed by the managed transformation)
  migration_run_updated_at?: string;
  [key: string]: unknown;
}

// One immutable per-scan rollup row (key 'rollup#<rollup_at>'). The series is
// the measurement history the Statistics charts draw. Each run
// appends exactly one of these; the backend never mutates an existing row.
export interface RollupRecord {
  item_key: string;              // rollup#<rollup_at>
  rollup_at: string;             // ISO timestamp (= the run's finished_at)
  run_id: string;
  accounts_scanned: number;
  accounts_not_checked: number;
  total_active: number;          // active backlog: Red/Yellow, not resolved
  red: number;
  yellow: number;
  migrated_total: number;        // rows with migration_status === 'MIGRATED'
  by_runtime: Record<string, number>;  // active backlog grouped by runtime
  by_account: Record<string, number>;  // active backlog grouped by account
  // Migration-run axis: the active backlog partitioned by the mig# enum.
  // Absent on rollups written before the run axis; read as 0.
  run_not_started?: number;
  run_in_progress?: number;      // QUEUED | IN_PROGRESS
  run_rejected?: number;
  run_failed?: number;
  run_no_changes?: number;
  run_branches_ready?: number;   // BRANCH_READY: upgrade branch pushed, awaiting merge
  run_unknown?: number;           // any status this build does not know
}

// --- Transport -------------------------------------------------------------

const callApi = async (method: 'GET' | 'POST', path: string, body?: unknown): Promise<any> => {
  if (!apiUrl) {
    throw new Error('API URL not configured (VITE_API_URL missing) - rebuild the frontend');
  }
  const idToken = await getIdToken();
  if (!idToken) {
    throw new Error('Not authenticated - no ID token available');
  }

  const response = await fetch(`${apiUrl}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${idToken}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await response.text();
  let data: any = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }
  }
  if (!response.ok) {
    throw new Error(data?.error || data?.message || `${method} ${path} failed (${response.status})`);
  }
  return data;
};

// Router action (POST /actions). Payload shape: { action, ...params }.
const invokeAction = async (payload: Record<string, unknown>): Promise<any> => callApi('POST', '/actions', payload);

// --- Inventory -------------------------------------------------------------

// Load the whole inventory, unfiltered. The scan discovers the whole org; the
// operator narrows the results in the table's PropertyFilter (region, tags).
export const getFunctions = async (): Promise<{ functions: MigrationFunction[] }> => {
  const result = await invokeAction({ action: 'list_functions' });
  return { functions: result.functions || [] };
};

// The per-scan rollup series (measurement history), ordered oldest-first by the
// backend. Shared with the Statistics charts.
export const getStats = async (): Promise<{ rollups: RollupRecord[] }> => {
  const result = await invokeAction({ action: 'get_stats' });
  return { rollups: result.rollups || [] };
};

// --- Settings (Git access + code-behind detection method) ------------------
// Non-secret configuration for the migration phase: which Git platform the
// build pushes the upgrade branch to, and how the tool detects each function's
// source repo.
// The token VALUE is never captured, sent, or stored — only the NAME/ARN of a
// Secrets Manager secret (secretRef) the tool reads server-side.

export type GitPlatform = 'github' | 'gitlab' | 'bitbucket' | 'codecommit';
export type DetectionMethod = 'tag' | 'ai';

interface GitSettings {
  platform: GitPlatform;
  host?: string;
  org?: string;
  secretRef?: string;        // name/ARN of a Secrets Manager secret — never a token
  detectionMethod: DetectionMethod;
  repoTag?: string;          // tag key whose value is the source repository
  branchTag?: string;        // optional tag key whose value is the release branch
  updated_at?: string;
}

// Read the stored non-secret settings. Returns {} (as a partial) when unset.
export const getSettings = async (): Promise<{ settings: Partial<GitSettings> }> => {
  const result = await invokeAction({ action: 'get_settings' });
  return { settings: result.settings || {} };
};

// Persist the non-secret settings. The backend whitelists the fields it stores;
// no token value is ever included in the payload.
export const saveSettings = async (
  settings: GitSettings,
): Promise<{ settings: Partial<GitSettings>; saved: boolean }> => {
  const result = await invokeAction({ action: 'put_settings', settings });
  return { settings: result.settings || {}, saved: !!result.saved };
};

// One function's resolved source repository, as returned by the AI detection
// playground. `repository` is null when the resolver could not resolve it with
// confidence — that function is sent to review, never guessed.
export interface RepoResolution {
  function_arn: string;
  function_name: string;
  runtime?: string;
  region?: string;
  account_id?: string;
  account_name?: string;
  repository: string | null;
  confidence: number;       // 0..1
  evidence: string[];
}

// Run the read-only AI code-behind detection playground: the backend picks up
// to 3 random inventory functions and, for each, asks Nova to infer the source
// repository with a confidence score and evidence. Nothing is written and no
// pull request is opened.
export const resolveReposDemo = async (
  prompt: string,
): Promise<{ results: RepoResolution[] }> => {
  const result = await invokeAction({ action: 'resolve_repos_demo', prompt });
  return { results: result.results || [] };
};

// --- Refresh pipeline (Lambda durable function) -----------------------------
// The whole refresh (Discover + Assess over the scope) runs server-side as one
// durable execution. The UI only starts and observes it, so closing the tab
// never kills a run.

export interface ScanPhase {
  label: string;
  status: 'pending' | 'in-progress' | 'success' | 'warning' | 'error' | 'stopped';
  done: number;
  failed: number;
  total: number | null;
  running: string[];   // step names still in flight (capped)
  // The "Determine the accounts to scan" phase polls AWS Organizations once, so
  // its count is always "1 of 1"; the backend instead reports how many accounts
  // it resolved. Only present on that phase.
  accountsFound?: number;
}

interface RefreshProgress {
  support_done?: number;
  discover_done: number;
  assess_done: number;
  phases?: ScanPhase[];
}

export interface RefreshExecutionStatus {
  executionArn: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'STOPPED';
  startDate?: Date;
  stopDate?: Date;
  summary?: Record<string, unknown>;
  progress?: RefreshProgress;
  error?: unknown;
}

interface RefreshRequest {
  // The scope the operator is rescanning: tag filter + optional regions/accounts.
  scope?: {
    tags?: Array<{ key: string; values: string[] }>;
    regions?: string[];
    accounts?: Array<{ id: string; name: string }>;
  };
}

// Start an org-wide scan (Discover + Assess). If one is already running, the
// API adopts it instead of starting a second one.
export const startScan = async (
  request: RefreshRequest = {}
): Promise<{ executionArn: string; alreadyRunning: boolean }> => {
  const scope = request.scope;
  const result = await callApi('POST', '/refresh', { refresh_origin: 'manual', ...(scope ? { scope } : {}) });
  return { executionArn: result.executionArn, alreadyRunning: !!result.alreadyRunning };
};

// --- Scan log (recent durable executions) ----------------------------------
// A read-only list of the last few pipeline executions, newest first, for the
// "Scan log" timeline. Each item carries the bounded summarize-step result
// when the run produced one.

export interface ScanSummary {
  discover?: {
    discovered?: number;
    cells_total?: number;
    cells_succeeded?: number;
    failed_cells?: string[];
    accounts_scanned?: string[];
    accounts_failed?: string[];
  };
  assess?: {
    total?: number;
    assessed?: number;
    skipped?: number;
    failed?: number;
  };
  // Accounts whose Support tier (Basic/Developer/unknown) made them ineligible
  // for Trusted Advisor, so they were never scanned. Surfaced as a COUNTER in
  // the UI (never a list) — per-account "not checked" reporting.
  accounts_not_checked?: Array<{
    account_id: string;
    account_name?: string;
    tier?: string;
    reason?: string;
  }>;
  accounts_tiers?: unknown[];
  refresh_origin?: string;
  [key: string]: unknown;
}

export interface ScanRecord {
  executionArn: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'STOPPED';
  startedAt?: Date;
  finishedAt?: Date;
  summary?: ScanSummary;
}

// List the recent scans (durable executions), newest first, capped at 10.
export const getRecentScans = async (limit = 10): Promise<ScanRecord[]> => {
  const result = await callApi('GET', `/scans?limit=${limit}`);
  const scans: any[] = result.scans || [];
  return scans.map((s) => ({
    executionArn: s.executionArn,
    status: s.status || 'RUNNING',
    startedAt: s.startedAt ? new Date(s.startedAt) : undefined,
    finishedAt: s.finishedAt ? new Date(s.finishedAt) : undefined,
    summary: s.summary || undefined,
  }));
};

// Poll the status of a refresh execution. The arn is appended to the greedy
// {arn+} route path.
export const getScanStatus = async (executionArn: string): Promise<RefreshExecutionStatus> => {
  const result = await callApi('GET', `/refresh/${executionArn}`);
  return {
    executionArn,
    status: result.status || 'RUNNING',
    startDate: result.startDate ? new Date(result.startDate) : undefined,
    stopDate: result.stopDate ? new Date(result.stopDate) : undefined,
    summary: result.summary || undefined,
    progress: result.progress || undefined,
    error: result.error,
  };
};

// --- Migration (AWS Transform via CodeBuild, durable) -----------------------
// Starting a migration is a direct action from the inventory (no wizard). The
// durable orchestrator verifies the source repo, renders the transform config
// and starts the CodeBuild job; the build pushes an upgrade branch to the
// repository when AWS Transform produced changes. Nothing is merged and no
// pull request is opened: the flow ends at the pushed branch.

// --- Migration BATCH (one durable execution per Migrate click) --------------
// A Migrate click starts ONE batch durable execution over the selected ARNs,
// surfaced as a small set of phases with counters (the same ScanPhase shape
// as the scan). The UI starts it, then polls getMigrationBatchStatus on phase
// state, so one click produces one card and one finish flash.

// One migration phase with a counter (same field shape as ScanPhase).
export interface MigrationPhase {
  label: string;
  status: 'pending' | 'in-progress' | 'success' | 'warning' | 'error' | 'stopped';
  done: number;
  failed: number;
  total: number | null;
  running: string[];
  note?: string;          // backend-owned explanation for a skipped/zero phase
  noChanges?: number;     // Transform phase: units that succeeded with no changes
  branchesReady?: number; // Transform phase: units whose upgrade branch is pushed
}

interface MigrationRunProgress {
  phases: MigrationPhase[];
  rejected_by_code?: Record<string, number>;
  total_selected?: number | null;   // from the decoded prepare-batch result
  accepted?: number | null;
}

// The durable Result of one batch run (migrate.summarize_migration keys).
export interface MigrationBatchSummary {
  batch_id: string;
  started_at: string;
  total_selected: number;
  over_cap_skipped: number;
  resolved: number;
  rejected: number;
  rejected_by_code: Record<string, number>;
  branches_ready: number;
  no_changes: number;
  failed: number;
}

export interface MigrationBatchStatus {
  executionArn: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'STOPPED';
  startDate?: Date;
  stopDate?: Date;
  summary?: MigrationBatchSummary;
  progress?: MigrationRunProgress;
}

// ONE call per Migrate click, whatever the selection size. Each function that
// transforms cleanly ends with its upgrade branch pushed; nothing is merged.
export const startMigrationBatch = async (
  functionArns: string[],
): Promise<{ executionArn: string; batchSize: number; overCap: number; skipped: string[] }> => {
  const r = await callApi('POST', '/migrate-batch', { function_arns: functionArns });
  return {
    executionArn: r.executionArn,
    batchSize: r.batchSize ?? functionArns.length,
    overCap: r.overCap ?? 0,
    skipped: r.skipped ?? [],
  };
};

// Poll one batch migration run's status + phases. The arn is appended to the
// greedy {arn+} route path. startDate/stopDate map the same way as getScanStatus.
export const getMigrationBatchStatus = async (
  executionArn: string,
): Promise<MigrationBatchStatus> => {
  const r = await callApi('GET', `/migrate-run/${executionArn}`);
  return {
    executionArn,
    status: r.status || 'RUNNING',
    startDate: r.startDate ? new Date(r.startDate) : undefined,
    stopDate: r.stopDate ? new Date(r.stopDate) : undefined,
    summary: r.summary || undefined,
    progress: r.progress || undefined,
  };
};

// One recent migration BATCH execution, read-only. Two consumers: the My
// functions Steps card re-attaches to the most recent batch on page load
// (limit 1) and the Activity log lists one entry per batch (limit 10).
export interface MigrationBatchRecord {
  executionArn: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'STOPPED';
  startedAt?: Date;
  finishedAt?: Date;
  summary?: MigrationBatchSummary;
}

// List the recent migration batch executions, newest first, capped at 10.
// Returns [] (not an error) when migration is not deployed.
export const getRecentMigrationRuns = async (limit = 10): Promise<MigrationBatchRecord[]> => {
  const r = await callApi('GET', `/migrate-runs?limit=${limit}`);
  const runs: any[] = r.runs || [];
  return runs.map((m) => ({
    executionArn: m.executionArn,
    status: m.status || 'RUNNING',
    startedAt: m.startedAt ? new Date(m.startedAt) : undefined,
    finishedAt: m.finishedAt ? new Date(m.finishedAt) : undefined,
    summary: m.summary || undefined,
  }));
};
