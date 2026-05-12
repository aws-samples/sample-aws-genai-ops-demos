import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as events from 'aws-cdk-lib/aws-events';
import * as eventsTargets from 'aws-cdk-lib/aws-events-targets';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import { AwsCustomResource, AwsCustomResourcePolicy, PhysicalResourceId } from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';

export interface ScannerStackProps extends cdk.StackProps {
  rawReportsBucketName: string;
  scanSchedule: string;
}

/**
 * Scanner Stack
 *
 * Runs Prowler against the current AWS account as a Fargate task.
 *
 *   - ECR repository `prowler-security-scanner` holds the scanner image.
 *   - CodeBuild project `prowler-security-image-build` builds and pushes the
 *     image on demand (invoked by scripts/build-scanner-image.sh). No local
 *     Docker needed — mirrors the EKS demo's pipeline-stack pattern.
 *   - ECS cluster + Fargate task definition with IAM role granting
 *     SecurityAudit + ViewOnlyAccess + Prowler-specific IAM report generation.
 *   - EventBridge schedule (default: daily 06:00 UTC) invokes the task.
 *   - `scriptedStartArn`/subnet/SG outputs are consumed by ApiStack so the
 *     dashboard can start on-demand scans via ecs:RunTask.
 *
 * A default VPC with two public subnets is provisioned so Prowler can reach
 * AWS service endpoints without a NAT gateway; Fargate platform 1.4+
 * assignPublicIp=ENABLED gives the task an egress IP.
 */
export class ScannerStack extends cdk.Stack {
  public readonly clusterArn: string;
  public readonly taskDefinitionArn: string;
  public readonly subnetIds: string[];
  public readonly securityGroupId: string;
  public readonly logGroupName: string;

  constructor(scope: Construct, id: string, props: ScannerStackProps) {
    super(scope, id, props);

    const { rawReportsBucketName, scanSchedule } = props;

    const ecrRepo = new ecr.Repository(this, 'ScannerRepo', {
      repositoryName: 'prowler-security-scanner',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      imageScanOnPush: true,
    });

    // Build project — builds the scanner image from the S3 source zip uploaded
    // by scripts/build-scanner-image.sh. Output image tagged :latest.
    const buildProject = new codebuild.Project(this, 'ScannerImageBuild', {
      projectName: 'prowler-security-image-build',
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        privileged: true, // required for docker build
        computeType: codebuild.ComputeType.SMALL,
      },
      source: codebuild.Source.s3({
        bucket: cdk.aws_s3.Bucket.fromBucketName(this, 'CbSource', rawReportsBucketName),
        path: 'codebuild-sources/scanner.zip',
      }),
      environmentVariables: {
        AWS_DEFAULT_REGION: { value: cdk.Aws.REGION },
        AWS_ACCOUNT_ID: { value: cdk.Aws.ACCOUNT_ID },
        ECR_REPO_URI: { value: ecrRepo.repositoryUri },
        IMAGE_TAG: { value: 'latest' },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          pre_build: {
            commands: [
              'echo "Logging in to Amazon ECR..."',
              'aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com"',
            ],
          },
          build: {
            commands: [
              'echo "Building Prowler scanner image..."',
              'docker build -t "$ECR_REPO_URI:$IMAGE_TAG" .',
            ],
          },
          post_build: {
            commands: [
              'echo "Pushing to ECR..."',
              'docker push "$ECR_REPO_URI:$IMAGE_TAG"',
              'echo "Image pushed: $ECR_REPO_URI:$IMAGE_TAG"',
            ],
          },
        },
      }),
    });
    ecrRepo.grantPullPush(buildProject);

    // Default VPC — simple public-subnet-only setup keeps the demo cheap
    // (no NAT gateway). The task gets a public IP for egress.
    const vpc = new ec2.Vpc(this, 'ScannerVpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
      ],
    });

    const securityGroup = new ec2.SecurityGroup(this, 'ScannerSg', {
      vpc,
      description: 'Prowler scanner egress-only SG',
      allowAllOutbound: true,
    });

    const cluster = new ecs.Cluster(this, 'ScannerCluster', {
      clusterName: 'prowler-security-scanner',
      vpc,
    });

    const taskRole = new iam.Role(this, 'ScannerTaskRole', {
      roleName: `prowler-security-scanner-task-${cdk.Aws.REGION}`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: 'IAM role Prowler assumes inside the Fargate task to scan the account',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('SecurityAudit'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('job-function/ViewOnlyAccess'),
      ],
    });

    // Permissions Prowler explicitly requires that aren't in SecurityAudit.
    taskRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ProwlerExtras',
      effect: iam.Effect.ALLOW,
      actions: [
        'iam:GenerateCredentialReport',
        'iam:GenerateServiceLastAccessedDetails',
        'account:Get*',
        'account:List*',
        'appstream:Describe*',
        'appstream:List*',
        'cognito-idp:GetUserPoolMfaConfig',
        'ds:ListAuthorizedApplications',
        'ds:DescribeRoles',
        'ec2:GetEbsEncryptionByDefault',
        'elasticfilesystem:DescribeBackupPolicy',
        'glue:GetConnections',
        'glue:GetSecurityConfiguration*',
        'glue:SearchTables',
        'lambda:GetFunction*',
        'macie2:GetMacieSession',
        's3:GetAccountPublicAccessBlock',
        'shield:DescribeProtection',
        'shield:GetSubscriptionState',
        'securityhub:BatchImportFindings',
        'securityhub:GetFindings',
        'ssm:GetDocument',
        'support:Describe*',
        'tag:GetTagKeys',
      ],
      resources: ['*'],
    }));

    // Upload OCSF reports to the raw-reports bucket.
    taskRole.addToPolicy(new iam.PolicyStatement({
      sid: 'WriteRawReports',
      effect: iam.Effect.ALLOW,
      actions: ['s3:PutObject', 's3:PutObjectAcl'],
      resources: [`arn:aws:s3:::${rawReportsBucketName}/*`],
    }));

    const logGroup = new logs.LogGroup(this, 'ScannerLogGroup', {
      logGroupName: '/aws/ecs/prowler-security-scanner',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const taskDefinition = new ecs.FargateTaskDefinition(this, 'ScannerTaskDef', {
      family: 'prowler-security-scanner',
      cpu: 1024,
      memoryLimitMiB: 2048,
      taskRole,
    });

    taskDefinition.addContainer('Prowler', {
      image: ecs.ContainerImage.fromEcrRepository(ecrRepo, 'latest'),
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'prowler', logGroup }),
      environment: {
        RAW_REPORTS_BUCKET: rawReportsBucketName,
        AWS_REGION: cdk.Aws.REGION,
        AWS_ACCOUNT_ID: cdk.Aws.ACCOUNT_ID,
      },
    });

    // Scheduled scans — EventBridge → ECS RunTask
    const rule = new events.Rule(this, 'ScheduledScanRule', {
      ruleName: 'prowler-security-scheduled-scan',
      schedule: events.Schedule.expression(scanSchedule),
      description: 'Triggers Prowler Fargate task on a recurring schedule',
    });
    rule.addTarget(new eventsTargets.EcsTask({
      cluster,
      taskDefinition,
      subnetSelection: { subnetType: ec2.SubnetType.PUBLIC },
      assignPublicIp: true,
      securityGroups: [securityGroup],
      launchType: ecs.LaunchType.FARGATE,
      platformVersion: ecs.FargatePlatformVersion.LATEST,
    }));

    this.clusterArn = cluster.clusterArn;
    this.taskDefinitionArn = taskDefinition.taskDefinitionArn;
    this.subnetIds = vpc.publicSubnets.map((s) => s.subnetId);
    this.securityGroupId = securityGroup.securityGroupId;
    this.logGroupName = logGroup.logGroupName;

    // Kick off the first scan as soon as the stack is created. Demos otherwise
    // open to an empty dashboard until the EventBridge schedule or the user
    // clicks "Run scan now", which makes the first-time experience feel slow
    // ("it doesn't work" vs "it's warming up"). Only runs onCreate — never on
    // update or replace — so re-deploys don't queue extra scans.
    const firstScan = new AwsCustomResource(this, 'AutoStartFirstScan', {
      onCreate: {
        service: 'ECS',
        action: 'runTask',
        parameters: {
          cluster: cluster.clusterArn,
          taskDefinition: taskDefinition.taskDefinitionArn,
          launchType: 'FARGATE',
          platformVersion: 'LATEST',
          count: 1,
          networkConfiguration: {
            awsvpcConfiguration: {
              subnets: vpc.publicSubnets.map((s) => s.subnetId),
              securityGroups: [securityGroup.securityGroupId],
              assignPublicIp: 'ENABLED',
            },
          },
        },
        physicalResourceId: PhysicalResourceId.of('prowler-first-scan'),
        // The runTask response body holds taskArn + failures; we don't need to
        // read any of it back into CFN outputs, so ignore any errors rather
        // than rolling back the whole stack on a transient ECS hiccup.
        ignoreErrorCodesMatching: '.*',
      },
      policy: AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ['ecs:RunTask'],
          resources: [`${taskDefinition.taskDefinitionArn}`, `${taskDefinition.taskDefinitionArn}:*`],
        }),
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ['iam:PassRole'],
          resources: [taskRole.roleArn, taskDefinition.executionRole!.roleArn],
          conditions: { StringEquals: { 'iam:PassedToService': 'ecs-tasks.amazonaws.com' } },
        }),
      ]),
      // Keep logs short so CW costs stay minimal; the scanner itself logs
      // anyway.
      logRetention: logs.RetentionDays.ONE_DAY,
    });
    // Wire an explicit dependency on the log group + container so the task
    // definition is fully ready before we RunTask.
    firstScan.node.addDependency(taskDefinition);

    new cdk.CfnOutput(this, 'EcrRepoUri', {
      value: ecrRepo.repositoryUri,
      description: 'ECR repository URI for the Prowler scanner image',
    });
    new cdk.CfnOutput(this, 'BuildProjectName', {
      value: buildProject.projectName,
      description: 'CodeBuild project that builds the Prowler image',
    });
    new cdk.CfnOutput(this, 'ClusterArn', { value: cluster.clusterArn });
    new cdk.CfnOutput(this, 'TaskDefinitionArn', { value: taskDefinition.taskDefinitionArn });
    new cdk.CfnOutput(this, 'ScannerSubnetIds', { value: this.subnetIds.join(',') });
    new cdk.CfnOutput(this, 'ScannerSecurityGroupId', { value: this.securityGroupId });
  }
}
