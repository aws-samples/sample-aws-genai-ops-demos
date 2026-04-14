import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import { Construct } from 'constructs';

export interface FrontendStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  apiGatewayEndpoint?: string;
  adminApiId?: string;
  adminApiStageName?: string;
}

export class FrontendStack extends cdk.Stack {
  public readonly websiteBucketName: string;
  public readonly cloudFrontDistributionId: string;
  public readonly cloudFrontDomainName: string;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    const { environment, projectName, apiGatewayEndpoint, adminApiId, adminApiStageName } = props;

    // -----------------------------------------------------------------------
    // S3 Bucket — AES256, all public access blocked, destroy on teardown
    // Matches original CloudFormation cloudfront-s3.yaml WebsiteBucket
    // -----------------------------------------------------------------------
    const websiteBucket = new s3.Bucket(this, 'WebsiteBucket', {
      bucketName: `${projectName}-${environment}-merchant-portal-${cdk.Aws.ACCOUNT_ID}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // -----------------------------------------------------------------------
    // CloudFront Origin Access Control (L1) — sigv4 for S3
    // -----------------------------------------------------------------------
    const oac = new cloudfront.CfnOriginAccessControl(this, 'CloudFrontOAC', {
      originAccessControlConfig: {
        name: `${projectName}-${environment}-oac`,
        description: 'OAC for Merchant Portal S3 bucket',
        originAccessControlOriginType: 's3',
        signingBehavior: 'always',
        signingProtocol: 'sigv4',
      },
    });

    // -----------------------------------------------------------------------
    // Cache Policy — 86400s default TTL, brotli + gzip
    // Matches original CloudFormation cloudfront-s3.yaml CachePolicy
    // -----------------------------------------------------------------------
    const cachePolicy = new cloudfront.CachePolicy(this, 'CachePolicy', {
      cachePolicyName: `${projectName}-${environment}-cache-policy`,
      defaultTtl: cdk.Duration.seconds(86400),
      maxTtl: cdk.Duration.seconds(31536000),
      minTtl: cdk.Duration.seconds(0),
      enableAcceptEncodingBrotli: true,
      enableAcceptEncodingGzip: true,
      cookieBehavior: cloudfront.CacheCookieBehavior.none(),
      headerBehavior: cloudfront.CacheHeaderBehavior.none(),
      queryStringBehavior: cloudfront.CacheQueryStringBehavior.none(),
    });

    // -----------------------------------------------------------------------
    // Response Headers Policy — CSP, HSTS, X-Frame-Options DENY, XSS
    // Matches original CloudFormation cloudfront-s3.yaml ResponseHeadersPolicy
    // -----------------------------------------------------------------------
    const responseHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, 'ResponseHeadersPolicy', {
      responseHeadersPolicyName: `${projectName}-${environment}-security-headers`,
      securityHeadersBehavior: {
        contentSecurityPolicy: {
          contentSecurityPolicy: "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self' https://*.amazonaws.com https://*.amazoncognito.com; frame-ancestors 'none'",
          override: true,
        },
        contentTypeOptions: { override: true },
        frameOptions: {
          frameOption: cloudfront.HeadersFrameOption.DENY,
          override: true,
        },
        referrerPolicy: {
          referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
          override: true,
        },
        strictTransportSecurity: {
          accessControlMaxAge: cdk.Duration.seconds(31536000),
          includeSubdomains: true,
          preload: true,
          override: true,
        },
        xssProtection: {
          protection: true,
          modeBlock: true,
          override: true,
        },
      },
    });

    // -----------------------------------------------------------------------
    // Build origins and behaviors
    // -----------------------------------------------------------------------

    // S3 origin using OAC (L2 S3BucketOrigin handles bucket policy automatically)
    const s3Origin = origins.S3BucketOrigin.withOriginAccessControl(websiteBucket);

    // Default behavior — S3 static assets
    const defaultBehavior: cloudfront.BehaviorOptions = {
      origin: s3Origin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
      cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD,
      cachePolicy,
      responseHeadersPolicy,
      compress: true,
    };

    // Additional behaviors — index.html no-cache + optional API origin
    const additionalBehaviors: Record<string, cloudfront.BehaviorOptions> = {};

    // index.html — no caching for SPA routing
    additionalBehaviors['/index.html'] = {
      origin: s3Origin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
      cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      responseHeadersPolicy,
      compress: true,
    };

    // Optional API origin (NLB endpoint) with /api/* behavior
    if (apiGatewayEndpoint) {
      const apiOrigin = new origins.HttpOrigin(apiGatewayEndpoint, {
        httpPort: 80,
        httpsPort: 443,
        protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      });

      additionalBehaviors['/api/*'] = {
        origin: apiOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        responseHeadersPolicy,
        compress: true,
      };
    }

    // Optional Admin API origin (API Gateway) with /admin/* behavior
    if (adminApiId) {
      const adminDomain = `${adminApiId}.execute-api.${cdk.Aws.REGION}.amazonaws.com`;
      const adminOrigin = new origins.HttpOrigin(adminDomain, {
        originPath: `/${adminApiStageName || 'prod'}`,
        httpsPort: 443,
        protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
      });

      additionalBehaviors['/admin/*'] = {
        origin: adminOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        responseHeadersPolicy,
        compress: true,
      };
    }

    // -----------------------------------------------------------------------
    // CloudFront Distribution
    // Matches original CloudFormation cloudfront-s3.yaml CloudFrontDistribution
    // -----------------------------------------------------------------------
    const distribution = new cloudfront.Distribution(this, 'CloudFrontDistribution', {
      comment: `Merchant Portal - ${environment}`,
      defaultRootObject: 'index.html',
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      enableIpv6: true,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      defaultBehavior,
      additionalBehaviors,
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
      ],
    });

    // -----------------------------------------------------------------------
    // Override the L2 distribution to attach OAC via L1 escape hatch
    // The L2 S3BucketOrigin.withOriginAccessControl creates its own OAC,
    // but we keep our explicit CfnOriginAccessControl for naming consistency.
    // -----------------------------------------------------------------------

    // -----------------------------------------------------------------------
    // Expose properties for cross-stack references
    // -----------------------------------------------------------------------
    this.websiteBucketName = websiteBucket.bucketName;
    this.cloudFrontDistributionId = distribution.distributionId;
    this.cloudFrontDomainName = distribution.distributionDomainName;

    // -----------------------------------------------------------------------
    // CloudFormation Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'WebsiteBucketName', {
      description: 'Name of the S3 bucket for website content',
      value: websiteBucket.bucketName,
    });

    new cdk.CfnOutput(this, 'CloudFrontDistributionId', {
      description: 'CloudFront distribution ID',
      value: distribution.distributionId,
    });

    new cdk.CfnOutput(this, 'CloudFrontDomainName', {
      description: 'CloudFront distribution domain name',
      value: distribution.distributionDomainName,
    });
  }
}
