import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as path from 'path';

export interface ApiStackProps extends cdk.StackProps {
  findingsTableName: string;
  remediationsBucketName: string;
  /** Raw OCSF reports bucket — dashboard-api lists `raw-reports/{scan_id}/` prefixes
   * to build the scan history (the authoritative source of "what scans have run"). */
  rawReportsBucketName: string;
  scannerClusterArn: string;
  scannerTaskDefinitionArn: string;
  scannerSubnetIds: string[];
  scannerSecurityGroupId: string;
  /** CloudWatch log group the scanner writes to — used by /scans/running/{taskArn}/logs. */
  scannerLogGroupName: string;
  authenticatedRoleArn: string;
  devOpsAgentTopicArn: string;
  devOpsAgentRegion: string;
  devOpsAgentSpaceId: string;
  /** ARN of the Secrets Manager secret storing {webhookUrl, webhookSecret,
   * agentSpaceId}. The dashboard reads `agentSpaceId` from this bundle so
   * it survives partial CDK redeploys (unlike a Lambda env var). */
  devOpsAgentSecretArn: string;
  /** Remediation context Lambda — dashboard-api invokes it synchronously on
   * POST /findings/{uid}/insights (lazy, on-demand generation). */
  remediationLambdaArn: string;
  remediationLambdaName: string;
  costEventsTableName: string;
}

/**
 * API Stack
 *
 * Single Lambda with a Function URL (IAM auth type). The browser calls it via
 * SigV4 using the temporary credentials from Cognito Identity Pool. No API
 * Gateway, no REST choreography — the Function URL is the cheapest, lowest-
 * latency way to expose a read/write endpoint backed by AWS IAM.
 *
 * Routes (implemented inside the Lambda via `event.requestContext.http.path`):
 *   GET  /findings                 → list findings (paginated via LastEvaluatedKey)
 *   GET  /findings/{finding_uid}   → detail + presigned URL to remediation markdown
 *   GET  /scans                    → list recent scan_ids (distinct scan_id on GSI)
 *   POST /scans                    → ecs:RunTask on the Prowler task definition
 */
export class ApiStack extends cdk.Stack {
  public readonly functionUrl: string;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const findingsTableArn = `arn:aws:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/${props.findingsTableName}`;
    const costEventsTableArn = `arn:aws:dynamodb:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/${props.costEventsTableName}`;

    const role = new iam.Role(this, 'DashboardApiRole', {
      roleName: `prowler-security-dashboard-api-${cdk.Aws.REGION}`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      // UpdateItem needed for the suppress/unsuppress endpoints.
      actions: ['dynamodb:Query', 'dynamodb:Scan', 'dynamodb:GetItem', 'dynamodb:UpdateItem'],
      resources: [findingsTableArn, `${findingsTableArn}/index/*`],
    }));
    // Cost events table is read-only from the dashboard; writes come from
    // the Bedrock / DevOps-Agent / Ingest Lambdas with their own policies.
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['dynamodb:Query', 'dynamodb:Scan'],
      resources: [costEventsTableArn, `${costEventsTableArn}/index/*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject'],
      resources: [`arn:aws:s3:::${props.remediationsBucketName}/*`],
    }));
    // ListBucket + GetObject on raw-reports so the /scans endpoint can
    // enumerate the authoritative scan history from S3 prefixes (the
    // DynamoDB findings table is overwritten each scan and loses scan_ids).
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:ListBucket'],
      resources: [`arn:aws:s3:::${props.rawReportsBucketName}`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject'],
      resources: [`arn:aws:s3:::${props.rawReportsBucketName}/raw-reports/*`],
    }));
    // ecs:RunTask scopes cleanly to the task definition. But ecs:ListTasks
    // and ecs:DescribeTasks are authorized on cluster + container-instance
    // ARNs, not on the task definition — they're also * in practice because
    // task ARNs don't exist until after RunTask. Restrict by cluster.
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ecs:RunTask'],
      resources: [props.scannerTaskDefinitionArn, `${props.scannerTaskDefinitionArn}:*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ecs:ListTasks', 'ecs:DescribeTasks'],
      resources: ['*'],
      conditions: {
        ArnEquals: {
          'ecs:cluster': props.scannerClusterArn,
        },
      },
    }));
    // Read scanner CloudWatch logs so /scans/running/{taskArn}/logs can return
    // live progress parsed from Prowler's tqdm output.
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['logs:FilterLogEvents', 'logs:GetLogEvents', 'logs:DescribeLogStreams'],
      resources: [
        `arn:aws:logs:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:log-group:${props.scannerLogGroupName}`,
        `arn:aws:logs:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:log-group:${props.scannerLogGroupName}:*`,
      ],
    }));

    // Manual investigate → publish to the DevOps Agent SNS topic
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['sns:Publish'],
      resources: [props.devOpsAgentTopicArn],
    }));

    // Read the DevOps Agent webhook bundle to recover the Agent Space ID at
    // runtime. setup-devops-agent writes this secret once; subsequent partial
    // CDK redeploys do not touch it, so the dashboard never loses the link
    // to the agent space just because someone ran `cdk deploy api`.
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['secretsmanager:GetSecretValue'],
      resources: [props.devOpsAgentSecretArn],
    }));

    // Read-only queries against AWS DevOps Agent for the Investigation tab.
    // The IAM service namespace is `aidevops` (not `devops-agent` — that's only
    // the AWS CLI command surface).
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'aidevops:ListBacklogTasks',
        'aidevops:GetBacklogTask',
        'aidevops:ListExecutions',
        'aidevops:ListJournalRecords',
      ],
      resources: ['*'],
    }));

    // Invoke the remediation-context Lambda for on-demand Bedrock Insights.
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['lambda:InvokeFunction'],
      resources: [props.remediationLambdaArn],
    }));
    // ecs:RunTask requires passing BOTH the task role and the auto-generated
    // execution role to Fargate. Narrow the PassRole scope by path prefix
    // of both (the execution role has a CDK-synthesized name so we allow the
    // ECSTaskExecutionRole naming convention plus our explicit task role).
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['iam:PassRole'],
      resources: [
        `arn:aws:iam::${cdk.Aws.ACCOUNT_ID}:role/prowler-security-scanner-task-${cdk.Aws.REGION}`,
        // CDK-synthesized execution role name lives in the Scanner stack —
        // PassRole wildcard scoped to Prowler stack resources only.
        `arn:aws:iam::${cdk.Aws.ACCOUNT_ID}:role/ProwlerSecurityScanner-*`,
      ],
      conditions: {
        StringEquals: {
          'iam:PassedToService': 'ecs-tasks.amazonaws.com',
        },
      },
    }));

    const fn = new lambda.Function(this, 'DashboardApiLambda', {
      functionName: 'prowler-security-dashboard-api',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'dashboard-api')),
      role,
      // Lazy Bedrock Insights invocation is synchronous — the remediation
      // Lambda has a 5 min timeout; keep a safe margin above that.
      timeout: cdk.Duration.minutes(6),
      memorySize: 512,
      environment: {
        FINDINGS_TABLE: props.findingsTableName,
        REMEDIATIONS_BUCKET: props.remediationsBucketName,
        RAW_REPORTS_BUCKET: props.rawReportsBucketName,
        SCANNER_CLUSTER_ARN: props.scannerClusterArn,
        SCANNER_TASK_DEFINITION_ARN: props.scannerTaskDefinitionArn,
        SCANNER_SUBNET_IDS: props.scannerSubnetIds.join(','),
        SCANNER_SECURITY_GROUP_ID: props.scannerSecurityGroupId,
        SCANNER_LOG_GROUP: props.scannerLogGroupName,
        DEVOPS_AGENT_TOPIC_ARN: props.devOpsAgentTopicArn,
        DEVOPS_AGENT_REGION: props.devOpsAgentRegion,
        // Kept as a deploy-time hint for fresh installs where the secret is
        // still a placeholder. Runtime always prefers the Secrets Manager
        // bundle so partial redeploys don't wipe a configured agent space.
        DEVOPS_AGENT_SPACE_ID: props.devOpsAgentSpaceId,
        DEVOPS_AGENT_SECRET_ARN: props.devOpsAgentSecretArn,
        REMEDIATION_LAMBDA: props.remediationLambdaName,
        COST_EVENTS_TABLE: props.costEventsTableName,
      },
    });

    const fnUrl = fn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
      cors: {
        allowedOrigins: ['*'],
        allowedMethods: [lambda.HttpMethod.GET, lambda.HttpMethod.POST, lambda.HttpMethod.DELETE],
        allowedHeaders: ['authorization', 'content-type', 'x-amz-content-sha256', 'x-amz-date', 'x-amz-security-token'],
      },
    });

    // Explicitly allow the authenticated Cognito role to invoke this Function URL.
    fn.grantInvokeUrl(iam.Role.fromRoleArn(this, 'ImportedAuthRole', props.authenticatedRoleArn));

    this.functionUrl = fnUrl.url;

    new cdk.CfnOutput(this, 'FunctionUrl', {
      value: fnUrl.url,
      description: 'Dashboard API Function URL (SigV4, IAM auth)',
    });
  }
}
