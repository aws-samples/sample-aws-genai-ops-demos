/**
 * G.O.A.T. - GenAI Operations Analytics Tool
 * Core TypeScript interfaces and types
 *
 * These types define the data contracts between the orchestration agent,
 * sub-agents, frontend, and DynamoDB persistence layer.
 */

// ---------------------------------------------------------------------------
// Orchestration Layer
// ---------------------------------------------------------------------------

/** Payload sent from the frontend to the Orchestration Agent */
export interface OrchestrationRequest {
  prompt: string;
  accountContext?: string; // Target account ID for cross-account queries
}

/** Internal routing decision produced by the Orchestration Agent's LLM */
export interface SubAgentRoute {
  agentId: string;
  domain: 'cost' | 'health' | 'support' | 'trusted_advisor' | 'cur';
  query: string;
}

/** Structured JSON payload sent from the orchestration agent's @tool functions to a sub-agent */
export interface SubAgentRequest {
  action: string;
  params?: Record<string, any>;
  accountId?: string;
}

/** Structured response returned by every sub-agent handler */
export interface SubAgentResponse {
  success: boolean;
  domain: string;
  data: Record<string, any>;
  formattedText: string;
  metadata: {
    sourceApi: string;
    queryTimestamp: string;
    dataFreshness: string;
  };
}

// ---------------------------------------------------------------------------
// DynamoDB Persistence – Conversations
// ---------------------------------------------------------------------------

/** A single message within a conversation */
export interface Message {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  metadata?: {
    sourceDomains: string[];
    subAgentsUsed: string[];
  };
}

/** DynamoDB item representing a conversation */
export interface ConversationItem {
  PK: string;           // USER#<userId>
  SK: string;           // CONV#<conversationId>
  title: string;
  createdAt: string;    // ISO 8601
  updatedAt: string;    // ISO 8601
  status: 'active' | 'archived';
  messages: Message[];
  TTL: number;          // 90-day expiry epoch seconds
}

// ---------------------------------------------------------------------------
// DynamoDB Persistence – Knowledge Articles
// ---------------------------------------------------------------------------

/** DynamoDB item representing a knowledge article */
export interface KnowledgeArticleItem {
  PK: string;           // ARTICLE#<articleId>
  SK: string;           // META
  GSI1PK: string;       // CATEGORY#<category>
  GSI1SK: string;       // <createdAt>
  title: string;
  category: string;
  sourceAgents: string[];
  originalQuery: string;
  content: string;
  createdAt: string;
  createdBy: string;
  tags: string[];
}

// ---------------------------------------------------------------------------
// DynamoDB Persistence – User Preferences
// ---------------------------------------------------------------------------

/** DynamoDB item representing user preferences */
export interface UserPreferencesItem {
  PK: string;           // USER#<userId>
  SK: string;           // PREFS
  defaultAccount: string;
  preferredTemplates: string[];
  displaySettings: {
    theme: 'light' | 'dark';
    responseFormat: 'detailed' | 'summary';
    chartType: 'bar' | 'line';
  };
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Prompt Templates
// ---------------------------------------------------------------------------

/** A single parameter within a prompt template */
export interface TemplateParameter {
  name: string;
  label: string;
  type: 'text' | 'date' | 'select';
  required: boolean;
  options?: string[];     // For select type
  defaultValue?: string;
}

/** A pre-configured prompt template */
export interface PromptTemplate {
  id: string;
  category: 'health' | 'trusted_advisor' | 'support' | 'cost';
  title: string;
  description: string;
  template: string;       // Template text with {{parameter}} placeholders
  parameters: TemplateParameter[];
}

// ---------------------------------------------------------------------------
// Cost Agent Handler Parameters
// ---------------------------------------------------------------------------

/** Parameters for handle_cost_and_usage */
export interface CostAndUsageParams {
  startDate: string;    // YYYY-MM-DD, max 12 months range
  endDate: string;
  granularity: 'DAILY' | 'MONTHLY';
  groupBy?: string[];   // SERVICE, REGION, etc.
  filter?: Record<string, string>;
}

/** Parameters for handle_cost_forecast */
export interface CostForecastParams {
  startDate: string;
  endDate: string;
  granularity: 'DAILY' | 'MONTHLY';
  metric: 'BLENDED_COST' | 'UNBLENDED_COST' | 'AMORTIZED_COST';
}

/** Parameters for handle_recommendations */
export interface RecommendationsParams {
  category?: 'cost_optimizing' | 'security' | 'performance';
  maxResults?: number;
}

// ---------------------------------------------------------------------------
// Health Agent Handler Parameters
// ---------------------------------------------------------------------------

/** Parameters for handle_describe_events */
export interface HealthEventsParams {
  region?: string;
  service?: string;
  eventTypeCategory?: 'issue' | 'accountNotification' | 'scheduledChange';
  startTime?: string;
  endTime?: string;
}

/** Parameters for handle_affected_entities */
export interface AffectedEntitiesParams {
  eventArn: string;
}

/** Parameters for handle_event_details */
export interface EventDetailsParams {
  eventArn: string;
}

// ---------------------------------------------------------------------------
// Trusted Advisor Agent Handler Parameters
// ---------------------------------------------------------------------------

/** Parameters for handle_describe_checks */
export interface TAChecksParams {
  pillar?: 'cost_optimizing' | 'security' | 'performance' | 'fault_tolerance' | 'service_limits';
  language?: string;
}

/** Parameters for handle_check_result */
export interface TACheckResultParams {
  checkId: string;
}

/** Parameters for handle_list_recommendations */
export interface TARecommendationsParams {
  pillar?: string;
  status?: 'ok' | 'warning' | 'error';
}

// ---------------------------------------------------------------------------
// CUR Agent Handler Parameters
// ---------------------------------------------------------------------------

/** Parameters for handle_query_cur */
export interface CURQueryParams {
  sqlQuery: string;     // Athena SQL query
  database: string;
  outputLocation?: string;
}

/** Parameters for handle_resource_costs */
export interface ResourceCostsParams {
  resourceId: string;
  startDate: string;
  endDate: string;
}

/** Parameters for handle_usage_patterns */
export interface UsagePatternsParams {
  service: string;
  startDate: string;
  endDate: string;
  groupBy?: string;
}
