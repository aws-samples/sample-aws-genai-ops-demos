import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface AuthStackProps extends cdk.StackProps {
  agentRuntimeArn?: string;
}

export class AuthStack extends cdk.Stack {
    public readonly userPool: cognito.UserPool;
    public readonly userPoolClient: cognito.UserPoolClient;
    public readonly identityPool: cognito.CfnIdentityPool;
    public readonly authenticatedRole: iam.Role;

    constructor(scope: Construct, id: string, props: AuthStackProps) {
        super(scope, id, props);

        // Cognito User Pool - ADMIN ONLY (no self-signup)
        this.userPool = new cognito.UserPool(this, 'LifecycleTrackerUserPool', {
            userPoolName: 'aws-services-lifecycle-tracker-admin-users',
            selfSignUpEnabled: false, // DISABLED - Admin only
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
            removalPolicy: cdk.RemovalPolicy.DESTROY, // For dev - change to RETAIN for prod
        });

        // User Pool Client for frontend JWT authentication
        this.userPoolClient = new cognito.UserPoolClient(this, 'LifecycleTrackerUserPoolClient', {
            userPool: this.userPool,
            userPoolClientName: 'aws-services-lifecycle-tracker-web-client',
            authFlows: {
                userPassword: true,
                userSrp: true,
            },
            generateSecret: false, // Public client (frontend)
            preventUserExistenceErrors: true,
        });

        // Cognito Identity Pool for AWS credentials
        this.identityPool = new cognito.CfnIdentityPool(this, 'LifecycleTrackerIdentityPool', {
            identityPoolName: 'aws-services-lifecycle-tracker-identity-pool',
            allowUnauthenticatedIdentities: false, // Require authentication
            cognitoIdentityProviders: [{
                clientId: this.userPoolClient.userPoolClientId,
                providerName: this.userPool.userPoolProviderName,
                serverSideTokenCheck: true, // Enable server-side token validation
            }],
        });

        // IAM Role for authenticated users (admin access to AgentCore)
        this.authenticatedRole = new iam.Role(this, 'AuthenticatedRole', {
            assumedBy: new iam.WebIdentityPrincipal('cognito-identity.amazonaws.com', {
                'StringEquals': {
                    'cognito-identity.amazonaws.com:aud': this.identityPool.ref,
                },
                'ForAnyValue:StringLike': {
                    'cognito-identity.amazonaws.com:amr': 'authenticated',
                },
            }),
            // Removed overly broad BedrockAgentCoreFullAccess policy
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
                            resources: ['*'], // Consider restricting to specific agent ARNs if known
                        }),
                    ],
                }),
            },
        });

        // Attach role to identity pool (simplified without role mappings)
        new cognito.CfnIdentityPoolRoleAttachment(this, 'IdentityPoolRoleAttachment', {
            identityPoolId: this.identityPool.ref,
            roles: {
                authenticated: this.authenticatedRole.roleArn,
            },
        });

        // Note: Admin user will be created manually after deployment
        // Use AWS CLI: aws cognito-idp admin-create-user --user-pool-id <POOL_ID> --username admin --user-attributes Name=email,Value=admin@company.com Name=email_verified,Value=true --message-action SUPPRESS



        // Outputs
        new cdk.CfnOutput(this, 'UserPoolId', {
            value: this.userPool.userPoolId,
            description: 'Cognito User Pool ID',
            exportName: 'AWSServicesLifecycleTrackerUserPoolId',
        });

        new cdk.CfnOutput(this, 'UserPoolArn', {
            value: this.userPool.userPoolArn,
            description: 'Cognito User Pool ARN',
            exportName: 'AWSServicesLifecycleTrackerUserPoolArn',
        });

        new cdk.CfnOutput(this, 'UserPoolClientId', {
            value: this.userPoolClient.userPoolClientId,
            description: 'Cognito User Pool Client ID',
            exportName: 'AWSServicesLifecycleTrackerUserPoolClientId',
        });

        new cdk.CfnOutput(this, 'IdentityPoolId', {
            value: this.identityPool.ref,
            description: 'Cognito Identity Pool ID',
            exportName: 'AWSServicesLifecycleTrackerIdentityPoolId',
        });

        new cdk.CfnOutput(this, 'AdminUsername', {
            value: 'admin',
            description: 'Admin username (password must be set manually)',
        });

        new cdk.CfnOutput(this, 'AdminEmail', {
            value: 'admin@company.com',
            description: 'Admin email (change this in the code before deployment)',
        });
    }
}
