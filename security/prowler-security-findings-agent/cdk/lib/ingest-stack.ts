import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import { Construct } from 'constructs';
import * as path from 'path';

export interface IngestStackProps extends cdk.StackProps {
  findingsTableName: string;
  rawReportsBucketName: string;
  remediationsBucketName: string;
  devOpsAgentTriggerTopicArn: string;
  bedrockModelId: string;
  costEventsTableName: string;
  /**
   * true → auto-fan-out every CRITICAL/HIGH finding to the DevOps Agent webhook on ingest.
   * false (default) → the dashboard user drives investigation manually, one finding at a time.
   */
  autoInvestigate?: boolean;
}

/**
 * Ingest Stack
 *
 * Two Lambdas wired together:
 *
 *   1. `ingest-findings`
 *      Trigger: S3:ObjectCreated on raw-reports/*.ocsf.json in the raw bucket.
 *      Action:  Parses OCSF, upserts one DynamoDB item per finding, publishes
 *               CRITICAL/HIGH findings to the DevOps Agent SNS topic, and
 *               invokes remediation-context async for those same findings.
 *
 *   2. `remediation-context`
 *      Trigger: Invoked async by ingest-findings.
 *      Action:  Calls Bedrock Converse (Amazon Nova Pro by default). Writes
 *               the generated markdown to s3://remediations/{finding_uid}.md
 *               and updates the DynamoDB item with remediation_s3_key.
 */
export class IngestStack extends cdk.Stack {
  public readonly remediationLambdaArn: string;
  public readonly remediationLambdaName: string;

  constructor(scope: Construct, id: string, props: IngestStackProps) {
    super(scope, id, props);

    const {
      findingsTableName,
      rawReportsBucketName,
      remediationsBucketName,
      devOpsAgentTriggerTopicArn,
      bedrockModelId,
    } = props;

    const { costEventsTableName } = props;
    const findingsTableArn = `arn:aws:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/${findingsTableName}`;
    const costEventsTableArn = `arn:aws:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/${costEventsTableName}`;

    // ---- remediation-context Lambda ----
    const remediationRole = new iam.Role(this, 'RemediationContextRole', {
      roleName: `prowler-security-remediation-role-${cdk.Aws.REGION}`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
    remediationRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel', 'bedrock:Converse'],
      resources: ['*'],
    }));
    remediationRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:PutObject'],
      resources: [`arn:aws:s3:::${remediationsBucketName}/*`],
    }));
    remediationRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem'],
      resources: [findingsTableArn],
    }));
    remediationRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['dynamodb:PutItem'],
      resources: [costEventsTableArn],
    }));

    const remediationLambda = new lambda.Function(this, 'RemediationContextLambda', {
      functionName: 'prowler-security-remediation-context',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'remediation-context')),
      role: remediationRole,
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      environment: {
        FINDINGS_TABLE: findingsTableName,
        REMEDIATIONS_BUCKET: remediationsBucketName,
        BEDROCK_MODEL_ID: bedrockModelId,
        COST_EVENTS_TABLE: costEventsTableName,
      },
    });

    // ---- ingest-findings Lambda ----
    const ingestRole = new iam.Role(this, 'IngestFindingsRole', {
      roleName: `prowler-security-ingest-role-${cdk.Aws.REGION}`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
    ingestRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject'],
      resources: [`arn:aws:s3:::${rawReportsBucketName}/*`],
    }));
    ingestRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['dynamodb:BatchWriteItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem'],
      resources: [findingsTableArn],
    }));
    ingestRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['sns:Publish'],
      resources: [devOpsAgentTriggerTopicArn],
    }));
    ingestRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['lambda:InvokeFunction'],
      resources: [remediationLambda.functionArn],
    }));
    ingestRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['dynamodb:PutItem'],
      resources: [costEventsTableArn],
    }));

    const ingestLambda = new lambda.Function(this, 'IngestFindingsLambda', {
      functionName: 'prowler-security-ingest',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ingest-findings')),
      role: ingestRole,
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      environment: {
        FINDINGS_TABLE: findingsTableName,
        DEVOPS_AGENT_TOPIC_ARN: devOpsAgentTriggerTopicArn,
        REMEDIATION_LAMBDA: remediationLambda.functionName,
        AUTO_INVESTIGATE: props.autoInvestigate ? 'true' : 'false',
        COST_EVENTS_TABLE: costEventsTableName,
      },
    });

    // Wire the S3 notification manually (we imported the bucket by name in
    // the app to keep stacks loosely coupled). fromBucketAttributes with the
    // account/region avoids CloudFormation circular refs when the bucket is
    // in a different stack but deployed in the same account/region.
    const rawBucket = s3.Bucket.fromBucketAttributes(this, 'RawBucketRef', {
      bucketName: rawReportsBucketName,
      account: cdk.Aws.ACCOUNT_ID,
      region: cdk.Aws.REGION,
    });
    rawBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(ingestLambda),
      { prefix: 'raw-reports/', suffix: '.ocsf.json' },
    );

    new cdk.CfnOutput(this, 'IngestLambdaArn', { value: ingestLambda.functionArn });
    this.remediationLambdaArn = remediationLambda.functionArn;
    this.remediationLambdaName = remediationLambda.functionName;

    new cdk.CfnOutput(this, 'RemediationLambdaArn', { value: remediationLambda.functionArn });
    new cdk.CfnOutput(this, 'BedrockModelId', { value: bedrockModelId });
  }
}
