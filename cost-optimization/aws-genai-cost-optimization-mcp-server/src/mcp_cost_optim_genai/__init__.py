"""AWS GenAI Cost Optimization MCP Server."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("awslabs-genai-cost-optim-mcp-server")
except PackageNotFoundError:
    # Package not installed (e.g., running from source)
    __version__ = "0.1.0-dev"
