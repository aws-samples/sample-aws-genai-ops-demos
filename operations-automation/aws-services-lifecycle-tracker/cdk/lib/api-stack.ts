import * as cdk from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpUserPoolAuthorizer } from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

export interface ApiStackProps extends cdk.StackProps {
  apiFunction: lambda.IFunction;
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
}

/**
 * HTTP API in front of the API Lambda (issue #139).
 *
 * The Cognito user-pool JWT authorizer validates the ID token the SPA sends
 * in the Authorization header; the browser itself holds no AWS credentials.
 *
 *   POST /actions          router actions (list_services, update_service, ...)
 *   POST /refresh          start (or adopt) a durable pipeline execution
 *   GET  /refresh/{arn}    execution status for UI polling / re-attach
 */
export class ApiStack extends cdk.Stack {
  public readonly httpApi: apigwv2.HttpApi;
  public readonly apiUrl: string;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const authorizer = new HttpUserPoolAuthorizer('CognitoAuthorizer', props.userPool, {
      userPoolClients: [props.userPoolClient],
    });

    this.httpApi = new apigwv2.HttpApi(this, 'LifecycleTrackerHttpApi', {
      apiName: 'aws-services-lifecycle-tracker-api',
      description: 'AWS Services Lifecycle Tracker UI API (Cognito JWT)',
      defaultAuthorizer: authorizer,
      corsPreflight: {
        // The CloudFront domain is only known after the frontend deploys,
        // which itself needs this API URL at build time; auth is enforced
        // by the JWT authorizer, not by the origin allow-list.
        allowOrigins: ['*'],
        allowMethods: [apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST, apigwv2.CorsHttpMethod.OPTIONS],
        allowHeaders: ['Content-Type', 'Authorization'],
        maxAge: cdk.Duration.hours(1),
      },
    });

    const integration = new HttpLambdaIntegration('ApiFunctionIntegration', props.apiFunction);

    this.httpApi.addRoutes({ path: '/actions', methods: [apigwv2.HttpMethod.POST], integration });
    this.httpApi.addRoutes({ path: '/refresh', methods: [apigwv2.HttpMethod.POST], integration });
    // Greedy: durable execution ARNs contain '/' segments
    this.httpApi.addRoutes({ path: '/refresh/{arn+}', methods: [apigwv2.HttpMethod.GET], integration });

    // apiEndpoint has no trailing slash
    this.apiUrl = this.httpApi.apiEndpoint;

    new cdk.CfnOutput(this, 'ApiUrl', {
      value: this.apiUrl,
      description: 'HTTP API base URL used by the frontend (VITE_API_URL)',
    });
    new cdk.CfnOutput(this, 'ApiId', {
      value: this.httpApi.apiId,
      description: 'HTTP API ID',
    });
  }
}
