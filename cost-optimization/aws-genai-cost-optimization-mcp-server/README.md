# AWS GenAI Cost Optimization Kiro Power

An MCP server that scans your code for AWS GenAI service usage and provides cost optimization insights through static code analysis.

## What it does

Analyzes your codebase to detect AWS GenAI patterns and provides structured findings with cost considerations. Works alongside the [AWS MCP Server](https://docs.aws.amazon.com/aws-mcp/latest/userguide/what-is-mcp-server.html) and [AWS Pricing MCP Server](https://awslabs.github.io/mcp/servers/aws-pricing-mcp-server) for enriched cost analysis.

## Prerequisites

- **Python 3.10+** and **uvx** for local MCP server installation on your laptop
- **AWS Credentials** (optional): For AI-powered prompt detection with higher accuracy
- **Kiro IDE** (if using as Kiro Power)

## Installation

### Using `uvx`

1. Install `uvx` from [Astral](https://docs.astral.sh/uv/getting-started/installation/) or the [GitHub README](https://github.com/astral-sh/uv#installation)
2. Install Python using `uv python install 3.10`
3. Install the MCP server:

```bash
# Install GenAi Cost Optimization MCP server on your laptop (from this current directory)
uv tool install .
```

## Configuration

Click on the relevant below badge to automatically add this IDE MCP server to your MCP client configuration.

| Kiro | Cursor | VS Code |
|:----:|:------:|:-------:|
| [![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=genai-cost-optimizer&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22mcp-cost-optim-genai%22%5D%2C%22env%22%3A%7B%22FASTMCP_LOG_LEVEL%22%3A%22ERROR%22%7D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%22scan_project%22%2C%22analyze_file%22%5D%7D) | [![Install MCP Server](https://cursor.com/deeplink/mcp-install-light.svg)](https://cursor.com/en/install-mcp?name=genai-cost-optimizer&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJtY3AtY29zdC1vcHRpbS1nZW5haSJdLCJlbnYiOnsiRkFTVE1DUF9MT0dfTEVWRUwiOiJFUlJPUiJ9LCJkaXNhYmxlZCI6ZmFsc2UsImF1dG9BcHByb3ZlIjpbInNjYW5fcHJvamVjdCIsImFuYWx5emVfZmlsZSJdfQ%3D%3D) | [![Install on VS Code](https://img.shields.io/badge/Install_on-VS_Code-FF9900?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=GenAI%20Cost%20Optimizer&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22mcp-cost-optim-genai%22%5D%2C%22env%22%3A%7B%22FASTMCP_LOG_LEVEL%22%3A%22ERROR%22%7D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%22scan_project%22%2C%22analyze_file%22%5D%7D) |

Or manually configure the MCP server in your MCP client configuration:

```json
{
  "mcpServers": {
    "genai-cost-optimizer": {
      "command": "uvx",
      "args": ["mcp-cost-optim-genai"],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      },
      "disabled": false,
      "autoApprove": ["scan_project", "analyze_file"]
    }
  }
}
```

If you are using Kiro we recommend you to configure this MCP Server as a [Kiro Power](https://kiro.dev/powers/) rather than a simple MCP Server.

Follow these steps to add a Kiro Power:

1. **Install MCP server** (see above)
2. **Click Powers icon** in Kiro left menu
3. **Add Custom Power** → "Import power from a folder" → browse to `power/` folder
4. **Test**: Try "Scan my project for GenAI cost optimization opportunities"


## Cost Estimates

### Operational Costs
- **MCP Server**: No runtime costs (runs locally)
- **AI Enhancement (Optional)**: ~$0.000035 per 1K tokens via Nova Micro
- **Typical Project Scan**: <$0.01 per scan
- **Monthly Usage**: $1-5 for regular scanning of multiple projects

### AWS Service Costs (If Using Enhanced Features)
- **Amazon Bedrock Nova Micro**: $0.000035 per 1K input tokens, $0.00014 per 1K output tokens
- **AWS Pricing API**: No additional charges (included with AWS account)

### Value Delivered
- **Prompt Caching Optimization**: Up to 90% cost reduction on recurring prompts
- **Model Tier Optimization**: 30-50% savings through intelligent model selection
- **AgentCore Lifecycle**: Up to 4x cost reduction from proper timeout configuration
- **VSC Format Optimization**: Up to 75% token reduction for JSON-heavy prompts

**ROI**: Typical savings of $50-500/month easily justify the <$5/month operational cost.

## Detectors

| Category | Detector | Features | Documentation |
|----------|----------|----------|---------------|
| **LLM** | Bedrock API & Features | **Generic model detection (future-proof)**, Model selection (ALL providers), API patterns (sync/streaming/**OpenAI Chat Completions**), Prompt caching, **Prompt routing**, **AWS Strands support**, **Nova explicit caching (90% savings)**, **Cross-region detection** | [→ Details](doc/bedrock-detector.md#prompt-routing) |
| **Prompt Engineering** | Prompt Optimization | Recurring prompts (AST), Repeated context (regex), Prompt quality analysis, Nova Optimizer, LLM calls in loops, Token usage, **VSC format (up to 75% token savings)**, **JSON in prompts detection** | [→ Details](doc/prompt-engineering.md) |
| **AgentCore Runtime** | Lifecycle Configuration | Idle timeout analysis, Max lifetime analysis, Cost alerts (critical), Session termination (StopRuntimeSession) | [→ Details](doc/agentcore-runtime.md#1-lifecycle-configuration-critical-for-cost) |
| **AgentCore Runtime** | Deployment Patterns | Decorators (@entrypoint, @async_task), Session management, Streaming, Async processing | [→ Details](doc/agentcore-runtime.md) |
| **Cross-Service** | Cost Amplification | Streaming in AgentCore Runtime, Service combination impacts | [→ Details](doc/cross-cutting-patterns.md) |
| **Anti-Patterns** | Cross-Region Caching | **Detects caching with global/cross-region inference profiles (can INCREASE costs by 50%+)** | [→ Details](doc/CACHING_CROSS_REGION_ANTIPATTERN.md) |

## Usage

### As Kiro Power (Recommended)

The server is designed to work seamlessly as a Kiro Power with automatic activation:

#### Automatic Activation Keywords

The power activates automatically when you mention any of these keywords:

- **Core Terms**: "bedrock", "cost", "genai", "optimization"
- **AWS Services**: "agentcore", "claude", "nova", "prompt", "caching"
- **Cost Analysis**: "llm", "model", "pricing", "savings"

#### Complete Workflow

Let's walk through a complete workflow to understand how each component works together:

**User Request:**
```
"Scan my project for GenAI cost optimization opportunities"
```

**Complete Workflow:**

```
┌─────────────────┐    ┌─────────────────┐    ┌───────────────────────┐   ┌───────────────────┐
│      USER       │    │   KIRO AGENT    │    │ genai-cost-optimizer  │   │   AWS MCP Server  │
│                 │    │  (MCP Client)   │    │       MCP Server      │   │    "Companion"    │
└─────────────────┘    └─────────────────┘    └───────────────────────┘   └───────────────────┘
         │                       │                       │                       │
         │ 1. Natural language   │                       │                       │
         │ cost optimization     │                       │                       │
         │ request               │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │                       │ 2. Detects keywords   │                       │
         │                       │ & activates power     │                       │
         │                       │ (bedrock, cost, etc.) │                       │
         │                       │                       │                       │
         │                       │ 3. scan_project       │                       │
         │                       │ (path, options)       │                       │
         │                       ├──────────────────────►│                       │
         │                       │                       │                       │
         │                       │ 4. Static code        │                       │
         │                       │ analysis results      │                       │
         │                       │ (patterns + findings) │                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │                       │ 5. IF ENRICHMENT:     │                       │
         │                       │ Get current Bedrock   │                       │
         │                       │ pricing & models      │                       │
         │                       ├───────────────────────┼──────────────────────►│
         │                       │                       │                       │
         │                       │ 6. Current pricing    │                       │
         │                       │ & model data          │                       │
         │                       │◄──────────────────────┼───────────────────────┤
         │                       │                       │                       │
         │                       │ 7. Agent calculates   │                       │
         │                       │ cost savings &        │                       │
         │                       │ generates report      │                       │
         │                       │                       │                       │
         │ 8. Actionable cost    │                       │                       │
         │ optimization report   │                       │                       │
         │ with savings estimates│                       │                       │
         │◄──────────────────────┤                       │                       │
         │                       │                       │                       │
```

**Component Responsibilities:**

### 🧑‍💻 User
- **Provides**: Project path or specific cost optimization request
- **Receives**: Detailed cost optimization report with specific savings estimates
- **Example**: "Check my Lambda functions for GenAI cost issues"

### 🤖 Kiro Agent (MCP Client)
- **Keyword Detection**: Automatically activates on "bedrock", "cost", "genai", "optimization"
- **Orchestration**: Coordinates MCP server interactions and enrichment
- **Analysis**: Interprets scan results and calculates potential savings
- **Report Generation**: Creates actionable recommendations with cost estimates

### 🔧 genai-cost-optimizer MCP Server
- **Pattern Detection**: Scans code for Bedrock, AgentCore, prompt engineering patterns
- **Cost Analysis**: Identifies optimization opportunities (caching, model tiers, lifecycle)
- **Anti-Pattern Detection**: Finds configurations that increase costs
- **Static Analysis**: AST + regex analysis across multiple languages

### ☁️ AWS MCP Server (Optional)
- **Current Pricing**: Real-time Bedrock model pricing and availability
- **Model Information**: Latest model capabilities and cost comparisons
- **Documentation**: Current AWS best practices and optimization guides

**Key Architecture Principles:**
- **Agent Orchestration**: Only the Kiro Agent coordinates between servers
- **Static Analysis**: No runtime execution, only code pattern detection
- **Enrichment Optional**: Works standalone or with AWS MCP for enhanced analysis
- **Actionable Results**: Specific file locations, line numbers, and cost estimates

### Available Tools

Once configured in your MCP client, use natural language to interact:

```
Scan my project at /path/to/my-project for cost optimization opportunities
```

The scanner will analyze your code and return findings with:
- 📍 Exact file and line numbers
- 💰 Cost impact estimates
- ✨ Specific optimization recommendations
- 📝 Code examples (before/after)
- 📚 AWS documentation links

### Example Findings

**1. Nova Explicit Caching Opportunity**
```
Type: nova_explicit_caching_opportunity
Model: amazon.nova-lite-v1:0
Savings: $8.09/month (90% reduction)
Implementation: 5-10 minutes
```

**2. VSC Format Optimization**
```
Type: json_serialization_near_llm_call
Pattern: json.dumps() before invoke_agent_runtime()
Savings: ~65 tokens per call (up to 75% reduction)
Use When: Flat, tabular data with known schema
```

**3. Cross-Region Caching Anti-Pattern**
```
Type: caching_cross_region_antipattern
Severity: HIGH
Issue: Global inference profile + caching
Impact: 50%+ cost INCREASE (not savings!)
Action: Use single-region model ID instead
```

### Available Tools

**Execute Analysis:**
- `scan_project(path)` - Scan entire project directory
- `analyze_file(path)` - Analyze single file

**Access Data:**
- `scan://latest` - Most recent scan results

**Guided Workflows:**
- `quick_cost_scan` - Fast scan (high-impact findings only)
- `comprehensive_analysis` - Detailed analysis with full report
- `bedrock_only_analysis` - Bedrock models and prompts only
- `agentcore_only_analysis` - AgentCore Runtime configurations only
- `analyze_with_current_models` - Analyze with latest Bedrock models (dynamic discovery)

### Usage Examples

**Basic Scanning:**
```
Scan my project for GenAI cost optimization opportunities
Analyze the file src/agent.py for cost issues
```

**Guided Workflows:**
```
Use quick_cost_scan to check my project
Run comprehensive_analysis and generate a report
Use bedrock_only_analysis to optimize my models
```

**Access Reference Data:**
```
Show me what optimization patterns are available
What tools can help with cost optimization?
Show me the latest scan results
```

**Why Resources & Prompts Matter:**
- **Discoverability**: See what patterns the server can detect before scanning
- **Guidance**: Pre-built workflows for common tasks
- **Structure**: Access optimization tools as structured data
- **Composability**: Combine with AWS MCP Server for complete workflows

See [MCP Features](doc/MCP_FEATURES.md) for detailed usage patterns.

## Supported Languages

- Python (`.py`)
- TypeScript/JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`)
- Shell scripts (`.sh`, `.bash`)
- Configuration files (`.yml`, `.yaml`)

## Required MCP Servers

This scanner works alongside **AWS MCP Servers** for complete cost analysis:

### AWS MCP Server
**Package:** `mcp-proxy-for-aws@latest`  
**Purpose:** AWS CLI commands, documentation, Agent SOPs, and workflow automation  
**Provides:** 15,000+ AWS APIs, documentation search, regional availability, execution plans

### AWS Pricing MCP Server
**Package:** `awslabs.aws-pricing-mcp-server@latest`  
**Purpose:** Real-time AWS pricing data and cost analysis  
**Why Needed:** The unified AWS MCP Server doesn't include pricing tools

**Complete Configuration:**
```json
{
  "mcpServers": {
    "genai-cost-optimizer": {
      "command": "mcp-cost-optim-genai",
      "disabled": false
    },
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
      },
      "disabled": false
    }
  }
}
```

**Why Both Servers:**
- **AWS MCP Server**: CLI operations, documentation, Agent SOPs, workflow automation
- **AWS Pricing MCP**: Current pricing data for cost comparisons and optimization recommendations
- **Together**: Complete workflow from pattern detection → cost analysis → optimization guidance

**Known Limitations:**

**1. Pricing API Lag**

The AWS Pricing API can lag behind model releases by weeks or months. The AWS MCP Server handles this gracefully by providing fallback options and current documentation links when pricing is unavailable.

**2. LangChain Service Tier Support**

The scanner correctly detects LangChain Bedrock usage (`ChatBedrockConverse`, `ChatBedrock`, `BedrockLLM`) and flags missing `service_tier` parameters. However, **LangChain doesn't directly support the `service_tier` parameter** in its current API.

**Workarounds for LangChain users:**
- **Option 1**: Use `additional_model_request_fields` (if supported in your LangChain version)
  ```python
  ChatBedrockConverse(
      model="anthropic.claude-3-sonnet-20240229-v1:0",
      additional_model_request_fields={"service_tier": "flex"}
  )
  ```
- **Option 2**: Switch to direct boto3 calls for service tier support
  ```python
  bedrock_client.converse(
      modelId="anthropic.claude-3-sonnet-20240229-v1:0",
      messages=[...],
      service_tier="flex"  # Direct support
  )
  ```
- **Option 3**: Wait for LangChain to add native `service_tier` support

**Why we still flag this:** Service tiers (Priority, Standard, Flex) can significantly impact costs. Even if LangChain doesn't make it easy, users should be aware of this optimization opportunity.

## Key Features

### 🎯 Comprehensive Detection
- **Prompt Engineering**: AST-based recurring prompts, repeated context, quality analysis, Nova optimizer, LLM calls in loops, VSC format optimization (up to 75% token savings), JSON schemas in prompts
- **Bedrock Features**: Generic model detection (future-proof), Model selection (ALL providers), API patterns (OpenAI Chat Completions API support), prompt caching, prompt routing, AWS Strands library support, Nova explicit caching (90% savings), Cross-region anti-pattern detection
- **AgentCore Lifecycle**: Idle timeout and max lifetime cost alerts, session termination (StopRuntimeSession)
- **Cross-Service Patterns**: Detects cost amplification when combining services (e.g., streaming in AgentCore)
- **Anti-Patterns**: Detects caching with cross-region inference (can INCREASE costs by 50%+)
- **Multi-Language Support**: Python (AST + regex), TypeScript/JavaScript (regex), CDK, shell scripts, YAML

### 💡 Smart Insights
- **Complexity Analysis**: Detects mixed simple/complex prompts for intelligent routing
- **Caching Opportunities**: Identifies repeated prompts (90% savings potential)
- **Tool Recommendations**: Points to Claude Prompt Improver, Nova Optimizer, Bedrock features
- **Positive Feedback**: Confirms when cost optimizations are already in place

### 🔧 Developer-Friendly
- **Comprehensive Test Suite**: 23 test files covering all detectors
- **False Positive Mitigation**: Filters validation messages, comments, and docstrings (67% accuracy improvement)
- **Pattern-Based**: No hardcoded rules, stays relevant as AWS evolves
- **Generic Detection**: Universal model ID parser works with current and future models
- **AST + Regex**: Combines code structure analysis with pattern matching
- **Composable**: Works with AWS MCP Server for enrichment
- **Fast**: Static analysis only, no runtime execution

## Quick Start

## Documentation

**[📚 Complete Documentation Map](doc/DOCUMENTATION_MAP.md)** - Navigate all documentation files

### Getting Started
- [MCP Features](doc/MCP_FEATURES.md) - Tools, Resources, Prompts, and usage patterns

### Architecture & Design
- [Architecture](doc/ARCHITECTURE.md) - System design and component overview
- [Design Principles](doc/DESIGN_PRINCIPLES.md) - Core philosophy and guardrails

### Detectors (What We Scan For)
- [Bedrock Detector](doc/bedrock-detector.md) - Model usage, API patterns, prompt caching
- [Prompt Engineering](doc/prompt-engineering.md) - Recurring prompts, quality analysis, Nova caching
- [VSC Format Detector](doc/VSC_DETECTOR.md) - JSON optimization for token reduction
- [AgentCore Runtime](doc/agentcore-runtime.md) - Lifecycle configuration and session management
- [Cross-Cutting Patterns](doc/cross-cutting-patterns.md) - Multi-service cost impacts

### Advanced Topics
- [Generic Model Detection](doc/GENERIC_MODEL_DETECTION.md) - Future-proof model detection
- [Nova Caching Strategy](doc/NOVA_PROMPT_CACHING_STRATEGY.md) - Implementation examples
- [Cross-Region Caching Anti-Pattern](doc/CACHING_CROSS_REGION_ANTIPATTERN.md) - Avoid cost increases

## Project Structure

```
mcp-cost-optim-genai/
├── src/mcp_cost_optim_genai/     # Main package
│   ├── server.py                 # FastMCP server entry point
│   ├── scanner.py                # Project scanning logic
│   ├── scan_config.py            # Scanning configuration
│   ├── presentation_guidelines.py # Output formatting
│   ├── detectors/                # Pattern detection modules
│   │   ├── base.py               # Base detector class
│   │   ├── bedrock_detector.py   # Bedrock API & model detection
│   │   ├── agentcore_detector.py # AgentCore lifecycle & runtime
│   │   ├── prompt_engineering_detector.py # Prompt optimization
│   │   └── vsc_detector.py       # VSC format optimization
│   └── utils/                    # Shared utilities
│       ├── bedrock_helper.py     # AI-powered analysis
│       └── file_links.py         # Clickable file links
├── doc/                          # Detailed documentation
│   ├── MCP_FEATURES.md           # Tools, resources, prompts
│   ├── ARCHITECTURE.md           # System design
│   ├── DESIGN_PRINCIPLES.md      # Architecture decisions
│   ├── bedrock-detector.md       # Bedrock detection patterns
│   ├── agentcore-runtime.md      # AgentCore cost patterns
│   ├── prompt-engineering.md     # Prompt optimization
│   └── VSC_DETECTOR.md           # VSC format optimization
├── examples/                     # Sample code for testing
│   ├── sample_bedrock_code.py    # Bedrock usage examples
│   ├── sample_agentcore_*.py     # AgentCore examples
│   ├── sample_nova_optimization.py # Nova caching examples
│   └── scan_results/             # Example scan outputs
├── power/                        # Kiro Power integration
│   ├── POWER.md                  # Power documentation
│   ├── mcp.json                  # MCP configuration
│   └── steering/                 # Guided workflows
├── tests/                        # Comprehensive test suite
└── pyproject.toml                # Package configuration
```

**Key Components:**
- **Server**: FastMCP v2.0 implementation with tools, resources, and guided prompts
- **Scanner**: Core scanning engine with smart filtering and pattern detection
- **Detectors**: Pluggable pattern detectors for Bedrock, AgentCore, prompt engineering, and VSC optimization
- **Power**: Complete Kiro Power package for IDE integration

## Why This Matters

AWS GenAI services have complex pricing models:
- **Bedrock**: Per-token pricing varies 10x+ between models
- **AgentCore Runtime**: Charges by compute time + memory (idle time = wasted cost)

A misconfigured AgentCore idle timeout (1 hour vs 15 minutes) can result in **4x higher costs** for idle instances.

## How It Works: Pattern Detection, Not Hardcoded Recommendations

This scanner follows a unique approach that keeps it maintainable as AWS evolves:

### ❌ What We DON'T Do
```python
# Bad: Hardcoded recommendations that become outdated
if model == "claude-3-opus":
    return "Switch to Amazon Nova to save 80%"

# Bad: Hardcoded "cheap" lists that need constant updates
CHEAP_MODELS = ["amazon.nova-micro", "amazon.nova-lite"]
if model not in CHEAP_MODELS:
    return "Use Nova for cost savings"
```

### ✅ What We DO
**1. Tier-based analysis** (not specific model recommendations)
```python
# Detect tier, not specific models
if "opus" in model or "4-" in model:
    tier = "ultra-premium"
elif "sonnet" in model:
    tier = "premium"
elif "haiku" in model:
    tier = "cost-effective"

# Provide guidance to fetch current alternatives
return {
    "tier": tier,
    "recommendation": "Use AWS MCP Server to fetch current lower-tier alternatives",
    "next_steps": ["Search AWS docs for 'Bedrock Claude latest models'"]
}
```

**3. Dynamic model discovery** (via AWS MCP Server)
```python
# Findings include instructions to call AWS MCP Server
{
    "enrichment_instructions": {
        "steps": [
            {"tool": "AWS MCP Server", "action": "search latest Bedrock models"},
            {"tool": "AWS MCP Server", "action": "get_pricing for comparison"},
            {"tool": "AWS MCP Server", "action": "use Agent SOP for optimization"}
        ]
    }
}
```

**4. Composable architecture** (let AWS MCP Server enrich)
- **Scanner detects**: "Using Claude Sonnet (premium tier) for structured extraction"
- **AWS MCP Server provides**: "Latest Haiku model: claude-haiku-4-5-20251001-v1:0"
- **AWS MCP Server provides**: "Sonnet: $3/1M tokens, Haiku: $0.25/1M tokens"
- **AWS MCP Server provides**: "Agent SOP for model optimization workflow"
- **You/AI decide**: "Haiku is 12x cheaper and fits the use case"

**Result**: The scanner never needs updates when AWS releases new models or changes pricing. It detects patterns, classifies tiers, and guides you to fetch current information dynamically via the AWS MCP Server.

See [Design Principles](doc/DESIGN_PRINCIPLES.md) for the full philosophy.

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
