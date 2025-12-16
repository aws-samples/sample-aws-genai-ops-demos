import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

/**
 * Authentication Stack
 * 
 * Creates:
 * 1. Cognito User Pool - the identity store for password resets (users reset passwords here)
 * 2. Cognito Identity Pool - provides AWS credentials for UNAUTHENTICATED access to AgentCore
 * 
 * Architecture:
 * - Anonymous users get temporary AWS credentials via Identity Pool (unauthenticated role)
 * - These credentials allow SigV4-signed requests to AgentCore
 * - The User Pool is the TARGET of password resets, not the auth mechanism for the chatbot
 */
export class AuthStack extends cdk.Stack {
    public readonly userPool: cognito.UserPool;
    public readonly userPoolClient: cognito.UserPoolClient;
    public readonly identityPool: cognito.CfnIdentityPool;
    public readonly unauthenticatedRole: iam.Role;

    constructor(scope: Construct, id: string, props?: cdk.StackProps) {
        super(scope, id, props);

        // Cognito User Pool - the identity store for password resets
        this.userPool = new cognito.UserPool(this, 'PasswordResetUserPool', {
            userPoolName: 'password-reset-demo-users',
            selfSignUpEnabled: true,
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
            // Email-only recovery (used by ForgotPassword flow)
            accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        // User Pool Client - used by the agent to call ForgotPassword/ConfirmForgotPassword
        this.userPoolClient = new cognito.UserPoolClient(this, 'PasswordResetUserPoolClient', {
            userPool: this.userPool,
            userPoolClientName: 'password-reset-agent-client',
            authFlows: {
                userPassword: true,
                userSrp: true,
            },
            generateSecret: false, // Public client
            preventUserExistenceErrors: true, // Security: don't reveal if user exists
        });

        // Cognito Identity Pool - provides AWS credentials for ANONYMOUS access
        // This enables unauthenticated users to call AgentCore with SigV4 signing
        this.identityPool = new cognito.CfnIdentityPool(this, 'PasswordResetIdentityPool', {
            identityPoolName: 'password-reset-chatbot-identity-pool',
            allowUnauthenticatedIdentities: true, // CRITICAL: Allow anonymous access
            allowClassicFlow: true, // Use basic auth flow - avoids session policy restrictions
            // No cognitoIdentityProviders - we don't require User Pool login for chatbot access
        });

        // IAM Role for UNAUTHENTICATED users (anonymous chatbot access)
        this.unauthenticatedRole = new iam.Role(this, 'UnauthenticatedRole', {
            assumedBy: new iam.WebIdentityPrincipal('cognito-identity.amazonaws.com', {
                'StringEquals': {
                    'cognito-identity.amazonaws.com:aud': this.identityPool.ref,
                },
                'ForAnyValue:StringLike': {
                    'cognito-identity.amazonaws.com:amr': 'unauthenticated',
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
                            resources: ['*'], // Will be restricted to specific agent in production
                        }),
                    ],
                }),
            },
        });

        // Attach unauthenticated role to identity pool
        new cognito.CfnIdentityPoolRoleAttachment(this, 'IdentityPoolRoleAttachment', {
            identityPoolId: this.identityPool.ref,
            roles: {
                unauthenticated: this.unauthenticatedRole.roleArn,
            },
        });

        // Outputs
        new cdk.CfnOutput(this, 'UserPoolId', {
            value: this.userPool.userPoolId,
            description: 'Cognito User Pool ID',
            exportName: 'PasswordResetUserPoolId',
        });

        new cdk.CfnOutput(this, 'UserPoolArn', {
            value: this.userPool.userPoolArn,
            description: 'Cognito User Pool ARN',
            exportName: 'PasswordResetUserPoolArn',
        });

        new cdk.CfnOutput(this, 'UserPoolClientId', {
            value: this.userPoolClient.userPoolClientId,
            description: 'Cognito User Pool Client ID',
            exportName: 'PasswordResetUserPoolClientId',
        });

        new cdk.CfnOutput(this, 'IdentityPoolId', {
            value: this.identityPool.ref,
            description: 'Cognito Identity Pool ID (for anonymous AWS credentials)',
            exportName: 'PasswordResetIdentityPoolId',
        });

        new cdk.CfnOutput(this, 'UnauthenticatedRoleArn', {
            value: this.unauthenticatedRole.roleArn,
            description: 'IAM Role ARN for unauthenticated users',
            exportName: 'PasswordResetUnauthenticatedRoleArn',
        });
    }
}
