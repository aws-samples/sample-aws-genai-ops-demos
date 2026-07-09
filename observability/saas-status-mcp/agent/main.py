"""SaaS Status MCP Server — hosted on AgentCore Runtime.

Exposes SaaS status page data as MCP tools for AWS DevOps Agent.
AgentCore Runtime expects the MCP server on 0.0.0.0:8000/mcp using
stateless streamable HTTP transport.
"""

import logging

from mcp.server.fastmcp import FastMCP

import tools

logging.basicConfig(level=logging.INFO)

# stateless_http=True and json_response=True are REQUIRED for AgentCore Runtime
mcp = FastMCP(
    "saas-status-mcp",
    host="0.0.0.0",  # nosec B104
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
async def list_providers() -> dict:
    """List all configured SaaS providers (name, display name, status page URL)."""
    return await tools.list_providers()


@mcp.tool()
async def get_service_status(provider: str) -> dict:
    """Get the current overall operational status for a SaaS provider.

    Args:
        provider: Provider name (e.g. "snowflake", "datadog", "mongodb").
    """
    return await tools.get_service_status(provider)


@mcp.tool()
async def get_active_events(provider: str, include_history: bool = False) -> dict:
    """Get all active events (unresolved incidents + active maintenances) for a provider.

    Core investigation tool: answers "is anything happening right now?"

    Args:
        provider: Provider name (e.g. "snowflake", "datadog", "mongodb").
        include_history: If true, include full update history per event. Default false.
    """
    return await tools.get_active_events(provider, include_history=include_history)


@mcp.tool()
async def check_all_dependencies(providers: list[str]) -> dict:
    """Bulk-check status and active events across multiple providers (max 10) in parallel.

    Args:
        providers: List of provider names to check.
    """
    return await tools.check_all_dependencies(providers)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
