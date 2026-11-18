# Aurora MySQL Incident Investigation with Business Context (AWS DevOps Agent)

A connection storm on an Aurora MySQL writer is easy to diagnose from metrics. Knowing who it
hurts, what each minute costs and whether anyone has to be notified is harder, and that
knowledge usually lives in tags, change history and people's heads. This demo stands up one
Aurora MySQL cluster whose tags carry that business metadata, starts a real connection storm
or changes the tags on demand, and lets AWS DevOps Agent investigate with the
[`aurora-incident-business-context`](https://github.com/aws/tools-for-devops-agent/tree/main/skills/aurora-incident-business-context)
skill from the Agent Tools repository loaded. The skill tells the agent where business
evidence lives (tags, CloudTrail, AWS Config) and to report only what that evidence supports:
a cost tied to real inputs, a compliance window that stays conditional, and a named gap where
nothing is tagged.

## At a glance

| | |
|---|---|
| **Capability shown** | Skill `aurora-incident-business-context` (Agent Tools repository), fetched from `main` at deploy time, never copied here. Every deploy gets the latest version |
| **Motions** | Incident RCA (a DatabaseConnections alarm starts an investigation) and Chat (a business-impact question, and an optional follow-up on each alarm-driven card) |
| **Environment** | One Aurora MySQL writer (db.t4g.medium, Performance Insights on) in a VPC with no internet path, carrying five business tags |
| **Scenarios** | Connection storm on a business-critical cluster, connection storm on a cluster tagged GDPR, business tags missing |
| **Lab** | Inject, watch, roll back from a CloudFront-hosted control room; every injection auto-reverts |
| **Deploy time** | 14 minutes measured (database stack 468 s) with the Agent Space stack already deployed and unchanged; a full fresh deploy was not timed |
| **Running cost** | About $2.35 per day plus agent time (see [Cost](#cost)) |
| **Region** | Built and documented for `us-east-1`; any region where AWS DevOps Agent is available works |

## Interactive Demo

Coming soon: a recorded walkthrough of the Lab.

## Why this demo is shaped this way

The demo was derived from the skill (skill, then motion, then scenarios, then the smallest
environment). The builder's answers at each fork, recorded so the next presenter knows why:

- **Both declared motions.** The skill declares `INCIDENT_RCA, CHAT`. The two storm scenarios
  run as investigations started by an alarm; the tags scenario runs in Chat; each alarm-driven
  card also offers an optional Chat follow-up. The skill is registered for exactly those two
  agent types (`CHAT,INCIDENT_RCA`).
- **A follow-up prompt on alarm cards (deviation from the Lab UI guide).** The guide puts
  prompts on Chat cards only and shows the trigger chain on alarm cards. Here the alarm cards
  show the trigger chain and also an "Optional follow-up in Chat, after the investigation"
  prompt, because the builder chose Chat as a follow-up to the investigations.
- **Three scenarios.** The connection storm is the headline: the investigation adds cost per
  minute (from `RevenuePerMinute`), SLA timing (from `SLAThresholdMinutes`) and labels the
  dependent services as unverified (they exist only in the `DependentServices` tag). The GDPR
  scenario runs as an investigation so "Reporting obligation undetermined" appears as a gap in
  the summary; the 72-hour window stays conditional, for the compliance owner to decide. The
  missing-tags scenario is kept as secondary: without the skill the agent also declines to
  invent a figure, so the difference is narrower (the four-line form, the recent-changes check,
  and the tag to add for each gap).
- **Dropped:** recent-change correlation (without the skill the agent also found the change),
  failover (same business-impact difference as the storm, plus a real outage and fail-back),
  cost from operator input (needs no environment).
- **No application.** The dependent services exist only in a tag, on purpose: that tag is a
  claim the skill must label unverified. A fake checkout page would contradict the scenario.
- **No AWS Config recorder.** There is one per region and it may already exist. The agent role
  can call `config:GetResourceConfigHistory`; without a recorder the call returns "not
  discovered". In the observed runs the investigations used CloudTrail (they named the Lab's own
  change as the cause), and the Chat answer said Config had no history and that it did not
  query CloudTrail.
- **Scoped read access for the storm evidence.** The agent role can also call
  `lambda:GetFunction` and `lambda:GetFunctionConcurrency` on the load function and
  `rds:DownloadDBLogFilePortion` on the writer, which the first observed investigations listed
  as denied. In the re-observed storm investigation the agent used them: "Reserved concurrency
  is currently 2" and "The MySQL error log was empty". The ARNs use the Agent Space stack's account and region, so they match only when the
  Agent Space and the database share a region (the default). `lambda:GetDurableExecution` is not
  granted, on purpose: the Lab engine's durable executions stay unreadable to the agent, which
  keeps it out of the Lab's control plane.
- **Baseline `DataClassification=Confidential`** names no framework, so the skill applies none
  until the GDPR scenario adds `Compliance=GDPR`.
- **Cost and duration** accepted under a ceiling of about $5 per day. The measured deploy took
  14 minutes (database stack 468 s) with the Agent Space stack unchanged; a full fresh deploy
  was not timed.
- **Skill fetched from `main`.** The deploy scripts and the Lab fetch the latest version of the
  skill from the Agent Tools repository on every deploy, so the demo always runs the current skill.
  The `withCapability` statements were observed on 2026-11-18; if the skill changes, re-check them.
- **CDK in `cdk/`, not `infrastructure/cdk/`.** The contributor guide's layout shows
  `infrastructure/cdk/`; this demo keeps `cdk/` to match the other AWS DevOps Agent demos
  (`observability/eks-investigation-devops-agent`, `resilience/fsx-windows-sla-review-devops-agent`).

## The scenarios

Each scenario states what the agent should conclude with the skill and without it
(`demonstrates` in [`lab/scenarios.yaml`](lab/scenarios.yaml), the single source of truth for
the engine, the API and the Lab cards). The "With the skill" column is the agent's output
observed on a deployed copy of this demo (2026-11-18). The "Without" column is a prediction
from the skill's own evaluations; see [Before you present](#before-you-present).

| Scenario | What the Lab does | Trigger | With the skill | Without |
|---|---|---|---|---|
| **Connection storm on a business-critical cluster** | Switches the load function on: it opens and holds about 85 of the writer's about 90 connections (max_connections). In the observed run no client was refused yet (AbortedClients 0) | DatabaseConnections alarm, investigation | Root cause plus the gaps "Impact on dependent services unverified", "Business cost unconfirmed" (5,100 USD per minute, when the 20-minute SLA would be breached, no refused connections seen) and "Reporting obligation undetermined" (no compliance framework tagged) | Root cause from DatabaseConnections and Performance Insights; no cost, SLA or compliance line |
| **Connection storm on a cluster tagged GDPR** | Adds `Compliance=GDPR`, sets `DataClassification=PersonalData`, then the same storm | DatabaseConnections alarm, investigation | Gap "GDPR reporting obligation undetermined": GDPR found in the tags, 72 hours if assessed as a personal data breach, for the compliance owner to decide | Compliance left out, or the 72-hour clock declared running |
| **Business tags missing** | Removes the five business tags | Chat prompt on the card | A Business Impact section: dependent services "None found", cost "Not calculable", no compliance tag found, and a Recent changes line (the cluster is new; CloudTrail not queried). It ends by naming the five tags to add | Also declines to invent a figure; no recent-changes check, no four-line form |

Talk track for the headline scenario: *"The connection storm is the technical cause. The skill
adds what it costs per minute, when the SLA breaks, and that the list of affected services
comes from a tag nobody has verified."*

Every injection is one Lambda durable function execution: inject, wait for a rollback, revert.
Choose **Rollback** or let the auto-revert run (30 minutes for the storms, 15 for the tags);
either road ends in the same revert, with the page closed or not.

## Architecture

Three CDK stacks (diagram and data flows in [ARCHITECTURE.md](ARCHITECTURE.md)):

| Stack | What it holds |
|---|---|
| `AuroraBizCtxAgentSpace-{agent-region}` | The Agent Space: IAM roles, operator app, AWS association, eventChannel webhook (shared `DevOpsAgentSpace` construct), plus `config:GetResourceConfigHistory`, read access to the load function and the writer's log files on the monitoring role |
| `AuroraBizCtxDatabase-{region}` | VPC (two isolated subnets, no NAT gateway), Aurora MySQL cluster and writer with the business tags on the cluster, the connection load function, Secrets Manager and Lambda interface endpoints, the DatabaseConnections alarm and the alarm → webhook trigger chain (shared `AlarmTrigger` construct). Main stack: carries the solution adoption tracking tag `(uksb-do9bhieqqh)(tag:aurora-mysql-incident-investigation-devops-agent,observability)` |
| `AuroraBizCtxLab-{region}` | The Lab: durable engine + API (shared `LabEngine` construct) behind a Lambda function URL, the site on S3 + CloudFront with HTTP Basic authentication at the edge |

Business tags on the cluster (set by CDK, restored by every revert):

| Tag | Value |
|---|---|
| `DependentServices` | `checkout-service:CRITICAL - order-history-api:HIGH - analytics-etl:MEDIUM` |
| `OnCallTeam` | `Data Platform / DBRE` |
| `RevenuePerMinute` | `5100` |
| `SLAThresholdMinutes` | `20` |
| `DataClassification` | `Confidential` |

The cost figures are the tags' values: demo data, not a benchmark.

## Prerequisites

- An AWS account with administrator access, in a region where
  [AWS DevOps Agent is available](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-supported-regions.html)
  (this demo was built for `us-east-1`; set `DEVOPS_AGENT_REGION` to host the Agent Space elsewhere)
- AWS CLI 2.34.64+ (the DevOps Agent Asset API the skill registration uses), Node.js 20+,
  Python 3.12+ with pip (the Lambda bundles are pip-installed at synth time), Git
- Outbound HTTPS from the machine that runs the scripts to `github.com` (the skill) and to
  `truststore.pki.rds.amazonaws.com`: every synth, including the one `destroy-all` runs, downloads
  the RDS CA bundle into the load function's asset, so the function verifies the writer's TLS
  certificate
- One free VPC under the VPCs-per-Region quota (Service Quotas code `L-F678F1CE`, default 5):
  the database stack creates its own VPC. Compare
  `aws service-quotas get-service-quota --service-code vpc --quota-code L-F678F1CE` with
  `aws ec2 describe-vpcs --query "length(Vpcs)"`, and request an increase if no VPC is free
- Lambda unreserved concurrency of at least 102 in the account and region: the Lab reserves 2
  for the load function while a storm runs (`aws lambda get-account-settings`)
- Credentials configured your usual way (`aws sso login --profile <name>` and `AWS_PROFILE`, an
  IAM role, or `aws configure`); the deploy script verifies them with `aws sts get-caller-identity`

## Quick start

```powershell
# Windows / PowerShell
cd observability/aurora-mysql-incident-investigation-devops-agent
$env:AWS_REGION = "us-east-1"
.\deploy-all.ps1
```

```bash
# macOS / Linux
cd observability/aurora-mysql-incident-investigation-devops-agent
export AWS_REGION=us-east-1
./deploy-all.sh
```

The script prints the cost and duration first, then:

1. deploys the Agent Space and reads its outputs (the webhook secret stays in Secrets Manager);
2. deploys the VPC, the Aurora MySQL cluster with its business tags, the load function, the alarm
   and the trigger chain (468 s in the measured deploy; the writer instance takes about 6 minutes of it);
3. deploys the Lab and publishes its site;
4. fetches the skill from `main` with `shared/devops-agent/agent-tools/deploy-skill.*`, packages it and registers it in the Agent Space through
   the Asset API for agent types `CHAT,INCIDENT_RCA`. Nothing to upload or paste in the console.

If the Lab site build fails, the script stops and prints the end of `frontend-build.log`.

It ends with the Lab URL, the sign-in (user `presenter`, a generated password; pass
`-LabPassword` / `--lab-password` to choose one) and the Agent Space console URL. Re-running the
script is safe: every step is idempotent and the skill is updated in place.

## Run the demo

1. Open the Lab and sign in. The **Environment** panel shows the cluster, the writer and the
   business tags (5 of 5). Every card shows **Failure injection: Not injected**, and the
   **Agent skill** panel at the bottom shows the skill registered.
2. **Connection storm on a business-critical cluster**: choose **Inject**. The **Load
   generator** fact reads On at once; **Connections** climbs within about a minute (it lags by
   a minute). The alarm fires within about 3 minutes and the investigation appears under
   **Agent tasks**. Open it: the root cause is the connection storm, and the gaps list "Impact
   on dependent services unverified", "Business cost unconfirmed" (cost per minute, when the
   SLA would be breached) and "Reporting obligation undetermined" (no compliance framework
   tagged). Optionally
   paste the card's follow-up prompt in Chat. Choose **Rollback**: the load stops, the Lab waits
   for DatabaseConnections to fall under the threshold, the alarm returns to OK. If the switch
   was changed outside the Lab, Rollback switches the load off at once and the Connections fact
   follows within about two minutes.
3. **Connection storm on a cluster tagged GDPR**: if you ran the storm first, wait until its
   investigation is complete under **Agent tasks** and the alarm reads OK. Then the same flow;
   the **Compliance tag** fact reads GDPR. The investigation's gaps include "GDPR reporting
   obligation undetermined", with GDPR and its
   72-hour window conditional on a breach determination. Rollback restores `Confidential`.
4. **Business tags missing**: choose **Inject**; the **Business tags** fact lists all five as
   missing. Copy the prompt from the card and ask it in Chat (**Open Chat**); the response
   comes back in the chat, not under Agent tasks. The response has a Business Impact section:
   dependent services none found, cost not calculable, no compliance tag found, and recent
   changes. It ends by naming the five tags to add. Choose **Rollback**; the **Business tags**
   fact shows all five again.

Investigations ran 4.5 to 6 minutes in the observed runs. Past 20 minutes after the storm started, the skill
should report the SLA as breached; that is correct behaviour. Talk track lines, incident chains
and customer impact are on each card (**Walkthrough**).

### Before you present

- The `withCapability` statements in `lab/scenarios.yaml` quote the agent's output on a
  deployed copy of this demo (2026-11-18).
  For example, the storm investigation listed "Impact on dependent services unverified" and
  "~$25,500 if 04:05–04:10 counts as downtime (5 min × $5,100/min, RevenuePerMinute tag). SLA
  (20 min) not breached; breach would be ~04:25 UTC if this continues" (the times are that
  run's). For `business-tags-removed`, the first Chat run got "Skill not found" for the skill
  and answered without the Business Impact form; a new chat loaded the skill and used it. If
  the response has no Business Impact section, ask again in a new chat. Its Recent changes
  line came from the stack's creation time, not CloudTrail. Run each scenario once on your deployment
  and compare; treat "the agent found no problem" on a storm scenario as a failed run (did the
  inject land, did it revert early), not as a pass.
- After a storm scenario's rollback, wait for its investigation to finish and give the alarm
  time in OK before the GDPR scenario. In the observed run the GDPR inject's first alarm, 3
  minutes after the storm's rollback, was added to the storm's investigation, which did not
  re-read the tags; the GDPR finding came from a second investigation 16 minutes later.
- Run one scenario at a time. The Lab refuses a second inject while one is active.
- A fresh deploy puts its own `CreateDBCluster` and `AddTagsToResource` calls in the 14-day
  CloudTrail lookback; the agent may list them under Recent changes, and the Lab's own tag
  changes too. Correlation is not causation, and the skill says so.

## The Lab

The Lab is the presenter's control room and this demo's whole site. It is built on the shared
Lab mechanism ([`shared/devops-agent/README.md`](../../shared/devops-agent/README.md)): the
durable engine runs each injection as one execution (inject → wait for a rollback → revert);
"is it injected" is read from the live environment (the cluster's tags, the load function's
reserved concurrency, DatabaseConnections, the alarm), never from a stored flag; one scenario
runs at a time (a second inject gets HTTP 409); the agent's tasks and spend come from the
DevOps Agent data plane.

The storm's on/off switch is the load function's reserved concurrency: 0 at deploy (nothing can
run), 2 while a storm is on (one invocation holds the connections, the next takes over before
the 15-minute Lambda limit). The load function checks the switch every 5 seconds and closes its
connections when it reads 0; it also stops on its own one hour after the storm started, even if
nothing reverted it.

Authentication is HTTP Basic at the CloudFront edge (a CloudFront Function checks every request,
site and API alike, then drops the header); the Lab API is a Lambda function URL that only
CloudFront can call (origin access control). The same function refuses a POST or DELETE on
`/admin/*` that lacks the `x-lab-request` header the Lab's own requests carry, so a page on
another site cannot start an injection through the presenter's cached credentials. The
credentials are generated by the deploy script and printed as soon as the Lab stack is up
(re-run with `--lab-password` / `-LabPassword` to keep them if a later step fails); they are
embedded in the CloudFront Function, so anyone who can read the stack template can read them.
That is acceptable for a demo control room; it is not a pattern for production, and a real
password should never be passed with `--lab-password`.

### Shared with the repository

The demo does not deploy from this folder alone; deploy from a full clone.

| File | Used by | For |
|---|---|---|
| `shared/scripts/check-prerequisites.ps1` / `.sh` | `deploy-all.*`, `destroy-all.*` | Tooling, credentials, region, DevOps Agent availability |
| `shared/scripts/deploy-cdk.ps1` / `.sh` | `deploy-all.*`, `destroy-all.*` | CDK bootstrap, dependencies, one stack per call |
| `shared/devops-agent/agent-tools/deploy-skill.ps1` / `.sh` | `deploy-all.*` | Fetches the skill from `main`, builds the zip, registers it in the Agent Space (Asset API) |
| `shared/devops-agent/agent-space/cdk/agent-space.ts` + `lambda/webhook-provisioner/` | `cdk/lib/agent-space-stack.ts` | `DevOpsAgentSpace`: roles, Agent Space, operator app, AWS association, webhook |
| `shared/devops-agent/agent-space/cdk/alarm-trigger.ts` + `lambda/alarm-trigger/` | `cdk/lib/database-stack.ts` | `AlarmTrigger`: SNS → Lambda → HMAC-signed incident on the webhook |
| `shared/devops-agent/lab/cdk/lab-engine.ts` | `cdk/lib/lab-stack.ts` | `LabEngine`: the engine and API functions, their role, the `live` alias, the code bundle |
| `shared/devops-agent/lab/lambda/engine.py`, `devops_agent.py`, `requirements.txt` | `lab/engine_main.py`, `lab/api.py` | The durable engine and its control plane; agent tasks and spend |
| `shared/utils/aws-utils.ts` | `cdk/bin/app.ts` | Region detection |

## Project structure

```
├── deploy-all.ps1 / .sh          # One-command deployment (prints cost and duration first)
├── destroy-all.ps1 / .sh         # Removes everything, Agent Space and run-time log groups included
├── validation.yaml               # Deploy / verify / destroy contract for the repository's validation workflow
├── cdk/
│   ├── bin/app.ts                # Three stacks, region-suffixed; tracking on the database stack
│   ├── lib/constants.ts          # Business tags, max_connections, alarm threshold: one definition for CDK and the Lab
│   ├── lib/agent-space-stack.ts  # Shared DevOpsAgentSpace + Config, load function and writer-log reads + outputs
│   ├── lib/database-stack.ts     # VPC, Aurora MySQL, endpoints, load function, alarm, shared AlarmTrigger
│   ├── lib/lab-stack.ts          # Shared LabEngine, function URL, S3 + CloudFront, Basic Auth function
│   ├── lambda/connection-load/   # Opens and holds MySQL connections while its reserved concurrency is above 0
│   └── test/stacks.test.ts       # The promises above, checked on the synthesized templates
├── lab/
│   ├── scenarios.yaml            # The three scenarios, the skill reference, the presenter notes
│   ├── handlers.py               # inject / revert / probe per scenario (RDS tags, Lambda concurrency, CloudWatch)
│   ├── api.py                    # Lab API routes (function URL events)
│   ├── engine_main.py            # Durable function entry point: shared engine + these handlers
│   ├── facts.py, scenarios.py    # Labelled facts for the UI; YAML loader
│   └── tests/                    # Engine on both roads, handlers, load function, API contract, YAML consistency
├── frontend/                     # The Lab site (React, Vite, Cloudscape)
└── ARCHITECTURE.md               # Stacks, data flows, security model
```

## Cost

Approximate, `us-east-1` list prices, while the demo is deployed.

| Resource | Specification | Per day | Per month |
|---|---|---|---|
| Aurora MySQL writer | db.t4g.medium, minimal storage and I/O, Performance Insights 7-day retention (no charge) | \~$1.75 | \~$53 |
| Interface endpoints | Secrets Manager and Lambda, one AZ each | \~$0.48 | \~$15 |
| CloudFront, S3, Lambda, CloudWatch, Secrets Manager | Demo traffic, one alarm, two secrets | \~$0.10 | \~$3 |
| **Infrastructure** | | **\~$2.35** | **\~$71** |
| AWS DevOps Agent | Per agent-second, list price $0.0083/s (about $2.50-3 per investigation; the observed investigations ran 4.5-6 minutes) | usage | usage |

Running the GDPR scenario right after the storm can add a second agent run: in the observed
run the GDPR alarm was attached to the storm's investigation, which ran again for about 2
minutes before the GDPR investigation started. The Lab's **Agent spend** panel shows the month's agent time. The deploy script prints the
estimate before it starts; `destroy-all` removes everything.

## Troubleshooting

- **The database stack failed and is in `ROLLBACK_COMPLETE`.** A stack in that state cannot
  be updated, so re-running the deploy script fails on it. Find the cause in the stack's
  events (for example "The maximum number of VPCs has been reached": see the VPC quota under
  [Prerequisites](#prerequisites)), fix it, delete the failed stack
  (`aws cloudformation delete-stack --stack-name AuroraBizCtxDatabase-<region>`), wait for the deletion to finish and re-run the
  deploy script.
- **Inject fails with "decreases account's UnreservedConcurrentExecution below its minimum".**
  The account's Lambda concurrency limit is too low to reserve 2 for the load function. Request
  a higher limit (Service Quotas, Lambda concurrent executions) and retry.
- **Connections stays low after Inject.** The **Load generator** fact reads `On, connections
  not held yet`. Right after Inject that is the metric's one-minute lag; if it stays, read the
  load function's log (Console link on the fact). "Cannot read the switch" means the Lambda interface endpoint is not
  reachable; "Connect failed" means the security group path or the secret.
- **A storm card shows `Injected outside the Lab`.** The load function's reserved concurrency is
  above 0 with no run owning it (a run ended early, or someone changed it by hand). Choose
  **Rollback**: with no run owning the condition the Lab reverts directly.
- **The Business tags card shows `Injected outside the Lab`.** At least one business tag was
  removed by hand. Rollback restores the five tags exactly as CDK created them.
- **No investigation after the alarm fired.** Check the trigger Lambda's log (link in each storm
  card's Walkthrough) for the webhook's answer (`200 Webhook received` is success).
- **My chat is not under Agent tasks.** Expected: the table lists the Agent Space backlog
  (investigations, evaluations, system learning). Chats are per user and live in the operator
  app; the table's "Your chats in the operator app" button opens them.
- **The investigation did not use the skill.** The **Agent tasks** table has a "Skills loaded"
  column. `aws devops-agent list-assets --agent-space-id <id> --asset-type skill` should list
  `aurora-incident-business-context` as `ACTIVE`; re-run the deploy script to re-register it.
- **The Lab asks for a password you lost.** Re-run the deploy script with `-LabPassword` /
  `--lab-password`; only the Lab stack changes.

## Cleanup

```powershell
.\destroy-all.ps1
```

```bash
./destroy-all.sh
```

Switches the load off, removes the Lab, the database stack (the cluster is deleted without a
final snapshot, about 10 minutes), the run-time Lambda log groups and the Agent Space. Not
removed: the CDK bootstrap stack.

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
