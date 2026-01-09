"""Test system prompt-specific dynamic variable detection."""
import sys
sys.path.insert(0, 'src')

from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector

# Test case 1: EOLTracker pattern - service_url in system_prompt
eol_tracker_code = '''
eol_agent = Agent(
    model=bedrock_model,
    system_prompt=f"""You are an AWS documentation analyst.

Your task:
1. Read and analyze the provided AWS service URL: {service_url}
2. Extract EOL information

The current timestamp is: {current_timestamp}
""",
    tools=tools,
)

# The query also has the URL (correct)
query = f"Extract EOL information from: {service_url}"
response = eol_agent(query)
'''

# Test case 2: Good pattern - no variables in system_prompt
good_pattern_code = '''
eol_agent = Agent(
    model=bedrock_model,
    system_prompt=f"""You are an AWS documentation analyst.

Your task:
1. Read and analyze the provided AWS service URL (will be in user message)
2. Extract EOL information

The current timestamp is: {current_timestamp}
""",
    tools=tools,
)

# URL only in user query (correct)
query = f"Extract EOL information from: {service_url}"
response = eol_agent(query)
'''

# Test case 3: Static system prompt (best)
static_prompt_code = '''
current_timestamp = datetime.now().strftime("%Y-%m-%d")

eol_agent = Agent(
    model=bedrock_model,
    system_prompt=f"""You are an AWS documentation analyst.

Your task:
1. Read and analyze the provided AWS service URL (will be in user message)
2. Extract EOL information

Last updated: {current_timestamp}
""",
    tools=tools,
)

# URL only in user query
query = f"Extract EOL information from: {service_url}"
response = eol_agent(query)
'''

detector = BedrockDetector()

print("=" * 80)
print("SYSTEM PROMPT DYNAMIC VARIABLE DETECTION TEST")
print("=" * 80)

test_cases = [
    ("EOLTracker Pattern (service_url in system_prompt)", eol_tracker_code, False),
    ("Good Pattern (service_url only in query)", good_pattern_code, False),
    ("Static System Prompt (timestamp per-invocation)", static_prompt_code, True),
]

for name, code, expected_static in test_cases:
    print(f"\n{'=' * 80}")
    print(f"TEST: {name}")
    print(f"{'=' * 80}")
    
    analysis = detector._analyze_system_prompt_staticness(code)
    
    print(f"\nSystem Prompts Found: {analysis.get('system_prompts_found', 0)}")
    print(f"Is Static: {analysis['is_static']}")
    print(f"Expected: {expected_static}")
    print(f"Confidence: {analysis['confidence']}")
    print(f"Indicators: {analysis['indicators']}")
    
    if analysis.get('dynamic_variables'):
        print(f"\n🚨 Dynamic Variables in system_prompt:")
        for var in analysis['dynamic_variables']:
            print(f"  - {var}")
    
    status = "✅ PASS" if analysis['is_static'] == expected_static else "❌ FAIL"
    print(f"\n{status}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print("\nExpected Results:")
print("  1. EOLTracker: Should detect service_url and current_timestamp as dynamic")
print("  2. Good Pattern: Should detect current_timestamp as dynamic")
print("  3. Static: Should detect current_timestamp as dynamic (per-invocation)")
print("\nNote: current_timestamp is technically dynamic but acceptable if set once per invocation")
