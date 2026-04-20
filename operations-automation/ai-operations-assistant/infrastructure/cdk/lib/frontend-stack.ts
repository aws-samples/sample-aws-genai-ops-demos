import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';

/**
 * Props for the G.O.A.T. FrontendStack.
 */
export interface FrontendStackProps extends cdk.StackProps {
  /** Orchestration Agent runtime ARN */
  orchestrationArn: string;
  /** Cognito User Pool ID */
  userPoolId: string;
  /** Cognito User Pool Client ID */
  userPoolClientId: string;
  /** Cognito Identity Pool ID */
  identityPoolId: string;
  /** AWS region */
  region: string;
}

/**
 * G.O.A.T. FrontendStack — S3 bucket, CloudFront distribution with OAC,
 * and BucketDeployment for the React + Cloudscape chatbot frontend.
 *
 * Follows the lifecycle tracker frontend-stack.ts pattern.
 */
export class FrontendStack extends cdk.Stack {
  public readonly distributionUrl: string;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    // -----------------------------------------------------------------------
    // S3 Bucket — static website hosting (private, CloudFront-only access)
    // -----------------------------------------------------------------------
    const websiteBucket = new s3.Bucket(this, 'WebsiteBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    // -----------------------------------------------------------------------
    // CloudFront Origin Access Control (OAC)
    // -----------------------------------------------------------------------
    const originAccessControl = new cloudfront.S3OriginAccessControl(this, 'OAC', {
      signing: cloudfront.Signing.SIGV4_NO_OVERRIDE,
    });

    // -----------------------------------------------------------------------
    // CloudFront Distribution
    // -----------------------------------------------------------------------
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'G.O.A.T. - GenAI Operations Analytics Tool Frontend',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(websiteBucket, {
          originAccessControl,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
        },
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
        },
      ],
    });

    // Grant CloudFront OAC access to S3 bucket
    websiteBucket.addToResourcePolicy(
      new cdk.aws_iam.PolicyStatement({
        actions: ['s3:GetObject'],
        resources: [websiteBucket.arnForObjects('*')],
        principals: [new cdk.aws_iam.ServicePrincipal('cloudfront.amazonaws.com')],
        conditions: {
          StringEquals: {
            'AWS:SourceArn': `arn:aws:cloudfront::${cdk.Stack.of(this).account}:distribution/${distribution.distributionId}`,
          },
        },
      })
    );

    // -----------------------------------------------------------------------
    // Deploy frontend build output to S3
    // -----------------------------------------------------------------------
    new s3deploy.BucketDeployment(this, 'DeployWebsite', {
      sources: [s3deploy.Source.asset('../../frontend/dist')],
      destinationBucket: websiteBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    this.distributionUrl = `https://${distribution.distributionDomainName}`;

    // -----------------------------------------------------------------------
    // Stack Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'WebsiteUrl', {
      value: this.distributionUrl,
      description: 'CloudFront Distribution URL',
      exportName: 'GOATWebsiteUrl',
    });

    new cdk.CfnOutput(this, 'BucketName', {
      value: websiteBucket.bucketName,
      description: 'S3 Website Bucket Name',
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: props.userPoolId,
      description: 'Cognito User Pool ID',
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: props.userPoolClientId,
      description: 'Cognito User Pool Client ID',
    });

    new cdk.CfnOutput(this, 'IdentityPoolId', {
      value: props.identityPoolId,
      description: 'Cognito Identity Pool ID',
    });

    new cdk.CfnOutput(this, 'OrchestrationArn', {
      value: props.orchestrationArn,
      description: 'Orchestration Agent Runtime ARN',
    });

    new cdk.CfnOutput(this, 'Region', {
      value: props.region,
      description: 'AWS Region',
    });
  }
}
