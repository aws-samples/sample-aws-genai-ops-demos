# DART agent container.
# Build context is this directory (agent/); the test fixture and tests/ live outside it
# and are intentionally NOT shipped in the image (see .dockerignore).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the agent source (the build context is agent/).
COPY . /app

# Run as a non-root user.
RUN useradd --create-home --uid 10001 dartuser \
    && chown -R dartuser:dartuser /app
USER dartuser

# Liveness: the agent package imports cleanly.
HEALTHCHECK CMD python -c 'import agent' || exit 1

ENTRYPOINT ["python", "agent.py"]
