"""Test Nova caching detection on LifeCycleApi."""

import json
import asyncio
from src.mcp_cost_optim_genai.scanner import ProjectScanner

async def main():
    scanner = ProjectScanner()
    result_json = await scanner.analyze_file("projects_sample/LifeCycleApi/agent/data_extractor.py")
    result = json.loads(result_json)
    
    # Filter Nova findings
    nova_findings = [f for f in result['findings'] if 'nova' in f.get('type', '').lower()]
    
    print(f"Total findings: {result['total_findings']}")
    print(f"Nova-related findings: {len(nova_findings)}")
    print()
    
    for finding in nova_findings:
        print(f"Type: {finding['type']}")
        print(f"Line: {finding.get('line', 'N/A')}")
        print(f"Description: {finding.get('description', 'N/A')}")
        if 'cost_impact' in finding:
            print(f"Monthly Savings: {finding['cost_impact'].get('monthly_savings', 'N/A')}")
        print()

if __name__ == "__main__":
    asyncio.run(main())
