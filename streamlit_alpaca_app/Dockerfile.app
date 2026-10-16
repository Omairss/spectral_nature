FROM python:3.10-slim

ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
ENV GIT_SHA=$SOURCE_REVISION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-ui.lock ./requirements-ui.lock
RUN pip install --require-hashes -r requirements-ui.lock
RUN python -m playwright install --with-deps chromium

COPY api ./api
COPY compute ./compute
COPY config ./config
COPY data ./data
COPY data_access ./data_access
COPY pipeline ./pipeline
COPY presentation ./presentation
COPY services ./services
COPY views ./views
COPY branding ./branding
COPY app.py ./app.py
COPY cache/cache_policy.json ./cache/cache_policy.json
COPY scripts/sync_robinhood_main.py ./scripts/sync_robinhood_main.py

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
