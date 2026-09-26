import json
from pathlib import Path

import pytest

from incident_agent import config
from incident_agent.mcp_tools import load_connection, open_mcp_tools


def write_config(tmp_path, server: dict):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"observability": server}}))
    return path


def test_relative_command_resolves_against_the_config_file(tmp_path):
    path = write_config(tmp_path, {"type": "stdio", "command": "bin/server", "args": ["-x"]})
    connection = load_connection(path, "observability")
    assert connection["command"] == str(tmp_path / "bin/server")
    assert connection["args"] == ["-x"] and connection["env"] == {}


def test_environment_overrides_the_config_files_env(tmp_path, monkeypatch):
    path = write_config(tmp_path, {"command": "/srv", "env": {"LOKI_URL": "http://a", "TEMPO_URL": "http://b"}})
    monkeypatch.setenv("LOKI_URL", "http://localhost:3100")
    monkeypatch.delenv("TEMPO_URL", raising=False)
    assert load_connection(path, "observability")["env"] == {"LOKI_URL": "http://localhost:3100", "TEMPO_URL": "http://b"}


def test_rejects_a_non_stdio_server(tmp_path):
    path = write_config(tmp_path, {"type": "http", "url": "http://127.0.0.1:8765/mcp"})
    with pytest.raises(ValueError, match="not a stdio server"):
        load_connection(path, "observability")


@pytest.mark.mcp
async def test_loads_the_observability_servers_tools_and_instructions():
    connection = load_connection(config.MCP_CONFIG, config.MCP_SERVER)
    if not Path(connection["command"]).exists():
        pytest.skip("observability-mcp isn't installed (make -C observability-mcp install)")
    async with open_mcp_tools(connection, config.MCP_SERVER) as mcp:
        names = {t.name for t in mcp.tools}
        assert {"prometheus_query", "prometheus_query_range", "loki_query", "tempo_get_trace"} <= names
        assert 'job="incident-investigator/<app>"' in mcp.instructions
        # A bad query comes back as an error result for the model, not an exception.
        prometheus_query = next(t for t in mcp.tools if t.name == "prometheus_query")
        result = await prometheus_query.ainvoke(
            {"type": "tool_call", "id": "1", "name": "prometheus_query", "args": {"query": "sum("}}
        )
        assert result.status == "error"
