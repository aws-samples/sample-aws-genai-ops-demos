export interface Message {
  role: "user" | "assistant";
  content: string;
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
  };
  /**
   * Tools the backend called to construct this assistant message. Rendered
   * as a Cloudscape ExpandableSection + Steps ("Thinking pattern") in
   * `MessageBubble.tsx` per #167 Req 5.
   */
  toolsUsed?: Array<{ tool: string; input_summary?: string }>;
  /**
   * Wall-clock duration of the /conversation request that produced this
   * assistant message, in whole seconds. Used as the header text of the
   * Thinking ExpandableSection ("Thought for Ns"). Absent when the
   * request completed instantly or the client couldn't measure it.
   */
  durationSeconds?: number;
  /**
   * Local (client-only, this-session-only) record of the user's helpful /
   * not-helpful vote on this response. Server-side telemetry for these
   * votes is deferred — see the spec's DD-4. Only applies to assistant
   * messages.
   */
  feedback?: "helpful" | "not-helpful";
  /**
   * When set to "error", the message is rendered as an inline Cloudscape
   * `<Alert type="error">` inside the transcript at the position of the
   * failed turn, instead of as a ChatBubble. Used for API errors,
   * timeouts, and other request failures per #167 Req 7.
   */
  kind?: "error";
  /**
   * For error messages: the user prompt that triggered the failing
   * request. Passed back through the Try again action so the message
   * can be re-sent without the user re-typing it.
   */
  retryPrompt?: string;
}

/**
 * One row of the session-start capability probe (#171 phase C).
 *
 * The assistant renders one pill per entry in the "Data sources" status
 * line so the user can see — before typing anything — which AWS sources
 * were reachable in this account/region and which were not.
 */
export interface CoverageEntry {
  source: "securityhub" | "accessanalyzer" | "cloudtrail" | string;
  state: "checked" | "unavailable";
  detail: string;
}

/**
 * Response body of ``GET /capabilities``. Called once when the chat opens.
 *
 * - ``region``: the region the Lambda ran in (surface it so a wrong-region
 *   deploy is obvious to the user).
 * - ``coverage``: structural per-source status used for the pill row.
 * - ``welcome_message``: server-composed prose describing what CAN and
 *   what CANNOT be seen — prepended to the greeting bubble so the user
 *   reads the honesty statement before the feature list.
 */
export interface Capabilities {
  region: string;
  coverage: CoverageEntry[];
  welcome_message: string;
}

export interface Finding {
  id: string;
  title: string;
  description: string;
  severity: string;
  severity_normalized?: number;
  resource_type: string;
  resource_id: string;
  resource_region?: string;
  status: string;
  finding_type?: string;
  created_at: string;
  updated_at?: string;
  recommendation: string;
  recommendation_url?: string;
  account_id?: string;
}

export interface PolicyResult {
  role_name: string;
  role_arn: string;
  analysis_period_days: number;
  events_analyzed: number;
  unique_actions_used: number;
  unique_services_used: number;
  proposed_policy: Record<string, unknown>;
  formatted_policy: string;
  output_format: string;
  reduction_metrics: {
    current_actions: number;
    proposed_actions: number;
    removed_actions: number;
    reduction_percentage: number;
    attack_surface_reduction: string;
    risk_level: string;
  };
  unused_permissions: string[];
  warnings?: string[];
}

export interface DependencyResult {
  entity_arn: string;
  entity_type: string;
  entity_name: string;
  direct_dependents: Array<{
    type: string;
    name: string;
    is_service_linked?: boolean;
    impact?: string;
  }>;
  trust_relationships: Array<{
    principal_type?: string;
    principal: string;
    conditions?: Record<string, unknown>;
    risk?: string;
  }>;
  policy_attachments: Array<{
    policy_name: string;
    policy_arn?: string;
    type?: string;
    is_aws_managed?: boolean;
  }>;
  dependency_graph: {
    root: string;
    depends_on: string[];
    depended_on_by: string[];
    total_impact_radius: number;
  };
  risk_score: {
    score: number;
    level: string;
    factors: string[];
    recommendation: string;
  };
  warnings: string[];
}

export interface ValidationResult {
  is_valid: boolean;
  findings: Array<{
    type: string;
    severity: string;
    message: string;
    source?: string;
    issue_code?: string;
    action?: string;
  }>;
  security_analysis: {
    wildcards: Array<{ issue: string }>;
    dangerous_patterns: Array<{ action: string; description: string }>;
    missing_conditions: Array<{ action: string; message: string }>;
  };
  summary: {
    errors: number;
    warnings: number;
    suggestions: number;
    total_findings: number;
    verdict: string;
  };
}

// Access-key triage (#175). Shape mirrors the Python tool response from
// src/tools/triage_access_keys.py exactly — snake_case fields, plain string
// enums, inline-nested objects, in keeping with the other tool payload types
// above.
export interface AccessKeyRow {
  account_id: string;
  user: string;
  is_root: boolean;
  key_id: string;
  status: string; // "Active" | "Inactive"
  created: string;
  key_age_days: number | null;
  last_used: string; // ISO timestamp, "NEVER", or "UNKNOWN"
  last_used_service: string;
  actions: string;
  policies: string;
  resource_scope: string; // "WILDCARD" | "SCOPED" | "MIXED" | "NONE"
  has_condition: boolean;
  risk_flags: string[];
  priority_class: string; // "Critical" | "High" | "Cleanup" | "Rotation"
  suggested_remediation: string;
  // Canonical AWS docs URL for the remediation, emitted by the Python tool
  // via `_REMEDIATION_DOCS`. Empty string when the tool can't map the label
  // (unknown remediation), in which case the frontend renders plain text
  // instead of a link. Never construct URLs client-side.
  suggested_remediation_url?: string;
}

export interface AccessKeysReport {
  keys: AccessKeyRow[];
  summary: {
    Critical?: number;
    High?: number;
    Cleanup?: number;
    Rotation?: number;
    total_keys: number;
    users_with_keys: number;
  };
  coverage: Array<{
    source: string;
    state: string; // "checked" | "empty" | "unavailable"
    detail?: string;
    count?: number;
  }>;
  usage_lag_caveat: string;
}
