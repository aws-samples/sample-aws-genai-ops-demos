import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { BaseRuntimeStack } from './base-runtime-stack';

/**
 * G.O.A.T. SupportRuntimeStack — Imports from SupportInfraStack, builds container,
 * creates AgentCore CfnRuntime for the Support Agent.
 */
export class SupportRuntimeStack extends BaseRuntimeStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      ...props,
      config: {
        domainName: 'support',
        exportPrefix: 'GOATSupportAgent',
        ecrRepoName: 'goat-support-agent-repository',
        runtimeName: 'goat_support_agent',
        runtimeDescription: 'G.O.A.T. Support Agent - AWS Support case queries',
        agentSourcePath: '../../agents/support-agent',
      },
    });
  }
}
