"""Test script to see what findings are generated."""
import asyncio
import json
from src.mcp_cost_optim_genai.scanner import ProjectScanner

async def main():
    scanner = ProjectScanner()
    print("Starting scan...")
    result_json = await asyncio.wait_for(
        scanner.scan_project("projects_sample/LifeCycleApi"),
        timeout=30.0
    )
    result = json.loads(result_json)
    
    print(f"\n{'='*80}")
    print(f"SCAN RESULTS FOR LifeCycleApi")
    print(f"{'='*80}\n")
    
    print(f"Files scanned: {result['files_scanned']}")
    print(f"Total findings: {result['total_findings']}")
    print(f"Actionable findings: {result.get('actionable_findings', 'N/A')}")
    print(f"Informational findings: {result.get('informational_findings', 'N/A')}")
    
    print(f"\n{'='*80}")
    print("FINDINGS SUMMARY (by_type)")
    print(f"{'='*80}\n")
    
    if 'findings_summary' in result:
        by_type = result['findings_summary'].get('by_type', {})
        for ftype, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            print(f"{ftype:45} {count:3}")
    else:
        print("⚠️  findings_summary NOT FOUND in result!")
    
    print(f"\n{'='*80}")
    print("ALL FINDINGS WITH LINE NUMBERS")
    print(f"{'='*80}\n")
    
    # Group findings by type
    by_type = {}
    for finding in result['findings']:
        ftype = finding.get('type', 'unknown')
        if ftype not in by_type:
            by_type[ftype] = []
        by_type[ftype].append(finding)
    
    # Show all findings with line numbers
    for ftype, findings in sorted(by_type.items()):
        severity = findings[0].get('severity', 'unknown')
        severity_label = f" [{severity}]" if severity else ""
        print(f"\n{ftype}{severity_label}: {len(findings)} findings")
        
        for i, f in enumerate(findings, 1):
            file_short = f.get('file', 'unknown').replace('projects_sample\\LifeCycleApi\\', '')
            line = f.get('line', 'N/A')
            desc = f.get('description', 'No description')[:80]
            print(f"  {i}. {file_short}:{line}")
            print(f"     {desc}")
        
        if len(findings) > 5:
            print(f"  (showing all {len(findings)} findings)")

if __name__ == "__main__":
    asyncio.run(main())
