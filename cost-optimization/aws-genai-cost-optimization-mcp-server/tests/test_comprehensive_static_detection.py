"""Comprehensive test for static vs dynamic prompt detection with all scenarios."""
import sys
sys.path.insert(0, 'src')

from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector

# Scenario 1: Static prompts + US region + caching = INFO (acceptable)
scenario1 = '''
bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    system_prompt="""You are a helpful assistant.""",
)
response = invoke_model(body=json.dumps({"cacheControl": {"type": "ephemeral"}}))
'''

# Scenario 2: Static prompts + global + caching = INFO (acceptable)
scenario2 = '''
bedrock_model = BedrockModel(
    model_id="global.anthropic.claude-3-7-sonnet-20250219-v1:0",
    system_prompt="""You are a helpful assistant.""",
)
response = invoke_model(body=json.dumps({"cacheControl": {"type": "ephemeral"}}))
'''

# Scenario 3: Dynamic prompts + US region + caching = MEDIUM risk
scenario3 = '''
bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    system_prompt=f"""You are helping {user_name}.""",
)
response = invoke_model(body=json.dumps({"cacheControl": {"type": "ephemeral"}}))
'''

# Scenario 4: Dynamic prompts + global + caching = HIGH risk
scenario4 = '''
bedrock_model = BedrockModel(
    model_id="global.anthropic.claude-3-7-sonnet-20250219-v1:0",
    system_prompt=f"""You are helping {user_name}.""",
)
response = invoke_model(body=json.dumps({"cacheControl": {"type": "ephemeral"}}))
'''

# Scenario 5: Cross-region without caching = NO ISSUE
scenario5 = '''
bedrock_model = BedrockModel(
    model_id="global.anthropic.claude-3-7-sonnet-20250219-v1:0",
    system_prompt=f"""You are helping {user_name}.""",
)
response = invoke_model(body=json.dumps({"messages": messages}))
'''

# Scenario 6: Single-region with caching = NO ISSUE
scenario6 = '''
bedrock_model = BedrockModel(
    model_id="anthropic.claude-3-7-sonnet-20250219-v1:0",
    system_prompt=f"""You are helping {user_name}.""",
)
response = invoke_model(body=json.dumps({"cacheControl": {"type": "ephemeral"}}))
'''

detector = BedrockDetector()

scenarios = [
    ("Static + US + Caching", scenario1, "INFO"),
    ("Static + Global + Caching", scenario2, "INFO"),
    ("Dynamic + US + Caching", scenario3, "MEDIUM"),
    ("Dynamic + Global + Caching", scenario4, "HIGH"),
    ("Dynamic + Global + NO Caching", scenario5, "NONE"),
    ("Dynamic + Single-Region + Caching", scenario6, "NONE"),
]

print("=" * 80)
print("COMPREHENSIVE STATIC VS DYNAMIC PROMPT DETECTION TEST")
print("=" * 80)

results = []
for name, code, expected_severity in scenarios:
    findings = detector.analyze(code, f"test_{name}.py")
    cross_region = [f for f in findings if f['type'] == 'caching_cross_region_antipattern']
    
    if expected_severity == "NONE":
        actual = "NONE" if len(cross_region) == 0 else cross_region[0]['severity'].upper()
    else:
        actual = cross_region[0]['severity'].upper() if cross_region else "NONE"
    
    passed = actual == expected_severity
    results.append((name, expected_severity, actual, passed))
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n{status} | {name}")
    print(f"  Expected: {expected_severity}")
    print(f"  Actual:   {actual}")
    
    if cross_region:
        print(f"  Prompt Analysis: {cross_region[0]['prompt_analysis']}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

passed_count = sum(1 for _, _, _, passed in results if passed)
total_count = len(results)

print(f"\nPassed: {passed_count}/{total_count}")

if passed_count == total_count:
    print("\n🎉 ALL TESTS PASSED!")
else:
    print("\n❌ SOME TESTS FAILED")
    for name, expected, actual, passed in results:
        if not passed:
            print(f"  - {name}: Expected {expected}, got {actual}")
