// API service for AWS Services Lifecycle Tracker Admin UI (issue #139)
//
// Every call goes over HTTPS to the HTTP API deployed by the Api stack, with
// the Cognito ID token in the Authorization header (validated by the API's
// JWT authorizer). The browser holds no AWS credentials.
//
//   POST /actions          -> router actions (read/write DynamoDB, action plans)
//   POST /refresh          -> start (or adopt) the durable refresh pipeline
//   GET  /refresh/{arn}    -> pipeline execution status for polling / re-attach
import { getIdToken } from './auth';

const apiUrl: string = ((import.meta as any).env?.VITE_API_URL || '').replace(/\/$/, '');
// Types
export interface ServiceConfig {
  service_name: string;
  name: string;
  enabled: boolean;
  documentation_urls: string[];
  extraction_focus: string;
  schema_key: string;
  item_properties: Record<string, string>;
  required_fields?: string[];
  last_extraction: string;
  extraction_count: number;
  success_rate: number;
  last_refresh_origin?: string;
  last_extraction_duration?: number;  // Duration in seconds
}

// Statuses the backend can actually store. The extraction pipeline emits
// deprecated / extended_support / end_of_life / end_of_support_date (see
// categorize_item_status), and account discovery can also write supported /
// unknown inventory rows into the same table.
export type LifecycleStatus =
  | 'deprecated'
  | 'extended_support'
  | 'end_of_life'
  | 'end_of_support_date'
  | 'supported'
  | 'unknown';

// Statuses that represent an actual lifecycle concern (shown on the
// Deprecations page). Excludes 'supported'/'unknown' inventory rows.
export const DEPRECATION_STATUSES: LifecycleStatus[] = [
  'deprecated',
  'extended_support',
  'end_of_life',
  'end_of_support_date',
];

export interface DeprecationItem {
  service_name: string;
  item_id: string;            // facts: '<schema_key>#<id>'; inventory: 'inventory#<id>'
  status: LifecycleStatus;
  source_url: string;
  extraction_date: string;
  last_verified: string;
  region?: string;            // inventory rows: region that was scanned (issue #141)
  provenance?: string;        // inventory rows: 'account_discovery'
  service_specific: Record<string, any>;
}

export interface ScannerInfo {
  label: string;
  service_keys: string[];
}

// Outcome of the AWS Health cross-check run with the last scan (#141)
export interface HealthCheckStatus {
  available: boolean;
  reason: string | null;
  checked_at: string | null;
  events: number;
  flagged_resources: number;
}

export interface ScanCoverage {
  scanners: ScannerInfo[];
  last_scan: { last_verified: string | null; resources: number; regions: string[] };
  health: HealthCheckStatus | null;
}

export interface DashboardMetrics {
  total_services: number;
  enabled_services: number;
  total_items: number;
  by_status: {
    deprecated: number;
    extended_support: number;
    end_of_life: number;
  };
  by_service: Record<string, number>;  // Add per-service item counts
  recent_extractions: Array<{
    service_name: string;
    timestamp: string;
    success: boolean;
  }>;
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

// API Functions

export const getServices = async (): Promise<ServiceConfig[]> => {
  const result = await invokeAction({ action: 'list_services' });
  return result.services || [];
};

export const getDeprecations = async (filters?: {
  service?: string;
  status?: string;
  limit?: number;
}): Promise<DeprecationItem[]> => {
  const result = await invokeAction({ action: 'list_deprecations', filters });
  return result.items || [];
};

// The same rows split by origin: what AWS publishes vs what was found in the
// account (issue #141). One call, two lenses.
export const getLifecycleData = async (): Promise<{ facts: DeprecationItem[]; inventory: DeprecationItem[] }> => {
  const all = await getDeprecations();
  return {
    facts: all.filter((i) => !i.item_id.startsWith('inventory#')),
    inventory: all.filter((i) => i.item_id.startsWith('inventory#')),
  };
};

export const getScanners = async (): Promise<ScanCoverage> => {
  const result = await invokeAction({ action: 'list_scanners' });
  return {
    scanners: result.scanners || [],
    last_scan: result.last_scan || { last_verified: null, resources: 0, regions: [] },
    health: result.health || null,
  };
};

// --- Refresh pipeline (Lambda durable function) -----------------------------
// The whole refresh (web extraction -> account scan -> reconcile -> notify)
// runs server-side as one durable execution. The UI only starts and observes
// it, so closing the tab never kills a run.

export interface RefreshPhaseSummary {
  total: number;
  succeeded: number;
  failed: string[];
  items_extracted: number;
}

export interface ScanPhaseSummary {
  cells_total: number;
  cells_succeeded: number;
  failed_cells: string[];
  items_discovered: number;
  needs_attention: number;
}

export interface RefreshSummary {
  run_id: string;
  mode: 'full' | 'extract' | 'scan';
  refresh_origin: string;
  started_at: string;
  finished_at: string;
  extract: RefreshPhaseSummary;
  scan: ScanPhaseSummary;
  inventory?: { items_saved?: number; stale_removed?: number; [key: string]: unknown };
}

export interface RefreshProgress {
  extract_done: number;
  scan_done: number;
}

export interface RefreshExecutionStatus {
  executionArn: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'STOPPED';
  startDate?: Date;
  stopDate?: Date;
  // Present on SUCCEEDED: the pipeline's final summary
  summary?: RefreshSummary;
  progress?: RefreshProgress;
  error?: unknown;
}

export interface RefreshRequest {
  mode?: 'full' | 'extract' | 'scan';
  services?: string[];
  regions?: string[];
}

// Start a refresh. If one is already running, the API adopts it instead of
// starting a second one.
export const startRefresh = async (
  request: RefreshRequest = {}
): Promise<{ executionArn: string; alreadyRunning: boolean }> => {
  const result = await callApi('POST', '/refresh', { refresh_origin: 'manual', ...request });
  return { executionArn: result.executionArn, alreadyRunning: !!result.alreadyRunning };
};

// Full end-to-end refresh: all enabled services, all scanners.
export const startRefreshAll = () => startRefresh({ mode: 'full' });

// Poll the status of a refresh execution.
export const getRefreshStatus = async (executionArn: string): Promise<RefreshExecutionStatus> => {
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

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Start a refresh and wait for it to finish (polling). Used for scoped runs
// such as a single-service extraction from the Services page.
export const runRefreshAndWait = async (
  request: RefreshRequest,
  pollIntervalMs = 5000
): Promise<RefreshExecutionStatus> => {
  const { executionArn, alreadyRunning } = await startRefresh(request);
  if (alreadyRunning) {
    throw new Error('A refresh is already running - wait for it to finish before starting another one.');
  }
  for (;;) {
    await sleep(pollIntervalMs);
    const status = await getRefreshStatus(executionArn);
    if (status.status !== 'RUNNING') {
      return status;
    }
  }
};

// Re-extract one service's deprecation facts from the AWS documentation.
// Runs through the pipeline (mode=extract) because a single extraction can
// take minutes, well beyond the API's 30 s request limit.
export const triggerExtraction = async (serviceName: string): Promise<RefreshExecutionStatus> => {
  const status = await runRefreshAndWait({ mode: 'extract', services: [serviceName] });
  if (status.status !== 'SUCCEEDED') {
    throw new Error(`Extraction ended with status ${status.status}`);
  }
  const failed = status.summary?.extract?.failed || [];
  if (failed.includes(serviceName)) {
    throw new Error(`Extraction failed for ${serviceName}`);
  }
  return status;
};
export const getDashboardMetrics = async (): Promise<DashboardMetrics> => {
  const result = await invokeAction({
    action: 'get_metrics'
  });

  return result.metrics || {
    total_services: 0,
    enabled_services: 0,
    total_items: 0,
    by_status: { deprecated: 0, extended_support: 0, end_of_life: 0 },
    recent_extractions: []
  };
};

export const updateServiceConfig = async (serviceName: string, updates: Partial<ServiceConfig>): Promise<void> => {
  await invokeAction({
    action: 'update_service',
    service_name: serviceName,
    updates
  });
};

// Action Plan Types
export interface ActionPlan {
  plan_id: string;
  service_name: string;
  item_id: string;
  item_name: string;
  owner: string;
  plan_status: 'not_started' | 'in_progress' | 'completed' | 'blocked';
  priority: 'low' | 'medium' | 'high' | 'critical';
  target_date: string;
  notes: string;
  created_at: string;
  updated_at: string;
  created_by: string;
}

// Action Plan API Functions
export const getActionPlans = async (filters?: {
  owner?: string;
  plan_status?: string;
}): Promise<ActionPlan[]> => {
  const result = await invokeAction({
    action: 'list_action_plans',
    filters
  });
  return result.plans || [];
};

export const getActionPlan = async (planId: string): Promise<ActionPlan | null> => {
  const result = await invokeAction({
    action: 'get_action_plan',
    plan_id: planId
  });
  return result.plan || null;
};

export const createActionPlan = async (data: {
  service_name: string;
  item_id: string;
  item_name?: string;
  owner: string;
  plan_status?: string;
  priority?: string;
  target_date?: string;
  notes?: string;
}): Promise<{ success: boolean; plan?: ActionPlan; error?: string }> => {
  return await invokeAction({
    action: 'create_action_plan',
    ...data
  });
};

export const updateActionPlan = async (
  planId: string,
  updates: Partial<ActionPlan>
): Promise<{ success: boolean; plan?: ActionPlan; error?: string }> => {
  return await invokeAction({
    action: 'update_action_plan',
    plan_id: planId,
    updates
  });
};

export const deleteActionPlan = async (
  planId: string
): Promise<{ success: boolean; error?: string }> => {
  return await invokeAction({
    action: 'delete_action_plan',
    plan_id: planId
  });
};
