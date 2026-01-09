#!/usr/bin/env python3
"""Quick test script for scanning projects in projects_sample/"""

import asyncio
import sys
from pathlib import Path
from src.mcp_cost_optim_genai.scanner import ProjectScanner


async def test_scan(project_path: str):
    """Scan a project and print results."""
    print(f"\n{'='*60}")
    print(f"Scanning: {project_path}")
    print(f"{'='*60}\n")
    
    scanner = ProjectScanner()
    result = await scanner.scan_project(project_path)
    
    print(result)
    print(f"\n{'='*60}")
    print("Scan complete!")
    print(f"{'='*60}\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_project.py <project_path>")
        print("\nExamples:")
        print("  python test_project.py projects_sample/my-bedrock-app")
        print("  python test_project.py projects_sample/agentcore-project")
        print("\nAvailable projects:")
        projects_dir = Path("projects_sample")
        if projects_dir.exists():
            for item in projects_dir.iterdir():
                if item.is_dir():
                    print(f"  - {item}")
        else:
            print("  (no projects yet - add some to projects_sample/)")
        sys.exit(1)
    
    project_path = sys.argv[1]
    
    if not Path(project_path).exists():
        print(f"Error: Project not found: {project_path}")
        sys.exit(1)
    
    asyncio.run(test_scan(project_path))


if __name__ == "__main__":
    main()
