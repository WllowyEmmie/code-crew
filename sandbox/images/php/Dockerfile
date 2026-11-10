FROM php:8.3-cli
RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 git ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
# PHP_CodeSniffer: phpcs checks PSR-12, phpcbf fixes it.
ENV COMPOSER_HOME=/opt/composer PATH="/opt/composer/vendor/bin:${PATH}"
RUN composer global require --no-interaction squizlabs/php_codesniffer
WORKDIR /workspace
