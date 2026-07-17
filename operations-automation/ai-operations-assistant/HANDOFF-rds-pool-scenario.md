# RDS Connection Pool Scenario — Handoff Context

## Current State (July 17, 2026)

### What's Deployed in Account 157643525386 (us-east-1)

| Stack | Status | Key Resources |
|-------|--------|---------------|
| GOATNetworkData-us-east-1 | ✅ | DynamoDB, S3 |
| GOATNetworkInfra-us-east-1 | ✅ | VPC, CodeBuild, ECR, Collector |
| GOATNetworkRuntime-us-east-1 | ✅ | AgentCore runtime (container rebuilt with enhanced probe) |
| GOATDevOpsIntegration-us-east-1 | ✅ | MCP server registered (`f996e1c1-b363-498b-8c29-7e64b180aa6a`) |
| GOATDemoScenarioC-us-east-1 | ✅ | TLS fragmentation topology |
| GOATDemoScenariosGL-us-east-1 | ✅ | Scenarios G-L (pool saturator Lambda, RDS, etc.) |

### Key Instance IDs

- **svc-alpha (EC2):** `i-014a450362093345a` (10.99.32.161)
- **svc-data-01 (RDS):** `svc-data-01.comvupvqkrj2.us-east-1.rds.amazonaws.com`
- **Pool Saturator Lambda:** `svc-data-sync-worker`
- **MCP Endpoint:** `https://h7pxl1uqya.execute-api.us-east-1.amazonaws.com/prod/`
- **AgentSpace ID:** `694411a8-520c-4a7b-bc81-82160b1b9f4b`

### RDS Configuration

- Engine: MySQL 8.4.8
- Instance class: db.t4g.micro
- **max_connections: 60** (formula-based for this instance class)
- Parameter group: custom with `authentication_policy=*:mysql_native_password`
- Admin user: `admin` / `GoatDemoK2026!` (uses `mysql_native_password` auth plugin)

### Pool Saturator Status

- Lambda holds **58 connections** (out of max 60)
- Triggered every **1 minute** via EventBridge
- TARGET_CONNECTIONS just changed from 90 → **150** (needs redeploy of GL stack)
- Uses `pymysql` via Lambda layer (`arn:aws:lambda:us-east-1:157643525386:layer:pymysql:1`)

## Remaining Fix Needed

### Problem

The `db_connectivity_probe` enhanced script (ENHANCED_DB_CONNECTIVITY_PROBE_SCRIPT) currently only reports pool exhaustion when the connection **fails** with error 1040. If there's even 1 free slot, the probe connects successfully and reports "all phases passed" — it never runs Phase 6 (pool status check via `SHOW STATUS`).

### What Needs to Change

**File:** `agents/network-agent/scripts/db_connectivity_probe_script.py`

**In the `ENHANCED_DB_CONNECTIVITY_PROBE_SCRIPT` template**, find the section after the connection test (Phase 4) passes. Currently, if auth succeeds, it skips pool checks. It should **always** run Phase 6 when a MySQL connection is established:

1. After successful auth in Phase 4, keep the connection open
2. Use that connection to execute:
   - `SHOW GLOBAL STATUS LIKE 'Threads_connected'`
   - `SHOW GLOBAL VARIABLES LIKE 'max_connections'`
3. Feed those values into `detect_pool_exhaustion()` 
4. If utilization >= 90%, set `root_cause_category = 'pool_exhaustion'` and add remediation steps
5. Close the connection

**Key logic:** The probe should report pool exhaustion even when it manages to connect on one of the last free slots. The important data is Threads_connected/max_connections ratio, not whether the probe itself got through.

### After the Fix

1. Redeploy GL stack (TARGET_CONNECTIONS 150):
```powershell
$env:AWS_PROFILE = "AdministratorAccess-157643525386"
cd operations-automation\ai-operations-assistant\infrastructure\cdk
npx cdk deploy GOATDemoScenariosGL-us-east-1 --app "npx ts-node --prefer-ts-exts bin/demo-scenarios-app.ts" --require-approval never
```

2. Invoke Lambda to re-saturate:
```powershell
aws lambda invoke --function-name "svc-data-sync-worker" --payload '{}' NUL --output text
```

3. Redeploy NetworkRuntime (to pick up updated probe script):
```powershell
cd operations-automation\ai-operations-assistant\infrastructure\cdk
Remove-Item -Recurse -Force cdk.out -ErrorAction SilentlyContinue
npx cdk deploy GOATNetworkRuntime-us-east-1 --require-approval never
```

4. Test with DevOps Agent:
> Use the rds troubleshooting tool to investigate why instance i-014a450362093345a can't connect to svc-data-01.comvupvqkrj2.us-east-1.rds.amazonaws.com on port 3306

### Expected Outcome After Fix

The probe should return:
```json
{
  "connection_pool_status": {
    "status": "exhausted",
    "threads_connected": 60,
    "max_connections": 60,
    "utilization_percent": 100.0
  },
  "root_cause_category": "pool_exhaustion",
  "remediation_steps": [
    "Increase max_connections in parameter group",
    "Use RDS Proxy for connection pooling",
    "Reduce client concurrency"
  ]
}
```

## Architecture Summary

```
DevOps Agent → MCP Endpoint (API GW) → Lambda (mcp-handler.ts)
    → AgentCore Runtime (container with main.py)
        → SSM RunShellScript on svc-alpha
            → ENHANCED_DB_CONNECTIVITY_PROBE_SCRIPT runs 7 phases
            → Returns structured JSON report
```

## Key Files

| File | Purpose |
|------|---------|
| `agents/network-agent/main.py` (line ~2451) | Handler that formats and executes the probe template |
| `agents/network-agent/scripts/db_connectivity_probe_script.py` | Contains `ENHANCED_DB_CONNECTIVITY_PROBE_SCRIPT` template + helper functions |
| `infrastructure/cdk/lib/demo-scenario-diagnostics-gl-stack.ts` | CDK stack with RDS, Lambda saturator, parameter group |
| `infrastructure/cdk/lib/base-runtime-stack.ts` | Base class for agent container builds (fixed exclude patterns) |
| `devops-integration/infrastructure/cdk/lib/devops-integration-stack.ts` | MCP registration via AWS::DevOpsAgent::Service |
| `demo-scenarios/deploy-demo-scenarios.ps1` | Deployment script with pymysql layer + post-deploy steps |
| `redeploy-network-mcp.ps1` | Full teardown + redeploy script (fixed wave-based deletion) |

## Fixes Applied This Session

1. **CDK stack** — Added custom parameter group (`authentication_policy=*:mysql_native_password`), pymysql Lambda layer reference, clean inline handler code
2. **base-runtime-stack.ts** — Excluded `.hypothesis`, `.pytest_cache`, `test_*` from CDK assets (fixed Windows path-length CodeBuild failure)
3. **redeploy-network-mcp.ps1** — Fixed smart quotes (Unicode), wave-based deletion order, `$ErrorActionPreference` wrapping
4. **deploy-demo-scenarios.ps1** — Added pymysql layer auto-creation, pool saturator post-deploy, SSM params via file (UTF8 no BOM)
5. **main.py** — Switched from inline `_DB_CONNECTIVITY_PROBE_SCRIPT_TEMPLATE` to imported `ENHANCED_DB_CONNECTIVITY_PROBE_SCRIPT`
6. **demo-scenario-diagnostics-gl-stack.ts** — Fixed `b"#":` string literal bug (line 549), added `ScenarioKTestPrompt` output, TARGET_CONNECTIONS 90→150
