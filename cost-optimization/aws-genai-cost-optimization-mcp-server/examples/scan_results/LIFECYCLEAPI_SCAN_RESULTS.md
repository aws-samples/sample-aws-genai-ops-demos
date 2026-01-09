# LifeCycleApi - Cost Optimization Scan Results

**Total Findings:** 21

## Findings Summary

- **Agentcore App Detected:** 1 finding(s)
- **Agentcore Decorator:** 1 finding(s)
- **Agentcore Lifecycle Idle Timeout:** 1 finding(s)
- **Agentcore Lifecycle Max Lifetime:** 1 finding(s)
- **Agentcore Lifecycle Using Defaults:** 2 finding(s)
- **Bedrock Api Call:** 1 finding(s)
- **Bedrock Client Detected:** 8 finding(s)
- **Bedrock Model Usage:** 1 finding(s)
- **Json Serialization Near Llm Call:** 3 finding(s)
- **Nova Optimization Opportunity:** 1 finding(s)
- **Prompt Builder Function Detected:** 1 finding(s)

## Key Findings


### Agentcore Lifecycle Idle Timeout

**File:** `projects_sample\LifeCycleApi\cdk\lib\runtime-stack.ts`
**Line:** 263
**Cost Impact:** Cost optimized: Idle timeout (300s / 5.0min) is lower than default (900s). Instances terminate faster when idle, reducing costs....


### Agentcore Lifecycle Max Lifetime

**File:** `projects_sample\LifeCycleApi\cdk\lib\runtime-stack.ts`
**Line:** 264
**Cost Impact:** Cost optimized: Max lifetime (1800s / 0.5h) is lower than default (28800s / 8h). Instances terminate sooner, reducing costs....


### Json Serialization Near Llm Call

**File:** `projects_sample\LifeCycleApi\cdk\lambda\api\extraction_api.py`
**Line:** 119
**Description:** json.dumps() used near LLM API call (line 117)
**Cost Impact:** JSON serialization adds token overhead. Estimated ~100 tokens could be reduced to ~25 with VSC....
**Potential Savings:** ~75 tokens (up to 75% reduction)

**File:** `projects_sample\LifeCycleApi\cdk\lambda\api\extraction_api.py`
**Line:** 183
**Description:** json.dumps() used near LLM API call (line 181)
**Cost Impact:** JSON serialization adds token overhead. Estimated ~100 tokens could be reduced to ~25 with VSC....
**Potential Savings:** ~75 tokens (up to 75% reduction)

**File:** `projects_sample\LifeCycleApi\cdk\lambda\api\extraction_api.py`
**Line:** 284
**Description:** json.dumps() used near LLM API call (line 282)
**Cost Impact:** JSON serialization adds token overhead. Estimated ~100 tokens could be reduced to ~25 with VSC....
**Potential Savings:** ~75 tokens (up to 75% reduction)


### Nova Optimization Opportunity

**File:** `projects_sample\LifeCycleApi\agent\data_extractor.py`
**Line:** 5
**Cost Impact:** Nova Prompt Optimizer can automatically test prompt variations to reduce token usage by 20-40% while maintaining quality....


### Prompt Builder Function Detected

**File:** `projects_sample\LifeCycleApi\agent\data_extractor.py`
**Line:** 322
**Description:** Function '_build_extraction_prompt' builds prompts dynamically and is called 1 time(s)
**Cost Impact:** This function builds prompts with ~39 tokens of static content. If called multiple times at runtime (e.g., processing multiple items), consider prompt caching for the static portions....
**Potential Savings:** Up to 90% on cached tokens

## Recommendations

2. **Consider VSC Format** - Reduce token usage by up to 75%
4. **Use Nova Prompt Optimizer** - Reduce tokens by 20-40%

---
*Scan completed: projects_sample/LifeCycleApi*
