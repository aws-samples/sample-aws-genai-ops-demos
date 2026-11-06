import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';

/**
 * Authentication Stack
 *
 * Creates:
 * 1. Cognito User Pool — admin-managed identity store for dashboard access (self-sign-up disabled)
 * 2. Cognito User Pool Client — used by the React frontend for authentication
 *
 * Architecture:
 * - Admins create users in the User Pool (no self-sign-up)
 * - The frontend authenticates against the User Pool Client and sends the
 *   Cognito JWT (id token) to the HTTP API, which validates it with a
 *   JWT authorizer (see ApiStack). No Identity Pool, no SigV4 credentials.
 */
export class AuthStack extends cdk.Stack {
    public readonly userPool: cognito.UserPool;
    public readonly userPoolClient: cognito.UserPoolClient;

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

        // Stack outputs
        new cdk.CfnOutput(this, 'UserPoolId', {
            value: this.userPool.userPoolId,
            description: 'Cognito User Pool ID',
        });

        new cdk.CfnOutput(this, 'UserPoolClientId', {
            value: this.userPoolClient.userPoolClientId,
            description: 'Cognito User Pool Client ID',
        });
    }
}
