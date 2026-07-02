/**
 * G.O.A.T. - GenAI Operations Analytics Tool
 * Shared constants used across CDK stacks, agents, and frontend
 */
/** Supported sub-agent domains */
export declare const DOMAINS: readonly ["cost", "health", "support", "trusted_advisor", "cur", "network"];
export type Domain = typeof DOMAINS[number];
/** Nova Pro -- used by the Orchestration Agent for complex reasoning */
export declare const MODEL_ID_NOVA_PRO = "amazon.nova-pro-v1:0";
/** Nova Lite -- used by sub-agents for simpler retrieve-and-format tasks */
export declare const MODEL_ID_NOVA_LITE = "amazon.nova-lite-v1:0";
/** Maximum time (seconds) to wait for a sub-agent response */
export declare const SUB_AGENT_TIMEOUT_SECONDS = 30;
/** Conversation TTL in days before archival */
export declare const CONVERSATION_TTL_DAYS = 90;
