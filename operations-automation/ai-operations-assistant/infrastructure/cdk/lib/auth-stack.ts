import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

/**
 * G.O.A.T. AuthStack — Cognito User Pool, Identity Pool, and authenticated IAM role.
 * Follows the lifecycle tracker auth-stack.ts pattern.
 */
export class AuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly identityPool: cognito.CfnIdentityPool;
  public readonly authenticatedRole: iam.Role;
  public readonly networkCaptureGroup: cognito.CfnUserPoolGroup;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Cognito User Pool — ADMIN ONLY (no self-signup)
    this.userPool = new cognito.UserPool(this, 'GOATUserPool', {
      userPoolName: 'goat-admin-users',
      selfSignUpEnabled: false,
      signInAliases: {
        username: true,
        email: true,
      },
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

    // User Pool Client — public client for frontend JWT auth
    this.userPoolClient = new cognito.UserPoolClient(this, 'GOATUserPoolClient', {
      userPool: this.userPool,
      userPoolClientName: 'goat-web-client',
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      generateSecret: false,
      preventUserExistenceErrors: true,
    });

    // Cognito Identity Pool — no unauthenticated access
    this.identityPool = new cognito.CfnIdentityPool(this, 'GOATIdentityPool', {
      identityPoolName: 'goat-identity-pool',
      allowUnauthenticatedIdentities: false,
      cognitoIdentityProviders: [{
        clientId: this.userPoolClient.userPoolClientId,
        providerName: this.userPool.userPoolProviderName,
        serverSideTokenCheck: true,
      }],
    });

    // IAM Role for authenticated users
    this.authenticatedRole = new iam.Role(this, 'AuthenticatedRole', {
      assumedBy: new iam.WebIdentityPrincipal('cognito-identity.amazonaws.com', {
        'StringEquals': {
          'cognito-identity.amazonaws.com:aud': this.identityPool.ref,
        },
        'ForAnyValue:StringLike': {
          'cognito-identity.amazonaws.com:amr': 'authenticated',
        },
      }),
      inlinePolicies: {
        CognitoIdentityAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['cognito-identity:GetCredentialsForIdentity'],
              resources: ['*'],
            }),
          ],
        }),
        BedrockAgentCoreAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock-agentcore:InvokeAgentRuntime',
                'bedrock-agentcore:InvokeAgentRuntimeForUser',
              ],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    // Attach role to identity pool
    new cognito.CfnIdentityPoolRoleAttachment(this, 'IdentityPoolRoleAttachment', {
      identityPoolId: this.identityPool.ref,
      roles: {
        authenticated: this.authenticatedRole.roleArn,
      },
    });

    // -----------------------------------------------------------------------
    // Capture Authorization Group (Req 8.7, 9.16)
    //
    // Members of this group are authorized to invoke capture lifecycle
    // actions (start_capture, stop_capture, transform_capture) through the
    // chatbot. Non-members can still use read-only pcap query actions and
    // ENI inventory. The Orchestration Agent checks group membership
    // server-side, and the frontend disables capture templates client-side
    // for non-members.
    // -----------------------------------------------------------------------
    this.networkCaptureGroup = new cognito.CfnUserPoolGroup(this, 'NetworkCaptureGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: 'GOATNetworkCaptureUsers',
      description: 'Members can start, stop, and transform network packet captures via the G.O.A.T. chatbot',
    });

    // -----------------------------------------------------------------------
    // Stack Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'UserPoolId', {
      value: this.userPool.userPoolId,
      description: 'Cognito User Pool ID',
      exportName: 'GOATUserPoolId',
    });

    new cdk.CfnOutput(this, 'UserPoolArn', {
      value: this.userPool.userPoolArn,
      description: 'Cognito User Pool ARN',
      exportName: 'GOATUserPoolArn',
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: this.userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
      exportName: 'GOATUserPoolClientId',
    });

    new cdk.CfnOutput(this, 'IdentityPoolId', {
      value: this.identityPool.ref,
      description: 'Cognito Identity Pool ID',
      exportName: 'GOATIdentityPoolId',
    });

    new cdk.CfnOutput(this, 'NetworkCaptureGroupName', {
      value: 'GOATNetworkCaptureUsers',
      description: 'Cognito group for network capture authorization',
      exportName: 'GOATNetworkCaptureGroupName',
    });
  }
}
