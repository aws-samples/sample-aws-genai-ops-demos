/**
 * GOAT Network Agent ↔ AWS DevOps Agent Integration
 *
 * Exposes the GOAT Network Agent's 22 packet-level diagnostic actions
 * plus a composite full_diagnostic workflow as an external tool for
 * AWS DevOps Agent.
 *
 * @module goat-devops-integration
 */

export * from './types';
export { AgentIntegrationTemplate } from './constructs/agent-integration-template';
