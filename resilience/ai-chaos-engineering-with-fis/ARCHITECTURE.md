# Architecture — AWS Chaos Engineering MCP Server

## Overview

A local MCP server that transforms natural language descriptions into validated AWS FIS experiment templates. It runs entirely on the developer's machine (installed via `uvx`) with no cloud infrastructure — AWS is only contacted to fetch current FIS capabilities when the local cache is stale.

---

## High-Level Architecture

```
User Input → Kiro Agent → aws-chaos-engineering MCP Server → Validated FIS Template
                ↓
           AWS MCP Server (for fresh FIS data when cache is stale)
```

### Full Request Flow

```
┌─────────────────┐    ┌─────────────────┐    ┌───────────────────────┐   ┌───────────────────┐
│      USER       │    │   KIRO AGENT    │    │  aws-chaos-engineering│   │   AWS MCP Server  │
│                 │    │  (MCP Client)   │    │       MCP Server      │   │    (companion)    │
└─────────────────┘    └─────────────────┘    └───────────────────────┘   └───────────────────┘
         │                       │                       │                       │
         │ 1. Natural language   │                       │                       │
         │    chaos request      │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │ 2. Keyword detection  │                       │
         │                       │    activates power    │                       │
         │                       │                       │                       │
         │                       │ 3. get_valid_fis_     │                       │
         │                       │    actions(region)    │                       │
         │                       ├──────────────────────►│                       │
         │                       │ 4. Cache status       │                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │                       │ 5. IF STALE: fetch    │                       │
         │                       │    describe_fis_      │                       │
         │                       │    actions            │                       │
         │                       ├───────────────────────┼──────────────────────►│
         │                       │ 6. IF STALE: fetch    │                       │
         │                       │    describe_fis_      │                       │
         │                       │    resource_types     │                       │
         │                       ├───────────────────────┼──────────────────────►│
         │                       │ 7. Fresh FIS data     │                       │
         │                       │◄──────────────────────┼───────────────────────┤
         │                       │                       │                       │
         │                       │ 8. refresh_valid_fis_ │                       │
         │                       │    actions_cache(data)│                       │
         │                       ├──────────────────────►│                       │
         │                       │ 9. Cache updated      │                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │                       │ 10. LLM call to       │                       │
         │                       │     generate template │                       │
         │                       │                       │                       │
         │                       │ 11. validate_fis_     │                       │
         │                       │     template(tpl)     │                       │
         │                       ├──────────────────────►│                       │
         │                       │ 12. Validation result │                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │ 13. Deployable        │                       │                       │
         │     CloudFormation    │                       │                       │
         │◄──────────────────────┤                       │                       │
```

---

## Components

### Kiro Agent (MCP Client)

The orchestrator. It detects chaos engineering keywords in the user's request, activates the Kiro Power, and coordinates all interactions between the two MCP servers. The agent is responsible for:

- Deciding when the cache is stale and needs refreshing
- Building the LLM system prompt from cached FIS capabilities
- Executing the LLM call to generate the experiment template
- Calling `validate_fis_template` and handling validation failures

### aws-chaos-engineering MCP Server

The core server, installed locally via `uvx`. It exposes three tools:

| Tool | Purpose |
|------|---------|
| `get_valid_fis_actions` | Returns cached FIS actions and resource types; signals FRESH / STALE / EMPTY |
| `refresh_valid_fis_actions_cache` | Accepts fresh FIS data from the agent and writes it to the local cache |
| `validate_fis_template` | Validates a generated template's action IDs and resource types against the cache |

The server has **no direct AWS access** — it relies entirely on the agent to fetch fresh data via the AWS MCP Server when the cache is stale. This keeps the server stateless with respect to AWS credentials.

### AWS MCP Server (companion)

A separate MCP server that provides direct AWS API access. Used only when the cache needs refreshing:

- `describe_fis_actions` — fetches current FIS actions from AWS APIs
- `describe_fis_resource_types` — fetches current FIS resource types from AWS APIs

### Local Cache

File-based cache with a 24-hour TTL, stored per region on the developer's machine:

- **Windows**: `%USERPROFILE%\.aws-chaos-engineering\`
- **macOS/Linux**: `~/.cache/aws-chaos-engineering/`

Files are named by region (e.g. `fis_actions_<region>.json`). On steady state (cache fresh), no AWS API calls are made — the server serves entirely from disk. Corrupted cache files are automatically removed.

---

## Design Decisions

### No cloud infrastructure

The server runs locally via `uvx` — no Lambda, no ECS, no CDK stack. Installation is a single command and teardown is implicit. This is intentional: the value is in the MCP tooling and Kiro Power integration, not in hosting.

### Agent orchestration, no server-to-server communication

MCP servers never call each other directly. The Kiro Agent is the sole coordinator — it calls `get_valid_fis_actions`, decides whether to refresh, fetches from AWS MCP Server if needed, and calls `refresh_valid_fis_actions_cache`. This follows the MCP architecture principle that servers are passive tool providers.

### Dynamic FIS capabilities vs. static lists

Rather than hardcoding FIS action IDs (which change as AWS adds new FIS capabilities), the server fetches the current list from AWS APIs and caches it locally. The 24-hour TTL balances freshness against API call frequency. This eliminates an entire class of hallucination where an LLM invents action IDs that don't exist.

### Validation before delivery

`validate_fis_template` checks every action ID and resource type in the generated template against the cached capabilities before returning the result to the user. Invalid templates are rejected with specific error messages identifying which actions or resource types are unknown — enabling the agent to retry with corrections.

---

## Kiro Power Integration

The demo ships as a Kiro Power (`power/` directory) which bundles:

- `POWER.md` — power documentation and tool descriptions
- `mcp.json` — pre-configured MCP server entry for `uvx aws-chaos-engineering`
- `steering/` — activation keywords and usage patterns injected into Kiro's context

The power activates automatically on keywords like "chaos engineering", "fault injection", "AWS FIS", and "resilience testing", making the workflow zero-friction for the developer.
