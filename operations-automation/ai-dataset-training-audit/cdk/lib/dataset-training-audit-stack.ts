import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as iam from 'aws-cdk-lib/aws-iam';

/**
 * DART — Dataset Audit & Readiness for Training.
 *
 * One stack (DatasetTrainingAudit-${region}) provisioning the Fargate batch task that runs
 * the pre-flight dataset audit, plus its KMS key, output bucket, audit table, log group, and
 * a no-NAT public-subnet VPC (Option B — zero standing network cost; the task reaches Bedrock
 * and Comprehend over the public internet, S3/DynamoDB over free gateway endpoints).
 *
 * The CRIS MODEL_ID and the Bedrock IAM ARNs are computed inline here (design.md sections 3,
 * 4.1): shared/utils/aws-utils.ts exports only getRegion/getAccountId — there is NO
 * getBedrockModelId on the TS side — so the region->prefix mapping from
 * shared/utils/aws_utils.py get_bedrock_model_id() is replicated here. When the shared Python
 * util's default or prefix mapping changes, this mapping and agent/aws_utils.py must both be
 * updated.
 */
export class DatasetTrainingAuditStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const region = this.region;
    const account = this.account;
    const partition = this.partition;

    // ---------------------------------------------------------------------
    // MODEL_ID + CRIS prefix (design.md section 3) — computed inline.
    // ---------------------------------------------------------------------
    const BASE_MODEL_ID = 'anthropic.claude-sonnet-4-5-20250929-v1:0'; // canonical demo default
    const geo = region.split('-')[0]; // e.g. "us", "eu", "ap", "ca"
    // MUST map ap -> apac (the util's documented trap); anything not us/eu/ap -> global.
    const crisPrefix = (({ us: 'us', eu: 'eu', ap: 'apac' } as Record<string, string>)[geo]) ?? 'global';
    const modelId = `${crisPrefix}.${BASE_MODEL_ID}`;

    // A `global.`-prefixed cross-region inference profile is addressed under the partition's
    // global-profile HOME region, not the arbitrary deploy region.
    const GLOBAL_PROFILE_HOME: Record<string, string> = {
      aws: 'us-east-1',
      'aws-us-gov': 'us-gov-west-1',
      'aws-cn': 'cn-north-1',
    };
    const profileRegion =
      crisPrefix === 'global' ? (GLOBAL_PROFILE_HOME[partition] ?? 'us-east-1') : region;

    // ---------------------------------------------------------------------
    // KMS key (shared by bucket, table, log group).
    // ---------------------------------------------------------------------
    const encryptionKey = new kms.Key(this, 'DartKey', {
      enableKeyRotation: true,
      alias: `alias/dataset-training-audit-${region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // CloudWatch Logs must be able to use the key, or a KMS-encrypted log group fails to
    // create. Scoped to this account's log group ARNs via the EncryptionContext condition.
    encryptionKey.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowCloudWatchLogs',
        principals: [new iam.ServicePrincipal(`logs.${region}.amazonaws.com`)],
        actions: [
          'kms:Encrypt*',
          'kms:Decrypt*',
          'kms:ReEncrypt*',
          'kms:GenerateDataKey*',
          'kms:Describe*',
        ],
        resources: ['*'],
        conditions: {
          ArnLike: {
            'kms:EncryptionContext:aws:logs:arn': `arn:${partition}:logs:${region}:${account}:log-group:*`,
          },
        },
      }),
    );

    // ---------------------------------------------------------------------
    // S3 output bucket.
    // ---------------------------------------------------------------------
    const outputBucket = new s3.Bucket(this, 'OutputBucket', {
      bucketName: `dart-agent-output-${account}-${region}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey,
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: 'expire-old-reports',
          prefix: 'reports/',
          expiration: cdk.Duration.days(365),
        },
      ],
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // autoDeleteObjects is backed by a stack-singleton CustomResourceProvider Lambda (a raw
    // CfnResource, so neither the logGroup prop nor @aws-cdk/aws-lambda:useCdkManagedLogGroup
    // applies to it). Left alone, Lambda creates /aws/lambda/<generated-name> on first
    // invocation, outside CloudFormation, and it survives `cdk destroy`. Route its logs to a
    // stack-owned log group via LoggingConfig instead. The Ref makes the Handler depend on the
    // log group.
    //
    // Lambda delivers logs asynchronously (seconds after the invocation), so the Delete
    // invocation's logs can land after this group is deleted; with AWSLambdaBasicExecutionRole
    // (logs:CreateLogGroup on *) Lambda would then recreate it as an orphan. So the provider
    // role gets no managed policy, only CreateLogStream/PutLogEvents on this group. The inline
    // policy references the group's ARN, so destroy order is Handler -> Role -> log group:
    // the role can never create a log group, and is gone before this one is deleted.
    const autoDeleteLogGroup = new logs.LogGroup(this, 'AutoDeleteObjectsLogGroup', {
      logGroupName: `/dataset-training-audit/${region}/auto-delete-objects`,
      encryptionKey,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const autoDeleteProvider = this.node.tryFindChild(
      'Custom::S3AutoDeleteObjectsCustomResourceProvider',
    ) as cdk.CustomResourceProviderBase | undefined;
    const autoDeleteHandler = autoDeleteProvider?.node.tryFindChild('Handler') as
      | cdk.CfnResource
      | undefined;
    const autoDeleteRole = autoDeleteProvider?.node.tryFindChild('Role') as
      | cdk.CfnResource
      | undefined;
    if (!autoDeleteProvider || !autoDeleteHandler || !autoDeleteRole) {
      throw new Error('S3 autoDeleteObjects provider not found; CDK internals changed');
    }
    autoDeleteHandler.addPropertyOverride('LoggingConfig', {
      LogGroup: autoDeleteLogGroup.logGroupName,
    });
    autoDeleteRole.addPropertyDeletionOverride('ManagedPolicyArns');
    autoDeleteProvider.addToRolePolicy({
      Effect: 'Allow',
      Action: ['logs:CreateLogStream', 'logs:PutLogEvents'],
      Resource: autoDeleteLogGroup.logGroupArn,
    });

    // ---------------------------------------------------------------------
    // DynamoDB audit table.
    // ---------------------------------------------------------------------
    const auditTable = new dynamodb.Table(this, 'AuditTable', {
      tableName: `dart-agent-audit-trail-${region}`,
      partitionKey: { name: 'run_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestamp', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ---------------------------------------------------------------------
    // CloudWatch log group.
    // ---------------------------------------------------------------------
    const logGroup = new logs.LogGroup(this, 'TaskLogGroup', {
      logGroupName: `/dataset-training-audit/${region}`,
      encryptionKey,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ---------------------------------------------------------------------
    // VPC — no NAT, public subnet only, S3 + DynamoDB gateway endpoints (Option B).
    // ---------------------------------------------------------------------
    const vpc = new ec2.Vpc(this, 'DartVpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });

    vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });
    vpc.addGatewayEndpoint('DynamoDbEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
    });

    // ---------------------------------------------------------------------
    // Task security group — explicit, least-privilege: no ingress, egress 443 only.
    // The public-subnet task reaches Bedrock/Comprehend (not gateway-endpoint services)
    // over the public internet, so 443 egress is load-bearing.
    // ---------------------------------------------------------------------
    const taskSecurityGroup = new ec2.SecurityGroup(this, 'TaskSecurityGroup', {
      vpc,
      description: 'DART Fargate task - HTTPS egress only, no ingress',
      allowAllOutbound: false,
    });
    taskSecurityGroup.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      'HTTPS egress for Bedrock and Comprehend',
    );

    // ---------------------------------------------------------------------
    // ECS cluster.
    // ---------------------------------------------------------------------
    const clusterName = `dataset-training-audit-${region}`;

    // Container Insights writes to /aws/ecs/containerinsights/<cluster>/performance and ECS
    // creates that group itself if missing, outside CloudFormation. Create it first with the
    // exact name (1-day retention, as ECS would) so the stack owns it and destroy removes it.
    const containerInsightsLogGroup = new logs.LogGroup(this, 'ContainerInsightsLogGroup', {
      logGroupName: `/aws/ecs/containerinsights/${clusterName}/performance`,
      encryptionKey,
      retention: logs.RetentionDays.ONE_DAY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const cluster = new ecs.Cluster(this, 'DartCluster', {
      clusterName,
      vpc,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    });
    cluster.node.addDependency(containerInsightsLogGroup);

    // ---------------------------------------------------------------------
    // Fargate task definition.
    // ---------------------------------------------------------------------
    const taskDefinition = new ecs.FargateTaskDefinition(this, 'DartTaskDef', {
      cpu: 4096,
      memoryLimitMiB: 8192,
      runtimePlatform: {
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
      },
    });

    // Container image — DockerImageAsset by default; an imageTag escape hatch references an
    // already-pushed ECR image so a re-synth/destroy needs no Docker daemon.
    const imageTag = this.node.tryGetContext('imageTag');
    let containerImage: ecs.ContainerImage;
    if (imageTag) {
      const repo = ecr.Repository.fromRepositoryName(
        this,
        'DartRepo',
        `dataset-training-audit-${region}`,
      );
      containerImage = ecs.ContainerImage.fromEcrRepository(repo, imageTag);
    } else {
      const asset = new ecr_assets.DockerImageAsset(this, 'DartImage', {
        directory: path.join(__dirname, '../../agent'),
        file: 'Dockerfile',
        platform: ecr_assets.Platform.LINUX_AMD64,
      });
      containerImage = ecs.ContainerImage.fromDockerImageAsset(asset);
    }

    // Path-safety allowlists (comma-separated; empty = allow any). Opt-in via context,
    // mirroring the Terraform allowed_s3_buckets / allowed_s3_prefixes / allowed_local_base_dir
    // variables so both IaC paths set the same three env vars the agent's Config reads.
    const allowedS3Buckets: string = this.node.tryGetContext('allowedS3Buckets') ?? '';
    const allowedS3Prefixes: string = this.node.tryGetContext('allowedS3Prefixes') ?? '';
    const allowedLocalBaseDir: string = this.node.tryGetContext('allowedLocalBaseDir') ?? '';

    taskDefinition.addContainer('DartContainer', {
      image: containerImage,
      environment: {
        AWS_REGION: region,
        AWS_ACCOUNT_ID: account,
        OUTPUT_BUCKET: outputBucket.bucketName,
        AUDIT_TABLE: auditTable.tableName,
        LOG_LEVEL: 'INFO',
        MODEL_ID: modelId,
        MAX_TOKENS: '4096',
        DEFAULT_MODE: 'step-by-step',
        DEDUP_SIMILARITY_THRESHOLD: '0.85',
        ALLOWED_S3_BUCKETS: allowedS3Buckets,
        ALLOWED_S3_PREFIXES: allowedS3Prefixes,
        ALLOWED_LOCAL_BASE_DIR: allowedLocalBaseDir,
      },
      healthCheck: {
        command: ['CMD-SHELL', "python -c 'import agent' || exit 1"],
      },
      logging: ecs.LogDrivers.awsLogs({
        logGroup,
        streamPrefix: 'dart-agent',
      }),
    });

    // ---------------------------------------------------------------------
    // IAM task role — least privilege (design.md section 4.1).
    // ---------------------------------------------------------------------
    const taskRole = taskDefinition.taskRole;

    // Approved Bedrock model ids (static). First entry is the canonical demo default.
    const approvedModelIds = [
      'anthropic.claude-sonnet-4-5-20250929-v1:0',
      'anthropic.claude-sonnet-4-20250514-v1:0',
      'anthropic.claude-haiku-4-5',
      'amazon.nova-pro-v1:0',
    ];

    // Two ARN families per model: foundation-model (wildcard region, no account id, to cover
    // CRIS cross-region fan-out) and inference-profile (region-specific, account-scoped).
    const bedrockResources: string[] = [];
    for (const mid of approvedModelIds) {
      bedrockResources.push(`arn:${partition}:bedrock:*::foundation-model/${mid}`);
      bedrockResources.push(
        `arn:${partition}:bedrock:${profileRegion}:${account}:inference-profile/${crisPrefix}.${mid}`,
      );
    }

    taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockInvoke',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: bedrockResources,
      }),
    );

    // Comprehend DetectPiiEntities — no resource-level permissions.
    taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: 'ComprehendPii',
        actions: ['comprehend:DetectPiiEntities'],
        resources: ['*'],
      }),
    );

    // X-Ray.
    taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: 'XRay',
        actions: ['xray:PutTraceSegments', 'xray:PutTelemetryRecords'],
        resources: ['*'],
      }),
    );

    // Agent's own resources only.
    outputBucket.grantReadWrite(taskRole);
    auditTable.grantReadWriteData(taskRole);
    logGroup.grantWrite(taskRole);
    encryptionKey.grantDecrypt(taskRole);

    // Opt-in input-bucket read: one scoped statement per named bucket, never "*".
    const inputBuckets: string[] = (this.node.tryGetContext('inputBuckets') ?? '')
      .split(',')
      .filter(Boolean);
    for (const bucket of inputBuckets) {
      taskRole.addToPrincipalPolicy(
        new iam.PolicyStatement({
          sid: `InputBucketRead${bucket.replace(/[^A-Za-z0-9]/g, '')}`,
          actions: ['s3:GetObject', 's3:ListBucket'],
          resources: [
            `arn:${partition}:s3:::${bucket}`,
            `arn:${partition}:s3:::${bucket}/*`,
          ],
        }),
      );
    }

    // ---------------------------------------------------------------------
    // Outputs (region-suffixed export names).
    // ---------------------------------------------------------------------
    const publicSubnetIds = vpc.publicSubnets.map((s) => s.subnetId).join(',');

    new cdk.CfnOutput(this, 'OutputBucketName', {
      value: outputBucket.bucketName,
      exportName: `DatasetTrainingAudit-OutputBucketName-${region}`,
    });
    new cdk.CfnOutput(this, 'AuditTableName', {
      value: auditTable.tableName,
      exportName: `DatasetTrainingAudit-AuditTableName-${region}`,
    });
    new cdk.CfnOutput(this, 'ClusterName', {
      value: cluster.clusterName,
      exportName: `DatasetTrainingAudit-ClusterName-${region}`,
    });
    new cdk.CfnOutput(this, 'TaskSecurityGroupId', {
      value: taskSecurityGroup.securityGroupId,
      exportName: `DatasetTrainingAudit-TaskSecurityGroupId-${region}`,
    });
    new cdk.CfnOutput(this, 'PublicSubnetIds', {
      value: publicSubnetIds,
      exportName: `DatasetTrainingAudit-PublicSubnetIds-${region}`,
    });
    new cdk.CfnOutput(this, 'TaskDefinitionArn', {
      value: taskDefinition.taskDefinitionArn,
      exportName: `DatasetTrainingAudit-TaskDefinitionArn-${region}`,
    });
  }
}
