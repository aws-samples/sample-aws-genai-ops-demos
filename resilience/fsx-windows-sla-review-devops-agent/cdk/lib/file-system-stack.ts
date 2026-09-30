import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as fsx from 'aws-cdk-lib/aws-fsx';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import * as path from 'path';
import { AlarmTrigger } from '../../../../shared/devops-agent/agent-space/cdk/alarm-trigger';
import { DirectoryFacts } from './directory-stack';

/**
 * The environment under review: one FSx for Windows file system joined to the self-managed
 * directory, the alarms the review looks for, the lifecycle canary that turns MISCONFIGURED
 * into an alarm, and the alarm -> webhook trigger chain (shared AlarmTrigger).
 *
 * Deliberate posture, all of it visible in the agent's report:
 *   Single-AZ 2, 32 GiB SSD, 8 MB/s      dimension 1 is a Warning in every report; the smallest
 *                                        and cheapest file system that can be reviewed
 *   daily backups kept 7 days            dimension 5 passes until the Lab turns them off
 *   one FreeStorageCapacity alarm         dimension 7 passes until the Lab deletes it
 *   no client ever mounts it              from day 14 the report adds the idle-file-system cost note
 *
 * The Lab (lab/handlers.py) changes the AD password, the backup retention and the alarm through
 * the FSx and CloudWatch APIs; nothing here is touched by CloudFormation afterwards. The alarm
 * the Lab recreates must match ALARM definition below (same name, metric, threshold).
 */
export interface FileSystemStackProps extends cdk.StackProps {
  projectName: string;
  vpc: ec2.Vpc;
  directory: DirectoryFacts;
  /** Agent Space stack outputs, passed by deploy-all as --context (empty before phase 1). */
  webhookUrl: string;
  webhookSecretArn: string;
  webhookSecretRegion: string;
}

export interface AlarmFacts {
  readonly topic: sns.Topic;
  readonly freeStorageAlarmName: string;
  readonly misconfiguredAlarmName: string;
  readonly metricsNamespace: string;
}

export const STORAGE_CAPACITY_GIB = 32;
export const THROUGHPUT_MBPS = 8;
export const BACKUP_RETENTION_DAYS = 7;
/** The review's 20% storage floor, in bytes (FreeStorageCapacity is reported in bytes). */
export const FREE_STORAGE_THRESHOLD_BYTES = Math.round(STORAGE_CAPACITY_GIB * 1024 ** 3 * 0.2);

export class FileSystemStack extends cdk.Stack {
  public readonly fileSystem: fsx.CfnFileSystem;
  public readonly alarms: AlarmFacts;
  public readonly triggerFunctionName: string;

  constructor(scope: Construct, id: string, props: FileSystemStackProps) {
    super(scope, id, props);
    const { projectName, vpc, directory } = props;

    const securityGroup = new ec2.SecurityGroup(this, 'FileSystemSg', {
      vpc,
      description: 'FSx for Windows file system: SMB from inside the VPC',
      allowAllOutbound: true,
    });
    securityGroup.addIngressRule(ec2.Peer.ipv4(vpc.vpcCidrBlock), ec2.Port.tcp(445), 'SMB from inside the VPC');

    this.fileSystem = new fsx.CfnFileSystem(this, 'FileSystem', {
      fileSystemType: 'WINDOWS',
      storageType: 'SSD',
      storageCapacity: STORAGE_CAPACITY_GIB,
      subnetIds: [vpc.privateSubnets[0].subnetId],
      securityGroupIds: [securityGroup.securityGroupId],
      tags: [{ key: 'Name', value: `${projectName}-file-system` }],
      windowsConfiguration: {
        deploymentType: 'SINGLE_AZ_2',
        throughputCapacity: THROUGHPUT_MBPS,
        automaticBackupRetentionDays: BACKUP_RETENTION_DAYS,
        dailyAutomaticBackupStartTime: '02:00',
        weeklyMaintenanceStartTime: '7:03:00',
        copyTagsToBackups: true,
        selfManagedActiveDirectoryConfiguration: {
          domainName: directory.domainName,
          dnsIps: [directory.dnsIp],
          userName: directory.serviceAccountSecret.secretValueFromJson('username').unsafeUnwrap(),
          // A CloudFormation dynamic reference: the password is resolved at deploy time and
          // never appears in the synthesized template.
          password: directory.serviceAccountSecret.secretValueFromJson('password').unsafeUnwrap(),
        },
      },
    });
    // FSx joins the domain at creation: the controller must exist (deploy-all also waits for it to be ready).
    this.fileSystem.node.addDependency(directory.domainController);

    // Alarm notifications: the trigger chain subscribes to this topic.
    const topic = new sns.Topic(this, 'AlarmsTopic', { displayName: `${projectName} alarms` });
    const metricsNamespace = projectName;

    // The one AWS/FSx alarm the review expects (dimension 7). The Lab deletes and recreates it.
    const freeStorageAlarm = new cloudwatch.Alarm(this, 'FreeStorageAlarm', {
      alarmName: `${projectName}-free-storage-capacity`,
      alarmDescription: `FSx file system ${projectName}: free storage below 20% of ${STORAGE_CAPACITY_GIB} GiB (the review's storage floor; writes fail at 0)`,
      metric: new cloudwatch.Metric({
        namespace: 'AWS/FSx',
        metricName: 'FreeStorageCapacity',
        dimensionsMap: { FileSystemId: this.fileSystem.ref },
        statistic: 'Minimum',
        period: cdk.Duration.minutes(5),
      }),
      threshold: FREE_STORAGE_THRESHOLD_BYTES,
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    freeStorageAlarm.addAlarmAction(new cloudwatchActions.SnsAction(topic));

    // FSx has no lifecycle metric: a canary reads DescribeFileSystems every minute and publishes one.
    const canary = new lambda.Function(this, 'LifecycleCanary', {
      runtime: lambda.Runtime.PYTHON_3_14,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'lifecycle-canary')),
      timeout: cdk.Duration.seconds(30),
      description: 'Publishes FileSystemMisconfigured (0/1) from the file system lifecycle every minute',
      environment: { FILE_SYSTEM_ID: this.fileSystem.ref, METRICS_NAMESPACE: metricsNamespace },
    });
    canary.addToRolePolicy(new iam.PolicyStatement({ actions: ['fsx:DescribeFileSystems'], resources: ['*'] }));
    canary.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': metricsNamespace } },
    }));
    new events.Rule(this, 'CanarySchedule', {
      schedule: events.Schedule.rate(cdk.Duration.minutes(1)),
      targets: [new targets.LambdaFunction(canary)],
    });

    const misconfiguredAlarm = new cloudwatch.Alarm(this, 'MisconfiguredAlarm', {
      alarmName: `${projectName}-file-system-misconfigured`,
      alarmDescription: `Critical: FSx file system ${projectName} reports lifecycle MISCONFIGURED (Active Directory unreachable or invalid service-account credentials); backups and maintenance fail while it lasts`,
      metric: new cloudwatch.Metric({
        namespace: metricsNamespace,
        metricName: 'FileSystemMisconfigured',
        dimensionsMap: { FileSystemId: this.fileSystem.ref },
        statistic: 'Maximum',
        period: cdk.Duration.minutes(1),
      }),
      threshold: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    misconfiguredAlarm.addAlarmAction(new cloudwatchActions.SnsAction(topic));
    misconfiguredAlarm.addOkAction(new cloudwatchActions.SnsAction(topic));

    // Incident RCA motion: ALARM notifications become signed incidents on the Agent Space webhook.
    // Phase 1 of deploy-all (before the Agent Space outputs exist) synthesizes with a placeholder.
    const webhookSecret = props.webhookSecretArn
      ? secretsmanager.Secret.fromSecretCompleteArn(this, 'WebhookSecret', props.webhookSecretArn)
      : secretsmanager.Secret.fromSecretNameV2(this, 'UnconfiguredWebhookSecret', `${projectName}/NOT_CONFIGURED`);
    const trigger = new AlarmTrigger(this, 'Trigger', {
      webhookUrl: props.webhookUrl,
      webhookSecret,
      webhookSecretRegion: props.webhookSecretRegion,
      topics: [topic],
      context: { 'File system': this.fileSystem.ref, 'Active Directory': directory.domainName },
    });
    this.triggerFunctionName = trigger.function.functionName;

    this.alarms = { topic, freeStorageAlarmName: freeStorageAlarm.alarmName, misconfiguredAlarmName: misconfiguredAlarm.alarmName, metricsNamespace };

    new cdk.CfnOutput(this, 'FileSystemId', { value: this.fileSystem.ref });
    new cdk.CfnOutput(this, 'FileSystemDnsName', { value: this.fileSystem.attrDnsName });
    new cdk.CfnOutput(this, 'AlarmsTopicArn', { value: topic.topicArn });
    new cdk.CfnOutput(this, 'FreeStorageAlarmName', { value: freeStorageAlarm.alarmName });
    new cdk.CfnOutput(this, 'MisconfiguredAlarmName', { value: misconfiguredAlarm.alarmName });
    new cdk.CfnOutput(this, 'TriggerFunctionName', { value: trigger.function.functionName });
  }
}
