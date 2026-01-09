"""Test EOLTracker with all detectors."""
import sys
sys.path.insert(0, 'src')

from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector
from mcp_cost_optim_genai.detectors.vsc_detector import VscDetector
from pathlib import Path
import json

# Read EOLTracker code
content = Path('projects_sample/EOLTracker/cfn-templates/src/EOLMcpAgent.py').read_text()

# Run detectors
bedrock = BedrockDetector()
vsc = VscDetector()

bedrock_findings = bedrock.analyze(content, 'EOLMcpAgent.py')
vsc_findings = vsc.analyze(content, 'EOLMcpAgent.py')

print("=" * 80)
print("EOL TRACKER SCAN RESULTS")
print("=" * 80)
print(f"\nTotal findings: {len(bedrock_findings) + len(vsc_findings)}")
print(f"  - Bedrock: {len(bedrock_findings)}")
print(f"  - VSC: {len(vsc_findings)}")

print("\n" + "=" * 80)
print("BEDROCK FINDINGS")
print("=" * 80)

for i, finding in enumerate(bedrock_findings, 1):
    print(f"\n{i}. {finding['type'].upper()}")
    print(f"   Line: {finding.get('line', 'N/A')}")
    
    if finding['type'] == 'bedrock_model_usage':
        print(f"   Model: {finding['model_id']}")
        print(f"   Parsed: {finding['parsed']['family']} {finding['parsed']['tier']} v{finding['parsed']['version']}")
        print(f"   Cross-region: {finding['is_cross_region']}")
        if finding.get('cross_region_warning'):
            print(f"   Warning: {finding['cross_region_warning']['message']}")
    
    elif finding['type'] == 'strands_bedrock_model_config':
        print(f"   Model: {finding['model_id']}")
        print(f"   Tier: {finding['model_tier']}")
        if 'streaming' in finding:
            print(f"   Streaming: {finding['streaming']}")
            print(f"   Assessment: {finding['streaming_assessment']['status']}")
    
    elif finding['type'] == 'caching_cross_region_antipattern':
        print(f"   Severity: {finding['severity'].upper()}")
        print(f"   Model: {finding['model_id']}")
        print(f"   Prompt Analysis: {'Static' if finding['prompt_analysis']['is_static'] else 'Dynamic'}")
        print(f"   Description: {finding['description']}")
    
    else:
        desc = finding.get('description', finding.get('issue', 'N/A'))
        if len(desc) > 100:
            print(f"   Description: {desc[:100]}...")
        else:
            print(f"   Description: {desc}")

print("\n" + "=" * 80)
print("VSC FINDINGS")
print("=" * 80)

for i, finding in enumerate(vsc_findings, 1):
    print(f"\n{i}. {finding['type'].upper()}")
    print(f"   Line: {finding.get('line', 'N/A')}")
    print(f"   Description: {finding.get('description', 'N/A')}")
    if 'estimated_token_savings' in finding:
        print(f"   Token Savings: ~{finding['estimated_token_savings']} tokens/request")
    if 'optimization' in finding:
        print(f"   Technique: {finding['optimization'].get('technique', 'N/A')}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

# Categorize findings
model_findings = [f for f in bedrock_findings if f['type'] == 'bedrock_model_usage']
streaming_findings = [f for f in bedrock_findings if f['type'] == 'strands_bedrock_model_config' and 'streaming' in f]
cross_region_findings = [f for f in bedrock_findings if f['type'] == 'caching_cross_region_antipattern']

print(f"\n✓ Model Detection: {len(model_findings)} model(s) found")
if model_findings:
    for f in model_findings:
        print(f"  - {f['parsed']['family']} {f['parsed']['tier']} v{f['parsed']['version']}")

print(f"\n✓ Streaming Analysis: {len(streaming_findings)} configuration(s) found")
if streaming_findings:
    for f in streaming_findings:
        print(f"  - Streaming: {f['streaming']} - {f['streaming_assessment']['status']}")

print(f"\n✓ Cross-Region Analysis: {len(cross_region_findings)} finding(s)")
if cross_region_findings:
    for f in cross_region_findings:
        print(f"  - Severity: {f['severity'].upper()} - {'Static prompts' if f['prompt_analysis']['is_static'] else 'Dynamic prompts'}")

print(f"\n✓ VSC Opportunities: {len(vsc_findings)} finding(s)")
if vsc_findings:
    for f in vsc_findings:
        if 'estimated_token_savings' in f:
            print(f"  - ~{f['estimated_token_savings']} tokens/request savings")

print("\n" + "=" * 80)
