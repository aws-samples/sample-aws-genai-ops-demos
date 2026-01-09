# Generic Bedrock Model Detection

## Problem with Hardcoded Patterns

Previously, the detector used hardcoded patterns for specific model families:

```python
# ❌ OLD APPROACH - Hardcoded and becomes outdated
MODEL_PATTERNS = {
    "claude-3-opus": r"anthropic\.claude-3-opus[^\"']*",
    "claude-3-sonnet": r"anthropic\.claude-3-sonnet[^\"']*",
    "claude-3-haiku": r"anthropic\.claude-3-haiku[^\"']*",
    # ... needs constant updates when AWS releases new models
}
```

**Issues:**
- ❌ Requires updates when AWS releases new models (Claude 4, Claude 4.5, etc.)
- ❌ Misses models we don't know about yet
- ❌ Violates "Dynamic Over Static" design principle
- ❌ Makes wrong recommendations (e.g., suggesting Claude 3.5 when user has 3.7)

## New Approach: Generic Detection + Parsing

### 1. Generic Pattern Matching

Use a **single regex pattern** that matches ANY Bedrock model ID:

```python
# ✅ NEW APPROACH - Generic and future-proof
BEDROCK_MODEL_ID_PATTERN = r'((?:us\.|eu\.|apac\.)?(?:anthropic|amazon|meta|cohere|mistral|ai21|stability|deepseek|openai|qwen|twelvelabs)\.[a-z0-9\-\.]+(?:-v\d+:\d+)?(?::\d+k)?(?::mm)?)'
```

**Matches:**
- ✅ `anthropic.claude-3-7-sonnet-20250219-v1:0`
- ✅ `us.anthropic.claude-sonnet-4-20250514-v1:0` (regional prefix)
- ✅ `global.anthropic.claude-sonnet-4-20250514-v1:0` (cross-region inference)
- ✅ `eu.amazon.nova-pro-v1:0` (EU region)
- ✅ `apac.meta.llama3-70b-instruct-v1:0` (APAC region)
- ✅ `anthropic.claude-sonnet-4-5-20250929-v1:0` (Claude 4.5!)
- ✅ `amazon.nova-pro-v1:0`
- ✅ Future models we don't know about yet!

### 2. Intelligent Parsing

Extract structured information from the model ID:

```python
parsed = {
    "full_model_id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    "provider": "anthropic",
    "family": "claude",
    "version": "3.7",
    "tier": "sonnet",
    "region_prefix": "us"
}
```

### 3. Enrichment Instructions

Provide clear instructions for AI/Kiro to fetch current model information:

```json
{
  "enrichment_instructions": {
    "description": "Use AWS MCP Server to check for newer models and pricing",
    "steps": [
      {
        "tool": "AWS CLI MCP",
        "command": "aws bedrock list-foundation-models --region us-east-1",
        "purpose": "Get list of all available Bedrock models"
      },
      {
        "action": "Filter models",
        "criteria": "provider='anthropic', family='claude', tier='sonnet'",
        "purpose": "Find models in the same family/tier"
      },
      {
        "action": "Compare versions",
        "current_version": "3.7",
        "purpose": "Check if newer version exists"
      },
      {
        "tool": "AWS MCP Server",
        "action": "get_pricing for current and newer models",
        "purpose": "Compare costs if newer model exists"
      }
    ]
  }
}
```

## Workflow: Separation of Concerns

### This MCP Server (Scanner)
**Responsibility:** Detect and parse model IDs

```
1. Scan code for ANY Bedrock model ID
2. Parse model ID → extract provider, family, version, tier
3. Return findings with enrichment instructions
```

### AWS CLI MCP
**Responsibility:** Provide current model catalog

```
1. Fetch list-foundation-models from AWS API
2. Return all available models with metadata
```

### AWS MCP Server
**Responsibility:** Provide pricing data, model catalog, and documentation

```
1. Get pricing for specific model IDs
2. Compare costs between models
```

### Kiro/AI (Orchestrator)
**Responsibility:** Combine data and make recommendations

```
1. Receive findings from scanner
2. Call AWS MCP Server to get current models
3. Compare detected version vs latest available
4. Call AWS MCP Server if newer model exists
5. Make informed recommendation
```

## Example: Claude 3.7 Sonnet Detection

### Step 1: Scanner Detects
```json
{
  "type": "bedrock_model_usage",
  "model_id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
  "parsed": {
    "provider": "anthropic",
    "family": "claude",
    "version": "3.7",
    "tier": "sonnet"
  }
}
```

### Step 2: AI Calls AWS CLI MCP
```bash
aws bedrock list-foundation-models --region us-east-1
```

**Discovers:**
- Claude 3.7 Sonnet (current)
- Claude 4 Sonnet (newer!)
- Claude 4.5 Sonnet (even newer!)

### Step 3: AI Calls AWS MCP Server
```python
get_pricing('AmazonBedrock', filters=[
    {"Field": "modelId", "Value": "claude-3-7-sonnet"},
    {"Field": "modelId", "Value": "claude-4-5-sonnet"}
])
```

### Step 4: AI Makes Recommendation
```
✅ CORRECT: "Upgrade to Claude 4.5 Sonnet (newer + potentially cheaper)"
❌ WRONG: "Downgrade to Claude 3.5 Haiku" (older model!)
```

## Benefits

### 1. Future-Proof
- ✅ Works with models released after this code was written
- ✅ No maintenance needed when AWS releases new models
- ✅ Detects Claude 5, Claude 6, etc. automatically

### 2. Accurate Recommendations
- ✅ Never recommends downgrading to older models
- ✅ Always checks current model catalog before recommending
- ✅ Uses real pricing data, not assumptions

### 3. Follows Design Principles
- ✅ **Dynamic Over Static**: No hardcoded model lists
- ✅ **Composable Architecture**: Works with other MCP servers
- ✅ **Separation of Concerns**: Scanner scans, others enrich

### 4. Fast Scanning
- ✅ No API calls during scanning (stays fast)
- ✅ Enrichment happens only when needed
- ✅ Single regex pattern (simpler than 10+ patterns)

## Testing

See `tests/test_generic_model_detection.py` for comprehensive tests covering:
- ✅ Claude 3.7, 4, 4.5 parsing
- ✅ Nova, Llama, Mistral parsing
- ✅ Multiple models in same file
- ✅ Future model detection
- ✅ Enrichment instructions

## Migration Notes

### Before (Hardcoded)
```python
# Required updates every time AWS released new models
MODEL_PATTERNS = {
    "claude-3-7-sonnet": r"anthropic\.claude-3-7-sonnet[^\"']*",
    # Missing Claude 4, 4.5, etc.
}
```

### After (Generic)
```python
# Works with all current and future models
BEDROCK_MODEL_ID_PATTERN = r'((?:us\.|eu\.|apac\.)?(?:anthropic|amazon|meta|...)\.[a-z0-9\-\.]+...)'
```

### Impact
- ✅ Removed 10+ hardcoded patterns
- ✅ Added 1 generic pattern
- ✅ Added intelligent parser
- ✅ Added enrichment instructions
- ✅ Fixed wrong recommendations (3.7 → 3.5 issue)

## Related Documentation

- [Design Principles](DESIGN_PRINCIPLES.md) - Why we avoid hardcoding
- [Bedrock Detector](bedrock-detector.md) - Full detector documentation
- [Model Recommendation Fix](MODEL_RECOMMENDATION_FIX.md) - Original issue that led to this
