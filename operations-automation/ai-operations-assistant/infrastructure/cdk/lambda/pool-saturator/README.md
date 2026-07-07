# Pool Saturator Lambda

## Purpose

Maintains 6 persistent MySQL connections to an RDS instance configured with `max_connections=5`, saturating the connection pool for the GOAT Network Diagnostics Scenario K demo.

## How It Works

1. **Connection persistence** — Python globals survive across warm Lambda invocations, keeping MySQL connections alive between triggers.
2. **Dead connection cleanup** — On each invocation, closed/broken connections are removed from the pool.
3. **Keep-alive pings** — Active connections are pinged with `conn.ping(reconnect=False)` to prevent server-side timeout.
4. **Graceful saturation** — New connections are opened until 6 are held (or MySQL returns error 1040 "Too many connections").

## Configuration

| Environment Variable | Required | Default | Description |
|---|---|---|---|
| `DB_ENDPOINT` | Yes | — | RDS MySQL endpoint hostname |
| `DB_USERNAME` | Yes | — | Database username |
| `DB_PASSWORD` | Yes | — | Database password |
| `DB_PORT` | No | `3306` | MySQL port |
| `TARGET_CONNECTIONS` | No | `6` | Number of connections to maintain |

## Lambda Settings

- **Runtime**: Python 3.12
- **Handler**: `handler.handler`
- **Timeout**: 300 seconds (5 minutes)
- **Memory**: 128 MB
- **Reserved Concurrency**: 1 (single instance maintains all connections)
- **Trigger**: EventBridge schedule (every 5 minutes)

## Packaging

The `pymysql` dependency must be bundled with the Lambda deployment package. Install from `requirements.txt`:

```bash
pip install -r requirements.txt -t .
```

Or use CDK's `PythonFunction` construct or a Lambda Layer to handle dependency bundling automatically.

## Scenario K Context

When this Lambda runs, it holds 6 connections against an RDS instance with `max_connections=5`. Any subsequent connection attempt (e.g., from svc-alpha using the `db_connectivity_probe` tool) will fail with:

```
ERROR 1040 (HY000): Too many connections
```

This simulates a real-world connection pool exhaustion scenario for diagnostic tool demonstration.
