"""Sample AgentCore lifecycle configuration for testing."""

import boto3

client = boto3.client("bedrock-agentcore-control", region_name="us-west-2")

# Example 1: Cost-optimized configuration (shorter timeouts)
response_optimized = client.create_agent_runtime(
    agentRuntimeName="cost_optimized_agent",
    agentRuntimeArtifact={
        "containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-west-2.amazonaws.com/my-agent:latest"
        }
    },
    lifecycleConfiguration={
        "idleRuntimeSessionTimeout": 300,  # 5 minutes - faster cleanup
        "maxLifetime": 3600,  # 1 hour - shorter max lifetime
    },
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn="arn:aws:iam::123456789012:role/AgentRuntimeRole",
)

# Example 2: Extended configuration (higher costs)
response_extended = client.create_agent_runtime(
    agentRuntimeName="long_running_agent",
    agentRuntimeArtifact={
        "containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-west-2.amazonaws.com/my-agent:latest"
        }
    },
    lifecycleConfiguration={
        "idleRuntimeSessionTimeout": 3600,  # 1 hour - keeps instances alive longer
        "maxLifetime": 28800,  # 8 hours - maximum allowed
    },
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn="arn:aws:iam::123456789012:role/AgentRuntimeRole",
)

# Example 3: Default configuration (no lifecycle specified)
response_default = client.create_agent_runtime(
    agentRuntimeName="default_agent",
    agentRuntimeArtifact={
        "containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-west-2.amazonaws.com/my-agent:latest"
        }
    },
    # No lifecycleConfiguration - uses defaults (900s idle, 28800s max)
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn="arn:aws:iam::123456789012:role/AgentRuntimeRole",
)

# Example 4: Update existing runtime with better lifecycle settings
client.update_agent_runtime(
    agentRuntimeId="existing_agent_id",
    agentRuntimeArtifact={
        "containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-west-2.amazonaws.com/my-agent:latest"
        }
    },
    lifecycleConfiguration={
        "idleRuntimeSessionTimeout": 600,  # 10 minutes
        "maxLifetime": 7200,  # 2 hours
    },
    networkConfiguration={"networkMode": "PUBLIC"},
)
