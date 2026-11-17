# Architecture — Aurora MySQL Incident Investigation with DevOps Agent

## Diagram

![Architecture diagram](architecture.svg)

## Component Overview

```
                        ┌─────────────────────────────────────────────────────────┐
                        │                    VPC (2 AZs, no NAT)                    │
                        │                                                           │
  Your laptop           │   Public subnet              Isolated (DB) subnets        │
  ┌──────────┐  SSH     │  ┌───────────────┐         ┌──────────────────────────┐  │
  │ inject-  │─────────▶│  │   Bastion /   │  MySQL   │  Aurora MySQL cluster     │  │
  │ failure  │  :22     │  │ load-generator│─────────▶│  writer: aurora-demo-writer│ │
  └──────────┘          │  │ (mysql client)│  :3306   │  reader: aurora-demo-reader│ │
        │               │  └───────┬───────┘         └───────────┬──────────────┘  │
        │ aws cli       │          │ reads secret               │ metrics/logs     │
        ▼               │          ▼                            ▼                   │
  rds failover-         │   Secrets Manager           CloudWatch (metrics,          │
  db-cluster ───────────┼──▶ aurora-demo/credentials   Performance Insights,        │
                        │                              error + slowquery logs)      │
                        └───────────────────────────────────┬───────────────────────┘
                                                             │
        CloudWatch alarms ──────────┐        EventBridge (RDS failover events)
        (connections, cpu,          │                 │
         deadlocks, memory,         ▼                 ▼
         replica-lag)          ┌─────────────────────────────┐
                               │      SNS: aurora-demo-alarm  │
                               └───────────────┬─────────────┘
                                               │ (only if --webhook-url given)
                                               ▼
                                    ┌────────────────────────┐        ┌──────────────────────┐
                                    │  Webhook Lambda (HMAC)  │───────▶│  AWS DevOps Agent  │
                                    └────────────────────────┘        │  (investigation +     │
                                                                       │   business-context    │
                                                                       │   skill)              │
                                                                       └───────────┬───────────┘
   Cluster tags, AWS Config, CloudTrail ◀── business context ──────────────────────┘
   (dependencies, cost impact, SLA, compliance, recent changes)
```

## Stacks

One CDK stack (region-suffixed ID), created from `infrastructure/cdk/app.py`:

- **`AuroraDemoStack-<region>`** (main; carries the solution-tracking tag)
  - `ec2.Vpc` — 2 AZs, `nat_gateways=0`, one public subnet group (bastion) and one `PRIVATE_ISOLATED` group (DB).
  - `rds.DatabaseCluster` — Aurora MySQL `8.0.mysql_aurora.3.08.0`, one writer + one reader (`db.t4g.medium`, Graviton burstable — the cheapest class that still supports CloudWatch Database Insights, formerly Performance Insights; only `db.t3.*` and `db.t4g.micro`/`small` are excluded), Database Insights on (Standard mode, free tier), `error`/`slowquery` logs exported to CloudWatch, `storage_encrypted`, generated credentials in Secrets Manager (`aurora-demo/credentials`).
  - `ec2.Instance` bastion — `t3.micro`, public subnet, instance role with read on the DB secret + `rds:FailoverDBCluster`; UserData installs the MySQL client. Fixed identifiers (`aurora-demo-cluster/-writer/-reader`) keep alarm dimensions and inject scripts deterministic.
  - Six `cloudwatch.Alarm`s → SNS. EventBridge rule matches RDS failover event IDs → SNS.
  - Conditional webhook `lambda.Function` (created only when `webhookUrl` context is supplied) subscribed to the SNS topic; HMAC-signs and POSTs to the DevOps Agent webhook.
  - Business-context tags on the cluster (`DependentServices`, `OnCallTeam`, `RevenuePerMinute`, `SLAThresholdMinutes`, `DataClassification`), read by the skill.

## Business Context Skill

Business context comes from the [`aurora-incident-business-context`](https://github.com/aws/tools-for-devops-agent/tree/main/skills/aurora-incident-business-context) skill in `aws/tools-for-devops-agent`, which is the source of truth for it. `scripts/devops_agent_setup.py` downloads `SKILL.md` and `references/` at a pinned commit and installs or updates the skill in the Agent Space through the DevOps Agent asset API, or saves a zip to upload in the web app when the installed boto3 predates that API. It also adds an inline policy granting `config:GetResourceConfigHistory` to the Agent Space role, the one action the skill needs beyond `AIDevOpsAgentAccessPolicy`. To pick up a newer reviewed version of the skill, update `SKILL_COMMIT` in that script.

## Investigation Flow

1. `inject-failure.sh <scenario>` SSHes to the bastion and runs `/opt/aurora-demo/inject`, which drives the Aurora writer/reader over the MySQL protocol (or calls `rds failover-db-cluster` for the failover scenario).
2. The workload moves a metric across an alarm threshold (or emits a failover event).
3. The alarm → SNS (or EventBridge → SNS) → webhook Lambda → DevOps Agent, which opens an investigation.
4. DevOps Agent reads CloudWatch metrics, Performance Insights, and the Aurora error/slow-query logs, then uses the skill to add business context from the cluster's tags, AWS Config, and CloudTrail.
5. The agent produces a root-cause report and answers follow-up questions on demand.

## Design Notes & Well-Architected Alignment

- **Reliability**: reader replica + Multi-AZ failover; the `failover` scenario exercises real RTO. Alarms treat missing data as not-breaching so the steady state is clean.
- **Security**: DB is isolated (no public access), encrypted at rest, MySQL reachable only from the bastion SG; credentials live in Secrets Manager and are fetched via instance role — never written to disk or code. SSH defaults to the operator's IP.
- **Cost Optimization**: `nat_gateways=0` avoids NAT charges; Graviton `db.t4g.medium` burstable instances and a `t3.micro` bastion keep the running cost low; `cleanup` destroys everything.
- **Operational Excellence**: one-command deploy/inject/rollback/cleanup; dedicated alarms are gated so only the intended signal fires per scenario.

### Trade-offs
- The bastion is intentionally public for a friction-free demo (SSH-in). In a hardened setup you would reach it via SSM Session Manager (the instance role already includes `AmazonSSMManagedInstanceCore`) and drop the public IP + port 22.
- `db.t4g.medium` is a burstable, cost-optimized default rather than a fixed-performance class. `replica-lag` remains best-effort (Aurora's shared storage keeps replica lag tiny regardless of instance size). `memory-pressure` may behave differently than on a larger instance — see the [Failure Scenarios](README.md#failure-scenarios) table for current status.
