import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { DevOpsAgentSpace } from '../../../../shared/devops-agent/agent-space/cdk/agent-space';

/**
 * The AWS DevOps Agent Agent Space of the demo: the shared construct (roles, operator app,
 * AWS monitor association, eventChannel webhook) plus the outputs deploy-all reads and passes
 * to the other stacks as --context.
 *
 * One addition on the monitoring role, through the construct's public role: the skill reads
 * AWS Config resource history for its Recent changes line. No Config recorder is created (one
 * per region, it may already exist); without one the skill falls back to CloudTrail. Two scoped
 * read statements follow: the load function (GetFunction, GetFunctionConcurrency) and the
 * writer's log files (DownloadDBLogFilePortion).
 */
export interface AgentSpaceStackProps extends cdk.StackProps {
  projectName: string;
}

export class AgentSpaceStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentSpaceStackProps) {
    super(scope, id, props);

    const space = new DevOpsAgentSpace(this, 'Space', {
      name: props.projectName,
      description: 'Agent Space for the Aurora MySQL incident investigation with business context demo',
    });

    space.agentSpaceRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ReadConfigHistoryForRecentChanges',
      actions: ['config:GetResourceConfigHistory'],
      resources: ['*'],
    }));

    // Read-only evidence the storm investigations asked for (observed as access denied): the load
    // function's configuration and reserved concurrency, and the writer's error log. Scoped to the
    // one function and the one instance, by the names DatabaseStack gives them, in this stack's
    // account and region. lambda:GetDurableExecution is left out on purpose: the Lab engine's
    // durable executions stay unreadable to the agent, which keeps it out of the Lab's control plane.
    const loadFunctionName = `${props.projectName}-connection-load`;
    const writerIdentifier = `${props.projectName}-writer`;
    const loadFunctionArn = this.formatArn({
      service: 'lambda', resource: 'function', resourceName: loadFunctionName, arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
    });
    space.agentSpaceRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ReadTheLoadFunction',
      actions: ['lambda:GetFunction', 'lambda:GetFunctionConcurrency'],
      resources: [loadFunctionArn, `${loadFunctionArn}:*`],
    }));
    space.agentSpaceRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ReadTheWriterLogs',
      actions: ['rds:DownloadDBLogFilePortion'],
      resources: [this.formatArn({
        service: 'rds', resource: 'db', resourceName: writerIdentifier, arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
      })],
    }));

    new cdk.CfnOutput(this, 'AgentSpaceId', { value: space.agentSpaceId, description: 'DevOps Agent Agent Space ID' });
    new cdk.CfnOutput(this, 'AgentSpaceArn', { value: space.agentSpaceArn, description: 'DevOps Agent Agent Space ARN' });
    new cdk.CfnOutput(this, 'WebhookUrl', { value: space.webhookUrl, description: 'Generic webhook URL that starts investigations' });
    new cdk.CfnOutput(this, 'WebhookSecretArn', { value: space.webhookSecret?.secretArn ?? '', description: 'Secrets Manager ARN of the webhook HMAC secret' });
    new cdk.CfnOutput(this, 'OperatorRoleArn', { value: space.operatorRole.roleArn, description: 'Operator app (web console) role' });
  }
}
