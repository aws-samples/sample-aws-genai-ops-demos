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
  };
}

/**
 * G.O.A.T. OrchRuntimeStack — Imports from OrchInfraStack, builds container,
 * creates AgentCore CfnRuntime for the Orchestration Agent.
 *
 * Passes sub-agent runtime ARNs as environment variables (COST_AGENT_ARN,
 * HEALTH_AGENT_ARN, SUPPORT_AGENT_ARN, TA_AGENT_ARN, CUR_AGENT_ARN) so the
 * orchestration agent's @tool functions can invoke sub-agent runtimes.
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
        },
      },
    });
  }
}
