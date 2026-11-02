"""Loopback-only MCP2 server for transport integration tests."""
import asyncio
import sys
from mcp.server.mcpserver import MCPServer

server = MCPServer('omnigent-mcp2-test')

@server.tool()
def add(a: int, b: int) -> int:
    return a + b

@server.tool()
async def slow() -> str:
    await asyncio.sleep(30)
    return "done"

if __name__ == '__main__':
    if len(sys.argv) == 2:
        server.run('streamable-http', host='127.0.0.1', port=int(sys.argv[1]))
    else:
        server.run('stdio')
