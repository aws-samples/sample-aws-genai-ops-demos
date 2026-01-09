# Architecture: MCP Server Design

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    MCP Client (Kiro, Claude)                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ MCP Protocol
                             │
┌────────────────────────────▼────────────────────────────────────┐
│              GenAI Cost Optimization MCP Server                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │    TOOLS     │  │  RESOURCES   │  │   PROMPTS    │         │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤         │
│  │ scan_project │  │ patterns     │  │ quick_scan   │         │
│  │ analyze_file │  │ tools        │  │ comprehensive│         │
│  │              │  │ latest       │  │ bedrock_only │         │
│  │              │  │              │  │ agentcore    │         │
│  └──────┬───────┘  └──────────────┘  └──────────────┘         │
│         │                                                       │
│         │                                                       │
│  ┌──────▼──────────────────────────────────────────────────┐  │
│  │              Project Scanner                             │  │
│  │  • File discovery                                        │  │
│  │  • Pattern matching                                      │  │
│  │  • Finding aggregation                                   │  │
│  └──────┬───────────────────────────────────────────────────┘  │
│         │                                                       │
│  ┌──────▼──────────────────────────────────────────────────┐  │
│  │                   Detectors                              │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │  ┌────────────────┐  ┌────────────────┐                 │  │
│  │  │ Bedrock        │  │ AgentCore      │                 │  │
│  │  │ • Models       │  │ • Lifecycle    │                 │  │
│  │  │ • API calls    │  │ • Deployment   │                 │  │
│  │  │ • Tokens       │  │ • Sessions     │                 │  │
│  │  └────────────────┘  └────────────────┘                 │  │
│  │                                                           │  │
│  │  ┌────────────────┐  ┌────────────────┐                 │  │
│  │  │ Prompt Optim   │  │ Cross-Cutting  │                 │  │
│  │  │ • Caching      │  │ • Streaming    │                 │  │
│  │  │ • Quality      │  │ • Amplification│                 │  │
│  │  │ • Routing      │  │                │                 │  │
│  │  └────────────────┘  └────────────────┘                 │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

### MCP Interface Layer

**Tools** - Execute operations
- `scan_project()` - Scan entire directory
- `analyze_file()` - Analyze single file

**Resources** - Provide structured data
- `optimization://patterns` - Pattern catalog
- `optimization://tools` - Tool recommendations
- `scan://latest` - Cached results

**Prompts** - Guide workflows
- `quick_cost_scan` - Fast analysis
- `comprehensive_analysis` - Full review
- `bedrock_only_analysis` - Service-specific
- `agentcore_only_analysis` - Service-specific

### Core Engine

**Project Scanner**
- Discovers files (`.py`, `.ts`, `.js`, `.yml`)
- Coordinates detector execution
- Aggregates findings
- Caches results
- Adds clickable file links to all findings

**Detectors** (Pattern-based, no hardcoded rules)
- Bedrock: Models, API patterns, token usage
- AgentCore: Lifecycle, deployment, sessions
- Prompt Optimization: Caching, quality, routing
- Cross-Cutting: Service interactions

**Presentation Guidelines** (`presentation_guidelines.py`)
- **Single source of truth** for AI formatting instructions
- Used by both scanner (in results) and server (in tool descriptions)
- Ensures consistent presentation across all outputs
- **IMPORTANT**: When adding new formatting instructions, update this file ONLY
  - Do NOT duplicate instructions in scanner.py or server.py
  - Import and use `PRESENTATION_GUIDELINES` or `PRESENTATION_SUMMARY`

## Data Flow

### Scan Execution
```
User Request
    │
    ▼
MCP Tool (scan_project)
    │
    ▼
Project Scanner
    │
    ├──▶ Bedrock Detector ──▶ Findings
    │
    ├──▶ AgentCore Detector ──▶ Findings
    │
    ├──▶ Prompt Optimizer ──▶ Findings
    │
    └──▶ Cross-Cutting ──▶ Findings
    │
    ▼
Aggregate & Cache
    │
    ▼
Return JSON Results
```

### Resource Access
```
User Request
    │
    ▼
MCP Resource (optimization://patterns)
    │
    ▼
Return Structured Data (no scan needed)
```

### Prompt Workflow
```
User Request
    │
    ▼
MCP Prompt (quick_cost_scan)
    │
    ▼
AI follows guided workflow
    │
    ├──▶ Calls scan_project()
    │
    ├──▶ Accesses optimization://patterns
    │
    └──▶ Generates recommendations
```

## Design Principles

### 1. Pattern-Based Detection
```python
# ✅ Good: Pattern-based (future-proof)
MODEL_PATTERNS = {
    "amazon-nova": r"amazon\.nova[^\"']*"  # Catches all Nova variants
}

# ❌ Bad: Hardcoded rules (becomes outdated)
if model == "amazon.nova-micro-v1:0":
    return "Use this model"
```

### 2. Composable Architecture
```
Our Server: Detects patterns
    ↓
AWS Pricing MCP: Provides cost data
    ↓
AWS Docs MCP: Provides best practices
    ↓
AI: Generates recommendations
```

### 3. Structured Findings
```json
{
  "type": "bedrock_model_usage",
  "model_family": "amazon-nova",
  "model_id": "amazon.nova-micro-v1:0",
  "file": "src/agent.py",
  "line": 42
}
```

## Integration Points

### With AWS Pricing MCP
```
1. Our server detects: "Using Claude Opus"
2. Pricing MCP provides: "$15/1M tokens"
3. AI calculates: Potential savings with Nova
```

### With AWS Documentation MCP
```
1. Our server detects: "Missing cache control"
2. Docs MCP provides: Bedrock caching guide
3. AI recommends: Implementation steps
```

## Extension Points

### Adding New Detectors
```python
# 1. Create detector in src/mcp_cost_optim_genai/detectors/
class NewServiceDetector:
    async def detect(self, content: str, file_path: str):
        # Pattern-based detection
        return findings

# 2. Register in scanner.py
self.detectors = [
    BedrockDetector(),
    AgentCoreDetector(),
    NewServiceDetector()  # Add here
]

# 3. Add tests in tests/
def test_new_service_detection():
    # Test patterns
```

### Adding New Resources
```python
@mcp.resource("optimization://new_resource")
async def new_resource() -> str:
    return json.dumps({"data": "..."})
```

### Adding New Prompts
```python
@mcp.prompt()
async def new_workflow():
    return {
        "messages": [{
            "role": "user",
            "content": "Workflow instructions..."
        }]
    }
```

## Performance Characteristics

- **Static Analysis**: No code execution, fast scanning
- **Pattern Matching**: Regex-based, efficient
- **Caching**: Results cached for quick re-access
- **Scalability**: Handles large codebases (1000+ files)

## Security Considerations

- **Read-Only**: Only reads code, never executes
- **No Network**: No external API calls during scan
- **Local Processing**: All analysis happens locally
- **Safe Patterns**: Regex patterns are safe, no eval()

## Future Enhancements

1. **More Services**: SageMaker, Comprehend, Translate
2. **More Languages**: Java, Go, Rust
3. **Runtime Analysis**: Complement static analysis
4. **Cost Tracking**: Historical cost trends
5. **Recommendations**: ML-based optimization suggestions
