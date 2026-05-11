import * as cdk from 'aws-cdk-lib';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import * as path from 'path';

export interface DevOpsAgentStackProps extends cdk.StackProps {
  /** Webhook URL — empty string on first deploy (before webhook is generated) */
  webhookUrl: string;
  /** Webhook HMAC secret — empty string on first deploy */
  webhookSecret: string;
  /** Region where the Agent Space lives (DevOpsAgent is not GA in every region) */
  devOpsAgentRegion: string;
  /** Agent Space ID (informational — forwarded in payload metadata) */
  devOpsAgentSpaceId: string;
  /** Bucket holding the Bedrock-generated remediation markdown files */
  remediationsBucketName: string;
  costEventsTableName: string;
}

/**
 * DevOps Agent Stack
 *
 * Mirrors the pattern from `observability/eks-investigation-devops-agent/cdk/lib/devops-agent-stack.ts`:
 * an SNS topic that the ingest pipeline publishes to for every CRITICAL/HIGH
 * finding, plus a Lambda that HMAC-signs the payload and POSTs it to the
 * DevOps Agent webhook. The Agent Space itself is created out-of-band by the
 * setup-devops-agent.sh script because DevOpsAgent CloudFormation resources
 * are not GA in all regions.
 */
export class DevOpsAgentStack extends cdk.Stack {
  public readonly triggerTopicArn: string;
  public readonly triggerLambdaArn: string;

  constructor(scope: Construct, id: string, props: DevOpsAgentStackProps) {
    super(scope, id, props);

    const { webhookUrl, webhookSecret, devOpsAgentRegion, devOpsAgentSpaceId, remediationsBucketName, costEventsTableName } = props;
    const costEventsTableArn = `arn:aws:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/${costEventsTableName}`;

    const webhookSecretResource = new secretsmanager.Secret(this, 'DevOpsAgentSecret', {
      secretName: 'prowler-security/devops-agent-webhook-secret',  // pragma: allowlist secret - Secrets Manager resource name, not a secret value
      description: 'DevOps Agent webhook HMAC secret key for Prowler security findings',
      secretStringValue: cdk.SecretValue.unsafePlainText(
        webhookSecret || 'PLACEHOLDER_GENERATE_WEBHOOK_IN_CONSOLE',
      ),
    });

    const triggerTopic = new sns.Topic(this, 'DevOpsAgentTriggerTopic', {
      topicName: 'prowler-security-devops-agent-trigger',
      displayName: 'Prowler Security → DevOps Agent Trigger',
    });

    const lambdaRole = new iam.Role(this, 'DevOpsAgentTriggerLambdaRole', {
      roleName: `prowler-security-devops-trigger-role-${cdk.Aws.REGION}`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['secretsmanager:GetSecretValue'],
      resources: [webhookSecretResource.secretArn],
    }));

    // The Lambda reads the Bedrock remediation markdown (if any) to embed it in
    // the webhook payload, so the DevOps Agent sees the remediation upfront.
    lambdaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject'],
      resources: [`arn:aws:s3:::${remediationsBucketName}/*`],
    }));
    lambdaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['dynamodb:PutItem'],
      resources: [costEventsTableArn],
    }));

    const triggerLambda = new lambda.Function(this, 'DevOpsAgentTriggerLambda', {
      functionName: 'prowler-security-devops-trigger',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'devops-agent-trigger')),
      role: lambdaRole,
      timeout: cdk.Duration.seconds(30),
      environment: {
        WEBHOOK_URL: webhookUrl || 'NOT_CONFIGURED',
        SECRET_ARN: webhookSecretResource.secretArn,
        REMEDIATIONS_BUCKET: remediationsBucketName,
        AWS_ACCOUNT_ID: cdk.Aws.ACCOUNT_ID,
        AWS_REGION_NAME: cdk.Aws.REGION,
        DEVOPS_AGENT_REGION: devOpsAgentRegion,
        DEVOPS_AGENT_SPACE_ID: devOpsAgentSpaceId,
        COST_EVENTS_TABLE: costEventsTableName,
      },
    });

    triggerTopic.addSubscription(new snsSubscriptions.LambdaSubscription(triggerLambda));

    this.triggerTopicArn = triggerTopic.topicArn;
    this.triggerLambdaArn = triggerLambda.functionArn;

    new cdk.CfnOutput(this, 'SNSTopicArn', {
      value: triggerTopic.topicArn,
      description: 'SNS topic that the ingest pipeline publishes to for CRITICAL/HIGH findings',
    });
    new cdk.CfnOutput(this, 'LambdaFunctionArn', {
      value: triggerLambda.functionArn,
      description: 'Lambda that forwards SNS messages to the DevOps Agent webhook',
    });
    new cdk.CfnOutput(this, 'WebhookSecretArn', {
      value: webhookSecretResource.secretArn,
      description: 'Secrets Manager secret storing the webhook HMAC key',
    });
  }
}
