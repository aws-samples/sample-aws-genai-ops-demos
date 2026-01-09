"""Tests for Bedrock detector."""

import pytest
from pathlib import Path
from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector


def test_detect_bedrock_client():
    """Test detection of Bedrock client initialization."""
    detector = BedrockDetector()
    
    content = """
import boto3

client = boto3.client('bedrock-runtime', region_name='us-east-1')
"""
    
    findings = detector.analyze(content, "test.py")
    assert len(findings) > 0
    assert any(f["type"] == "bedrock_client_detected" for f in findings)


def test_detect_claude_model():
    """Test detection of Claude model usage."""
    detector = BedrockDetector()
    
    content = """
response = client.invoke_model(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=json.dumps({"prompt": "Hello"})
)
"""
    
    findings = detector.analyze(content, "test.py")
    model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
    
    assert len(model_findings) > 0
    assert model_findings[0]["parsed"]["family"] == "claude"
    assert model_findings[0]["parsed"]["tier"] == "sonnet"


def test_detect_streaming_call():
    """Test detection of streaming API calls."""
    detector = BedrockDetector()
    
    content = """
response = client.invoke_model_with_response_stream(
    modelId="anthropic.claude-3-haiku-20240307-v1:0",
    body=body
)
"""
    
    findings = detector.analyze(content, "test.py")
    api_findings = [f for f in findings if f["type"] == "bedrock_api_call"]
    
    assert len(api_findings) > 0
    assert api_findings[0]["pattern"] == "streaming"


def test_detect_large_prompt():
    """Test detection of large static prompts."""
    detector = BedrockDetector()
    
    large_prompt = "x" * 600
    content = f'''
import boto3
bedrock = boto3.client('bedrock-runtime')

prompt = "{large_prompt}"

response = bedrock.invoke_model(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=json.dumps({{"messages": [{{"role": "user", "content": prompt}}]}})
)
'''
    
    findings = detector.analyze(content, "test.py")
    # Check that we detected the model and API call
    model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
    api_findings = [f for f in findings if f["type"] == "bedrock_api_call"]
    
    assert len(model_findings) > 0
    assert len(api_findings) > 0


def test_detect_amazon_nova_models():
    """Test detection of Amazon Nova models."""
    detector = BedrockDetector()
    
    content = """
response = client.invoke_model(
    modelId="amazon.nova-micro-v1:0",
    body=json.dumps({"prompt": "Hello"})
)
"""
    
    findings = detector.analyze(content, "test.py")
    model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
    
    assert len(model_findings) > 0
    assert model_findings[0]["parsed"]["family"] == "nova"
    assert model_findings[0]["parsed"]["tier"] == "micro"
    assert "nova-micro" in model_findings[0]["model_id"]



def test_detect_openai_chat_completions_with_bedrock():
    """Test detection of OpenAI Chat Completions API with Bedrock endpoint."""
    detector = BedrockDetector()
    
    content = """
from openai import OpenAI

client = OpenAI(
    base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
    api_key="test-key"
)

completion = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[{"role": "user", "content": "Hello!"}]
)
"""
    
    findings = detector.analyze(content, "test_openai.py")
    api_findings = [f for f in findings if f["type"] == "bedrock_api_call"]
    
    assert len(api_findings) > 0
    assert api_findings[0]["call_type"] == "chat_completions_create"
    assert api_findings[0]["api_style"] == "openai_compatible"
    assert api_findings[0]["bedrock_confirmed"] is True
    assert api_findings[0]["pattern"] == "synchronous"


def test_detect_openai_chat_completions_streaming():
    """Test detection of OpenAI Chat Completions API with streaming."""
    detector = BedrockDetector()
    
    content = """
from openai import OpenAI

client = OpenAI(
    base_url="https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
    api_key="test-key"
)

response = client.chat.completions.create(
    model="openai.gpt-oss-120b-1:0",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
)
"""
    
    findings = detector.analyze(content, "test_streaming.py")
    api_findings = [f for f in findings if f["type"] == "bedrock_api_call"]
    
    assert len(api_findings) > 0
    assert api_findings[0]["call_type"] == "chat_completions_create"
    assert api_findings[0]["pattern"] == "streaming"
    assert api_findings[0]["bedrock_confirmed"] is True


def test_detect_openai_without_bedrock_endpoint():
    """Test detection of OpenAI SDK without Bedrock endpoint."""
    detector = BedrockDetector()
    
    content = """
from openai import OpenAI

client = OpenAI(api_key="test-key")

completion = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
"""
    
    findings = detector.analyze(content, "test_generic_openai.py")
    api_findings = [f for f in findings if f["type"] == "bedrock_api_call"]
    
    assert len(api_findings) > 0
    assert api_findings[0]["call_type"] == "chat_completions_create"
    assert api_findings[0]["bedrock_confirmed"] is False


def test_detect_service_tier_priority():
    """Test detection of Priority service tier."""
    detector = BedrockDetector()
    
    content = """
response = bedrock.invoke_model(
    modelId="amazon.nova-pro-v1:0",
    body=json.dumps({
        "messages": [{"role": "user", "content": "Hello"}],
        "service_tier": "priority"
    })
)
"""
    
    findings = detector.analyze(content, "test_priority.py")
    tier_findings = [f for f in findings if f["type"] == "bedrock_service_tier"]
    
    assert len(tier_findings) > 0
    assert tier_findings[0]["service_tier"] == "priority"
    assert tier_findings[0]["tier_category"] == "premium"
    assert "price premium" in tier_findings[0]["pricing_model"].lower()


def test_detect_service_tier_flex():
    """Test detection of Flex service tier."""
    detector = BedrockDetector()
    
    content = """
response = client.converse(
    modelId="openai.gpt-oss-120b-1:0",
    messages=[{"role": "user", "content": "Summarize this"}],
    service_tier="flex"
)
"""
    
    findings = detector.analyze(content, "test_flex.py")
    tier_findings = [f for f in findings if f["type"] == "bedrock_service_tier"]
    
    assert len(tier_findings) > 0
    assert tier_findings[0]["service_tier"] == "flex"
    assert tier_findings[0]["tier_category"] == "cost-optimized"
    assert "discount" in tier_findings[0]["pricing_model"].lower()


def test_detect_service_tier_default():
    """Test detection of Default/Standard service tier."""
    detector = BedrockDetector()
    
    content = """
body = {
    "model": "qwen.qwen3-32b-v1:0",
    "service_tier": "default"
}
"""
    
    findings = detector.analyze(content, "test_default.py")
    tier_findings = [f for f in findings if f["type"] == "bedrock_service_tier"]
    
    assert len(tier_findings) > 0
    assert tier_findings[0]["service_tier"] == "default"
    assert tier_findings[0]["tier_category"] == "standard"


def test_detect_service_tier_typescript():
    """Test detection of service tier in TypeScript/JavaScript code."""
    detector = BedrockDetector()
    
    content = """
const params = {
    modelId: "deepseek.v3-v1:0",
    serviceTier: "flex"
};
"""
    
    findings = detector.analyze(content, "test.ts")
    tier_findings = [f for f in findings if f["type"] == "bedrock_service_tier"]
    
    assert len(tier_findings) > 0
    assert tier_findings[0]["service_tier"] == "flex"


def test_detect_service_tier_reserved():
    """Test detection of Reserved service tier."""
    detector = BedrockDetector()
    
    content = """
response = bedrock.invoke_model(
    modelId="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    body=json.dumps({
        "messages": [{"role": "user", "content": "Critical request"}],
        "service_tier": "reserved"
    })
)
"""
    
    findings = detector.analyze(content, "test_reserved.py")
    tier_findings = [f for f in findings if f["type"] == "bedrock_service_tier"]
    
    assert len(tier_findings) > 0
    assert tier_findings[0]["service_tier"] == "reserved"
    assert tier_findings[0]["tier_category"] == "ultra-premium"
    assert "fixed price" in tier_findings[0]["pricing_model"].lower()
    assert "99.5%" in tier_findings[0]["typical_use_cases"]


def test_detect_missing_service_tier():
    """Test detection when service_tier is NOT specified (optimization opportunity)."""
    detector = BedrockDetector()
    
    content = """
response = bedrock.invoke_model(
    modelId="amazon.nova-lite-v1:0",
    body=json.dumps({
        "messages": [{"role": "user", "content": "Process this batch"}]
    })
)
"""
    
    findings = detector.analyze(content, "test_missing_tier.py")
    tier_findings = [f for f in findings if f["type"] == "bedrock_service_tier_missing"]
    
    assert len(tier_findings) > 0
    assert tier_findings[0]["service_tier"] == "default (implicit)"
    assert tier_findings[0]["optimization_opportunity"] == True
    assert "flex" in tier_findings[0]["recommendation"].lower()
    assert "cost savings" in tier_findings[0]["recommendation"].lower()


def test_no_missing_tier_when_explicitly_set():
    """Test that we don't flag missing service_tier when it's explicitly configured."""
    detector = BedrockDetector()
    
    content = """
response = bedrock.invoke_model(
    modelId="amazon.nova-pro-v1:0",
    body=json.dumps({
        "messages": [{"role": "user", "content": "Hello"}],
        "service_tier": "flex"
    })
)
"""
    
    findings = detector.analyze(content, "test_has_tier.py")
    
    # Should have bedrock_service_tier finding
    tier_findings = [f for f in findings if f["type"] == "bedrock_service_tier"]
    assert len(tier_findings) > 0
    assert tier_findings[0]["service_tier"] == "flex"
    
    # Should NOT have bedrock_service_tier_missing finding
    missing_findings = [f for f in findings if f["type"] == "bedrock_service_tier_missing"]
    assert len(missing_findings) == 0


def test_detect_service_tier_openai_sdk_with_tier():
    """Test detection of service_tier in OpenAI SDK (Bedrock-compatible)."""
    detector = BedrockDetector()
    
    content = """
from openai import OpenAI

client = OpenAI(
    base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
    api_key="$AWS_BEARER_TOKEN_BEDROCK"
)

completion = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[{"role": "user", "content": "Hello!"}],
    service_tier="priority"
)
"""
    
    findings = detector.analyze(content, "test_openai.py")
    tier_findings = [f for f in findings if f["type"] == "bedrock_service_tier"]
    
    assert len(tier_findings) > 0
    assert tier_findings[0]["service_tier"] == "priority"
    assert tier_findings[0]["tier_category"] == "premium"


def test_detect_missing_service_tier_openai_sdk():
    """Test detection of missing service_tier in OpenAI SDK."""
    detector = BedrockDetector()
    
    content = """
from openai import OpenAI

client = OpenAI(
    base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
    api_key="$AWS_BEARER_TOKEN_BEDROCK"
)

completion = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[{"role": "user", "content": "Hello!"}]
)
"""
    
    findings = detector.analyze(content, "test_openai_missing.py")
    missing_findings = [f for f in findings if f["type"] == "bedrock_service_tier_missing"]
    
    assert len(missing_findings) > 0
    assert missing_findings[0]["service_tier"] == "default (implicit)"
    assert missing_findings[0]["api_call"] == "chat_completions_create"
    assert missing_findings[0]["optimization_opportunity"] == True
