FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ticker_payload.py delta_ticker_client.py delta_ticker.py ./

RUN useradd --create-home --uid 10001 app
USER app

CMD ["python", "delta_ticker.py"]
