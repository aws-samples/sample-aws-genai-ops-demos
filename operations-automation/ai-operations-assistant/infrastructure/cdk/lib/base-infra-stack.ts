import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

/**
 * Configuration for a domain-specific InfraStack.
 * Each domain provides its name, prefix, and additional IAM policies.
 */
export interface DomainInfraConfig {
  /** Short domain name used in resource naming, e.g. "cost", "health" */
  domainName: string;
  /** PascalCase prefix for CfnOutput exportName, e.g. "GOATCostAgent" */
  exportPrefix: string;
  /** Docker image tag name, e.g. "goat_cost_agent" */
  imageTag: string;
  /**
   * AgentCore workload identity name used to scope the
   * `GetWorkloadAccessToken*` IAM permissions. AgentCore derives the
   * workload identity from the runtime name, so this MUST match the
   * `runtimeName` configured on the corresponding RuntimeStack.
   *
   * Defaults to `goat_${domainName}_agent`, which matches the naming
   * convention used by every sub-agent. The orchestration agent is the
   * one exception (`domainName: 'orch'` but `runtimeName:
   * 'goat_orchestration_agent'`), so it overrides this value.
   */
  workloadIdentityName?: string;
  /** Additional IAM policy statements for the AgentCore runtime role */
  domainPolicies: iam.PolicyStatement[];
}

/**
 * G.O.A.T. BaseInfraStack — Shared base class for all domain InfraStacks.
 *
 * Creates: ECR repository, S3 source bucket, CodeBuild project (ARM64),
 * IAM role for AgentCore runtime with common + domain-specific policies.
 * Exports 4 values via CfnOutput with exportName for cross-stack import.
 *
 * Follows the lifecycle tracker infra-stack.ts pattern.
 */
export class BaseInfraStack extends cdk.Stack {
  public readonly agentRole: iam.Role;
  public readonly agentRepository: ecr.Repository;
  public readonly sourceBucket: s3.Bucket;
  public readonly buildProject: codebuild.Project;

  constructor(scope: Construct, id: string, config: DomainInfraConfig, props?: cdk.StackProps) {
    super(scope, id, props);

    const { domainName, exportPrefix, imageTag, domainPolicies } = config;

    // AgentCore workload identity name — defaults to the standard
    // `goat_<domain>_agent` convention but can be overridden when the
    // runtime name diverges from the domain name (e.g. orchestration).
    const workloadIdentityName = config.workloadIdentityName ?? `goat_${domainName}_agent`;

    // -----------------------------------------------------------------------
    // ECR Repository
    // -----------------------------------------------------------------------
    this.agentRepository = new ecr.Repository(this, 'AgentRepo', {
      repositoryName: `goat-${domainName}-agent-repository`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      lifecycleRules: [{ maxImageCount: 5, description: 'Keep only 5 most recent images' }],
    });

    // -----------------------------------------------------------------------
    // IAM Role for AgentCore Runtime
    // -----------------------------------------------------------------------
    this.agentRole = new iam.Role(this, 'AgentRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: `Execution role for G.O.A.T. ${domainName} agent runtime`,
    });

    // --- Common policies (ECR, CloudWatch, X-Ray, AgentCore identity) ---
    this.agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ECRImageAccess',
      effect: iam.Effect.ALLOW,
      actions: ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer'],
      resources: [`arn:aws:ecr:${this.region}:${this.account}:repository/*`],
    }));

    this.agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ECRTokenAccess',
      effect: iam.Effect.ALLOW,
      actions: ['ecr:GetAuthorizationToken'],
      resources: ['*'],
    }));

    this.agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['logs:DescribeLogStreams', 'logs:CreateLogGroup'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
    }));

    this.agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['logs:DescribeLogGroups'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:*`],
    }));

    this.agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`],
    }));

    this.agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['xray:PutTraceSegments', 'xray:PutTelemetryRecords', 'xray:GetSamplingRules', 'xray:GetSamplingTargets'],
      resources: ['*'],
    }));

    this.agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' } },
    }));

    this.agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'GetAgentAccessToken',
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock-agentcore:GetWorkloadAccessToken',
        'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
        'bedrock-agentcore:GetWorkloadAccessTokenForUserId',
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/${workloadIdentityName}-*`,
      ],
    }));

    // --- Domain-specific policies ---
    for (const policy of domainPolicies) {
      this.agentRole.addToPolicy(policy);
    }

    // -----------------------------------------------------------------------
    // S3 Source Bucket
    // -----------------------------------------------------------------------
    this.sourceBucket = new s3.Bucket(this, 'SourceBucket', {
      bucketName: `goat-${domainName}-agent-sources-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // -----------------------------------------------------------------------
    // CodeBuild Role
    // -----------------------------------------------------------------------
    const codeBuildRole = new iam.Role(this, 'CodeBuildRole', {
      assumedBy: new iam.ServicePrincipal('codebuild.amazonaws.com'),
      description: `Build role for G.O.A.T. ${domainName} agent container`,
    });

    codeBuildRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ecr:GetAuthorizationToken'],
      resources: ['*'],
    }));

    codeBuildRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'ecr:BatchCheckLayerAvailability', 'ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer',
        'ecr:PutImage', 'ecr:InitiateLayerUpload', 'ecr:UploadLayerPart', 'ecr:CompleteLayerUpload',
      ],
      resources: [this.agentRepository.repositoryArn],
    }));

    codeBuildRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/goat-${domainName}-*`],
    }));

    codeBuildRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
      resources: [this.sourceBucket.bucketArn, `${this.sourceBucket.bucketArn}/*`],
      conditions: { StringEquals: { 's3:ResourceAccount': this.account } },
    }));

    // -----------------------------------------------------------------------
    // CodeBuild Project (ARM64)
    // -----------------------------------------------------------------------
    this.buildProject = new codebuild.Project(this, 'BuildProject', {
      projectName: `goat-${domainName}-agent-builder`,
      description: `Builds ARM64 container image for G.O.A.T. ${domainName} agent`,
      role: codeBuildRole,
      environment: {
        buildImage: codebuild.LinuxBuildImage.AMAZON_LINUX_2_ARM_3,
        computeType: codebuild.ComputeType.SMALL,
        privileged: true,
      },
      source: codebuild.Source.s3({
        bucket: this.sourceBucket,
        path: 'agent-source/',
      }),
      cache: codebuild.Cache.local(codebuild.LocalCacheMode.DOCKER_LAYER),
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          pre_build: {
            commands: [
              'echo Logging in to Amazon ECR...',
              `aws ecr get-login-password --region ${this.region} | docker login --username AWS --password-stdin ${this.account}.dkr.ecr.${this.region}.amazonaws.com`,
            ],
          },
          build: {
            commands: [
              'echo Building Docker image...',
              `docker build --platform linux/arm64 -t ${imageTag}:latest .`,
              `docker tag ${imageTag}:latest ${this.agentRepository.repositoryUri}:latest`,
            ],
          },
          post_build: {
            commands: [
              'echo Pushing Docker image to ECR...',
              `docker push ${this.agentRepository.repositoryUri}:latest`,
              'echo Build completed successfully',
            ],
          },
        },
      }),
    });

    // -----------------------------------------------------------------------
    // Stack Outputs (cross-stack exports)
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'RuntimeRoleArn', {
      value: this.agentRole.roleArn,
      description: `IAM Role ARN for ${domainName} agent runtime`,
      exportName: `${exportPrefix}RuntimeRoleArn`,
    });

    new cdk.CfnOutput(this, 'SourceBucketName', {
      value: this.sourceBucket.bucketName,
      description: `S3 bucket for ${domainName} agent source`,
      exportName: `${exportPrefix}SourceBucketName`,
    });

    new cdk.CfnOutput(this, 'BuildProjectName', {
      value: this.buildProject.projectName,
      description: `CodeBuild project name for ${domainName} agent`,
      exportName: `${exportPrefix}BuildProjectName`,
    });

    new cdk.CfnOutput(this, 'BuildProjectArn', {
      value: this.buildProject.projectArn,
      description: `CodeBuild project ARN for ${domainName} agent`,
      exportName: `${exportPrefix}BuildProjectArn`,
    });
  }
}
