import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface AuthStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
}

export class AuthStack extends cdk.Stack {
  public readonly userPoolId: string;
  public readonly userPoolArn: string;
  public readonly userPoolClientId: string;
  public readonly userPoolDomain: string;
  public readonly userPoolProviderUrl: string;

  constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, props);

    const { environment, projectName } = props;

    // -----------------------------------------------------------------------
    // Cognito User Pool — strong password policy, admin-only creation,
    // advanced security ENFORCED. Matches original CloudFormation cognito.yaml
    // -----------------------------------------------------------------------
    const userPool = new cognito.UserPool(this, 'MerchantUserPool', {
      userPoolName: `${projectName}-${environment}-merchant-pool`,

      // Strong password policy — min 12 chars, all character types
      passwordPolicy: {
        minLength: 12,
        requireUppercase: true,
        requireLowercase: true,
        requireDigits: true,
        requireSymbols: true,
        tempPasswordValidity: cdk.Duration.days(1),
      },

      // MFA disabled for demo
      mfa: cognito.Mfa.OFF,

      // Account recovery via verified email
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,

      // Auto-verify email
      autoVerify: { email: true },

      // User attributes
      standardAttributes: {
        email: { required: true, mutable: true },
        fullname: { required: true, mutable: true },
      },
      customAttributes: {
        merchant_id: new cognito.StringAttribute({ mutable: false }),
      },

      // Email configuration
      email: cognito.UserPoolEmail.withCognito(),

      // Threat protection — replaces deprecated advancedSecurityMode
      standardThreatProtectionMode: cognito.StandardThreatProtectionMode.FULL_FUNCTION,

      // Username case insensitive
      signInCaseSensitive: false,

      // Admin-only user creation
      selfSignUpEnabled: false,

      // Removal policy for demo teardown
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // -----------------------------------------------------------------------
    // User Pool Domain for Hosted UI
    // -----------------------------------------------------------------------
    const domain = userPool.addDomain('UserPoolDomain', {
      cognitoDomain: {
        domainPrefix: `${projectName}-${environment}-${cdk.Aws.ACCOUNT_ID}`,
      },
    });

    // -----------------------------------------------------------------------
    // App Client — OAuth code flow, token validity, SRP + password auth
    // -----------------------------------------------------------------------
    const appClient = userPool.addClient('MerchantPortalClient', {
      userPoolClientName: `${projectName}-${environment}-merchant-portal`,

      // Token validity
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),

      // OAuth configuration
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.PROFILE,
        ],
        callbackUrls: [
          'http://localhost:3000/callback',
          'https://localhost:3000/callback',
        ],
        logoutUrls: [
          'http://localhost:3000',
          'https://localhost:3000',
        ],
      },

      // Security settings
      generateSecret: false,
      preventUserExistenceErrors: true,

      // Auth flows
      authFlows: {
        userSrp: true,
        userPassword: true,
      },

      // Read/write attributes
      readAttributes: new cognito.ClientAttributes()
        .withStandardAttributes({ email: true, emailVerified: true, fullname: true })
        .withCustomAttributes('merchant_id'),
      writeAttributes: new cognito.ClientAttributes()
        .withStandardAttributes({ email: true, fullname: true }),

      // Supported identity providers
      supportedIdentityProviders: [
        cognito.UserPoolClientIdentityProvider.COGNITO,
      ],
    });

    // -----------------------------------------------------------------------
    // Resource Server — payment API scopes
    // -----------------------------------------------------------------------
    userPool.addResourceServer('PaymentApiResourceServer', {
      identifier: `${projectName}-payment-api`,
      userPoolResourceServerName: 'Payment API',
      scopes: [
        new cognito.ResourceServerScope({
          scopeName: 'payments.read',
          scopeDescription: 'Read payment transactions',
        }),
        new cognito.ResourceServerScope({
          scopeName: 'payments.write',
          scopeDescription: 'Create and modify payments',
        }),
        new cognito.ResourceServerScope({
          scopeName: 'webhooks.manage',
          scopeDescription: 'Manage webhook configurations',
        }),
      ],
    });

    // -----------------------------------------------------------------------
    // User Pool Groups
    // -----------------------------------------------------------------------
    new cognito.CfnUserPoolGroup(this, 'MerchantsGroup', {
      userPoolId: userPool.userPoolId,
      groupName: 'Merchants',
      description: 'Standard merchant users',
      precedence: 10,
    });

    new cognito.CfnUserPoolGroup(this, 'AdminsGroup', {
      userPoolId: userPool.userPoolId,
      groupName: 'Admins',
      description: 'Administrative users with elevated privileges',
      precedence: 1,
    });

    // -----------------------------------------------------------------------
    // CloudWatch Log Group — environment-based retention
    // 365 days for prod, 30 days otherwise
    // -----------------------------------------------------------------------
    const logRetention = environment === 'prod'
      ? logs.RetentionDays.ONE_YEAR
      : logs.RetentionDays.ONE_MONTH;

    const cognitoLogGroup = new logs.LogGroup(this, 'CognitoLogGroup', {
      logGroupName: `/aws/cognito/${projectName}-${environment}`,
      retention: logRetention,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // -----------------------------------------------------------------------
    // Expose properties for cross-stack references
    // -----------------------------------------------------------------------
    this.userPoolId = userPool.userPoolId;
    this.userPoolArn = userPool.userPoolArn;
    this.userPoolClientId = appClient.userPoolClientId;
    this.userPoolDomain = `${projectName}-${environment}-${cdk.Aws.ACCOUNT_ID}`;
    this.userPoolProviderUrl = userPool.userPoolProviderUrl;

    // -----------------------------------------------------------------------
    // CloudFormation Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'UserPoolId', {
      description: 'Cognito User Pool ID',
      value: userPool.userPoolId,
    });

    new cdk.CfnOutput(this, 'UserPoolArn', {
      description: 'Cognito User Pool ARN',
      value: userPool.userPoolArn,
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      description: 'Cognito User Pool Client ID for Merchant Portal',
      value: appClient.userPoolClientId,
    });

    new cdk.CfnOutput(this, 'UserPoolDomain', {
      description: 'Cognito User Pool Domain',
      value: `${projectName}-${environment}-${cdk.Aws.ACCOUNT_ID}`,
    });

    new cdk.CfnOutput(this, 'UserPoolDomainUrl', {
      description: 'Full URL for Cognito Hosted UI',
      value: `https://${projectName}-${environment}-${cdk.Aws.ACCOUNT_ID}.auth.${cdk.Aws.REGION}.amazoncognito.com`,
    });

    new cdk.CfnOutput(this, 'UserPoolProviderUrl', {
      description: 'Cognito User Pool Provider URL (for JWT validation)',
      value: userPool.userPoolProviderUrl,
    });

    new cdk.CfnOutput(this, 'JwksUri', {
      description: 'JWKS URI for JWT token validation',
      value: `https://cognito-idp.${cdk.Aws.REGION}.amazonaws.com/${userPool.userPoolId}/.well-known/jwks.json`,
    });

    new cdk.CfnOutput(this, 'ResourceServerId', {
      description: 'Payment API Resource Server Identifier',
      value: `${projectName}-payment-api`,
    });

    new cdk.CfnOutput(this, 'CognitoLogGroupName', {
      description: 'CloudWatch Log Group for Cognito events',
      value: cognitoLogGroup.logGroupName,
    });
  }
}
