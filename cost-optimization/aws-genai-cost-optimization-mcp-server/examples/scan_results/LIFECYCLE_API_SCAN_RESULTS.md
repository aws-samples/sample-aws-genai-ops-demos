# LifeCycleApi Project - Scan Results

## Summary

**Total Findings:** 21
**Project:** projects_sample/LifeCycleApi

## Findings Breakdown

### Bedrock Usage (10 findings)
- **Bedrock clients detected:** 7 files
  - `agent/data_extractor.py`
  - `agent/main.py`
  - `frontend/src/agentcore.ts`
  - `frontend/src/api.ts`
  - `cdk/lib/runtime-stack.ts`
  - `test-cognito-auth.js`
  - `test-scheduler-iam.js`
  - `test-scheduler-payload.js`

- **Model usage:** Nova Lite (`amazon.nova-lite-v1:0`)
- **API calls:** Synchronous converse (line 392)

### Prompt Engineering (2 findings)
- **Nova Optimizer Opportunity** (data_extractor.py:5)
  - Prompt length: 347 characters (~86 tokens)
  - **Potential savings:** 20-40% token reduction
  - **Tool:** AWS Nova Prompt Optimizer
  - **Installation:** `pip install nova-prompt-optimizer`

- **Prompt Builder Function Detected** (data_extractor.py:322) ✅ **NEW!**
  - Function: `_build_extraction_prompt`
  - Static content: ~32 tokens
  - **Pattern:** Called once in source, but likely multiple times at runtime (processing multiple URLs)
  - **Optimization:** Prompt caching for static portions
  - **Potential savings:** Up to 90% on cached tokens

### VSC Format Optimization (3 findings) 🆕
- **JSON Serialization Near LLM Calls** (extraction_api.py)
  - **3 instances detected** at lines 119, 183, 284
  - Pattern: `json.dumps(agent_payload)` before `invoke_agent_runtime()`
  - **Token overhead:** JSON format adds up to 75% more tokens than VSC
  - **Potential savings:** ~65 tokens per call (up to 75% reduction)
  - **Implementation:** Replace JSON with VSC format (comma-separated values)
  - **Use when:** Flat, tabular data with known schema

### AgentCore Runtime (4 findings)
- **Application detected:** `agent/main.py`
- **Entrypoint decorator:** Line 213
- **Lifecycle Configuration (OPTIMIZED!):**
  - ✅ Idle timeout: 300s (5 min) - **Better than default 900s**
  - ✅ Max lifetime: 1800s (30 min) - **Better than default 8h**
  - Location: `cdk/lib/runtime-stack.ts:263-264`

- **Default lifecycle configs:** 2 additional runtimes using defaults (also good)

## Key Insights

### ✅ What's Good
1. **AgentCore lifecycle is optimized!**
   - 5-minute idle timeout (vs 15-minute default)
   - 30-minute max lifetime (vs 8-hour default)
   - This will significantly reduce idle compute costs

2. **Using Nova Lite**
   - Cost-effective model choice
   - Good for extraction tasks

### 💡 Optimization Opportunities

1. **Nova Prompt Optimizer** (Easy win)
   - Current: ~86 tokens per prompt
   - Potential: 20-40% reduction
   - **Savings:** If processing 1000 documents:
     - Before: 86,000 tokens × $0.00035/1K = $0.03
     - After (30% reduction): 60,200 tokens × $0.00035/1K = $0.021
     - **Save: $0.009 per 1000 documents**

2. **Prompt Caching for Recurring Prompts** ✅ **NOW DETECTED!**
   - Function: `_build_extraction_prompt()` builds prompts with ~32 tokens static content
   - Called once per URL in the processing loop
   - **Savings potential:** If processing 100 URLs:
     - Without caching: 100 × 32 = 3,200 static tokens charged
     - With caching: 32 + (99 × 3.2) = ~350 tokens charged
     - **Save: ~89% on static content**

3. **VSC Format for JSON Payloads** 🆕 **NEW OPTIMIZATION!**
   - 3 instances of JSON serialization before LLM calls
   - JSON format wastes tokens with ALL structural overhead
   - **Example:** `{"users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]}`
   - **VSC format:** `1,Alice\n2,Bob` (schema: id,name)
   - **Savings:** Up to 75% token reduction for flat, tabular data
   - **When to use:** Flat data with known schema on both sides

## Recommendations

### Immediate Actions
1. **Try Nova Prompt Optimizer**
   ```bash
   pip install nova-prompt-optimizer
   # Test on your extraction prompts
   ```

2. **Monitor AgentCore costs**
   - Your lifecycle config is already optimized
   - Track actual idle time to see if you can reduce further

### Future Considerations
1. **Prompt Caching**
   - If you're processing multiple documents with the same extraction template
   - Could save 90% on repeated static content
   - Check if your prompts have large static sections

2. **Batch Processing**
   - If processing many documents, consider batching
   - Reduces API call overhead

## Cost Impact Estimate

### Current Setup
- Nova Lite: $0.00035 per 1K input tokens
- Optimized lifecycle: Minimal idle time
- **Estimated monthly cost:** Depends on volume

### With Nova Optimizer (30% reduction)
- **Savings:** ~$0.009 per 1000 documents
- **For 10,000 documents/month:** ~$0.09 saved
- **For 100,000 documents/month:** ~$0.90 saved

### With Prompt Caching (if applicable)
- **Savings:** Up to 90% on repeated static content
- **Requires:** Same extraction template across documents

## Conclusion

**Overall Assessment:** ✅ Well-optimized!

Your LifeCycleApi project is already following many cost optimization best practices:
- ✅ Optimized AgentCore lifecycle configuration
- ✅ Using cost-effective Nova Lite model
- ✅ Synchronous API calls (appropriate for extraction)

**Quick Win:** Try Nova Prompt Optimizer for 20-40% token reduction.

**Next Steps:** Monitor actual costs and consider prompt caching if you're processing many documents with similar templates.
