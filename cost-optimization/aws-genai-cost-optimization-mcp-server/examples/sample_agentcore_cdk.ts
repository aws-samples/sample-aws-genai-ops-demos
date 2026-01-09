// CDK Stack with AgentCore lifecycle configuration
// This demonstrates cost optimization patterns in AWS CDK/TypeScript

import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';

export class AgentCoreStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Example 1: Cost-optimized configuration for batch processing
    const batchAgent = new cdk.CfnResource(this, 'BatchProcessingAgent', {
      type: 'AWS::BedrockAgentCore::AgentRuntime',
      properties: {
        AgentRuntimeName: 'batch-processing-agent',
        AgentRuntimeArtifact: {
          ContainerConfiguration: {
            ContainerUri: '123456789012.dkr.ecr.us-west-2.amazonaws.com/batch-agent:latest'
          }
        },
        NetworkConfiguration: { NetworkMode: 'PUBLIC' },
        RoleArn: 'arn:aws:iam::123456789012:role/AgentRuntimeRole'
      }
    });

    // Add lifecycle configuration for cost optimization
    // Note: CDK types don't support this yet, so we use addPropertyOverride
    // Aggressive cleanup for scheduled batch processing workload
    batchAgent.addPropertyOverride('LifecycleConfiguration', {
      IdleRuntimeSessionTimeout: 300,  // 5 minutes (in seconds) - quick cleanup after task completion
      MaxLifetime: 1800,               // 30 minutes (in seconds) - max runtime for extraction tasks
    });

    // Example 2: Interactive agent with moderate timeouts
    const interactiveAgent = new cdk.CfnResource(this, 'InteractiveAgent', {
      type: 'AWS::BedrockAgentCore::AgentRuntime',
      properties: {
        AgentRuntimeName: 'interactive-agent',
        AgentRuntimeArtifact: {
          ContainerConfiguration: {
            ContainerUri: '123456789012.dkr.ecr.us-west-2.amazonaws.com/interactive-agent:latest'
          }
        },
        NetworkConfiguration: { NetworkMode: 'PUBLIC' },
        RoleArn: 'arn:aws:iam::123456789012:role/AgentRuntimeRole'
      }
    });

    interactiveAgent.addPropertyOverride('LifecycleConfiguration', {
      IdleRuntimeSessionTimeout: 600,  // 10 minutes - balance between UX and cost
      MaxLifetime: 7200,               // 2 hours - typical session length
    });

    // Example 3: Long-running agent (HIGHER COSTS)
    const longRunningAgent = new cdk.CfnResource(this, 'LongRunningAgent', {
      type: 'AWS::BedrockAgentCore::AgentRuntime',
      properties: {
        AgentRuntimeName: 'long-running-agent',
        AgentRuntimeArtifact: {
          ContainerConfiguration: {
            ContainerUri: '123456789012.dkr.ecr.us-west-2.amazonaws.com/long-agent:latest'
          }
        },
        NetworkConfiguration: { NetworkMode: 'PUBLIC' },
        RoleArn: 'arn:aws:iam::123456789012:role/AgentRuntimeRole'
      }
    });

    // WARNING: This configuration will result in higher costs
    longRunningAgent.addPropertyOverride('LifecycleConfiguration', {
      IdleRuntimeSessionTimeout: 3600,  // 1 hour - keeps instances alive longer
      MaxLifetime: 28800,               // 8 hours - maximum allowed
    });

    // Example 4: Using default configuration (no override)
    const defaultAgent = new cdk.CfnResource(this, 'DefaultAgent', {
      type: 'AWS::BedrockAgentCore::AgentRuntime',
      properties: {
        AgentRuntimeName: 'default-agent',
        AgentRuntimeArtifact: {
          ContainerConfiguration: {
            ContainerUri: '123456789012.dkr.ecr.us-west-2.amazonaws.com/default-agent:latest'
          }
        },
        NetworkConfiguration: { NetworkMode: 'PUBLIC' },
        RoleArn: 'arn:aws:iam::123456789012:role/AgentRuntimeRole'
      }
      // No LifecycleConfiguration - uses AWS defaults (900s idle, 28800s max)
    });
  }
}
