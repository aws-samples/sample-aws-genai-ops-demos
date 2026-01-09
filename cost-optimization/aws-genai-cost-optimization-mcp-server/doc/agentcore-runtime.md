# AgentCore Runtime Detector

Detects Amazon Bedrock AgentCore Runtime usage patterns and provides cost optimization insights for deployment, lifecycle configuration, and runtime behavior.

## What It Detects

### 1. Lifecycle Configuration (CRITICAL FOR COST)

AgentCore Runtime charges based on compute time + memory allocation. Lifecycle configuration controls how long instances stay alive, directly impacting costs.

#### Idle Timeout Detection
**What it is**: Time before terminating idle instances (default: 900s / 15 minutes)

**Example Finding:**
```json
{
  "type": "agentcore_lifecycle_idle_timeout",
  "file": "infrastructure/agentcore.py",
  "line": 16,
  "configured_value": 3600,
  "default_value": 900,
  "unit": "seconds",
  "cost_consideration": "COST ALERT: Idle timeout (3600s / 60.0min) is HIGHER than default (900s). Instances stay alive longer when idle, increasing costs."
}
```

**Cost Impact**: 1 hour idle timeout vs 15 minutes = **4x longer billing** for idle instances.

#### Max Lifetime Detection
**What it is**: Maximum instance runtime before forced termination (default: 28800s / 8 hours)

**Example Finding:**
```json
{
  "type": "agentcore_lifecycle_max_lifetime",
  "file": "infrastructure/agentcore.py",
  "line": 17,
  "configured_value": 14400,
  "default_value": 28800,
  "unit": "seconds",
  "cost_consideration": "Cost optimized: Max lifetime (14400s / 4.0h) is lower than default (28800s / 8h). Instances terminate sooner, reducing costs."
}
```

### 2. Session Termination (Cost Optimization Best Practice)

**CRITICAL FOR COST:** Proactive session termination prevents idle timeout charges.

#### StopRuntimeSession API

Detects usage of `StopRuntimeSession` API call to manually terminate sessions.

**Why it matters:**
- Sessions default to 15-minute idle timeout
- Manually stopping sessions eliminates idle time charges
- Best practice: Call after completing agent tasks

**Example (Python):**
```python
import boto3

client = boto3.client('bedrock-agentcore-runtime')

# Terminate session immediately after work completes
response = client.stop_runtime_session(
    agentRuntimeArn='arn:aws:bedrock-agentcore:...',
    sessionId='session-123'
)
```

**Example (TypeScript):**
```typescript
import { BedrockAgentCoreRuntimeClient, StopRuntimeSessionCommand } 
  from "@aws-sdk/client-bedrock-agentcore-runtime";

const client = new BedrockAgentCoreRuntimeClient({ region: "us-east-1" });

const command = new StopRuntimeSessionCommand({
    agentRuntimeArn: runtimeArn,
    sessionId: sessionId
});

await client.send(command);
```

**Example Finding:**
```json
{
  "type": "agentcore_stop_session_detected",
  "file": "src/agent.py",
  "line": 42,
  "description": "✅ EXCELLENT: Proactive session termination detected",
  "cost_consideration": "Manually stopping sessions prevents idle timeout charges. Sessions terminate immediately instead of waiting for idle timeout (default 15min).",
  "benefit": "Eliminates idle time charges by terminating sessions immediately when work is complete",
  "api_reference": "https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_StopRuntimeSession.html"
}
```

**Cost Impact**: Eliminates up to 15 minutes of idle compute charges per session.

### 3. Application Initialization

Detects AgentCore app setup:

**Example Finding:**
```json
{
  "type": "agentcore_app_detected",
  "file": "src/agent.py",
  "service": "bedrock-agentcore",
  "description": "Amazon Bedrock AgentCore application detected",
  "cost_consideration": "AgentCore Runtime charges based on compute time and memory allocation"
}
```

### 3. Decorator Patterns

Detects how agent logic is structured:

#### @app.entrypoint
Main agent logic - compute time charged per invocation.

**Example Finding:**
```json
{
  "type": "agentcore_decorator",
  "file": "src/agent.py",
  "line": 18,
  "decorator_type": "entrypoint",
  "service": "bedrock-agentcore",
  "cost_consideration": "Main agent logic - compute time charged per invocation"
}
```

#### @app.async_task
Background tasks - extends compute time, agent stays in HealthyBusy state.

**Example Finding:**
```json
{
  "type": "agentcore_decorator",
  "file": "src/agent.py",
  "line": 33,
  "decorator_type": "async_task",
  "service": "bedrock-agentcore",
  "cost_consideration": "Background task - extends compute time, agent stays in HealthyBusy state"
}
```

#### @app.ping
Health check endpoint - minimal cost impact.

### 4. Session Management

Detects session handling patterns:

**Example Finding:**
```json
{
  "type": "agentcore_session_management",
  "file": "src/agent.py",
  "line": 22,
  "service": "bedrock-agentcore",
  "description": "Session management detected",
  "cost_consideration": "Sessions timeout after 15 minutes of inactivity. Consider session cleanup for cost optimization."
}
```

### 5. Streaming Responses

Detects streaming patterns:

**Example Finding:**
```json
{
  "type": "agentcore_streaming",
  "file": "src/agent.py",
  "line": 26,
  "service": "bedrock-agentcore",
  "description": "Streaming response pattern detected",
  "cost_consideration": "Streaming responses improve UX but may extend compute time. Consider chunking strategy."
}
```

### 6. Async/Background Processing

Detects long-running tasks:

**Example Finding:**
```json
{
  "type": "agentcore_async_processing",
  "file": "src/agent.py",
  "line": 58,
  "service": "bedrock-agentcore",
  "description": "Async/background processing detected",
  "cost_consideration": "Background tasks keep agent in HealthyBusy state, extending compute time. Monitor task duration."
}
```

### 7. Deployment Patterns

Detects deployment modes:
- **Direct deploy**: Recommended for production, uses managed runtime
- **Local dev**: No cloud costs during development
- **Hybrid build**: Local container build, cloud deployment

### 8. Authentication Patterns

Detects auth configuration:
- **JWT**: Custom JWT authorizer
- **IAM**: IAM SigV4 (default)

## Cost Optimization Strategies

### 1. Lifecycle Configuration (Highest Impact)

**Cost Impact**: Misconfigured lifecycle = 4x-10x higher costs

#### Analyze Your Workload
```python
# Questions to ask:
# - How long do agent invocations take? (e.g., 2 minutes)
# - Time between invocations? (e.g., 5-10 minutes)
# - Traffic pattern? (bursty vs continuous)
```

#### Optimization Examples

**Bursty Traffic** (long idle periods):
```python
lifecycleConfiguration={
    'idleRuntimeSessionTimeout': 300,   # 5 min - terminate quickly
    'maxLifetime': 3600                 # 1 hour
}
```
**Savings**: 67% reduction vs default (15 min idle)

**Continuous Traffic** (short idle periods):
```python
lifecycleConfiguration={
    'idleRuntimeSessionTimeout': 900,   # 15 min - keep alive for next request
    'maxLifetime': 7200                 # 2 hours
}
```
**Savings**: Balanced approach, prevents cold starts

**Long-Running Sessions**:
```python
lifecycleConfiguration={
    'idleRuntimeSessionTimeout': 600,   # 10 min
    'maxLifetime': 14400                # 4 hours
}
```
**Savings**: 50% reduction in max lifetime vs default

#### Cost Calculation Example

**Assumptions**:
- AgentCore Runtime: 2GB RAM
- Pricing: $0.10 per GB-hour
- Workload: 10 invocations/day, 2 min each

| Configuration | Idle Timeout | Active Time | Idle Time | Total Runtime | Daily Cost |
|---------------|--------------|-------------|-----------|---------------|------------|
| **Optimized** | 5 min | 20 min | 50 min | 70 min | $0.23 |
| **Default** | 15 min | 20 min | 150 min | 170 min | $0.57 |
| **Extended** | 60 min | 20 min | 600 min | 620 min | $2.07 |

**Annual Savings** (per runtime):
- Optimized vs Default: $122/year
- Optimized vs Extended: $662/year

### 2. Async Task Management

**Cost Impact**: Background tasks extend compute time

**Optimization**:
- Monitor task duration
- Use `@app.async_task` decorator for proper tracking
- Implement task timeouts
- Consider moving long tasks to separate services (Lambda, Batch)

**Example**:
```python
# Before: Untracked background work
def handler(event):
    threading.Thread(target=long_task).start()  # No visibility
    return {"status": "started"}

# After: Tracked async task
@app.async_task
async def long_task():
    await process_data()  # Status becomes "HealthyBusy"
    return "done"

@app.entrypoint
async def handler(event):
    asyncio.create_task(long_task())
    return {"status": "started"}
```

### 3. Session Management

**Cost Impact**: Orphaned sessions waste resources

**Optimization**:
- Implement explicit session cleanup
- Use session timeouts appropriately
- Monitor active session count

### 4. Streaming Strategy

**Cost Impact**: Streaming extends compute time but improves UX

**When to use streaming**:
- Long responses (>1000 tokens)
- Interactive applications
- Need to show progress

**When to avoid**:
- Short responses (<500 tokens)
- Batch processing
- Cost is primary concern

## Supported Languages & Frameworks

### Python
```python
from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@app.entrypoint
def my_agent(payload):
    return {"result": "Hello"}
```

### TypeScript/JavaScript (CDK)
```typescript
agentRuntime.addPropertyOverride('LifecycleConfiguration', {
  IdleRuntimeSessionTimeout: 300,  // 5 minutes
  MaxLifetime: 1800,               // 30 minutes
});
```

### Configuration Files
```yaml
# CloudFormation/SAM
LifecycleConfiguration:
  IdleRuntimeSessionTimeout: 300
  MaxLifetime: 1800
```

## Detection Across Code Types

The detector works with:
- **Python**: `.py` files with AgentCore SDK
- **TypeScript/JavaScript**: `.ts`, `.tsx`, `.js`, `.jsx` files with CDK
- **Shell scripts**: `.sh`, `.bash` deployment scripts
- **Config files**: `.yml`, `.yaml` CloudFormation/SAM templates

## Best Practices

### ✅ DO
- Start with shorter timeouts and increase if needed
- Monitor actual usage patterns before optimizing
- Use different configurations for different workloads
- Re-scan regularly as your application evolves
- Use `@app.async_task` for background work

### ❌ DON'T
- Set very high timeouts "just in case"
- Use the same configuration for all environments
- Forget to update configurations as usage patterns change
- Ignore cost alerts from the scanner
- Run long tasks without proper tracking

## Monitoring & Iteration

1. **Deploy with conservative settings**
2. **Monitor CloudWatch metrics**:
   - Session duration
   - Idle time percentage
   - Invocation frequency
   - HealthyBusy state duration
3. **Adjust lifecycle configuration** based on actual usage
4. **Re-scan with this tool** to validate optimizations

## Integration with AWS Pricing MCP

The AgentCore detector provides structured findings that can be enriched with real-time pricing:

1. **Scanner detects**: "Idle timeout: 3600s (4x default)"
2. **AWS Pricing MCP provides**: "$0.10 per GB-hour for 2GB runtime"
3. **Combined insight**: "Extra 45 min idle time = $0.15 per invocation"

## Example Scan Output

```json
{
  "status": "success",
  "file": "infrastructure/agentcore.py",
  "total_findings": 6,
  "findings": [
    {
      "type": "agentcore_app_detected",
      "service": "bedrock-agentcore",
      "cost_consideration": "AgentCore Runtime charges based on compute time and memory allocation"
    },
    {
      "type": "agentcore_lifecycle_idle_timeout",
      "line": 16,
      "configured_value": 300,
      "default_value": 900,
      "cost_consideration": "Cost optimized: Idle timeout (300s / 5.0min) is lower than default (900s)."
    }
  ]
}
```

## Next Steps

1. **Scan your infrastructure code** for lifecycle configurations
2. **Review cost alerts** for high-cost settings
3. **Analyze your workload** to determine optimal timeouts
4. **Implement optimizations** and monitor impact
5. **Re-scan regularly** as usage patterns evolve

## Related Documentation

- [Bedrock Detector](bedrock-detector.md) - Model and prompt optimization
- [AWS AgentCore Lifecycle Settings](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html) - Official AWS documentation
