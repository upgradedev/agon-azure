"""matchfacts over the MCP protocol (SDK pinned in requirements/mcp.txt). Runs in CI (mcp.yml).

Locally, without the SDK installed, this module skips. With AGON_REQUIRE_MCP=1 (set by mcp.yml) a
missing SDK is a hard failure instead, so the CI job can never pass by skipping.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

REQUIRE = os.environ.get("AGON_REQUIRE_MCP") == "1"
HAVE_SDK = importlib.util.find_spec("mcp") is not None and importlib.util.find_spec("mcp.server.fastmcp") is not None
if not HAVE_SDK:
    if REQUIRE:
        raise RuntimeError("AGON_REQUIRE_MCP=1 but the MCP SDK is not importable; install requirements/mcp.txt")
    pytest.skip("MCP SDK not installed (CI installs requirements/mcp.txt)", allow_module_level=True)

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from agon.mcp.matchfacts import build_server  # noqa: E402
from matchfacts_kit import BLOCKER, EXPECTED_TOOLS, calls, source, subprocess_env, write_like  # noqa: E402


def payload(result) -> dict:
    """The envelope from a tool result: structured content when present, else the JSON text block."""
    if getattr(result, "structuredContent", None):
        data = result.structuredContent
        return data.get("result", data) if set(data) == {"result"} else data
    return json.loads(result.content[0].text)


async def _session_run(fn):
    src = source()
    server = build_server(src)
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client, src)


def run_session(fn):
    return asyncio.run(_session_run(fn))


def test_server_lists_exactly_the_nine_read_only_tools():
    async def body(client, src):
        return (await client.list_tools()).tools

    tools = run_session(body)
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    assert not [n for n in names if write_like(n)]
    for t in tools:
        assert t.annotations is not None and t.annotations.readOnlyHint is True and t.annotations.destructiveHint is False
        assert t.inputSchema.get("type") == "object"


def test_every_tool_answers_over_the_protocol():
    async def body(client, src):
        out = {}
        for name, args in calls(src):
            res = await client.call_tool(name, args)
            out[name] = (res.isError, payload(res) if not res.isError else res.content[0].text)
        return out

    results = run_session(body)
    assert set(results) == EXPECTED_TOOLS
    for name, (is_error, env) in results.items():
        assert not is_error, (name, env)
        assert env["tool"] == name and env["content"] == "quoted_data"


def test_a_write_like_tool_does_not_exist():
    async def body(client, src):
        mid = src.match_ids()[0]
        out = []
        for name in ("bundle.publish", "events.write", "approval.approve", "publish"):
            res = await client.call_tool(name, {"match_id": mid})
            out.append(res.isError)
        return out

    assert run_session(body) == [True, True, True, True]


def test_bad_input_is_an_error_result_not_a_crash():
    async def body(client, src):
        mid = src.match_ids()[0]
        res = await client.call_tool("wp.swing", {"match_id": mid, "event_id": "0" * 16})
        return res.isError, res.content[0].text

    is_error, text = run_session(body)
    assert is_error and "unknown event_id" in text


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_streamable_http_round_trip():
    import uvicorn

    port = _free_port()
    src = source()
    app = build_server(src, host="127.0.0.1", port=port).streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"

    async def client_run():
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                name, args = calls(src)[0]
                res = await session.call_tool(name, args)
                return names, res.isError, payload(res)

    try:
        names, is_error, env = asyncio.run(client_run())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert names == EXPECTED_TOOLS
    assert not is_error and env["tool"] == "events.window" and env["data"]["count"] > 0


def test_referee_is_unreachable_through_the_server():
    script = BLOCKER + """
import asyncio
from mcp.shared.memory import create_connected_server_and_client_session
from agon.mcp.matchfacts import build_server
import matchfacts_kit as kit

async def main():
    src = kit.source()
    server = build_server(src)
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        for name, args in kit.calls(src):
            res = await client.call_tool(name, args)
            assert not res.isError, (name, res.content)

asyncio.run(main())
leaked = sorted(m for m in sys.modules if m.startswith("agon." + "referee"))
print("LEAKED" if leaked else "CLEAN", leaked)
"""
    res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=subprocess_env(), timeout=300)
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("CLEAN"), res.stdout
