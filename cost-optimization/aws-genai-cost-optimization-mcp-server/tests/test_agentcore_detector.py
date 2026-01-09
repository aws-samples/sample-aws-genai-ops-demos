"""Tests for AgentCore detector."""

import pytest
from pathlib import Path
from mcp_cost_optim_genai.detectors.agentcore_detector import AgentCoreDetector


def test_detect_agentcore_app():
    """Test detection of AgentCore app initialization."""
    detector = AgentCoreDetector()

    content = """
from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()
"""

    findings = detector.analyze(content, "test.py")
    assert len(findings) > 0
    assert any(f["type"] == "agentcore_app_detected" for f in findings)


def test_detect_entrypoint_decorator():
    """Test detection of entrypoint decorator."""
    detector = AgentCoreDetector()

    content = """
@app.entrypoint
def my_agent(payload):
    return {"result": "Hello"}
"""

    findings = detector.analyze(content, "test.py")
    decorator_findings = [f for f in findings if f["type"] == "agentcore_decorator"]

    assert len(decorator_findings) > 0
    assert decorator_findings[0]["decorator_type"] == "entrypoint"


def test_detect_async_task():
    """Test detection of async task decorator."""
    detector = AgentCoreDetector()

    content = """
@app.async_task
async def background_work():
    await process_data()
    return "done"
"""

    findings = detector.analyze(content, "test.py")
    decorator_findings = [f for f in findings if f["type"] == "agentcore_decorator"]

    assert len(decorator_findings) > 0
    assert decorator_findings[0]["decorator_type"] == "async_task"


def test_detect_session_management():
    """Test detection of session management."""
    detector = AgentCoreDetector()

    content = """
from bedrock_agentcore.runtime.context import RequestContext

@app.entrypoint
def agent(payload, context: RequestContext):
    session_id = context.session_id
    return {"session": session_id}
"""

    findings = detector.analyze(content, "test.py")
    session_findings = [f for f in findings if f["type"] == "agentcore_session_management"]

    assert len(session_findings) > 0


def test_detect_streaming():
    """Test detection of streaming patterns."""
    detector = AgentCoreDetector()

    content = """
@app.entrypoint
async def streaming_agent(payload):
    async for event in stream:
        yield event["data"]
"""

    findings = detector.analyze(content, "test.py")
    streaming_findings = [f for f in findings if f["type"] == "agentcore_streaming"]

    assert len(streaming_findings) > 0


def test_detect_deployment_pattern():
    """Test detection of deployment patterns."""
    detector = AgentCoreDetector()

    content = """
# Deploy to production
agentcore launch
"""

    findings = detector.analyze(content, "test.sh")
    deployment_findings = [f for f in findings if f["type"] == "agentcore_deployment"]

    assert len(deployment_findings) > 0
    assert deployment_findings[0]["deployment_type"] == "direct_deploy"


def test_detect_auth_pattern():
    """Test detection of authentication patterns."""
    detector = AgentCoreDetector()

    content = """
agentcore configure --entrypoint my_agent.py \\
  --authorizer-config '{"customJWTAuthorizer": {"discoveryUrl": "https://..."}}'
"""

    findings = detector.analyze(content, "test.sh")
    auth_findings = [f for f in findings if f["type"] == "agentcore_authentication"]

    assert len(auth_findings) > 0


def test_detect_lifecycle_config_optimized():
    """Test detection of cost-optimized lifecycle configuration."""
    detector = AgentCoreDetector()

    content = """
lifecycleConfiguration={
    'idleRuntimeSessionTimeout': 300,  # 5 minutes
    'maxLifetime': 3600  # 1 hour
}
"""

    findings = detector.analyze(content, "test.py")
    idle_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_idle_timeout"]
    max_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_max_lifetime"]

    assert len(idle_findings) > 0
    assert idle_findings[0]["configured_value"] == 300
    assert "Cost optimized" in idle_findings[0]["cost_consideration"]

    assert len(max_findings) > 0
    assert max_findings[0]["configured_value"] == 3600


def test_detect_lifecycle_config_alert():
    """Test detection of high-cost lifecycle configuration."""
    detector = AgentCoreDetector()

    content = """
lifecycleConfiguration={
    'idleRuntimeSessionTimeout': 3600,  # 1 hour - higher than default
    'maxLifetime': 28800  # 8 hours
}
"""

    findings = detector.analyze(content, "test.py")
    idle_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_idle_timeout"]

    assert len(idle_findings) > 0
    assert idle_findings[0]["configured_value"] == 3600
    assert "COST ALERT" in idle_findings[0]["cost_consideration"]


def test_detect_cdk_typescript_lifecycle():
    """Test detection of lifecycle configuration in CDK/TypeScript code."""
    detector = AgentCoreDetector()

    content = """
    agentRuntime.addPropertyOverride('LifecycleConfiguration', {
      IdleRuntimeSessionTimeout: 300,  // 5 minutes
      MaxLifetime: 1800,               // 30 minutes
    });
"""

    findings = detector.analyze(content, "test.ts")
    idle_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_idle_timeout"]
    max_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_max_lifetime"]

    assert len(idle_findings) > 0
    assert idle_findings[0]["configured_value"] == 300
    assert "Cost optimized" in idle_findings[0]["cost_consideration"]

    assert len(max_findings) > 0
    assert max_findings[0]["configured_value"] == 1800


def test_detect_cdk_runtime_using_defaults():
    """Test detection of CDK Runtime without lifecycle configuration (using defaults)."""
    detector = AgentCoreDetector()

    content = """
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';

const agentRuntime = new bedrockagentcore.CfnRuntime(this, 'AgentRuntime', {
  agentRuntimeName: 'my-agent',
  roleArn: agentRole.roleArn,
  agentRuntimeArtifact: {
    containerConfiguration: {
      containerUri: `${repository.repositoryUri}:latest`,
    },
  },
  // No lifecycleConfiguration specified - using AWS defaults
});
"""

    findings = detector.analyze(content, "runtime-stack.ts")
    missing_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_missing"]

    assert len(missing_findings) > 0
    assert "optimization_opportunity" in missing_findings[0]
    assert "Defaults may not be optimal" in missing_findings[0]["cost_consideration"]
    assert missing_findings[0]["defaults_being_used"]["idleRuntimeSessionTimeout"] == "900 seconds (15 minutes)"
    assert missing_findings[0]["defaults_being_used"]["maxLifetime"] == "28800 seconds (8 hours)"
    assert "potential_savings" in missing_findings[0]["optimization_opportunity"]


def test_detect_python_create_runtime_with_lifecycle():
    """Test detection of Python create_agent_runtime WITH lifecycle configuration."""
    detector = AgentCoreDetector()

    content = """
import boto3

client = boto3.client("bedrock-agentcore-control", region_name="us-west-2")

response = client.create_agent_runtime(
    agentRuntimeName="my_agent",
    agentRuntimeArtifact={
        "containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-west-2.amazonaws.com/my-agent:latest"
        }
    },
    lifecycleConfiguration={
        "idleRuntimeSessionTimeout": 300,
        "maxLifetime": 3600,
    },
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn="arn:aws:iam::123456789012:role/AgentRuntimeRole",
)
"""

    findings = detector.analyze(content, "test.py")
    
    # Should detect the explicit lifecycle config
    idle_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_idle_timeout"]
    assert len(idle_findings) > 0
    assert idle_findings[0]["configured_value"] == 300
    
    # Should NOT flag as missing lifecycle config
    missing_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_missing"]
    assert len(missing_findings) == 0


def test_detect_python_create_runtime_missing_lifecycle():
    """Test detection of Python create_agent_runtime WITHOUT lifecycle configuration."""
    detector = AgentCoreDetector()

    content = """
import boto3

client = boto3.client("bedrock-agentcore-control", region_name="us-west-2")

response = client.create_agent_runtime(
    agentRuntimeName="default_agent",
    agentRuntimeArtifact={
        "containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-west-2.amazonaws.com/my-agent:latest"
        }
    },
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn="arn:aws:iam::123456789012:role/AgentRuntimeRole",
)
"""

    findings = detector.analyze(content, "test.py")
    missing_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_missing"]

    # Should detect missing lifecycle config
    assert len(missing_findings) > 0
    assert missing_findings[0]["api_call"] == "create_agent_runtime"
    assert "optimization_opportunity" in missing_findings[0]
    assert "Defaults may not be optimal" in missing_findings[0]["cost_consideration"]
    assert missing_findings[0]["defaults_being_used"]["idleRuntimeSessionTimeout"] == "900 seconds (15 minutes)"


def test_detect_python_update_runtime_missing_lifecycle():
    """Test detection of Python update_agent_runtime WITHOUT lifecycle configuration."""
    detector = AgentCoreDetector()

    content = """
import boto3

client = boto3.client("bedrock-agentcore-control")

client.update_agent_runtime(
    agentRuntimeId="existing_agent_id",
    agentRuntimeArtifact={
        "containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-west-2.amazonaws.com/my-agent:latest"
        }
    },
    networkConfiguration={"networkMode": "PUBLIC"},
)
"""

    findings = detector.analyze(content, "test.py")
    missing_findings = [f for f in findings if f["type"] == "agentcore_lifecycle_missing"]

    # Should detect missing lifecycle config
    assert len(missing_findings) > 0
    assert missing_findings[0]["api_call"] == "update_agent_runtime"
    assert "optimization_opportunity" in missing_findings[0]


def test_detect_stop_runtime_session():
    """Test detection of StopRuntimeSession API usage."""
    detector = AgentCoreDetector()
    
    code = """
    import boto3
    
    client = boto3.client('bedrock-agentcore-runtime')
    
    # Terminate session after work is complete
    response = client.stop_runtime_session(
        agentRuntimeArn='arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-agent',
        sessionId='session-123'
    )
    """
    
    findings = detector.analyze(code, "test.py")
    
    stop_findings = [f for f in findings if f["type"] == "agentcore_stop_session_detected"]
    assert len(stop_findings) == 1
    assert "EXCELLENT" in stop_findings[0]["description"]
    assert "cost optimization best practice" in stop_findings[0]["cost_consideration"]
    assert "api_reference" in stop_findings[0]


def test_detect_stop_runtime_session_typescript():
    """Test detection of StopRuntimeSession in TypeScript/JavaScript."""
    detector = AgentCoreDetector()
    
    code = """
    import { BedrockAgentCoreRuntimeClient, StopRuntimeSessionCommand } from "@aws-sdk/client-bedrock-agentcore-runtime";
    
    const client = new BedrockAgentCoreRuntimeClient({ region: "us-east-1" });
    
    // Clean up session to avoid idle charges
    const command = new StopRuntimeSessionCommand({
        agentRuntimeArn: runtimeArn,
        sessionId: sessionId
    });
    
    await client.send(command);
    """
    
    findings = detector.analyze(code, "test.ts")
    
    stop_findings = [f for f in findings if f["type"] == "agentcore_stop_session_detected"]
    assert len(stop_findings) == 1
    assert "Eliminates idle time charges" in stop_findings[0]["benefit"]


def test_no_stop_session_without_api_call():
    """Test that we don't flag stop session when API is not used."""
    detector = AgentCoreDetector()
    
    code = """
    # Regular agent code without session termination
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    
    app = BedrockAgentCoreApp()
    
    @app.entrypoint
    async def handler(payload):
        return "Hello"
    """
    
    findings = detector.analyze(code, "test.py")
    
    stop_findings = [f for f in findings if f["type"] == "agentcore_stop_session_detected"]
    assert len(stop_findings) == 0
