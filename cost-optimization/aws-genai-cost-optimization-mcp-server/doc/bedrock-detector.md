# Bedrock Detector

Detects Amazon Bedrock API usage patterns and provides cost optimization insights for model selection and prompt engineering.

## What It Detects

### 1. Model Usage
Identifies which Bedrock models are being used in your code:
- **Claude models**: Opus, Sonnet, Haiku (3.0, 3.5)
- **Titan models**: Text, Embeddings
- **Llama models**: Meta Llama variants
- **Mistral models**: All variants

**Example Finding:**
```json
{
  "type": "bedrock_model_usage",
  "file": "src/agent.py",
  "line": 42,
  "model_family": "claude-3-sonnet",
  "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
  "service": "bedrock"
}
```

### 2. API Call Patterns
Detects how you're invoking Bedrock:
- **Synchronous calls**: `invoke_model()` - blocks until complete
- **Streaming calls**: `invoke_model_with_response_stream()` - real-time responses
- **Converse API**: `converse()`, `converse_stream()`
- **OpenAI Chat Completions API**: `chat.completions.create()` - OpenAI-compatible interface

**Example Finding:**
```json
{
  "type": "bedrock_api_call",
  "file": "src/agent.py",
  "line": 45,
  "call_type": "invoke_model_with_response_stream",
  "pattern": "streaming",
  "service": "bedrock"
}
```

#### OpenAI Chat Completions API Support

Amazon Bedrock supports the OpenAI Chat Completions API, allowing you to use the OpenAI SDK with Bedrock models. The detector identifies these patterns:

**Example Finding:**
```json
{
  "type": "bedrock_api_call",
  "file": "src/openai_agent.py",
  "line": 15,
  "call_type": "chat_completions_create",
  "api_style": "openai_compatible",
  "bedrock_confirmed": true,
  "pattern": "synchronous",
  "note": "Using OpenAI SDK with Bedrock Runtime endpoint",
  "service": "bedrock"
}
```

**Detection Features:**
- Detects `client.chat.completions.create()` calls
- Identifies if the OpenAI client uses a Bedrock Runtime endpoint
- Detects streaming mode (`stream=True`)
- Flags when Bedrock endpoint cannot be confirmed

**Code Example:**
```python
from openai import OpenAI

# OpenAI SDK configured for Bedrock
client = OpenAI(
    base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1", 
    api_key="$AWS_BEARER_TOKEN_BEDROCK"
)

# Detected as bedrock_api_call with api_style: "openai_compatible"
completion = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[{"role": "user", "content": "Hello!"}]
)

# Streaming variant - detected with pattern: "streaming"
stream = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
)
```

**Documentation:** [Invoke a model with the OpenAI Chat Completions API](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions.html)

### 3. Token Configuration
Analyzes token limits and usage:
- Detects `max_tokens` configuration
- Flags high token limits (>4000)
- Identifies potential over-provisioning

**Example Finding:**
```json
{
  "type": "token_configuration",
  "file": "src/agent.py",
  "line": 14,
  "max_tokens": 8000,
  "service": "bedrock",
  "note": "High token limit"
}
```

## Prompt Optimization

The scanner detects multiple prompt optimization opportunities:

### 1. Repeated Prompt Context
Detects large prompts used multiple times (caching opportunity):
- Identifies prompts >200 characters used repeatedly
- Calculates potential savings with prompt caching
- **Potential savings**: 90% on cached tokens

**Example Finding:**
```json
{
  "type": "repeated_prompt_context",
  "estimated_tokens": 450,
  "usage_count": 12,
  "cost_consideration": "Same 450-token context used 12 times. Prompt caching could reduce costs by 90%.",
  "aws_feature": "Bedrock Prompt Caching"
}
```

### 2. Prompt Quality Opportunities
Detects complex tasks without proper structure:
- Identifies complexity indicators (analyze, evaluate, compare)
- Checks for chain-of-thought reasoning
- Suggests Claude Prompt Improver tool

**Example Finding:**
```json
{
  "type": "prompt_improvement_opportunity",
  "issue": "Complex task without chain-of-thought reasoning",
  "cost_consideration": "Unstructured prompts may require 2-3x retries",
  "optimization_tool": "Claude Prompt Improver",
  "tool_url": "https://docs.claude.com/..."
}
```

### 3. Missing Cache Control
Detects large prompts without cacheControl:
- Identifies prompts >200 characters without caching
- Points to Bedrock Prompt Caching feature
- **Potential savings**: 90% on input tokens

**Example Finding:**
```json
{
  "type": "missing_prompt_caching",
  "estimated_tokens": 750,
  "cost_consideration": "Large prompts without cache control. Bedrock offers 90% discount.",
  "aws_feature": "Bedrock Prompt Caching"
}
```

### 4. Nova Optimization Opportunities
Detects prompts that could benefit from AWS Nova Prompt Optimizer:
- Identifies Nova model usage with large prompts
- Suggests automated optimization tool
- **Potential savings**: 20-40% token reduction

**Example Finding:**
```json
{
  "type": "nova_optimization_opportunity",
  "estimated_tokens": 450,
  "nova_models": ["amazon.nova-micro-v1:0"],
  "cost_consideration": "Nova Prompt Optimizer can automatically test variations to reduce token usage by 20-40%.",
  "optimization_tool": "AWS Nova Prompt Optimizer",
  "tool_url": "https://github.com/aws/nova-prompt-optimizer",
  "installation": "pip install nova-prompt-optimizer",
  "when_to_use": "When you have test datasets and want automated optimization"
}
```

## Optimization Tools

The scanner recommends external tools for prompt optimization:

### Claude Prompt Improver
**When**: Complex tasks without chain-of-thought  
**How**: Manual tool in Claude Console Workbench  
**Benefits**: Adds CoT instructions, XML structure  
**Cost Impact**: Reduces retries by improving accuracy  

### AWS Nova Prompt Optimizer
**When**: Using Nova models with test datasets  
**How**: Python SDK that tests variations automatically  
**Benefits**: Finds optimal prompt through testing  
**Cost Impact**: 20-40% token reduction  
**Installation**: `pip install nova-prompt-optimizer`  
**Requirements**: AWS credentials, test dataset  

### Bedrock Prompt Caching
**When**: Repeated large prompts  
**How**: Add cacheControl to API calls  
**Benefits**: 90% discount on cached tokens  
**Cost Impact**: Massive savings for repeated context  

## Cost Optimization Strategies

### 1. Model Selection
**Cost Impact**: Claude Opus costs ~10x more than Haiku per token.

**Optimization**:
- Use Haiku for simple tasks (classification, extraction)
- Use Sonnet for balanced performance
- Reserve Opus for complex reasoning

**Example**:
```python
# Before: Using Opus for everything
model = "anthropic.claude-3-opus-20240229-v1:0"  # $15/1M input tokens

# After: Use appropriate model for task
model = "anthropic.claude-3-5-haiku-20241022-v1:0"  # $1/1M input tokens
```

**Savings**: 93% cost reduction for simple tasks

### 2. Token Optimization
**Cost Impact**: Tokens are the billing unit - every token counts.

**Optimization**:
- Reduce `max_tokens` to actual needs
- Compress system prompts
- Use prompt caching for repeated context

**Example**:
```python
# Before: Over-provisioned
max_tokens = 8000  # "Just in case"

# After: Right-sized
max_tokens = 2000  # Actual average response length
```

**Savings**: 75% reduction in potential token costs

### 3. Streaming vs Synchronous
**Cost Impact**: Same token cost, but better UX and timeout handling.

**When to use streaming**:
- Long responses (>1000 tokens)
- Interactive applications
- Need to show progress

**When to use synchronous**:
- Short responses (<500 tokens)
- Batch processing
- Simpler error handling

## Supported Languages

- **Python**: Full support for boto3 patterns
- **TypeScript/JavaScript**: AWS SDK v3 patterns
- **Configuration files**: YAML/JSON with Bedrock configs

## Example Code Patterns

### Python (boto3)
```python
import boto3

bedrock = boto3.client('bedrock-runtime')

# Detected: Model usage, API call pattern, token config
response = bedrock.invoke_model(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=json.dumps({
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}]
    })
)
```

### TypeScript (AWS SDK v3)
```typescript
import { BedrockRuntimeClient, InvokeModelCommand } from "@aws-sdk/client-bedrock-runtime";

const client = new BedrockRuntimeClient({ region: "us-east-1" });

// Detected: Model usage, API call pattern
const response = await client.send(new InvokeModelCommand({
  modelId: "anthropic.claude-3-haiku-20240307-v1:0",
  body: JSON.stringify({
    max_tokens: 2000,
    messages: [{ role: "user", content: prompt }]
  })
}));
```

### Python (OpenAI SDK with Bedrock)
```python
from openai import OpenAI

# OpenAI SDK configured for Bedrock Runtime
client = OpenAI(
    base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1", 
    api_key="$AWS_BEARER_TOKEN_BEDROCK"
)

# Detected: API call pattern (openai_compatible), model usage
completion = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[
        {"role": "developer", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt}
    ]
)
```

## Integration with AWS Pricing MCP

The Bedrock detector provides structured findings that can be enriched with real-time pricing:

1. **Scanner detects**: "Using Claude Sonnet with 5000 tokens"
2. **AWS Pricing MCP provides**: "$0.003 per 1K input tokens"
3. **Combined insight**: "This call costs ~$0.015 per invocation"

## Next Steps

- Review detected models and consider cheaper alternatives
- Analyze token configurations and right-size limits
- Identify large static prompts for optimization
- Use AWS Pricing MCP to calculate actual costs

## Prompt Routing

### Overview

Amazon Bedrock's Prompt Routing feature automatically optimizes model selection based on prompt complexity, routing simple prompts to cheaper models and complex prompts to more capable models.

**How it works:**
1. Create a prompt router in AWS Console
2. Configure quality vs cost trade-off
3. Replace model IDs with router ARN
4. Router automatically selects optimal model per request

### Detection Capabilities

#### 1. Existing Routing (Positive Feedback)

Detects when code already uses a prompt router ARN:

```python
router_arn = "arn:aws:bedrock:us-east-1:123456789012:prompt-router/abc123"
response = bedrock.invoke_model(modelId=router_arn, body=body)
```

**Finding Type:** `prompt_routing_detected`

**Output:**
- ✅ Confirms routing is enabled
- Provides monitoring best practices
- Suggests CloudWatch metrics to track
- Links to documentation

#### 2. Multiple Models from Same Family

Detects when code uses multiple different models from the same family, suggesting routing as an alternative to manual model selection.

**Examples:**
- Claude Sonnet + Claude Haiku
- Nova Premier + Nova Lite + Nova Micro

**Finding Type:** `prompt_routing_opportunity` (subtype: `multiple_models_same_family`)

**Example Finding:**
```json
{
  "type": "prompt_routing_opportunity",
  "subtype": "multiple_models_same_family",
  "model_family": "nova",
  "models_detected": ["us.amazon.nova-premier-v1", "us.amazon.nova-lite-v1", "us.amazon.nova-micro-v1"],
  "tiers_detected": ["Premier", "Lite", "Micro"],
  "potential_savings": "30-50% by routing simple prompts to cheaper models"
}
```

#### 3. Mixed Complexity Prompts

Detects when code uses premium or ultra-premium tier models with prompts of varying complexity.

**Trigger Conditions:**
- Using premium tier (Sonnet, Nova Pro) OR ultra-premium tier (Opus, Nova Premier, Claude 4+)
- Multiple prompts detected with complexity range ≥ 2 (on 1-5 scale)

**Complexity Scale:**
- 1: Simple (summarize, list, extract)
- 2: Moderate (explain, describe)
- 3: Complex (analyze, compare)
- 4: Very complex (detailed analysis, multiple perspectives)
- 5: Extremely complex (research-level, edge cases)

**Finding Type:** `prompt_routing_opportunity` (subtype: `mixed_complexity_prompts`)

**Example Finding:**
```json
{
  "type": "prompt_routing_opportunity",
  "subtype": "mixed_complexity_prompts",
  "current_model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
  "current_tier": "Sonnet",
  "complexity_variation": {
    "min": 1,
    "max": 5,
    "range": 4,
    "prompt_count": 2
  },
  "potential_savings": "50%+ for simple prompts routed to cheaper models"
}
```

**Cost Impact:** Simple prompts could use cheaper models (50%+ savings) while complex prompts stay on premium models.

### When Routing is NOT Suggested

- Already using routing (router ARN detected)
- Uniform complexity (all prompts similar, range < 2)
- Cost-effective tiers (Haiku, Nova Lite/Micro already cheap)
- Single prompt detected

### Real-World Example

**Investment Analyst Project:**
```python
# Detected: Multiple Nova models with manual selection logic
if model_id == 'us.amazon.nova-lite-v1:0':
    response = bedrock_agent_runtime.invoke_agent(agentId=lite_agent_id, ...)
elif model_id == 'us.amazon.nova-micro-v1:0':
    response = bedrock_agent_runtime.invoke_agent(agentId=micro_agent_id, ...)
elif model_id == 'us.amazon.nova-premier-v1:0':
    response = bedrock_agent_runtime.invoke_agent(agentId=premier_agent_id, ...)
```

**Scanner Output:**
```json
{
  "type": "prompt_routing_opportunity",
  "subtype": "multiple_models_same_family",
  "model_family": "nova",
  "models_detected": ["us.amazon.nova-premier-v1", "us.amazon.nova-lite-v1", "us.amazon.nova-micro-v1"],
  "description": "Multiple nova models detected: Premier, Lite, Micro",
  "issue": "Manual model selection logic detected",
  "cost_consideration": "Using multiple nova models suggests conditional logic for model selection. Prompt Routing can automate this and optimize costs by automatically selecting the best model for each request.",
  "potential_savings": "30-50% by routing simple prompts to cheaper models"
}
```

### Benefits

1. **Automatic Cost Optimization** - No manual model selection needed
2. **Maintains Quality** - Complex tasks still use powerful models
3. **Reduces Cost** - Simple tasks use cheaper models (30-50% savings)
4. **Future-Proof** - Works with new models as AWS adds them
5. **No Code Changes** - Just replace model ID with router ARN

### Setup Steps

1. Create prompt router in AWS Bedrock Console
2. Configure routing criteria (balance quality vs cost)
3. Replace model IDs with router ARN in code
4. Monitor routing decisions via CloudWatch

### Documentation

- [AWS Bedrock Prompt Routing](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html)
- [CloudWatch Metrics for Routing](https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring-cloudwatch.html)

## Related Documentation

- [Prompt Engineering](prompt-engineering.md) - Generic prompt optimization techniques
- [AgentCore Runtime](agentcore-runtime.md) - Lifecycle configuration and deployment patterns
