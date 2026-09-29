import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import { KubectlV36Layer } from '@aws-cdk/lambda-layer-kubectl-v36';
import { Construct } from 'constructs';
import * as path from 'path';
import { LabEngine } from '../../../../shared/lab/cdk/lab-engine';

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

/**
 * The EKS demo's Lab backend: the shared LabEngine (durable engine + API Lambda, one
 * bundle, one role) fed by this demo's ../../lab folder (scenarios.yaml, handlers.py,
 * api.py, engine_main.py), plus what the handlers need to reach and change the cluster
 * (VPC placement, kubectl layer, EKS/STS/CloudWatch permissions) and the API Gateway
 * the Lab UI calls through CloudFront /admin/*.
 */
export class FailureSimulatorApiStack extends cdk.Stack {
  public readonly apiEndpoint: string;
  public readonly apiId: string;
  public readonly apiStageName = 'prod';

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
      description: 'Security group for the DevOps Agent Lab Lambda functions',
      allowAllOutbound: true,
    });

    new ec2.CfnSecurityGroupIngress(this, 'EksIngressFromFailureSimulator', {
      groupId: eksSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 443,
      toPort: 443,
      sourceSecurityGroupId: lambdaSg.securityGroupId,
      description: 'Allow Lab Lambda functions to reach the EKS API server',
    });

    // -----------------------------------------------------------------------
    // Engine + API Lambda. deploy-all grants the ONE role an EKS access entry
    // (see the FailureSimulatorLambdaRoleArn output), hence the fixed role name.
    // -----------------------------------------------------------------------
    const lab = new LabEngine(this, 'Lab', {
      labDir: path.join(__dirname, '..', '..', 'lab'),
      stageDir: path.join(__dirname, '..', '.lab-stage'),
      engineHandler: 'engine_main.handler',
      apiHandler: 'api.handler',
      namePrefix: `${projectName}-${environment}`,
      roleName: `${projectName}-${environment}-failure-simulator-role`,
      layers: [new KubectlV36Layer(this, 'KubectlLayer')],   // keep within one minor of eksKubernetesVersion (bin/app.ts)
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
        TRIGGER_LAMBDA_NAME: `${projectName}-${environment}-devops-trigger`,
        DEVOPS_AGENT_REGION: devOpsAgentRegion,
        DEVOPS_AGENT_SPACE_ID: devOpsAgentSpaceId,
      },
      policyStatements: [
        new iam.PolicyStatement({
          sid: 'EksDescribeCluster',
          actions: ['eks:DescribeCluster'],
          resources: [this.formatArn({ service: 'eks', resource: 'cluster', resourceName: eksClusterName })],
        }),
        new iam.PolicyStatement({
          sid: 'StsGetCallerIdentity',
          actions: ['sts:GetCallerIdentity'],
          resources: ['*'],
        }),
        new iam.PolicyStatement({
          sid: 'CloudWatchAlarmsAndDnsMetric',
          actions: ['cloudwatch:DescribeAlarms', 'cloudwatch:PutMetricData'],
          resources: ['*'],
        }),
      ],
    });

    // -----------------------------------------------------------------------
    // API Gateway — the routes this Lab's api.py serves
    // -----------------------------------------------------------------------
    // Scoped under `lab` so the API and its methods keep the logical ids they were deployed
    // with (the Frontend stack imports the API id; methods cannot be recreated in place).
    const api = new apigateway.RestApi(lab, 'RestApi', {
      restApiName: `${projectName}-${environment}-failure-simulator-api`,
      description: 'DevOps Agent Lab API for the EKS demo',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
      deployOptions: {
        stageName: this.apiStageName,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        metricsEnabled: true,
      },
    });

    const integration = new apigateway.LambdaIntegration(lab.apiFunction);
    const admin = api.root.addResource('admin');
    admin.addResource('status').addMethod('GET', integration);
    admin.addResource('usage').addMethod('GET', integration);
    admin.addResource('tasks').addMethod('GET', integration);
    // /admin/scenarios lists definitions; /admin/scenarios/{id}/inject drives one.
    const scenarios = admin.addResource('scenarios');
    scenarios.addMethod('GET', integration);
    const inject = scenarios.addResource('{scenarioId}').addResource('inject');
    inject.addMethod('POST', integration);
    inject.addMethod('DELETE', integration);

    this.apiEndpoint = api.url;
    this.apiId = api.restApiId;

    // -----------------------------------------------------------------------
    // Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'FailureSimulatorApiEndpoint', {
      description: 'DevOps Agent Lab API Gateway endpoint URL',
      value: api.url,
    });

    new cdk.CfnOutput(this, 'FailureSimulatorLambdaRoleArn', {
      description: 'Lab Lambda IAM Role ARN (add to EKS access entries)',
      value: lab.role.roleArn,
    });

    new cdk.CfnOutput(this, 'LabEngineAliasArn', {
      description: 'Qualified ARN of the Lab engine durable function (invoke with --durable-execution-name)',
      value: lab.engineAlias.functionArn,
    });
  }
}
