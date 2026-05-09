import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import { Construct } from 'constructs';

export interface ObservabilityStackProps extends cdk.StackProps {
  /** Lambda function names whose Duration/Errors/Throttles get a widget. */
  lambdaNames: {
    ingest: string;
    remediationContext: string;
    devOpsTrigger: string;
    dashboardApi: string;
  };
  /** DynamoDB table names whose capacity usage gets a widget. */
  tableNames: {
    findings: string;
    costEvents: string;
  };
  /** ECS cluster whose task count gets a widget. */
  scannerClusterName: string;
  /** Bedrock model ID whose invocation latency/errors get a widget. */
  bedrockModelId: string;
}

/**
 * Observability Stack
 *
 * Provisions a single CloudWatch Dashboard named `prowler-security-{region}`
 * with per-component panels so the demo's health is visible without clicking
 * around CloudWatch:
 *
 *   1. Lambda health — Duration / Errors / Throttles for the 4 Lambdas.
 *   2. Bedrock — Nova Pro invocation latency + client-side errors.
 *   3. DynamoDB — consumed read/write capacity for findings + cost-events.
 *   4. Fargate — running/pending scanner tasks from ECS.
 *
 * The dashboard opens directly at the URL printed in the deployment output
 * (ApiStack surfaces it as a CfnOutput).
 */
export class ObservabilityStack extends cdk.Stack {
  public readonly dashboardUrl: string;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);

    const { lambdaNames, tableNames, scannerClusterName, bedrockModelId } = props;

    const dashboardName = `prowler-security-${cdk.Aws.REGION}`;

    const lambdaWidgets = Object.entries(lambdaNames).map(([logicalName, functionName]) => {
      const metricProps = {
        namespace: 'AWS/Lambda',
        dimensionsMap: { FunctionName: functionName },
        statistic: 'Sum',
        period: cdk.Duration.minutes(5),
      } as const;
      return new cloudwatch.GraphWidget({
        title: `Lambda · ${logicalName}`,
        width: 12,
        height: 6,
        left: [
          new cloudwatch.Metric({ ...metricProps, metricName: 'Invocations', label: 'Invocations' }),
          new cloudwatch.Metric({ ...metricProps, metricName: 'Errors', label: 'Errors', color: cloudwatch.Color.RED }),
          new cloudwatch.Metric({ ...metricProps, metricName: 'Throttles', label: 'Throttles', color: cloudwatch.Color.ORANGE }),
        ],
        right: [
          new cloudwatch.Metric({
            namespace: 'AWS/Lambda',
            dimensionsMap: { FunctionName: functionName },
            metricName: 'Duration',
            statistic: 'Average',
            period: cdk.Duration.minutes(5),
            label: 'Avg duration (ms)',
            color: cloudwatch.Color.BLUE,
          }),
        ],
      });
    });

    const bedrockWidget = new cloudwatch.GraphWidget({
      title: `Bedrock · ${bedrockModelId}`,
      width: 12,
      height: 6,
      left: [
        new cloudwatch.Metric({
          namespace: 'AWS/Bedrock',
          metricName: 'Invocations',
          dimensionsMap: { ModelId: bedrockModelId },
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
        }),
        new cloudwatch.Metric({
          namespace: 'AWS/Bedrock',
          metricName: 'InvocationClientErrors',
          dimensionsMap: { ModelId: bedrockModelId },
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
          color: cloudwatch.Color.RED,
        }),
        new cloudwatch.Metric({
          namespace: 'AWS/Bedrock',
          metricName: 'InvocationThrottles',
          dimensionsMap: { ModelId: bedrockModelId },
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
          color: cloudwatch.Color.ORANGE,
        }),
      ],
      right: [
        new cloudwatch.Metric({
          namespace: 'AWS/Bedrock',
          metricName: 'InvocationLatency',
          dimensionsMap: { ModelId: bedrockModelId },
          statistic: 'Average',
          period: cdk.Duration.minutes(5),
          label: 'Avg latency (ms)',
          color: cloudwatch.Color.BLUE,
        }),
      ],
    });

    const ddbWidget = new cloudwatch.GraphWidget({
      title: 'DynamoDB · consumed capacity',
      width: 12,
      height: 6,
      left: Object.entries(tableNames).map(([logicalName, tableName], i) =>
        new cloudwatch.Metric({
          namespace: 'AWS/DynamoDB',
          metricName: 'ConsumedReadCapacityUnits',
          dimensionsMap: { TableName: tableName },
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
          label: `${logicalName} · read`,
          color: i === 0 ? cloudwatch.Color.BLUE : cloudwatch.Color.GREEN,
        }),
      ),
      right: Object.entries(tableNames).map(([logicalName, tableName], i) =>
        new cloudwatch.Metric({
          namespace: 'AWS/DynamoDB',
          metricName: 'ConsumedWriteCapacityUnits',
          dimensionsMap: { TableName: tableName },
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
          label: `${logicalName} · write`,
          color: i === 0 ? cloudwatch.Color.ORANGE : cloudwatch.Color.PURPLE,
        }),
      ),
    });

    const fargateWidget = new cloudwatch.GraphWidget({
      title: 'ECS Fargate · scanner cluster',
      width: 12,
      height: 6,
      left: [
        new cloudwatch.Metric({
          namespace: 'AWS/ECS',
          metricName: 'CPUUtilization',
          dimensionsMap: { ClusterName: scannerClusterName },
          statistic: 'Average',
          period: cdk.Duration.minutes(5),
          label: 'CPU %',
        }),
        new cloudwatch.Metric({
          namespace: 'AWS/ECS',
          metricName: 'MemoryUtilization',
          dimensionsMap: { ClusterName: scannerClusterName },
          statistic: 'Average',
          period: cdk.Duration.minutes(5),
          label: 'Memory %',
          color: cloudwatch.Color.GREEN,
        }),
      ],
    });

    const headerMarkdown = new cloudwatch.TextWidget({
      markdown: [
        `# Prowler Security Demo — Observability`,
        '',
        `**Region:** ${cdk.Aws.REGION}   ·   **Bedrock model:** \`${bedrockModelId}\`   ·   **Dashboard:** \`${dashboardName}\``,
        '',
        'End-to-end health of the Prowler Security Findings Agent demo.',
        'Open per-component logs by clicking a widget title.',
      ].join('\n'),
      width: 24,
      height: 3,
    });

    const dashboard = new cloudwatch.Dashboard(this, 'ProwlerSecurityDashboard', {
      dashboardName,
      widgets: [
        [headerMarkdown],
        lambdaWidgets.slice(0, 2),
        lambdaWidgets.slice(2, 4),
        [bedrockWidget, ddbWidget],
        [fargateWidget],
      ],
    });

    this.dashboardUrl = `https://${cdk.Aws.REGION}.console.aws.amazon.com/cloudwatch/home?region=${cdk.Aws.REGION}#dashboards:name=${dashboardName}`;

    new cdk.CfnOutput(this, 'DashboardName', { value: dashboardName });
    new cdk.CfnOutput(this, 'DashboardUrl', { value: this.dashboardUrl });
    // Suppress the unused-variable TS warning — dashboard holds the CFN
    // resource reference regardless of whether we reference it again below.
    void dashboard;
  }
}
