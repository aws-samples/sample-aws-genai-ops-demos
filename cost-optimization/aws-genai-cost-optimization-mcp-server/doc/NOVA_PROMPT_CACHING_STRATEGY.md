# Nova Prompt Caching Strategy for MCP Cost Optimization

> **Note:** Core content has been consolidated into [prompt-engineering.md](prompt-engineering.md#nova-prompt-caching-90-savings). This file contains extended examples and implementation details.

## Overview

Amazon Nova models (Micro, Lite, Pro, Premier) now support **automatic and explicit prompt caching** with significant cost and latency benefits.

## Key Features

### 1. Automatic Prompt Caching (Built-in)
- **Always enabled** for Nova models
- Automatically caches repetitive text prompts
- Provides latency benefits without configuration
- Works for `User` and `System` messages

### 2. Explicit Prompt Caching (Recommended)
- **Opt-in for cost savings** and consistent performance
- Requires adding `cachePoint` markers
- 90% discount on cached tokens
- 5-minute TTL (resets on cache hit)

## Nova Model Specifications

| Model | Min Tokens/Checkpoint | Max Checkpoints | Max Cache Tokens | Cacheable Fields |
|-------|----------------------|-----------------|------------------|------------------|
| Nova Micro | 1,000 | 4 | 20,000 | `system`, `messages` |
| Nova Lite | 1,000 | 4 | 20,000 | `system`, `messages` |
| Nova Pro | 1,000 | 4 | 20,000 | `system`, `messages` |
| Nova Premier | 1,000 | 4 | 20,000 | `system`, `messages` |

**Note:** Nova models primarily cache text prompts (not images/video).

## Implementation in Our MCP Scanner

### Current Detection Capabilities

Our scanner already detects:
- ✅ Nova model usage
- ✅ Large prompts (>300 chars)
- ✅ Repeated prompt contexts
- ✅ Missing cache control

### Enhancement Opportunities


#### 1. Nova-Specific Cache Detection

Add detection for:
- Nova model + large system prompts (>1K tokens)
- Nova model + repeated user messages
- Nova model without explicit cachePoint markers

#### 2. Automatic vs Explicit Caching Analysis

Detect when projects rely on automatic caching and recommend explicit caching for:
- Cost savings (90% discount)
- Consistent performance
- Better control

#### 3. Cache Point Placement Recommendations

Analyze prompt structure and suggest optimal cachePoint locations:
- After static system prompts
- After document/context uploads
- Before dynamic user queries

## Real-World Application: EOLTracker

### Current Configuration
```python
bedrock_model = BedrockModel(
  model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",  # Claude, not Nova
  temperature=0.1,
  streaming=True,
)
```

### Recommendation 1: Switch to Nova Lite
```python
bedrock_model = BedrockModel(
  model_id="amazon.nova-lite-v1:0",  # Cost-effective for extraction
  temperature=0.1,
  streaming=False,  # Faster for Lambda
)
```

**Benefits:**
- Lower base cost than Claude
- Automatic caching built-in
- Excellent for structured data extraction

### Recommendation 2: Add Explicit Caching

**Current System Prompt (Line 401):**
```python
system_prompt=f"""You are an AWS documentation analyst specialized in extracting 
End of Life (EOL) information from AWS service documentation.

Your task:
1. Read and analyze the provided AWS service URL: {service_url}
2. Include the following key-value pairs...
[~800 tokens of static instructions]
"""
```

**Optimized with Caching:**
```python
system_prompt=[
    {
        "text": """You are an AWS documentation analyst specialized in extracting 
        End of Life (EOL) information from AWS service documentation.
        
        Your task:
        1. Read and analyze the provided AWS service URL
        2. Include the following key-value pairs...
        [~800 tokens of static instructions]
        """
    },
    {
        "cachePoint": {"type": "default"}  # Cache static instructions
    },
    {
        "text": f"Service URL: {service_url}"  # Dynamic part (not cached)
    }
]
```

**Cost Impact (1000 services/month):**
- Without caching: 1000 × 800 = 800,000 tokens
- With caching: 800 + (999 × 80) = 80,720 tokens
- **Savings: 719,280 tokens (90%)**


## Real-World Application: LifeCycleApi

### Current Configuration
```python
# Using Nova Lite (already cost-effective!)
model_id="amazon.nova-lite-v1:0"
```

### Opportunity: Explicit Caching for Extraction Prompt

**Current Pattern (data_extractor.py:322):**
```python
def _build_extraction_prompt(self, url: str, content: str) -> str:
    """Build prompt for extracting deprecation data."""
    return f"""
    Extract deprecation information from this AWS documentation:
    
    URL: {url}
    Content: {content}
    
    Return JSON with: service, feature, deprecation_date, replacement
    """
```

**Optimized with Caching:**
```python
def _build_extraction_prompt_cached(self, url: str, content: str) -> list:
    """Build prompt with caching for static instructions."""
    return [
        {
            "text": """Extract deprecation information from AWS documentation.
            
            Return JSON with these fields:
            - service: AWS service name
            - feature: Deprecated feature
            - deprecation_date: When it will be deprecated
            - replacement: Recommended alternative
            
            Be precise and extract only factual information."""
        },
        {
            "cachePoint": {"type": "default"}  # Cache instructions
        },
        {
            "text": f"URL: {url}\nContent: {content}"  # Dynamic content
        }
    ]
```

**Cost Impact (1000 URLs/month):**
- Static instructions: ~150 tokens
- Without caching: 1000 × 150 = 150,000 tokens
- With caching: 150 + (999 × 15) = 15,135 tokens
- **Savings: 134,865 tokens (90%)**

## Real-World Application: Golf Caddy

### Current Configuration
```python
# Already using Nova models
# Detected: 2 Nova optimization opportunities
```

### Opportunity: Cache Course Data

**Pattern:** Golf course information is static and reused across multiple queries

**Implementation:**
```python
# Cache course layout, rules, and hole information
course_context = [
    {
        "text": """Course: Pebble Beach Golf Links
        
        Hole 1: Par 4, 380 yards, slight dogleg right...
        Hole 2: Par 5, 502 yards, straight fairway...
        [Full course data ~5000 tokens]
        """
    },
    {
        "cachePoint": {"type": "default"}  # Cache course data
    },
    {
        "text": f"Current hole: {hole_number}\nWind: {wind_conditions}"
    }
]
```

**Cost Impact (100 rounds/month, 18 holes each):**
- Course data: ~5000 tokens
- Without caching: 1800 × 5000 = 9,000,000 tokens
- With caching: 5000 + (1799 × 500) = 904,500 tokens
- **Savings: 8,095,500 tokens (90%)**


## Real-World Application: Fullstack Webapp

### Current Findings
- 24 repeated prompt contexts detected
- Perfect candidate for caching

### Opportunity: Multi-Turn Conversations

**Pattern:** Chat applications with repeated context

**Implementation:**
```python
# First message - establish cache
messages = [
    {
        "role": "user",
        "content": [
            {
                "text": """System context: You are a helpful assistant for AWS services.
                
                Available services: EC2, S3, Lambda, DynamoDB...
                [Large context ~3000 tokens]
                """
            },
            {
                "cachePoint": {"type": "default"}  # Cache context
            },
            {
                "text": "What is EC2?"  # User query
            }
        ]
    }
]

# Subsequent messages - reuse cache
messages.append({
    "role": "assistant",
    "content": [{"text": "EC2 is..."}]
})
messages.append({
    "role": "user",
    "content": [{"text": "What about S3?"}]  # Cache hit!
})
```

**Cost Impact (10K conversations/month, 5 turns each):**
- Context: ~3000 tokens per conversation
- Without caching: 50,000 × 3000 = 150,000,000 tokens
- With caching: (10,000 × 3000) + (40,000 × 300) = 42,000,000 tokens
- **Savings: 108,000,000 tokens (72%)**

## Scanner Enhancement Plan

### Phase 1: Nova-Specific Detection (This Week)

Add to `bedrock_detector.py`:

```python
def _detect_nova_caching_opportunity(self, content: str, file_path: str) -> List[Dict]:
    """Detect Nova models without explicit caching."""
    findings = []
    
    # Check if using Nova model
    has_nova = bool(re.search(r'amazon\.nova', content, re.IGNORECASE))
    
    if has_nova:
        # Check for explicit cachePoint usage
        has_cache_point = bool(re.search(r'cachePoint', content))
        
        if not has_cache_point:
            # Check for large prompts that should be cached
            large_prompts = self._find_large_strings(content)
            
            if large_prompts:
                findings.append({
                    'type': 'nova_explicit_caching_opportunity',
                    'file': file_path,
                    'line': large_prompts[0]['line'],
                    'estimated_tokens': large_prompts[0]['length'] // 4,
                    'service': 'bedrock',
                    'cost_consideration': 'Nova automatic caching provides latency benefits, but explicit caching unlocks 90% cost savings on cached tokens.',
                    'optimization': {
                        'technique': 'Nova Explicit Prompt Caching',
                        'potential_savings': '90% on cached tokens',
                        'implementation': 'Add cachePoint markers after static content',
                        'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html'
                    }
                })
    
    return findings
```

### Phase 2: Cache Point Placement Analysis (Next Week)

Analyze prompt structure to suggest optimal cache point locations:
- Detect static vs dynamic content
- Recommend cache point placement
- Estimate token savings

### Phase 3: Multi-Turn Conversation Detection (Next Month)

Detect conversation patterns:
- Message history management
- Repeated context across turns
- Cache hit opportunities


## Best Practices for Nova Caching

### 1. Cache Point Placement

**✅ Good:**
```python
[
    {"text": "Static instructions..."},
    {"cachePoint": {"type": "default"}},
    {"text": f"Dynamic content: {variable}"}
]
```

**❌ Bad:**
```python
[
    {"text": f"Instructions with {variable}..."},  # Dynamic in static
    {"cachePoint": {"type": "default"}}
]
```

### 2. Minimum Token Requirements

- Nova models: 1,000 tokens minimum per checkpoint
- If below minimum, cache point is ignored (no error)
- Plan prompt structure to meet minimums

### 3. Cache TTL Management

- 5-minute TTL (resets on hit)
- Design for frequent access patterns
- Batch similar requests together

### 4. Cost Optimization Strategy

**Priority 1: System Prompts**
- Usually static
- Repeated across all requests
- Highest ROI for caching

**Priority 2: Document Context**
- Large documents (>1K tokens)
- Reused across multiple queries
- Good ROI for multi-turn conversations

**Priority 3: Tool Definitions**
- Static tool schemas
- Repeated in every tool-using request
- Moderate ROI

## Pricing Impact

### Nova Lite Pricing (Example)
- Input tokens: $0.00006 per 1K tokens
- Cached tokens (read): $0.000006 per 1K tokens (90% discount)
- Cached tokens (write): $0.00009 per 1K tokens (50% premium)

### Break-Even Analysis

**Scenario:** 1000-token system prompt

- First request: 1000 tokens × $0.00009 = $0.09 (write to cache)
- Subsequent requests: 1000 tokens × $0.000006 = $0.006 (read from cache)

**Break-even:** After 2 requests
- Without caching: 2 × $0.06 = $0.12
- With caching: $0.09 + $0.006 = $0.096
- **Savings start immediately after first cache hit**

### ROI by Use Case

| Use Case | Requests/Month | Cached Tokens | Monthly Savings |
|----------|----------------|---------------|-----------------|
| EOLTracker (batch) | 1,000 | 800 | $43.20 |
| LifeCycleApi (extraction) | 1,000 | 150 | $8.10 |
| Golf Caddy (course data) | 1,800 | 5,000 | $486.00 |
| Fullstack (chat) | 50,000 | 3,000 | $8,100.00 |
| **Total** | **53,800** | - | **$8,637.30** |

## Implementation Checklist

### For EOLTracker
- [ ] Switch from Claude 3.7 Sonnet to Nova Lite
- [ ] Add cachePoint after system prompt
- [ ] Test with 10 sample services
- [ ] Monitor cache hit rate in CloudWatch
- [ ] Measure cost savings

### For LifeCycleApi
- [ ] Already using Nova Lite ✅
- [ ] Refactor `_build_extraction_prompt` to return list with cachePoint
- [ ] Update Agent() to use new prompt format
- [ ] Test extraction accuracy
- [ ] Monitor cache metrics

### For Golf Caddy
- [ ] Add cachePoint after course data
- [ ] Structure prompts: static course info → cache → dynamic query
- [ ] Test across multiple holes
- [ ] Verify cache hits

### For Fullstack Webapp
- [ ] Identify repeated contexts (24 detected)
- [ ] Add cachePoint markers
- [ ] Test multi-turn conversations
- [ ] Monitor cache hit rate

## Monitoring & Validation

### CloudWatch Metrics
- `CacheReadInputTokens` - Tokens read from cache
- `CacheWriteInputTokens` - Tokens written to cache
- Cache hit rate = Read / (Read + Write)

### Success Criteria
- Cache hit rate > 80% for repeated contexts
- Cost reduction > 70% on cached tokens
- No degradation in response quality
- Latency improvement (bonus)

## Conclusion

Nova prompt caching offers significant cost savings (90% on cached tokens) with minimal implementation effort. Our scanner can detect opportunities automatically, and the implementation is straightforward with cachePoint markers.

**Recommended Action:** Start with EOLTracker (highest impact, simplest implementation) and expand to other projects based on results.

**Expected Total Savings:** $8,637/month across all projects with proper caching implementation.

---

*Document created: 2025-11-14*  
*Based on: AWS Bedrock Prompt Caching Documentation*
