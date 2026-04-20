import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { BaseRuntimeStack } from './base-runtime-stack';

/**
 * G.O.A.T. TARuntimeStack — Imports from TAInfraStack, builds container,
 * creates AgentCore CfnRuntime for the Trusted Advisor Agent.
 */
export class TARuntimeStack extends BaseRuntimeStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      ...props,
      config: {
        domainName: 'ta',
        exportPrefix: 'GOATTAAgent',
        ecrRepoName: 'goat-ta-agent-repository',
        runtimeName: 'goat_ta_agent',
        runtimeDescription: 'G.O.A.T. Trusted Advisor Agent - TA check and recommendation queries',
        agentSourcePath: '../../agents/ta-agent',
      },
    });
  }
}
