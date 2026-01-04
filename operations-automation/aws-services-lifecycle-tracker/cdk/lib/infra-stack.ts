#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export class AWSServicesLifecycleTrackerInfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Create ECR repository for the agent container
    const agentRepository = new ecr.Repository(this, 'LifecycleTrackerRepository', {
      repositoryName: 'aws-services-lifecycle-tracker-repository',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      lifecycleRules: [{
        maxImageCount: 5,
        description: 'Keep only 5 most recent images',
      }],
    });

    // Create IAM role for the agent runtime
    const agentRole = new iam.Role(this, 'LifecycleTrackerRuntimeRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'Execution role for AWS Services Lifecycle Tracker runtime',
    });

    // ECR Image Access
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ECRImageAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'ecr:BatchGetImage',
        'ecr:GetDownloadUrlForLayer',
      ],
      resources: [`arn:aws:ecr:${this.region}:${this.account}:repository/*`],
    }));

    // ECR Token Access
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ECRTokenAccess',
      effect: iam.Effect.ALLOW,
      actions: ['ecr:GetAuthorizationToken'],
      resources: ['*'],
    }));

    // CloudWatch Logs
    agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'logs:DescribeLogStreams',
        'logs:CreateLogGroup',
      ],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
    }));

    agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['logs:DescribeLogGroups'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:*`],
    }));

    agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'logs:CreateLogStream',
        'logs:PutLogEvents',
      ],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`],
    }));

    // X-Ray Tracing
    agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'xray:PutTraceSegments',
        'xray:PutTelemetryRecords',
        'xray:GetSamplingRules',
        'xray:GetSamplingTargets',
      ],
      resources: ['*'],
    }));

    // CloudWatch Metrics
    agentRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: {
        StringEquals: {
          'cloudwatch:namespace': 'bedrock-agentcore',
        },
      },
    }));

    // AgentCore Identity - Get Workload Access Token
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'GetAgentAccessToken',
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock-agentcore:GetWorkloadAccessToken',
        'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
        'bedrock-agentcore:GetWorkloadAccessTokenForUserId',
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/aws_services_lifecycle_agent-*`,
      ],
    }));

    // Bedrock Model Invocation (including Converse API and Inference Profiles)
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'BedrockModelInvocation',
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
        'bedrock:Converse',
        'bedrock:ConverseStream',
      ],
      resources: [
        'arn:aws:bedrock:*::foundation-model/*',
        `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/*`,
        `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
        `arn:aws:bedrock:${this.region}:${this.account}:*`,
      ],
    }));

    // AWS Marketplace permissions for Bedrock models
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'MarketplaceAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'aws-marketplace:ViewSubscriptions',
        'aws-marketplace:Subscribe',
        'aws-marketplace:Unsubscribe',
      ],
      resources: ['*'],
    }));

    // DynamoDB permissions for lifecycle data and configuration
    agentRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DynamoDBAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:GetItem',
        'dynamodb:PutItem',
        'dynamodb:UpdateItem',
        'dynamodb:DeleteItem',
        'dynamodb:Query',
        'dynamodb:Scan',
        'dynamodb:BatchGetItem',
        'dynamodb:BatchWriteItem',
      ],
      resources: [
        `arn:aws:dynamodb:${this.region}:${this.account}:table/aws-services-lifecycle`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/aws-services-lifecycle/index/*`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/service-extraction-config`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/service-extraction-config/index/*`,
      ],
    }));

    // Create S3 bucket for CodeBuild source
    const sourceBucket = new s3.Bucket(this, 'SourceBucket', {
      bucketName: `aws-services-lifecycle-tracker-sources-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      // No lifecycle rules - keep source files indefinitely for CodeBuild
    });

    // Create IAM role for CodeBuild
    const codeBuildRole = new iam.Role(this, 'LifecycleTrackerCodeBuildRole', {
      assumedBy: new iam.ServicePrincipal('codebuild.amazonaws.com'),
      description: 'Build role for lifecycle tracker container image pipeline',
    });

    // Grant CodeBuild permissions - ECR Token Access
    codeBuildRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ecr:GetAuthorizationToken'],
      resources: ['*'],
    }));

    // ECR Image Operations (scoped to our repository)
    codeBuildRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'ecr:BatchCheckLayerAvailability',
        'ecr:BatchGetImage',
        'ecr:GetDownloadUrlForLayer',
        'ecr:PutImage',
        'ecr:InitiateLayerUpload',
        'ecr:UploadLayerPart',
        'ecr:CompleteLayerUpload',
      ],
      resources: [agentRepository.repositoryArn],
    }));

    // CloudWatch Logs
    codeBuildRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
      ],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/aws-services-lifecycle-tracker-*`],
    }));

    // S3 Access with account condition
    codeBuildRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        's3:GetObject',
        's3:PutObject',
        's3:ListBucket',
      ],
      resources: [
        sourceBucket.bucketArn,
        `${sourceBucket.bucketArn}/*`,
      ],
      conditions: {
        StringEquals: {
          's3:ResourceAccount': this.account,
        },
      },
    }));

    // Create CodeBuild project for building ARM64 container
    const buildProject = new codebuild.Project(this, 'LifecycleTrackerBuildProject', {
      projectName: 'aws-services-lifecycle-tracker-builder',
      description: 'Builds ARM64 container image for AgentCore runtime',
      role: codeBuildRole,
      environment: {
        buildImage: codebuild.LinuxBuildImage.AMAZON_LINUX_2_ARM_3,
        computeType: codebuild.ComputeType.SMALL,
        privileged: true, // Required for Docker builds
      },
      source: codebuild.Source.s3({
        bucket: sourceBucket,
        path: 'agent-source/',  // Path to extracted agent files
      }),
      cache: codebuild.Cache.local(codebuild.LocalCacheMode.DOCKER_LAYER),  // Cache Docker layers for faster builds
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
              'docker build --platform linux/arm64 -t aws_services_lifecycle_agent:latest .',
              `docker tag aws_services_lifecycle_agent:latest ${agentRepository.repositoryUri}:latest`,
            ],
          },
          post_build: {
            commands: [
              'echo Pushing Docker image to ECR...',
              `docker push ${agentRepository.repositoryUri}:latest`,
              'echo Build completed successfully',
            ],
          },
        },
      }),
    });

    // Outputs
    new cdk.CfnOutput(this, 'RepositoryUri', {
      value: agentRepository.repositoryUri,
      description: 'ECR Repository URI for agent container',
    });

    new cdk.CfnOutput(this, 'RoleArn', {
      value: agentRole.roleArn,
      description: 'IAM Role ARN for Lifecycle Tracker Runtime',
      exportName: 'AWSServicesLifecycleTrackerRuntimeRoleArn',
    });

    new cdk.CfnOutput(this, 'SourceBucketName', {
      value: sourceBucket.bucketName,
      description: 'S3 bucket for CodeBuild source',
      exportName: 'AWSServicesLifecycleTrackerSourceBucketName',
    });

    new cdk.CfnOutput(this, 'BuildProjectName', {
      value: buildProject.projectName,
      description: 'CodeBuild project name',
      exportName: 'AWSServicesLifecycleTrackerBuildProjectName',
    });

    new cdk.CfnOutput(this, 'BuildProjectArn', {
      value: buildProject.projectArn,
      description: 'CodeBuild project ARN',
      exportName: 'AWSServicesLifecycleTrackerBuildProjectArn',
    });
  }
}


