# Example Files

This directory contains sample code files for testing the cost optimization scanner.

## Files

### `sample_bedrock_code.py`
Demonstrates various Bedrock API usage patterns:
- Multiple Claude models (Opus, Sonnet, Haiku)
- Synchronous and streaming API calls
- Token configuration with high limits
- Large static prompts (optimization opportunity)

**Key findings**: Model usage, API call patterns, token limits

### `sample_agentcore_code.py`
Demonstrates AgentCore Runtime patterns:
- App initialization with decorators
- Streaming agent with session management
- Async background tasks
- Custom health checks

**Key findings**: Decorators, session management, streaming, async processing

### `sample_agentcore_lifecycle.py`
Demonstrates lifecycle configuration patterns (CRITICAL FOR COST):
- Cost-optimized configuration (shorter timeouts)
- Extended configuration (higher costs)
- Default configuration (no lifecycle specified)
- Update operations

**Key findings**: Lifecycle configurations with cost analysis

## Testing the Scanner

### Scan a single file:
```bash
python -c "import asyncio; from mcp_cost_optim_genai.scanner import ProjectScanner; scanner = ProjectScanner(); print(asyncio.run(scanner.analyze_file('examples/sample_agentcore_lifecycle.py')))"
```

### Scan the entire examples directory:
```bash
python -c "import asyncio; from mcp_cost_optim_genai.scanner import ProjectScanner; scanner = ProjectScanner(); print(asyncio.run(scanner.scan_project('examples')))"
```

## Understanding Lifecycle Findings

### Cost-Optimized Configuration
```json
{
  "type": "agentcore_lifecycle_idle_timeout",
  "configured_value": 300,
  "default_value": 900,
  "cost_consideration": "Cost optimized: Idle timeout (300s / 5.0min) is lower than default (900s). Instances terminate faster when idle, reducing costs."
}
```
✅ **Good**: Instances terminate after 5 minutes of inactivity instead of 15 minutes.

### Cost Alert Configuration
```json
{
  "type": "agentcore_lifecycle_idle_timeout",
  "configured_value": 3600,
  "default_value": 900,
  "cost_consideration": "COST ALERT: Idle timeout (3600s / 60.0min) is HIGHER than default (900s). Instances stay alive longer when idle, increasing costs."
}
```
⚠️ **Warning**: Instances stay alive for 1 hour when idle, 4x longer than default. This can significantly increase costs.

## Cost Impact Example

**Scenario**: AgentCore Runtime with 2GB RAM, $0.10/GB-hour

| Configuration | Idle Timeout | Cost per Idle Period | Monthly Cost (10 idle periods/day) |
|---------------|--------------|---------------------|-----------------------------------|
| Optimized | 5 minutes | $0.017 | $5.00 |
| Default | 15 minutes | $0.050 | $15.00 |
| Extended | 60 minutes | $0.200 | $60.00 |

**Savings**: Optimizing from extended to default = $45/month per runtime instance.
