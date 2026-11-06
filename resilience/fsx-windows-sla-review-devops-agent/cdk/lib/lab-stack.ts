import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as fsx from 'aws-cdk-lib/aws-fsx';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import * as path from 'path';
import { LabEngine } from '../../../../shared/devops-agent/lab/cdk/lab-engine';
import { DirectoryFacts } from './directory-stack';
import { AlarmFacts, BACKUP_RETENTION_DAYS, FREE_STORAGE_THRESHOLD_BYTES } from './file-system-stack';

/**
 * The Demo Lab: the shared LabEngine (durable engine + API Lambda, one bundle from
 * shared/devops-agent/lab/lambda and this demo's lab/) fronted by a Lambda function URL that
 * only CloudFront may call (origin access control), and the Lab site (S3 + CloudFront).
 *
 * Authentication is HTTP Basic at the edge: a CloudFront Function checks every request (site
 * and /admin/* API alike) against the credentials deploy-all generated, then drops the header
 * so it does not collide with the SigV4 signature CloudFront adds for the function URL. No
 * API Gateway, no user pool: the demo has no application, only this control room.
 */
export interface LabStackProps extends cdk.StackProps {
  projectName: string;
  fileSystem: fsx.CfnFileSystem;
  directory: DirectoryFacts;
  alarms: AlarmFacts;
  triggerFunctionName: string;
  devOpsAgentRegion: string;
  devOpsAgentSpaceId: string;
  labUser: string;
  /** Empty (no --context labPassword) denies every request; deploy-all always sets it. */
  labPassword: string;
}

export class LabStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: LabStackProps) {
    super(scope, id, props);
    const { projectName, fileSystem, directory, alarms } = props;

    const fileSystemArn = this.formatArn({ service: 'fsx', resource: 'file-system', resourceName: fileSystem.ref });
    const alarmArn = (name: string) => this.formatArn({ service: 'cloudwatch', resource: 'alarm', resourceName: name, arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME });

    const lab = new LabEngine(this, 'Lab', {
      labDir: path.join(__dirname, '..', '..', 'lab'),
      stageDir: path.join(__dirname, '..', '.lab-stage'),
      engineHandler: 'engine_main.handler',
      apiHandler: 'api.handler',
      namePrefix: projectName,
      // revert() waits for FSx to settle before and after its update (up to ~12 minutes).
      engineTimeout: cdk.Duration.minutes(15),
      environment: {
        FILE_SYSTEM_ID: fileSystem.ref,
        DOMAIN_NAME: directory.domainName,
        DNS_IP: directory.dnsIp,
        DOMAIN_CONTROLLER_INSTANCE_ID: directory.domainController.instanceId,
        SERVICE_ACCOUNT_SECRET_ARN: directory.serviceAccountSecret.secretArn,
        BACKUP_RETENTION_DAYS: String(BACKUP_RETENTION_DAYS),
        FREE_STORAGE_ALARM_NAME: alarms.freeStorageAlarmName,
        FREE_STORAGE_THRESHOLD_BYTES: String(FREE_STORAGE_THRESHOLD_BYTES),
        MISCONFIGURED_ALARM_NAME: alarms.misconfiguredAlarmName,
        ALARMS_TOPIC_ARN: alarms.topic.topicArn,
        TRIGGER_LAMBDA_NAME: props.triggerFunctionName,
        DEVOPS_AGENT_REGION: props.devOpsAgentRegion,
        DEVOPS_AGENT_SPACE_ID: props.devOpsAgentSpaceId,
      },
      policyStatements: [
        new iam.PolicyStatement({ sid: 'ReadFileSystems', actions: ['fsx:DescribeFileSystems', 'fsx:DescribeBackups'], resources: ['*'] }),
        new iam.PolicyStatement({ sid: 'ChangeTheFileSystem', actions: ['fsx:UpdateFileSystem'], resources: [fileSystemArn] }),
        new iam.PolicyStatement({ sid: 'ReadAlarms', actions: ['cloudwatch:DescribeAlarms'], resources: ['*'] }),
        new iam.PolicyStatement({ sid: 'DeleteAndRecreateTheAlarm', actions: ['cloudwatch:PutMetricAlarm', 'cloudwatch:DeleteAlarms'], resources: [alarmArn(alarms.freeStorageAlarmName)] }),
        new iam.PolicyStatement({ sid: 'ReadServiceAccount', actions: ['secretsmanager:GetSecretValue'], resources: [directory.serviceAccountSecret.secretArn] }),
        new iam.PolicyStatement({ sid: 'ReadDomainController', actions: ['ec2:DescribeInstances', 'ec2:DescribeInstanceStatus'], resources: ['*'] }),
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
      cdk.Annotations.of(this).addWarningV2('fsx-sla-review:lab-password',
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

    // FunctionUrlOrigin.withOriginAccessControl grants lambda:InvokeFunctionUrl only; the OAC
    // documentation requires lambda:InvokeFunction as well, and without it every signed
    // request from CloudFront is a 403 (rendered as the site's index.html by the SPA error mapping).
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
  delete request.headers.authorization;
  return request;
}
`;
}
