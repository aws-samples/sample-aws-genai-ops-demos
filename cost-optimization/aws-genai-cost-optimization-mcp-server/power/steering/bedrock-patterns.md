# Bedrock Patterns Guide

Detailed workflows for Bedrock model detection, prompt caching, and prompt routing optimization.

## Model Detection

The scanner detects ALL Bedrock models using generic pattern matching (future-proof):

**Supported Providers:**
- Anthropic (Claude 3.x, 3.5, 4.x)
- Amazon (Nova Micro, Lite, Pro, Premier, Titan)
- Meta (Llama variants)
- Mistral (all variants)
- Cohere (Command, Embed)

**Detection includes:**
- Model ID extraction
- Provider/family/version parsing
- Tier classification (cost-effective, premium, ultra-premium)
- Cross-region prefix detection (global, us, eu, apac)

## Prompt Caching (90% Savings)

### When to Use
- Large static prompts (>1000 tokens)
- Repeated context across calls
- System prompts with instructions

### Implementation Workflow

1. **Scan for opportunities:**
   ```
   scan_project("/path/to/project")
   ```

2. **Look for findings:**
   - `nova_explicit_caching_opportunity`
   - `missing_prompt_caching`
   - `repeated_prompt_context`

3. **Implement caching:**

   **Anthropic Claude (cacheControl):**
   ```python
   messages = [{
       "role": "user",
       "content": [
           {
               "type": "text",
               "text": "Large static instructions...",
               "cache_control": {"type": "ephemeral"}
           },
           {
               "type": "text", 
               "text": f"Dynamic: {data}"
           }
       ]
   }]
   ```

   **Amazon Nova (cachePoint):**
   ```python
   system_prompt = [
       {"text": "Large static instructions..."},
       {"cachePoint": {"type": "default"}},
       {"text": f"Dynamic: {variable}"}
   ]
   ```

### Cost Calculation

| Scenario | Without Caching | With Caching | Savings |
|----------|-----------------|--------------|---------|
| 100 calls × 1000 tokens | 100,000 tokens | 10,900 tokens | 89% |
| 1000 calls × 800 tokens | 800,000 tokens | 80,720 tokens | 90% |

## Prompt Routing (30-50% Savings)

### When Detected

1. **Multiple models from same family:**
   - Using Claude Sonnet + Claude Haiku
   - Using Nova Premier + Nova Lite + Nova Micro

2. **Mixed complexity prompts:**
   - Premium tier model with varying prompt complexity
   - Simple tasks that could use cheaper models

### Implementation Workflow

1. **Scan for opportunities:**
   ```
   scan_project("/path/to/project")
   ```

2. **Look for findings:**
   - `prompt_routing_opportunity` (subtype: `multiple_models_same_family`)
   - `prompt_routing_opportunity` (subtype: `mixed_complexity_prompts`)
   - `prompt_routing_detected` (already using routing ✅)

3. **Implement routing:**
   ```python
   # Before: Manual model selection
   if simple_task:
       model = "anthropic.claude-3-haiku"
   else:
       model = "anthropic.claude-3-sonnet"
   
   # After: Prompt router handles selection
   router_arn = "arn:aws:bedrock:us-east-1:123456789012:prompt-router/abc123"
   response = bedrock.invoke_model(modelId=router_arn, body=body)
   ```

### Setup Steps

1. Create prompt router in AWS Bedrock Console
2. Configure routing criteria (quality vs cost balance)
3. Replace model IDs with router ARN
4. Monitor via CloudWatch metrics

## Cross-Region Caching Anti-Pattern

### The Problem

Using prompt caching with global/cross-region inference profiles can **INCREASE costs by 50%+** instead of reducing them.

**Why:** Caches are region-specific. Global profiles route to different regions, causing cache misses.

### Detection

The scanner flags:
- 🔴 HIGH RISK: Global inference profile + caching
- 🟡 MEDIUM RISK: Geography-specific profile + caching
- ✅ SAFE: Single-region model + caching

### Fix

```python
# ❌ Bad: Global profile with caching
model_id = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.anthropic.claude-3-sonnet"

# ✅ Good: Single-region model with caching
model_id = "anthropic.claude-3-sonnet-20240229-v1:0"
```

## OpenAI SDK Compatibility

The scanner detects OpenAI SDK usage with Bedrock endpoints:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
    api_key="$AWS_BEARER_TOKEN_BEDROCK"
)

# Detected as bedrock_api_call with api_style: "openai_compatible"
completion = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

## Service Tier Detection

The scanner detects missing `service_tier` parameter:

```python
# Detected: Missing service_tier (using default)
response = bedrock.converse(
    modelId="anthropic.claude-3-sonnet",
    messages=[...]
)

# Optimized: Explicit flex tier for non-urgent workloads
response = bedrock.converse(
    modelId="anthropic.claude-3-sonnet",
    messages=[...],
    service_tier="flex"  # Lower cost, higher latency
)
```

## Best Practices

1. **Use prompt caching** for any static content >1000 tokens
2. **Consider prompt routing** when using multiple model tiers
3. **Avoid global profiles** when using caching
4. **Right-size models** - use Haiku/Nova Micro for simple tasks
5. **Monitor with CloudWatch** after implementing optimizations
