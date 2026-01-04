import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';

interface FrontendStackProps extends cdk.StackProps {
  userPoolId: string;
  userPoolClientId: string;
  agentRuntimeArn: string;
  region: string;
  orchestratorFunctionArn?: string;
  orchestratorFunctionName?: string;
  lifecycleTableName?: string;
  configTableName?: string;
  userPool?: cognito.UserPool;
}

export class FrontendStack extends cdk.Stack {
  public readonly distributionUrl: string;
  public api?: apigateway.RestApi;
  public extractionFunction?: lambda.Function;
  public configFunction?: lambda.Function;
  public dataFunction?: lambda.Function;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    const websiteBucket = new s3.Bucket(this, 'WebsiteBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    const originAccessControl = new cloudfront.S3OriginAccessControl(this, 'OAC', {
      signing: cloudfront.Signing.SIGV4_NO_OVERRIDE,
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: 'AWS Services Lifecycle Tracker - Frontend Distribution',
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

    new s3deploy.BucketDeployment(this, 'DeployWebsite', {
      sources: [s3deploy.Source.asset('../frontend/dist')],
      destinationBucket: websiteBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    this.distributionUrl = `https://${distribution.distributionDomainName}`;

    new cdk.CfnOutput(this, 'WebsiteUrl', {
      value: this.distributionUrl,
      description: 'CloudFront Distribution URL',
    });

    new cdk.CfnOutput(this, 'BucketName', {
      value: websiteBucket.bucketName,
      description: 'S3 Bucket Name',
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: props.userPoolId,
      description: 'Cognito User Pool ID',
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: props.userPoolClientId,
      description: 'Cognito User Pool Client ID',
    });

    new cdk.CfnOutput(this, 'AgentRuntimeArn', {
      value: props.agentRuntimeArn,
      description: 'AgentCore Runtime ARN',
    });

    new cdk.CfnOutput(this, 'Region', {
      value: props.region,
      description: 'AWS Region',
    });

    // Add API Gateway and Lambda functions for admin interface
    if (props.userPool && props.orchestratorFunctionArn && props.lifecycleTableName && props.configTableName) {
      this.setupAdminApi(props);
    }
  }

  private setupAdminApi(props: FrontendStackProps) {
    // Create Cognito Authorizer for API Gateway
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, 'ApiAuthorizer', {
      cognitoUserPools: [props.userPool!],
      identitySource: 'method.request.header.Authorization'
    });

    // Create API Gateway
    this.api = new apigateway.RestApi(this, 'AdminApi', {
      restApiName: 'aws-services-lifecycle-admin-api',
      description: 'Admin API for AWS Services Lifecycle Tracker',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'X-Amz-Date', 'Authorization', 'X-Api-Key', 'X-Amz-Security-Token'],
      },
    });

    // Lambda function for extraction operations (UI triggers)
    this.extractionFunction = new lambda.Function(this, 'ExtractionApiFunction', {
      functionName: 'aws-services-lifecycle-extraction-api',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'extraction_api.lambda_handler',
      code: lambda.Code.fromAsset('lambda/api'),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      environment: {
        ORCHESTRATOR_FUNCTION_NAME: props.orchestratorFunctionName!,
        AGENT_RUNTIME_ARN: props.agentRuntimeArn,
        LIFECYCLE_TABLE_NAME: props.lifecycleTableName!,
        CONFIG_TABLE_NAME: props.configTableName!
      }
    });

    // Lambda function for service configuration management
    this.configFunction = new lambda.Function(this, 'ConfigApiFunction', {
      functionName: 'aws-services-lifecycle-config-api',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'config_api.lambda_handler',
      code: lambda.Code.fromAsset('lambda/api'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        CONFIG_TABLE_NAME: props.configTableName!
      }
    });

    // Lambda function for data operations (viewing deprecations)
    this.dataFunction = new lambda.Function(this, 'DataApiFunction', {
      functionName: 'aws-services-lifecycle-data-api',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'data_api.lambda_handler',
      code: lambda.Code.fromAsset('lambda/api'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        LIFECYCLE_TABLE_NAME: props.lifecycleTableName!,
        CONFIG_TABLE_NAME: props.configTableName!
      }
    });

    // IAM Permissions
    this.setupApiPermissions(props);
    this.setupApiRoutes(authorizer);

    // API Gateway outputs
    new cdk.CfnOutput(this, 'AdminApiUrl', {
      value: this.api.url,
      description: 'Admin API Gateway URL'
    });

    new cdk.CfnOutput(this, 'AdminApiId', {
      value: this.api.restApiId,
      description: 'Admin API Gateway ID'
    });
  }

  private setupApiPermissions(props: FrontendStackProps) {
    if (!this.extractionFunction || !this.configFunction || !this.dataFunction) {
      return;
    }

    // Permissions for extraction function
    this.extractionFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['lambda:InvokeFunction'],
      resources: [props.orchestratorFunctionArn!]
    }));

    this.extractionFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['bedrock-agentcore:InvokeAgentRuntime'],
      resources: [props.agentRuntimeArn]
    }));

    // DynamoDB permissions for all functions
    const dynamoDbPolicy = new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:GetItem',
        'dynamodb:PutItem',
        'dynamodb:UpdateItem',
        'dynamodb:DeleteItem',
        'dynamodb:Query',
        'dynamodb:Scan',
        'dynamodb:BatchGetItem',
        'dynamodb:BatchWriteItem'
      ],
      resources: [
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.lifecycleTableName!}`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.lifecycleTableName!}/index/*`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.configTableName!}`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.configTableName!}/index/*`
      ]
    });

    this.extractionFunction.addToRolePolicy(dynamoDbPolicy);
    this.configFunction.addToRolePolicy(dynamoDbPolicy);
    this.dataFunction.addToRolePolicy(dynamoDbPolicy);
  }

  private setupApiRoutes(authorizer: apigateway.CognitoUserPoolsAuthorizer) {
    if (!this.api || !this.extractionFunction || !this.configFunction || !this.dataFunction) {
      return;
    }

    // /extract - Manual extraction triggers from UI
    const extractResource = this.api.root.addResource('extract');
    
    // POST /extract - Trigger extraction (single service or all)
    extractResource.addMethod('POST', new apigateway.LambdaIntegration(this.extractionFunction), {
      authorizer
    });

    // POST /extract/test/{service} - Test extraction for specific service
    const testResource = extractResource.addResource('test').addResource('{service}');
    testResource.addMethod('POST', new apigateway.LambdaIntegration(this.extractionFunction), {
      authorizer
    });

    // GET /extract/status - Get current extraction status
    const statusResource = extractResource.addResource('status');
    statusResource.addMethod('GET', new apigateway.LambdaIntegration(this.extractionFunction), {
      authorizer
    });

    // /services - Service configuration management
    const servicesResource = this.api.root.addResource('services');
    
    // GET /services - List all service configurations
    servicesResource.addMethod('GET', new apigateway.LambdaIntegration(this.configFunction), {
      authorizer
    });

    // POST /services - Create new service configuration
    servicesResource.addMethod('POST', new apigateway.LambdaIntegration(this.configFunction), {
      authorizer
    });

    // Service-specific operations
    const serviceResource = servicesResource.addResource('{service}');
    
    // GET /services/{service} - Get specific service configuration
    serviceResource.addMethod('GET', new apigateway.LambdaIntegration(this.configFunction), {
      authorizer
    });

    // PUT /services/{service} - Update service configuration
    serviceResource.addMethod('PUT', new apigateway.LambdaIntegration(this.configFunction), {
      authorizer
    });

    // DELETE /services/{service} - Delete service configuration
    serviceResource.addMethod('DELETE', new apigateway.LambdaIntegration(this.configFunction), {
      authorizer
    });

    // /deprecations - View deprecation data
    const deprecationsResource = this.api.root.addResource('deprecations');
    
    // GET /deprecations - List all deprecations with filtering
    deprecationsResource.addMethod('GET', new apigateway.LambdaIntegration(this.dataFunction), {
      authorizer
    });

    // GET /deprecations/{service} - Get deprecations for specific service
    const serviceDeprecationsResource = deprecationsResource.addResource('{service}');
    serviceDeprecationsResource.addMethod('GET', new apigateway.LambdaIntegration(this.dataFunction), {
      authorizer
    });

    // /admin - Administrative operations
    const adminResource = this.api.root.addResource('admin');
    
    // GET /admin/health - System health check
    const healthResource = adminResource.addResource('health');
    healthResource.addMethod('GET', new apigateway.LambdaIntegration(this.dataFunction), {
      authorizer
    });

    // GET /admin/metrics - System metrics
    const metricsResource = adminResource.addResource('metrics');
    metricsResource.addMethod('GET', new apigateway.LambdaIntegration(this.dataFunction), {
      authorizer
    });

    // POST /admin/refresh-all - Trigger full system refresh
    const refreshResource = adminResource.addResource('refresh-all');
    refreshResource.addMethod('POST', new apigateway.LambdaIntegration(this.extractionFunction), {
      authorizer
    });
  }
}
