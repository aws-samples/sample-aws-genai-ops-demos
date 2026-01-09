# VSC Detector: Prompt Analysis Enhancement

> **Note:** This content has been consolidated into [VSC_DETECTOR.md](VSC_DETECTOR.md#prompt-analysis-enhancement). This file is kept for reference but may be removed in future versions.

## Problem

The VSC detector was only finding `json.dumps()` calls near LLM API invocations, but missing a major opportunity: **JSON schemas and examples embedded directly in prompts**.

### Example Missed Pattern

```python
agent = Agent(
    model=bedrock_model,
    system_prompt=f"""Extract EOL information.
    
    Return data in this JSON format:
    {{
        "service": "string",
        "cycle": "string",
        "lts": "bool",
        "releaseDate": "YYYY-MM-DD",
        "eol": "YYYY-MM-DD"
    }}
    """
)
```

**Problem:** This JSON schema is sent to the LLM on **every request**, wasting tokens repeatedly.

## Solution: Prompt Analysis

Enhanced the VSC detector to:

1. **Parse system_prompt arguments** in Agent() calls
2. **Detect JSON patterns** in prompt text:
   - JSON object examples: `{"key": "value"}`
   - JSON schema field definitions: `- "fieldName": description`
   - Repetitive key patterns (tabular data indicators)
3. **Find variables in prompts** that might contain JSON
4. **Estimate token savings** from converting to VSC format (up to 75%)

## What We Detect

### 1. JSON Schemas in Prompts

**Pattern:**
```python
system_prompt="""
Return data in this format:
{
    "field1": "type1",
    "field2": "type2"
}
"""
```

**Detection:** Finds JSON object patterns in prompt text

### 2. JSON Field Definitions

**Pattern:**
```python
system_prompt="""
Include these fields:
- "service": Name of the service
- "version": Version number
- "releaseDate": Release date
"""
```

**Detection:** Finds repeated field definition patterns (3+ fields)

### 3. Repetitive Keys (Tabular Data)

**Pattern:**
```python
system_prompt="""
Example:
{"id": 1, "name": "Alice", "role": "admin"}
{"id": 2, "name": "Bob", "role": "user"}
"""
```

**Detection:** Finds keys that appear 3+ times (indicates tabular structure)

### 4. Variables in Prompts

**Pattern:**
```python
data_json = json.dumps(data)
system_prompt=f"Process this: {data_json}"
```

**Detection:** Finds f-string variables that might contain JSON

## Implementation Details

### New Method: `_analyze_prompts_for_json()`

Analyzes extracted prompts for JSON patterns:

```python
def _analyze_prompts_for_json(self, analyzer, file_path):
    for prompt_info in analyzer.prompts:
        # Find JSON patterns in prompt text
        json_patterns = self._find_json_patterns_in_text(prompt_info['text'])
        
        if json_patterns:
            # Calculate token savings
            estimated_tokens = total_chars // 4
            vsc_savings = int(estimated_tokens * 0.70)  # 70% savings
            
            # Generate finding
            findings.append({
                'type': 'json_schema_in_prompt',
                'estimated_token_savings': vsc_savings,
                ...
            })
```

### Enhanced AST Visitor

Added prompt tracking to `PythonVscAnalyzer`:

```python
def visit_Call(self, node):
    # NEW: Check for Agent creation
    if 'Agent' in call_name:
        self._extract_prompts_from_agent(node)

def _extract_prompts_from_agent(self, node):
    # Extract system_prompt keyword argument
    for keyword in node.keywords:
        if keyword.arg == 'system_prompt':
            prompt_text = self._extract_string_value(keyword.value)
            self.prompts.append({
                'type': 'system_prompt',
                'text': prompt_text,
                ...
            })
```

### String Extraction

Handles various Python string formats:

- **Simple strings:** `"text"`
- **f-strings:** `f"text {variable}"`
- **Triple-quoted:** `"""text"""`
- **Concatenation:** `"part1" + "part2"`

## Example Output

### EOLTracker Detection

```json
{
  "type": "json_schema_in_prompt",
  "file": "EOLMcpAgent.py",
  "line": 398,
  "prompt_type": "system_prompt",
  "description": "JSON schema/example embedded in system_prompt. This is sent to LLM on every request.",
  "cost_consideration": "JSON schemas in prompts waste tokens on every request. Estimated ~159 tokens could be reduced to ~48 with VSC format.",
  "optimization": {
    "technique": "VSC format for schema definition",
    "potential_savings": "~111 tokens per request (70% reduction)",
    "implementation": "Replace JSON schema with VSC format in prompt",
    "use_when": "Flat schema, known structure on both sides",
    "example": "
# JSON Schema in Prompt (verbose):
{
    \"service\": \"string\",
    \"cycle\": \"string\",
    \"lts\": \"bool\",
    \"releaseDate\": \"YYYY-MM-DD\",
    \"eol\": \"YYYY-MM-DD\"
}

# VSC Schema in Prompt (hyper-minimal):
service,cycle,lts,releaseDate,eol

# Schema known by both sides, no structure needed
"
  },
  "estimated_token_savings": 71,
  "json_patterns_found": 1
}
```

## Benefits

### 1. Catches Real-World Patterns

- ✅ JSON schemas in system prompts (EOLTracker case)
- ✅ JSON examples in prompts
- ✅ Field definitions in prompts
- ✅ Variables containing JSON

### 2. Accurate Token Estimates

- Analyzes actual prompt content
- Counts JSON pattern characters
- Estimates tokens (chars / 4)
- Calculates 70% VSC savings

### 3. Actionable Recommendations

- Shows exact VSC format alternative
- Provides implementation guidance
- Includes before/after examples
- Estimates per-request savings

## Testing

Created comprehensive tests in `tests/test_vsc_prompt_detection.py`:

- ✅ JSON schema in system_prompt
- ✅ JSON field definitions
- ✅ No false positives without JSON
- ✅ Variables in prompts
- ✅ EOLTracker real-world pattern
- ✅ Multiple JSON patterns
- ✅ f-string extraction

**All 7 tests pass** ✅

## Real-World Impact

### Before Enhancement

```
Scan EOLTracker project
→ No VSC opportunities found
```

**Missed:** 111 tokens per request savings opportunity

### After Enhancement

```
Scan EOLTracker project
→ Found JSON schema in system_prompt
→ Estimated savings: ~111 tokens per request (70% reduction)
→ Recommendation: Replace JSON schema with VSC format
```

**Detected:** Real cost optimization opportunity!

## Token Savings Calculation

### EOLTracker Example

**JSON Schema in Prompt:**
```json
{
    "service": "string",
    "cycle": "string",
    "lts": "bool",
    "releaseDate": "YYYY-MM-DD",
    "supportEndDate": "YYYY-MM-DD",
    "eol": "YYYY-MM-DD",
    "latest": "string",
    "link": "url"
}
```

- **Characters:** ~636
- **Estimated tokens:** ~159 (chars / 4)
- **VSC savings:** ~111 tokens (70%)
- **Per request:** 111 tokens saved
- **Monthly (1000 requests):** 111,000 tokens saved
- **Cost savings:** Depends on model pricing

## Future Enhancements

Potential improvements:

1. **Detect user prompts** (not just system_prompt)
2. **Analyze prompt templates** (Jinja2, etc.)
3. **Track prompt variables** more accurately
4. **Suggest specific VSC format** based on schema
5. **Calculate actual cost savings** using model pricing

## Related Documentation

- [VSC Detector](VSC_DETECTOR.md) - VSC detector documentation
- [Prompt Engineering Detector](prompt-engineering.md) - Related prompt analysis
- [VSC Format](https://en.wikipedia.org/wiki/Comma-separated_values) - CSV/VSC principles

## Summary

The enhanced VSC detector now catches a critical pattern that was previously missed: JSON schemas embedded in prompts. This is especially important for agents that use structured output formats, as these schemas are sent on every request, wasting tokens repeatedly.

**Key Achievement:** Can detect up to 70% token savings per request in prompts with embedded JSON schemas.
