import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { KubectlV31Layer } from '@aws-cdk/lambda-layer-kubectl-v31';
import { Construct } from 'constructs';
import * as path from 'path';

export interface FailureSimulatorApiStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  vpc: ec2.Vpc;
  privateComputeSubnets: ec2.ISubnet[];
  eksSecurityGroup: ec2.ISecurityGroup;
  eksClusterName: string;
  alarmName: string;
  /** DevOps Agent region (cross-stack from DevOpsAgentStack or context fallback) */
  devOpsAgentRegion: string;
  /** DevOps Agent Space ID (cross-stack reference from DevOpsAgentStack) */
  devOpsAgentSpaceId: string;
}

export class FailureSimulatorApiStack extends cdk.Stack {
  public readonly apiEndpoint: string;
  public readonly apiId: string;
  public readonly apiStageName: string;

  constructor(scope: Construct, id: string, props: FailureSimulatorApiStackProps) {
    super(scope, id, props);

    const {
      environment,
      projectName,
      vpc,
      privateComputeSubnets,
      eksSecurityGroup,
      eksClusterName,
      alarmName,
      devOpsAgentRegion,
      devOpsAgentSpaceId,
    } = props;

    // -----------------------------------------------------------------------
    // Security Group — needs to reach EKS API + internet
    // -----------------------------------------------------------------------
    const lambdaSg = new ec2.SecurityGroup(this, 'FailureSimulatorLambdaSg', {
      vpc,
      securityGroupName: `${projectName}-${environment}-failure-simulator-sg`,
      description: 'Security group for Failure Simulator API Lambda',
      allowAllOutbound: true,
    });

    new ec2.CfnSecurityGroupIngress(this, 'EksIngressFromFailureSimulator', {
      groupId: eksSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 443,
      toPort: 443,
      sourceSecurityGroupId: lambdaSg.securityGroupId,
      description: 'Allow Failure Simulator Lambda to reach EKS API server',
    });

    // -----------------------------------------------------------------------
    // IAM Role
    // -----------------------------------------------------------------------
    const lambdaRole = new iam.Role(this, 'FailureSimulatorLambdaRole', {
      roleName: `${projectName}-${environment}-failure-simulator-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole'),
      ],
    });

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'EksDescribeCluster',
      actions: ['eks:DescribeCluster'],
      resources: [`arn:aws:eks:${this.region}:${this.account}:cluster/${eksClusterName}`],
    }));

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'StsGetCallerIdentity',
      actions: ['sts:GetCallerIdentity'],
      resources: ['*'],
    }));

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchDescribeAlarms',
      actions: ['cloudwatch:DescribeAlarms', 'cloudwatch:PutMetricData'],
      resources: ['*'],
    }));

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DevOpsAgentUsage',
      actions: ['aidevops:GetAccountUsage', 'aidevops:ListBacklogTasks', 'aidevops:ListExecutions', 'aidevops:ListJournalRecords'],
      resources: ['*'],
    }));

    // -----------------------------------------------------------------------
    // DynamoDB — persistent state for scenario timers (auto-revert)
    // -----------------------------------------------------------------------
    const stateTable = new dynamodb.Table(this, 'SimulatorStateTable', {
      tableName: `${projectName}-${environment}-simulator-settings`,
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // -----------------------------------------------------------------------
    // kubectl Lambda Layer
    // -----------------------------------------------------------------------
    const kubectlLayer = new KubectlV31Layer(this, 'KubectlLayer');

    // -----------------------------------------------------------------------
    // Lambda Function
    // -----------------------------------------------------------------------
    const simulatorLambda = new lambda.Function(this, 'FailureSimulatorLambda', {
      functionName: `${projectName}-${environment}-failure-simulator`,
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'failure-simulator-api')),
      layers: [kubectlLayer],
      role: lambdaRole,
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      vpc,
      vpcSubnets: { subnets: privateComputeSubnets },
      securityGroups: [lambdaSg],
      environment: {
        EKS_CLUSTER_NAME: eksClusterName,
        K8S_NAMESPACE: 'payment-demo',
        DEPLOYMENT_NAME: 'payment-processor',
        ALARM_NAME: alarmName,
        DNS_ALARM_NAME: `${projectName}-${environment}-dns-resolution-errors`,
        METRICS_NAMESPACE: `${projectName}/${environment}`,
        STATE_TABLE_NAME: stateTable.tableName,
        AWS_REGION_NAME: cdk.Aws.REGION,
        DEVOPS_AGENT_REGION: devOpsAgentRegion,
        DEVOPS_AGENT_SPACE_ID: devOpsAgentSpaceId,
      },
    });

    // DynamoDB permissions for the Lambda
    stateTable.grantReadWriteData(lambdaRole);

    // -----------------------------------------------------------------------
    // API Gateway
    // -----------------------------------------------------------------------
    const api = new apigateway.RestApi(this, 'FailureSimulatorApi', {
      restApiName: `${projectName}-${environment}-failure-simulator-api`,
      description: 'Failure Simulator API for DevOps Agent EKS Demo',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
      deployOptions: {
        stageName: 'prod',
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        metricsEnabled: true,
      },
    });

    const adminResource = api.root.addResource('admin');
    const integration = new apigateway.LambdaIntegration(simulatorLambda);

    // Legacy routes (backward compatible)
    const injectResource = adminResource.addResource('inject');
    injectResource.addMethod('POST', integration);
    injectResource.addMethod('DELETE', integration);

    const statusResource = adminResource.addResource('status');
    statusResource.addMethod('GET', integration);

    // Usage route: /admin/usage
    const usageResource = adminResource.addResource('usage');
    usageResource.addMethod('GET', integration);

    // Logs route: /admin/logs
    const logsResource = adminResource.addResource('logs');
    logsResource.addMethod('GET', integration);

    // Scenario-based routes: /admin/scenarios/{id}/inject
    const scenariosResource = adminResource.addResource('scenarios');
    for (const scenarioId of ['db-connection-failure', 'dns-resolution-failure']) {
      const scenarioResource = scenariosResource.addResource(scenarioId);
      const scenarioInjectResource = scenarioResource.addResource('inject');
      scenarioInjectResource.addMethod('POST', integration);
      scenarioInjectResource.addMethod('DELETE', integration);
    }

    this.apiEndpoint = api.url;
    this.apiId = api.restApiId;
    this.apiStageName = 'prod';

    // -----------------------------------------------------------------------
    // Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'FailureSimulatorApiEndpoint', {
      description: 'Failure Simulator API Gateway endpoint URL',
      value: api.url,
    });

    new cdk.CfnOutput(this, 'FailureSimulatorLambdaRoleArn', {
      description: 'Failure Simulator Lambda IAM Role ARN (add to EKS access entries)',
      value: lambdaRole.roleArn,
    });
  }
}
