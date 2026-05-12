# Use ECR Public mirror to avoid Docker Hub unauthenticated pull rate limits
# when building in CodeBuild (shared-tenant IPs get throttled quickly).
FROM public.ecr.aws/docker/library/python:3.12-slim

# System deps: AWS CLI v2 (for s3 cp), git (Prowler needs it at runtime), curl.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        unzip \
        git \
        ca-certificates \
        bash \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscli.zip \
    && unzip -q /tmp/awscli.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/awscli.zip /tmp/aws \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Prowler from PyPI — the official distribution channel for v5+.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir prowler

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# Strip Windows CRLF line endings that break the shebang on Linux.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

# Create a non-root user for the scanner. Prowler only needs read access to the
# target AWS account via the task role and write access under /tmp — no elevated
# privileges are required inside the container. Running as UID 1000 mitigates
# container-escape impact if the base image or Prowler itself is later exploited.
RUN useradd --create-home --shell /bin/bash --uid 1000 prowler
USER prowler

# Container health — the entrypoint is a one-shot batch job, so there is no
# long-running listener. Instead verify the Prowler binary is resolvable and
# runnable as the current user, which would fail if the image were tampered
# with at rest or the user/permissions drifted.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD prowler --version > /dev/null 2>&1 || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
