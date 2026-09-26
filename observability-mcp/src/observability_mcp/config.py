import os

# The *.localhost ingresses from ../deploy/observability. Point these elsewhere (e.g. a
# `kubectl port-forward`) with the environment variables.
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus.localhost")
LOKI_URL = os.environ.get("LOKI_URL", "http://loki.localhost")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://tempo.localhost")
# Per HTTP request to a backend.
HTTP_TIMEOUT = float(os.environ.get("OBSERVABILITY_HTTP_TIMEOUT", "30"))
