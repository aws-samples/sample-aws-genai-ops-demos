import { getIdToken, signOut } from './auth';

export class ApiError extends Error {
  status: number;
  /** run_id carried by a 409 body (the scan already running). */
  runId?: string;
  constructor(status: number, message: string, runId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.runId = runId;
  }
}

export type RunStatus = 'RUNNING' | 'SUCCEEDED' | 'FAILED';
export type StepStatus = 'pending' | 'in-progress' | 'success' | 'error';
export type ReportFormat = 'json' | 'xlsx' | 'html';

export interface Run {
  run_id: string;
  status: RunStatus;
  started_at: string;
  finished_at?: string;
  scope?: string;
  accounts?: { id: string; name: string; outcome: string }[];
  buckets?: number;
  regions?: number;
  recs?: { HIGH: number; MEDIUM: number; LOW: number };
  ai?: { classification: boolean; narrative: string };
  error?: string;
}

export interface Step {
  label: string;
  status: StepStatus;
  detail?: string;
}

export interface RunDetail {
  run: Run;
  steps: Step[];
}

let onUnauthorized: (() => void) | null = null;

/** The app registers a callback that returns to the sign-in page. */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

const STATUS_TEXT: Record<number, string> = {
  400: 'The request was not valid.',
  403: 'You do not have access to this.',
  404: 'Not found.',
};
const SERVER_TEXT = 'Something went wrong on the server. Try again.';

async function errorInfo(res: Response): Promise<{ message: string; runId?: string }> {
  let body: { error?: unknown; message?: unknown; run_id?: unknown } = {};
  try {
    body = (await res.json()) ?? {};
  } catch {
    // no JSON body
  }
  const runId = typeof body.run_id === 'string' ? body.run_id : undefined;
  if (res.status === 409) {
    const text = body.error ?? body.message;
    return { message: typeof text === 'string' && text ? text : 'A scan is already running.', runId };
  }
  if (res.status >= 500) return { message: SERVER_TEXT };
  if (STATUS_TEXT[res.status]) return { message: STATUS_TEXT[res.status] };
  const text = body.error ?? body.message;
  return { message: typeof text === 'string' && text ? text : `Request failed (${res.status}).`, runId };
}

function unauthorized(): ApiError {
  signOut();
  onUnauthorized?.();
  return new ApiError(401, 'Your session has ended. Sign in again.');
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getIdToken();
  if (!token) throw unauthorized();
  const headers = new Headers(init.headers);
  headers.set('Authorization', token);
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) throw unauthorized();
  if (!res.ok) {
    const info = await errorInfo(res);
    throw new ApiError(res.status, info.message, info.runId);
  }
  return (await res.json()) as T;
}

export const startScan = () => request<{ run_id: string }>('/api/scans', { method: 'POST' });
export const listRuns = () => request<{ runs: Run[] }>('/api/scans');
export const getRun = (id: string) => request<RunDetail>(`/api/scans/${encodeURIComponent(id)}`);
export const reportUrl = (id: string, format: ReportFormat) =>
  request<{ url: string; expires_in: number }>(
    `/api/scans/${encodeURIComponent(id)}/report?format=${format}`,
  );
export const getSample = <T = unknown>() => request<T>('/api/sample');

/** Plain fetch of a presigned URL: no Authorization header. */
export async function fetchReport<T = unknown>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new ApiError(res.status, `The report could not be loaded (${res.status}).`);
  return (await res.json()) as T;
}
