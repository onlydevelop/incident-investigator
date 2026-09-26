import os
from pathlib import Path

from incident_rag.ingest import DEFAULT_STRATEGY

# agent/src/incident_agent/config.py -> the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]

MODEL = os.environ.get("INCIDENT_AGENT_MODEL", "claude-opus-5")
# Investigations are multi-step and evidence-heavy, which is where higher effort pays off.
EFFORT = os.environ.get("INCIDENT_AGENT_EFFORT", "high")
# Model calls per run, the last two kept for writing the report (see graph.py).
MAX_STEPS = int(os.environ.get("INCIDENT_AGENT_MAX_STEPS", "30"))

# Where approved remediation is applied, through kubectl. The context defaults to kubectl's current one.
NAMESPACE = os.environ.get("INCIDENT_AGENT_NAMESPACE", "incident-investigator")
KUBE_CONTEXT = os.environ.get("INCIDENT_AGENT_KUBE_CONTEXT") or None

# The same server definition Claude Code uses, so there is one place to point it at the stack.
MCP_CONFIG = Path(os.environ.get("INCIDENT_AGENT_MCP_CONFIG", REPO_ROOT / ".mcp.json"))
MCP_SERVER = "observability"

# The retrieval setup rag/README.md recommends. `memory` needs no OpenSearch: it chunks and embeds
# the corpus at startup.
RAG_BACKEND = os.environ.get("INCIDENT_AGENT_RAG_BACKEND", "opensearch-hybrid")
RAG_STRATEGY = os.environ.get("INCIDENT_AGENT_RAG_STRATEGY", DEFAULT_STRATEGY)
CORPUS = Path(os.environ.get("INCIDENT_AGENT_CORPUS", REPO_ROOT / "incident-corpus"))
