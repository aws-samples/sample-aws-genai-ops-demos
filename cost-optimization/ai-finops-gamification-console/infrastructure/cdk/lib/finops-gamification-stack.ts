import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';

export class FinOpsGamificationStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const region = cdk.Stack.of(this).region;

    // ==================== Authentication (Cognito) ====================
    
    // User Pool
    const userPool = new cognito.UserPool(this, 'FinOpsUserPool', {
      userPoolName: `finops-gamification-users-${region}`,
      selfSignUpEnabled: false,
      signInAliases: {
        email: true,
      },
      autoVerify: {
        email: true,
      },
      standardAttributes: {
        email: {
          required: true,
          mutable: true,
        },
        givenName: {
          required: true,
          mutable: true,
        },
        familyName: {
          required: true,
          mutable: true,
        },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // User Pool Groups (RBAC)
    new cognito.CfnUserPoolGroup(this, 'AdminGroup', {
      userPoolId: userPool.userPoolId,
      groupName: 'finops-admin',
      description: 'Full access: governance, config, all teams',
    });

    new cognito.CfnUserPoolGroup(this, 'ChampionGroup', {
      userPoolId: userPool.userPoolId,
      groupName: 'champion',
      description: 'Own team findings, accept/reject, personal stats',
    });

    new cognito.CfnUserPoolGroup(this, 'ViewerGroup', {
      userPoolId: userPool.userPoolId,
      groupName: 'viewer',
      description: 'Read-only dashboards and leaderboard',
    });

    // User Pool Client
    const userPoolClient = new cognito.UserPoolClient(this, 'FinOpsUserPoolClient', {
      userPool,
      userPoolClientName: 'finops-gamification-web',
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      generateSecret: false,
      preventUserExistenceErrors: true,
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
    });

    // ==================== Data Layer (DynamoDB) ====================

    // Teams table
    const teamsTable = new dynamodb.Table(this, 'TeamsTable', {
      tableName: `finops-teams-${region}`,
      partitionKey: { name: 'teamId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pointInTimeRecovery: true,
    });

    // Scoping rules table
    const scopingTable = new dynamodb.Table(this, 'ScopingTable', {
      tableName: `finops-scoping-${region}`,
      partitionKey: { name: 'ruleId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    scopingTable.addGlobalSecondaryIndex({
      indexName: 'teamId-index',
      partitionKey: { name: 'teamId', type: dynamodb.AttributeType.STRING },
    });

    // Findings table
    const findingsTable = new dynamodb.Table(this, 'FindingsTable', {
      tableName: `finops-findings-${region}`,
      partitionKey: { name: 'findingId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pointInTimeRecovery: true,
    });

    findingsTable.addGlobalSecondaryIndex({
      indexName: 'status-createdAt-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'createdAt', type: dynamodb.AttributeType.STRING },
    });

    findingsTable.addGlobalSecondaryIndex({
      indexName: 'teamId-status-index',
      partitionKey: { name: 'assignedTeamId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'status', type: dynamodb.AttributeType.STRING },
    });

    // Learnings table
    const learningsTable = new dynamodb.Table(this, 'LearningsTable', {
      tableName: `finops-learnings-${region}`,
      partitionKey: { name: 'learningId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    learningsTable.addGlobalSecondaryIndex({
      indexName: 'service-category-index',
      partitionKey: { name: 'service', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'category', type: dynamodb.AttributeType.STRING },
    });

    // Scores table
    const scoresTable = new dynamodb.Table(this, 'ScoresTable', {
      tableName: `finops-scores-${region}`,
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'month', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    scoresTable.addGlobalSecondaryIndex({
      indexName: 'month-savings-index',
      partitionKey: { name: 'month', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'totalSavingsUsd', type: dynamodb.AttributeType.NUMBER },
    });

    // ==================== Secrets ====================

    // Slack Bot Token secret (placeholder - user configures after deployment)
    const slackSecret = new secretsmanager.Secret(this, 'SlackBotToken', {
      secretName: `finops-gamification/slack-bot-token-${region}`,
      description: 'Slack Bot Token for FinOps Agent report ingestion',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ token: 'PLACEHOLDER' }),
        generateStringKey: 'placeholder',
      },
    });

    // ==================== Lambda Functions ====================

    // Common Lambda environment
    const lambdaEnvironment = {
      TEAMS_TABLE: teamsTable.tableName,
      SCOPING_TABLE: scopingTable.tableName,
      FINDINGS_TABLE: findingsTable.tableName,
      LEARNINGS_TABLE: learningsTable.tableName,
      SCORES_TABLE: scoresTable.tableName,
      USER_POOL_ID: userPool.userPoolId,
      REGION: region,
    };

    // API Lambda (handles all API routes)
    const apiLambda = new lambda.Function(this, 'ApiLambda', {
      functionName: `FinOpsAPI-${region}`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'api_handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../../src/lambda/api')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: lambdaEnvironment,
      logRetention: logs.RetentionDays.ONE_WEEK,
    });

    // Grant DynamoDB permissions to API Lambda
    teamsTable.grantReadWriteData(apiLambda);
    scopingTable.grantReadWriteData(apiLambda);
    findingsTable.grantReadWriteData(apiLambda);
    learningsTable.grantReadWriteData(apiLambda);
    scoresTable.grantReadWriteData(apiLambda);

    // Ingestion Lambda (processes Slack messages)
    const ingestionLambda = new lambda.Function(this, 'IngestionLambda', {
      functionName: `FinOpsIngestion-${region}`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'ingestion_handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../../src/lambda/ingestion')),
      timeout: cdk.Duration.minutes(5),
      memorySize: 1024,
      environment: {
        ...lambdaEnvironment,
        SLACK_SECRET_ARN: slackSecret.secretArn,
        SLACK_CHANNEL_ID: '', // Configured post-deployment
      },
      logRetention: logs.RetentionDays.ONE_WEEK,
    });

    // Grant permissions to Ingestion Lambda
    findingsTable.grantReadWriteData(ingestionLambda);
    learningsTable.grantReadData(ingestionLambda);
    scopingTable.grantReadData(ingestionLambda);
    slackSecret.grantRead(ingestionLambda);

    // EventBridge rule for scheduled ingestion
    new events.Rule(this, 'IngestionSchedule', {
      ruleName: `finops-ingestion-schedule-${region}`,
      description: 'Trigger FinOps report ingestion from Slack',
      schedule: events.Schedule.rate(cdk.Duration.hours(1)),
      targets: [new targets.LambdaFunction(ingestionLambda)],
      enabled: false, // Disabled by default - enable after Slack configuration
    });

    // ==================== API Gateway ====================

    // REST API
    const api = new apigateway.RestApi(this, 'FinOpsApi', {
      restApiName: `finops-gamification-api-${region}`,
      description: 'FinOps Gamification Console API',
      deployOptions: {
        stageName: 'api',
        throttlingBurstLimit: 100,
        throttlingRateLimit: 50,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization', 'X-Amz-Date', 'X-Api-Key'],
      },
    });

    // Cognito Authorizer
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, 'CognitoAuthorizer', {
      cognitoUserPools: [userPool],
      authorizerName: 'finops-cognito-authorizer',
    });

    // Lambda integration
    const lambdaIntegration = new apigateway.LambdaIntegration(apiLambda);

    // API Routes
    const findings = api.root.addResource('findings');
    findings.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    
    const findingById = findings.addResource('{findingId}');
    findingById.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    findingById.addMethod('PATCH', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    
    const acceptFinding = findingById.addResource('accept');
    acceptFinding.addMethod('POST', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    
    const rejectFinding = findingById.addResource('reject');
    rejectFinding.addMethod('POST', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });

    const teams = api.root.addResource('teams');
    teams.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    teams.addMethod('POST', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    
    const teamById = teams.addResource('{teamId}');
    teamById.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    teamById.addMethod('PUT', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    teamById.addMethod('DELETE', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });

    const scoping = api.root.addResource('scoping');
    scoping.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    scoping.addMethod('POST', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });
    
    const scopingById = scoping.addResource('{ruleId}');
    scopingById.addMethod('DELETE', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });

    const leaderboard = api.root.addResource('leaderboard');
    leaderboard.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });

    const scores = api.root.addResource('scores');
    const scoresByUser = scores.addResource('{userId}');
    scoresByUser.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });

    const learnings = api.root.addResource('learnings');
    learnings.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });

    const dashboard = api.root.addResource('dashboard');
    dashboard.addMethod('GET', lambdaIntegration, { authorizer, authorizationType: apigateway.AuthorizationType.COGNITO });

    // ==================== Frontend Hosting ====================

    // S3 bucket for frontend
    const websiteBucket = new s3.Bucket(this, 'WebsiteBucket', {
      bucketName: `finops-gamification-frontend-${this.account}-${region}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // CloudFront Origin Access Control
    const oac = new cloudfront.CfnOriginAccessControl(this, 'OAC', {
      originAccessControlConfig: {
        name: `finops-gamification-oac-${region}`,
        originAccessControlOriginType: 's3',
        signingBehavior: 'always',
        signingProtocol: 'sigv4',
      },
    });

    // CloudFront Distribution
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: new origins.S3Origin(websiteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
      ],
    });

    // Grant CloudFront access to S3
    websiteBucket.addToResourcePolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'],
      resources: [websiteBucket.arnForObjects('*')],
      principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
      conditions: {
        StringEquals: {
          'AWS:SourceArn': `arn:aws:cloudfront::${this.account}:distribution/${distribution.distributionId}`,
        },
      },
    }));

    // ==================== Outputs ====================

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: userPool.userPoolId,
      description: 'Cognito User Pool ID',
      exportName: `FinOpsUserPoolId-${region}`,
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
      exportName: `FinOpsUserPoolClientId-${region}`,
    });

    new cdk.CfnOutput(this, 'ApiEndpoint', {
      value: api.url,
      description: 'API Gateway endpoint URL',
      exportName: `FinOpsApiEndpoint-${region}`,
    });

    new cdk.CfnOutput(this, 'WebsiteUrl', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'CloudFront website URL',
      exportName: `FinOpsWebsiteUrl-${region}`,
    });

    new cdk.CfnOutput(this, 'WebsiteBucketName', {
      value: websiteBucket.bucketName,
      description: 'S3 bucket for frontend hosting',
      exportName: `FinOpsWebsiteBucket-${region}`,
    });

    new cdk.CfnOutput(this, 'CloudFrontDistributionId', {
      value: distribution.distributionId,
      description: 'CloudFront distribution ID',
      exportName: `FinOpsDistributionId-${region}`,
    });

    new cdk.CfnOutput(this, 'SlackSecretArn', {
      value: slackSecret.secretArn,
      description: 'Slack Bot Token secret ARN (configure after deployment)',
      exportName: `FinOpsSlackSecretArn-${region}`,
    });

    new cdk.CfnOutput(this, 'Region', {
      value: region,
      description: 'Deployment region',
    });
  }
}
