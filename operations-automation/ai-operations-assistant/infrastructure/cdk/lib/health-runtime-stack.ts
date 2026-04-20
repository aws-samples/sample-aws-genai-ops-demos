import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { BaseRuntimeStack } from './base-runtime-stack';

/**
 * G.O.A.T. HealthRuntimeStack — Imports from HealthInfraStack, builds container,
 * creates AgentCore CfnRuntime for the Health Agent.
 */
export class HealthRuntimeStack extends BaseRuntimeStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      ...props,
      config: {
        domainName: 'health',
        exportPrefix: 'GOATHealthAgent',
        ecrRepoName: 'goat-health-agent-repository',
        runtimeName: 'goat_health_agent',
        runtimeDescription: 'G.O.A.T. Health Agent - AWS Health Dashboard event queries',
        agentSourcePath: '../../agents/health-agent',
      },
    });
  }
}
