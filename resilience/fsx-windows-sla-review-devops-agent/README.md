# FSx for Windows SLA Review with AWS DevOps Agent

A file server that answers SMB requests is not the same as a file server that will survive
the year. This demo stands up one Amazon FSx for Windows File Server file system, lets you
mis-configure it in ways that break nothing today, and shows what AWS DevOps Agent says
about it once it knows the FSx for Windows availability model: with the
[`storage-fsx-windows-sla-optimizer`](https://github.com/aws/tools-for-devops-agent/tree/main/skills/storage-fsx-windows-sla-optimizer)
skill from the Agent Tools repository, the agent rates the file system across seven SLA
dimensions and names the fix; without it, the same question gets a list of settings.

## At a glance

| | |
|---|---|
| **Capability shown** | Skill `storage-fsx-windows-sla-optimizer` (Agent Tools repository, `main`), fetched at deploy time, never copied here |
| **Motions** | Chat (the review), Incident RCA (a MISCONFIGURED file system starts an investigation), Evaluation (the weekly run over past investigations) |
| **Environment** | One FSx for Windows file system (Single-AZ 2, 32 GiB SSD, 8 MB/s) joined to a self-managed Active Directory on one EC2 domain controller |
| **Scenarios** | Active Directory credentials rotated, automatic backups disabled, no CloudWatch alarm on the file system |
| **Lab** | Inject, watch, roll back from a CloudFront-hosted control room; every injection auto-reverts |
| **Deploy time** | About 1 hour (the file system alone takes 25-35 minutes) |
| **Running cost** | About $5 per day plus agent time (see [Cost](#cost)) |

## Why this demo is shaped this way

Decisions taken while deriving the demo from the skill (the demo was built capability-first:
skill, then motion, then scenarios, then the smallest environment; the builder was asked at
every fork and the answers are recorded here so the next presenter knows why):

- **All three declared motions.** The skill declares `Chat tasks, Evaluation, Incident RCA`.
  Chat carries the review; Incident RCA is exercised by one scenario whose failure produces an
  alarm; Evaluation consumes the investigations that scenario leaves behind.
- **Self-managed Active Directory on EC2, not AWS Managed Microsoft AD.** The headline scenario
  gives FSx a service-account password nobody rotated in the domain. Managed AD hands FSx no
  password to get wrong, so it cannot demonstrate dimension 2. A t3.medium is also cheaper than
  a managed directory. Demo shortcut: the service account is a Domain Admin; the FSx
  documentation lists the delegated permissions to use in real life.
- **Single-AZ, 32 GiB, 8 MB/s.** The smallest and cheapest file system that can be reviewed.
  Dimension 1 (deployment type) is a Warning in every report on purpose: it is what makes the
  backups scenario bite (on Single-AZ, the backup is the whole recovery plan). The demo is about
  availability posture, not throughput sizing.
- **No client ever mounts the file system, no load generator.** The failures are legible from
  the Lab and the agent's report; a fake user journey would cost credibility with the audience
  that knows FSx. From day 14 the report adds the idle-file-system cost note on its own: the
  environment delivers the cost story without a scenario.
- **Three scenarios, not seven.** Throughput saturation and storage headroom need real SMB load
  and 14 days of CloudWatch history; maintenance window cannot be made to fail; they were dropped
  as not demonstrable. Trend findings on a fresh deployment read "insufficient data": that is the
  skill being honest.
- **Cost and duration accepted** at about $5/day and about an hour, on the condition that both
  are shown to the user: the deploy script prints them before it starts.

## The scenarios

Each scenario states what the agent concludes with the skill and without it (`demonstrates`
in [`lab/scenarios.yaml`](lab/scenarios.yaml), the single source of truth for the engine, the
API and the Lab cards).

| Scenario | What the Lab does | Dimension | With the skill | Without |
|---|---|---|---|---|
| **Active Directory credentials rotated** | `UpdateFileSystem` with a wrong service-account password; after 7-8 minutes of validation the file system goes `MISCONFIGURED`; a canary metric alarms and starts an investigation | 2, Active Directory health | Critical, `ACTIVE_DIRECTORY_INVALID_CREDENTIALS` mapped to a rotated password, the Protected Users caveat, the `AWSSupport-ValidateFSxWindowsADConfig` runbook | "Misconfigured", generic AD connectivity causes |
| **Automatic backups disabled** | `AutomaticBackupRetentionDays` set to 0 | 5, backups (with the Single-AZ baseline) | Warning, no recovery point on a file system whose only recovery path is a backup; rating capped at Medium | May mention backups are off |
| **No CloudWatch alarm on the file system** | The `FreeStorageCapacity` alarm is deleted | 7, alarms and observability | Warning, no `AWS/FSx` alarm scoped to the file system, the exact alarm to create | Alarm coverage is not part of the assessment |

Every injection is one Lambda durable function execution: inject, wait for a rollback, revert.
Click **Rollback** or let the auto-revert run (30 minutes for the credentials scenario, 10 for
the others); either road ends in the same revert, with the page closed or not.

## Architecture

Four CDK stacks (diagram and data flows in [ARCHITECTURE.md](ARCHITECTURE.md)):

| Stack | What it holds |
|---|---|
| `FsxSlaReviewAgentSpace-{agent-region}` | The Agent Space: IAM roles, operator app, AWS association, eventChannel webhook (shared `DevOpsAgentSpace` construct) |
| `FsxSlaReviewDirectory-{region}` | VPC (one AZ, one NAT gateway), the Windows Server domain controller that promotes `corp.example.com` from its user data, the service-account secret |
| `FsxSlaReviewFileSystem-{region}` | The file system, the `FreeStorageCapacity` alarm, the lifecycle canary and its alarm, the alarm → webhook trigger chain (shared `AlarmTrigger` construct). Main stack |
| `FsxSlaReviewLab-{region}` | The Lab: durable engine + API (shared `LabEngine` construct) behind a Lambda function URL, the site on S3 + CloudFront with HTTP Basic authentication at the edge |

## Prerequisites

- An AWS account with administrator access, in a region where
  [AWS DevOps Agent is available](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-supported-regions.html)
  (or set `DEVOPS_AGENT_REGION` to one that is; the file system deploys where your CLI points)
- AWS CLI 2.34.20+, Node.js 20+, Python 3.12+ (the Lab Lambda bundle is pip-installed at synth time)
- Credentials configured your usual way (`aws sso login --profile <name>` and `AWS_PROFILE`, an
  IAM role, or `aws configure`); the deploy script verifies them with `aws sts get-caller-identity`

## Quick start

```powershell
# Windows / PowerShell
cd resilience/fsx-windows-sla-review-devops-agent
.\deploy-all.ps1
```

```bash
# macOS / Linux
cd resilience/fsx-windows-sla-review-devops-agent
./deploy-all.sh
```

The script prints the cost and duration first, then:

1. deploys the Agent Space and reads its outputs (the webhook secret stays in Secrets Manager);
2. deploys the VPC and the domain controller, and **waits** until the controller reports the
   forest and the service account ready (an SSM parameter written by its user data, ~10 minutes);
3. deploys the file system (25-35 minutes; FSx joins the domain at creation and fails after
   30 minutes if the domain is not there, hence the wait);
4. deploys the Lab and publishes its site;
5. fetches the skill at its ref with `shared/devops-agent/agent-tools/deploy-skill.*`, packages
   it, and registers it in the Agent Space through the Asset API (agent type Generic, so Chat,
   Incident RCA and Evaluation all load it). Nothing to upload or paste in the console.

It ends with the Lab URL, the sign-in (user `presenter`, a generated password; pass
`-LabPassword` / `--lab-password` to choose one), the Agent Space console URL and the chat
prompt. Re-running the script is safe: every step is idempotent and it resumes where it stopped
(the skill is updated in place).

## Run the demo

1. Open the Lab, sign in. The **Environment** panel shows the file system (lifecycle, deployment,
   domain controller). Every card shows **Failure injection: Not injected**.
2. Ask the baseline in the Agent Space **Chat**: *Review all my FSx for Windows file systems in
   `<region>` for SLA readiness.* Expect one Warning (Single-AZ) and passes elsewhere; the trend
   dimensions say insufficient data on a fresh deployment.
3. **Automatic backups disabled**: Inject, ask again. Backups flip to Warning and the rating is
   capped at Medium because of the Single-AZ baseline. Rollback, ask once more, watch it pass.
4. **No CloudWatch alarm**: Inject, ask again. Dimension 7 flips to Warning with the alarm to
   create. Rollback.
5. **Active Directory credentials rotated**: Inject. The **Lifecycle** fact reads `UPDATING`
   while FSx validates the credentials against the domain (7-8 minutes), then `MISCONFIGURED`
   with the `ACTIVE_DIRECTORY_INVALID_CREDENTIALS` detail; the canary alarm fires within two more
   minutes and the **Agent tasks** table shows the investigation. Open it: the cause is a rotated
   password, not a network problem. Rollback (or the 30-minute auto-revert): the correct password
   is restored and the file system returns to `AVAILABLE` in a few minutes (the card says
   Reverting). Start this one first in a session; the other two take seconds.
6. Once an investigation exists, run an **Evaluation** from the Agent Space and read the
   Improvements page: it consumes the investigation the scenario left behind.

Talk track lines, incident chains and customer impact are on each card (**Walkthrough**).

## The Lab

The Lab is the presenter's control room and this demo's whole site: there is no application,
because the failures need none to be legible. It is built on the shared Lab mechanism
([`shared/devops-agent/README.md`](../../shared/devops-agent/README.md)): the durable engine
runs each injection as one execution (inject → wait for a rollback → revert); "is it injected"
is read from the live file system and its alarms, never from a stored flag; one scenario runs
at a time (a second inject gets HTTP 409); the agent's tasks and spend come from the DevOps
Agent data plane.

Authentication is HTTP Basic at the CloudFront edge (a CloudFront Function checks every request,
site and API alike, then drops the header); the Lab API is a Lambda function URL that only
CloudFront can call (origin access control). The credentials are generated by the deploy
script and printed once; they are embedded in the CloudFront Function, so anyone who can read
the stack template can read them. That is acceptable for a demo control room and no worse than
a printed demo password; it is not a pattern for production.

### Shared with the repository

The demo does not deploy from this folder alone; deploy from a full clone.

| File | Used by | For |
|---|---|---|
| `shared/scripts/check-prerequisites.ps1` / `.sh` | `deploy-all.*`, `destroy-all.*` | Tooling, credentials, region, DevOps Agent availability |
| `shared/scripts/deploy-cdk.ps1` / `.sh` | `deploy-all.*` | CDK bootstrap, dependencies, one stack per call |
| `shared/devops-agent/agent-tools/deploy-skill.ps1` / `.sh` | `deploy-all.*` | Fetches the skill at its ref, builds the zip, registers it in the Agent Space (Asset API) |
| `shared/devops-agent/agent-space/cdk/agent-space.ts` + `lambda/webhook-provisioner/` | `cdk/lib/agent-space-stack.ts` | `DevOpsAgentSpace`: roles, Agent Space, operator app, AWS association, webhook |
| `shared/devops-agent/agent-space/cdk/alarm-trigger.ts` + `lambda/alarm-trigger/` | `cdk/lib/file-system-stack.ts` | `AlarmTrigger`: SNS → Lambda → HMAC-signed incident on the webhook |
| `shared/devops-agent/lab/cdk/lab-engine.ts` | `cdk/lib/lab-stack.ts` | `LabEngine`: the engine and API functions, their role, the `live` alias, the code bundle |
| `shared/devops-agent/lab/lambda/engine.py`, `devops_agent.py`, `requirements.txt` | `lab/engine_main.py`, `lab/api.py` | The durable engine and its control plane; agent tasks and spend |
| `shared/utils/aws-utils.ts` | `cdk/bin/app.ts` | Region detection |

## Project structure

```
├── deploy-all.ps1 / .sh          # One-command deployment (prints cost and duration first)
├── destroy-all.ps1 / .sh         # Removes everything, Agent Space included
├── validation.yaml               # Deploy / verify / destroy contract for the repository's validation workflow
├── cdk/
│   ├── bin/app.ts                # Four stacks, region-suffixed; tracking on the file system stack
│   ├── lib/agent-space-stack.ts  # Shared DevOpsAgentSpace + outputs
│   ├── lib/directory-stack.ts    # VPC, domain controller (user data promotes the forest), service-account secret
│   ├── lib/file-system-stack.ts  # File system, alarms, lifecycle canary, shared AlarmTrigger
│   ├── lib/lab-stack.ts          # Shared LabEngine, function URL, S3 + CloudFront, Basic Auth function
│   ├── lambda/lifecycle-canary/  # Publishes FileSystemMisconfigured from the lifecycle every minute
│   └── test/stacks.test.ts       # The promises above, checked on the synthesized templates
├── lab/
│   ├── scenarios.yaml            # The three scenarios, the skill reference, the presenter notes
│   ├── handlers.py               # inject / revert / probe per scenario (FSx and CloudWatch APIs)
│   ├── api.py                    # Lab API routes (function URL events)
│   ├── engine_main.py            # Durable function entry point: shared engine + these handlers
│   ├── facts.py, scenarios.py    # Labelled facts for the UI; YAML loader
│   └── tests/test_lab.py         # Engine on both roads (recorder handlers), handlers, API contract, YAML consistency
├── frontend/                     # The Lab site (React, Vite, Cloudscape), adapted from the EKS demo's Lab
└── ARCHITECTURE.md               # Stacks, data flows, security model
```

## Cost

Approximate, `us-east-1` list prices, while the demo is deployed.

| Resource | Specification | Per day | Per month |
|---|---|---|---|
| EC2 domain controller | t3.medium, Windows Server 2022, 30 GiB gp3 | $1.50 | $45 |
| NAT gateway | 1, minimal data | $1.10 | $33 |
| FSx for Windows | Single-AZ 2, 32 GiB SSD, 8 MB/s, 7-day backups | $0.75 | $23 |
| CloudFront, S3, Lambda, CloudWatch, Secrets Manager | Demo traffic, one canary a minute, two alarms | $0.40 | $12 |
| **Infrastructure** | | **~$3.75** | **~$113** |
| AWS DevOps Agent | Per agent-second, list price $0.0083/s (about $0.50 per investigation, $0.10 per chat review) | usage | usage |

The Lab's **Agent spend** panel shows the month's agent time. The deploy script prints the
estimate before it starts; `destroy-all` removes everything.

## Troubleshooting

- **The deploy script waits forever for the domain controller.** Open a Session Manager shell on
  the instance (`DomainControllerInstanceId` output of the Directory stack; there is no key pair)
  and read `C:\dc-setup.log`. The user data runs on every boot and is idempotent; fixing and
  rebooting re-runs it. Re-run the deploy script afterwards; it resumes.
- **The file system stack fails after 30 minutes.** FSx could not join `corp.example.com`. Run
  the `AWSSupport-ValidateFSxWindowsADConfig` Systems Manager automation with the
  `ServiceAccountSecretArn` output (the secret is already in the `{"username","password"}` shape
  it expects), fix what it reports, re-run the deploy script.
- **Lifecycle stays `UPDATING` after a rollback.** FSx applies a self-managed AD update in a few
  minutes; the engine's Revert step waits for `AVAILABLE` (up to 12 minutes) before the run ends.
- **The credentials scenario shows `Injected outside the Lab`.** Someone changed the file system
  by hand, or a run ended while FSx was still updating. Click **Rollback**: with no run owning the
  condition the Lab reverts directly.
- **The Lab asks for a password you lost.** Re-run the deploy script with `-LabPassword` /
  `--lab-password`; only the Lab stack changes.
- **No investigation after the alarm fired.** Check the trigger Lambda's log (link in each card's
  Walkthrough) for the webhook's answer (`200 Webhook received` is success).
- **The investigation did not use the skill.** The Lab's **Agent tasks** table has a "Skills
  loaded" column. `aws devops-agent list-assets --agent-space-id <id> --asset-type skill` should
  list `storage-fsx-windows-sla-optimizer` as `ACTIVE`; re-run the deploy script to re-register it.

## Cleanup

```powershell
.\destroy-all.ps1
```

```bash
./destroy-all.sh
```

Removes the four stacks, the Agent Space included; deleting the file system and its automatic
backups takes about 15 minutes. Not removed: the CDK bootstrap stack.

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
