"""Tests for cross-cutting pattern detection."""

import pytest
import asyncio
import json
from mcp_cost_optim_genai.scanner import ProjectScanner


@pytest.mark.asyncio
async def test_streaming_in_agentcore_detection():
    """Test detection of Bedrock streaming in AgentCore Runtime context."""
    scanner = ProjectScanner()
    
    # Create a file with both AgentCore and Bedrock streaming
    content = """
from bedrock_agentcore import BedrockAgentCoreApp
import boto3

app = BedrockAgentCoreApp()
bedrock = boto3.client('bedrock-runtime')

@app.entrypoint
async def streaming_agent(payload):
    # Using streaming in AgentCore context
    response = bedrock.invoke_model_with_response_stream(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=body
    )
    
    for event in response['body']:
        yield event
"""
    
    # Write to temp file
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(content)
        temp_path = f.name
    
    try:
        result = await scanner.analyze_file(temp_path)
        data = json.loads(result)
        
        # Should detect cross-cutting pattern
        cross_findings = [
            f for f in data['findings'] 
            if f.get('type') == 'cross_service_cost_impact'
        ]
        
        assert len(cross_findings) > 0, "Should detect cross-service cost impact"
        
        finding = cross_findings[0]
        assert finding['pattern'] == 'streaming_in_agentcore_runtime'
        assert 'bedrock' in finding['services']
        assert 'bedrock-agentcore' in finding['services']
        assert 'optimization_questions' in finding
        assert len(finding['optimization_questions']) > 0
        
    finally:
        import os
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_no_cross_cutting_without_agentcore():
    """Test that streaming alone doesn't trigger cross-cutting alert."""
    scanner = ProjectScanner()
    
    # Bedrock streaming without AgentCore
    content = """
import boto3

bedrock = boto3.client('bedrock-runtime')

response = bedrock.invoke_model_with_response_stream(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=body
)
"""
    
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(content)
        temp_path = f.name
    
    try:
        result = await scanner.analyze_file(temp_path)
        data = json.loads(result)
        
        # Should NOT detect cross-cutting pattern
        cross_findings = [
            f for f in data['findings'] 
            if f.get('type') == 'cross_service_cost_impact'
        ]
        
        assert len(cross_findings) == 0, "Should not detect cross-service impact without AgentCore"
        
    finally:
        import os
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_no_cross_cutting_without_streaming():
    """Test that AgentCore alone doesn't trigger cross-cutting alert."""
    scanner = ProjectScanner()
    
    # AgentCore without streaming
    content = """
from bedrock_agentcore import BedrockAgentCoreApp
import boto3

app = BedrockAgentCoreApp()
bedrock = boto3.client('bedrock-runtime')

@app.entrypoint
def sync_agent(payload):
    # Synchronous call - no streaming
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=body
    )
    return response
"""
    
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(content)
        temp_path = f.name
    
    try:
        result = await scanner.analyze_file(temp_path)
        data = json.loads(result)
        
        # Should NOT detect cross-cutting pattern
        cross_findings = [
            f for f in data['findings'] 
            if f.get('type') == 'cross_service_cost_impact'
        ]
        
        assert len(cross_findings) == 0, "Should not detect cross-service impact without streaming"
        
    finally:
        import os
        os.unlink(temp_path)
