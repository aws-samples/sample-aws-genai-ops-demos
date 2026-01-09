"""Comprehensive test for system prompt dynamic variable detection."""
import sys
sys.path.insert(0, 'src')

from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector
import json

# Test case 1: EOLTracker real-world pattern
eol_code = '''
eol_agent = Agent(
    model=bedrock_model,
    system_prompt=f"""You are an AWS documentation analyst.

Your task:
1. Read and analyze the provided AWS service URL: {service_url}
2. Extract EOL information

Current timestamp: {current_timestamp}
""",
    tools=tools,
)
'''

# Test case 2: Fixed version - variables in user message
fixed_code = '''
eol_agent = Agent(
    model=bedrock_model,
    system_prompt="""You are an AWS documentation analyst.

Your task:
1. Read and analyze the provided AWS service URL (will be in user message)
2. Extract EOL information
""",
    tools=tools,
)

# Pass variables in user message instead
query = f"Extract EOL from {service_url} as of {current_timestamp}"
'''

# Test case 3: JSON schema (should not trigger - not variables)
json_schema_code = '''
eol_agent = Agent(
    model=bedrock_model,
    system_prompt="""You are an assistant.

Return JSON in this format:
{
    "service": "string",
    "eol": "date"
}
""",
    tools=tools,
)
'''

detector = BedrockDetector()

print("=" * 80)
print("SYSTEM PROMPT DYNAMIC VARIABLE DETECTION - COMPREHENSIVE TEST")
print("=" * 80)

test_cases = [
    ("EOLTracker Pattern (service_url in system_prompt)", eol_code, True, ["service_url", "current_timestamp"]),
    ("Fixed Pattern (variables in user message)", fixed_code, False, []),
    ("JSON Schema (not variables)", json_schema_code, False, []),
]

all_passed = True

for name, code, should_find, expected_vars in test_cases:
    print(f"\n{'=' * 80}")
    print(f"TEST: {name}")
    print(f"{'=' * 80}")
    
    findings = detector.analyze(code, f"test_{name}.py")
    dynamic_prompt_findings = [f for f in findings if f['type'] == 'dynamic_system_prompt']
    
    found = len(dynamic_prompt_findings) > 0
    passed = found == should_find
    
    if found:
        finding = dynamic_prompt_findings[0]
        detected_vars = finding.get('dynamic_variables', [])
        vars_match = set(detected_vars) == set(expected_vars)
        passed = passed and vars_match
        
        print(f"\n✓ Finding detected:")
        print(f"  Severity: {finding['severity']}")
        print(f"  Variables: {detected_vars}")
        print(f"  Expected: {expected_vars}")
        print(f"  Variables match: {'✅' if vars_match else '❌'}")
        print(f"\n  Description: {finding['description']}")
        print(f"  Problem: {finding['problem']}")
        print(f"  Impact: {finding['impact']}")
    else:
        print(f"\n✓ No finding (as expected)" if should_find == False else "\n❌ No finding (should have detected)")
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n{status}")
    
    if not passed:
        all_passed = False

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

if all_passed:
    print("\n🎉 ALL TESTS PASSED!")
    print("\nThe detector correctly:")
    print("  ✅ Detects dynamic variables in system_prompt (service_url, current_timestamp)")
    print("  ✅ Ignores fixed patterns with variables in user messages")
    print("  ✅ Ignores JSON schema patterns (not Python variables)")
else:
    print("\n❌ SOME TESTS FAILED")
