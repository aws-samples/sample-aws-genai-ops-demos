FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# Run as a non-root user. A numeric UID lets Kubernetes enforce runAsNonRoot.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin agatha
USER 10001

CMD ["python", "-u", "agent.py"]
