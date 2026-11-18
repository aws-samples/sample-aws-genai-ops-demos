import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import * as path from 'path';
import { LabEngine } from '../../../../shared/devops-agent/lab/cdk/lab-engine';
import { BUSINESS_TAGS, CONNECTIONS_THRESHOLD, LOAD_CONCURRENCY_ON, MAX_CONNECTIONS } from './constants';
import { DatabaseStack } from './database-stack';

/**
 * The Demo Lab: the shared LabEngine (durable engine + API Lambda, one bundle from
 * shared/devops-agent/lab/lambda and this demo's lab/) fronted by a Lambda function URL that
 * only CloudFront may call (origin access control), and the Lab site (S3 + CloudFront).
 *
 * Authentication is HTTP Basic at the edge: a CloudFront Function checks every request (site
 * and /admin/* API alike) against the credentials deploy-all generated, then drops the header
 * so it does not collide with the SigV4 signature CloudFront adds for the function URL. No
 * API Gateway, no user pool: the demo has no application, only this control room.
 *
 * The handlers change the cluster's tags and the load function's reserved concurrency; nothing
 * here needs the VPC (the load function is the only code that talks MySQL).
 */
/**
 * Header the Lab UI adds to every POST/DELETE (frontend/src/lab/api.ts). The edge function refuses
 * state-changing /admin/* requests without it, so a cross-site form cannot start an injection
 * with the presenter's cached Basic credentials.
 */
export const LAB_REQUEST_HEADER = 'x-lab-request';
export interface LabStackProps extends cdk.StackProps {
  projectName: string;
  database: DatabaseStack;
  devOpsAgentRegion: string;
  devOpsAgentSpaceId: string;
  labUser: string;
  /** Empty (no --context labPassword) denies every request; deploy-all always sets it. */
  labPassword: string;
}

export class LabStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: LabStackProps) {
    super(scope, id, props);
    const { projectName, database } = props;
    const loadFunctionArn = this.formatArn({
      service: 'lambda', resource: 'function', resourceName: database.loadFunctionName, arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
    });

    const lab = new LabEngine(this, 'Lab', {
      labDir: path.join(__dirname, '..', '..', 'lab'),
      stageDir: path.join(__dirname, '..', '.lab-stage'),
      engineHandler: 'engine_main.handler',
      apiHandler: 'api.handler',
      namePrefix: projectName,
      // revert() waits for DatabaseConnections to fall back under the alarm threshold (up to 4 minutes).
      engineTimeout: cdk.Duration.minutes(6),
      environment: {
        CLUSTER_ID: database.clusterIdentifier,
        CLUSTER_ARN: database.clusterArn,
        WRITER_INSTANCE_ID: database.writerIdentifier,
        LOAD_FUNCTION_NAME: database.loadFunctionName,
        LOAD_CONCURRENCY_ON: String(LOAD_CONCURRENCY_ON),
        CONNECTIONS_ALARM_NAME: database.connectionsAlarmName,
        CONNECTIONS_THRESHOLD: String(CONNECTIONS_THRESHOLD),
        MAX_CONNECTIONS: String(MAX_CONNECTIONS),
        BASELINE_TAGS: JSON.stringify(BUSINESS_TAGS),
        TRIGGER_LAMBDA_NAME: database.triggerFunctionName,
        DEVOPS_AGENT_REGION: props.devOpsAgentRegion,
        DEVOPS_AGENT_SPACE_ID: props.devOpsAgentSpaceId,
      },
      policyStatements: [
        new iam.PolicyStatement({ sid: 'ReadTheCluster', actions: ['rds:DescribeDBClusters', 'rds:DescribeDBInstances'], resources: ['*'] }),
        new iam.PolicyStatement({
          sid: 'ChangeTheClusterTags',
          actions: ['rds:ListTagsForResource', 'rds:AddTagsToResource', 'rds:RemoveTagsFromResource'],
          resources: [database.clusterArn],
        }),
        new iam.PolicyStatement({
          sid: 'SwitchTheLoadFunction',
          actions: ['lambda:GetFunctionConcurrency', 'lambda:PutFunctionConcurrency', 'lambda:InvokeFunction'],
          resources: [loadFunctionArn],
        }),
        new iam.PolicyStatement({ sid: 'ReadMetricsAndAlarms', actions: ['cloudwatch:GetMetricData', 'cloudwatch:DescribeAlarms'], resources: ['*'] }),
      ],
    });

    // The API, reachable only through CloudFront (SigV4 by origin access control).
    const apiUrl = lab.apiFunction.addFunctionUrl({ authType: lambda.FunctionUrlAuthType.AWS_IAM });

    const siteBucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    if (!props.labPassword) {
      cdk.Annotations.of(this).addWarningV2('aurora-bizctx:lab-password',
        'No labPassword context: the Lab site denies every request. deploy-all generates one and prints it.');
    }
    const basicAuth = new cloudfront.Function(this, 'BasicAuth', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      comment: 'HTTP Basic authentication for the Demo Lab site and API',
      code: cloudfront.FunctionCode.fromInline(basicAuthFunction(props.labUser, props.labPassword)),
    });
    const viewerRequest = [{ function: basicAuth, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }];

    const distribution = new cloudfront.Distribution(this, 'Site', {
      comment: `${projectName} Demo Lab`,
      defaultRootObject: 'index.html',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        functionAssociations: viewerRequest,
      },
      additionalBehaviors: {
        '/admin/*': {
          origin: origins.FunctionUrlOrigin.withOriginAccessControl(apiUrl),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          functionAssociations: viewerRequest,
        },
      },
      // Single-page app: unknown paths (S3 answers 403 behind origin access control) load the app.
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: cdk.Duration.seconds(0) },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: cdk.Duration.seconds(0) },
      ],
    });
    // FunctionUrlOrigin.withOriginAccessControl grants lambda:InvokeFunctionUrl only; OAC also
    // needs lambda:InvokeFunction, or every signed request from CloudFront is a 403.
    lab.apiFunction.addPermission('InvokeFromCloudFront', {
      principal: new iam.ServicePrincipal('cloudfront.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: distribution.distributionArn,
    });

    new cdk.CfnOutput(this, 'LabUrl', { value: `https://${distribution.distributionDomainName}/`, description: 'The Demo Lab (HTTP Basic authentication, credentials printed by deploy-all)' });
    new cdk.CfnOutput(this, 'LabUser', { value: props.labUser });
    new cdk.CfnOutput(this, 'SiteBucketName', { value: siteBucket.bucketName });
    new cdk.CfnOutput(this, 'DistributionId', { value: distribution.distributionId });
    new cdk.CfnOutput(this, 'LabEngineAliasArn', { value: lab.engineAlias.functionArn, description: 'Qualified ARN of the Lab engine durable function' });
  }
}

/**
 * CloudFront Function (JavaScript runtime 2.0): HTTP Basic authentication at the edge. The
 * expected `user:password` pair is embedded base64-encoded; an empty password denies everything.
 * The Authorization header is removed once checked so it never reaches the SigV4-signed origin.
 */
function basicAuthFunction(user: string, password: string): string {
  const expected = password ? `Basic ${Buffer.from(`${user}:${password}`).toString('base64')}` : '';
  return `
function handler(event) {
  var request = event.request;
  var expected = ${JSON.stringify(expected)};
  var header = request.headers.authorization;
  if (!expected || !header || header.value !== expected) {
    return {
      statusCode: 401,
      statusDescription: 'Unauthorized',
      headers: { 'www-authenticate': { value: 'Basic realm="AWS DevOps Agent Demo Lab"' } },
    };
  }
  // State-changing Lab calls must come from the Lab's own fetch: a cross-site HTML form cannot
  // set a custom header, and a cross-site fetch that sets one needs a CORS preflight the API never grants.
  if (request.method !== 'GET' && request.method !== 'HEAD' && request.uri.indexOf('/admin/') === 0
      && !request.headers['${LAB_REQUEST_HEADER}']) {
    return { statusCode: 403, statusDescription: 'Forbidden' };
  }
  delete request.headers.authorization;
  return request;
}
`;
}
