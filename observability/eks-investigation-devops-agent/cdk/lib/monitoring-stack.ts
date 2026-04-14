import * as cdk from 'aws-cdk-lib';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import { Construct } from 'constructs';

export interface MonitoringStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
}

export class MonitoringStack extends cdk.Stack {
  public readonly criticalAlarmsTopicArn: string;
  public readonly paymentProcessorLogGroupArn: string;

  constructor(scope: Construct, id: string, props: MonitoringStackProps) {
    super(scope, id, props);

    const { environment, projectName } = props;

    // -----------------------------------------------------------------------
    // SNS Topic for Critical Alarms
    // -----------------------------------------------------------------------
    const criticalAlarmsTopic = new sns.Topic(this, 'CriticalAlarmsTopic', {
      topicName: `${projectName}-${environment}-critical-alarms`,
      displayName: `${projectName} ${environment} Critical Alarms`,
    });

    // Topic policy — allow CloudWatch alarms to publish
    criticalAlarmsTopic.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'AllowCloudWatchAlarms',
      effect: iam.Effect.ALLOW,
      principals: [new iam.ServicePrincipal('cloudwatch.amazonaws.com')],
      actions: ['sns:Publish'],
      resources: [criticalAlarmsTopic.topicArn],
      conditions: {
        ArnLike: {
          'aws:SourceArn': `arn:aws:cloudwatch:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:alarm:*`,
        },
      },
    }));

    // -----------------------------------------------------------------------
    // CloudWatch Log Group — payment-processor, 7-day retention
    // -----------------------------------------------------------------------
    const paymentProcessorLogGroup = new logs.LogGroup(this, 'PaymentProcessorLogGroup', {
      logGroupName: `/aws/eks/${projectName}-${environment}/payment-processor`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // -----------------------------------------------------------------------
    // CloudWatch Log Group — merchant-gateway (created by Fluent Bit, imported here)
    // -----------------------------------------------------------------------
    const merchantGatewayLogGroup = logs.LogGroup.fromLogGroupName(
      this, 'MerchantGatewayLogGroup',
      `/aws/eks/${projectName}-${environment}/merchant-gateway`,
    );

    // -----------------------------------------------------------------------
    // Metric Filter — database connection errors
    // Matches filter pattern from original CloudFormation observability.yaml
    // -----------------------------------------------------------------------
    const dbConnectionErrorMetric = new logs.MetricFilter(this, 'DatabaseConnectionErrorMetricFilter', {
      logGroup: paymentProcessorLogGroup,
      filterPattern: logs.FilterPattern.literal(
        '?ECONNREFUSED ?ETIMEDOUT ?"connection refused" ?"authentication failed" ?"Connection terminated" ?"too many connections" ?"connection pool"',
      ),
      metricNamespace: `${projectName}/${environment}`,
      metricName: 'DatabaseConnectionErrors',
      metricValue: '1',
      defaultValue: 0,
    });

    // -----------------------------------------------------------------------
    // CloudWatch Alarm — Sum >= 1 in 60s period
    // -----------------------------------------------------------------------
    const dbConnectionErrorAlarm = new cloudwatch.Alarm(this, 'DatabaseConnectionErrorAlarm', {
      alarmName: `${projectName}-${environment}-database-connection-errors`,
      alarmDescription: 'CRITICAL: Database connection errors detected - payment processing impacted',
      metric: dbConnectionErrorMetric.metric({
        statistic: 'Sum',
        period: cdk.Duration.seconds(60),
      }),
      evaluationPeriods: 1,
      threshold: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    dbConnectionErrorAlarm.addAlarmAction(new cloudwatchActions.SnsAction(criticalAlarmsTopic));
    dbConnectionErrorAlarm.addOkAction(new cloudwatchActions.SnsAction(criticalAlarmsTopic));

    // -----------------------------------------------------------------------
    // Metric Filter + Alarm — DNS resolution errors
    // Uses a custom metric published by the Failure Simulator Lambda
    // (Fluent Bit can't ship logs when DNS is down, so log-based filters won't work)
    // -----------------------------------------------------------------------
    const dnsErrorAlarm = new cloudwatch.Alarm(this, 'DnsResolutionErrorAlarm', {
      alarmName: `${projectName}-${environment}-dns-resolution-errors`,
      alarmDescription: 'CRITICAL: DNS resolution failures detected - service discovery broken',
      metric: new cloudwatch.Metric({
        namespace: `${projectName}/${environment}`,
        metricName: 'DnsResolutionErrors',
        statistic: 'Sum',
        period: cdk.Duration.seconds(60),
      }),
      evaluationPeriods: 1,
      threshold: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    dnsErrorAlarm.addAlarmAction(new cloudwatchActions.SnsAction(criticalAlarmsTopic));
    dnsErrorAlarm.addOkAction(new cloudwatchActions.SnsAction(criticalAlarmsTopic));

    // -----------------------------------------------------------------------
    // Expose properties for cross-stack references
    // -----------------------------------------------------------------------
    this.criticalAlarmsTopicArn = criticalAlarmsTopic.topicArn;
    this.paymentProcessorLogGroupArn = paymentProcessorLogGroup.logGroupArn;

    // -----------------------------------------------------------------------
    // CloudFormation Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'CriticalAlarmsTopicArn', {
      description: 'SNS Topic ARN for critical alarms',
      value: criticalAlarmsTopic.topicArn,
    });

    new cdk.CfnOutput(this, 'PaymentProcessorLogGroupArn', {
      description: 'CloudWatch Log Group ARN for Payment Processor',
      value: paymentProcessorLogGroup.logGroupArn,
    });

    new cdk.CfnOutput(this, 'DatabaseConnectionErrorAlarmArn', {
      description: 'Database Connection Error Alarm ARN',
      value: dbConnectionErrorAlarm.alarmArn,
    });
  }
}
