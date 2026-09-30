import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { DevOpsAgentSpace } from '../../../../shared/devops-agent/agent-space/cdk/agent-space';

/**
 * The AWS DevOps Agent Agent Space of the demo: the shared construct (roles, operator app,
 * AWS monitor association, eventChannel webhook) plus the outputs deploy-all reads and
 * passes to the other stacks as --context. Deploys to the DevOps Agent region, which may
 * differ from the region of the file system: an Agent Space sees every region of the account.
 */
export interface AgentSpaceStackProps extends cdk.StackProps {
  projectName: string;
}

export class AgentSpaceStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentSpaceStackProps) {
    super(scope, id, props);

    const space = new DevOpsAgentSpace(this, 'Space', {
      name: props.projectName,
      description: 'Agent Space for the FSx for Windows SLA review demo',
    });

    new cdk.CfnOutput(this, 'AgentSpaceId', { value: space.agentSpaceId, description: 'DevOps Agent Agent Space ID' });
    new cdk.CfnOutput(this, 'AgentSpaceArn', { value: space.agentSpaceArn, description: 'DevOps Agent Agent Space ARN' });
    new cdk.CfnOutput(this, 'WebhookUrl', { value: space.webhookUrl, description: 'Generic webhook URL that starts investigations' });
    new cdk.CfnOutput(this, 'WebhookSecretArn', { value: space.webhookSecret?.secretArn ?? '', description: 'Secrets Manager ARN of the webhook HMAC secret' });
    new cdk.CfnOutput(this, 'OperatorRoleArn', { value: space.operatorRole.roleArn, description: 'Operator app (web console) role' });
  }
}
