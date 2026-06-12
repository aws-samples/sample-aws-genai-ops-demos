import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

/**
 * Auth Stack
 *
 * Admin-managed Cognito User Pool plus Identity Pool producing temporary AWS
 * credentials for authenticated users. The browser uses those credentials to
 * SigV4-sign calls to the dashboard-api Lambda Function URL (IAM auth).
 *
 * The authenticated IAM role only grants `lambda:InvokeFunctionUrl`. All the
 * DynamoDB / S3 / ECS access is done server-side by the Lambda role (ApiStack),
 * so the browser never talks to those services directly.
 */
export class AuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly identityPool: cognito.CfnIdentityPool;
  public readonly authenticatedRoleArn: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.userPool = new cognito.UserPool(this, 'DashboardUserPool', {
      userPoolName: 'prowler-security-dashboard-users',
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: {
        email: { required: true, mutable: false },
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

    this.userPoolClient = new cognito.UserPoolClient(this, 'DashboardUserPoolClient', {
      userPool: this.userPool,
      userPoolClientName: 'prowler-security-dashboard-client',
      authFlows: { userPassword: true, userSrp: true },
      generateSecret: false,
      preventUserExistenceErrors: true,
    });

    this.identityPool = new cognito.CfnIdentityPool(this, 'DashboardIdentityPool', {
      identityPoolName: 'prowler-security-dashboard-identity-pool',
      allowUnauthenticatedIdentities: false,
      cognitoIdentityProviders: [
        {
          clientId: this.userPoolClient.userPoolClientId,
          providerName: this.userPool.userPoolProviderName,
        },
      ],
    });

    const authenticatedRole = new iam.Role(this, 'AuthenticatedRole', {
      roleName: `prowler-security-dashboard-authenticated-${cdk.Aws.REGION}`,
      assumedBy: new iam.WebIdentityPrincipal('cognito-identity.amazonaws.com', {
        'StringEquals': {
          'cognito-identity.amazonaws.com:aud': this.identityPool.ref,
        },
        'ForAnyValue:StringLike': {
          'cognito-identity.amazonaws.com:amr': 'authenticated',
        },
      }),
      inlinePolicies: {
        InvokeDashboardApi: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['lambda:InvokeFunctionUrl'],
              // Narrowed down later by the Lambda Function URL resource policy;
              // we allow * here because the function name depends on ApiStack,
              // which depends on this role ARN — avoids a circular ref.
              resources: ['*'],
            }),
          ],
        }),
      },
    });
    this.authenticatedRoleArn = authenticatedRole.roleArn;

    new cognito.CfnIdentityPoolRoleAttachment(this, 'IdentityPoolRoleAttachment', {
      identityPoolId: this.identityPool.ref,
      roles: { authenticated: authenticatedRole.roleArn },
    });

    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId });
    new cdk.CfnOutput(this, 'IdentityPoolId', { value: this.identityPool.ref });
    new cdk.CfnOutput(this, 'AuthenticatedRoleArn', { value: this.authenticatedRoleArn });
  }
}
