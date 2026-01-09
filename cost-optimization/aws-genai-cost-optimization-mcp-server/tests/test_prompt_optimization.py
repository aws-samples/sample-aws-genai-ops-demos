"""Tests for prompt optimization detection."""

import pytest
from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector


def test_detect_repeated_prompt_context():
    """Test detection of repeated prompt context (caching opportunity)."""
    detector = BedrockDetector()
    
    # Same large prompt literal used multiple times (>200 chars to be detected)
    large_prompt = "You are an expert AI assistant with deep knowledge of software engineering, cloud architecture, and best practices. Always provide detailed, accurate, and comprehensive responses that demonstrate your expertise. Consider multiple perspectives and provide balanced viewpoints."
    
    content = f'''
import boto3
import json

bedrock = boto3.client('bedrock-runtime')

def call_one():
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=json.dumps({{"system": "{large_prompt}", "messages": []}})
    )

def call_two():
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=json.dumps({{"system": "{large_prompt}", "messages": []}})
    )
'''
    
    findings = detector.analyze(content, "test.py")
    repeated_findings = [f for f in findings if f["type"] == "repeated_prompt_context"]
    
    assert len(repeated_findings) > 0
    assert repeated_findings[0]["usage_count"] >= 2
    assert "caching" in repeated_findings[0]["cost_consideration"].lower()
    assert "90%" in repeated_findings[0]["cost_consideration"]


def test_detect_prompt_improvement_opportunity():
    """Test detection of complex prompts that could benefit from improvement."""
    detector = BedrockDetector()
    
    # Complex task without chain-of-thought (>200 chars with complexity indicators)
    content = '''
prompt = """Analyze the following code and evaluate its performance characteristics in detail. 
Compare different approaches and provide a comprehensive assessment of the trade-offs involved.
Consider scalability, maintainability, and resource utilization in your detailed analysis."""

response = bedrock.invoke_model(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=json.dumps({"messages": [{"role": "user", "content": prompt}]})
)
'''
    
    findings = detector.analyze(content, "test.py")
    improvement_findings = [f for f in findings if f["type"] == "prompt_improvement_opportunity"]
    
    assert len(improvement_findings) > 0
    assert "chain-of-thought" in improvement_findings[0]["issue"].lower()
    assert "Claude Prompt Improver" in improvement_findings[0]["optimization_tool"]
    assert "tool_url" in improvement_findings[0]


def test_no_improvement_suggestion_for_structured_prompt():
    """Test that structured prompts don't trigger improvement suggestion."""
    detector = BedrockDetector()
    
    # Complex task WITH chain-of-thought
    content = '''
prompt = """Analyze the following code step by step. Think through your reasoning process:
1. First, evaluate the structure
2. Then, assess performance
3. Finally, provide recommendations"""

response = bedrock.invoke_model(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=json.dumps({"messages": [{"role": "user", "content": prompt}]})
)
'''
    
    findings = detector.analyze(content, "test.py")
    improvement_findings = [f for f in findings if f["type"] == "prompt_improvement_opportunity"]
    
    # Should not suggest improvement for already structured prompts
    assert len(improvement_findings) == 0


def test_detect_missing_cache_control():
    """Test detection of large prompts without cache control."""
    detector = BedrockDetector()
    
    large_prompt = "x" * 300  # Large prompt
    
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
    cache_findings = [f for f in findings if f["type"] == "missing_prompt_caching"]
    
    assert len(cache_findings) > 0
    assert "cache" in cache_findings[0]["cost_consideration"].lower()
    assert "90%" in cache_findings[0]["cost_consideration"]
    assert "Bedrock Prompt Caching" in cache_findings[0]["aws_feature"]


def test_no_cache_warning_with_cache_control():
    """Test that cache warning doesn't appear when cache control is present."""
    detector = BedrockDetector()
    
    large_prompt = "x" * 300
    
    content = f'''
import boto3

bedrock = boto3.client('bedrock-runtime')

prompt = "{large_prompt}"

response = bedrock.invoke_model(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=json.dumps({{
        "messages": [{{
            "role": "user", 
            "content": [{{
                "type": "text",
                "text": prompt,
                "cacheControl": {{"type": "ephemeral"}}
            }}]
        }}]
    }})
)
'''
    
    findings = detector.analyze(content, "test.py")
    cache_findings = [f for f in findings if f["type"] == "missing_prompt_caching"]
    
    # Should not warn about missing cache when it's present
    assert len(cache_findings) == 0


def test_prompt_optimization_provides_actionable_info():
    """Test that findings include actionable information."""
    detector = BedrockDetector()
    
    # Repeated context literal (>200 chars)
    large_prompt = "You are an expert assistant with comprehensive knowledge across multiple domains including software engineering, data science, and cloud architecture. Always provide detailed and accurate responses. " * 2
    
    content = f'''
def call1():
    bedrock.invoke_model(body=json.dumps({{"system": "{large_prompt}"}}))

def call2():
    bedrock.invoke_model(body=json.dumps({{"system": "{large_prompt}"}}))
'''
    
    findings = detector.analyze(content, "test.py")
    repeated_findings = [f for f in findings if f["type"] == "repeated_prompt_context"]
    
    assert len(repeated_findings) > 0
    finding = repeated_findings[0]
    
    # Should have actionable information
    assert "optimization_questions" in finding
    assert len(finding["optimization_questions"]) > 0
    assert "aws_feature" in finding
    assert "potential_savings" in finding


def test_detect_nova_optimization_opportunity():
    """Test detection of Nova optimization opportunities."""
    detector = BedrockDetector()
    
    # Large prompt with Nova model
    large_prompt = "You are an expert AI assistant. Analyze the following data carefully and provide comprehensive insights. Consider all aspects of the problem and provide detailed recommendations based on best practices and industry standards. " * 2
    
    content = f'''
import boto3

bedrock = boto3.client('bedrock-runtime')

prompt = "{large_prompt}"

response = bedrock.invoke_model(
    modelId="amazon.nova-micro-v1:0",
    body=json.dumps({{"messages": [{{"role": "user", "content": prompt}}]}})
)
'''
    
    findings = detector.analyze(content, "test.py")
    nova_findings = [f for f in findings if f["type"] == "nova_optimization_opportunity"]
    
    assert len(nova_findings) > 0
    assert "Nova Prompt Optimizer" in nova_findings[0]["optimization_tool"]
    assert "nova_models" in nova_findings[0]
    assert "amazon.nova-micro-v1:0" in nova_findings[0]["nova_models"]
    assert "installation" in nova_findings[0]
    assert "pip install" in nova_findings[0]["installation"]


def test_no_nova_optimization_without_nova_model():
    """Test that Nova optimization isn't suggested without Nova models."""
    detector = BedrockDetector()
    
    large_prompt = "x" * 400
    
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
    nova_findings = [f for f in findings if f["type"] == "nova_optimization_opportunity"]
    
    # Should not suggest Nova optimizer for non-Nova models
    assert len(nova_findings) == 0


def test_nova_optimization_includes_requirements():
    """Test that Nova optimization findings include requirements."""
    detector = BedrockDetector()
    
    large_prompt = "x" * 400
    
    content = f'''
prompt = "{large_prompt}"

response = bedrock.invoke_model(
    modelId="amazon.nova-pro-v1:0",
    body=json.dumps({{"messages": [{{"role": "user", "content": prompt}}]}})
)
'''
    
    findings = detector.analyze(content, "test.py")
    nova_findings = [f for f in findings if f["type"] == "nova_optimization_opportunity"]
    
    assert len(nova_findings) > 0
    finding = nova_findings[0]
    
    # Should include requirements and actionable info
    assert "requirements" in finding
    assert len(finding["requirements"]) > 0
    assert "when_to_use" in finding
    assert "benefits" in finding
    assert len(finding["benefits"]) > 0


def test_detect_prompt_routing_opportunity():
    """Test detection of mixed complexity prompts (routing opportunity)."""
    detector = BedrockDetector()
    
    # Simple prompt (>200 chars)
    simple_prompt = "Summarize the following text briefly and list the key points in a simple format. Keep it concise and straightforward. Focus on the main ideas without going into too much detail. Just extract the essential information."
    
    # Complex prompt (>200 chars)
    complex_prompt = "Analyze the following data comprehensively and in great detail. Evaluate multiple perspectives carefully, compare different approaches systematically, and provide detailed reasoning for your recommendations. Consider edge cases and potential biases. Think through the implications thoroughly."
    
    content = f'''
import boto3

bedrock = boto3.client('bedrock-runtime')

def simple_task(text):
    prompt = "{simple_prompt}"
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-5-sonnet-20241022-v2:0",
        body=json.dumps({{"messages": [{{"role": "user", "content": prompt}}]}})
    )

def complex_task(data):
    prompt = "{complex_prompt}"
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-5-sonnet-20241022-v2:0",
        body=json.dumps({{"messages": [{{"role": "user", "content": prompt}}]}})
    )
'''
    
    findings = detector.analyze(content, "test.py")
    routing_findings = [f for f in findings if f["type"] == "prompt_routing_opportunity"]
    
    assert len(routing_findings) > 0
    assert "claude-3-5-sonnet" in routing_findings[0]["current_model"]
    assert "complexity_variation" in routing_findings[0]
    assert routing_findings[0]["complexity_variation"]["range"] >= 2


def test_no_routing_suggestion_for_uniform_complexity():
    """Test that routing isn't suggested when all prompts have similar complexity."""
    detector = BedrockDetector()
    
    # All simple prompts
    prompt1 = "Summarize this text briefly."
    prompt2 = "List the key points."
    
    content = f'''
def task1():
    prompt = "{prompt1}"
    bedrock.invoke_model(modelId="anthropic.claude-3-5-sonnet-20241022-v2:0", body=body)

def task2():
    prompt = "{prompt2}"
    bedrock.invoke_model(modelId="anthropic.claude-3-5-sonnet-20241022-v2:0", body=body)
'''
    
    findings = detector.analyze(content, "test.py")
    routing_findings = [f for f in findings if f["type"] == "prompt_routing_opportunity"]
    
    # Should not suggest routing for uniform complexity
    assert len(routing_findings) == 0


def test_no_routing_suggestion_when_already_using_routing():
    """Test that routing isn't suggested when already using it."""
    detector = BedrockDetector()
    
    simple_prompt = "Summarize this."
    complex_prompt = "Analyze comprehensively and evaluate multiple detailed perspectives with reasoning."
    
    content = f'''
# Already using prompt routing
router = bedrock.create_prompt_router()

def task1():
    prompt = "{simple_prompt}"
    response = router.invoke(prompt)

def task2():
    prompt = "{complex_prompt}"
    response = router.invoke(prompt)
'''
    
    findings = detector.analyze(content, "test.py")
    routing_findings = [f for f in findings if f["type"] == "prompt_routing_opportunity"]
    
    # Should not suggest routing when already using it
    assert len(routing_findings) == 0


def test_detect_existing_prompt_routing():
    """Test detection of existing prompt routing usage (positive feedback)."""
    detector = BedrockDetector()
    
    content = '''
import boto3

bedrock = boto3.client('bedrock-runtime')

# Using prompt router ARN
router_arn = "arn:aws:bedrock:us-east-1:517675598740:prompt-router/z0e0g1c7y7za"

def process_request(prompt):
    response = bedrock.invoke_model(
        modelId=router_arn,
        body=json.dumps({"messages": [{"role": "user", "content": prompt}]})
    )
    return response
'''
    
    findings = detector.analyze(content, "test.py")
    routing_detected = [f for f in findings if f["type"] == "prompt_routing_detected"]
    
    assert len(routing_detected) > 0
    assert "router_arn" in routing_detected[0]
    assert "arn:aws:bedrock" in routing_detected[0]["router_arn"]
    assert "best_practices" in routing_detected[0]
    assert len(routing_detected[0]["best_practices"]) > 0


def test_routing_detection_includes_monitoring_guidance():
    """Test that routing detection includes monitoring guidance."""
    detector = BedrockDetector()
    
    content = '''
router_id = "arn:aws:bedrock:eu-west-1:123456789012:prompt-router/abc123xyz"
response = bedrock.invoke_model(modelId=router_id, body=body)
'''
    
    findings = detector.analyze(content, "test.py")
    routing_detected = [f for f in findings if f["type"] == "prompt_routing_detected"]
    
    assert len(routing_detected) > 0
    finding = routing_detected[0]
    
    # Should include monitoring and best practices
    assert "monitoring" in finding
    assert "best_practices" in finding
    assert "CloudWatch" in finding["monitoring"]
