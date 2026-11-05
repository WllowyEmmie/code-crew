# -slim is much smaller than the full image; add back the git the agents need.
FROM node:22-slim
RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
