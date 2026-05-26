import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface FrontendStackProps extends cdk.StackProps {
  userPoolId: string;
  userPoolClientId: string;
  identityPoolId: string;
  apiFunctionUrl: string;
  region: string;
}

/**
 * Frontend Stack — S3 + CloudFront with OAC.
 *
 * No API Gateway. The browser signs SigV4 requests to the dashboard-api
 * Function URL directly using temporary credentials from the Cognito Identity
 * Pool — same pattern as `operations-automation/ai-lambda-runtime-migration/`.
 */
export class FrontendStack extends cdk.Stack {
  public readonly distributionUrl: string;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    const websiteBucket = new s3.Bucket(this, 'WebsiteBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    const oac = new cloudfront.S3OriginAccessControl(this, 'OAC', {
      signing: cloudfront.Signing.SIGV4_NO_OVERRIDE,
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'Prowler Security Findings Dashboard',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(websiteBucket, { originAccessControl: oac }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html' },
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' },
      ],
    });

    websiteBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject'],
        resources: [websiteBucket.arnForObjects('*')],
        principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
        conditions: {
          StringEquals: {
            'AWS:SourceArn': `arn:aws:cloudfront::${cdk.Stack.of(this).account}:distribution/${distribution.distributionId}`,
          },
        },
      }),
    );

    new s3deploy.BucketDeployment(this, 'DeployWebsite', {
      sources: [s3deploy.Source.asset('../frontend/dist')],
      destinationBucket: websiteBucket,
      distribution,
      distributionPaths: ['/*'],
      // Bust HTML caches across deploys; assets are fingerprinted so they
      // can be cached aggressively.
      cacheControl: [s3deploy.CacheControl.fromString('no-cache, no-store, must-revalidate')],
    });

    this.distributionUrl = `https://${distribution.distributionDomainName}`;

    new cdk.CfnOutput(this, 'WebsiteUrl', { value: this.distributionUrl });
    new cdk.CfnOutput(this, 'UserPoolId', { value: props.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: props.userPoolClientId });
    new cdk.CfnOutput(this, 'IdentityPoolId', { value: props.identityPoolId });
    new cdk.CfnOutput(this, 'ApiFunctionUrl', { value: props.apiFunctionUrl });
  }
}
