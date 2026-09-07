import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { DevOpsAgentSpace } from './constructs/devops-agent-space';

/**
 * DevOpsAgentSpaceStack — owns the AWS DevOps Agent Agent Space and everything
 * needed to use it: IAM roles, operator app, the AWS monitor association, and
 * the eventChannel webhook (URL as output, HMAC secret in Secrets Manager).
 *
 * Deploys to the DevOps Agent region (`devOpsAgentRegion` context), which may
 * differ from the infra region — an Agent Space monitors resources across all
 * regions of the associated account, so it does not need to live with the EKS
 * stacks. All heavy lifting is in the reusable DevOpsAgentSpace construct
 * (lib/constructs/devops-agent-space.ts).
 *
 * Replaces the imperative `aws devops-agent` CLI flow previously in
 * scripts/setup-devops-agent.ps1|.sh — including the manual console step of
 * generating the webhook and pasting its URL/secret.
 */
export interface DevOpsAgentSpaceStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
}

export class DevOpsAgentSpaceStack extends cdk.Stack {
  public readonly agentSpaceId: string;
  public readonly webhookUrl: string;

  constructor(scope: Construct, id: string, props: DevOpsAgentSpaceStackProps) {
    super(scope, id, props);

    const space = new DevOpsAgentSpace(this, 'Space', {
      name: props.projectName,
      description: `Agent Space for the ${props.projectName} EKS investigation demo (${props.environment})`,
    });

    this.agentSpaceId = space.agentSpaceId;
    this.webhookUrl = space.webhookUrl;

    new cdk.CfnOutput(this, 'AgentSpaceId', {
      description: 'DevOps Agent Agent Space ID',
      value: space.agentSpaceId,
    });

    new cdk.CfnOutput(this, 'AgentSpaceArn', {
      description: 'DevOps Agent Agent Space ARN',
      value: space.agentSpaceArn,
    });

    new cdk.CfnOutput(this, 'WebhookUrl', {
      description: 'Generic webhook URL for triggering investigations',
      value: space.webhookUrl,
    });

    new cdk.CfnOutput(this, 'WebhookSecretArn', {
      description: 'Secrets Manager ARN holding the webhook HMAC secret',
      value: space.webhookSecret?.secretArn ?? '',
    });

    new cdk.CfnOutput(this, 'AgentSpaceRoleArn', {
      description: 'Monitoring role assumed by DevOps Agent',
      value: space.agentSpaceRole.roleArn,
    });

    new cdk.CfnOutput(this, 'OperatorRoleArn', {
      description: 'Operator app (web console) role',
      value: space.operatorRole.roleArn,
    });
  }
}
