"""MCP server implementation for GenAI cost optimization."""

import json
from pathlib import Path
from fastmcp import FastMCP
from .scanner import ProjectScanner
from .presentation_guidelines import PRESENTATION_SUMMARY

# Initialize FastMCP server
mcp = FastMCP("genai-cost-optimizer")

# Cache for scan results
_scan_cache = {}
_latest_scan = None


# ============================================================================
# TOOLS - Execute scans and analysis
# ============================================================================

@mcp.tool()
async def scan_project(
    path: str,
    skip_dirs: str = "",
    max_files: int = 0,
    estimate_only: bool = False
) -> str:
    """Scan a project directory for AWS GenAI service usage patterns.
    
    Uses smart filtering to skip common build/dependency directories (node_modules,
    venv, cdk.out, etc.) and avoid timeouts on large projects.
    
    Args:
        path: Path to the project directory to scan
        skip_dirs: Comma-separated list of additional directory names to skip
                  (e.g., "my_cache,custom_build"). Merged with default skip list.
        max_files: Maximum number of files to scan (0 = unlimited). Use this as a
                  safety limit for very large projects.
        estimate_only: If true, only return scan size estimate without actually scanning.
                      Useful for checking if a scan will be too large.
        
    Returns:
        JSON string with scan results and findings.
        
        {PRESENTATION_SUMMARY}
        
    Examples:
        # Basic scan with defaults
        scan_project("/path/to/project")
        
        # Estimate scan size first
        scan_project("/path/to/project", estimate_only=True)
        
        # Limit to 500 files for quick scan
        scan_project("/path/to/project", max_files=500)
        
        # Skip additional custom directories
        scan_project("/path/to/project", skip_dirs="data,models,checkpoints")
    """
    global _latest_scan
    scanner = ProjectScanner()
    
    # Parse skip_dirs if provided
    custom_skip_dirs = None
    if skip_dirs:
        custom_skip_dirs = set(d.strip() for d in skip_dirs.split(",") if d.strip())
    
    # Convert max_files (0 means None/unlimited)
    max_files_param = max_files if max_files > 0 else None
    
    result = await scanner.scan_project(
        path,
        skip_dirs=custom_skip_dirs,
        max_files=max_files_param,
        estimate_only=estimate_only
    )
    
    if not estimate_only:
        _latest_scan = result
    
    return result


@mcp.tool()
async def analyze_file(path: str) -> str:
    """Analyze a single file for AWS GenAI usage patterns.
    
    Args:
        path: Path to the file to analyze
        
    Returns:
        JSON string with analysis results and findings
    """
    scanner = ProjectScanner()
    return await scanner.analyze_file(path)


# ============================================================================
# RESOURCES - Access structured data
# ============================================================================

@mcp.resource("scan://latest")
async def get_latest_scan() -> str:
    """Get the most recent scan results."""
    if _latest_scan:
        return _latest_scan
    return json.dumps({"error": "No scans performed yet"})




# ============================================================================
# PROMPTS - Guided workflows
# ============================================================================

@mcp.prompt()
async def quick_cost_scan():
    """Quick cost optimization scan focusing on high-impact findings."""
    return {
        "messages": [
            {
                "role": "user",
                "content": """Run a quick cost optimization scan:

1. Use scan_project() on the current directory
2. Focus on high-impact findings:
   - AgentCore lifecycle configurations (4x cost impact)
   - Repeated prompt context (90% savings potential)
   - Prompt routing opportunities (50%+ savings)
3. Summarize top 3 optimization opportunities
4. Provide estimated savings for each

Keep it concise and actionable."""
            }
        ]
    }


@mcp.prompt()
async def comprehensive_analysis():
    """Comprehensive cost analysis with detailed recommendations."""
    return {
        "messages": [
            {
                "role": "user",
                "content": """Perform comprehensive GenAI cost analysis:

1. Scan entire project with scan_project()
2. Analyze all finding categories:
   - LLM model selection
   - Prompt engineering opportunities
   - AgentCore Runtime configurations
   - Cross-service patterns
3. Access optimization://patterns for reference
4. Calculate potential savings using AWS MCP Server
5. Generate detailed report with:
   - Executive summary
   - Findings by category
   - Prioritized recommendations
   - Implementation steps

Be thorough and data-driven."""
            }
        ]
    }


@mcp.prompt()
async def bedrock_only_analysis():
    """Analyze Bedrock usage only (models and prompts)."""
    return {
        "messages": [
            {
                "role": "user",
                "content": """Analyze Bedrock usage patterns:

1. Scan project for Bedrock patterns
2. Focus on:
   - Model selection (Claude, Nova, Titan, Llama)
   - Prompt optimization (caching, quality, routing)
   - Token usage patterns
3. Access optimization://tools for recommendations
4. Provide specific optimization steps

Ignore AgentCore and other services."""
            }
        ]
    }


@mcp.prompt()
async def agentcore_only_analysis():
    """Analyze AgentCore Runtime configurations only."""
    return {
        "messages": [
            {
                "role": "user",
                "content": """Analyze AgentCore Runtime configurations:

1. Scan for AgentCore patterns
2. Focus on:
   - Lifecycle configurations (idle timeout, max lifetime)
   - Deployment patterns
   - Session management
   - Async processing
3. Compare against AWS defaults
4. Calculate cost impact of current settings
5. Provide optimization recommendations

This is critical for cost control."""
            }
        ]
    }


@mcp.prompt()
async def analyze_with_current_models():
    """Analyze project with awareness of latest Bedrock models.
    
    This prompt guides the AI to use AWS MCP Server for dynamic model
    discovery, ensuring recommendations always use current model information.
    """
    return {
        "messages": [
            {
                "role": "user",
                "content": """Analyze Bedrock usage with current model information:

CRITICAL: Never hardcode model IDs or versions. Always fetch current information.

1. Scan project with scan_project()

2. For each Bedrock model found:
   a. Access bedrock://models/catalog for tier-based guidance
   b. Use AWS MCP Server to search "Bedrock [family] latest models"
      - For Claude: "Bedrock Claude latest models"
      - For Nova: "Bedrock Nova latest models"
   c. Read https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html
   d. If AWS MCP Server is available, get current pricing for comparison

3. Analyze model appropriateness:
   - Detect use case from code context (structured extraction, reasoning, chat, etc.)
   - Compare detected model tier vs use case complexity
   - Check if lower-tier models could handle the task

4. Provide recommendations based on:
   - Current model catalog (fetched dynamically, not hardcoded)
   - Use case fit (reasoning vs structured data vs chat)
   - Tier appropriateness (haiku vs sonnet vs opus, or micro vs lite vs pro)
   - Current pricing (if available)

5. Format recommendations as:
   - Current state: [detected model and tier]
   - Use case: [what the code is doing]
   - Tier analysis: [is current tier appropriate?]
   - Alternatives: [list current models from lower tiers if applicable]
   - Next steps: [testing recommendations]

Example output:
"Current: Claude 3.7 Sonnet (premium tier)
Use case: Structured JSON extraction from documentation
Analysis: Premium tier (Sonnet) is over-provisioned for structured extraction
Alternatives: Consider Claude 4.5 Haiku (latest fast tier) - check current pricing
Next steps: Test with sample data to validate accuracy"

Remember: Model versions change frequently. Always fetch current information."""
            }
        ]
    }


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
