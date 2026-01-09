"""Test Nova detection on simple test file."""

import json
import asyncio
from src.mcp_cost_optim_genai.scanner import ProjectScanner

async def main():
    scanner = ProjectScanner()
    result_json = await scanner.analyze_file("test_files/nova_test.py")
    result = json.loads(result_json)
    
    print(f"Total findings: {result['total_findings']}")
    print()
    
    for finding in result['findings']:
        print(f"{'='*80}")
        print(f"Type: {finding['type']}")
        if 'line' in finding:
            print(f"Line: {finding['line']}")
        if 'description' in finding:
            print(f"Description: {finding['description']}")
        if 'cost_impact' in finding:
            print(f"\nCost Impact:")
            print(f"  Monthly Savings: {finding['cost_impact'].get('monthly_savings', 'N/A')}")
        if 'optimization' in finding and isinstance(finding['optimization'], dict):
            print(f"\nOptimization:")
            print(f"  Technique: {finding['optimization'].get('technique', 'N/A')}")
            print(f"  Difficulty: {finding['optimization'].get('difficulty', 'N/A')}")
        print()

if __name__ == "__main__":
    asyncio.run(main())
