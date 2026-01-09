"""Test detection of Strands Agent prompt syntax variations."""
import sys
sys.path.insert(0, 'src')

from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector

# Test case 1: Strands documentation example (static)
strands_static = '''
from strands import Agent

agent = Agent(
    system_prompt=(
        "You are a financial advisor specialized in retirement planning. "
        "Use tools to gather information and provide personalized advice. "
        "Always explain your reasoning and cite sources when possible."
    )
)
'''

# Test case 2: Strands with f-string (dynamic)
strands_dynamic = '''
from strands import Agent

user_specialty = "retirement planning"
agent = Agent(
    system_prompt=f"You are a financial advisor specialized in {user_specialty}. "
)
'''

# Test case 3: Strands with parentheses concatenation (static)
strands_concat_static = '''
from strands import Agent

agent = Agent(
    system_prompt=(
        "You are a helpful assistant. "
        "Provide clear and concise answers. "
        "Use tools when necessary."
    )
)
'''

# Test case 4: Strands with f-string in parentheses (dynamic)
strands_concat_dynamic = '''
from strands import Agent

user_role = "financial advisor"
agent = Agent(
    system_prompt=(
        f"You are a {user_role}. "
        "Provide personalized advice. "
        "Always cite sources."
    )
)
'''

# Test case 5: Multi-line f-string (dynamic)
strands_multiline = '''
from strands import Agent

specialty = "retirement planning"
agent = Agent(
    system_prompt=f"""You are a financial advisor specialized in {specialty}.
    
Use tools to gather information and provide personalized advice.
Always explain your reasoning and cite sources when possible."""
)
'''

detector = BedrockDetector()

print("=" * 80)
print("STRANDS AGENT PROMPT SYNTAX DETECTION TEST")
print("=" * 80)

test_cases = [
    ("Strands Static (parentheses)", strands_static, False, []),
    ("Strands Dynamic (f-string)", strands_dynamic, True, ["user_specialty"]),
    ("Strands Concat Static", strands_concat_static, False, []),
    ("Strands Concat Dynamic", strands_concat_dynamic, True, ["user_role"]),
    ("Strands Multi-line Dynamic", strands_multiline, True, ["specialty"]),
]

all_passed = True

for name, code, should_detect, expected_vars in test_cases:
    print(f"\n{'=' * 80}")
    print(f"TEST: {name}")
    print(f"{'=' * 80}")
    
    analysis = detector._analyze_system_prompt_staticness(code)
    
    is_dynamic = not analysis['is_static']
    detected_vars = analysis.get('dynamic_variables', [])
    
    passed = (is_dynamic == should_detect) and (set(detected_vars) == set(expected_vars))
    
    print(f"\nSystem Prompts Found: {analysis.get('system_prompts_found', 0)}")
    print(f"Is Dynamic: {is_dynamic} (expected: {should_detect})")
    print(f"Variables: {detected_vars} (expected: {expected_vars})")
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n{status}")
    
    if not passed:
        all_passed = False

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

if all_passed:
    print("\n🎉 ALL TESTS PASSED!")
    print("\nThe detector correctly handles:")
    print("  ✅ Strands static prompts (parentheses concatenation)")
    print("  ✅ Strands dynamic prompts (f-strings)")
    print("  ✅ Multi-line prompts with triple quotes")
    print("  ✅ Mixed static/dynamic in parentheses")
else:
    print("\n❌ SOME TESTS FAILED")
    print("\nNeed to enhance detection for Strands syntax variations")
