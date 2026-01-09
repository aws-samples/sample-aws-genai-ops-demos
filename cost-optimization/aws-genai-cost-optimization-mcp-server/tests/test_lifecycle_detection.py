from src.mcp_cost_optim_genai.detectors.prompt_engineering_detector import PromptEngineeringDetector

detector = PromptEngineeringDetector()

with open('projects_sample/LifeCycleApi/agent/data_extractor.py', 'r', encoding='utf-8') as f:
    content = f.read()

print("Analyzing...")
findings = detector.analyze(content, 'data_extractor.py')

print(f"\nTotal findings: {len(findings)}")
for finding in findings:
    print(f"\nType: {finding['type']}")
    if 'function_name' in finding:
        print(f"Function: {finding['function_name']}")
    if 'call_count' in finding:
        print(f"Call count: {finding['call_count']}")
    if 'estimated_static_tokens' in finding:
        print(f"Estimated tokens: {finding['estimated_static_tokens']}")
