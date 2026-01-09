---
name: "genai-cost-optimizer"
displayName: "AWS GenAI Cost Optimizer"
description: "Scan code for AWS GenAI patterns (Bedrock, AgentCore) and identify cost optimization opportunities. Detects model usage, prompt caching opportunities, lifecycle configurations, and anti-patterns with clickable file links."
keywords: ["aws", "bedrock", "genai", "cost", "optimization", "agentcore", "claude", "nova", "prompt", "caching", "llm"]
author: "AWS"
---

# AWS GenAI Cost Optimizer

Scan your codebase for AWS GenAI service usage patterns and get actionable cost optimization insights through static code analysis.

## Overview

This power analyzes your code to detect AWS GenAI patterns and provides structured findings with cost considerations. It works alongside the AWS MCP Server for enriched cost analysis.

**Key Capabilities:**
- Detect Bedrock model usage (Claude, Nova, Titan, Llama, Mistral)
- Identify prompt caching opportunities (90% savings)
- Find prompt routing candidates (30-50% savings)
- Analyze AgentCore lifecycle configurations (4x cost impact)
- Detect cross-region caching anti-patterns (prevents 50%+ cost increase)
- VSC format optimization (up to 75% token reduction)

## Available Steering Files

- **bedrock-patterns** - Bedrock model detection, prompt caching, prompt routing workflows
- **agentcore-patterns** - AgentCore lifecycle configuration and session management
- **prompt-engineering** - Prompt optimization techniques and best practices

## MCP Server: genai-cost-optimizer

### Tools

| Tool | Description |
|------|-------------|
| `scan_project(path)` | Scan entire project directory for GenAI patterns |
| `analyze_file(path)` | Analyze single file for patterns |

**scan_project parameters:**
- `path` (required): Project directory path
- `skip_dirs`: Comma-separated directories to skip (e.g., "data,models")
- `max_files`: Limit files scanned (0 = unlimited)
- `estimate_only`: Preview scan size without scanning

### Resources

| Resource | Content |
|----------|---------|
| `scan://latest` | Most recent scan results |

### Prompts (Guided Workflows)

| Prompt | Focus |
|--------|-------|
| `quick_cost_scan` | Fast scan for high-impact findings |
| `comprehensive_analysis` | Full analysis with detailed report |
| `bedrock_only_analysis` | Bedrock models and prompts only |
| `agentcore_only_analysis` | AgentCore Runtime configurations |
| `analyze_with_current_models` | Dynamic model discovery workflow |

## Integration Workflows

### Basic Pattern Detection
```
Scan my project for GenAI cost optimization opportunities
```

### Enhanced Cost Analysis (with AWS MCP)
```
1. Scan project for patterns
2. Get current Bedrock pricing for detected models
3. Calculate potential savings with prompt caching
4. Generate cost optimization report
```

### Automated Optimization (with AWS MCP Agent SOPs)
```
1. Detect AgentCore lifecycle misconfigurations
2. Use Agent SOP to optimize lifecycle settings
3. Implement prompt caching for recurring prompts
4. Set up cost monitoring and alerts
```

## What It Detects

### Bedrock Patterns
- Model usage (all providers: Claude, Nova, Titan, Llama, Mistral)
- API patterns (sync, streaming, OpenAI Chat Completions)
- Prompt caching opportunities (90% savings)
- Prompt routing candidates (30-50% savings)
- Cross-region caching anti-patterns

### AgentCore Runtime
- Lifecycle configurations (idle timeout, max lifetime)
- Session termination patterns (StopRuntimeSession)
- Decorator usage (@entrypoint, @async_task)
- Streaming and async processing

### Prompt Engineering
- Recurring prompts with static content
- LLM calls in loops
- Large prompts without caching
- Nova optimization opportunities
- VSC format candidates (JSON in prompts)

## Example Findings

**Nova Explicit Caching Opportunity:**
```
Type: nova_explicit_caching_opportunity
Model: amazon.nova-lite-v1:0
Savings: $8.09/month (90% reduction)
```

**Cross-Region Anti-Pattern:**
```
Type: caching_cross_region_antipattern
Severity: HIGH
Issue: Global inference profile + caching
Impact: 50%+ cost INCREASE
```

**AgentCore Lifecycle Alert:**
```
Type: agentcore_lifecycle_idle_timeout
Configured: 3600s (60 min)
Default: 900s (15 min)
Impact: 4x longer billing for idle instances
```

## Companion MCP Servers

For complete cost analysis and workflow automation, use with these **AWS MCP Servers**:

| Server | Purpose |
|--------|---------|
| `aws-mcp` | AWS CLI commands, documentation, Agent SOPs, and workflow automation |
| `aws-pricing` | Real-time AWS pricing data and cost analysis for all services |

**Why Both Servers?**
- **AWS MCP Server**: Provides 15,000+ AWS APIs, documentation search, Agent SOPs for complex workflows
- **AWS Pricing MCP Server**: Provides current pricing data including latest Bedrock models (Nova, Claude 4.x)
- **Together**: Complete workflow from pattern detection → current pricing → optimization guidance

## Supported Languages

- Python (`.py`) - Full AST + regex analysis
- TypeScript/JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`)
- Shell scripts (`.sh`, `.bash`)
- Configuration files (`.yml`, `.yaml`)

## Prerequisites

### Installation (Required First Time)

The MCP server must be installed before first use. Run this once:

```bash
# Install from local project directory
cd /path/to/aws-genai-cost-optimization-mcp-server
uv tool install .

# To update after code changes
uv tool install . --force --reinstall
```

**Note:** First-time `uvx` runs may timeout while downloading. Pre-installing avoids this.

### AWS Credentials (Optional)

**Optional but recommended:** AWS credentials for AI-powered prompt detection (Nova Micro).

Without credentials:
- ✅ All detectors work
- ⚠️ AI-powered prompt detection falls back to regex
- 📊 Accuracy: ~88% with AI vs ~23% with regex only

### AWS MCP Servers Setup (Recommended)

For enriched cost analysis and workflow automation, both MCP servers are configured automatically with this power:

```json
{
  "mcpServers": {
    "aws-mcp": {
      "command": "uvx",
      "timeout": 100000,
      "transport": "stdio",
      "args": [
        "mcp-proxy-for-aws@latest",
        "https://aws-mcp.us-east-1.api.aws/mcp",
        "--metadata",
        "AWS_REGION=us-west-2"
      ]
    },
    "aws-pricing": {
      "command": "uvx",
      "args": ["awslabs.aws-pricing-mcp-server@latest"],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

**What this enables:**
- **Current Bedrock pricing** including Nova models and latest Claude versions
- **Agent SOPs** for cost optimization workflows  
- **AWS documentation** and best practices
- **Multi-service cost analysis** across your infrastructure

## Troubleshooting

**Scan times out on large projects:**
```
scan_project("/path", max_files=500)
```

**Too many findings:**
```
Use quick_cost_scan for high-impact only
```

**Missing findings:**
- Ensure file extensions are supported
- Check skip_dirs isn't excluding your code

## Best Practices

### Workflow Integration
1. **Start with pattern detection** using this power's scan tools
2. **Enrich with AWS MCP** for real-time pricing and current model data
3. **Use Agent SOPs** for automated optimization workflows
4. **Monitor costs** with AWS MCP's built-in cost management

### Optimization Priority
1. **AgentCore lifecycle** first (highest cost impact - 4x difference)
2. **Prompt caching** for repeated static content (90% savings)
3. **Prompt routing** when using multiple model tiers (30-50% savings)
4. **Cross-region patterns** to avoid cost increases (50%+ impact)
5. **VSC format optimization** for JSON-heavy prompts (75% token reduction)

### Cost Analysis Workflow
```
# 1. Detect patterns
scan_project("/path/to/project")

# 2. Get current pricing (via AWS Pricing MCP)
"Get current pricing for anthropic.claude-3-sonnet-20240229-v1:0"

# 3. Calculate savings (via AWS Pricing MCP)
"Calculate monthly savings if I implement prompt caching for these 5 recurring prompts"

# 4. Implement optimizations (via AWS MCP Agent SOPs)
"Use Agent SOP to optimize my AgentCore lifecycle configuration"
```
