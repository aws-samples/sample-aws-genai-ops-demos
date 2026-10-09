# Requirements — Security Hub Findings Ingestion

> Feature branch / worktree: `c:\Users\umancini\sviluppi-new\sample-aws-genai-ops-demos\.worktrees\securityhub-ingestion\observability\proactive-health-event-impact-analyzer`
> All file paths below are absolute under this worktree. Do NOT edit the main checkout.

## Summary

Extend the existing "Proactive Health Event Impact Analyzer" (AWS CDK / TypeScript) so it ingests
**AWS Security Hub findings** delivered to EventBridge and runs the **same** AWS DevOps Agent
investigation pipeline on them. Security Hub findings arrive as EventBridge events (`source:
aws.securityhub`, `detail-type: Security Hub Findings - Imported`) in ASFF format; each finding
already names its impacted resource ARNs and a severity label. The investigation must correlate those
ARNs with the application topology/teams, assess real impact, and reuse the existing OpsCenter and
notification behavior.

This is **Option A** (Security Hub findings via EventBridge). It is a settled design decision, not an
open question. **Option B (Security Bulletins / RSS polling) is explicitly out of scope.**

The guiding constraint is **reuse, not duplication**: a new EventBridge rule feeds the *existing*
Event Router Lambda (extended to recognize a new event type), which starts the *existing* Step
Functions state machine, which drives the *existing* Investigation Trigger / Callback / OpsCenter
Creator / Notifier Lambdas. The existing AWS Health path must keep working unchanged.

### Verified existing pipeline (read from source)

1. `infrastructure/cdk/lib/constructs/event-ingestion.ts` — EventBridge rule `health-event-analyzer-capture`
   matches `source: aws.health`, `detail-type: ['AWS Health Event', 'AWS Health Abuse Event']`, targets the
   Event Router Lambda (`retryAttempts: 185`, `maxEventAge: 24h`). Event Router has a DLQ + DLQ alarm.
2. `infrastructure/cdk/lambda/event-router/index.ts` — normalizes the Health event into a `workflowInput`
   object and calls `StartExecutionCommand` on the state machine.
3. `infrastructure/cdk/lib/constructs/investigation-workflow.ts` — Step Functions `health-event-impact-analyzer`:
   `TriggerInvestigation` (Wait-for-Task-Token) → `HasFindings?` Choice → `CreateOpsItem` → `SendNotifications`.
   Task-token table `health-analyzer-task-tokens` (PK `investigationId`, TTL `ttl`, PAY_PER_REQUEST, PITR).
   Completion rule `health-analyzer-devops-agent-completion` matches `source: aws.aidevops`.
4. `infrastructure/cdk/lambda/investigation-trigger/index.ts` — builds the DevOps Agent webhook payload and
   prompt, stores the task token in DynamoDB keyed by `incidentId`.
5. `infrastructure/cdk/lambda/investigation-callback/index.ts` — correlates the `aws.aidevops` completion event
   back to the token, parses the agent's markdown, and calls `SendTaskSuccess`/`SendTaskFailure`.

---

## User Stories / Goals

- **US-1** — As a security operations engineer, I want HIGH/CRITICAL Security Hub findings to automatically
  trigger a DevOps Agent investigation, so that I learn which of my workloads are actually impacted and which
  teams must act, without manually triaging raw ASFF.
- **US-2** — As the platform owner, I want Security Hub findings to flow through the *existing* investigation /
  OpsCenter / notification pipeline, so there is one operational path to maintain, not two parallel ones.
- **US-3** — As an on-call responder, I want each distinct finding investigated and notified on its own merits
  (per-finding impact and routing), rather than a single blended summary of an entire batch.
- **US-4** — As an operator, I do not want the same finding re-investigated every time Security Hub re-imports
  it (findings re-import on every update), so repeated investigations and duplicate OpsItems/notifications are
  suppressed.
- **US-5** — As a maintainer, I want the existing AWS Health behavior and the DevOps Agent skill's output
  contract untouched, so the change is additive and low-risk.

---

## Functional Requirements

### FR-1 — New EventBridge ingestion rule for Security Hub findings
A new EventBridge rule (in `event-ingestion.ts`, alongside `HealthEventRule`) MUST match:
- `source`: `aws.securityhub`
- `detail-type`: `Security Hub Findings - Imported`
- In-pattern content filtering on the finding fields where EventBridge supports it:
  - `detail.findings.Workflow.Status` = `NEW`
  - `detail.findings.RecordState` = `ACTIVE`
  - `detail.findings.Severity.Label` ∈ { `HIGH`, `CRITICAL` }

The rule MUST target the **existing** Event Router Lambda (same `targets.LambdaFunction` style, same
`retryAttempts`/`maxEventAge` convention used by `HealthEventRule`).

**OPEN ITEM / verification required (FR-1a):** `detail.findings` is a JSON **array** in the EventBridge
envelope. EventBridge content filtering against fields nested under array elements is not guaranteed for every
field. The implementer MUST confirm, at build/deploy time, which of the three filters above are expressible in
the event pattern. **Any field that cannot be filtered in-pattern MUST be re-filtered inside the Event Router
Lambda** (see FR-4), and the chosen split MUST be documented in code comments and in the docs (FR-11). The
severity + status + record-state gating MUST be enforced *somewhere* regardless of where the filter lives; the
net behavior (only NEW/ACTIVE/HIGH/CRITICAL findings investigated) is the hard requirement.

### FR-2 — No duplicate ingestion of Health events
The new rule MUST NOT alter, widen, or overlap `HealthEventRule`. Security Hub events (`aws.securityhub`) and
Health events (`aws.health`) are disjoint by `source`, so no event may be delivered to the Event Router twice.

### FR-3 — Event Router recognizes a new `securityhub-finding` event type
`infrastructure/cdk/lambda/event-router/index.ts` MUST be extended to dispatch on event `source` (or
`detail-type`) and branch:
- `aws.health` → existing Health normalization (unchanged behavior).
- `aws.securityhub` → new Security Hub normalization path.

The existing Health code path MUST remain behaviorally identical (same `workflowInput` shape, same execution
name prefix `health-...`). The branch MUST be clearly scoped and commented.

### FR-4 — Per-finding fan-out, filtering, and normalization in the Event Router
A single `Security Hub Findings - Imported` event CAN contain **multiple findings** (`detail.findings[]`). The
Event Router MUST, per the agreed design, **prefer one investigation per finding**:
- Iterate `detail.findings[]`.
- Apply (or re-apply, per FR-1a) the severity/status/record-state filter per finding; skip findings that do not
  qualify.
- Apply dedup per finding `Id` (see FR-6); skip findings already in-flight/recently investigated.
- For each surviving finding, start **one** Step Functions execution with a normalized payload (FR-5).

The batching decision (one execution per finding, not one per EventBridge event) MUST be documented in a code
comment and in the docs. If zero findings survive filtering/dedup, the Router MUST complete successfully
without starting any execution (no error, no DLQ).

**Execution-name uniqueness:** each started execution MUST have a unique name. Use a prefix distinct from the
Health path (e.g. `securityhub-...`) plus the finding `Id` (sanitized to the Step Functions name charset) or a
hash, plus a time/random component, so concurrent re-imports of different findings never collide.

### FR-5 — Normalized internal event contract (the Step Functions / Investigation-Trigger input)
The Security Hub path MUST emit a `workflowInput` that satisfies the **exact** field contract the downstream
components already consume. The Investigation Trigger (`investigation-trigger/index.ts`, `interface
HealthEvent`) reads these fields and will break if any are missing or renamed:

| Field | Type | Health path source | Security Hub path mapping (required) |
|-------|------|--------------------|--------------------------------------|
| `eventId` | string | `detail.eventArn` | finding `Id` (ASFF `detail.findings[].Id`) — the correlation key; MUST be unique per finding |
| `service` | string | `detail.service` | derived label, e.g. `SecurityHub` (or the finding's `ProductName` / resource service) |
| `eventType` | string | `detail.eventTypeCode` | finding `Types[0]` or `GeneratorId` (a stable type descriptor) |
| `category` | string | `detail.eventTypeCategory` | a value that maps to a sane priority (see FR-5a) |
| `region` | string | `event.region`/`detail.region` | `event.region` (EventBridge envelope) |
| `availabilityZone` | string \| null | `detail.availabilityZone` | `null` (ASFF findings are not AZ-scoped) |
| `startTime` | string \| null | `detail.startTime` | finding `CreatedAt` / `FirstObservedAt` (or `null`) |
| `endTime` | string \| null | `detail.endTime` | `null` |
| `status` | string | `detail.statusCode` | finding `Workflow.Status` / `RecordState` (e.g. `NEW`/`ACTIVE`) |
| `description` | string | `extractDescription(...)` | finding `Title` + `Description` (concise, non-empty) |
| `affectedResources` | `Array<{resourceId, tags, status}>` | mapped from `affectedEntities` | mapped from `detail.findings[].Resources[]`: `resourceId` = `Resources[].Id` (ARN), `tags` = `Resources[].Tags` (or `{}`), `status` = finding `Workflow.Status` or a constant |
| `sourceAccountId` | string | `event.account` | `event.account` (preserves multi-account agent-space routing) |
| `rawEvent` | object | the full event | the full EventBridge event (or the single finding — document the choice) |
| `ingestedAt` | string (ISO) | `new Date().toISOString()` | same |

Rationale: the Investigation Trigger maps `affectedResources[].resourceId` into the prompt's "Affected
Resources" block and the webhook payload's `data.healthEvent.affectedResources`; the Callback correlates on
`eventId` via the `[INVESTIGATION_ID]`/`[CORRELATION_ID]` tags the Trigger inlines. Any deviation from this
contract breaks correlation or impact extraction.

**FR-5a — Category→priority:** `investigation-trigger/index.ts :: mapCategoryToPriority()` maps
`issue→CRITICAL`, `scheduledChange→HIGH`, `accountNotification→MEDIUM`, default→`MEDIUM`. This is only the
**intake** priority; the final OpsItem/notification priority comes from the agent's own `## Key Findings`
severities (parsed in the Callback). The Security Hub path MUST set `category` to a value that yields a
reasonable intake priority (recommended: map CRITICAL findings to the `issue` category and HIGH findings to
`scheduledChange`, OR add an explicit new mapping in `mapCategoryToPriority` for a security category). The
chosen approach MUST be stated in the design, and if `mapCategoryToPriority` is extended it MUST remain
backward-compatible with the existing Health categories.

### FR-6 — Finding-level deduplication (DynamoDB, short TTL)
Because Security Hub re-imports a finding on every update, the system MUST dedup on finding `Id` so the same
finding is not re-investigated repeatedly:
- Keyed by finding `Id`.
- Short TTL (demo-appropriate; recommend a small number of hours, aligned with the existing 1-hour task-token
  TTL style — final value is a planner choice but MUST be a bounded TTL, not permanent).
- Implemented using a DynamoDB conditional write (write-if-absent) so concurrent duplicate deliveries resolve
  deterministically; a finding whose dedup key already exists MUST be skipped (no new execution).
- Modeled on the existing task-token table style (PAY_PER_REQUEST, `timeToLiveAttribute`, PITR for a
  production table where the existing one uses it). The planner MAY use a **new table** or a **new key prefix /
  item type in the existing table** — either is acceptable, but the dedup MUST exist and MUST be least-privilege
  scoped (FR-9).
- Dedup is keyed on finding `Id` only (identity of the finding), independent of severity, so a finding that
  escalates from HIGH to CRITICAL within the TTL window is a known tradeoff; document it (acceptable for a demo).

### FR-7 — Reuse the existing Step Functions and downstream Lambdas unchanged
The Security Hub path MUST reuse, without behavioral change:
- the existing state machine `health-event-impact-analyzer` (same ARN env var `STATE_MACHINE_ARN`),
- `TriggerInvestigation` → `HasFindings?` → `CreateOpsItem` → `SendNotifications`,
- the `HasFindings?` Choice gating on `$.investigationResult.priority` (LOW/MINIMAL → skip),
- the Investigation Trigger, Investigation Callback, OpsCenter Creator, and Notifier Lambdas.

No new state machine and no new copies of these Lambdas may be introduced. (The state-machine name/wording may
remain Health-centric; renaming is out of scope.)

### FR-8 — DevOps Agent skill handles the new input type, SAME output contract
`devops-agent-skill/SKILL.md` MUST be extended to handle a Security Hub finding investigation as an
**additional, clearly-scoped path**, keeping the existing Health-event steps intact. For the new path:
- The impacted resource ARNs are **already known** (from `affectedResources`), so the skill correlates them
  with the topology/teams and assesses *real* impact (is the resource actually in a monitored workload? what is
  the blast radius? who owns it?), rather than discovering resources from scratch.
- The skill MUST produce the **same structured output sections** the Callback parser already depends on. Use
  these section headings **verbatim** (see Verified Skill Output Contract below): `## Summary`,
  `## Key Findings`, `## Answers to Your Questions`, `## Recommended Actions`, `## Notification Routing`,
  `## Jira Tracking`. Do **not** invent new section names.
- The existing "no impact" convention MUST be preserved (emit only `## Summary` with the no-impact keywords
  when the finding's resources are not in any monitored workload / are fully mitigated).

### FR-9 — Event-type context inlined into the prompt (consistent with existing mechanism)
If the Security Hub path needs the agent to know it is handling a security finding (so it chooses the correct
skill branch), that context MUST be injected using the **same inline-tag mechanism** the Trigger already uses
for `[CORRELATION_ID:...]`, `[INVESTIGATION_ID:...]`, and `[JIRA_CONFIG:{...}]` — e.g. an `[EVENT_TYPE:securityhub-finding]`
tag — not a new side channel. The existing `[JIRA_CONFIG]` behavior and the `[INVESTIGATION_ID]`/`[CORRELATION_ID]`
correlation tags MUST be preserved exactly, because the Callback's `extractCorrelationKey()` depends on them.

### FR-10 — Test fixture
Add a test fixture under `events/` representing a `Security Hub Findings - Imported` EventBridge event:
- `source: aws.securityhub`, `detail-type: Security Hub Findings - Imported`.
- `detail.findings[]` containing **at least one CRITICAL and one HIGH** finding, each with realistic ASFF:
  `Id`, `Severity.Label`, `Workflow.Status: NEW`, `RecordState: ACTIVE`, and `Resources[].Id` ARNs.
- `account: 123456789012` and a `region` matching the other fixtures (the fixtures use `us-east-1` and
  `eu-west-1`; use `us-east-1` to match `test-invoke-event.json`).
- The existing `events/test-security-event.json` is **misnamed** — it currently holds an *IAM Health* event
  (`source: aws.health`, `detail-type: AWS Health Event`), NOT a Security Hub finding. It MUST be either
  renamed to something accurate (e.g. `test-iam-security-health-event.json`) and a correctly-named Security Hub
  fixture added, OR repurposed to the real Security Hub ASFF content. The final fixture file(s) MUST make the
  distinction unambiguous. (Flagging because the step brief implied this file was already a Security Hub
  fixture; it is not.)

### FR-11 — Documentation
- Extend `ARCHITECTURE.md`: add the Security Hub ingestion path to the diagram and/or the component tables
  (new EventBridge rule → existing Event Router → existing Step Functions), and describe per-finding fan-out +
  dedup.
- Add or extend a doc under `docs/` (e.g. `docs/security-hub-integration.md`) describing: the EventBridge event
  pattern and the in-pattern-vs-Lambda filter split (per FR-1a), the HIGH/CRITICAL severity filter, the
  NEW/ACTIVE status gating, per-finding fan-out, the dedup table/TTL, the new prompt tag, and the prerequisite
  of **enabling AWS Security Hub** in the account/region.
- Docs MUST stay technical/implementation-focused: **no business-value marketing, no Workshop Studio
  references** (per repo content-distribution rules).

---

## Verified Internal Event Contract (normalization target)

From `event-router/index.ts` (`workflowInput`) and `investigation-trigger/index.ts` (`interface HealthEvent`),
the normalized object MUST contain exactly these keys (consumed downstream):

```
eventId, service, eventType, category, region, availabilityZone,
startTime, endTime, status, description,
affectedResources: [ { resourceId, tags, status } ],
sourceAccountId, rawEvent, ingestedAt
```

- The Step Functions `TriggerInvestigation` passes `healthEvent.$ = $` (the whole normalized object) to the
  Trigger Lambda; the Trigger reads the fields above by name.
- `$.eventId` is used by the state machine's error-catch `Pass` states as `healthEventArn` (correlation id) and
  by the Trigger as `[CORRELATION_ID:...]`. For the Security Hub path, `eventId` = finding `Id`.
- The `HasFindings?` Choice reads `$.investigationResult.priority`; that value is produced by the Callback, not
  the Router, so the Router/normalizer does not set it.

## Verified Skill Output Contract (what the Callback parser depends on)

From `investigation-callback/index.ts :: parseAgentAnalysis()` and `SKILL.md`:

- **`## Summary`** — regex `/## Summary\s*\n+([\s\S]*?)(?=\n##|\n---|\n\|)/`; first ≤500 chars become the OpsItem summary.
- **`## Key Findings`** — bullets of the shape `- **<title>**: <SEVERITY> — <detail>`. The parser extracts the
  bolded title and reads the **leading** severity word (`CRITICAL|HIGH|MEDIUM|LOW|MINIMAL`) of each bullet
  detail; the highest across all bullets becomes the overall severity → OpsItem priority and the `HasFindings?`
  gate. Only the first 5 findings are emitted for display, but ALL are counted for overall severity.
- **`## Recommended Actions`** (also matches `## Actions` / `### 4.`) — a markdown table whose first column is
  `**P1**`/`**P2**`/`**P3**`/`**P4**` (mapped to CRITICAL/HIGH/MEDIUM/LOW); or a numbered list fallback.
- **`## Notification Routing`**, **`## Answers to Your Questions`** — consumed/searched by the parser.
- **`## Jira Tracking`** — the skill's section is titled **`## Jira Tracking`** (two words, title case). The step
  brief referred to a "JIRA TRACKING" block; the actual, verified heading is `## Jira Tracking`. The new path
  MUST use the **existing** heading verbatim and MUST NOT rename it. (Flagged — see Open Items OI-2.)
- **"No impact" detection**: presence of the keywords `no operational impact` / `no immediate operational` /
  `no workloads` (and absence of findings/recommendations) marks the investigation `NO_IMPACT` → priority LOW →
  `HasFindings?` skips OpsItem/notification. The new path MUST honor this exact convention for findings whose
  resources are not in any monitored workload.

---

## Non-Functional / IAM / CDK-Convention Constraints

- **NFR-1 (IAM least privilege).** Any new permissions MUST be scoped to specific resource ARNs and attached
  per-Lambda, matching the existing inline-policy style (e.g. the Trigger's `ssm:GetParameter` scoped to
  `/health-analyzer/jira/*`, the Callback's `aidevops:ListJournalRecords` scoped to `agentspace/*` in the agent
  region). The dedup table grant MUST be the minimum (`PutItem`/`GetItem`/conditional write) on the dedup table
  only. If the dedup lives in the existing token table, the Router gains only the specific actions it needs.
- **NFR-2 (no hardcoded region/account).** Use `cdk.Aws.REGION`/`cdk.Aws.ACCOUNT_ID` / `cdk.Stack.of(this)` and
  stack-ID region suffixing exactly as the existing stacks do. No literal region or account strings.
- **NFR-3 (DynamoDB).** Any new table MUST be `PAY_PER_REQUEST`, use `timeToLiveAttribute`, and (for a
  production table) enable PITR and deletion protection, mirroring `health-analyzer-task-tokens`.
- **NFR-4 (DLQ + alarms).** The ingestion path already runs through the Event Router, which has a DLQ
  (`health-analyzer-event-router-dlq`), a DLQ alarm, and is counted in the composite CloudWatch alarm. Reusing
  the Router means no new DLQ is strictly required; if any new event-driven Lambda is added it MUST follow the
  same DLQ + alarm + composite-alarm-metric pattern.
- **NFR-5 (log retention).** Any new Lambda MUST use the environment-aware retention helper (production =
  3 months, non-production = 2 weeks) as the existing constructs do.
- **NFR-6 (Node.js 24 / pre-bundling).** Any new/changed Lambda code MUST build via the existing
  `scripts/bundle-lambdas.js` esbuild flow (NOT `NodejsFunction`), runtime `NODEJS_24_X`, consistent with every
  existing function.
- **NFR-7 (additive, non-breaking).** The AWS Health ingestion, the existing fixtures, and the existing skill
  behavior MUST remain unchanged; the Security Hub path is purely additive.
- **NFR-8 (demo-quality).** Basic error handling + logging as in the existing Lambdas; no production-grade
  over-engineering.
- **NFR-9 (Jira contract unchanged).** Jira integration behavior MUST NOT change beyond keeping the parser /
  `[JIRA_CONFIG]` / `## Jira Tracking` contract working (explicit out-of-scope item).

---

## Out of Scope

- Option B — the Security Bulletins / RSS-polling ingestion path (declined).
- Any change to Jira integration behavior beyond preserving the existing parser and tag contract.
- Renaming the state machine, replacing the Health path, or building a parallel pipeline.
- Business-value / marketing content and any Workshop Studio references in docs.

---

## Open Items (assumptions that could not be fully verified from code)

- **OI-1 (EventBridge array filtering) — must verify at implementation time.** Whether EventBridge content
  filtering can match `Workflow.Status`, `RecordState`, and `Severity.Label` *inside* the `detail.findings[]`
  array elements. If a given field cannot be matched in-pattern, that filter moves into the Event Router Lambda
  (FR-1a / FR-4). This cannot be settled from the current repo and depends on EventBridge semantics.
- **OI-2 (section heading wording).** The step brief said the callback depends on a "`JIRA TRACKING` block"; the
  verified heading in `SKILL.md` and the parser context is **`## Jira Tracking`**. Requirements standardize on
  the verified `## Jira Tracking`. If the design intends a different literal, it must reconcile against the real
  parser before any rename.
- **OI-3 (ASFF field choices).** The exact ASFF fields chosen for `service`, `eventType`, `category`, and
  `description` (FR-5) are recommendations; the design step should pin the final mapping against real Security
  Hub finding samples.
- **OI-4 (dedup placement).** New dedup table vs. new key-prefix in `health-analyzer-task-tokens` is left to the
  design/planner; both satisfy the requirement. The decision affects IAM scoping (NFR-1) and must be made
  explicit in the design.
- **OI-5 (`events/test-security-event.json` is misnamed).** It currently holds an IAM *Health* event, not a
  Security Hub finding (FR-10). The brief implied otherwise; the design must decide rename-vs-repurpose.

---

## Acceptance Criteria

1. A new EventBridge rule exists in `event-ingestion.ts` matching `source: aws.securityhub` and
   `detail-type: Security Hub Findings - Imported`, targeting the **existing** Event Router Lambda with the same
   `retryAttempts`/`maxEventAge` convention as `HealthEventRule`. (FR-1, FR-7)
2. Only findings with `Workflow.Status = NEW`, `RecordState = ACTIVE`, and `Severity.Label ∈ {HIGH, CRITICAL}`
   result in an investigation; non-qualifying findings never start an execution. Each gating field is filtered
   either in-pattern or in the Event Router, and the actual split is documented in code and docs. (FR-1, FR-1a, FR-4, FR-11)
3. For a `Security Hub Findings - Imported` event containing N qualifying, non-duplicate findings, the Event
   Router starts **N** Step Functions executions — one per finding — each with a unique, `securityhub-`-prefixed
   execution name. A batch with zero qualifying findings starts zero executions and does not error. (FR-4)
4. Each started execution's input is a normalized object with **exactly** the keys in the Verified Internal
   Event Contract, with `eventId` = finding `Id`, `affectedResources[].resourceId` = `Resources[].Id` ARNs, and
   `sourceAccountId` = `event.account`. (FR-5)
5. A finding `Id` already recorded in the dedup store within its TTL window does NOT start a second investigation;
   concurrent duplicate deliveries resolve via a conditional write so exactly one execution starts. The dedup store
   is PAY_PER_REQUEST with a bounded TTL and least-privilege IAM. (FR-6, NFR-1, NFR-3)
6. The AWS Health ingestion path is byte-for-byte behaviorally unchanged: existing Health fixtures still produce
   the same `workflowInput` shape and `health-`-prefixed execution names; no existing Lambda or the state machine
   is duplicated or altered in behavior. (FR-3, FR-7, NFR-7)
7. `SKILL.md` contains an additional, clearly-scoped Security Hub path that uses the already-known impacted ARNs
   to assess real impact and emits output using the verified section headings verbatim (`## Summary`,
   `## Key Findings`, `## Answers to Your Questions`, `## Recommended Actions`, `## Notification Routing`,
   `## Jira Tracking`), with no new or renamed sections, and preserves the "no impact" convention. (FR-8)
8. The agent is told which path to run via an inline prompt tag added by the Investigation Trigger using the same
   mechanism as the existing `[CORRELATION_ID]`/`[INVESTIGATION_ID]`/`[JIRA_CONFIG]` tags; the existing tags and
   the Callback's correlation logic still work. (FR-9)
9. A new `events/` fixture represents a `Security Hub Findings - Imported` event with ≥1 CRITICAL and ≥1 HIGH
   finding in realistic ASFF (`Id`, `Severity.Label`, `Workflow.Status: NEW`, `RecordState: ACTIVE`,
   `Resources[].Id` ARNs), `account: 123456789012`, `region: us-east-1`; the misnamed
   `events/test-security-event.json` is resolved (renamed or repurposed) so file names are unambiguous. (FR-10)
10. `ARCHITECTURE.md` shows the Security Hub ingestion path and a `docs/` doc describes the event pattern,
    filter split, severity/status filters, per-finding fan-out, dedup, prompt tag, and the Security Hub
    enablement prerequisite — with no marketing or Workshop Studio content. (FR-11)
11. All new IAM is resource-ARN-scoped and per-Lambda; no hardcoded regions/accounts; any new Lambda uses
    `NODEJS_24_X` via `scripts/bundle-lambdas.js`, environment-aware log retention, and the DLQ/alarm pattern.
    (NFR-1, NFR-2, NFR-4, NFR-5, NFR-6)
12. The CDK project builds/synthesizes and the existing test suite passes (`npm run bundle` + `cdk synth` /
    `npm test`) with the change in place.
