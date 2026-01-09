# AgentCore Patterns Guide

Detailed workflows for AgentCore Runtime lifecycle configuration and session management optimization.

## Why AgentCore Costs Matter

AgentCore Runtime charges based on **compute time + memory allocation**. Lifecycle configuration directly controls how long instances stay alive.

**Cost Impact Examples:**
- 1 hour idle timeout vs 15 minutes = **4x longer billing**
- Missing session termination = 15 minutes wasted per session
- Extended max lifetime = unnecessary compute charges

## Lifecycle Configuration

### Idle Timeout Detection

**What it is:** Time before terminating idle instances (default: 900s / 15 minutes)

**Finding types:**
- `agentcore_lifecycle_idle_timeout` - Configured value detected
- Cost alert when higher than default

**Optimization workflow:**

1. **Scan infrastructure code:**
   ```
   scan_project("/path/to/infrastructure")
   ```

2. **Review findings for:**
   - Idle timeout higher than 900s (cost alert)
   - Idle timeout lower than 900s (cost optimized ✅)

3. **Optimize based on workload:**

   **Bursty traffic (long idle periods):**
   ```python
   lifecycleConfiguration={
       'idleRuntimeSessionTimeout': 300,   # 5 min
       'maxLifetime': 3600                 # 1 hour
   }
   # Savings: 67% vs default
   ```

   **Continuous traffic (short idle periods):**
   ```python
   lifecycleConfiguration={
       'idleRuntimeSessionTimeout': 900,   # 15 min (default)
       'maxLifetime': 7200                 # 2 hours
   }
   # Balanced: prevents cold starts
   ```

### Max Lifetime Detection

**What it is:** Maximum instance runtime before forced termination (default: 28800s / 8 hours)

**Optimization:**
```python
# Long-running sessions
lifecycleConfiguration={
    'idleRuntimeSessionTimeout': 600,   # 10 min
    'maxLifetime': 14400                # 4 hours
}
# Savings: 50% reduction vs default
```

## Session Termination (Critical)

### The Problem

Sessions default to 15-minute idle timeout. Without explicit termination, you pay for idle time.

### Detection

The scanner detects `StopRuntimeSession` API usage:
- `agentcore_stop_session_detected` - Best practice detected ✅

### Implementation

**Python:**
```python
import boto3

client = boto3.client('bedrock-agentcore-runtime')

# After completing work, terminate immediately
response = client.stop_runtime_session(
    agentRuntimeArn='arn:aws:bedrock-agentcore:...',
    sessionId='session-123'
)
```

**TypeScript:**
```typescript
import { BedrockAgentCoreRuntimeClient, StopRuntimeSessionCommand } 
  from "@aws-sdk/client-bedrock-agentcore-runtime";

const client = new BedrockAgentCoreRuntimeClient({ region: "us-east-1" });

await client.send(new StopRuntimeSessionCommand({
    agentRuntimeArn: runtimeArn,
    sessionId: sessionId
}));
```

**Cost Impact:** Eliminates up to 15 minutes of idle charges per session.

## Decorator Patterns

### @app.entrypoint
Main agent logic - compute time charged per invocation.

```python
@app.entrypoint
def my_agent(payload):
    return {"result": process(payload)}
```

### @app.async_task
Background tasks - extends compute time, agent stays in HealthyBusy state.

```python
@app.async_task
async def long_task():
    await process_data()
    return "done"
```

**Cost consideration:** Background tasks keep agent in HealthyBusy state, extending compute time.

### @app.ping
Health check endpoint - minimal cost impact.

## Cost Calculation Example

**Assumptions:**
- AgentCore Runtime: 2GB RAM
- Pricing: $0.10 per GB-hour
- Workload: 10 invocations/day, 2 min each

| Configuration | Idle Timeout | Active | Idle | Total | Daily Cost |
|---------------|--------------|--------|------|-------|------------|
| Optimized | 5 min | 20 min | 50 min | 70 min | $0.23 |
| Default | 15 min | 20 min | 150 min | 170 min | $0.57 |
| Extended | 60 min | 20 min | 600 min | 620 min | $2.07 |

**Annual Savings (per runtime):**
- Optimized vs Default: $122/year
- Optimized vs Extended: $662/year

## CDK Configuration

**TypeScript CDK:**
```typescript
agentRuntime.addPropertyOverride('LifecycleConfiguration', {
  IdleRuntimeSessionTimeout: 300,  // 5 minutes
  MaxLifetime: 1800,               // 30 minutes
});
```

**CloudFormation/SAM:**
```yaml
LifecycleConfiguration:
  IdleRuntimeSessionTimeout: 300
  MaxLifetime: 1800
```

## Streaming Considerations

Streaming responses improve UX but may extend compute time.

**When to use streaming:**
- Long responses (>1000 tokens)
- Interactive applications
- Need to show progress

**When to avoid:**
- Short responses (<500 tokens)
- Batch processing
- Cost is primary concern

## Async Processing

**Detection:** `agentcore_async_processing`

**Cost consideration:** Background tasks keep agent in HealthyBusy state.

**Best practices:**
- Monitor task duration
- Implement task timeouts
- Consider moving long tasks to Lambda/Batch

```python
# ❌ Bad: Untracked background work
def handler(event):
    threading.Thread(target=long_task).start()
    return {"status": "started"}

# ✅ Good: Tracked async task
@app.async_task
async def long_task():
    await process_data()
    return "done"
```

## Optimization Checklist

1. ✅ Review idle timeout (lower = less idle charges)
2. ✅ Review max lifetime (right-size for workload)
3. ✅ Implement StopRuntimeSession after work completes
4. ✅ Use @app.async_task for background work
5. ✅ Monitor CloudWatch metrics after changes
6. ✅ Re-scan regularly as usage patterns evolve

## Best Practices

### ✅ DO
- Start with shorter timeouts, increase if needed
- Monitor actual usage before optimizing
- Use different configs for different workloads
- Call StopRuntimeSession when work completes

### ❌ DON'T
- Set high timeouts "just in case"
- Use same config for all environments
- Ignore cost alerts from scanner
- Run long tasks without tracking
