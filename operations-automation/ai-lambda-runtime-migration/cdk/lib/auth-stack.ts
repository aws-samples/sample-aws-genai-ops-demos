import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

/**
 * Authentication Stack
 *
 * Creates:
 * 1. Cognito User Pool — admin-managed identity store for dashboard access (self-sign-up disabled)
 * 2. Cognito User Pool Client — used by the React frontend for authentication
 * 3. Cognito Identity Pool — provides AWS credentials for AUTHENTICATED dashboard users
 *
 * Architecture:
 * - Admins create users in the User Pool (no self-sign-up)
 * - Authenticated users get temporary AWS credentials via Identity Pool
 * - These credentials allow SigV4-signed requests to AgentCore
 */
export class AuthStack extends cdk.Stack {
    public readonly userPool: cognito.UserPool;
    public readonly userPoolClient: cognito.UserPoolClient;
    public readonly identityPool: cognito.CfnIdentityPool;

    constructor(scope: Construct, id: string, props?: cdk.StackProps) {
        super(scope, id, props);

        // Cognito User Pool — admin-managed users for dashboard access
        this.userPool = new cognito.UserPool(this, 'DashboardUserPool', {
            userPoolName: 'lambda-migration-dashboard-users',
            selfSignUpEnabled: false, // Admin creates users
            signInAliases: {
                email: true,
            },
            autoVerify: {
                email: true,
            },
            standardAttributes: {
                email: {
                    required: true,
                    mutable: false,
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

        // User Pool Client — used by the React frontend for authentication
        this.userPoolClient = new cognito.UserPoolClient(this, 'DashboardUserPoolClient', {
            userPool: this.userPool,
            userPoolClientName: 'lambda-migration-dashboard-client',
            authFlows: {
                userPassword: true,
                userSrp: true,
            },
            generateSecret: false, // Public client for browser-based auth
            preventUserExistenceErrors: true,
        });

        // Cognito Identity Pool — provides AWS credentials for AUTHENTICATED users
        this.identityPool = new cognito.CfnIdentityPool(this, 'DashboardIdentityPool', {
            identityPoolName: 'lambda-migration-dashboard-identity-pool',
            allowUnauthenticatedIdentities: false, // Authenticated access only
            cognitoIdentityProviders: [
                {
                    clientId: this.userPoolClient.userPoolClientId,
                    providerName: this.userPool.userPoolProviderName,
                },
            ],
        });

        // IAM Role for AUTHENTICATED users (dashboard access)
        const authenticatedRole = new iam.Role(this, 'AuthenticatedRole', {
            assumedBy: new iam.WebIdentityPrincipal('cognito-identity.amazonaws.com', {
                'StringEquals': {
                    'cognito-identity.amazonaws.com:aud': this.identityPool.ref,
                },
                'ForAnyValue:StringLike': {
                    'cognito-identity.amazonaws.com:amr': 'authenticated',
                },
            }),
            inlinePolicies: {
                BedrockAgentCoreAccess: new iam.PolicyDocument({
                    statements: [
                        new iam.PolicyStatement({
                            effect: iam.Effect.ALLOW,
                            actions: [
                                'bedrock-agentcore:InvokeAgentRuntime',
                            ],
                            resources: ['*'],
                        }),
                    ],
                }),
            },
        });

        // Attach authenticated role to identity pool
        new cognito.CfnIdentityPoolRoleAttachment(this, 'IdentityPoolRoleAttachment', {
            identityPoolId: this.identityPool.ref,
            roles: {
                authenticated: authenticatedRole.roleArn,
            },
        });

        // Stack outputs
        new cdk.CfnOutput(this, 'UserPoolId', {
            value: this.userPool.userPoolId,
            description: 'Cognito User Pool ID',
        });

        new cdk.CfnOutput(this, 'UserPoolClientId', {
            value: this.userPoolClient.userPoolClientId,
            description: 'Cognito User Pool Client ID',
        });

        new cdk.CfnOutput(this, 'IdentityPoolId', {
            value: this.identityPool.ref,
            description: 'Cognito Identity Pool ID (for authenticated AWS credentials)',
        });
    }
}
