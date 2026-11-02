"""Tiny real streamable-HTTP MCP server for tests (loopback only)."""

import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("remote-fixture")


@server.tool()
def probe(text: str) -> str:
    """Echo text so tests can see approved calls reach the server."""
    return f"remote-ok:{text}"


if __name__ == "__main__":
    server.run("streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
