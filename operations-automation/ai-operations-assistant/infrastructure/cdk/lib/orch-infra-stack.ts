import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { BaseInfraStack } from './base-infra-stack';

/**
 * G.O.A.T. OrchInfraStack — ECR, S3, CodeBuild, and IAM for the Orchestration Agent.
 * IAM role scoped to AgentCore invoke (calling sub-agent runtimes via @tool functions)
 * and Bedrock model invocation (Nova Pro for Strands Agent LLM reasoning).
 */
export class OrchInfraStack extends BaseInfraStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      domainName: 'orch',
      exportPrefix: 'GOATOrchAgent',
      imageTag: 'goat_orch_agent',
      domainPolicies: [
        // AgentCore runtime invocation (call sub-agent runtimes)
        new iam.PolicyStatement({
          sid: 'AgentCoreInvoke',
          effect: iam.Effect.ALLOW,
          actions: [
            'bedrock-agentcore:InvokeAgentRuntime',
          ],
          resources: [
            `arn:aws:bedrock-agentcore:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:runtime/*`,
          ],
        }),
        // Bedrock model invocation (Nova Pro for LLM reasoning)
        new iam.PolicyStatement({
          sid: 'BedrockModelInvocation',
          effect: iam.Effect.ALLOW,
          actions: [
            'bedrock:InvokeModel',
            'bedrock:InvokeModelWithResponseStream',
            'bedrock:Converse',
            'bedrock:ConverseStream',
          ],
          resources: [
            'arn:aws:bedrock:*::foundation-model/*',
            `arn:aws:bedrock:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:inference-profile/*`,
            `arn:aws:bedrock:*:${cdk.Aws.ACCOUNT_ID}:inference-profile/*`,
          ],
        }),
        // AWS Marketplace for Bedrock model access
        new iam.PolicyStatement({
          sid: 'MarketplaceAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'aws-marketplace:ViewSubscriptions',
            'aws-marketplace:Subscribe',
            'aws-marketplace:Unsubscribe',
          ],
          resources: ['*'],
        }),
      ],
    }, props);
  }
}
