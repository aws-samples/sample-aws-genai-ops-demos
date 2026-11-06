# AgentCore Runtime image. ARM64 is required by AgentCore.
#
# All three engines are included deliberately: T5 validate_script runs a real
# 1-VU smoke test, and "verified by execution" is the point of this agent.
# Dropping an engine degrades T5 to static checking for that engine only —
# build_k6_script and build_locust_script stay advertised as tools either way,
# so a missing runtime means the agent can generate a script it can never
# validate. JMeter and k6 are binaries; Locust goes into its own venv. None of
# the three shares the agent's site-packages.

# Pinned to Debian bookworm: the moving `python:3.12-slim` tag now resolves to
# trixie, where `openjdk-17-jre-headless` has no install candidate (replaced by
# openjdk-21). Pinning keeps JDK17 and makes the build reproducible.
FROM --platform=linux/arm64 python:3.12-slim-bookworm

# --- JMeter (for T5 validate_script) ---------------------------------------
ARG JMETER_VERSION=5.6.3
# Fetch JMeter from the fast Apache CDN first (dlcdn), falling back to the slow
# archive mirror only if the CDN lacks this version. archive.apache.org alone
# has stalled long enough to overrun the in-stack build's wait window.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless curl \
    && rm -rf /var/lib/apt/lists/* \
    && ( curl -fsSL --retry 3 --retry-delay 2 "https://dlcdn.apache.org/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz" \
         || curl -fsSL --retry 3 --retry-delay 2 "https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz" ) \
       | tar -xz -C /opt \
    && ln -s /opt/apache-jmeter-${JMETER_VERSION}/bin/jmeter /usr/local/bin/jmeter

# --- k6 (for T5 validate_script on .js) -------------------------------------
# Single static binary, so no runtime deps beyond the tarball. arm64 explicitly:
# AgentCore is arm64-only and the amd64 asset would install without error and
# then fail at exec time with "cannot execute binary file".
ARG K6_VERSION=2.2.0
RUN curl -fsSL --retry 3 --retry-delay 2 \
      "https://github.com/grafana/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-linux-arm64.tar.gz" \
      | tar -xz -C /opt \
    && ln -s /opt/k6-v${K6_VERSION}-linux-arm64/k6 /usr/local/bin/k6 \
    && k6 version

# --- Locust (for T5 validate_script on .py) ---------------------------------
# Installed into its own venv, deliberately NOT into the agent's site-packages.
# Locust pulls in 28 packages including gevent/greenlet, and sharing the
# interpreter broke every invoke with a 500 on 2026-08-27:
#   RuntimeError: asyncio.run() cannot be called from a running event loop
# bedrock-agentcore dispatches the entrypoint through run_in_threadpool and
# strands calls asyncio.run() inside that thread. Which of the 28 packages is
# the culprit was never identified — strands, bedrock-agentcore, starlette and
# the OTel packages were identical across the working and broken builds — and
# the venv makes that question moot: the tree never enters the agent's import
# path. validate_script reaches this interpreter via LOCUST_PYTHON below.
ARG LOCUST_VERSION=2.46.4
RUN python3 -m venv /opt/locust-venv \
    && /opt/locust-venv/bin/pip install --no-cache-dir "locust==${LOCUST_VERSION}" \
    && /opt/locust-venv/bin/python -m locust --version

# --- agent -------------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py aws_utils.py ./
COPY prompt/ prompt/
COPY tools/ tools/
COPY builder/ builder/

# Credentials come from the AgentCore execution role — no .env in the image.
ENV AGENTCORE_RUNTIME=1
# Locust lives outside this interpreter; validate_script honours this and falls
# back to sys.executable when unset, which is what local runs rely on.
ENV LOCUST_PYTHON=/opt/locust-venv/bin/python
ENV PYTHONPATH=/app:/app/builder

EXPOSE 8080
# opentelemetry-instrument routes stdout/logs + traces into the OTEL pipeline
# that AgentCore ships to CloudWatch. PYTHONUNBUFFERED so logs are not withheld.
ENV PYTHONUNBUFFERED=1
CMD ["opentelemetry-instrument", "python3", "agent.py"]
