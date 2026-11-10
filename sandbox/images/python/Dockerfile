# -slim is ~150MB instead of ~1GB; add back the git the agents need.
FROM python:3.12-slim
RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest ruff
WORKDIR /workspace
