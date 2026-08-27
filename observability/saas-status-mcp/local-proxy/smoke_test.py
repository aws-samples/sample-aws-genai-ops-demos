"""Smoke test: spawn proxy.py as a stdio MCP server and call the tools.

Simulates what Kiro does — launches the proxy, runs the MCP handshake over
stdio, lists tools, and calls one. Confirms the local bridge works before
wiring it into Kiro's mcp.json.
"""

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

RUNTIME_ARN = "arn:aws:bedrock-agentcore:eu-west-3:517675598740:runtime/saas_status_mcp-405ED0Cw5p"
REGION = "eu-west-3"


async def main():
    env = dict(os.environ)
    env["SAAS_MCP_RUNTIME_ARN"] = RUNTIME_ARN
    env["AWS_REGION"] = REGION

    params = StdioServerParameters(
        command="python",
        args=["proxy.py"],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("--- tools/list")
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"    * {t.name}")
            print()

            print("--- list_providers (should show S3-backed registry count)")
            result = await session.call_tool("list_providers", {})
            import json as _json
            for c in result.content:
                data = _json.loads(c.text)
                providers = data.get("providers", [])
                print(f"    {len(providers)} providers: {', '.join(p['name'] for p in providers)}")


if __name__ == "__main__":
    asyncio.run(main())
