FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install .

RUN useradd --create-home --uid 10001 app
USER app

CMD ["delta-ticker"]
