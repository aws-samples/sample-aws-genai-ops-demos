"""Entry point for the AWS Chaos Engineering MCP Server.

This module provides the main entry point for running the MCP server
with stdio transport for Kiro integration.
"""

import sys
from typing import NoReturn

from .server import mcp


def main() -> NoReturn:
    """Main entry point for the MCP server.
    
    Runs the FastMCP server with stdio transport for Kiro integration.
    This function does not return as it runs the server indefinitely.
    """
    try:
        # Run the FastMCP server with stdio transport
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
    except KeyboardInterrupt:
        # Handle graceful shutdown on Ctrl+C
        sys.exit(0)
    except Exception as e:
        # Handle any unexpected errors during server startup
        print(f"Error starting MCP server: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
