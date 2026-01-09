# Design Principles

This document defines the core design principles that guide the development of this cost optimization scanner. These principles act as guardrails to ensure the tool remains maintainable and relevant as AWS services evolve.

## Core Philosophy

**Pattern Detection, Not Rule Prescription**

This scanner identifies what's being used in code, not what should be done. We detect patterns and provide context, allowing other tools (AWS MCP Server) or humans to make optimization decisions.

### The Value Proposition

**Traditional approach** (breaks when AWS changes):
- Hardcoded recommendations: "Use Nova instead of Opus"
- Hardcoded lists: `CHEAP_MODELS = ["nova-micro"]`
- Requires constant updates for new models/pricing

**Our approach** (stays relevant):
- Pattern detection: `r"amazon\.nova[^\"']*"` catches all Nova variants
- Structured findings: `{"model_family": "amazon-nova", "model_id": "..."}`
- Composable: Works with AWS MCP Server for real-time cost comparison

**Result**: Scanner never needs updates when AWS releases new models or changes pricing. See [README](../README.md#how-it-works-pattern-detection-not-hardcoded-recommendations) for user-facing explanation.

## Principle 1: No Hardcoded Recommendations

### ❌ AVOID
```python
# Bad: Hardcoded recommendation
if model == "claude-3-opus":
    recommendation = "Switch to claude-3-sonnet to save 60%"
```

### ✅ PREFER
```python
# Good: Detect pattern, provide context
finding = {
    "type": "bedrock_model_usage",
    "model_family": "claude-3-opus",
    "model_id": model_id,
    # Let AWS Pricing MCP provide cost comparison
}
```

**Why**: Model pricing changes frequently. Hardcoded recommendations become stale and misleading.

## Principle 2: Detect Patterns, Not Specific Values

### ❌ AVOID
```python
# Bad: Hardcoded "good" values
if idle_timeout == 300:
    note = "Optimal configuration"
elif idle_timeout == 900:
    note = "Default configuration"
```

### ✅ PREFER
```python
# Good: Compare against documented defaults
DEFAULT_IDLE_TIMEOUT = 900  # From AWS documentation
if configured_value > DEFAULT_IDLE_TIMEOUT:
    note = f"COST ALERT: Higher than default ({DEFAULT_IDLE_TIMEOUT}s)"
elif configured_value < DEFAULT_IDLE_TIMEOUT:
    note = f"Cost optimized: Lower than default ({DEFAULT_IDLE_TIMEOUT}s)"
```

**Why**: What's "optimal" depends on workload. We compare against AWS defaults and let users decide.

## Principle 3: Dynamic Data Sources Over Static Lists

### ❌ AVOID
```python
# Bad: Hardcoded model list
SUPPORTED_MODELS = [
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    # This list becomes outdated quickly
]
```

### ✅ PREFER
```python
# Good: Pattern-based detection
MODEL_PATTERNS = {
    "claude-3-opus": r"anthropic\.claude-3-opus[^\"']*",
    "claude-3-sonnet": r"anthropic\.claude-3-sonnet[^\"']*",
    # Matches any version/variant
}
```

**Why**: AWS releases new models frequently. Pattern-based detection catches new variants automatically.

## Principle 4: Structured Output for Enrichment

### ❌ AVOID
```python
# Bad: Prescriptive message
return "Your idle timeout is too high. Set it to 300 seconds to save $50/month."
```

### ✅ PREFER
```python
# Good: Structured data for enrichment
return {
    "type": "agentcore_lifecycle_idle_timeout",
    "configured_value": 3600,
    "default_value": 900,
    "cost_consideration": "COST ALERT: Higher than default. Instances stay alive longer when idle, increasing costs."
}
```

**Why**: Other MCP servers can enrich with real-time pricing. We provide facts, not calculations.

## Principle 5: Language-Agnostic Pattern Detection

### ❌ AVOID
```python
# Bad: Python-only detection
if "boto3.client('bedrock-runtime')" in content:
    detect_bedrock()
```

### ✅ PREFER
```python
# Good: Multi-language patterns
BEDROCK_PATTERNS = [
    r"boto3\.client\(['\"]bedrock-runtime['\"]",  # Python
    r"BedrockRuntimeClient",                       # TypeScript
    r"@aws-sdk/client-bedrock",                    # JavaScript
]
```

**Why**: Infrastructure as Code uses multiple languages (Python, TypeScript, YAML). Detect patterns across all.

## Principle 6: Configuration Over Code

### ❌ AVOID
```python
# Bad: Hardcoded thresholds in code
if token_count > 4000:
    flag_as_high()
```

### ✅ PREFER
```python
# Good: Configurable or documented thresholds
# Use AWS-documented limits or make configurable
DEFAULT_TOKEN_THRESHOLD = 4000  # AWS default for many models
if token_count > DEFAULT_TOKEN_THRESHOLD:
    flag_as_high()
```

**Why**: Thresholds may need adjustment. Document source (AWS docs) or make configurable.

## Principle 7: Fail Gracefully, Don't Block

### ❌ AVOID
```python
# Bad: Strict validation that breaks on new patterns
if model_id not in KNOWN_MODELS:
    raise ValueError(f"Unknown model: {model_id}")
```

### ✅ PREFER
```python
# Good: Detect what we can, report what we find
if model_match := re.search(MODEL_PATTERN, content):
    findings.append({"model_id": model_match.group(0)})
# If no match, no finding - scanner continues
```

**Why**: New AWS features shouldn't break the scanner. Detect what we recognize, ignore the rest.

## Principle 8: Document Data Sources

### ❌ AVOID
```python
# Bad: Magic numbers
DEFAULT_IDLE_TIMEOUT = 900
```

### ✅ PREFER
```python
# Good: Documented source
# Default values from AWS documentation:
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html
DEFAULT_IDLE_TIMEOUT = 900  # 15 minutes
DEFAULT_MAX_LIFETIME = 28800  # 8 hours
```

**Why**: When AWS changes defaults, we know where to verify and update.

## Principle 9: Extensible Architecture

### ❌ AVOID
```python
# Bad: Monolithic detector
def scan_everything(content):
    # 1000 lines of detection logic
    pass
```

### ✅ PREFER
```python
# Good: Pluggable detectors
class ProjectScanner:
    def __init__(self):
        self.detectors = [
            BedrockDetector(),
            AgentCoreDetector(),
            # Easy to add: SageMakerDetector(),
        ]
```

**Why**: New AWS services can be added without modifying existing detectors.

## Principle 10: DRY - Don't Repeat Yourself (Especially API Patterns)

### ❌ AVOID
```python
# Bad: Duplicating API patterns in multiple methods
class BedrockDetector:
    INVOKE_PATTERNS = {
        "invoke_model": r"invoke_model\s*\(",
        "converse": r"converse\s*\(",
        "chat_completions_create": r"chat\.completions\.create\s*\(",
    }
    
    def _detect_api_calls(self):
        # Uses self.INVOKE_PATTERNS ✓
        for call_type, pattern in self.INVOKE_PATTERNS.items():
            ...
    
    def _detect_service_tier(self):
        # Bad: Duplicates the patterns instead of reusing
        api_patterns = [
            (r'invoke_model\s*\(', 'invoke_model'),
            (r'converse\s*\(', 'converse'),
            # Missing chat.completions.create!
        ]
```

### ✅ PREFER
```python
# Good: Single source of truth for API patterns
class BedrockDetector:
    INVOKE_PATTERNS = {
        "invoke_model": r"invoke_model\s*\(",
        "converse": r"converse\s*\(",
        "chat_completions_create": r"chat\.completions\.create\s*\(",
    }
    
    def _detect_api_calls(self):
        for call_type, pattern in self.INVOKE_PATTERNS.items():
            ...
    
    def _detect_service_tier(self):
        # Good: Reuses the same patterns
        for api_name, pattern in self.INVOKE_PATTERNS.items():
            ...
```

**Why**: 
- **Consistency**: All detection methods use the same API patterns
- **Maintainability**: Add new API once, works everywhere
- **Bug Prevention**: Can't forget to add new APIs to some methods
- **Single Source of Truth**: One place to update when AWS adds new APIs

**Real Example**: When we duplicated API patterns in `_detect_service_tier`, we forgot to include `chat.completions.create`, causing OpenAI SDK calls to be missed. Using `self.INVOKE_PATTERNS` fixed this automatically.

## Principle 11: Test Both Presence AND Absence

### ❌ AVOID
```python
# Bad: Only testing when parameter is present
def test_service_tier_flex():
    content = 'invoke_model(..., service_tier="flex")'
    findings = detector.analyze(content, "test.py")
    assert findings[0]["service_tier"] == "flex"

def test_service_tier_priority():
    content = 'invoke_model(..., service_tier="priority")'
    findings = detector.analyze(content, "test.py")
    assert findings[0]["service_tier"] == "priority"

# Missing: What happens when service_tier is ABSENT?
```

### ✅ PREFER
```python
# Good: Test explicit configurations
def test_service_tier_flex():
    content = 'invoke_model(..., service_tier="flex")'
    findings = detector.analyze(content, "test.py")
    assert findings[0]["service_tier"] == "flex"

# Good: Test missing parameter (often the most important case!)
def test_missing_service_tier():
    content = 'invoke_model(...)'  # No service_tier
    findings = detector.analyze(content, "test.py")
    missing = [f for f in findings if f["type"] == "service_tier_missing"]
    assert len(missing) > 0
    assert missing[0]["optimization_opportunity"] == True

# Good: Verify no false positives
def test_no_missing_when_present():
    content = 'invoke_model(..., service_tier="flex")'
    findings = detector.analyze(content, "test.py")
    missing = [f for f in findings if f["type"] == "service_tier_missing"]
    assert len(missing) == 0  # Should NOT flag as missing
```

**Why**: 
- **Absence is often the most common case** - Most code won't have optional parameters
- **Absence often means optimization opportunity** - Using defaults without considering alternatives
- **Prevents false positives** - Ensures we don't flag when parameter IS present
- **Real-world relevance** - Users need to know what they're NOT doing, not just what they ARE doing

**Real Example**: Service tier tests only checked explicit values (flex, priority, default), but missed that most code doesn't specify service_tier at all, meaning they're using default tier without considering cost-optimized flex tier.

## Principle 12: Test Patterns, Not Specific Values

### ❌ AVOID
```python
# Bad: Testing specific recommendations
def test_recommendation():
    assert "switch to haiku" in result.recommendation
```

### ✅ PREFER
```python
# Good: Testing detection capability
def test_model_detection():
    content = 'modelId="anthropic.claude-3-opus-20240229-v1:0"'
    findings = detector.analyze(content, "test.py")
    assert findings[0]["model_family"] == "claude-3-opus"
    assert findings[0]["model_id"] == "anthropic.claude-3-opus-20240229-v1:0"
```

**Why**: Tests verify detection works, not that recommendations are "correct" (which changes).

## Implementation Checklist

When adding a new detector or feature, verify:

- [ ] No hardcoded recommendations (only pattern detection)
- [ ] Comparisons use documented AWS defaults (with source links)
- [ ] Pattern-based detection (not specific value lists)
- [ ] Structured JSON output (not prescriptive messages)
- [ ] Multi-language support where applicable
- [ ] Graceful handling of unknown patterns
- [ ] Data sources documented in comments
- [ ] Pluggable architecture maintained
- [ ] **DRY: Reuse existing patterns (e.g., `self.INVOKE_PATTERNS`), don't duplicate**
- [ ] **Test both presence AND absence of optional parameters**
- [ ] **Test no false positives when parameter is present**
- [ ] Tests verify detection, not recommendations

## Examples of Good vs Bad

### Example 1: Model Detection

**Bad Approach:**
```python
# Hardcoded list that becomes outdated
CHEAP_MODELS = ["claude-3-haiku"]
EXPENSIVE_MODELS = ["claude-3-opus"]

if model in EXPENSIVE_MODELS:
    return "Use Haiku instead to save money"
```

**Good Approach:**
```python
# Pattern detection with context
MODEL_PATTERNS = {
    "claude-3-opus": r"anthropic\.claude-3-opus[^\"']*",
    "claude-3-haiku": r"anthropic\.claude-3-haiku[^\"']*",
}

# Return structured finding
return {
    "type": "bedrock_model_usage",
    "model_family": "claude-3-opus",
    "model_id": detected_id,
    # AWS Pricing MCP can provide cost comparison
}
```

### Example 2: Lifecycle Configuration

**Bad Approach:**
```python
# Hardcoded "optimal" values
OPTIMAL_IDLE = 300
if idle_timeout != OPTIMAL_IDLE:
    return f"Set idle timeout to {OPTIMAL_IDLE} for best cost"
```

**Good Approach:**
```python
# Compare against AWS defaults
DEFAULT_IDLE_TIMEOUT = 900  # From AWS docs
if configured > DEFAULT_IDLE_TIMEOUT:
    return {
        "configured_value": configured,
        "default_value": DEFAULT_IDLE_TIMEOUT,
        "cost_consideration": "COST ALERT: Higher than default. Instances stay alive longer when idle."
    }
```

## Maintenance Guidelines

### When AWS Releases New Features

1. **Don't rush to add specific support** - Pattern-based detection may already catch it
2. **Test with new feature** - Verify existing patterns work
3. **Add pattern if needed** - Extend regex patterns, don't hardcode values
4. **Update documentation** - Add examples to detector docs

### When AWS Changes Pricing

1. **Don't update the scanner** - We don't store pricing
2. **Verify defaults haven't changed** - Check AWS documentation links
3. **Update default values if needed** - With new documentation link

### When AWS Deprecates Features

1. **Keep detection** - Historical code still exists
2. **Add note in documentation** - Mark as deprecated
3. **Don't remove patterns** - Users may still have old code

## Questions to Ask

Before implementing a feature, ask:

1. **Am I hardcoding a recommendation?** → Provide context instead
2. **Will this break when AWS releases a new version?** → Use patterns
3. **Am I calculating costs?** → Let AWS MCP Server do that
4. **Am I prescribing a solution?** → Detect the pattern, let users decide
5. **Is this data source documented?** → Add documentation link

## Lessons Learned: Service Tier Detection

### The Problem
When implementing service tier detection, we made several mistakes:

1. **Only tested explicit values** - Tests checked `service_tier="flex"`, `service_tier="priority"`, but not the absence of the parameter
2. **Duplicated API patterns** - Created a new list of API patterns instead of reusing `self.INVOKE_PATTERNS`
3. **Incomplete coverage** - Forgot to include `chat.completions.create` in the duplicated list
4. **Context window bug** - Looked 500 chars ahead and found service_tier from the NEXT API call

### The Impact
- **Missed optimization opportunities** - Most code doesn't specify service_tier (uses default), but we didn't flag this
- **Inconsistent detection** - OpenAI SDK calls weren't checked for missing service_tier
- **False negatives** - API calls without service_tier weren't detected as optimization opportunities

### The Fix
1. **Added test for absence**: `test_detect_missing_service_tier()` - flags when parameter is missing
2. **Reused existing patterns**: Changed from duplicated list to `for api_name, pattern in self.INVOKE_PATTERNS.items()`
3. **Fixed context scoping**: Used parenthesis matching to limit search to current API call only
4. **Added negative test**: `test_no_missing_tier_when_explicitly_set()` - ensures no false positives

### The Takeaway
**Absence is often more important than presence.** When detecting optional parameters:
- Test explicit values (flex, priority, default)
- **Test absence** (no parameter = using default)
- Test no false positives (don't flag when present)
- Reuse existing patterns (DRY principle)

This pattern applies to many AWS features:
- `service_tier` - absence means using default tier (missed optimization)
- `caching` - absence means no caching (missed 90% savings)
- `streaming` - absence means synchronous (might be suboptimal)
- `timeout` - absence means using default (might be too high/low)

## Conclusion

These principles ensure the scanner remains:
- **Maintainable**: No constant updates for new AWS features
- **Accurate**: No stale recommendations
- **Composable**: Works with other MCP servers
- **Extensible**: Easy to add new detectors
- **Consistent**: Single source of truth for patterns (DRY)
- **Complete**: Tests both presence and absence

When in doubt, remember: **Detect patterns, provide context, let others enrich. Test both what IS there and what ISN'T.**
