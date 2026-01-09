import sys
import asyncio
sys.path.insert(0, 'src')

from mcp_cost_optim_genai.scanner import ProjectScanner

async def main():
    scanner = ProjectScanner()
    result = await scanner.scan_project('projects_sample/EOLTracker')
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
