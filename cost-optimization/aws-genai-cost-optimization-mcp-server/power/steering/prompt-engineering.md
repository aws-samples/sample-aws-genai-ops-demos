# Prompt Engineering Guide

Detailed workflows for prompt optimization techniques applicable to any LLM.

## Overview

This guide covers generic prompt optimization that works with any LLM provider. For Bedrock-specific features (caching, routing), see the bedrock-patterns steering file.

## Detection Techniques

The scanner uses two approaches:

**AST-based (Python only):**
- Analyzes code structure
- Detects function calls and recursion
- Analyzes f-strings for static/dynamic parts

**Regex-based (all languages):**
- Pattern matching on source code
- Finds repeated strings
- Identifies optimization opportunities

## Recurring Prompts with Static Content

### Detection

**Finding type:** `recurring_prompt_with_static_content`

**Pattern:** Functions that build prompts with large static sections called multiple times.

```python
def build_extraction_prompt(data):
    # Large static template (~800 tokens)
    prompt = f'''Extract information from this data.
    
    Follow these instructions:
    1. Parse the data structure
    2. Extract key fields
    3. Return JSON format
    
    DATA: {data}  # ← Only this changes
    
    Return JSON format: {{...}}'''
    return prompt

# Called multiple times
for item in items:
    prompt = build_extraction_prompt(item)  # ← Detected!
    response = llm.call(prompt)
```

### Optimization

**Use prompt caching:**
```python
messages = [{
    "role": "user",
    "content": [
        {
            "type": "text",
            "text": "Large static instructions...",
            "cache_control": {"type": "ephemeral"}  # Cache this
        },
        {
            "type": "text",
            "text": f"Dynamic: {data}"  # Don't cache
        }
    ]
}]
```

**Cost Impact:**
- Without caching: 10 calls × 1000 tokens = 10,000 tokens
- With caching: 1000 + (9 × 100) = 1,900 tokens
- **Savings: 81%**

## LLM Calls in Loops

### Detection

**Finding type:** `llm_api_call_in_loop`

**Pattern:** LLM API calls inside for/while loops.

```python
def process_batch(items):
    results = []
    for item in items:
        # LLM call in loop - detected!
        response = bedrock.converse(
            modelId='anthropic.claude-3-sonnet',
            messages=[{"role": "user", "content": f"Process: {item}"}]
        )
        results.append(response)
    return results
```

### Optimization Options

**Option 1: Batch processing**
```python
# ❌ Bad: Individual calls
for item in items:
    response = llm.call(f"Process: {item}")

# ✅ Good: Single batch call
batch_prompt = "Process these items:\n" + "\n".join(items)
response = llm.call(batch_prompt)
```

**Option 2: Prompt caching**
```python
# If items need individual processing, cache the static instructions
for item in items:
    response = llm.call(
        system="Large static instructions...",  # Cached
        user=f"Process: {item}"  # Dynamic
    )
```

## Repeated Prompt Context

### Detection

**Finding type:** `repeated_prompt_context`

**Pattern:** Same large prompt string appears multiple times in code.

```python
# Same prompt repeated - detected!
prompt1 = "You are a helpful assistant. Analyze this data..."
prompt2 = "You are a helpful assistant. Analyze this data..."
```

### Optimization

**Extract to constant:**
```python
# ❌ Bad: Repeated prompt
def process1(data):
    prompt = "You are a helpful assistant..."
    
def process2(data):
    prompt = "You are a helpful assistant..."

# ✅ Good: Shared constant
SYSTEM_PROMPT = "You are a helpful assistant..."

def process1(data):
    prompt = SYSTEM_PROMPT
    
def process2(data):
    prompt = SYSTEM_PROMPT
```

## Large Prompts

### Detection

**Finding type:** `large_prompt_detected`

**Threshold:** >200 tokens (800 characters)

### Optimization Options

1. **Prompt caching** - Cache static portions
2. **Compression** - Remove redundant instructions
3. **Chunking** - Break into smaller prompts
4. **VSC format** - Use token-efficient data formats

## VSC Format Optimization

### Detection

**Finding type:** `json_serialization_near_llm_call`

**Pattern:** JSON serialization near LLM calls, or JSON schemas in prompts.

### What is VSC?

VSC (Values Separated by Comma) is a hyper-minimal format that eliminates structural overhead.

**Token Savings:** Up to 75% vs JSON

**Example:**
```
JSON: {"users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]}
      (89 tokens)

VSC:  1,Alice,admin
      2,Bob,user
      (22 tokens - 75% savings)
```

### When to Use VSC

✅ **Ideal for:**
- Flat, uniform, spreadsheet-like data
- Known schemas on both sides
- High-frequency workflows (RAG, agents, logs)

❌ **Avoid for:**
- Nested/hierarchical data
- Unknown schemas
- Human-readable output

### Implementation

```python
# ❌ Before: JSON in prompt
data = json.dumps(records)
prompt = f"Analyze this data: {data}"

# ✅ After: VSC format
vsc_data = "\n".join(f"{r['id']},{r['name']},{r['role']}" for r in records)
prompt = f"""Analyze this data (format: id,name,role):
{vsc_data}"""
```

## Nova Optimizer Opportunities

### Detection

**Finding type:** `nova_optimization_opportunity`

**Pattern:** Nova models with prompts that could be optimized.

### Implementation

```python
from nova_prompt_optimizer import optimize_prompt

original_prompt = "Long, unoptimized prompt..."
optimized_prompt = optimize_prompt(
    original_prompt,
    test_dataset=test_data,
    model="amazon.nova-lite-v1:0"
)
# Result: 20-40% token reduction
```

**Installation:** `pip install nova-prompt-optimizer`

## Prompt Quality Opportunities

### Detection

**Finding type:** `prompt_quality_opportunity`

**Pattern:** Complex prompts without chain-of-thought or structured output.

### Optimization

**Use Claude Prompt Improver:**
- Manual tool in Claude Console Workbench
- Adds chain-of-thought instructions
- Adds XML structure
- Reduces retries by improving accuracy

## Cost Impact Examples

### Example 1: Recurring Prompts

**Scenario:** Processing 100 documents with same extraction template

| Approach | Tokens | Cost |
|----------|--------|------|
| Without caching | 100,000 | $0.08 |
| With caching | 10,900 | $0.0087 |
| **Savings** | 89% | $0.07 |

### Example 2: Nova Optimizer

**Scenario:** Optimizing prompts for Nova Lite

| Approach | Avg Tokens | Total (1000 calls) | Cost |
|----------|------------|-------------------|------|
| Before | 500 | 500,000 | $0.175 |
| After (30% reduction) | 350 | 350,000 | $0.1225 |
| **Savings** | 30% | 150,000 | $0.0525 |

### Example 3: VSC Format

**Scenario:** Sending 100 records per request

| Format | Tokens/Request | 1000 Requests | Savings |
|--------|----------------|---------------|---------|
| JSON | 890 | 890,000 | - |
| VSC | 220 | 220,000 | 75% |

## Best Practices

1. **Cache static content** - Any repeated instructions >1000 tokens
2. **Batch when possible** - Combine multiple items into single calls
3. **Extract constants** - Don't repeat prompts in code
4. **Use VSC for data** - When schema is known on both sides
5. **Optimize with tools** - Nova Optimizer, Claude Prompt Improver
6. **Avoid loops** - Batch process instead of individual calls

## Thresholds Reference

| Detection | Threshold |
|-----------|-----------|
| Static content | 50 tokens (200 chars) |
| Large prompt | 200 tokens (800 chars) |
| Repeated context | 2+ occurrences |
| Nova caching minimum | 1,000 tokens |
