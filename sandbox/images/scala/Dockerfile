# sbt has no official image, so install it from its GitHub release onto a JDK.
FROM eclipse-temurin:21-jdk
RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://github.com/sbt/sbt/releases/download/v1.10.7/sbt-1.10.7.tgz | tar xz -C /opt
ENV PATH="/opt/sbt/bin:${PATH}"
WORKDIR /workspace
