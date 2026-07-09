"""Local SigV4 bridge for testing the SaaS Status MCP server from Kiro.

The MCP server is hosted on AgentCore Runtime behind IAM (SigV4) auth, so a
local MCP client like Kiro can't reach it directly. This stdio MCP server runs
on your machine, re-exposes the same 4 tools, and forwards each call to the
deployed runtime via invoke_agent_runtime (boto3 signs the request with your
AWS credentials).

Kiro launches this as a normal stdio MCP server. Configure via env vars:
    SAAS_MCP_RUNTIME_ARN   (required) — the AgentCore runtime ARN
    AWS_REGION             (required) — region the runtime lives in
    AWS_PROFILE            (optional) — AWS credentials profile to use
"""

import json
import os
import sys

import boto3
from mcp.server.fastmcp import FastMCP

RUNTIME_ARN = os.environ.get("SAAS_MCP_RUNTIME_ARN")
REGION = os.environ.get("AWS_REGION")

if not RUNTIME_ARN or not REGION:
    print(
        "ERROR: set SAAS_MCP_RUNTIME_ARN and AWS_REGION environment variables.",
        file=sys.stderr,
    )
    sys.exit(1)

_client = boto3.client("bedrock-agentcore", region_name=REGION)

# Local stdio MCP server that Kiro connects to
mcp = FastMCP("saas-status-mcp-proxy")

_rpc_id = 0


def _forward(tool_name: str, arguments: dict) -> dict:
    """Forward a tools/call to the remote AgentCore MCP runtime and unwrap the result."""
    global _rpc_id
    _rpc_id += 1

    rpc_message = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": _rpc_id,
        "params": {"name": tool_name, "arguments": arguments},
    }

    response = _client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        payload=json.dumps(rpc_message).encode("utf-8"),
        contentType="application/json",
        accept="application/json, text/event-stream",
    )

    body = response["response"].read().decode("utf-8")
    if body.startswith("data:"):
        body = body.split("data:", 1)[1].strip()

    parsed = json.loads(body)
    result = parsed.get("result", {})

    # MCP tool results wrap the payload as content[].text (a JSON string)
    content = result.get("content", [])
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return {"text": content[0]["text"]}
    return result


@mcp.tool()
def list_providers() -> dict:
    """List all configured SaaS providers (name, display name, status page URL)."""
    return _forward("list_providers", {})


@mcp.tool()
def get_service_status(provider: str) -> dict:
    """Get the current overall operational status for a SaaS provider.

    Args:
        provider: Provider name (e.g. "snowflake", "datadog", "mongodb").
    """
    return _forward("get_service_status", {"provider": provider})


@mcp.tool()
def get_active_events(provider: str, include_history: bool = False) -> dict:
    """Get all active events (unresolved incidents + active maintenances) for a provider.

    Args:
        provider: Provider name (e.g. "snowflake", "datadog", "mongodb").
        include_history: If true, include full update history per event. Default false.
    """
    return _forward("get_active_events", {"provider": provider, "include_history": include_history})


@mcp.tool()
def check_all_dependencies(providers: list[str]) -> dict:
    """Bulk-check status and active events across multiple providers (max 10) in parallel.

    Args:
        providers: List of provider names to check.
    """
    return _forward("check_all_dependencies", {"providers": providers})


if __name__ == "__main__":
    mcp.run(transport="stdio")
