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

/** HTTP API behind a Cognito JWT authorizer. No CORS: CloudFront serves the site and /api/* from one origin. */
export class ApiStack extends cdk.Stack {
  public readonly httpApi: apigwv2.HttpApi;
  public readonly apiId: string;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const authorizer = new HttpUserPoolAuthorizer('Authorizer', props.userPool, {
      userPoolClients: [props.userPoolClient],
    });

    this.httpApi = new apigwv2.HttpApi(this, 'HttpApi', {
      apiName: 's3-data-protection-api',
      description: 'S3 data protection advisor API (Cognito JWT)',
      defaultAuthorizer: authorizer,
    });

    const integration = new HttpLambdaIntegration('ApiFunctionIntegration', props.apiFunction);
    const { GET, POST } = apigwv2.HttpMethod;
    this.httpApi.addRoutes({ path: '/api/scans', methods: [POST, GET], integration });
    this.httpApi.addRoutes({ path: '/api/scans/{id}', methods: [GET], integration });
    this.httpApi.addRoutes({ path: '/api/scans/{id}/report', methods: [GET], integration });
    this.httpApi.addRoutes({ path: '/api/sample', methods: [GET], integration });

    this.apiId = this.httpApi.apiId;

    new cdk.CfnOutput(this, 'ApiUrl', { value: this.httpApi.apiEndpoint, description: 'HTTP API base URL' });
    new cdk.CfnOutput(this, 'ApiId', { value: this.apiId, exportName: `S3DataProtectionApiId-${this.region}` });
  }
}
