"""The observability MCP server's tools, as LangChain tools.

The server is started once over stdio and its session is kept for the whole investigation. The
adapters' `get_tools()` would instead start a new server process for every tool call.
"""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StdioConnection
from langchain_mcp_adapters.tools import load_mcp_tools


@dataclass
class McpTools:
    tools: list[BaseTool]
    # What the server tells a model about the stack: services, label conventions, how to investigate.
    instructions: str | None


def load_connection(mcp_config: Path, server: str) -> StdioConnection:
    """`server`'s entry in a Claude Code `.mcp.json`. A relative command is resolved against the
    file's directory, and variables already set in the environment override the file's `env`, as
    observability-mcp/README.md describes."""
    spec = json.loads(mcp_config.read_text())["mcpServers"][server]
    if spec.get("type", "stdio") != "stdio":
        raise ValueError(f"{server} in {mcp_config} is not a stdio server")
    command = Path(spec["command"])
    if not command.is_absolute():
        command = mcp_config.parent / command
    env = {key: os.environ.get(key, value) for key, value in (spec.get("env") or {}).items()}
    return StdioConnection(transport="stdio", command=str(command), args=spec.get("args", []), env=env)


@asynccontextmanager
async def open_mcp_tools(connection: StdioConnection, server: str) -> AsyncIterator[McpTools]:
    client = MultiServerMCPClient({server: connection})
    # Initialized here rather than by the client, to keep the server's instructions.
    async with client.session(server, auto_initialize=False) as session:
        result = await session.initialize()
        # Tool errors (a bad PromQL query, "is the stack up?") go back to the model as error results.
        tools = await load_mcp_tools(session, server_name=server, handle_tool_errors=True)
        yield McpTools(tools, result.instructions)
