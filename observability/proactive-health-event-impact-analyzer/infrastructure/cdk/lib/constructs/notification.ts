import * as cdk from 'aws-cdk-lib';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';

export interface NotificationProps {
  email: string;
  /** Deployment environment — drives deletion protection and removal policy */
  deployEnvironment: 'production' | 'staging';
}

export class Notification extends Construct {
  public readonly topic: sns.Topic;
  public readonly teamsTable: dynamodb.Table;
  public readonly agentSpacesTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: NotificationProps) {
    super(scope, id);

    // Main notification topic (catch-all for unrouted notifications)
    // Encryption at rest using AWS managed SNS key (Requirement 3.4)
    this.topic = new sns.Topic(this, 'HealthImpactTopic', {
      topicName: 'health-event-impact-alerts',
      displayName: 'AWS Health Event Impact Alerts',
      masterKey: kms.Alias.fromAliasName(this, 'SnsKey', 'alias/aws/sns'),
    });

    // Conditionally add email subscription only when email parameter is provided
    const emailCondition = new cdk.CfnCondition(this, 'HasEmail', {
      expression: cdk.Fn.conditionNot(cdk.Fn.conditionEquals(props.email, '')),
    });

    const emailSubscription = new cdk.aws_sns.CfnSubscription(this, 'EmailSubscription', {
      topicArn: this.topic.topicArn,
      protocol: 'email',
      endpoint: props.email,
    });
    emailSubscription.cfnOptions.condition = emailCondition;

    const isProduction = props.deployEnvironment === 'production';

    // Teams configuration table — stores team notification preferences
    this.teamsTable = new dynamodb.Table(this, 'TeamsTable', {
      tableName: 'health-analyzer-teams',
      partitionKey: { name: 'teamId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Agent spaces routing table — maps AWS account IDs to DevOps Agent spaces
    // Used for hybrid multi-account routing: per-account override or shared default
    this.agentSpacesTable = new dynamodb.Table(this, 'AgentSpacesTable', {
      tableName: 'health-analyzer-agent-spaces',
      partitionKey: { name: 'accountId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
  }
}
