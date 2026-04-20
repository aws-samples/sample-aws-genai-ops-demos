import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { BaseRuntimeStack } from './base-runtime-stack';

/**
 * G.O.A.T. CURRuntimeStack — Imports from CURInfraStack, builds container,
 * creates AgentCore CfnRuntime for the CUR Agent.
 */
export class CURRuntimeStack extends BaseRuntimeStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      ...props,
      config: {
        domainName: 'cur',
        exportPrefix: 'GOATCURAgent',
        ecrRepoName: 'goat-cur-agent-repository',
        runtimeName: 'goat_cur_agent',
        runtimeDescription: 'G.O.A.T. CUR Agent - Cost and Usage Report queries via Athena',
        agentSourcePath: '../../agents/cur-agent',
      },
    });
  }
}
