import os
from pathlib import Path

from incident_rag.ingest import DEFAULT_STRATEGY

# agent/src/incident_agent/config.py -> the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]

MODEL = os.environ.get("INCIDENT_AGENT_MODEL", "claude-opus-5")
# Investigations are multi-step and evidence-heavy, which is where higher effort pays off.
EFFORT = os.environ.get("INCIDENT_AGENT_EFFORT", "high")
# Rounds of tool calls before the agent is told to write its report with what it has.
MAX_TOOL_ROUNDS = int(os.environ.get("INCIDENT_AGENT_MAX_TOOL_ROUNDS", "25"))

# The same server definition Claude Code uses, so there is one place to point it at the stack.
MCP_CONFIG = Path(os.environ.get("INCIDENT_AGENT_MCP_CONFIG", REPO_ROOT / ".mcp.json"))
MCP_SERVER = "observability"

# The retrieval setup rag/README.md recommends. `memory` needs no OpenSearch: it chunks and embeds
# the corpus at startup.
RAG_BACKEND = os.environ.get("INCIDENT_AGENT_RAG_BACKEND", "opensearch-hybrid")
RAG_STRATEGY = os.environ.get("INCIDENT_AGENT_RAG_STRATEGY", DEFAULT_STRATEGY)
CORPUS = Path(os.environ.get("INCIDENT_AGENT_CORPUS", REPO_ROOT / "incident-corpus"))
