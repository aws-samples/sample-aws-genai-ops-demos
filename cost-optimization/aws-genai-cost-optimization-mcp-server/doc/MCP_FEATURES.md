# MCP Features: Tools, Resources & Prompts

Our MCP server provides three types of capabilities for comprehensive cost optimization.

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    MCP Server Capabilities                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  🔧 TOOLS (Execute)          📊 RESOURCES (Access)          │
│  • scan_project()            • scan://latest                │
│  • analyze_file()                                           │
│                                                              │
│  🎯 PROMPTS (Guided Workflows)                              │
│  • quick_cost_scan           • comprehensive_analysis       │
│  • bedrock_only_analysis     • agentcore_only_analysis      │
│  • analyze_with_current_models                              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 Tools (Execute Analysis)

Execute scans and analysis on your codebase:

| Tool | Purpose |
|------|---------|
| `scan_project(path)` | Scan entire project directory for GenAI patterns |
| `analyze_file(path)` | Analyze single file for patterns |

**Example:**
```
"Scan my project for cost optimization opportunities"
```

## 📊 Resources (Access Data)

Access cached scan results:

| Resource | Content |
|----------|---------|
| `scan://latest` | Most recent scan results |

**Example:**
```
"Show me the latest scan results"
```

**Benefits:**
- Access previous scan data without re-scanning
- Cached results for faster analysis

## 🎯 Prompts (Guided Workflows)

Pre-built workflows for common analysis tasks:

| Prompt | Focus | Use Case |
|--------|-------|----------|
| `quick_cost_scan` | High-impact findings | Fast daily check |
| `comprehensive_analysis` | Full analysis + report | Detailed review |
| `bedrock_only_analysis` | Bedrock patterns only | Model/prompt optimization |
| `agentcore_only_analysis` | AgentCore Runtime only | Lifecycle configuration |
| `analyze_with_current_models` | Latest Bedrock models | Dynamic model discovery |

**Example:**
```
"Use quick_cost_scan to check my project"
"Run comprehensive_analysis and generate a report"
```

**Benefits:**
- Consistent analysis approach
- Guided workflows for non-technical users
- Composable with tools and resources

## 💡 Usage Patterns

### Quick Check
```
User: "Use quick_cost_scan"
AI: Runs scan_project() → Identifies top 3 findings → Provides savings estimates
```

### Comprehensive Review
```
User: "Use comprehensive_analysis"
AI: Scans project → Accesses optimization://patterns → Calls AWS MCP Server → Generates detailed report
```

### Pattern Discovery
```
User: "Show me the latest scan results"
AI: Accesses scan://latest → Shows previous findings
User: "Focus on AgentCore"
AI: Uses agentcore_only_analysis prompt
```

### Tool Recommendations
```
User: "Use analyze_with_current_models"
AI: Scans project → Fetches latest Bedrock models → Provides current recommendations
```

## 🔗 Integration with Other MCP Servers

The combination of Tools + Resources + Prompts enables powerful workflows:

```
1. Use quick_cost_scan prompt (our server)
2. Access AWS MCP Server for cost data and documentation
3. Generate comprehensive recommendations
```

## 📈 Why This Matters

**Tools alone** require users to know what to ask for.

**Tools + Resources + Prompts** provide:
- Discoverability (what can this server do?)
- Guidance (how should I use it?)
- Structure (what data is available?)
- Workflows (common analysis patterns)

This makes the server accessible to both technical and non-technical users.
