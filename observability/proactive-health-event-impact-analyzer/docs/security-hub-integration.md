# Security Hub Findings Integration

This document describes the second ingestion path that feeds AWS Security Hub
findings into the existing investigation pipeline. It is additive: the AWS
Health path is unchanged, and everything downstream of the Event Router (Step
Functions, Investigation Trigger/Callback, OpsCenter Creator, Notifier) is
shared and runs unmodified.

## Prerequisite: enable AWS Security Hub

Security Hub must be **enabled in the account and region** where this stack is
deployed. When enabled, Security Hub emits `Security Hub Findings - Imported`
events to the default EventBridge event bus. No cross-account aggregation is
required for the single-account demo — findings are consumed from the local bus
of the deploying account/region.

## EventBridge rule

A rule (`health-analyzer-securityhub-capture`) is added to the `EventIngestion`
construct, targeting the **same** Event Router Lambda as the Health rule:

```jsonc
{
  "source": ["aws.securityhub"],
  "detail-type": ["Security Hub Findings - Imported"],
  "detail": {
    "findings": {
      "Severity":   { "Label": ["CRITICAL"] },
      "Workflow":   { "Status": ["NEW"] },
      "RecordState": ["ACTIVE"]
    }
  }
}
```

`aws.securityhub` and `aws.health` are disjoint `source` values, so no event is
ever delivered to the Router twice.

## Filter split: EventBridge pattern vs. Event Router

The three leaf filters (`Severity.Label`, `Workflow.Status`, `RecordState`) live
under the `findings[]` array. EventBridge content filtering matches array
elements with **any-element** semantics: a batch is admitted if **any single
finding** satisfies the matchers, and the **whole batch** — including
non-qualifying findings — is delivered to the target.

Consequently the event-pattern filter is only a **coarse batch-admission gate**:
it keeps batches with zero qualifying findings from invoking the Router at all
(cost/noise reduction), but it cannot guarantee that a *given* finding in a
matched batch qualifies.

The **authoritative** gate is therefore re-applied **per finding inside the
Event Router**: a finding starts an investigation only if
`Severity.Label === 'CRITICAL'` **and** `Workflow.Status === 'NEW'` **and**
`RecordState === 'ACTIVE'`. The net behavior — only NEW/ACTIVE/CRITICAL
findings are ever investigated — is enforced in the Lambda regardless of what
the pattern admits.

**Fallback (FR-1a):** because the Router is the authoritative gate, the
in-pattern filter carries no correctness weight. If a nested leaf is ever
rejected at `cdk synth`/deploy, drop only that leaf from the pattern (keeping
`source` + `detail-type` + the accepted leaves); the Router still enforces it.
The only cost is that more batches reach the Router, which then filters them.

## Per-resource aggregation

A single `Security Hub Findings - Imported` event can carry many findings.
Rather than starting one Step Functions execution per finding, the Router
groups qualifying findings by an **aggregation key** — the first entry in
`finding.Resources[]`, falling back to the finding's own `Id` when
`Resources[]` is empty — and starts **one** execution per group. One group →
one investigation, not one finding → one investigation.

The first-resource rule is a convention commonly, though not contractually,
followed by AWS-managed finding generators (the primary affected resource is
usually listed first, with secondary/contextual resources following), not a
documented ASFF guarantee. The design does not depend on it holding
universally: the empty-`Resources[]` fallback, and the fact that
`affectedResources` carries the deduplicated union of every resource across
the whole group (see below), mean a "wrong" first-resource assumption at worst
changes which resource a group is filed under — never whether the
investigating agent sees the full resource context.

Execution names are `securityhub-<sanitized-key>-<timestamp>-<random>`,
truncated to the Step Functions 80-character limit and restricted to the legal
`[0-9A-Za-z-_]` character set. The `securityhub-` prefix (distinct from the
Health path's `health-`) is for observability only; callback correlation uses
the `[INVESTIGATION_ID]` tag, not the execution name.

### Normalized contract mapping

| Contract field | Source | Notes |
|---|---|---|
| `eventId` | `securityhub-aggregate:<aggregationKey>` | Deterministic, stable per aggregation key |
| `service` | `'SecurityHub'` | Constant literal — stable in exec names/titles |
| `eventType` | `Types[0]` → `GeneratorId` → `'SecurityHubFinding'` | From the group's representative finding |
| `category` | `'issue'` | Fixed constant — every finding reaching this point is CRITICAL |
| `region` | event envelope `region` | Not per-resource `Resources[].Region` |
| `availabilityZone` | `null` | Findings are not AZ-scoped |
| `startTime` / `endTime` | `null` | Findings have no maintenance window |
| `status` | `Workflow.Status` → `RecordState` → `'NEW'` | From the representative finding |
| `description` | group size + representative `Title`/`Description` | Mentions "(and N more)" when the group has more than one finding |
| `affectedResources` | deduplicated union of `Resources[].{Id,Tags}` across the group | `resourceId` = ARN; status = workflow status |
| `sourceAccountId` | event envelope `account` | Authoritative for agent-space routing |
| `rawEvent` | the group of findings (array) | Not the whole original batch |
| `ingestedAt` | now (ISO) | |

The `category` here is a severity-derived **intake priority hint**, not a Health
event category; the existing `mapCategoryToPriority` is left unchanged — it is
no longer reachable with `'scheduledChange'` on this path now that only
CRITICAL findings qualify. The final OpsItem/notification priority comes from
the agent's own severities parsed by the Investigation Callback.

## Dedup table

A dedicated DynamoDB table `health-analyzer-securityhub-dedup` (created in the
`EventIngestion` construct, granted only `dynamodb:PutItem` to the Event Router)
de-dups findings:

- **Partition key:** `findingId`. The attribute name is unchanged (no schema
  migration), but it now holds the **aggregation key** — a resource ARN, or
  the fallback finding Id when a finding has no `Resources[]` — rather than
  always a bare ASFF finding Id. One conditional write is attempted per
  aggregate group per invocation, not per finding.
- **Billing:** `PAY_PER_REQUEST`. **PITR:** enabled. Deletion protection +
  `RETAIN` in production, disposable otherwise (mirrors the task-token table).
- **TTL:** `ttl` attribute, 6 hours. Longer than the 1h task-token TTL because
  Security Hub re-imports the same finding repeatedly over a span of hours;
  short enough that a genuinely re-raised finding re-investigates after the
  window.
- **Write:** a conditional `PutItem` with `attribute_not_exists(findingId)`,
  issued **before** `StartExecution`, and only after the per-invocation cap
  (see "Rate limiting" below) confirms this group is not skipped — a
  capped-out group never attempts the write, so it stays eligible to claim its
  key on a later invocation. Concurrent duplicate deliveries resolve
  deterministically via DynamoDB's atomic conditional write: exactly one
  writer wins and starts exactly one execution, and the loser receives
  `ConditionalCheckFailedException` and is skipped (not fatal — logged and the
  loop continues to the next group). Any other DynamoDB error on a qualifying
  group is rethrown, failing the invocation so the async-retry/DLQ path
  engages. The write-before-start order means a crash between the two at worst
  drops one investigation within the window (preferable to a duplicate). A
  successful write logs `Dedup key written for aggregation key: <key> (<n>
  finding(s))` immediately afterward, so a CloudWatch Logs Insights query
  against the Event Router's log group can independently confirm the write
  path executed, without needing to scan the DynamoDB table directly.

### Verifying the dedup write path is live

After any deploy that touches the Event Router, confirm the dedup write path
is actually executing against the **currently deployed** Lambda code — not a
stale physical function left behind by an earlier construct change:

1. **Confirm the table is being written to.** Run
   `aws dynamodb scan --table-name health-analyzer-securityhub-dedup --select COUNT`,
   then trigger (or wait for) a real qualifying finding, and confirm the count
   increments.
2. **Confirm the EventBridge rule target is the current function, not an
   orphan.** Run
   `aws events list-targets-by-rule --rule health-analyzer-securityhub-capture --query "Targets[].Arn"`
   and cross-check the returned function ARN against the current deployment's
   `EventRouter` function — either via the stack's CloudFormation outputs
   (`aws cloudformation describe-stacks --stack-name <stack> --query
   "Stacks[0].Outputs"`) or by resolving the logical id directly
   (`aws cloudformation describe-stack-resource --stack-name <stack>
   --logical-resource-id <EventRouter's logical id> --query
   "StackResourceDetail.PhysicalResourceId"`). A mismatch means the rule is
   still invoking an old physical Lambda whose code predates the current
   source — redeploy before trusting any dedup/aggregation/cap behavior
   described in this document.

### Dedup tradeoffs

Dedup is keyed on the aggregation key, independent of severity and
investigation outcome. For this demo that is acceptable, with these
consequences:

- New CRITICAL findings arriving on an already-dedup-claimed resource within
  the 6h window are skipped — not merged into the already-running or
  already-completed investigation — until the key's TTL expires. A resource
  that keeps accumulating new findings is investigated once per window, not
  once per new finding; this is the intended volume reduction, not an
  oversight.
- A finding evaluated as no-impact/LOW still consumes a 6h dedup key, so a
  re-import within the window that *should* now be investigated (e.g. because
  the resource later joins a monitored workload) is suppressed until expiry.

## Rate limiting

`MAX_INVESTIGATIONS_PER_INVOCATION` (env var on the Event Router, with a
matching `maxInvestigationsPerInvocation` construct prop) caps how many
investigations — i.e. aggregate groups — can be started in a single Router
invocation. It defaults to `10` when unset or unparseable.

The cap is enforced **after** grouping, so it counts investigations, not raw
findings: a batch of many CRITICAL findings all on the same resource is one
investigation and never touches the cap. Once the cap is reached, remaining
groups are skipped entirely — no dedup write is attempted for them, so they
remain fully eligible to start on a later invocation — and a single summary
`console.warn` is emitted for the whole invocation (not one per skipped
group). Hitting the cap is not a failure: the invocation still returns
successfully, and the async-retry/DLQ path never engages for a capped batch.

**Scope and limitation:** the cap bounds investigations **per invocation**
(per EventBridge delivery), not cross-event concurrency. Two overlapping
deliveries processed by two concurrent Lambda invocations can each
independently start up to the cap, so the true ceiling across a burst of
deliveries is `(cap) × (concurrent invocations)`, not a hard global cap.
Reserved concurrency on the Event Router Lambda would tighten this but
changes retry/throttling behavior stack-wide and is not implemented here.

## Prompt signal: `[EVENT_TYPE:securityhub-finding]`

The Investigation Trigger inlines an `[EVENT_TYPE:securityhub-finding]` tag into
the prompt immediately after `[INVESTIGATION_ID:...]`, using the same mechanism
as the existing `[CORRELATION_ID]` / `[INVESTIGATION_ID]` / `[JIRA_CONFIG]`
tags. The tag is emitted solely when the normalized `service` equals
`'SecurityHub'`; for Health events it is absent and the Health prompt is
byte-identical.

The DevOps Agent skill (`devops-agent-skill/SKILL.md`) uses this tag to select
its **Security Hub Finding Impact Assessment** path. That path starts from the
already-known finding resource ARNs (rather than discovering resources),
correlates them with the topology/teams, assesses real impact and blast radius,
and emits the **same** output contract (`## Summary`, `## Key Findings`,
`## Answers to Your Questions`, `## Recommended Actions`,
`## Notification Routing`, `## Jira Tracking`) the Health path uses.
