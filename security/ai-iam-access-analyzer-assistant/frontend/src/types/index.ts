export interface Message {
  role: "user" | "assistant";
  content: string;
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
  };
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
