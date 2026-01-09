# sample-nova-sonic-golf-caddy - Cost Optimization Scan Results

**Total Findings:** 10

## Findings Summary

- **Agentcore Async Processing:** 1 finding(s)
- **Agentcore Streaming:** 1 finding(s)
- **Bedrock Client Detected:** 1 finding(s)
- **Bedrock Model Usage:** 3 finding(s)
- **Missing Prompt Caching:** 1 finding(s)
- **Nova Optimization Opportunity:** 2 finding(s)
- **Prompt Builder Function Detected:** 1 finding(s)

## Key Findings


### Nova Optimization Opportunity

**File:** `projects_sample\sample-nova-sonic-golf-caddy\config.py`
**Line:** 17
**Cost Impact:** Nova Prompt Optimizer can automatically test prompt variations to reduce token usage by 20-40% while maintaining quality....

**File:** `projects_sample\sample-nova-sonic-golf-caddy\nova_sonic_tool_use.py`
**Line:** 79
**Cost Impact:** Nova Prompt Optimizer can automatically test prompt variations to reduce token usage by 20-40% while maintaining quality....


### Prompt Builder Function Detected

**File:** `projects_sample\sample-nova-sonic-golf-caddy\golfcourse_helper.py`
**Line:** 234
**Description:** Function 'format_course_summary' builds prompts dynamically and is called 1 time(s)
**Cost Impact:** This function builds prompts with ~13 tokens of static content. If called multiple times at runtime (e.g., processing multiple items), consider prompt caching for the static portions....
**Potential Savings:** Up to 90% on cached tokens


### Missing Prompt Caching

**File:** `projects_sample\sample-nova-sonic-golf-caddy\nova_sonic_tool_use.py`
**Line:** 916
**Cost Impact:** Large prompts (~232 tokens) without cache control. Bedrock offers 90% discount on cached tokens....

## Recommendations

3. **Enable Prompt Caching** - Save 90% on repeated prompts
4. **Use Nova Prompt Optimizer** - Reduce tokens by 20-40%

---
*Scan completed: projects_sample/sample-nova-sonic-golf-caddy*
