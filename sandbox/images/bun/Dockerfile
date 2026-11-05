FROM oven/bun:1
RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
