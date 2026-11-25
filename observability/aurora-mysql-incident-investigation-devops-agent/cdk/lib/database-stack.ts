import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { AlarmTrigger } from '../../../../shared/devops-agent/agent-space/cdk/alarm-trigger';
import {
  BUSINESS_TAGS, CONNECTIONS_THRESHOLD, LOAD_MAX_STORM_SECONDS, LOAD_TARGET_CONNECTIONS, MAX_CONNECTIONS,
} from './constants';

/**
 * The environment under investigation (main stack, carries the tracking tag):
 *
 *   VPC                two isolated subnets, no NAT gateway, no internet path
 *   Aurora MySQL       one writer, db.t4g.medium, Performance Insights on, the business tags
 *                      on the CLUSTER only (the skill reads the cluster; tagging the instance
 *                      too would leave a second copy the business-tags-removed scenario misses)
 *   Load function      VPC-attached, opens and holds connections to the writer while its
 *                      reserved concurrency is above 0 (the Lab's on/off switch); reaches
 *                      Secrets Manager and Lambda through interface endpoints (one AZ)
 *   Alarm              DatabaseConnections on the writer -> SNS -> shared AlarmTrigger -> webhook
 *
 * No application and no compute for the services named in DependentServices: that tag is a
 * claim the skill has to label unverified.
 */
export interface DatabaseStackProps extends cdk.StackProps {
  projectName: string;
  /** Agent Space stack outputs, passed by deploy-all as --context (placeholders before). */
  webhookUrl: string;
  webhookSecretArn: string;
  webhookSecretRegion: string;
}

const LOAD_LAMBDA_DIR = path.join(__dirname, '..', 'lambda', 'connection-load');

/** The RDS global CA bundle, shipped in the asset so the load function verifies the writer's certificate. */
const RDS_CA_BUNDLE_URL = 'https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem';
const RDS_CA_BUNDLE_FILE = 'global-bundle.pem';
/** Python one-liner that downloads the CA bundle to argv[1] (same code locally and in the CDK image). */
const DOWNLOAD_CA_PY = `import sys, urllib.request; urllib.request.urlretrieve('${RDS_CA_BUNDLE_URL}', sys.argv[1])`;

/** PyMySQL is pure Python: pip it locally into the asset, add the CA bundle; fall back to the CDK image (Docker). */
function bundleLoadFunction(outputDir: string): boolean {
  const pipArgs = ['-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--no-deps',
    '--target', outputDir, '-r', path.join(LOAD_LAMBDA_DIR, 'requirements.txt')];
  const caArgs = ['-c', DOWNLOAD_CA_PY, path.join(outputDir, RDS_CA_BUNDLE_FILE)];
  const run = (python: string, args: string[]) => spawnSync(python, args, { stdio: ['ignore', 'pipe', 'pipe'] }).status === 0;
  const ok = ['python', 'python3'].some(python => run(python, pipArgs) && run(python, caArgs));
  if (!ok) {
    return false;
  }
  fs.copyFileSync(path.join(LOAD_LAMBDA_DIR, 'index.py'), path.join(outputDir, 'index.py'));
  return true;
}

export class DatabaseStack extends cdk.Stack {
  public readonly clusterIdentifier: string;
  public readonly writerIdentifier: string;
  public readonly clusterArn: string;
  public readonly loadFunctionName: string;
  public readonly connectionsAlarmName: string;
  public readonly triggerFunctionName: string;

  constructor(scope: Construct, id: string, props: DatabaseStackProps) {
    super(scope, id, props);
    const { projectName } = props;
    this.clusterIdentifier = `${projectName}-cluster`;
    this.writerIdentifier = `${projectName}-writer`;
    this.loadFunctionName = `${projectName}-connection-load`;

    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [{ name: 'isolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 }],
    });

    const loadSg = new ec2.SecurityGroup(this, 'LoadSg', {
      vpc, description: 'Connection load function: MySQL to the cluster, HTTPS to the endpoints', allowAllOutbound: false,
    });
    const endpointSg = new ec2.SecurityGroup(this, 'EndpointSg', {
      vpc, description: 'Interface endpoints: HTTPS from the load function', allowAllOutbound: false,
    });
    // One AZ is enough for a demo; the load function reaches it from either subnet.
    const endpointSubnets = { subnets: [vpc.isolatedSubnets[0]] };
    for (const [name, service] of [['SecretsManager', ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER], ['Lambda', ec2.InterfaceVpcEndpointAwsService.LAMBDA]] as const) {
      const endpoint = vpc.addInterfaceEndpoint(`${name}Endpoint`, {
        service, subnets: endpointSubnets, privateDnsEnabled: true, securityGroups: [endpointSg], open: false,
      });
      endpoint.connections.allowDefaultPortFrom(loadSg, `HTTPS from the load function to ${name}`);
    }

    const cluster = new rds.DatabaseCluster(this, 'Cluster', {
      engine: rds.DatabaseClusterEngine.auroraMysql({ version: rds.AuroraMysqlEngineVersion.VER_3_10_3 }),
      clusterIdentifier: this.clusterIdentifier,
      credentials: rds.Credentials.fromGeneratedSecret('admin'),
      writer: rds.ClusterInstance.provisioned('Writer', {
        instanceIdentifier: this.writerIdentifier,
        instanceType: ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MEDIUM),
        enablePerformanceInsights: true,
        performanceInsightRetention: rds.PerformanceInsightRetention.DEFAULT,
        publiclyAccessible: false,
      }),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      storageEncrypted: true,
      backup: { retention: cdk.Duration.days(1) },
      deletionProtection: false,
      // Teardown removes everything: no final snapshot left behind.
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    cluster.connections.allowDefaultPortFrom(loadSg, 'MySQL from the connection load function');
    cluster.secret?.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);
    for (const [key, value] of Object.entries(BUSINESS_TAGS)) {
      cdk.Tags.of(cluster).add(key, value, { includeResourceTypes: ['AWS::RDS::DBCluster'] });
    }
    this.clusterArn = this.formatArn({
      service: 'rds', resource: 'cluster', resourceName: this.clusterIdentifier, arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
    });

    // The load function: its reserved concurrency is the switch (0 = off, the Lab sets it above 0).
    const loadFunctionArn = this.formatArn({
      service: 'lambda', resource: 'function', resourceName: this.loadFunctionName, arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
    });
    const load = new lambda.Function(this, 'ConnectionLoad', {
      functionName: this.loadFunctionName,
      description: `Opens and holds up to ${LOAD_TARGET_CONNECTIONS} MySQL connections to the writer while its reserved concurrency is above 0`,
      runtime: lambda.Runtime.PYTHON_3_14,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(LOAD_LAMBDA_DIR, {
        bundling: {
          image: lambda.Runtime.PYTHON_3_14.bundlingImage,
          command: ['bash', '-c', [
            'pip install --quiet --no-deps -r requirements.txt -t /asset-output',
            `python -c "${DOWNLOAD_CA_PY}" /asset-output/${RDS_CA_BUNDLE_FILE}`,
            'cp index.py /asset-output/',
          ].join(' && ')],
          local: { tryBundle: bundleLoadFunction },
        },
      }),
      timeout: cdk.Duration.minutes(15),
      memorySize: 256,
      reservedConcurrentExecutions: 0,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [loadSg],
      environment: {
        CLUSTER_ENDPOINT: cluster.clusterEndpoint.hostname,
        SECRET_ARN: cluster.secret!.secretArn,
        TARGET_CONNECTIONS: String(LOAD_TARGET_CONNECTIONS),
        MAX_STORM_SECONDS: String(LOAD_MAX_STORM_SECONDS),
      },
      logGroup: new logs.LogGroup(this, 'ConnectionLoadLogs', {
        retention: logs.RetentionDays.ONE_WEEK, removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      // A hand-over event that meets reserved concurrency 0 (the storm was reverted) is dropped, not retried for hours.
      maxEventAge: cdk.Duration.minutes(2),
      retryAttempts: 0,
    });
    cluster.secret!.grantRead(load);
    // Built from the fixed name: the function's own ARN token here would be a dependency cycle.
    load.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ReadTheSwitchAndHandOver',
      actions: ['lambda:GetFunctionConcurrency', 'lambda:InvokeFunction'],
      resources: [loadFunctionArn],
    }));

    // Alarm notifications: the trigger chain subscribes to this topic.
    const topic = new sns.Topic(this, 'AlarmsTopic', { displayName: `${projectName} alarms` });
    this.connectionsAlarmName = `${projectName}-writer-database-connections`;
    const connectionsAlarm = new cloudwatch.Alarm(this, 'ConnectionsAlarm', {
      alarmName: this.connectionsAlarmName,
      alarmDescription: `Aurora MySQL writer ${this.writerIdentifier} (cluster ${this.clusterIdentifier}): DatabaseConnections above ${CONNECTIONS_THRESHOLD} for 2 minutes. max_connections is about ${MAX_CONNECTIONS} on db.t4g.medium; near the limit new clients are refused with Too many connections.`,
      metric: new cloudwatch.Metric({
        namespace: 'AWS/RDS',
        metricName: 'DatabaseConnections',
        dimensionsMap: { DBInstanceIdentifier: this.writerIdentifier },
        statistic: 'Maximum',
        period: cdk.Duration.minutes(1),
      }),
      threshold: CONNECTIONS_THRESHOLD,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 2,
      datapointsToAlarm: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    connectionsAlarm.addAlarmAction(new cloudwatchActions.SnsAction(topic));

    // Incident RCA motion: ALARM notifications become signed incidents on the Agent Space webhook.
    // Before the Agent Space outputs exist (placeholder synth, teardown) the secret is a placeholder.
    const webhookSecret = props.webhookSecretArn
      ? secretsmanager.Secret.fromSecretCompleteArn(this, 'WebhookSecret', props.webhookSecretArn)
      : secretsmanager.Secret.fromSecretNameV2(this, 'UnconfiguredWebhookSecret', `${projectName}/NOT_CONFIGURED`);
    const trigger = new AlarmTrigger(this, 'Trigger', {
      webhookUrl: props.webhookUrl,
      webhookSecret,
      webhookSecretRegion: props.webhookSecretRegion,
      topics: [topic],
      context: { 'Aurora MySQL cluster': this.clusterIdentifier, 'Writer instance': this.writerIdentifier },
    });
    this.triggerFunctionName = trigger.function.functionName;

    new cdk.CfnOutput(this, 'ClusterIdentifier', { value: this.clusterIdentifier });
    new cdk.CfnOutput(this, 'WriterIdentifier', { value: this.writerIdentifier });
    new cdk.CfnOutput(this, 'ClusterEndpoint', { value: cluster.clusterEndpoint.hostname });
    new cdk.CfnOutput(this, 'LoadFunctionName', { value: load.functionName });
    new cdk.CfnOutput(this, 'ConnectionsAlarmName', { value: connectionsAlarm.alarmName });
    new cdk.CfnOutput(this, 'AlarmsTopicArn', { value: topic.topicArn });
    new cdk.CfnOutput(this, 'TriggerFunctionName', { value: trigger.function.functionName });
  }
}
