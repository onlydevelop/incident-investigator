FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

# Unit tests: `docker build --target test .` fails the build if any test fails.
FROM base AS test
RUN pip install ".[dev]"
COPY tests/ tests/
RUN python -m pytest -q -p no:cacheprovider

# Runtime image (default, last stage).
FROM base AS runtime
RUN pip install .

RUN useradd --create-home --uid 10001 app
USER app

CMD ["delta-ticker"]
