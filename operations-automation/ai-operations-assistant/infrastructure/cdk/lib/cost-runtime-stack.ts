import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { BaseRuntimeStack } from './base-runtime-stack';

/**
 * G.O.A.T. CostRuntimeStack — Imports from CostInfraStack, builds container,
 * creates AgentCore CfnRuntime for the Cost Agent.
 */
export class CostRuntimeStack extends BaseRuntimeStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      ...props,
      config: {
        domainName: 'cost',
        exportPrefix: 'GOATCostAgent',
        ecrRepoName: 'goat-cost-agent-repository',
        runtimeName: 'goat_cost_agent',
        runtimeDescription: 'G.O.A.T. Cost Agent - AWS Cost Explorer and Cost Optimization Hub queries',
        agentSourcePath: '../../agents/cost-agent',
      },
    });
  }
}
