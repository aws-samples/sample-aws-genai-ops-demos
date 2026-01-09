# Cross-Cutting Cost Patterns

This scanner detects cost optimization opportunities that span multiple AWS services. These patterns emerge when combining services in ways that amplify costs.

## What Are Cross-Cutting Patterns?

Individual service usage may be optimized, but their combination can create unexpected cost impacts. The scanner correlates findings across services to identify these scenarios.

## Detected Patterns

### 1. Bedrock Streaming in AgentCore Runtime

**Pattern**: Using Bedrock streaming responses (`invoke_model_with_response_stream`) within AgentCore Runtime.

**Why It Matters**:
- **Bedrock**: Streaming vs synchronous has same token cost
- **AgentCore Runtime**: Charges by compute time (RAM × duration)
- **Combined**: Streaming extends response time → longer AgentCore billing

**Example Detection**:
```json
{
  "type": "cross_service_cost_impact",
  "file": "src/agent.py",
  "services": ["bedrock", "bedrock-agentcore"],
  "pattern": "streaming_in_agentcore_runtime",
  "severity": "medium",
  "cost_consideration": "Bedrock streaming responses in AgentCore Runtime extend compute billing time. While streaming improves user experience, it keeps the runtime active longer.",
  "optimization_questions": [
    "Does the user need to see responses in real-time?",
    "Could responses be batched or returned synchronously?",
    "Is the extended compute time worth the UX improvement?",
    "For long responses, is streaming necessary, or would pagination work?"
  ]
}
```

**Cost Impact Example**:

Scenario: AgentCore Runtime with 2GB RAM, $0.10/GB-hour

| Response Type | Response Time | AgentCore Cost | When to Use |
|---------------|---------------|----------------|-------------|
| **Synchronous** | 5 seconds | $0.00028 | Batch processing, APIs, short responses |
| **Streaming** | 30 seconds | $0.00167 | Interactive UX, long responses, real-time feedback |

**Difference**: Streaming costs 6x more in AgentCore compute time for the same response.

**When Streaming Is Worth It**:
- ✅ Interactive chat applications
- ✅ Long responses (>1000 tokens) where users need progress
- ✅ Real-time feedback is critical to UX
- ✅ Users would abandon if waiting for full response

**When Synchronous Is Better**:
- ✅ Batch processing workflows
- ✅ API endpoints with no human waiting
- ✅ Short responses (<500 tokens)
- ✅ Cost is primary concern

## How Detection Works

### 1. Individual Detection
Each detector identifies patterns independently:
- **BedrockDetector**: Finds streaming API calls
- **AgentCoreDetector**: Finds AgentCore Runtime usage

### 2. Correlation Analysis
The scanner's `_correlate_findings()` method:
1. Groups findings by file
2. Identifies service combinations
3. Adds cross-cutting insights

### 3. Structured Output
Provides context without prescribing solutions:
- **Pattern identified**: What combination was found
- **Cost consideration**: Why it matters
- **Optimization questions**: Help you evaluate your use case
- **Context**: Background information

## Design Philosophy

Following our [Design Principles](DESIGN_PRINCIPLES.md):

### ❌ What We DON'T Do
```python
# Bad: Hardcoded recommendation
if has_streaming and has_agentcore:
    return "Don't use streaming in AgentCore - it costs too much"
```

### ✅ What We DO
```python
# Good: Provide context and questions
return {
    "pattern": "streaming_in_agentcore_runtime",
    "cost_consideration": "Streaming extends compute time",
    "optimization_questions": [
        "Is real-time streaming necessary?",
        "Could synchronous responses work?"
    ]
}
```

**Why**: The "right" choice depends on your use case. We provide facts and questions to help you decide.

## Future Cross-Cutting Patterns

The correlation framework can detect additional patterns:

### Potential Future Patterns
- **Large prompts + High-frequency invocations**: Caching opportunity
- **Multiple model calls in sequence**: Batch processing opportunity
- **Bedrock + Lambda cold starts**: Provisioned concurrency consideration
- **AgentCore + Long async tasks**: Consider separate compute service

## Example Code

### Detected Pattern
```python
from bedrock_agentcore import BedrockAgentCoreApp
import boto3

app = BedrockAgentCoreApp()
bedrock = boto3.client('bedrock-runtime')

@app.entrypoint
async def streaming_agent(payload):
    # This combination triggers cross-cutting detection
    response = bedrock.invoke_model_with_response_stream(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=body
    )
    
    for event in response['body']:
        yield event  # Streaming in AgentCore context
```

### Scanner Output
```json
{
  "findings": [
    {
      "type": "bedrock_api_call",
      "pattern": "streaming",
      "service": "bedrock"
    },
    {
      "type": "agentcore_app_detected",
      "service": "bedrock-agentcore"
    },
    {
      "type": "cross_service_cost_impact",
      "pattern": "streaming_in_agentcore_runtime",
      "services": ["bedrock", "bedrock-agentcore"],
      "optimization_questions": [
        "Does the user need to see responses in real-time?",
        "Could responses be batched or returned synchronously?"
      ]
    }
  ]
}
```

## Integration with Other Tools

### With AWS Pricing MCP
1. **Scanner detects**: Streaming in AgentCore
2. **AWS Pricing MCP provides**: AgentCore compute costs
3. **Combined insight**: "Streaming adds $X per invocation in compute time"

### With AWS Documentation MCP
1. **Scanner detects**: Pattern combination
2. **AWS Docs MCP provides**: Best practices for streaming
3. **Combined insight**: "AWS recommends streaming for responses >1000 tokens"

## Testing

The scanner includes tests for cross-cutting detection:
- ✅ Detects streaming + AgentCore combination
- ✅ Doesn't alert on streaming alone
- ✅ Doesn't alert on AgentCore alone
- ✅ Provides structured optimization questions

See `tests/test_cross_cutting.py` for implementation.

## Summary

Cross-cutting patterns reveal cost impacts that aren't obvious when looking at services individually. The scanner:
- **Detects** service combinations automatically
- **Provides** context about cost implications
- **Asks** questions to help you evaluate trade-offs
- **Doesn't prescribe** solutions (you know your use case best)

This approach stays maintainable as AWS evolves while providing actionable insights for cost optimization.
