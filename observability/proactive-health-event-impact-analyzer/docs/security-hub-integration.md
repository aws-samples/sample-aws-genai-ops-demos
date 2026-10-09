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
      "Severity":   { "Label": ["HIGH", "CRITICAL"] },
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
`Severity.Label ∈ {HIGH, CRITICAL}` **and** `Workflow.Status === 'NEW'` **and**
`RecordState === 'ACTIVE'`. The net behavior — only NEW/ACTIVE/HIGH/CRITICAL
findings are ever investigated — is enforced in the Lambda regardless of what
the pattern admits.

**Fallback (FR-1a):** because the Router is the authoritative gate, the
in-pattern filter carries no correctness weight. If a nested leaf is ever
rejected at `cdk synth`/deploy, drop only that leaf from the pattern (keeping
`source` + `detail-type` + the accepted leaves); the Router still enforces it.
The only cost is that more batches reach the Router, which then filters them.

## Per-finding fan-out

A single `Security Hub Findings - Imported` event can carry many findings. The
Router iterates `detail.findings[]` and, for each qualifying finding, starts its
**own** Step Functions execution carrying the same normalized contract the
Health path produces. One finding → one investigation.

Execution names are `securityhub-<sanitized-id>-<timestamp>-<random>`, truncated
to the Step Functions 80-character limit and restricted to the legal
`[0-9A-Za-z-_]` character set. The `securityhub-` prefix (distinct from the
Health path's `health-`) is for observability only; callback correlation uses
the `[INVESTIGATION_ID]` tag, not the execution name.

### Normalized contract mapping

| Contract field | Source | Notes |
|---|---|---|
| `eventId` | `finding.Id` | ASFF Id (an ARN); the correlation key |
| `service` | `'SecurityHub'` | Constant literal — stable in exec names/titles |
| `eventType` | `Types[0]` → `GeneratorId` → `'SecurityHubFinding'` | First available |
| `category` | severity-derived | `CRITICAL`→`issue`, `HIGH`→`scheduledChange` (intake hint only) |
| `region` | event envelope `region` | Not per-resource `Resources[].Region` |
| `availabilityZone` | `null` | Findings are not AZ-scoped |
| `startTime` / `endTime` | `null` | Findings have no maintenance window |
| `status` | `Workflow.Status` → `RecordState` → `'NEW'` | Human-readable |
| `description` | `Title`. `Description` | Non-empty, with fallbacks |
| `affectedResources` | `Resources[].{Id,Tags}` | `resourceId` = ARN; status = workflow status |
| `sourceAccountId` | event envelope `account` | Authoritative for agent-space routing |
| `rawEvent` | the single finding | Not the whole batch |
| `ingestedAt` | now (ISO) | |

The `category` here is a severity-derived **intake priority hint**, not a Health
event category; the existing `mapCategoryToPriority` is left unchanged. The
final OpsItem/notification priority comes from the agent's own severities parsed
by the Investigation Callback.

## Dedup table

A dedicated DynamoDB table `health-analyzer-securityhub-dedup` (created in the
`EventIngestion` construct, granted only `dynamodb:PutItem` to the Event Router)
de-dups findings:

- **Partition key:** `findingId` (= ASFF `Id`).
- **Billing:** `PAY_PER_REQUEST`. **PITR:** enabled. Deletion protection +
  `RETAIN` in production, disposable otherwise (mirrors the task-token table).
- **TTL:** `ttl` attribute, 6 hours. Longer than the 1h task-token TTL because
  Security Hub re-imports the same finding repeatedly over a span of hours;
  short enough that a genuinely re-raised finding re-investigates after the
  window.
- **Write:** a conditional `PutItem` with `attribute_not_exists(findingId)`,
  issued **before** `StartExecution`. Concurrent duplicate deliveries resolve
  deterministically — exactly one writer wins and starts exactly one execution.
  A `ConditionalCheckFailedException` means the finding is already in flight and
  is skipped. The write-before-start order means a crash between the two at
  worst drops one investigation within the window (preferable to a duplicate).

### Dedup tradeoffs

Dedup is keyed on `findingId` only, independent of severity and investigation
outcome. For this demo that is acceptable, with two consequences:

- A finding that escalates HIGH→CRITICAL within the 6h window is not
  re-investigated until the key expires.
- A finding evaluated as no-impact/LOW still consumes a 6h dedup key, so a
  re-import within the window that *should* now be investigated (e.g. because
  the resource later joins a monitored workload) is suppressed until expiry.

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
