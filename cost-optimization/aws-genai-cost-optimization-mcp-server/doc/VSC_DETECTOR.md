# VSC Format Optimization Detector

## Overview

The VSC detector identifies opportunities to use VSC (Values Separated by Comma) instead of JSON for maximum token efficiency in LLM applications.

## What is VSC?

VSC (Values Separated by Comma) is a hyper-minimal, token-optimized format that eliminates ALL structural overhead. No keys, no nesting, no metadata, no structure - just pure values, comma-separated.

**Token Savings:**
- Up to 75% vs JSON

### JSON vs VSC Comparison

**JSON (89 tokens):**
```json
{
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"},
    {"id": 3, "name": "Charlie", "role": "user"}
  ]
}
```

**VSC (22 tokens - 75% savings):**
```
1,Alice,admin
2,Bob,user
3,Charlie,user
```

Schema known by both sides: `id,name,role`

## Detection Patterns

The VSC detector identifies:

1. **JSON Serialization Near LLM Calls**
   - `json.dumps()` or `JSON.stringify()` within 10 lines of LLM API calls
   - Estimates token savings potential (up to 75%)
   - Provides VSC conversion recommendations

2. **JSON Schemas in Prompts**
   - JSON object examples in system prompts: `{"key": "value"}`
   - JSON schema field definitions: `- "fieldName": description`
   - Repetitive key patterns (tabular data indicators)
   - Variables in prompts that might contain JSON

3. **Repetitive Data Structures**
   - List comprehensions creating dictionaries
   - Arrays of objects with repeated keys
   - Flat, tabular data being sent to LLMs

### Prompt Analysis Enhancement

The detector analyzes system prompts for embedded JSON patterns that waste tokens on every request:

**Example:**
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

**Finding:** `json_schema_in_prompt` - Estimates 70% token savings by converting JSON schema to VSC format in the prompt.

## Supported Languages

- **Python:** Detects `json.dumps()`, `json.loads()`, `.to_json()`
- **JavaScript/TypeScript:** Detects `JSON.stringify()`, `JSON.parse()`

## Cost Impact

### Real-World Example (LifeCycleApi)

**Detected:** 3 instances of `json.dumps(agent_payload)` before `invoke_agent_runtime()`

**Savings per call:**
- JSON: ~100 tokens
- VSC: ~25 tokens
- **Reduction: 75 tokens (75%)**

**Monthly impact (1000 calls):**
- Before: 100,000 tokens × $0.00035/1K = $0.035
- After: 25,000 tokens × $0.00035/1K = $0.009
- **Savings: $0.026/month (74% cost reduction)**

## When to Use VSC

### ✅ Ideal Use Cases

1. **Flat, Uniform Data**
   - Spreadsheet-like data with consistent structure
   - Database query results (tabular)
   - CSV-like data structures

2. **Known Schemas**
   - Both sender and receiver understand the field order
   - Schema documented or agreed upon
   - High-frequency workflows where schema is stable

3. **High-Volume Workflows**
   - RAG systems processing many documents
   - Agent workflows with repeated data patterns
   - Log analysis and analytics
   - Batch processing data

3. **Large Payloads**
   - Multiple records being sent to LLMs
   - Data-heavy prompts
   - Structured context for AI agents

### ❌ When NOT to Use VSC

1. **Nested/Complex Structures**
   - Deep object hierarchies
   - Varying schemas per object
   - Non-flat data structures

2. **Interoperability**
   - APIs expecting JSON
   - Standard REST endpoints
   - Third-party integrations requiring specific formats

3. **Small Payloads**
   - Single objects
   - < 50 tokens total
   - Minimal repetition

4. **Unknown or Dynamic Schemas**
   - Schema changes frequently
   - Receiver doesn't know field order
   - Self-describing data needed

## Implementation

### Python

```python
# Convert data to VSC format
users = [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"}
]

# Manual VSC conversion (no library needed!)
def to_vsc(data, fields):
    """Convert list of dicts to VSC format."""
    lines = []
    for item in data:
        values = [str(item[field]) for field in fields]
        lines.append(','.join(values))
    return '\n'.join(lines)

# Instead of: json.dumps(users)
vsc_data = to_vsc(users, ['id', 'name', 'role'])
# Result: "1,Alice,admin\n2,Bob,user"

# Send to Bedrock with schema context
response = bedrock.converse(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    messages=[{
        "role": "user",
        "content": [{"text": f"Analyze this data (schema: id,name,role):\n{vsc_data}"}]
    }]
)
```

### JavaScript/TypeScript

```typescript
// Manual VSC conversion (no library needed!)
function toVSC(data: any[], fields: string[]): string {
  return data
    .map(item => fields.map(field => item[field]).join(','))
    .join('\n');
}

const users = [
  {id: 1, name: "Alice", role: "admin"},
  {id: 2, name: "Bob", role: "user"}
];

// Instead of: JSON.stringify(users)
const vscData = toVSC(users, ['id', 'name', 'role']);
// Result: "1,Alice,admin\n2,Bob,user"

// Send to Bedrock with schema context
const response = await bedrock.converse({
  modelId: "anthropic.claude-3-sonnet-20240229-v1:0",
  messages: [{
    role: "user",
    content: [{ text: `Analyze this data (schema: id,name,role):\n${vscData}` }]
  }]
});
```

## Detection Output

### Finding Structure

```json
{
  "type": "json_serialization_near_llm_call",
  "file": "extraction_api.py",
  "line": 119,
  "service": "bedrock",
  "description": "json.dumps() used near LLM API call (line 117)",
  "cost_consideration": "JSON serialization adds token overhead. Estimated ~100 tokens could be reduced to ~25 with VSC.",
  "optimization": {
    "technique": "VSC (Values Separated by Comma)",
    "potential_savings": "~75 tokens (up to 75% reduction)",
    "implementation": "Replace json.dumps() with VSC serialization",
    "use_when": "Flat, tabular data with known schema on both sides"
  },
  "estimated_token_savings": 75
}
```

## References

- **VSC Format:** Hyper-minimal, token-optimized format
- **No libraries needed:** Simple comma-separated values
- **Key Principle:** Schema known by both sides, no structural overhead

## Real-World Impact: EOLTracker

**Detected:** JSON schema in system_prompt (line 398)

**JSON Schema in Prompt:**
```json
{
    "service": "string",
    "cycle": "string",
    "lts": "bool",
    "releaseDate": "YYYY-MM-DD",
    "eol": "YYYY-MM-DD"
}
```

**Token Analysis:**
- Characters: ~636
- Estimated tokens: ~159
- VSC savings: ~111 tokens (70%)
- Monthly (1000 requests): 111,000 tokens saved

**VSC Alternative in Prompt:**
```
Schema: service,cycle,lts,releaseDate,eol
```

## Design Principles Alignment

✅ **Dynamic Over Static:** Uses pattern detection, not hardcoded rules
✅ **Composable:** Works alongside other detectors
✅ **Actionable:** Provides specific libraries and implementation guidance
✅ **Cost-Focused:** Directly addresses token efficiency = cost savings
