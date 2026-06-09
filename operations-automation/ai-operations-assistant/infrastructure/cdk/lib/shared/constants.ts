/**
 * G.O.A.T. - GenAI Operations Analytics Tool
 * Shared constants used across CDK stacks, agents, and frontend
 */

// ---------------------------------------------------------------------------
// Operational Domains
// ---------------------------------------------------------------------------

/** Supported sub-agent domains */
export const DOMAINS = ['cost', 'health', 'support', 'trusted_advisor', 'cur'] as const;
export type Domain = typeof DOMAINS[number];

// ---------------------------------------------------------------------------
// Amazon Bedrock Model IDs
// ---------------------------------------------------------------------------

/** Nova Pro -- used by the Orchestration Agent for complex reasoning */
export const MODEL_ID_NOVA_PRO = 'amazon.nova-pro-v1:0';

/** Nova Lite -- used by sub-agents for simpler retrieve-and-format tasks */
export const MODEL_ID_NOVA_LITE = 'amazon.nova-lite-v1:0';

// ---------------------------------------------------------------------------
// Timeouts & TTLs
// ---------------------------------------------------------------------------

/** Maximum time (seconds) to wait for a sub-agent response */
export const SUB_AGENT_TIMEOUT_SECONDS = 30;

/** Conversation TTL in days before archival */
export const CONVERSATION_TTL_DAYS = 90;
