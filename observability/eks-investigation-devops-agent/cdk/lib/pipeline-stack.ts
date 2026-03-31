import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface PipelineStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  eksNodeArchitecture: string; // 'arm64' | 'amd64'
}

/** Convert kebab-case to PascalCase (e.g. merchant-gateway → MerchantGateway) */
function toPascal(s: string): string {
  return s.replace(/(^|-)(\w)/g, (_, _dash, ch) => ch.toUpperCase());
}

export class PipelineStack extends cdk.Stack {
  public readonly merchantGatewayBuildProject: string;
  public readonly paymentProcessorBuildProject: string;
  public readonly webhookServiceBuildProject: string;
  public readonly merchantGatewayRepoUri: string;
  public readonly paymentProcessorRepoUri: string;
  public readonly webhookServiceRepoUri: string;

  constructor(scope: Construct, id: string, props: PipelineStackProps) {
    super(scope, id, props);

    const { environment, projectName, eksNodeArchitecture } = props;
    const isArm = eksNodeArchitecture === 'arm64';

    // -----------------------------------------------------------------------
    // ECR Repositories
    // -----------------------------------------------------------------------
    const serviceNames = ['merchant-gateway', 'payment-processor', 'webhook-service'] as const;

    const repos: Record<string, ecr.Repository> = {};
    for (const service of serviceNames) {
      repos[service] = new ecr.Repository(this, `${toPascal(service)}Repository`, {
        repositoryName: `${projectName}/${service}`,
        imageScanOnPush: true,
        encryption: ecr.RepositoryEncryption.AES_256,
        emptyOnDelete: true,
        imageTagMutability: ecr.TagMutability.MUTABLE,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        lifecycleRules: [
          {
            rulePriority: 1,
            description: 'Keep last 30 images',
            maxImageCount: 30,
          },
        ],
      });
    }

    this.merchantGatewayRepoUri = repos['merchant-gateway'].repositoryUri;
    this.paymentProcessorRepoUri = repos['payment-processor'].repositoryUri;
    this.webhookServiceRepoUri = repos['webhook-service'].repositoryUri;

    // -----------------------------------------------------------------------
    // Shared CodeBuild IAM Role
    // -----------------------------------------------------------------------
    const codeBuildRole = new iam.Role(this, 'CodeBuildServiceRole', {
      roleName: `${projectName}-${environment}-codebuild-role`,
      assumedBy: new iam.ServicePrincipal('codebuild.amazonaws.com'),
      inlinePolicies: {
        [`${projectName}-${environment}-codebuild-policy`]: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              sid: 'ECRAuth',
              actions: ['ecr:GetAuthorizationToken'],
              resources: ['*'],
            }),
            new iam.PolicyStatement({
              sid: 'ECRPush',
              actions: [
                'ecr:BatchCheckLayerAvailability',
                'ecr:GetDownloadUrlForLayer',
                'ecr:BatchGetImage',
                'ecr:PutImage',
                'ecr:InitiateLayerUpload',
                'ecr:UploadLayerPart',
                'ecr:CompleteLayerUpload',
              ],
              resources: [
                `arn:aws:ecr:${this.region}:${this.account}:repository/${projectName}/*`,
              ],
            }),
            new iam.PolicyStatement({
              sid: 'S3Read',
              actions: [
                's3:GetObject',
                's3:GetObjectVersion',
                's3:GetBucketLocation',
              ],
              resources: [
                `arn:aws:s3:::${projectName}-cfn-templates-${this.account}`,
                `arn:aws:s3:::${projectName}-cfn-templates-${this.account}/*`,
              ],
            }),
            new iam.PolicyStatement({
              sid: 'CloudWatchLogs',
              actions: [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
              ],
              resources: [
                `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/${projectName}-*`,
                `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/${projectName}-*:*`,
              ],
            }),
          ],
        }),
      },
    });

    // -----------------------------------------------------------------------
    // CodeBuild Projects
    // -----------------------------------------------------------------------
    // Use exact image IDs from original CloudFormation templates
    const buildImage = isArm
      ? codebuild.LinuxArmBuildImage.fromCodeBuildImageId('aws/codebuild/amazonlinux-aarch64-standard:3.0')
      : codebuild.LinuxBuildImage.fromCodeBuildImageId('aws/codebuild/amazonlinux-x86_64-standard:5.0');

    // S3 bucket name used by deploy scripts to upload source zips at build time
    const sourceBucketName = `${projectName}-cfn-templates-${this.account}`;

    const builds: Record<string, codebuild.Project> = {};
    for (const service of serviceNames) {
      builds[service] = new codebuild.Project(this, `${toPascal(service)}Build`, {
        projectName: `${projectName}-${environment}-${service}`,
        description: `Build and push ${service} container image to ECR`,
        role: codeBuildRole,
        // No source configured — deploy script provides S3 source at
        // start-build time via --source-type-override / --source-location-override.
        // This avoids requiring the S3 bucket to exist at stack creation.
        buildSpec: codebuild.BuildSpec.fromObject({
          version: '0.2',
          phases: {
            pre_build: {
              commands: [
                'echo Logging in to Amazon ECR...',
                'aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $ECR_REPO_URI',
              ],
            },
            build: {
              commands: [
                'echo Building Docker image...',
                'docker build -t $ECR_REPO_URI:$IMAGE_TAG .',
              ],
            },
            post_build: {
              commands: [
                'echo Pushing Docker image...',
                'docker push $ECR_REPO_URI:$IMAGE_TAG',
              ],
            },
          },
        }),
        // No artifacts — CodeBuild pushes images directly to ECR
        environment: {
          buildImage,
          computeType: codebuild.ComputeType.SMALL,
          privileged: true,
          environmentVariables: {
            ECR_REPO_URI: { value: repos[service].repositoryUri },
            IMAGE_TAG: { value: environment },
            AWS_DEFAULT_REGION: { value: this.region },
          },
        },
        logging: {
          cloudWatch: {
            logGroup: new cdk.aws_logs.LogGroup(this, `${toPascal(service)}BuildLogGroup`, {
              logGroupName: `/aws/codebuild/${projectName}-${service}`,
              removalPolicy: cdk.RemovalPolicy.DESTROY,
            }),
          },
        },
      });
    }

    this.merchantGatewayBuildProject = builds['merchant-gateway'].projectName;
    this.paymentProcessorBuildProject = builds['payment-processor'].projectName;
    this.webhookServiceBuildProject = builds['webhook-service'].projectName;

    // -----------------------------------------------------------------------
    // CloudFormation Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'MerchantGatewayBuildProject', {
      description: 'CodeBuild project name for merchant-gateway',
      value: builds['merchant-gateway'].projectName,
    });

    new cdk.CfnOutput(this, 'PaymentProcessorBuildProject', {
      description: 'CodeBuild project name for payment-processor',
      value: builds['payment-processor'].projectName,
    });

    new cdk.CfnOutput(this, 'WebhookServiceBuildProject', {
      description: 'CodeBuild project name for webhook-service',
      value: builds['webhook-service'].projectName,
    });

    new cdk.CfnOutput(this, 'MerchantGatewayRepositoryUri', {
      description: 'ECR Repository URI for merchant-gateway',
      value: repos['merchant-gateway'].repositoryUri,
    });

    new cdk.CfnOutput(this, 'PaymentProcessorRepositoryUri', {
      description: 'ECR Repository URI for payment-processor',
      value: repos['payment-processor'].repositoryUri,
    });

    new cdk.CfnOutput(this, 'WebhookServiceRepositoryUri', {
      description: 'ECR Repository URI for webhook-service',
      value: repos['webhook-service'].repositoryUri,
    });
  }
}
