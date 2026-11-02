"""Exercise OmniGent against an actual MCP2 server over both transports."""
import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from omnigent.spec.types import MCPServerConfig
from omnigent.tools.mcp import McpServerConnection


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_add_over_mcp2(transport):
    fixture = Path(__file__).parent / 'fixtures' / 'mcp2_live_server.py'
    process = None
    if transport == 'stdio':
        config = MCPServerConfig(name='mcp2-add', transport='stdio', command=sys.executable, args=[str(fixture)])
    else:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([sys.executable, str(fixture), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail('MCP HTTP server exited before listening')
            try:
                _, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail('MCP HTTP server did not start')
        config = MCPServerConfig(name='mcp2-add-http', transport='http', url=f'http://127.0.0.1:{port}/mcp')
    conn = McpServerConnection(config)
    try:
        tools = await conn.connect()
        assert any(tool.name == 'add' for tool in tools)
        assert '42' in await conn.call_tool('add', {'a': 19, 'b': 23})
        pending = asyncio.create_task(conn.call_tool("slow", {}))
        await asyncio.sleep(0.1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert '42' in await conn.call_tool('add', {'a': 19, 'b': 23})
        assert '42' in await conn.call_tool('add', {'a': 19, 'b': 23})
    finally:
        await conn.close()
        if process is not None:
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("redirect", [False, True])
async def test_http_auth_is_not_leaked_on_redirect(redirect):
    origin_auth = []
    received = []

    class Destination(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.headers.get("Authorization"))
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Destination)

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            origin_auth.append(self.headers.get("Authorization"))
            self.send_response(307 if redirect else 401)
            if redirect:
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/mcp")
            self.end_headers()

        def log_message(self, *args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    threads = [Thread(target=server.serve_forever, daemon=True) for server in (origin, target)]
    for thread in threads:
        thread.start()
    config = MCPServerConfig(name="unauthorized", transport="http", url=f"http://127.0.0.1:{origin.server_port}/mcp", headers={"Authorization": "Bearer secret"})
    conn = McpServerConnection(config, http_transport="streamable-http")
    try:
        with pytest.raises(Exception):
            await conn.connect()
        assert origin_auth == ["Bearer secret"]
        # MCP2 deliberately rejects redirects instead of following them.
        assert not received
    finally:
        await conn.close()
        for server in (origin, target):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()
