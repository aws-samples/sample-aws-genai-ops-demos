import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface ComputeStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  privateComputeSubnets: ec2.ISubnet[];
  eksSecurityGroup: ec2.ISecurityGroup;
  nodeInstanceType: string;
  nodeArchitecture: string; // 'arm64' | 'amd64'
  nodeDesiredCapacity: number;
}

export class ComputeStack extends cdk.Stack {
  public readonly clusterName: string;
  public readonly clusterEndpoint: string;
  public readonly oidcProviderArn: string;
  public readonly oidcProviderUrl: string;
  public readonly paymentProcessorRoleArn: string;
  public readonly merchantGatewayRoleArn: string;
  public readonly webhookServiceRoleArn: string;
  public readonly fluentBitRoleArn: string;

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id, props);

    const {
      environment,
      projectName,
      privateComputeSubnets,
      eksSecurityGroup,
      nodeInstanceType,
      nodeArchitecture,
      nodeDesiredCapacity,
    } = props;

    const isArm = nodeArchitecture === 'arm64';

    // -----------------------------------------------------------------------
    // EKS Cluster IAM Role
    // -----------------------------------------------------------------------
    const eksClusterRole = new iam.Role(this, 'EksClusterRole', {
      roleName: `${projectName}-${environment}-eks-cluster-role`,
      assumedBy: new iam.ServicePrincipal('eks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKSClusterPolicy'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKSVPCResourceController'),
      ],
    });

    // -----------------------------------------------------------------------
    // EKS Node Group IAM Role
    // -----------------------------------------------------------------------
    const eksNodeRole = new iam.Role(this, 'EksNodeRole', {
      roleName: `${projectName}-${environment}-eks-node-role`,
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKSWorkerNodePolicy'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKS_CNI_Policy'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEC2ContainerRegistryReadOnly'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });

    // -----------------------------------------------------------------------
    // EKS Cluster — L1 CfnCluster to avoid L2 side effects
    // (K8s 1.33, public+private endpoint, specified security group)
    // -----------------------------------------------------------------------
    const cluster = new eks.CfnCluster(this, 'EksCluster', {
      name: `${projectName}-${environment}-cluster`,
      version: '1.33',
      roleArn: eksClusterRole.roleArn,
      accessConfig: {
        authenticationMode: 'API_AND_CONFIG_MAP',
      },
      resourcesVpcConfig: {
        subnetIds: privateComputeSubnets.map(s => s.subnetId),
        securityGroupIds: [eksSecurityGroup.securityGroupId],
        endpointPrivateAccess: true,
        endpointPublicAccess: true,
      },
      tags: [
        { key: 'Name', value: `${projectName}-${environment}-cluster` },
        { key: 'Environment', value: environment },
        { key: 'Project', value: projectName },
      ],
    });

    this.clusterName = cluster.ref;
    this.clusterEndpoint = cluster.attrEndpoint;

    // -----------------------------------------------------------------------
    // EKS Managed Node Group — L1 CfnNodegroup
    // -----------------------------------------------------------------------
    const nodeGroup = new eks.CfnNodegroup(this, 'EksNodeGroup', {
      clusterName: cluster.ref,
      nodegroupName: `${projectName}-${environment}-${isArm ? 'arm' : 'amd'}-node-group`,
      nodeRole: eksNodeRole.roleArn,
      subnets: privateComputeSubnets.map(s => s.subnetId),
      scalingConfig: {
        minSize: 1,
        maxSize: 10,
        desiredSize: nodeDesiredCapacity,
      },
      instanceTypes: [nodeInstanceType],
      amiType: isArm ? 'AL2023_ARM_64_STANDARD' : 'AL2023_x86_64_STANDARD',
      capacityType: 'ON_DEMAND',
      diskSize: 50,
      labels: {
        environment,
        project: projectName,
      },
      tags: {
        Name: `${projectName}-${environment}-node-group`,
        Environment: environment,
        Project: projectName,
      },
    });

    // -----------------------------------------------------------------------
    // OIDC Provider for IRSA — L1 CfnOIDCProvider
    // -----------------------------------------------------------------------
    const oidcProvider = new iam.CfnOIDCProvider(this, 'OIDCProvider', {
      url: cluster.attrOpenIdConnectIssuerUrl,
      clientIdList: ['sts.amazonaws.com'],
      thumbprintList: ['9e99a48a9960b14926bb7f3b02e22da2b0ab7280'],
      tags: [
        { key: 'Name', value: `${projectName}-${environment}-eks-oidc-provider` },
        { key: 'Environment', value: environment },
        { key: 'Project', value: projectName },
      ],
    });

    this.oidcProviderArn = oidcProvider.attrArn;
    // Strip https:// prefix for IRSA trust policy conditions
    this.oidcProviderUrl = cdk.Fn.select(1, cdk.Fn.split('https://', cluster.attrOpenIdConnectIssuerUrl));

    // -----------------------------------------------------------------------
    // Helper: create an IRSA role with federated trust policy.
    // The OIDC URL is a deploy-time token (Fn::GetAtt), so we use CfnJson
    // to defer condition key resolution to CloudFormation deploy time.
    // -----------------------------------------------------------------------
    const createIrsaRole = (
      constructId: string,
      roleName: string,
      serviceAccountNamespace: string,
      serviceAccountName: string,
      policyName: string,
      policyStatements: iam.PolicyStatement[],
    ): iam.Role => {
      const audCondition = new cdk.CfnJson(this, `${constructId}AudCondition`, {
        value: {
          [`${this.oidcProviderUrl}:aud`]: 'sts.amazonaws.com',
        },
      });

      const subCondition = new cdk.CfnJson(this, `${constructId}SubCondition`, {
        value: {
          [`${this.oidcProviderUrl}:sub`]: `system:serviceaccount:${serviceAccountNamespace}:${serviceAccountName}`,
        },
      });

      const role = new iam.Role(this, constructId, {
        roleName,
        assumedBy: new iam.FederatedPrincipal(
          oidcProvider.attrArn,
          {
            StringEquals: audCondition,
            StringLike: subCondition,
          },
          'sts:AssumeRoleWithWebIdentity',
        ),
        inlinePolicies: {
          [policyName]: new iam.PolicyDocument({ statements: policyStatements }),
        },
      });
      return role;
    };

    // -----------------------------------------------------------------------
    // Payment Processor IRSA Role
    // -----------------------------------------------------------------------
    const paymentProcessorRole = createIrsaRole(
      'PaymentProcessorRole',
      `${projectName}-${environment}-payment-processor-role`,
      'payment-demo',
      'payment-processor-sa',
      'PaymentProcessorPolicy',
      [
        new iam.PolicyStatement({
          sid: 'SecretsManagerAccess',
          actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'],
          resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:${projectName}-${environment}-*`],
        }),
        new iam.PolicyStatement({
          sid: 'KMSDecrypt',
          actions: ['kms:Decrypt', 'kms:DescribeKey'],
          resources: [`arn:aws:kms:${this.region}:${this.account}:key/*`],
          conditions: {
            StringEquals: { 'kms:ViaService': `secretsmanager.${this.region}.amazonaws.com` },
          },
        }),
        new iam.PolicyStatement({
          sid: 'SQSAccess',
          actions: ['sqs:SendMessage', 'sqs:GetQueueUrl', 'sqs:GetQueueAttributes'],
          resources: [`arn:aws:sqs:${this.region}:${this.account}:${projectName}-${environment}-*`],
        }),
        new iam.PolicyStatement({
          sid: 'CloudWatchLogsAccess',
          actions: ['logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
          resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/eks/${projectName}-${environment}-*:*`],
        }),
      ],
    );
    this.paymentProcessorRoleArn = paymentProcessorRole.roleArn;

    // -----------------------------------------------------------------------
    // Merchant Gateway IRSA Role
    // -----------------------------------------------------------------------
    const merchantGatewayRole = createIrsaRole(
      'MerchantGatewayRole',
      `${projectName}-${environment}-merchant-gateway-role`,
      'payment-demo',
      'merchant-gateway-sa',
      'MerchantGatewayPolicy',
      [
        new iam.PolicyStatement({
          sid: 'CloudWatchLogsAccess',
          actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
          resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/eks/${projectName}-${environment}-*:*`],
        }),
      ],
    );
    this.merchantGatewayRoleArn = merchantGatewayRole.roleArn;

    // -----------------------------------------------------------------------
    // Webhook Service IRSA Role
    // -----------------------------------------------------------------------
    const webhookServiceRole = createIrsaRole(
      'WebhookServiceRole',
      `${projectName}-${environment}-webhook-service-role`,
      'payment-demo',
      'webhook-service-sa',
      'WebhookServicePolicy',
      [
        new iam.PolicyStatement({
          sid: 'SQSAccess',
          actions: ['sqs:ReceiveMessage', 'sqs:DeleteMessage', 'sqs:GetQueueUrl', 'sqs:GetQueueAttributes', 'sqs:ChangeMessageVisibility'],
          resources: [`arn:aws:sqs:${this.region}:${this.account}:${projectName}-${environment}-*`],
        }),
        new iam.PolicyStatement({
          sid: 'SecretsManagerAccess',
          actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'],
          resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:${projectName}-${environment}-*`],
        }),
        new iam.PolicyStatement({
          sid: 'KMSDecrypt',
          actions: ['kms:Decrypt', 'kms:DescribeKey'],
          resources: [`arn:aws:kms:${this.region}:${this.account}:key/*`],
          conditions: {
            StringEquals: { 'kms:ViaService': `secretsmanager.${this.region}.amazonaws.com` },
          },
        }),
        new iam.PolicyStatement({
          sid: 'CloudWatchLogsAccess',
          actions: ['logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
          resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/eks/${projectName}-${environment}-*:*`],
        }),
      ],
    );
    this.webhookServiceRoleArn = webhookServiceRole.roleArn;

    // -----------------------------------------------------------------------
    // Fluent Bit IRSA Role
    // -----------------------------------------------------------------------
    const fluentBitRole = createIrsaRole(
      'FluentBitRole',
      `${projectName}-${environment}-fluent-bit-role`,
      'kube-system',
      'fluent-bit-sa',
      'FluentBitCloudWatchPolicy',
      [
        new iam.PolicyStatement({
          sid: 'CloudWatchLogsWrite',
          actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams', 'logs:DescribeLogGroups'],
          resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/eks/${projectName}-${environment}/*:*`],
        }),
      ],
    );
    this.fluentBitRoleArn = fluentBitRole.roleArn;

    // -----------------------------------------------------------------------
    // CloudFormation Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'EksClusterName', {
      description: 'EKS Cluster Name',
      value: cluster.ref,
    });

    new cdk.CfnOutput(this, 'EksClusterEndpoint', {
      description: 'EKS Cluster API Endpoint',
      value: cluster.attrEndpoint,
    });

    new cdk.CfnOutput(this, 'OIDCProviderArn', {
      description: 'OIDC Provider ARN for IRSA',
      value: oidcProvider.attrArn,
    });

    new cdk.CfnOutput(this, 'PaymentProcessorRoleArn', {
      description: 'IAM Role ARN for Payment Processor Service Account',
      value: paymentProcessorRole.roleArn,
    });

    new cdk.CfnOutput(this, 'MerchantGatewayRoleArn', {
      description: 'IAM Role ARN for Merchant Gateway Service Account',
      value: merchantGatewayRole.roleArn,
    });

    new cdk.CfnOutput(this, 'WebhookServiceRoleArn', {
      description: 'IAM Role ARN for Webhook Service Account',
      value: webhookServiceRole.roleArn,
    });

    new cdk.CfnOutput(this, 'FluentBitRoleArn', {
      description: 'IAM Role ARN for Fluent Bit Service Account',
      value: fluentBitRole.roleArn,
    });
  }
}
