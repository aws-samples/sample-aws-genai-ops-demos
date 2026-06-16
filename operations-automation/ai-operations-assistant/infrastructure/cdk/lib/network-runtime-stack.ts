import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { BaseRuntimeStack } from './base-runtime-stack';

/**
 * G.O.A.T. NetworkRuntimeStack — Imports from NetworkInfraStack, builds container,
 * creates AgentCore CfnRuntime for the Network Agent.
 *
 * Pure subclass of BaseRuntimeStack (Req 6.13) that wires the Network Agent runtime
 * with the cross-stack values produced by NetworkInfraStack. All environment
 * variables are sourced from `cdk.Fn.importValue()` of the `GOATNetworkAgent*`
 * exports so synthesis never embeds hardcoded resource identifiers.
 *
 * The foundation model identifier (`amazon.nova-lite-v1:0`) is passed via the
 * `AGENTCORE_MODEL_ID` environment variable per the design (Req 1.3).
 *
 * The Network Agent container image bundles tshark and a handful of Python
 * dependencies that take noticeably longer to build than the other sub-agents.
 * `buildWaitTimeoutMinutes: 30` raises the build-waiter budget from the default
 * 14 minutes to the 30-minute envelope mandated by Req 6.13/6.14 so the
 * deployment does not fail spuriously while the image is still building.
 */
export class NetworkRuntimeStack extends BaseRuntimeStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      ...props,
      config: {
        domainName: 'network',
        exportPrefix: 'GOATNetworkAgent',
        ecrRepoName: 'goat-network-agent-repository',
        runtimeName: 'goat_network_agent',
        runtimeDescription: 'G.O.A.T. Network Agent - VPC packet capture and pcap analysis',
        agentSourcePath: '../../agents/network-agent',
        buildWaitTimeoutMinutes: 30,
        environmentVariables: {
          CAPTURE_STATE_TABLE: cdk.Fn.importValue('GOATNetworkAgentCaptureStateTableName'),
          VNI_LOOKUP_TABLE: cdk.Fn.importValue('GOATNetworkAgentVniLookupTableName'),
          STOP_CAPTURE_INVOKER_LAMBDA_ARN: cdk.Fn.importValue('GOATNetworkAgentStopCaptureInvokerLambdaArn'),
          SCHEDULE_GROUP_NAME: cdk.Fn.importValue('GOATNetworkAgentAutoStopScheduleGroupName'),
          SCHEDULER_TARGET_ROLE_ARN: cdk.Fn.importValue('GOATNetworkAgentSchedulerTargetRoleArn'),
          TRAFFIC_MIRROR_FILTER_ID: cdk.Fn.importValue('GOATNetworkAgentTrafficMirrorFilterId'),
          TRAFFIC_MIRROR_TARGET_ID: cdk.Fn.importValue('GOATNetworkAgentTrafficMirrorTargetId'),
          COLLECTOR_INSTANCE_ID: cdk.Fn.importValue('GOATNetworkAgentCollectorInstanceId'),
          TRANSFORMATION_SFN_ARN: cdk.Fn.importValue('GOATNetworkAgentTransformationStateMachineArn'),
          DATA_BUCKET_NAME: cdk.Fn.importValue('GOATNetworkAgentDataBucketName'),
          GLUE_DATABASE: cdk.Fn.importValue('GOATNetworkAgentGlueDatabaseName'),
          GLUE_TABLE: cdk.Fn.importValue('GOATNetworkAgentGlueTableName'),
          AGENTCORE_MODEL_ID: 'amazon.nova-lite-v1:0',
        },
      },
    });
  }
}
