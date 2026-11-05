FROM gcc:14
RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 cmake \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
