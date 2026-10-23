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
 * HTTP API in front of the API Lambda (v1.1 Phase 1).
 *
 * The Cognito user-pool JWT authorizer validates the ID token the SPA sends in
 * the Authorization header; the browser itself holds no AWS credentials.
 *
 *   POST /actions          router actions (list_functions, get_stats, settings, ...)
 *   POST /refresh          start (or adopt) a durable pipeline execution
 *   GET  /refresh/{arn+}   execution status for UI polling / re-attach
 *   GET  /scans            recent durable executions for the scan-log timeline
 *   POST /migrate          start a one-function migration (Phase 2)
 *   POST /migrate-batch    start one batch migration over a list of ARNs
 *   GET  /migrate/{arn+}   one function's mig# item
 *   GET  /migrate-run/{arn+}  one batch execution's status + phases
 *   GET  /migrate-runs     recent batch executions (Steps card re-attach, Activity log)
 */
export class ApiStack extends cdk.Stack {
  public readonly httpApi: apigwv2.HttpApi;
  public readonly apiUrl: string;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const authorizer = new HttpUserPoolAuthorizer('CognitoAuthorizer', props.userPool, {
      userPoolClients: [props.userPoolClient],
    });

    this.httpApi = new apigwv2.HttpApi(this, 'MigrationHttpApi', {
      apiName: 'lambda-runtime-migration-api',
      description: 'AI Lambda Runtime Migration UI API (Cognito JWT)',
      defaultAuthorizer: authorizer,
      corsPreflight: {
        // The CloudFront domain is only known after the frontend deploys, which
        // itself needs this API URL at build time; auth is enforced by the JWT
        // authorizer, not by the origin allow-list.
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
    // Scan log: the last ~10 durable executions (newest first) for the timeline.
    this.httpApi.addRoutes({ path: '/scans', methods: [apigwv2.HttpMethod.GET], integration });
    // Migration routes: the API function reaches the orchestrator through
    // MIGRATE_FUNCTION_ARN. Greedy {arn+} tolerates a URL-encoded ':'.
    this.httpApi.addRoutes({ path: '/migrate', methods: [apigwv2.HttpMethod.POST], integration });
    // One batch migration (function_arns) in a single durable execution.
    this.httpApi.addRoutes({ path: '/migrate-batch', methods: [apigwv2.HttpMethod.POST], integration });
    this.httpApi.addRoutes({ path: '/migrate/{arn+}', methods: [apigwv2.HttpMethod.GET], integration });
    // Batch run progress (the three migration phases). Greedy: durable execution
    // ARNs contain '/' segments (mirror /refresh/{arn+}).
    this.httpApi.addRoutes({ path: '/migrate-run/{arn+}', methods: [apigwv2.HttpMethod.GET], integration });
    // Recent migrate batch executions (newest first) for the Steps card
    // re-attach and the Activity log; mirror of /scans.
    this.httpApi.addRoutes({ path: '/migrate-runs', methods: [apigwv2.HttpMethod.GET], integration });

    // apiEndpoint has no trailing slash
    this.apiUrl = this.httpApi.apiEndpoint;

    new cdk.CfnOutput(this, 'ApiUrl', {
      value: this.apiUrl,
      description: 'HTTP API base URL used by the frontend (VITE_API_URL)',
    });
  }
}
