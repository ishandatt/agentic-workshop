"""The adapter between MCP and LangChain — about thirty lines, and worth reading.

There are published packages that do this. We write it out instead, because
once you have seen it there is no magic left in MCP: a client asks the server
what tools exist, gets back names, descriptions and JSON Schemas, and wraps
each one in whatever object the local framework expects.

That is the decoupling made concrete. The server knows nothing about
LangChain. LangChain knows nothing about MCP. This file is the seam.

A note on the design below, because it is a deliberate simplification:
every tool call opens a fresh connection to the server, calls one tool, and
closes it — about 350ms of overhead per call. Real clients open one session and
keep it for the life of the agent. We spawn per call so that the agent scripts
stay ordinary synchronous Python; a persistent session would make every script
in this module async. Next to a 4-second model call, 350ms is noise. In
production it would not be.
"""

import asyncio
import importlib.metadata
import json
import sys
from pathlib import Path

# `mcp` is the official SDK. These three imports are the whole client API we need.
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# StructuredTool is LangChain's "a tool with typed arguments" class. Building
# them by hand is what lets us convert anything into a LangChain tool.
from langchain_core.tools import StructuredTool

# Where the server lives, and which interpreter to run it with. `sys.executable`
# is the Python currently running this file, so the subprocess automatically
# uses the same virtualenv.
SERVER_PATH = str(Path(__file__).resolve().parents[0] / "mcp_server.py")
SERVER_PARAMS = StdioServerParameters(command=sys.executable, args=[SERVER_PATH])


def preflight() -> None:
    """Fail early and legibly if the installed `mcp` cannot run our server.

    This exists because of how badly the alternative fails. The server runs as
    a SUBPROCESS, so when it cannot start, its real error goes to stderr and
    what surfaces here is a nested ExceptionGroup ending in "Connection
    closed" — sixty lines of traceback that name neither the cause nor the fix.

    The common cause is a version mismatch. `mcp` 2.0 removed
    `mcp.server.fastmcp`, so an environment that installed before the pin in
    requirements.txt was added will have 2.x and fail exactly this way.

    Checking here works because the subprocess is launched with
    `sys.executable` — the same interpreter running this code. If the import
    succeeds in this process, it will succeed in that one.
    """
    try:
        # `noqa` tells linters we imported this deliberately without using it.
        import mcp.server.fastmcp  # noqa: F401
    except ModuleNotFoundError:
        try:
            installed = importlib.metadata.version("mcp")
        except importlib.metadata.PackageNotFoundError:
            installed = "not installed"
        # `raise ... from None` suppresses the original traceback, because the
        # ModuleNotFoundError adds nothing a reader needs.
        raise RuntimeError(
            f"This module needs the mcp 1.x API, but mcp {installed} is installed.\n"
            f"\n"
            f"  mcp 2.0 removed `mcp.server.fastmcp`, which mcp_server.py uses.\n"
            f"  requirements.txt pins `mcp>=1.29,<2`; your environment predates\n"
            f"  that pin or was upgraded afterwards.\n"
            f"\n"
            f"  Fix:  pip install -r requirements.txt\n"
        ) from None


# `async def` marks a coroutine — a function that can pause partway through
# while it waits for something slow (here: a pipe to another process). You call
# it with `await` from inside other async code, or `asyncio.run()` from
# ordinary code. Everything MCP does is async because talking to another
# process means waiting on it.
async def _with_session(action):
    """Start the server, run one action against it, shut it down.

    `action` is an async function taking the open session. Passing behaviour in
    as a parameter avoids writing this connect/teardown dance twice.
    """
    # `async with` is `with` for things that need async setup and teardown —
    # here, spawning the subprocess and reaping it afterwards.
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            # The MCP handshake: agree on protocol version and capabilities.
            await session.initialize()
            return await action(session)


def _call_tool(name: str, arguments: dict) -> str:
    """Call one tool on the server and return its result as text."""
    async def action(session):
        result = await session.call_tool(name, arguments)
        # Results come back as a list of content blocks. Our tools return JSON
        # objects, which arrive as a single text block.
        return result.content[0].text if result.content else ""

    # `asyncio.run` builds an event loop, runs the coroutine to completion, and
    # tears the loop down. It is the bridge from ordinary sync code into async.
    return asyncio.run(_with_session(action))


def load_mcp_tools() -> list[StructuredTool]:
    """Ask the MCP server what it can do, and return LangChain tools for it.

    Nothing about the returned tools is MCP-specific. To the agent they are
    indistinguishable from tools defined with @tool in the same file — which is
    exactly the property that makes MCP worth having.
    """
    # Check the environment before spawning anything, so a version mismatch
    # produces one clear sentence instead of a nested ExceptionGroup.
    preflight()

    async def action(session):
        # The discovery call. This is what "self-describing" means in practice.
        listed = await session.list_tools()
        return [(t.name, t.description, t.inputSchema) for t in listed.tools]

    try:
        specs = asyncio.run(_with_session(action))
    except Exception as e:
        # Anything reaching here means the subprocess started but the
        # conversation failed. Point at how to see the server's own error,
        # since its stderr is not shown above.
        raise RuntimeError(
            f"Could not talk to the MCP server at {SERVER_PATH}.\n"
            f"\n"
            f"  Underlying error: {type(e).__name__}: {e}\n"
            f"\n"
            f"  The server runs as a subprocess, so its own error is not shown.\n"
            f"  To see it, import the server directly:\n"
            f"      python -c \"import sys; sys.path.insert(0, 'modules/03-mcp-tools'); import mcp_server\"\n"
        ) from None

    tools = []
    for name, description, schema in specs:
        # A closure problem worth naming, because it bites everyone once:
        # if the lambda referred to `name` directly, all the tools would end up
        # calling whatever `name` held at the END of the loop. Binding it as a
        # default argument (`_name=name`) captures the value now instead.
        def make_runner(_name):
            def run(**kwargs):
                return _call_tool(_name, kwargs)
            return run

        tools.append(
            StructuredTool(
                name=name,
                description=description,
                # The JSON Schema the server published becomes the tool's
                # argument schema, unchanged. LangChain hands it to the model,
                # which is how the model knows what arguments to supply.
                args_schema=schema,
                func=make_runner(name),
            )
        )
    return tools


if __name__ == "__main__":
    # Run this file directly to inspect what the server publishes, without any
    # model in the loop: python modules/03-mcp-tools/mcp_bridge.py
    for t in load_mcp_tools():
        print(f"{t.name}")
        print(f"   description: {t.description.splitlines()[0]}")
        print(f"   arguments:   {list(t.args_schema.get('properties', {}))}")
        print(f"   json schema: {json.dumps(t.args_schema)[:110]}…\n")
