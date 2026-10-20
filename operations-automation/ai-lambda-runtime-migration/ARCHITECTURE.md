# Architecture — AI Lambda Runtime Migration Assistant

This document describes the architecture that is built today: an org-wide
discovery and assessment pipeline for deprecated-runtime AWS Lambda functions,
driven by a Lambda durable function and Amazon Nova, presented through a React
dashboard. The optional Transform build plane runs the actual code migration via
AWS Transform in CodeBuild and ends by pushing an upgrade branch to the source
repository (see "Migration" below).

## Overview

```
                  ┌──────────────┐      ID token (JWT)      ┌───────────────┐
  Operator  ───►  │  CloudFront  │ ─────────────────────►   │  HTTP API     │
  (browser)       │  + S3 (SPA)  │   Authorization: Bearer  │  (API GW)     │
                  └──────────────┘                          └───────┬───────┘
                         ▲                                          │ Cognito JWT
                         │ Cognito ID token                         │ authorizer
                         │                                          ▼
                  ┌──────────────┐                          ┌───────────────┐
                  │  Cognito     │                          │  API Lambda   │
                  │  User Pool   │                          │ (start/adopt  │
                  └──────────────┘                          │  + read)      │
                                                            └───────┬───────┘
                                                                    │ start / poll
                                                                    ▼
                                           ┌──────────────────────────────────────┐
                                           │  Durable pipeline Lambda (5 steps)    │
                                           │  determine → support tier → discover  │
                                           │  → assess → summarize                 │
                                           └───────┬───────────────────────┬───────┘
                                                   │ hub creds             │ assume spoke role
                                                   ▼                       ▼
                                           ┌───────────────┐       ┌───────────────┐
                                           │  Hub account  │       │ Member account│
                                           │  (TA, Lambda, │       │ (read-only    │
                                           │  CloudWatch)  │       │  spoke role)  │
                                           └───────────────┘       └───────────────┘
                                                   │
                                                   ▼
                                    ┌───────────────────────────┐
                                    │  DynamoDB inventory + S3   │
                                    │  Amazon Nova (Bedrock)     │
                                    └───────────────────────────┘
```

The browser holds **no AWS credentials**. It authenticates against a Cognito
User Pool and calls the HTTP API with the Cognito **ID token**; an API Gateway
Cognito JWT authorizer validates every request. There is no Identity Pool and
no SigV4 signing in the browser. The API Lambda only starts (or re-attaches to)
the durable execution and reads the inventory — all discovery and assessment
happens server-side in the durable pipeline Lambda.

## The 5-step durable pipeline

A single **Refresh** is one [Lambda durable function](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html)
execution with five checkpointed steps. Each step is a `@durable_step`, so a
mid-run failure resumes from the last checkpoint rather than restarting. All
non-determinism (clock, uuid, boto3/Bedrock/DynamoDB calls) lives **inside**
steps; code outside steps is pure transformation of bounded step outputs. The
full function list is written to DynamoDB inside a step and never crosses a
checkpoint.

The step labels below are the exact strings the dashboard renders from the
execution history:

1. **Determine the accounts to scan** — `resolve_accounts()` in
   `backend/org_accounts.py` turns the refresh scope into concrete accounts.
   Explicit accounts in the scope win verbatim; otherwise AWS Organizations is
   queried for the active member accounts. The hub is always first and always
   scanned with its own credentials (empty account id); member accounts keep
   their real id, which triggers the spoke-role assume path.
2. **Check each account's AWS Support tier** — one durable unit per account
   (`backend/support.py`). Trusted Advisor needs Business/Enterprise Support, so
   each account's tier is inferred first as the ceiling of the case severities
   it can open (`support:DescribeSeverityLevels`). Accounts on
   Basic/Developer/unknown tiers are marked not TA-eligible but are **not
   dropped** — their tier and reason flow forward.
3. **Discover deprecated-runtime functions via Trusted Advisor** — for each
   TA-eligible account × region, query TA check `L4dfs2Q4C5`, enrich each hit
   with Lambda API configuration and CloudWatch metrics, compute a rule-based
   priority score and let Nova (constrained decoding / tool use) adjust it by at
   most ±15. Container-image functions are skipped (cannot migrate via code).
4. **Assess migration complexity and risks** — one durable unit per function.
   Download the deployment package (source plus manifests), send it to Nova for an
   AI assessment (complexity, deprecated APIs, breaking changes, dependency
   issues, migration risks), and store the result.
5. **Summarize findings and update the inventory** — assemble the bounded run
   summary: counts, per-account tiers, and `accounts_not_checked` (the accounts
   skipped in step 2). Finalize the inventory.

### Per-account / per-function isolation

Each account (support-tier step) and each function (assess step) is its own
checkpointed unit. A failure on one account or one function fails only that unit
— the run records it (`accounts_failed`, failed cells) and continues. One
unreachable account never fails the whole run.

### Per-account Support-tier gating and "not checked" reporting

Because Trusted Advisor requires Business/Enterprise Support **per account**, an
account on Basic/Developer Support cannot be scanned. Rather than silently
omitting such accounts (which would read as "nothing found"), the summarize step
emits `accounts_not_checked`, and the dashboard shows it as a **counter** on the
My Functions hero — `N accounts not checked (Basic/Developer tier)` — never a
list, because the list could be very large. An empty scan therefore reads as
"not checked" and not as "no deprecated functions".

## Multi-account: hub-and-spoke, decided at runtime

Single-account is the default outcome, but it is decided at **runtime**, not by
a deploy-time flag. Every Refresh attempts `organizations:ListAccounts`:

- **Standalone account / not the management or delegated-admin account**
  (`AccessDeniedException`, `AWSOrganizationsNotInUseException`) or a
  **single-member org** → degrade cleanly to the hub alone, with no surfaced
  error (`source: "hub"`).
- **Organization with members** → fan out across the active members
  (`source: "organization"`); suspended/closed accounts are excluded.
- **Any other failure** (throttling, a genuine service error) → still degrade to
  the hub, but keep a one-line reported error so real misconfiguration stays
  visible.

This keeps the scan durable across redeploys: a plain `deploy-all` already
leaves a pipeline that fans out to whatever org it can read, with no
`MULTI_ACCOUNT` env var to drift out of sync.

### Single source of truth for scan permissions

`cdk/lib/scan-permissions.ts` is the single source of the read/scan action list
(`DISCOVER_READ_ACTIONS`) and the role names. Two roles must grant exactly the
same permissions and are both built from it, so they cannot drift:

- the **hub pipeline role** (scans the hub account itself), and
- the **read-only spoke role** (`LambdaRuntimeMigrationScanRole`), assumed by the
  hub in each member account.

The hub role carries the `organizations:*` read grants and the
`sts:AssumeRole` on the spoke role **unconditionally** — they are harmless in
single-account (no org to list, no spoke roles to assume).

### Spoke trust

The spoke role trusts the hub **account root** with an `aws:PrincipalArn`
condition pinned to the region-suffixed hub role name (`hubPipelineRoleName()`),
with an optional `sts:ExternalId` confused-deputy guard. Pinning the name (not a
specific role ARN) keeps the trust valid when the hub role is recreated.

## CDK stacks

All stack IDs are region-suffixed so global resources never collide across
regions. Solution-adoption tracking lives **only** on the Pipeline stack.

| Stack | Purpose | Key resources | Depends on |
|-------|---------|---------------|------------|
| `LambdaRuntimeMigrationData-{region}` | State storage | DynamoDB inventory table (`item_key` PK), S3 artifacts bucket | — |
| `LambdaRuntimeMigrationAuth-{region}` | Authentication | Cognito User Pool + app client | — |
| `LambdaRuntimeMigrationPipeline-{region}` | 5-step Discover + Assess | Durable pipeline Lambda, API Lambda, hub pipeline role (always org-read + spoke-assume) | Data |
| `LambdaRuntimeMigrationApi-{region}` | HTTP API | API Gateway HTTP API + Cognito JWT authorizer | Auth, Pipeline |
| `LambdaRuntimeMigrationFrontend-{region}` | Dashboard | CloudFront distribution + S3 website bucket | Auth, Api |

Two **optional** stacks support the opt-in spoke rollout and are only
synthesized when their context keys are present, so the default single-account
synth never depends on them (both use `BootstraplessSynthesizer` — no
`cdk bootstrap` in member accounts):

- `LambdaRuntimeMigrationSpoke-{region}` — one read-only role, deployed in a
  member account pointing at the hub (`--context hubAccountId=...`).
- `LambdaRuntimeMigrationOrg-{region}` — a service-managed, auto-deploying
  `CfnStackSet` that rolls the spoke role out to the org root/OUs (hub excluded),
  deployed from the hub (`--context orgTargets=...`).

Neither optional stack carries a tracking tag — the Pipeline stack is the
demo's single tracked stack.

## Authentication flow (browser holds no credentials)

```
Cognito User Pool  →  ID token  →  HTTP API (Cognito JWT authorizer)  →  API Lambda
```

The React SPA signs in against the Cognito User Pool and attaches the ID token
to every HTTP API call. The API Gateway JWT authorizer validates the token
before the request reaches the API Lambda. No AWS credentials, Identity Pool, or
SigV4 signing exist in the browser.

## Migration: AWS Transform, the flow ends at a pushed branch

The migration engine is [AWS Transform](https://aws.amazon.com/transform/).
`additionalPlanContext` is written as directives, the way AWS documents the
field for managed transformations: a scope directive (upgrade this one function,
named with its handler and runtime, to the resolved target; leave the rest of
the repository untouched), for Python only a short paragraph on the Lambda
handler and template conventions a generic language upgrade cannot infer, then
the non-empty assessment findings (deprecated APIs, breaking changes, dependency
issues, migration risks) as an advisory paragraph. The target is the assessed
one when it is valid for the family and newer than the source runtime, else the
family default (`nodejs24.x`, `dotnet10`, `python3.13`). The
optional Transform build plane (`--context transform=true`) adds a second Lambda
durable function (`backend/migrate.py`), a CodeBuild project running the `atx`
CLI and an EventBridge-triggered completion handler (`backend/build_complete.py`).

One **Migrate** click is one batch durable execution with three steps per
function: `prepare-batch` -> `resolve-<i>` -> `transform-<i>`. Resolve reads the
repository (and optional branch) tag, verifies both exist in CodeCommit and
writes the `mig#` item (`QUEUED`); a function that cannot be resolved is
`REJECTED` with a code and never reaches a build. Transform is a durable
`wait_for_callback`: its submitter renders the transformation config to S3 and
starts the CodeBuild job; the build clones the repository, runs AWS Transform,
and **pushes the upgrade branch** `lambda-migration/<target>-<job>` when the
transformation produced a diff. The completion handler reads the build's
`result.json`, writes the terminal status on the `mig#` item (`BRANCH_READY`
with the branch and diff summary, `NO_CHANGES`, or `FAILED` with
`TRANSFORM_FAILED` / `PUSH_FAILED`) and completes the callback. A `settle-<i>`
step runs only when the orchestrator must write the outcome itself (callback
timed out, submitter failed).

The pushed branch is where the flow ends. `main` is never written, no pull
request is opened and no Lambda function is modified; the team opens the pull
request, reviews and merges in the repository host. Once the merged code is
deployed, Trusted Advisor stops reporting the function and the next scan infers
`MIGRATED` on the function row. Every `mig#` write is a field-scoped, guarded
`update_item`, so a build from an earlier batch can never clobber an item a later
batch re-queued.

The dashboard shows the batch as a persistent, collapsible **Migration steps**
card (re-attached on page load from `GET /migrate-runs`, refreshed every 30 s
while a batch runs), links every **Branch ready** row to its branch in the
CodeCommit console, lists one Activity-log line per batch, and explains every
step, status and code on the **How it works** page.
