"""Test Nova caching detection on EOLTracker."""

import json
import asyncio
from src.mcp_cost_optim_genai.scanner import ProjectScanner

async def main():
    scanner = ProjectScanner()
    result_json = await scanner.analyze_file("projects_sample/EOLTracker/cfn-templates/src/EOLMcpAgent.py")
    result = json.loads(result_json)
    
    print(f"Total findings: {result['total_findings']}")
    print()
    
    # Show all finding types
    for finding in result['findings']:
        print(f"Type: {finding['type']}")
        if 'line' in finding:
            print(f"  Line: {finding['line']}")
        if 'description' in finding:
            print(f"  Description: {finding['description'][:100]}...")
        print()

if __name__ == "__main__":
    asyncio.run(main())
