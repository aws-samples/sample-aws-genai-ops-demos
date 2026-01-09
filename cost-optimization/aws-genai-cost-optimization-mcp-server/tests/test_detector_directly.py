"""Test the Nova detector directly."""

from src.mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector

# Read test file
with open('test_files/nova_test.py', 'r') as f:
    content = f.read()

# Create detector
detector = BedrockDetector()

# Test the method directly
findings = detector._detect_nova_explicit_caching_opportunity(content, 'test_files/nova_test.py')

print(f"Nova explicit caching findings: {len(findings)}")
print()

for finding in findings:
    print(f"Type: {finding['type']}")
    print(f"Line: {finding.get('line', 'N/A')}")
    print(f"Description: {finding.get('description', 'N/A')}")
    if 'cost_impact' in finding:
        print(f"Monthly Savings: {finding['cost_impact'].get('monthly_savings', 'N/A')}")
    print()
