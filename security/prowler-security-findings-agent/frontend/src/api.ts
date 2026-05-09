/**
 * Dashboard API client.
 *
 * The backend is a Lambda Function URL with IAM auth. We sign every request
 * with SigV4 using temporary credentials from the Cognito Identity Pool —
 * exact same auth chain as `operations-automation/ai-lambda-runtime-migration/`
 * uses to call AgentCore (Cognito User Pool → ID Token → Identity Pool →
 * AWS Credentials → SigV4).
 */

import { CognitoIdentityClient } from '@aws-sdk/client-cognito-identity';
import { fromCognitoIdentityPool } from '@aws-sdk/credential-provider-cognito-identity';
// Use @smithy/signature-v4 (not the older @aws-sdk/signature-v4@3.370) — the
// older package canonicalises paths with encodeURIComponent which leaves
// !'()* literal, while AWS Lambda URL decodes-and-re-canonicalises with
// full RFC 3986 (*.→ %2A, etc.), producing a 403 on any finding whose uid
// contains one of those 5 chars. @smithy/signature-v4 uses escapeUri which
// matches AWS exactly.
import { SignatureV4 } from '@smithy/signature-v4';
import { HttpRequest } from '@smithy/protocol-http';
import { Sha256 } from '@aws-crypto/sha256-js';
import { getIdToken } from './auth';

const region = import.meta.env.VITE_REGION || 'us-east-1';
const identityPoolId = import.meta.env.VITE_IDENTITY_POOL_ID;
const userPoolId = import.meta.env.VITE_USER_POOL_ID;
const apiUrl = import.meta.env.VITE_API_FUNCTION_URL;

// Cache the Cognito client + credentials provider so concurrent requests share
// one in-memory credential fetch. Creating a fresh provider per call meant two
// parallel bulk-action requests raced on GetCredentialsForIdentity, and one
// would fail — visible as "Investigations dispatched: 1 succeeded · 1 failed".
let _cognitoClient: CognitoIdentityClient | null = null;
let _providerCache: { idToken: string; provider: ReturnType<typeof fromCognitoIdentityPool> } | null = null;

async function credentialsProvider() {
  const idToken = await getIdToken();
  if (!idToken) throw new Error('Not authenticated');
  if (_providerCache && _providerCache.idToken === idToken) {
    return _providerCache.provider;
  }
  if (!_cognitoClient) _cognitoClient = new CognitoIdentityClient({ region });
  const provider = fromCognitoIdentityPool({
    client: _cognitoClient,
    identityPoolId,
    logins: {
      [`cognito-idp.${region}.amazonaws.com/${userPoolId}`]: idToken,
    },
  });
  _providerCache = { idToken, provider };
  return provider;
}

// RFC 3986 path escaping matching what AWS Lambda URL expects on the wire.
// Full-unreserved mode: everything except A-Z a-z 0-9 - _ . ~ gets %-encoded,
// and we then restore `/` as a path separator. This is the same transform
// @smithy/signature-v4 uses internally for the canonical path, so the signer
// and the wire value agree down to the last character.
function escapeUriPath(path: string): string {
  const enc = (s: string) =>
    encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
  return enc(path).replace(/%2F/g, '/');
}

async function signedFetch(method: 'GET' | 'POST' | 'DELETE', path: string, body?: unknown): Promise<any> {
  if (!apiUrl) throw new Error('VITE_API_FUNCTION_URL not configured');
  const u = new URL(path.startsWith('http') ? path : `${apiUrl.replace(/\/$/, '')}${path}`);

  const credsFactory = await credentialsProvider();
  const creds = await credsFactory();
  const signer = new SignatureV4({
    credentials: creds,
    region,
    service: 'lambda',
    sha256: Sha256,
    uriEscapePath: true,
    applyChecksum: true,
  });

  // SigV4 (non-S3) expects DOUBLE URI encoding in the canonical path: the
  // signer receives the path as it will appear on the wire (already once-
  // encoded) and encodes it a second time for the string-to-sign. AWS on
  // the receiving side does the same: takes the wire path, encodes it once,
  // and that's its canonical. So the wire path must be exactly the once-
  // encoded string we hand to the signer.
  let decodedPath: string;
  try {
    decodedPath = decodeURIComponent(u.pathname);
  } catch {
    decodedPath = u.pathname;
  }
  const wirePath = escapeUriPath(decodedPath);

  const query: Record<string, string> = {};
  u.searchParams.forEach((v, k) => { query[k] = v; });

  const headers: Record<string, string> = { host: u.hostname };
  if (body !== undefined) headers['content-type'] = 'application/json';

  const req = new HttpRequest({
    method,
    protocol: u.protocol,
    hostname: u.hostname,
    path: wirePath,
    query,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const signed = await signer.sign(req);

  const finalUrl = `${u.protocol}//${u.hostname}${wirePath}${u.search}`;
  const response = await fetch(finalUrl, {
    method: signed.method,
    headers: signed.headers as Record<string, string>,
    body: signed.body,
  });
  if (!response.ok) {
    throw new Error(`API ${method} ${path} failed: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

export interface Finding {
  finding_uid: string;
  check_id: string;
  check_title: string;
  check_description?: string;
  status_extended?: string;
  severity: string;
  status: string;
  service_name: string;
  resource_uid: string;
  region?: string;
  account_id?: string;
  compliance_frameworks?: string[];
  /** Full mapping framework → control IDs (populated since PR #21). */
  compliance_controls?: Record<string, string[]>;
  scan_id?: string;
  last_seen_at?: string;
  remediation_s3_key?: string;
  remediation_presigned_url?: string;
  /** Bedrock-generated remediation markdown, inlined by the detail endpoint. */
  remediation_markdown?: string;
  remediation_generated_at?: string;
  /** Prowler-native remediation guidance (from OCSF `remediation.desc`). */
  remediation_guidance?: string;
  /** Primary Prowler Hub documentation URL for the check. */
  remediation_url?: string;
  /** Additional reference URLs (AWS docs, vendor docs) from Prowler. */
  additional_urls?: string[];
  /** Functional categories Prowler groups the check under. */
  categories?: string[];
  /** Free-form note Prowler attaches (typically the AWS pillar). */
  notes?: string;
  /** ASFF-style types the finding maps to in Security Hub. */
  finding_types?: string[];
  /** Prowler risk description in business language. */
  risk_details?: string;
  /** Timestamp of the first scan that ever produced this finding_uid. */
  first_seen_at?: string;
  /** Per-scan status trail (last 20 entries) — used to compute fixed/regressed badges. */
  status_history?: Array<{ scan_id: string; status: string; last_seen_at: string }>;
  /** Present when the finding has been suppressed through the dashboard. */
  suppressed_at?: string;
  suppress_reason?: string;
  suppressed_by?: string;
  /** Truncated OCSF JSON from Prowler — only returned by the detail endpoint. */
  raw?: string;
}

export const listFindings = (filters: { severity?: string; status?: string; limit?: number } = {}) => {
  const qs = new URLSearchParams();
  if (filters.severity) qs.set('severity', filters.severity);
  if (filters.status) qs.set('status', filters.status);
  if (filters.limit) qs.set('limit', String(filters.limit));
  const path = '/findings' + (qs.toString() ? `?${qs}` : '');
  return signedFetch('GET', path) as Promise<{ items: Finding[]; count: number }>;
};

export const getFinding = (findingUid: string) =>
  signedFetch('GET', `/findings/${encodeURIComponent(findingUid)}`) as Promise<Finding>;

export const listScans = () =>
  signedFetch('GET', '/scans') as Promise<{ scans: Array<{ scan_id: string; last_seen_at: string }> }>;

export interface RunningTask {
  taskArn: string;
  lastStatus: string;
  desiredStatus: string;
  createdAt?: string;
  startedAt?: string;
  stoppedAt?: string;
  stoppedReason?: string;
}

export const listRunningScans = () =>
  signedFetch('GET', '/scans/running') as Promise<{ tasks: RunningTask[] }>;

export const runScan = () =>
  signedFetch('POST', '/scans') as Promise<{ task_arns: string[] }>;

export interface AgentTask {
  taskId: string;
  executionId?: string;
  title: string;
  status: string;
  priority?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface AgentJournalRecord {
  timestamp?: string;
  type?: string;
  content?: string;
}

export interface InvestigationState {
  incidentId: string;
  status: 'idle' | 'pending' | 'in_progress' | 'completed' | 'not_configured' | 'error';
  agentSpaceId?: string;
  executionId?: string;
  tasks: AgentTask[];
  journal: AgentJournalRecord[];
  error?: string;
}

export const investigateFinding = (findingUid: string) =>
  signedFetch('POST', `/findings/${encodeURIComponent(findingUid)}/investigate`) as Promise<{
    incidentId: string;
    message: string;
  }>;

export const generateInsights = (findingUid: string) =>
  signedFetch('POST', `/findings/${encodeURIComponent(findingUid)}/insights`) as Promise<{
    remediation_s3_key: string;
    remediation_markdown: string;
  }>;

export const getInvestigation = (findingUid: string) =>
  signedFetch('GET', `/findings/${encodeURIComponent(findingUid)}/investigation`) as Promise<InvestigationState>;

export const suppressFinding = (findingUid: string, reason: string) =>
  signedFetch('POST', `/findings/${encodeURIComponent(findingUid)}/suppress`, { reason }) as Promise<{
    finding_uid: string;
    suppressed_at: string;
    reason: string;
  }>;

export const unsuppressFinding = (findingUid: string) =>
  signedFetch('DELETE', `/findings/${encodeURIComponent(findingUid)}/suppress`) as Promise<{
    finding_uid: string;
    suppressed: false;
  }>;

export interface InvestigationSummary {
  finding_uid: string;
  incidentId: string;
  taskId?: string;
  executionId?: string;
  status?: string;
  priority?: string;
  title?: string;
  createdAt?: string;
  updatedAt?: string;
  check_id?: string;
  check_title?: string;
  severity?: string;
  service_name?: string;
  resource_uid?: string;
}

export const listInvestigations = () =>
  signedFetch('GET', '/investigations') as Promise<{
    investigations: InvestigationSummary[];
    agentSpaceId?: string;
    error?: string;
  }>;

export interface CostEvent {
  event_id: string;
  created_at: string;
  event_type: 'bedrock_insights' | 'devops_agent_dispatch' | 'scan' | string;
  cost_usd: number;
  finding_uid?: string;
  model_id?: string;
  input_tokens?: number;
  output_tokens?: number;
  duration_ms?: number;
  user?: string;
  metadata?: Record<string, unknown>;
}

export interface CostSummary {
  total_usd: number;
  total_events: number;
  total_input_tokens: number;
  total_output_tokens: number;
  by_type: Record<string, { count: number; cost_usd: number }>;
  error?: string;
}

export const listCostEvents = (limit = 100) =>
  signedFetch('GET', `/cost/events?limit=${limit}`) as Promise<{
    events: CostEvent[];
    count: number;
  }>;

export const getCostSummary = () =>
  signedFetch('GET', '/cost/summary') as Promise<CostSummary>;
