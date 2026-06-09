import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { BaseRuntimeStack } from './base-runtime-stack';

/**
 * Props for the OrchRuntimeStack, including sub-agent runtime ARNs.
 */
export interface OrchRuntimeStackProps extends cdk.StackProps {
  /** Sub-agent runtime ARNs passed as environment variables to the orchestration container */
  subAgentArns: {
    cost: string;
    health: string;
    support: string;
    ta: string;
    cur: string;
    network: string;
  };
  /**
   * Conversations table name imported from {@link DataStack} so the
   * orchestration agent can persist Capture_Conversation_Context
   * entries (Reqs 9.20, 17.9 — Task 36). Surfaced into the container
   * via the ``CONVERSATIONS_TABLE_NAME`` environment variable.
   *
   * Made optional so existing call sites that haven't been updated
   * yet still type-check; when omitted, the orchestration agent's
   * Capture_Conversation_Context persistence layer becomes a no-op
   * (see ``state.py``).
   */
  conversationsTableName?: string;
}

/**
 * G.O.A.T. OrchRuntimeStack — Imports from OrchInfraStack, builds container,
 * creates AgentCore CfnRuntime for the Orchestration Agent.
 *
 * Passes sub-agent runtime ARNs as environment variables (COST_AGENT_ARN,
 * HEALTH_AGENT_ARN, SUPPORT_AGENT_ARN, TA_AGENT_ARN, CUR_AGENT_ARN,
 * NETWORK_AGENT_ARN) so the orchestration agent's @tool functions can invoke
 * sub-agent runtimes. Also surfaces the Conversations table name
 * (``CONVERSATIONS_TABLE_NAME``) so the orchestration agent can persist
 * Capture_Conversation_Context entries (Task 36, Reqs 9.20 / 17.9).
 */
export class OrchRuntimeStack extends BaseRuntimeStack {
  constructor(scope: Construct, id: string, props: OrchRuntimeStackProps) {
    super(scope, id, {
      ...props,
      config: {
        domainName: 'orch',
        exportPrefix: 'GOATOrchAgent',
        ecrRepoName: 'goat-orch-agent-repository',
        runtimeName: 'goat_orchestration_agent',
        runtimeDescription: 'G.O.A.T. Orchestration Agent - Multi-agent orchestration with Strands Agent SDK',
        agentSourcePath: '../../agents/orchestration-agent',
        environmentVariables: {
          COST_AGENT_ARN: props.subAgentArns.cost,
          HEALTH_AGENT_ARN: props.subAgentArns.health,
          SUPPORT_AGENT_ARN: props.subAgentArns.support,
          TA_AGENT_ARN: props.subAgentArns.ta,
          CUR_AGENT_ARN: props.subAgentArns.cur,
          NETWORK_AGENT_ARN: props.subAgentArns.network,
          // Capture_Conversation_Context persistence (Task 36).
          // The empty-string fallback keeps the env contract stable
          // while letting the agent fall back to no-op persistence
          // when the prop is omitted by an older deployment script.
          CONVERSATIONS_TABLE_NAME: props.conversationsTableName ?? '',
          // Foundation model override (Req 9.9). When set, the agent
          // uses this model instead of the default in main.py.
          ...(process.env.ORCH_MODEL_ID ? { ORCH_MODEL_ID: process.env.ORCH_MODEL_ID } : {}),
        },
      },
    });
  }
}
