"""Test static vs dynamic prompt detection."""
import sys
sys.path.insert(0, 'src')

from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector

# Test case 1: Static prompts (like EOLTracker)
static_code = '''
bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    temperature=0.1,
    streaming=True,
)

eol_agent = Agent(
    model=bedrock_model,
    system_prompt="""You are an AWS documentation analyst.

Your task:
1. Read and analyze the provided AWS service URL
2. Extract EOL information
3. Structure the information in JSON format

CRITICAL: Your response must be ONLY valid JSON.""",
    tools=tools,
)

# Enable caching
response = invoke_model(
    modelId=model_id,
    body=json.dumps({
        "messages": messages,
        "cacheControl": {"type": "ephemeral"}
    })
)
'''

# Test case 2: Dynamic prompts with f-strings
dynamic_code = '''
bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    temperature=0.1,
)

user_name = "John"
task_type = "analysis"

eol_agent = Agent(
    model=bedrock_model,
    system_prompt=f"""You are an AWS documentation analyst for {user_name}.

Your task is to perform {task_type} on the provided data.

Please analyze carefully.""",
    tools=tools,
)

# Enable caching
response = invoke_model(
    modelId=model_id,
    body=json.dumps({
        "messages": messages,
        "cacheControl": {"type": "ephemeral"}
    })
)
'''

# Test case 3: Dynamic prompts with .format()
format_code = '''
bedrock_model = BedrockModel(
    model_id="global.anthropic.claude-3-7-sonnet-20250219-v1:0",
    temperature=0.1,
)

prompt_template = """You are analyzing {} for user {}.
Please provide {} analysis."""

eol_agent = Agent(
    model=bedrock_model,
    system_prompt=prompt_template.format(service_name, user_id, analysis_type),
    tools=tools,
)

# Enable caching
response = invoke_model(
    modelId=model_id,
    body=json.dumps({
        "messages": messages,
        "cache_control": {"type": "ephemeral"}
    })
)
'''

detector = BedrockDetector()

print("=" * 80)
print("STATIC PROMPT DETECTION TEST")
print("=" * 80)

print("\n" + "=" * 80)
print("TEST 1: STATIC PROMPTS (like EOLTracker)")
print("=" * 80)
findings1 = detector.analyze(static_code, "test_static.py")
cross_region1 = [f for f in findings1 if f['type'] == 'caching_cross_region_antipattern']
print(f"\nCross-region findings: {len(cross_region1)}")
if cross_region1:
    for f in cross_region1:
        print(f"\nSeverity: {f['severity'].upper()}")
        print(f"Model: {f['model_id']}")
        print(f"Prompt Analysis:")
        print(f"  - Is Static: {f['prompt_analysis']['is_static']}")
        print(f"  - Confidence: {f['prompt_analysis']['confidence']}")
        print(f"  - Indicators: {f['prompt_analysis']['indicators']}")
        print(f"\nDescription: {f['description']}")
else:
    print("❌ NO FINDINGS - This is the problem!")

print("\n" + "=" * 80)
print("TEST 2: DYNAMIC PROMPTS (f-strings)")
print("=" * 80)
findings2 = detector.analyze(dynamic_code, "test_dynamic.py")
cross_region2 = [f for f in findings2 if f['type'] == 'caching_cross_region_antipattern']
print(f"\nCross-region findings: {len(cross_region2)}")
if cross_region2:
    for f in cross_region2:
        print(f"\nSeverity: {f['severity'].upper()}")
        print(f"Model: {f['model_id']}")
        print(f"Prompt Analysis:")
        print(f"  - Is Static: {f['prompt_analysis']['is_static']}")
        print(f"  - Confidence: {f['prompt_analysis']['confidence']}")
        print(f"  - Indicators: {f['prompt_analysis']['indicators']}")
        print(f"\nDescription: {f['description']}")
else:
    print("❌ NO FINDINGS")

print("\n" + "=" * 80)
print("TEST 3: DYNAMIC PROMPTS (.format() with global profile)")
print("=" * 80)
findings3 = detector.analyze(format_code, "test_format.py")
cross_region3 = [f for f in findings3 if f['type'] == 'caching_cross_region_antipattern']
print(f"\nCross-region findings: {len(cross_region3)}")
if cross_region3:
    for f in cross_region3:
        print(f"\nSeverity: {f['severity'].upper()}")
        print(f"Model: {f['model_id']}")
        print(f"Prompt Analysis:")
        print(f"  - Is Static: {f['prompt_analysis']['is_static']}")
        print(f"  - Confidence: {f['prompt_analysis']['confidence']}")
        print(f"  - Indicators: {f['prompt_analysis']['indicators']}")
        print(f"\nDescription: {f['description']}")
else:
    print("❌ NO FINDINGS")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"\nTest 1 (Static + US region): {len(cross_region1)} findings")
print(f"Test 2 (Dynamic + US region): {len(cross_region2)} findings")
print(f"Test 3 (Dynamic + Global): {len(cross_region3)} findings")

print("\n✅ Expected behavior:")
print("  - Test 1: INFO severity (static prompts are OK)")
print("  - Test 2: MEDIUM severity (dynamic prompts with US region)")
print("  - Test 3: HIGH severity (dynamic prompts with global)")
