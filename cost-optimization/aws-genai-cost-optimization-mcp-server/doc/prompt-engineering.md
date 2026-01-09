# Prompt Engineering Detector

Comprehensive prompt optimization detector that combines AST-based code analysis with regex pattern matching to find cost optimization opportunities.

## Overview

This detector focuses on **generic prompt engineering best practices** that apply to any LLM, not just Bedrock-specific features (which are in `bedrock_detector.py`).

**Detection Techniques:**
- **AST-based** (Python only): Analyzes code structure to find recurring prompts and call patterns
- **Regex-based** (all languages): Pattern matching for repeated content and optimization opportunities

## What It Detects

### 1. Recurring Prompts with Static Content (AST)

**Pattern:** Functions that build prompts with large static sections and are called multiple times.

**Example:**
```python
def build_extraction_prompt(data):
    # Large static template
    prompt = f'''Extract information from this data.
    
    Follow these instructions:
    1. Parse the data structure
    2. Extract key fields
    3. Return JSON format
    
    DATA: {data}  # ← Only this changes
    
    Return JSON format: {{...}}'''  # ← Large static section
    return prompt

# Called multiple times (or in a loop)
for item in items:
    prompt = build_extraction_prompt(item)  # ← Detected!
    response = llm.call(prompt)
```

**Finding:**
```json
{
  "type": "recurring_prompt_with_static_content",
  "function_name": "build_extraction_prompt",
  "call_count": 1,
  "estimated_static_tokens": 200,
  "cost_consideration": "Prompt caching can save 90% on repeated static content",
  "optimization": {
    "technique": "Bedrock Prompt Caching",
    "potential_savings": "90% on cached tokens",
    "documentation": "https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html"
  }
}
```

**Cost Impact:**
- Without caching: 10 calls × 1000 tokens = 10,000 tokens charged
- With caching: 1000 + (9 × 100) = 1,900 tokens charged
- **Savings: 81%**

### 2. LLM API Calls in Loops (AST)

**Pattern:** LLM API calls inside for/while loops.

**Example:**
```python
def process_batch(items):
    results = []
    for item in items:
        # LLM call in loop - potential for caching
        response = bedrock.converse(
            modelId='anthropic.claude-3-sonnet',
            messages=[{"role": "user", "content": f"Process: {item}"}]
        )
        results.append(response)
    return results
```

**Finding:**
```json
{
  "type": "llm_api_call_in_loop",
  "function_name": "process_batch",
  "loop_type": "for",
  "cost_consideration": "LLM calls in loops can result in many repeated API calls. Consider prompt caching if the same context is used across iterations."
}
```

### 3. Repeated Prompt Context (Regex)

**Pattern:** Same large prompt string appears multiple times in the code.

**Example:**
```python
# Same prompt repeated in multiple places
prompt1 = "You are a helpful assistant. Analyze this data..."
prompt2 = "You are a helpful assistant. Analyze this data..."  # ← Duplicate!
```

**Finding:**
```json
{
  "type": "repeated_prompt_context",
  "occurrences": 2,
  "estimated_tokens": 150,
  "cost_consideration": "Repeated prompt context detected. Consider using prompt caching to save 90% on repeated tokens."
}
```

### 4. Prompt Quality Opportunities (Regex)

**Pattern:** Complex prompts without chain-of-thought or structured output.

**Example:**
```python
# Complex prompt without structure
prompt = """Analyze this complex scenario and provide recommendations..."""
```

**Finding:**
```json
{
  "type": "prompt_quality_opportunity",
  "prompt_length": 500,
  "estimated_tokens": 125,
  "cost_consideration": "Complex prompt detected. Consider using Claude Prompt Improver to optimize quality and reduce retries.",
  "tool": {
    "name": "Claude Prompt Improver",
    "url": "https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/prompt-improver"
  }
}
```

### 5. Nova Optimizer Opportunities (Regex)

**Pattern:** Nova models being used with prompts that could be optimized.

**Example:**
```python
model_id = "amazon.nova-lite-v1:0"
prompt = "Long prompt that could be optimized..."
```

**Finding:**
```json
{
  "type": "nova_optimization_opportunity",
  "nova_models": ["amazon.nova-lite-v1:0"],
  "estimated_tokens": 100,
  "cost_consideration": "Nova Prompt Optimizer can automatically test prompt variations to reduce token usage by 20-40%",
  "tool": {
    "name": "AWS Nova Prompt Optimizer",
    "installation": "pip install nova-prompt-optimizer",
    "url": "https://github.com/aws/nova-prompt-optimizer"
  }
}
```

### 6. Token Usage Patterns (Regex)

**Pattern:** Large prompts that could benefit from optimization.

**Example:**
```python
# Very large prompt
prompt = """..."""  # 2000+ characters
```

**Finding:**
```json
{
  "type": "large_prompt_detected",
  "estimated_tokens": 500,
  "cost_consideration": "Large prompt detected. Consider breaking into smaller prompts or using prompt caching."
}
```

## Detection Techniques

### AST-Based Analysis (Python Only)

**How it works:**
1. Parse Python code into Abstract Syntax Tree
2. Find functions that build prompts (name patterns: `.*prompt.*`, `build_.*`, `format_.*`)
3. Analyze f-strings for static vs dynamic content
4. Count function calls to detect recursion
5. Detect LLM API calls inside loops

**Advantages:**
- ✅ Understands code structure
- ✅ Detects function calls and recursion
- ✅ Analyzes f-strings for static/dynamic parts
- ✅ Finds patterns regex can't

**Limitations:**
- ❌ Python-only
- ❌ More complex implementation

### Regex-Based Analysis (All Languages)

**How it works:**
1. Pattern matching on source code text
2. Find repeated strings
3. Detect model usage
4. Identify optimization opportunities

**Advantages:**
- ✅ Fast and simple
- ✅ Works across all languages
- ✅ Good for API patterns

**Limitations:**
- ❌ Can't understand code structure
- ❌ Misses dynamic prompt building

## Separation from Bedrock Detector

### Prompt Engineering Detector (This File)
**Focus:** Generic prompt optimization applicable to any LLM

**Contains:**
- Recurring prompts (AST)
- Repeated context (regex)
- Prompt quality
- Nova optimizer
- LLM calls in loops
- Token patterns

### Bedrock Detector
**Focus:** Bedrock-specific features and API usage

**Contains:**
- Bedrock client, models, API calls
- **Prompt caching** (Bedrock feature)
- **Prompt routing** (Bedrock feature)

**Why this separation?**
- ✅ Clear distinction: Generic optimization vs Bedrock features
- ✅ Prompt caching/routing are Bedrock features (can't use without Bedrock)
- ✅ Prompt engineering applies to any LLM (OpenAI, Anthropic, etc.)
- ✅ Easier to maintain and extend

## Configuration

### Function Name Patterns (AST)

Functions matching these patterns are analyzed as prompt builders:
```python
PROMPT_BUILDER_PATTERNS = [
    r'.*prompt.*',      # Any function with "prompt" in name
    r'.*message.*',     # Any function with "message" in name
    r'build_.*',        # Functions starting with "build_"
    r'format_.*',       # Functions starting with "format_"
    r'generate_.*',     # Functions starting with "generate_"
]
```

### LLM API Patterns (AST)

API calls matching these patterns are tracked:
```python
LLM_API_PATTERNS = [
    'bedrock.converse',
    'bedrock_runtime.converse',
    'bedrock.invoke_model',
    'openai.chat.completions.create',
    'anthropic.messages.create',
]
```

### Thresholds

- **Static content threshold**: 50 tokens (200 characters)
- **Large prompt threshold**: 200 tokens (800 characters)
- **Repeated context threshold**: 2+ occurrences

## Cost Impact Examples

### Example 1: Recurring Prompts

**Scenario:** Processing 100 documents with same extraction template

**Without optimization:**
```
100 calls × 1000 static tokens = 100,000 tokens
Cost: 100,000 × $0.0008/1K = $0.08
```

**With prompt caching:**
```
First call: 1000 tokens × $0.0008/1K = $0.0008
Next 99 calls: 99 × 1000 × $0.00008/1K = $0.00792
Total: $0.00872
Savings: 89% ($0.07128 saved)
```

### Example 2: Nova Optimizer

**Scenario:** Optimizing prompts for Nova Lite

**Before optimization:**
```
Average prompt: 500 tokens
1000 calls × 500 tokens = 500,000 tokens
Cost: 500,000 × $0.00035/1K = $0.175
```

**After optimization (30% reduction):**
```
Average prompt: 350 tokens
1000 calls × 350 tokens = 350,000 tokens
Cost: 350,000 × $0.00035/1K = $0.1225
Savings: 30% ($0.0525 saved)
```

## Best Practices

### 1. Use Prompt Caching for Static Content
```python
# ✅ Good: Mark static content for caching
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Large static instructions...",
                "cache_control": {"type": "ephemeral"}  # Cache this!
            },
            {
                "type": "text",
                "text": f"Dynamic data: {data}"  # Don't cache
            }
        ]
    }
]
```

### 2. Extract Repeated Prompts
```python
# ❌ Bad: Repeated prompt
def process1(data):
    prompt = "You are a helpful assistant..."
    
def process2(data):
    prompt = "You are a helpful assistant..."  # Duplicate!

# ✅ Good: Shared prompt
SYSTEM_PROMPT = "You are a helpful assistant..."

def process1(data):
    prompt = SYSTEM_PROMPT
    
def process2(data):
    prompt = SYSTEM_PROMPT
```

### 3. Optimize Prompts Before Deployment
```python
# Use Nova Prompt Optimizer
from nova_prompt_optimizer import optimize_prompt

original_prompt = "Long, unoptimized prompt..."
optimized_prompt = optimize_prompt(
    original_prompt,
    test_dataset=test_data,
    model="amazon.nova-lite-v1:0"
)
# Result: 20-40% token reduction
```

### 4. Avoid LLM Calls in Tight Loops
```python
# ❌ Bad: LLM call in loop
for item in large_list:
    response = llm.call(f"Process: {item}")

# ✅ Good: Batch processing
batch_prompt = "Process these items:\n" + "\n".join(large_list)
response = llm.call(batch_prompt)
```

## Testing

Run tests:
```bash
pytest tests/test_prompt_engineering_detector.py -v
```

Test coverage:
- ✅ Recurring prompts with static content
- ✅ LLM calls in loops
- ✅ Small prompts (no false positives)
- ✅ Single calls (conservative detection)
- ✅ F-strings with static/dynamic content
- ✅ Python-only analysis
- ✅ Syntax error handling

## Future Enhancements

### Potential Improvements
1. **Call graph analysis** - Trace calls through multiple functions
2. **Loop iteration estimation** - Estimate how many times loops run
3. **TypeScript AST support** - Extend to TypeScript/JavaScript
4. **Smarter thresholds** - Adjust based on actual token costs
5. **Prompt template detection** - Find template engines (Jinja2, etc.)

### Not Planned
- ❌ Runtime analysis (out of scope)
- ❌ Actual token counting (use AWS MCP Server)
- ❌ Code execution (static analysis only)

## Nova Prompt Caching (90% Savings)

Amazon Nova models support **automatic and explicit prompt caching** with significant cost benefits.

### Automatic vs Explicit Caching

**Automatic Caching (Built-in):**
- Always enabled for Nova models
- Provides latency benefits
- No configuration needed

**Explicit Caching (Recommended for Cost):**
- 90% discount on cached tokens
- Requires `cachePoint` markers
- 5-minute TTL (resets on cache hit)

### Nova Model Specifications

| Model | Min Tokens/Checkpoint | Max Checkpoints | Max Cache Tokens |
|-------|----------------------|-----------------|------------------|
| Nova Micro/Lite/Pro/Premier | 1,000 | 4 | 20,000 |

### Implementation Example

**Before (No Caching):**
```python
system_prompt = f"""You are an AWS documentation analyst.

Your task:
1. Read and analyze the provided URL: {service_url}
2. Extract key information...
[~800 tokens of static instructions]
"""
```

**After (With Explicit Caching):**
```python
system_prompt = [
    {
        "text": """You are an AWS documentation analyst.
        
        Your task:
        1. Read and analyze the provided URL
        2. Extract key information...
        [~800 tokens of static instructions]
        """
    },
    {
        "cachePoint": {"type": "default"}  # Cache static instructions
    },
    {
        "text": f"Service URL: {service_url}"  # Dynamic part (not cached)
    }
]
```

**Cost Impact (1000 requests/month):**
- Without caching: 1000 × 800 = 800,000 tokens
- With caching: 800 + (999 × 80) = 80,720 tokens
- **Savings: 719,280 tokens (90%)**

### Best Practices

1. **Cache Point Placement:** Place after static content, before dynamic variables
2. **Minimum Tokens:** Nova requires 1,000 tokens minimum per checkpoint
3. **TTL Management:** 5-minute TTL resets on each cache hit
4. **Priority Order:** System prompts → Document context → Tool definitions

## Related Documentation

- [Bedrock Detector](bedrock-detector.md) - Bedrock-specific features (caching, routing)
- [VSC Detector](VSC_DETECTOR.md) - Token-efficient data formats
- [AgentCore Runtime](agentcore-runtime.md) - AgentCore patterns
- [Cross-Cutting Patterns](cross-cutting-patterns.md) - Multi-service impacts
- [Design Principles](DESIGN_PRINCIPLES.md) - Architecture decisions
