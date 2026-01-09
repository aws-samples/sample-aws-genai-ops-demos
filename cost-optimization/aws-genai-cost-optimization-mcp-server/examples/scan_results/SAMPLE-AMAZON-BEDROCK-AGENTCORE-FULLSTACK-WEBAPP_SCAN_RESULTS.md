# sample-amazon-bedrock-agentcore-fullstack-webapp - Cost Optimization Scan Results

**Total Findings:** 102

## Findings Summary

- **Agentcore App Detected:** 11 finding(s)
- **Agentcore Authentication:** 1 finding(s)
- **Agentcore Decorator:** 11 finding(s)
- **Agentcore Lifecycle Using Defaults:** 1 finding(s)
- **Agentcore Streaming:** 12 finding(s)
- **Bedrock Client Detected:** 22 finding(s)
- **Repeated Prompt Context:** 24 finding(s)
- **Repetitive Data Structure:** 20 finding(s)

## Key Findings


### Repeated Prompt Context

**File:** `projects_sample\sample-amazon-bedrock-agentcore-fullstack-webapp\memory-test.js`
**Line:** 80
**Cost Impact:** Same 54-token context used 2 times. Prompt caching could reduce costs by 90%....

**File:** `projects_sample\sample-amazon-bedrock-agentcore-fullstack-webapp\memory-test.js`
**Line:** 134
**Cost Impact:** Same 54-token context used 2 times. Prompt caching could reduce costs by 90%....

**File:** `projects_sample\sample-amazon-bedrock-agentcore-fullstack-webapp\cdk\cdk.out.20251112110436\asset.c6155f5841fbc0885e82c458df4d71f0609a02d79e72e2bd81d5431d8f4e1b76\assets\index-FUTsrFj6.js`
**Line:** 41
**Cost Impact:** Same 54-token context used 3 times. Prompt caching could reduce costs by 90%....

## Recommendations

3. **Enable Prompt Caching** - Save 90% on repeated prompts

---
*Scan completed: projects_sample/sample-amazon-bedrock-agentcore-fullstack-webapp*
