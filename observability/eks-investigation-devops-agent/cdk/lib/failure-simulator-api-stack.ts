import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import { KubectlV36Layer } from '@aws-cdk/lambda-layer-kubectl-v36';
import { Construct } from 'constructs';
import * as path from 'path';
import { LabBackend } from '../../../../shared/lab/cdk/lab-backend';

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
 * The EKS demo's Lab: the shared LabBackend (engine, API, bundling, durable IAM) plus
 * what this demo's handlers need to reach and change the cluster: VPC placement, the
 * kubectl layer, EKS/STS/CloudWatch permissions. Scenario definitions live in
 * ../../lab/scenarios.yaml, the handlers in ../../lab/handlers.py.
 */
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
    // The Lab. deploy-all grants the ONE role an EKS access entry
    // (see the FailureSimulatorLambdaRoleArn output), hence the fixed role name.
    // -----------------------------------------------------------------------
    const lab = new LabBackend(this, 'Lab', {
      labDir: path.join(__dirname, '..', '..', 'lab'),
      stageDir: path.join(__dirname, '..', '.lab-stage'),
      namePrefix: `${projectName}-${environment}`,
      roleName: `${projectName}-${environment}-failure-simulator-role`,
      devOpsAgentRegion,
      devOpsAgentSpaceId,
      triggerLambdaName: `${projectName}-${environment}-devops-trigger`,
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
          sid: 'DnsScenarioMetric',
          actions: ['cloudwatch:PutMetricData'],
          resources: ['*'],
        }),
      ],
    });

    this.apiEndpoint = lab.api.url;
    this.apiId = lab.api.restApiId;
    this.apiStageName = lab.apiStageName;

    // -----------------------------------------------------------------------
    // Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'FailureSimulatorApiEndpoint', {
      description: 'DevOps Agent Lab API Gateway endpoint URL',
      value: lab.api.url,
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
