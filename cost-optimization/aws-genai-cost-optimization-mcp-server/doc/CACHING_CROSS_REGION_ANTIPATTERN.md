# Prompt Caching with Cross-Region Inference - Anti-Pattern Warning

## Critical Issue

**Using prompt caching with cross-region inference profiles can create cache entries in multiple regions, potentially INCREASING costs instead of reducing them.**

## Background

### Cross-Region Inference Profiles

AWS Bedrock offers inference profiles that route requests across multiple regions:

1. **Geography-specific profiles** (e.g., `us.anthropic.claude-3-haiku`)
   - Routes within a geography (US, EU, APAC)
   - Multiple destination regions within that geography

2. **Global profiles** (e.g., `global.anthropic.claude-sonnet-4`)
   - Routes to ANY commercial AWS region
   - Maximum throughput, maximum region spread

### How Prompt Caching Works

- Cache is **region-specific**
- 5-minute TTL per region
- Cache entries are NOT shared across regions

## The Anti-Pattern

### Scenario: Global Inference Profile + Prompt Caching

```python
# ANTI-PATTERN: Using global profile with caching
model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"

system_prompt = [
    {"text": "Large static instructions..."},  # 1000 tokens
    {"cachePoint": {"type": "default"}}
]
```

### What Happens

1. **Request 1** → Routes to `us-east-1`
   - Writes cache in `us-east-1` (1000 tokens × $0.00009 = $0.09)

2. **Request 2** → Routes to `eu-west-1` (different region!)
   - Cache MISS (no cache in `eu-west-1`)
   - Writes NEW cache in `eu-west-1` ($0.09)

3. **Request 3** → Routes to `ap-northeast-1`
   - Cache MISS again
   - Writes NEW cache in `ap-northeast-1` ($0.09)

### Cost Impact

**Without caching:**
- 3 requests × 1000 tokens × $0.00006 = $0.18

**With caching (cross-region):**
- 3 cache writes × 1000 tokens × $0.00009 = $0.27
- **INCREASED cost by 50%!**

## When This Becomes a Problem

### High-Risk Scenarios

1. **Global inference profiles**
   - Can route to 20+ regions
   - Each region creates separate cache
   - Cache write cost × number of regions

2. **Low request volume per region**
   - Cache expires (5min TTL) before reuse
   - Constant cache writes, few cache reads
   - Write premium (50%) without read discount (90%)

3. **Unpredictable routing**
   - No control over which region handles request
   - Cannot guarantee cache hits
   - Wasted cache writes

### Break-Even Analysis

**For caching to save money with cross-region:**
- Need multiple requests to SAME region within 5 minutes
- With global profile routing to N regions:
  - Need N × 2 requests minimum to break even
  - Need consistent traffic to each region

**Example:**
- Global profile with 10 possible regions
- Need 20+ requests within 5 minutes to break even
- If traffic is sporadic, caching COSTS more

## Detection Strategy

### Patterns to Detect

1. **Global inference profile IDs:**
   ```
   global.anthropic.claude-*
   global.amazon.nova-*
   ```

2. **Geography-specific profiles:**
   ```
   us.anthropic.claude-*
   eu.anthropic.claude-*
   apac.anthropic.claude-*
   ```

3. **Combined with cachePoint:**
   - Inference profile + cachePoint markers
   - Potential anti-pattern

### Warning Criteria

**HIGH RISK (warn strongly):**
- Global inference profile + caching
- Low request volume (<100/hour)
- Sporadic traffic patterns

**MEDIUM RISK (warn with context):**
- Geography-specific profile + caching
- Moderate request volume (100-1000/hour)
- Need to assess traffic distribution

**LOW RISK (caching beneficial):**
- Single-region model ID + caching
- High request volume (>1000/hour)
- Consistent traffic patterns


## Detector Implementation

### Detection Logic

```python
def _detect_caching_with_cross_region_antipattern(content, file_path):
    """Detect prompt caching used with cross-region inference profiles."""
    
    # Step 1: Check for cross-region inference profiles
    global_profile_pattern = r'global\.(anthropic|amazon)\.'
    geo_profile_pattern = r'(us|eu|apac)\.(anthropic|amazon)\.'
    
    has_global_profile = bool(re.search(global_profile_pattern, content))
    has_geo_profile = bool(re.search(geo_profile_pattern, content))
    
    # Step 2: Check for caching
    has_cache_point = bool(re.search(r'cachePoint', content))
    has_cache_control = bool(re.search(r'cache_control', content))
    
    # Step 3: Detect anti-pattern
    if (has_global_profile or has_geo_profile) and (has_cache_point or has_cache_control):
        return True
    
    return False
```

### Warning Message

```json
{
  "type": "caching_cross_region_antipattern",
  "severity": "high",
  "file": "app.py",
  "line": 42,
  "model_id": "global.anthropic.claude-sonnet-4-20250514-v1:0",
  
  "issue": "Prompt caching with global inference profile",
  
  "problem": "Cross-region inference profiles route requests to multiple regions. Each region maintains separate caches, causing cache writes in multiple regions without guaranteed cache hits.",
  
  "cost_impact": {
    "scenario": "Global profile routing to 10 regions",
    "without_caching": "$0.60/1000 requests (1000 tokens each)",
    "with_caching_cross_region": "$0.90/1000 requests (cache writes in multiple regions)",
    "cost_increase": "50% MORE expensive with caching"
  },
  
  "why_this_happens": [
    "Cache is region-specific, not shared across regions",
    "Global profiles route to any commercial AWS region",
    "Each new region creates a new cache entry",
    "Cache writes cost 50% more than regular tokens",
    "5-minute TTL means caches expire quickly"
  ],
  
  "recommendations": {
    "option_1": {
      "solution": "Use single-region model ID instead of inference profile",
      "example": "anthropic.claude-sonnet-4-20250514-v1:0 (in us-east-1)",
      "benefit": "Consistent region = consistent cache hits",
      "tradeoff": "No automatic cross-region failover"
    },
    "option_2": {
      "solution": "Disable caching when using cross-region profiles",
      "example": "Remove cachePoint markers",
      "benefit": "Avoid cache write premium in multiple regions",
      "tradeoff": "No caching benefits"
    },
    "option_3": {
      "solution": "Use geography-specific profile with high traffic",
      "example": "us.anthropic.claude-sonnet-4 (only US regions)",
      "benefit": "Fewer regions = better cache hit rate",
      "requirement": "Need >1000 requests/hour for break-even"
    }
  },
  
  "when_caching_is_ok": [
    "Using single-region model ID (not inference profile)",
    "Very high request volume (>1000/hour) with geo-specific profile",
    "Consistent traffic patterns to same regions"
  ],
  
  "action_required": "Review your traffic patterns and choose appropriate strategy"
}
```

## Real-World Example

### Bad: Global Profile + Caching

```python
# ❌ ANTI-PATTERN
model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"

system_prompt = [
    {"text": "You are an expert..."},  # 1000 tokens
    {"cachePoint": {"type": "default"}}  # Creates cache in multiple regions!
]

# Result: Cache writes in 10+ regions, few cache hits
# Cost: HIGHER than without caching
```

### Good: Single Region + Caching

```python
# ✅ CORRECT
model_id = "anthropic.claude-sonnet-4-20250514-v1:0"  # Single region
region = "us-east-1"  # Explicit region

system_prompt = [
    {"text": "You are an expert..."},  # 1000 tokens
    {"cachePoint": {"type": "default"}}  # Cache in one region only
]

# Result: Consistent cache hits in us-east-1
# Cost: 90% savings on cached tokens
```

### Alternative: Global Profile WITHOUT Caching

```python
# ✅ ACCEPTABLE
model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"

system_prompt = "You are an expert..."  # No cachePoint

# Result: Cross-region routing for throughput, no cache complexity
# Cost: Standard pricing, no cache write premium
```

## Summary

### Key Takeaways

1. **Caching is region-specific** - Not shared across regions
2. **Cross-region profiles route unpredictably** - Can't guarantee same region
3. **Cache writes cost more** - 50% premium on write
4. **Low traffic = anti-pattern** - Cache expires before reuse
5. **Single region + caching = best** - Consistent cache hits

### Decision Matrix

| Scenario | Use Caching? | Use Cross-Region? |
|----------|--------------|-------------------|
| High traffic, need throughput | ❌ No | ✅ Yes (global profile) |
| High traffic, cost-sensitive | ✅ Yes | ❌ No (single region) |
| Low traffic, need reliability | ❌ No | ✅ Yes (geo profile) |
| Low traffic, cost-sensitive | ✅ Yes | ❌ No (single region) |

### Detector Priority

**HIGH:** Detect and warn about global profile + caching
**MEDIUM:** Detect and provide context for geo profile + caching
**LOW:** Encourage single region + caching

---

*This anti-pattern can increase costs by 50%+ if not properly understood.*
