import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';

export interface ApiStackProps extends cdk.StackProps {
  orchestratorFunctionArn: string;
  orchestratorFunctionName: string;
  agentRuntimeArn: string;
  lifecycleTableName: string;
  configTableName: string;
  userPool: cognito.UserPool;
  userPoolClient: cognito.UserPoolClient;
}

export class AWSServicesLifecycleTrackerApi extends cdk.Stack {
  public readonly api: apigateway.RestApi;
  public readonly extractionFunction: lambda.Function;
  public readonly configFunction: lambda.Function;
  public readonly dataFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    // Create Cognito Authorizer for API Gateway
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, 'ApiAuthorizer', {
      cognitoUserPools: [props.userPool],
      identitySource: 'method.request.header.Authorization'
    });

    // Create API Gateway
    this.api = new apigateway.RestApi(this, 'LifecycleTrackerApi', {
      restApiName: 'aws-services-lifecycle-tracker-api',
      description: 'API for AWS Services Lifecycle Tracker admin interface',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'X-Amz-Date', 'Authorization', 'X-Api-Key', 'X-Amz-Security-Token'],
      },
    }); 
   // Lambda function for extraction operations
    this.extractionFunction = new lambda.Function(this, 'ExtractionApiFunction', {
      functionName: 'aws-services-lifecycle-extraction-api',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'extraction_api.lambda_handler',
      code: lambda.Code.fromAsset('lambda/api'),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      environment: {
        ORCHESTRATOR_FUNCTION_NAME: props.orchestratorFunctionName,
        AGENT_RUNTIME_ARN: props.agentRuntimeArn,
        LIFECYCLE_TABLE_NAME: props.lifecycleTableName,
        CONFIG_TABLE_NAME: props.configTableName,
        AWS_REGION: this.region
      }
    });

    // Lambda function for configuration operations
    this.configFunction = new lambda.Function(this, 'ConfigApiFunction', {
      functionName: 'aws-services-lifecycle-config-api',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'config_api.lambda_handler',
      code: lambda.Code.fromAsset('lambda/api'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        CONFIG_TABLE_NAME: props.configTableName,
        AWS_REGION: this.region
      }
    });

    // Lambda function for data operations
    this.dataFunction = new lambda.Function(this, 'DataApiFunction', {
      functionName: 'aws-services-lifecycle-data-api',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'data_api.lambda_handler',
      code: lambda.Code.fromAsset('lambda/api'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        LIFECYCLE_TABLE_NAME: props.lifecycleTableName,
        CONFIG_TABLE_NAME: props.configTableName,
        AWS_REGION: this.region
      }
    });    
// IAM Permissions for extraction function
    this.extractionFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['lambda:InvokeFunction'],
      resources: [props.orchestratorFunctionArn]
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
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.lifecycleTableName}`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.lifecycleTableName}/index/*`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.configTableName}`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.configTableName}/index/*`
      ]
    });

    this.extractionFunction.addToRolePolicy(dynamoDbPolicy);
    this.configFunction.addToRolePolicy(dynamoDbPolicy);
    this.dataFunction.addToRolePolicy(dynamoDbPolicy);    
// API Routes - Basic endpoints
    const extractResource = this.api.root.addResource('extract');
    extractResource.addMethod('POST', new apigateway.LambdaIntegration(this.extractionFunction), {
      authorizer
    });

    const servicesResource = this.api.root.addResource('services');
    servicesResource.addMethod('GET', new apigateway.LambdaIntegration(this.configFunction), {
      authorizer
    });

    const deprecationsResource = this.api.root.addResource('deprecations');
    deprecationsResource.addMethod('GET', new apigateway.LambdaIntegration(this.dataFunction), {
      authorizer
    });

    // Outputs
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: this.api.url,
      description: 'API Gateway URL for admin interface'
    });

    new cdk.CfnOutput(this, 'ApiId', {
      value: this.api.restApiId,
      description: 'API Gateway ID'
    });
  }
}