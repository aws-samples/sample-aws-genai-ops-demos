import * as cdk from 'aws-cdk-lib';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaDestinations from 'aws-cdk-lib/aws-lambda-destinations';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatch_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import { Runtime } from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import * as path from 'path';

export interface InvestigationWorkflowProps {
  notificationTopic: sns.ITopic;
  teamsTable: dynamodb.ITable;
  agentSpacesTable: dynamodb.ITable;
  devOpsAgentWebhookUrl: string;
  /** SSM Parameter Store name for the webhook HMAC secret */
  webhookSecretParamName: string;
  /** SSM Parameter Store name for the Slack webhook URL */
  slackWebhookParamName: string;
  /** SSM Parameter Store name for the MS Teams webhook URL */
  msTeamsWebhookParamName: string;
  /** Deployment environment — drives log retention (production=90d, non-production=14d) */
  deployEnvironment: string;
  /** SNS topic for DLQ alarm notifications */
  alarmTopic: sns.ITopic;
}

export class InvestigationWorkflow extends Construct {
  public readonly stateMachine: sfn.StateMachine;
  /** The Notifier Lambda's execution role ARN — used for SNS topic resource policy */
  public readonly notifierRoleArn: string;
  /** Lambda function references — exposed for composite alarm metric aggregation */
  public readonly investigationTriggerFunction: NodejsFunction;
  public readonly opsCenterCreatorFunction: NodejsFunction;
  public readonly notifierFunction: NodejsFunction;
  public readonly investigationCallbackFunction: NodejsFunction;

  constructor(scope: Construct, id: string, props: InvestigationWorkflowProps) {
    super(scope, id);

    const lambdaDir = path.join(__dirname, '../../lambda');

    // Environment-aware log retention: 90 days production, 14 days non-production
    const logRetention = props.deployEnvironment === 'production'
      ? logs.RetentionDays.THREE_MONTHS
      : logs.RetentionDays.TWO_WEEKS;

    const bundlingOptions = {
      forceDockerBundling: false,
      externalModules: ['@aws-sdk/*'],
    };

    // ─── Lambda: Investigation Trigger ────────────────────────────────────────
    // Triggers DevOps Agent investigation via HMAC-authenticated webhook
    const investigationTrigger = new NodejsFunction(this, 'InvestigationTrigger', {
      runtime: Runtime.NODEJS_24_X,
      entry: path.join(lambdaDir, 'investigation-trigger/index.ts'),
      handler: 'handler',
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        DEVOPS_AGENT_WEBHOOK_URL: props.devOpsAgentWebhookUrl,
        WEBHOOK_SECRET_PARAM_NAME: props.webhookSecretParamName,
        AGENT_SPACES_TABLE: props.agentSpacesTable.tableName,
      },
      logGroup: new logs.LogGroup(this, 'InvestigationTriggerLogs', {
        retention: logRetention,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      description: 'Triggers AWS DevOps Agent investigation for a Health event (hybrid multi-account routing)',
      bundling: bundlingOptions,
    });

    this.investigationTriggerFunction = investigationTrigger;

    // Grant read access to agent spaces routing table
    props.agentSpacesTable.grantReadData(investigationTrigger);

    // Grant read access to the webhook secret in SSM Parameter Store
    investigationTrigger.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ReadWebhookSecret',
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter'],
      resources: [
        `arn:aws:ssm:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:parameter${props.webhookSecretParamName}`,
      ],
    }));

    // Grant read access to the optional Jira routing config in SSM Parameter
    // Store. The trigger Lambda inlines these values into the prompt sent to
    // AWS DevOps Agent, because the agent's session policy strips its role's
    // SSM permissions and the agent itself can't read them at runtime. The
    // wildcard resource here covers the whole `/health-analyzer/jira/*`
    // namespace (the wizard writes projectKey, issueType, siteUrl).
    investigationTrigger.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ReadJiraRoutingConfig',
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter', 'ssm:GetParameters'],
      resources: [
        `arn:aws:ssm:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:parameter/health-analyzer/jira/*`,
      ],
    }));

    // ─── Lambda: OpsCenter Creator ────────────────────────────────────────────
    // Creates an OpsItem in Systems Manager OpsCenter when impact is detected
    const opsCenterCreator = new NodejsFunction(this, 'OpsCenterCreator', {
      runtime: Runtime.NODEJS_24_X,
      entry: path.join(lambdaDir, 'opscenter-creator/index.ts'),
      handler: 'handler',
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {},
      logGroup: new logs.LogGroup(this, 'OpsCenterCreatorLogs', {
        retention: logRetention,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      description: 'Creates OpsItem in AWS Systems Manager OpsCenter for Health event impact tracking',
      bundling: bundlingOptions,
    });

    this.opsCenterCreatorFunction = opsCenterCreator;

    // Grant permission to create OpsItems — scoped to account/region OpsItem ARN pattern
    opsCenterCreator.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'ssm:CreateOpsItem',
        'ssm:AddTagsToResource',
      ],
      resources: [
        cdk.Arn.format({
          service: 'ssm',
          resource: 'opsitem',
          resourceName: '*',
          arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
        }, cdk.Stack.of(this)),
      ],
    }));

    // ─── Lambda: Notifier ─────────────────────────────────────────────────────
    // Routes alerts to the right teams based on investigation findings
    const notifier = new NodejsFunction(this, 'Notifier', {
      runtime: Runtime.NODEJS_24_X,
      entry: path.join(lambdaDir, 'notifier/index.ts'),
      handler: 'handler',
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      environment: {
        SNS_TOPIC_ARN: props.notificationTopic.topicArn,
        SLACK_WEBHOOK_PARAM_NAME: props.slackWebhookParamName,
        MSTEAMS_WEBHOOK_PARAM_NAME: props.msTeamsWebhookParamName,
        TEAMS_TABLE: props.teamsTable.tableName,
        ENABLE_DEFAULT_ROUTING: 'true',
        AWS_ACCOUNT_ID: cdk.Stack.of(this).account,
      },
      logGroup: new logs.LogGroup(this, 'NotifierLogs', {
        retention: logRetention,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      description: 'Routes impact notifications to affected teams (with default routing fallback and OpsItem link)',
      bundling: bundlingOptions,
    });

    this.notifierFunction = notifier;

    props.notificationTopic.grantPublish(notifier);
    props.teamsTable.grantReadData(notifier);

    // Grant read access to Slack and MS Teams webhook URL secrets in SSM Parameter Store
    notifier.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ReadNotificationWebhookSecrets',
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter'],
      resources: [
        `arn:aws:ssm:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:parameter${props.slackWebhookParamName}`,
        `arn:aws:ssm:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:parameter${props.msTeamsWebhookParamName}`,
      ],
    }));

    // Grant permission to read AWS Account contact information for default routing fallback
    // Scoped to the deployment account ARN (account service uses global partition ARN)
    notifier.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'account:GetContactInformation',
        'account:GetAlternateContact',
      ],
      resources: [
        `arn:aws:account::${cdk.Stack.of(this).account}:account`,
      ],
    }));

    // Expose Notifier role ARN for SNS resource policy (Requirement 14.1)
    this.notifierRoleArn = notifier.role!.roleArn;

    // ─── Dead Letter Queue for Notifier async invocation failures ─────────────
    const notifierDlq = new sqs.Queue(this, 'NotifierDlq', {
      queueName: 'health-analyzer-notifier-dlq',
      retentionPeriod: cdk.Duration.days(14),
    });

    new lambda.EventInvokeConfig(this, 'NotifierInvokeConfig', {
      function: notifier,
      maxEventAge: cdk.Duration.hours(6),
      retryAttempts: 2,
      onFailure: new lambdaDestinations.SqsDestination(notifierDlq),
    });

    const notifierDlqAlarm = new cloudwatch.Alarm(this, 'NotifierDlqAlarm', {
      alarmName: 'health-analyzer-notifier-dlq-messages',
      alarmDescription: 'Notifier DLQ has messages — failed async invocations detected',
      metric: notifierDlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.seconds(60),
      }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    notifierDlqAlarm.addAlarmAction(new cloudwatch_actions.SnsAction(props.alarmTopic));

    // ─── Step Functions Workflow Definition ────────────────────────────────────
    //
    // Flow:
    //   Input (healthEvent) → TriggerInvestigation [wait for task token]
    //     → HasFindings?
    //       → NO  → NoImpact (skip)
    //       → YES → CreateOpsItem → SendNotifications
    //
    // Each Lambda task state has:
    //   - Retry: States.ALL, 3 attempts, 5s interval, backoff rate 2 (Req 13.3)
    //   - Catch: routes to HandleError state (Req 8.4, 8.5)
    //
    // State shape after TriggerInvestigation:
    //   The investigation result is placed at $.investigationResult
    //   while the original healthEvent is preserved at $.healthEvent
    //   (achieved via ResultPath on the TriggerInvestigation step)

    // ─── HandleError State ────────────────────────────────────────────────────
    // Publishes error notification to SNS with correlation ID (health event ARN),
    // failed function name, and error cause (Requirement 8.5)
    const handleError = new tasks.SnsPublish(this, 'HandleError', {
      topic: props.notificationTopic,
      message: sfn.TaskInput.fromObject({
        type: 'WORKFLOW_ERROR',
        correlationId: sfn.JsonPath.stringAt('$.healthEventArn'),
        failedFunction: sfn.JsonPath.stringAt('$.failedState'),
        error: sfn.JsonPath.stringAt('$.error'),
        cause: sfn.JsonPath.stringAt('$.cause'),
        message: 'Step Functions workflow task failed after exhausting retries',
      }),
      subject: 'Health Event Analyzer — Workflow Task Failure',
      comment: 'Publish error notification to SNS with correlation ID, function name, and error cause',
    });

    // Pass states to inject the failed function name before routing to HandleError.
    // When Catch fires with resultPath: '$.errorInfo', the state has the full execution
    // input plus $.errorInfo.Error and $.errorInfo.Cause. These Pass states reformat
    // the data for HandleError's expected input shape.
    const catchTriggerInvestigation = new sfn.Pass(this, 'CatchTriggerInvestigation', {
      comment: 'Inject correlation ID and failed function name for error reporting',
      parameters: {
        'healthEventArn.$': '$.eventId',
        'failedState': 'TriggerInvestigation',
        'error.$': '$.errorInfo.Error',
        'cause.$': '$.errorInfo.Cause',
      },
    }).next(handleError);

    const catchCreateOpsItem = new sfn.Pass(this, 'CatchCreateOpsItem', {
      comment: 'Inject correlation ID and failed function name for error reporting',
      parameters: {
        'healthEventArn.$': '$.eventId',
        'failedState': 'CreateOpsItem',
        'error.$': '$.errorInfo.Error',
        'cause.$': '$.errorInfo.Cause',
      },
    }).next(handleError);

    const catchSendNotifications = new sfn.Pass(this, 'CatchSendNotifications', {
      comment: 'Inject correlation ID and failed function name for error reporting',
      parameters: {
        'healthEventArn.$': '$.eventId',
        'failedState': 'SendNotifications',
        'error.$': '$.errorInfo.Error',
        'cause.$': '$.errorInfo.Cause',
      },
    }).next(handleError);

    // Retry configuration applied to all Lambda task states (Requirement 13.3)
    const retryProps: sfn.RetryProps = {
      errors: ['States.ALL'],
      maxAttempts: 3,
      interval: cdk.Duration.seconds(5),
      backoffRate: 2,
    };

    // Step 1: Trigger investigation and wait for callback
    // Input: { eventId, service, ..., affectedResources, ... } (the health event)
    // Output: investigation result placed at $.investigationResult (via ResultPath)
    // The health event ARN ($.eventId) flows through as correlation ID
    const triggerInvestigation = new tasks.LambdaInvoke(this, 'TriggerInvestigation', {
      lambdaFunction: investigationTrigger,
      integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
      payload: sfn.TaskInput.fromObject({
        'taskToken': sfn.JsonPath.taskToken,
        'healthEvent.$': '$',
      }),
      resultPath: '$.investigationResult',
      heartbeatTimeout: sfn.Timeout.duration(cdk.Duration.minutes(30)),
      comment: 'Trigger DevOps Agent investigation and wait for results',
    });

    // Add Retry and Catch to TriggerInvestigation
    triggerInvestigation.addRetry(retryProps);
    triggerInvestigation.addCatch(catchTriggerInvestigation, {
      resultPath: '$.errorInfo',
    });

    // Step 2: Create OpsItem in OpsCenter (only when impact detected)
    // Input: { healthEvent: {...}, investigationResult: {...} }
    // The Lambda receives both the original event context and investigation findings
    const createOpsItem = new tasks.LambdaInvoke(this, 'CreateOpsItem', {
      lambdaFunction: opsCenterCreator,
      payload: sfn.TaskInput.fromObject({
        'investigationResult.$': '$.investigationResult',
        'healthEvent.$': '$',
      }),
      resultSelector: {
        'opsItemId.$': '$.Payload.opsItemId',
        'opsItemUrl.$': '$.Payload.opsItemUrl',
      },
      resultPath: '$.opsItemResult',
      retryOnServiceExceptions: false, // Using explicit Retry instead
      comment: 'Create OpsItem in Systems Manager OpsCenter for impact tracking',
    });

    // Add Retry and Catch to CreateOpsItem
    createOpsItem.addRetry(retryProps);
    createOpsItem.addCatch(catchCreateOpsItem, {
      resultPath: '$.errorInfo',
    });

    // Step 3: Send notifications to affected teams
    // Input: receives the full state including opsItemResult (flattened by resultSelector)
    // The Notifier Lambda extracts what it needs (investigation result + opsItem link)
    const sendNotification = new tasks.LambdaInvoke(this, 'SendNotifications', {
      lambdaFunction: notifier,
      payload: sfn.TaskInput.fromObject({
        'investigationResult.$': '$.investigationResult',
        'opsItemId.$': '$.opsItemResult.opsItemId',
        'opsItemUrl.$': '$.opsItemResult.opsItemUrl',
      }),
      outputPath: '$.Payload',
      retryOnServiceExceptions: false, // Using explicit Retry instead
      comment: 'Route notifications to affected teams based on investigation findings (includes OpsItem link)',
    });

    // Add Retry and Catch to SendNotifications
    sendNotification.addRetry(retryProps);
    sendNotification.addCatch(catchSendNotifications, {
      resultPath: '$.errorInfo',
    });

    // Decision: skip OpsItem/notification only when priority is LOW or MINIMAL (non-actionable).
    // This ensures OpsCenter and notifications fire for all MEDIUM+ events,
    // matching the same threshold the agent uses to file Jira tickets.
    const hasFindings = new sfn.Choice(this, 'HasFindings?')
      .when(
        sfn.Condition.stringEquals('$.investigationResult.priority', 'LOW'),
        new sfn.Pass(this, 'NoImpact', {
          comment: 'Priority too low — skip OpsItem and notification',
        })
      )
      .when(
        sfn.Condition.stringEquals('$.investigationResult.priority', 'MINIMAL'),
        new sfn.Pass(this, 'MinimalImpact', {
          comment: 'Minimal priority — skip OpsItem and notification',
        })
      )
      .otherwise(createOpsItem.next(sendNotification));

    const definition = triggerInvestigation.next(hasFindings);

    // ─── State Machine ────────────────────────────────────────────────────────
    this.stateMachine = new sfn.StateMachine(this, 'StateMachine', {
      stateMachineName: 'health-event-impact-analyzer',
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.minutes(35),
      tracingEnabled: true,
      logs: {
        destination: new logs.LogGroup(this, 'StateMachineLogGroup', {
          logGroupName: '/aws/stepfunctions/health-event-analyzer',
          retention: logRetention,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
        }),
        level: sfn.LogLevel.ALL,
      },
    });

    // ─── Lambda: Investigation Callback ───────────────────────────────────────
    // Handles DevOps Agent investigation completion events and resumes Step Functions.
    // Calls DevOps Agent API (ListJournalRecords) to retrieve investigation findings
    // and correlate the callback with our workflow via healthEventArn.
    const investigationCallback = new NodejsFunction(this, 'InvestigationCallback', {
      runtime: Runtime.NODEJS_24_X,
      entry: path.join(lambdaDir, 'investigation-callback/index.ts'),
      handler: 'handler',
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      environment: {
        TASK_TOKEN_TABLE: '', // Set below
      },
      logGroup: new logs.LogGroup(this, 'InvestigationCallbackLogs', {
        retention: logRetention,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      description: 'Handles DevOps Agent investigation completion, retrieves findings via API, and resumes Step Functions',
      bundling: {
        forceDockerBundling: false,
        // @aws-sdk/client-devops-agent is NOT in Lambda runtime — must be bundled
        externalModules: ['@aws-sdk/client-dynamodb', '@aws-sdk/client-sfn'],
      },
    });

    this.stateMachine.grantTaskResponse(investigationCallback);
    this.investigationCallbackFunction = investigationCallback;

    // Grant permission to call DevOps Agent API (ListJournalRecords)
    investigationCallback.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['aidevops:ListJournalRecords'],
      resources: [`arn:aws:aidevops:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:agentspace/*`],
    }));

    // ─── DynamoDB: Task Token Table ───────────────────────────────────────────
    // Stores Step Functions task tokens keyed by investigation ID
    const isProduction = props.deployEnvironment === 'production';
    const tokenTable = new dynamodb.Table(this, 'TaskTokenTable', {
      tableName: 'health-analyzer-task-tokens',
      partitionKey: { name: 'investigationId', type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: 'ttl',
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    tokenTable.grantReadWriteData(investigationTrigger);
    tokenTable.grantReadWriteData(investigationCallback);

    investigationTrigger.addEnvironment('TASK_TOKEN_TABLE', tokenTable.tableName);
    investigationCallback.addEnvironment('TASK_TOKEN_TABLE', tokenTable.tableName);

    // ─── EventBridge: DevOps Agent Completion Events ──────────────────────────
    const devOpsAgentCompletionRule = new events.Rule(this, 'DevOpsAgentCompletionRule', {
      ruleName: 'health-analyzer-devops-agent-completion',
      description: 'Captures DevOps Agent investigation completion events',
      eventPattern: {
        source: ['aws.aidevops'],
        detailType: [
          'Investigation Completed',
          'Investigation Failed',
          'Investigation Timed Out',
          'Investigation Cancelled',
        ],
      },
    });

    devOpsAgentCompletionRule.addTarget(new targets.LambdaFunction(investigationCallback, {
      retryAttempts: 185,
      maxEventAge: cdk.Duration.hours(24),
    }));

    // ─── Dead Letter Queue for Investigation Callback async invocation failures ─
    const callbackDlq = new sqs.Queue(this, 'CallbackDlq', {
      queueName: 'health-analyzer-callback-dlq',
      retentionPeriod: cdk.Duration.days(14),
    });

    new lambda.EventInvokeConfig(this, 'CallbackInvokeConfig', {
      function: investigationCallback,
      maxEventAge: cdk.Duration.hours(6),
      retryAttempts: 2,
      onFailure: new lambdaDestinations.SqsDestination(callbackDlq),
    });

    const callbackDlqAlarm = new cloudwatch.Alarm(this, 'CallbackDlqAlarm', {
      alarmName: 'health-analyzer-callback-dlq-messages',
      alarmDescription: 'Investigation Callback DLQ has messages — failed async invocations detected',
      metric: callbackDlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.seconds(60),
      }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    callbackDlqAlarm.addAlarmAction(new cloudwatch_actions.SnsAction(props.alarmTopic));
  }
}
