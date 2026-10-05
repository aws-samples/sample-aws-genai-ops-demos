# Technical Design — Security Hub Findings Ingestion

> Feature worktree: `c:\Users\umancini\sviluppi-new\sample-aws-genai-ops-demos\.worktrees\securityhub-ingestion\observability\proactive-health-event-impact-analyzer`
> All code paths cited below are absolute under this worktree. The main checkout is NOT edited.
> This is a **revision pass** — it resolves every finding in `design-review.json` / `design-review.md`
> (2 HIGH, 3 MEDIUM, 4 NIT). See §15 "Responses to review findings" for the point-by-point disposition.
> Based on `requirements.md` (same task folder). Option A (Security Hub findings via EventBridge) only.

---

## 1. Overview

This design adds a second EventBridge ingestion path that feeds AWS Security Hub findings into the
**existing** investigation pipeline. The guiding constraint is reuse: a new EventBridge rule targets the
*existing* Event Router Lambda, which is extended with a `source`-based branch that fans a multi-finding
Security Hub event out into **one Step Functions execution per qualifying finding**, each carrying the
*exact same* normalized `workflowInput` contract the Health path already produces. From `TriggerInvestigation`
onward, nothing changes: the same state machine, Investigation Trigger, Investigation Callback, OpsCenter
Creator, and Notifier run unchanged. The DevOps Agent skill gains an additive security-finding branch that
emits the *same* output sections the callback parser depends on, selected via a new inline prompt tag
`[EVENT_TYPE:securityhub-finding]` added through the same mechanism as the existing `[CORRELATION_ID]` /
`[INVESTIGATION_ID]` / `[JIRA_CONFIG]` tags.

Two net-new pieces of infrastructure are introduced, both additive:
1. a new EventBridge rule in the `EventIngestion` construct, and
2. a new DynamoDB dedup table (also in `EventIngestion`), granted only to the Event Router.

The Health path is untouched end-to-end. Every new filter, fan-out decision, and dedup write lives in the
Event Router or the new rule; no existing Lambda handler besides `event-router/index.ts` changes behavior
(the Investigation Trigger gains one additive tag emission; see §7).

**Technology stack (locked):** AWS CDK v2 / TypeScript (`aws-cdk-lib ^2.246.0`), Lambda `NODEJS_24_X` bundled
via `scripts/bundle-lambdas.js` (esbuild Node API, NOT `NodejsFunction`), AWS SDK v3 (`@aws-sdk/client-sfn` —
already imported by the Router today — plus `@aws-sdk/client-dynamodb` added to the Router for dedup),
EventBridge, Step Functions, DynamoDB `PAY_PER_REQUEST` with TTL, jest + `aws-cdk-lib/assertions` for tests.

**No edit to `infrastructure/cdk/package.json` is needed, and the reason is mechanical, not "runtime-provided"
(review finding #3).** Verified against source: neither `@aws-sdk/client-sfn` nor `@aws-sdk/client-dynamodb` is
present in `infrastructure/cdk/node_modules/@aws-sdk/` — yet the existing Router imports `client-sfn` and the
build/test suite is green today. Two concrete mechanisms make that work and they apply unchanged to the new
`client-dynamodb` import:

1. **esbuild marks `@aws-sdk/*` external for the Router.** `scripts/bundle-lambdas.js` bundles `event-router`
   with `external: SDK_PROVIDED_BY_RUNTIME = ['@aws-sdk/*']`, so esbuild never resolves those modules from disk
   at bundle time (`npm run bundle` succeeds even though the packages are absent). At runtime the Node.js 24
   Lambda runtime ships the SDK v3 clients, so the import resolves in production.
2. **`tsconfig.json` excludes `lambda`.** The root CDK `tsconfig.json` has `"exclude": ["node_modules",
   "build", "lambda"]`, so `tsc` (`npm run build`) never type-checks the handler sources or their `@aws-sdk/*`
   imports. Adding `import ... from '@aws-sdk/client-dynamodb'` to the Router therefore does not break `tsc`.

This asymmetry (handlers excluded from `tsc`, SDK clients absent from `node_modules`) is exactly why the new
unit tests MUST NOT `import` the Router handler module — see §11.3 / review finding #2. The
`infrastructure/cdk/package.json` is left untouched.

---

## 2. The internal normalized-event contract (what the Event Router must emit)

The downstream contract is fixed by two consumers, read from source:

- The state machine `TriggerInvestigation` state passes the **whole** normalized object through unchanged:
  `payload: { 'taskToken': <token>, 'healthEvent.$': '$' }`
  (`infrastructure/cdk/lib/constructs/investigation-workflow.ts`). So `workflowInput` *is* `healthEvent`.
- The Investigation Trigger reads it as `interface HealthEvent`
  (`infrastructure/cdk/lambda/investigation-trigger/index.ts`), using exactly these fields:
  `eventId, service, eventType, category, region, availabilityZone, startTime, endTime, status,
  description, affectedResources[].{resourceId,tags,status}, sourceAccountId, ingestedAt` (and `rawEvent`
  is carried but not read by the Trigger).
- The state machine error-catch `Pass` states read `$.eventId` as `healthEventArn`.
- The `HasFindings?` Choice reads `$.investigationResult.priority` — produced by the **Callback**, not the
  Router. The Router/normalizer MUST NOT set `priority`.

Therefore the Security Hub normalizer in `event-router/index.ts` MUST emit an object with **exactly these keys**
(same shape the Health branch emits today), one object per surviving finding:

```
eventId, service, eventType, category, region, availabilityZone,
startTime, endTime, status, description,
affectedResources: [ { resourceId, tags, status } ],
sourceAccountId, rawEvent, ingestedAt
```

### 2.1 ASFF finding → `workflowInput` field-by-field mapping

For a single ASFF finding `f` = `event.detail.findings[i]`:

| `workflowInput` field | Type | Value from the ASFF finding | Notes |
|---|---|---|---|
| `eventId` | string | `f.Id` | ASFF `Id` (a full ARN). The correlation key; unique per finding. Used by the Trigger as `[CORRELATION_ID]`/token key material and by the state machine catch as `healthEventArn`. |
| `service` | string | `'SecurityHub'` (constant literal) | Chosen over `f.ProductName` for a stable, predictable value that is safe inside the execution-name prefix and the webhook title. Documented in a code comment. |
| `eventType` | string | `f.Types?.[0] ?? f.GeneratorId ?? 'SecurityHubFinding'` | ASFF `Types[0]` is the stable namespaced type (e.g. `Software and Configuration Checks/Vulnerabilities/CVE`); fall back to `GeneratorId`, then a constant so the field is never empty. |
| `category` | string | derived from `f.Severity.Label` (see §2.2) | Drives `mapCategoryToPriority()` intake priority only. |
| `region` | string | `event.region` | EventBridge envelope region (NOT `f.Resources[].Region`, which may vary per resource). Matches the Health branch's `event.region` preference. |
| `availabilityZone` | `string \| null` | `null` | ASFF findings are not AZ-scoped. |
| `startTime` | `string \| null` | `null` | **Decision (review finding #9):** leave `startTime`/`endTime` `null` for the security path. `buildDescription` emits a literal `Maintenance Window: <...>` line from these fields; with both `null`, `maintenanceWindow` is the string `'Not specified'`, so the line reads `Maintenance Window: Not specified`. Findings have no maintenance window, so suppressing a real value is correct, and the §6.2 skill branch explicitly instructs the agent to ignore the maintenance-window line for security findings (belt-and-suspenders). We deliberately do **not** map `FirstObservedAt`/`CreatedAt` here, to avoid mislabeling a finding's observation time as a maintenance window. |
| `endTime` | `string \| null` | `null` | Findings have no end time. (See `startTime` note re: the cosmetic `Maintenance Window` line.) |
| `status` | string | `f.Workflow?.Status ?? f.RecordState ?? 'NEW'` | Human-readable status for the prompt. |
| `description` | string | `` `${f.Title}. ${f.Description}` `` trimmed, non-empty | Concatenate ASFF `Title` + `Description`; fall back to `f.Title` or a constant if `Description` is absent. Must be non-empty (the Health branch guarantees non-empty via `extractDescription`). |
| `affectedResources` | `Array<{resourceId,tags,status}>` | `f.Resources.map(r => ({ resourceId: r.Id, tags: r.Tags ?? {}, status: f.Workflow?.Status ?? 'ACTIVE' }))` | `resourceId` = ASFF `Resources[].Id` (ARN). `tags` = `Resources[].Tags` or `{}`. `status` = finding workflow status (per-resource status is not in ASFF). Empty `Resources` → `[]` (the finding still investigates; the skill treats "no known resources" as its no-impact path input). |
| `sourceAccountId` | string | `event.account` | Preserves the multi-account agent-space routing in `resolveAgentSpace()`. (ASFF `f.AwsAccountId` equals `event.account` for same-account imports; the envelope `account` is authoritative for routing.) |
| `rawEvent` | object | the **single finding** `f` (NOT the whole batch) | Documented choice: downstream the webhook payload only needs the one finding's context, and carrying the whole multi-finding batch into every per-finding execution would be redundant and bloat the payload. A code comment records this. |
| `ingestedAt` | string (ISO) | `new Date().toISOString()` | Same as Health branch. |

### 2.2 Category → intake priority (FR-5a)

`investigation-trigger/index.ts :: mapCategoryToPriority()` maps `issue→CRITICAL`, `scheduledChange→HIGH`,
`accountNotification→MEDIUM`, default→`MEDIUM`. **Decision: reuse the existing categories, do not modify
`mapCategoryToPriority`.** The Security Hub normalizer sets `category` by severity label:

- `Severity.Label === 'CRITICAL'` → `category = 'issue'` (→ intake priority CRITICAL)
- `Severity.Label === 'HIGH'`     → `category = 'scheduledChange'` (→ intake priority HIGH)

Rationale: this keeps `mapCategoryToPriority` byte-for-byte unchanged (zero risk to the Health path and the
`lambda-logic.test.ts` category tests), and the intake priority is only a *fallback* anyway — the final
OpsItem/notification priority comes from the agent's own `## Key Findings` severities parsed in the Callback
(`buildOutput`/`parseAgentAnalysis`). A code comment in the normalizer documents that `category` here is a
severity-derived intake hint, not a Health event category.

---

## 3. New EventBridge rule and the in-pattern vs. in-Lambda filter split

### 3.1 Event pattern

A new rule `SecurityHubFindingRule` is added to `EventIngestion`
(`infrastructure/cdk/lib/constructs/event-ingestion.ts`), alongside `HealthEventRule`, targeting the **same**
`this.eventRouter` Lambda with the **same** target retry convention (`retryAttempts: 185`,
`maxEventAge: cdk.Duration.hours(24)`):

```ts
const securityHubFindingRule = new events.Rule(this, 'SecurityHubFindingRule', {
  ruleName: 'health-analyzer-securityhub-capture',
  description: 'Captures HIGH/CRITICAL AWS Security Hub findings for impact analysis',
  eventPattern: {
    source: ['aws.securityhub'],
    detailType: ['Security Hub Findings - Imported'],
    detail: {
      findings: {
        'Severity': { 'Label': ['HIGH', 'CRITICAL'] },
        'Workflow': { 'Status': ['NEW'] },
        'RecordState': ['ACTIVE'],
      },
    },
  },
});
securityHubFindingRule.addTarget(new targets.LambdaFunction(this.eventRouter, {
  retryAttempts: 185,
  maxEventAge: cdk.Duration.hours(24),
}));
```

### 3.2 Filter-split decision (FR-1a / OI-1 resolved)

**EventBridge array-matching basis (review finding #4).** EventBridge content filtering matches fields nested
under elements of a JSON array: when a pattern path crosses an array, EventBridge evaluates the leaf matcher
against **each element** and delivers the event if **any** element satisfies it. This is the documented
behavior in the AWS EventBridge "Content filtering in Amazon EventBridge event patterns → Arrays" / "Matching
with arrays" guidance. **This design does not treat the exact in-pattern nesting as proven by the repository**
— per requirements OI-1 / FR-1a it is marked **implementation-time-verified**: the implementer MUST confirm at
`cdk synth`/deploy that the rule is accepted and that a known-qualifying fixture matches. The design is
engineered so correctness does not depend on that verification succeeding (see the fallback below); the
in-pattern filter is purely an optimization.

With that basis, all three gating fields (`Severity.Label`, `Workflow.Status`, `RecordState`) are *intended* to
be expressible in-pattern and are placed there.

**CDK rendering of the nested path (verified behavior of `events.Rule`).** `events.Rule` serializes the
`eventPattern.detail` object literally into the CloudFormation `EventPattern`. The object
`detail: { findings: { Severity: { Label: ['HIGH','CRITICAL'] }, Workflow: { Status: ['NEW'] }, RecordState: ['ACTIVE'] } }`
renders as the JSON pattern
`"detail": { "findings": { "Severity": { "Label": ["HIGH","CRITICAL"] }, "Workflow": { "Status": ["NEW"] }, "RecordState": ["ACTIVE"] } }`.
Because `detail.findings` is an array in the real event, EventBridge applies the `Severity`/`Workflow`/
`RecordState` sub-matchers against each `findings[]` element (the any-element semantics above). The implementer
MUST assert this rendered shape in the synth test (§11.3) and confirm the rule deploys; CDK does not transform
the nesting, so the literal object above is what reaches EventBridge.

**Fallback if a leaf is not expressible in-pattern (review finding #4).** The Event Router's per-finding filter
(§4.2) is the authoritative gate, so the in-pattern filter carries **no correctness weight**. If, at
implementation time, EventBridge rejects any leaf (e.g. the two-level `Workflow.Status` nested inside an array
element) — the rule fails to create or matches nothing — the implementer MUST drop *only that leaf* from the
pattern, keeping `source` + `detail-type` (which are not array-nested and always valid) plus whichever leaves
ARE accepted, and rely on the Router filter for the dropped leaf. Net behavior is unchanged: only
NEW/ACTIVE/HIGH/CRITICAL findings ever start an execution. The only cost of dropping a leaf is that more
batches reach the Router (which then filters them out). A valid, deployable rule is therefore guaranteed; the
rule must never ship in a state that fails to synth/deploy.

**But array matching is "any-element", not "same-element".** A batch where finding A is HIGH/NEW/ACTIVE and
finding B is LOW/SUPPRESSED/ARCHIVED still matches the rule (A satisfies it), and EventBridge delivers the
**entire batch** — including B — to the Router. The pattern is therefore a *coarse batch admission gate*, not a
per-finding guarantee.

**Decision — defense-in-depth, dual enforcement:**
- The three filters live **in the event pattern** (admission gate): batches containing zero qualifying
  findings never invoke the Router at all (cost/noise reduction).
- The **same three filters are re-applied per-finding inside the Event Router** (FR-4), because the pattern's
  any-element semantics cannot guarantee that a *given* finding in a matched batch qualifies. The Router's
  per-finding check is the authoritative gate; the net behavior (only NEW/ACTIVE/HIGH/CRITICAL findings ever
  start an execution) is enforced in the Lambda regardless.

This split is documented in a code comment in `event-ingestion.ts` (on the rule) and in `event-router/index.ts`
(on the per-finding filter), and in `docs/security-hub-integration.md` (FR-11). It satisfies AC-2.

The rule does not overlap `HealthEventRule`: `aws.securityhub` and `aws.health` are disjoint `source` values,
so no event reaches the Router twice (FR-2, AC-6).

---

## 4. Per-finding fan-out, filtering, and dedup in the Event Router

### 4.1 Dispatch branch (FR-3)

`event-router/index.ts :: handler` is extended to branch on `event.source` **before** any normalization:

```
if (event.source === 'aws.securityhub')  → handleSecurityHubEvent(event, context)
else                                      → existing Health normalization (UNCHANGED)
```

The existing Health code is physically unchanged — it is wrapped in the `else` branch (or extracted verbatim
into a `handleHealthEvent` helper that contains the current body). The current single-`StartExecution` return
shape (`{ statusCode, executionArn }`) is preserved for the Health path. The handler's return type is widened
to allow the Security Hub path to return an aggregate summary (`{ statusCode, startedExecutions: string[] }`);
the Health path keeps returning exactly what it returns today. The branch is clearly commented as the only
behavioral addition.

### 4.2 Fan-out loop (FR-4, AC-3)

`handleSecurityHubEvent` iterates `event.detail.findings ?? []`. For each finding `f`:

1. **Per-finding filter (re-applied, §3.2):** skip unless
   `f.Severity?.Label ∈ {HIGH, CRITICAL}` **and** `f.Workflow?.Status === 'NEW'` **and**
   `f.RecordState === 'ACTIVE'`.
2. **Dedup (write-if-absent, §5):** attempt a conditional `PutItem` keyed on `f.Id`. If the condition fails
   (`ConditionalCheckFailedException`), the finding is already in-flight/recently investigated → skip (no
   execution). Any other DynamoDB error on a *qualifying* finding is rethrown so the invocation fails and the
   async retry/DLQ path engages (NFR-4).
3. **Normalize (§2):** build the `workflowInput` for `f`.
4. **StartExecution:** one `StartExecutionCommand` with a unique `securityhub-`-prefixed name (§4.3).

Findings are processed sequentially (a small batch; keeps DynamoDB conditional writes and SFN starts simple
and ordered). Per-finding `StartExecution` failures: on a transient SFN error the whole invocation throws so
EventBridge/async-retry re-delivers the batch — dedup (already-written keys) makes re-processing of
already-started findings a no-op, so re-delivery is safe and idempotent (documented in a code comment).

If **zero** findings survive filtering + dedup, the handler returns `200` with `startedExecutions: []` and
starts no executions and raises no error (AC-3, FR-4 "zero findings → success, no DLQ").

The one-execution-per-finding decision (vs. one per EventBridge event) is documented in a code comment in
`event-router/index.ts` and in `docs/security-hub-integration.md`.

### 4.3 Execution-name uniqueness (FR-4)

Step Functions execution names must be ≤80 chars and match `[0-9A-Za-z-_]`. ASFF `Id` is a long ARN with
illegal characters, so it is **not** usable raw. The name is:

```
securityhub-${sanitize(shortFindingId)}-${Date.now()}-${randomHex(4)}
```

where `sanitize` replaces every non-`[0-9A-Za-z-_]` char with `-` and the finding component is truncated so
the total stays ≤80 chars (prefix + timestamp + random suffix budgeted first). Because the Router already runs
inside a single Lambda invocation per batch, `Date.now()` + a 4-byte `crypto.randomBytes` suffix guarantees
uniqueness across findings in the same batch and across concurrent re-imports of different findings. The
prefix `securityhub-` is deliberately distinct from the Health path's `health-` SFN-execution-name prefix
(AC-3) purely for observability (so a security investigation is identifiable in the SFN console/logs).

**It has no bearing on callback correlation (review finding #6).** The SFN *execution name* and the *incidentId
in the webhook title* are different strings: the Investigation Trigger derives the title from `incidentId`
(`[${incidentId}] AWS Health: ...`), and `incidentId` is `health-${service}-...` → for a finding
`health-SecurityHub-...` (because `service === 'SecurityHub'`, §2.1). So the title DOES start with `health-`,
and the Callback's `extractCorrelationKey` title-fallback (`startsWith('health-')`) *would* match a security
investigation — it is simply never reached, because `[INVESTIGATION_ID:${incidentId}]` (always present, unique,
keyed `GetItem` on the token table) is matched first. Correlation is therefore safe for both paths via
`[INVESTIGATION_ID]`; the `securityhub-` execution-name prefix is not a correlation safeguard and this design
makes no such claim.

---

## 5. DynamoDB dedup design

### 5.1 Table vs. key-prefix reuse — decision (OI-4 resolved)

**Decision: a NEW dedicated table `health-analyzer-securityhub-dedup`, created in the `EventIngestion`
construct and granted only to the Event Router.**

Rationale (over reusing `health-analyzer-task-tokens`):
- **Ownership/locality.** The token table lives in `InvestigationWorkflow` and is granted to the Trigger and
  Callback. The dedup store is written exclusively by the Event Router, which lives in `EventIngestion`. A new
  table keeps the grant local to the one Lambda that touches it and avoids cross-construct coupling (the Router
  would otherwise need the token table passed in as a prop just to dedup).
- **Least privilege (NFR-1).** The Router gets `PutItem` on the dedup table *only* — it never needs any access
  to task tokens. Reusing the token table would widen the Router's blast radius onto token rows.
- **Schema clarity.** The token table's PK is `investigationId`; overloading it with a `finding#`-prefixed
  item type is possible but muddies two unrelated lifecycles (task tokens are per-investigation and deleted on
  callback; dedup keys are per-finding and expire by TTL).

### 5.2 Table definition

```ts
const findingDedupTable = new dynamodb.Table(this, 'SecurityHubFindingDedupTable', {
  tableName: 'health-analyzer-securityhub-dedup',
  partitionKey: { name: 'findingId', type: dynamodb.AttributeType.STRING },
  timeToLiveAttribute: 'ttl',
  billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
  pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
  deletionProtection: isProduction,
  removalPolicy: isProduction ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
});
```

This mirrors `health-analyzer-task-tokens` exactly (NFR-3): `PAY_PER_REQUEST`, `timeToLiveAttribute: 'ttl'`,
PITR on, deletion protection + `RETAIN` in production and disposable in non-production. `isProduction` is
derived in `EventIngestion` the same way `InvestigationWorkflow` derives it
(`props.deployEnvironment === 'production'`); `EventIngestion` already receives `deployEnvironment`.

- **Key schema:** PK `findingId` (string) = ASFF `f.Id`.
- **TTL:** `ttl` (epoch seconds) = `now + DEDUP_TTL_SECONDS`. **Decision: 6 hours** (`21600`). Bounded, not
  permanent (FR-6); long enough to absorb Security Hub's frequent re-imports of the same finding, short enough
  that a genuinely re-raised finding after the window re-investigates. (The task-token TTL is 1h; dedup is
  deliberately longer because re-imports recur over hours, not minutes. The value is a single named constant in
  the Router with a comment.) The table env var `FINDING_DEDUP_TABLE` is passed to the Router.
- **Dedup identity:** keyed on `findingId` **only**, independent of severity and of the investigation outcome.
  Documented tradeoffs (FR-6), both acceptable for a demo and both recorded in `docs/security-hub-integration.md`:
  - A finding that escalates HIGH→CRITICAL within the 6h window is not re-investigated until the key expires.
  - **(Review finding #7)** A finding evaluated as `NO_IMPACT`/`LOW` still consumes a 6h dedup key, so a
    genuine re-import within the window that *should* now be investigated — e.g. after the resource is added to
    a monitored workload (topology change) — is suppressed until the key expires. Same class as the
    severity-escalation case, triggered by topology change rather than severity change.

### 5.3 Conditional write (idempotent across concurrent deliveries)

For each qualifying finding the Router issues:

```ts
await dynamo.send(new PutItemCommand({
  TableName: FINDING_DEDUP_TABLE,
  Item: {
    findingId: { S: f.Id },
    createdAt: { S: new Date().toISOString() },
    ttl: { N: String(Math.floor(Date.now() / 1000) + DEDUP_TTL_SECONDS) },
  },
  ConditionExpression: 'attribute_not_exists(findingId)',
}));
```

A `ConditionalCheckFailedException` means another delivery already claimed this `findingId` → the finding is
skipped (no execution). This resolves concurrent duplicate EventBridge deliveries deterministically: exactly
one writer wins and starts exactly one execution (AC-5). The write happens **before** `StartExecution`, so a
crash between the write and the start at worst drops one investigation for one finding within the TTL window —
an acceptable demo-grade tradeoff, and preferable to the inverse (duplicate investigations), noted in a code
comment.

### 5.4 IAM (NFR-1)

In `EventIngestion`, grant the Router the minimum on the dedup table only. Because the Router uses a conditional
`PutItem` and never reads, the grant is scoped to `dynamodb:PutItem` on the single table ARN:

```ts
findingDedupTable.grant(this.eventRouter, 'dynamodb:PutItem');
```

(`grant(...)` with the explicit action produces a table-ARN-scoped statement — not a wildcard — satisfying the
existing `DynamoDB access ... not wildcard` test in `health-event-analyzer.test.ts`.) No `GetItem`/`Query`/
`DeleteItem` is granted. The Router's existing `grantStartExecution` on the state machine is unchanged.

---

## 6. SKILL.md changes (additive security-finding path, same output contract)

The file `devops-agent-skill/SKILL.md` is extended additively. The existing Health steps (Step 1–6, Output
Format, Heading rules, "no impact") are **kept verbatim**. The following additive changes are made:

### 6.1 Frontmatter / scope note
The `description` frontmatter is lightly extended to mention security findings, and a short "**Which path to
run**" note is added at the top of the body: *if the prompt contains `[EVENT_TYPE:securityhub-finding]`, run the
Security Hub Finding path (§new); otherwise run the Health Event path (existing).* This is the only routing
signal and it uses the same inline-tag convention as the existing tags.

### 6.2 New section: "Security Hub Finding Impact Assessment"
A new top-level section (placed after the Health "Step 6" and before "## Output Format") describes the
security path. It opens with an explicit instruction (review finding #9): *the `Maintenance Window:` line in
the prompt is not applicable to a security finding — ignore it; findings have no maintenance window.* (The
normalizer already sets `startTime`/`endTime` to `null` so the line reads `Not specified`, §2.1.)

Its defining difference from the Health path: **the impacted resource ARNs are already known** (from the
prompt's `Affected Resources` block, populated from `affectedResources[].resourceId`). So the agent does
**not** discover resources; it:
1. Takes the given finding ARNs as the starting set.
2. Correlates each ARN with the application topology/teams (resource tags, stack ownership, topology
   groupings — reusing the Health path's Step 4 technique).
3. Assesses **real** impact: is the resource actually part of a monitored workload? what is the blast radius
   through dependencies? who owns it?
4. Assigns per-workload severity using the **same** CRITICAL/HIGH/MEDIUM/LOW scale and the same "overall =
   highest" rule as the Health path's Step 3.
5. Produces recommendations and routing exactly as the Health path does.

### 6.3 Same output contract (FR-8, AC-7) — exact existing section names
The new path reuses the **existing** "## Output Format", "### Heading rules", and "### What \"no impact\"
looks like" sections **unchanged**. It MUST emit, verbatim, the headings the Callback parser
(`investigation-callback/index.ts :: parseAgentAnalysis`) depends on. Quoting the exact existing section names
from `SKILL.md` / verified against the parser:

- `## Summary`
- `## Key Findings`  (bullets `- **<title>**: <SEVERITY> — <detail>`)
- `## Answers to Your Questions`
- `## Recommended Actions`  (table with `**P1**`/`**P2**`/`**P3**` first column)
- `## Notification Routing`
- `## Jira Tracking`  ← **two words, title case, verbatim.** (OI-2 resolved: the step brief's "JIRA TRACKING"
  is reconciled to the real heading `## Jira Tracking`; no rename.)

No new or renamed sections are introduced. The security path explicitly points back to the existing Output
Format block rather than restating it, to prevent drift.

### 6.4 Jira sub-step reuse — decision: reuse the Health conventions verbatim (review finding #8)
The existing Step 6 ("Jira ticket tracking") block is reused **completely unchanged** for the security path.

**Decision (option a):** keep the existing Jira summary prefix `[Health] <eventTypeCode> — <severity>` and the
labels `["aws-health-event", "auto-created"]` **verbatim**, and keep the de-dup JQL exactly as written
(`labels = "aws-health-event"` AND `text ~ "<eventArn or eventTypeCode>"` AND `statusCategory != Done`). This
is chosen over introducing `[Security]` / an `aws-securityhub-finding` label because:
- It touches **nothing** in the Jira contract, giving zero risk to the existing JQL de-dup and the `## Jira
  Tracking` parser (NFR-9).
- De-dup still works for findings: the JQL's `text ~ "<eventArn or eventTypeCode>"` clause matches on the
  normalized `eventId` (= ASFF `f.Id`, unique per finding) which the agent substitutes exactly as it does the
  Health event ARN, so one ticket per finding is still enforced.

The accepted cost is cosmetic: a security-finding ticket is prefixed `[Health]` and carries the
`aws-health-event` label. The agent's `## Summary`/`## Key Findings` body makes the security nature explicit,
so the mislabel is not operationally confusing. No `may` remains: Step 6 is used byte-for-byte, and §8 marks
the SKILL.md Step 6 block as **unchanged**. The `[JIRA_CONFIG:{...}]` trigger condition, the "do not call
ssm:GetParameter" rule, the MEDIUM+ threshold, and the search-before-create behavior are all unchanged.

### 6.5 "No impact" convention preserved (FR-8)
The existing "What \"no impact\" looks like" rule is explicitly reaffirmed for the security path: when the
finding's resources are not in any monitored workload (or are fully mitigated/redundant), the agent emits only
`## Summary` with the no-impact keywords (`no operational impact` / `no immediate operational` / `no
workloads`) and stops. The Callback then marks `NO_IMPACT` → priority `LOW` → `HasFindings?` skips OpsItem and
notification — identical to Health.

---

## 7. Event-type context inlined into the prompt (FR-9, AC-8)

The agent must know it is handling a security finding so it selects the §6.2 branch. This is injected using
the **same** inline-tag mechanism the Investigation Trigger already uses in `buildDescription()`
(`investigation-trigger/index.ts`), which today emits `[CORRELATION_ID:...]`, `[INVESTIGATION_ID:...]`, and the
optional `[JIRA_CONFIG:{...}]`.

**Change (additive, minimal):** add an optional `[EVENT_TYPE:securityhub-finding]` tag to the description,
immediately after `[INVESTIGATION_ID:...]`.

**Decision — no new interface field (review finding #5).** The normalized object carries **only** the contract
keys listed in §2 — no `eventSource` or any other field is added, so the "exactly these keys" invariant (AC-4)
and the §11.3 exact-keys test hold. The Trigger gates the new tag **solely** on the already-present contract
key `healthEvent.service === 'SecurityHub'` (the constant literal set by the §2.1 normalizer). No earlier draft
`eventSource` option remains; an implementer must not add one.

```
[CORRELATION_ID:${healthEvent.eventId}]
[INVESTIGATION_ID:${incidentId}]
${eventTypeTag}      // "[EVENT_TYPE:securityhub-finding]\n" when healthEvent.service === 'SecurityHub', else ""
${jiraTag}
...
```

Because the Health branch never sets `service` to the literal `'SecurityHub'`, `eventTypeTag` is `''` for every
Health event — `interface HealthEvent` and the Health prompt stay byte-identical (no new key, no new required
field). The incident title is produced by the unchanged Trigger code (`[${incidentId}] AWS Health:
${service} ...`); for a finding `service` is `SecurityHub`, so `incidentId` is `health-SecurityHub-${ts}-${rand}`
and the title is `[health-SecurityHub-...] AWS Health: SecurityHub ...`. Correlation is unaffected: the Callback
matches `[INVESTIGATION_ID:${incidentId}]` (unique, keyed `GetItem` on the token table) first for both paths
(see §4.3 and review finding #6). The only Trigger code change is the conditional emission of this one tag.

The existing `[CORRELATION_ID]`/`[INVESTIGATION_ID]`/`[JIRA_CONFIG]` tags and the Callback's
`extractCorrelationKey()` logic are preserved exactly (NFR-9, AC-8). All other Trigger behavior (webhook
payload, HMAC, retry, agent-space resolution, token write) is unchanged.

---

## 8. Files created / changed (all under the worktree)

**Changed:**
- `infrastructure/cdk/lib/constructs/event-ingestion.ts` — add `SecurityHubFindingRule` (new EventBridge rule
  targeting the existing Router), add the `health-analyzer-securityhub-dedup` DynamoDB table, grant the Router
  `dynamodb:PutItem` on it, and add `FINDING_DEDUP_TABLE` env var to the Router. Document the in-pattern-vs-Lambda
  filter split and per-finding fan-out in comments.
- `infrastructure/cdk/lambda/event-router/index.ts` — add `source`-based dispatch; add `handleSecurityHubEvent`
  (per-finding filter, conditional-write dedup, normalization per §2, `securityhub-`-prefixed StartExecution
  fan-out); keep the Health branch behavior identical. Add ASFF detail types.
- `infrastructure/cdk/lambda/investigation-trigger/index.ts` — emit the optional
  `[EVENT_TYPE:securityhub-finding]` tag in `buildDescription()` when `service === 'SecurityHub'`; no other
  change.
- `devops-agent-skill/SKILL.md` — additive "Security Hub Finding Impact Assessment" section + top-of-body path
  router note + frontmatter tweak + Jira sub-step note; existing sections and output contract unchanged.
- `ARCHITECTURE.md` — add the Security Hub ingestion path to the diagram and the "Event Ingestion Layer"
  component table (new rule → existing Router → existing state machine), describe per-finding fan-out + dedup.
- `README.md` — one line in prerequisites: enabling AWS Security Hub in the account/region.
- `events/test-security-event.json` — **rename** to `events/test-iam-security-health-event.json` (its content
  is an IAM *Health* event, `source: aws.health`; §9 / OI-5). Content unchanged on rename.

**Created:**
- `events/test-securityhub-findings-event.json` — new ASFF fixture (FR-10, §9).
- `docs/security-hub-integration.md` — new doc (FR-11, §10).
- `test/securityhub-ingestion.test.ts` — new jest tests (CDK assertions + Router logic). **Path is relative to
  the demo root** (review finding #1): the file is created at
  `observability/proactive-health-event-impact-analyzer/test/securityhub-ingestion.test.ts`, the **same
  directory** as `lambda-logic.test.ts` and `health-event-analyzer.test.ts`. This is the only location jest
  scans: `infrastructure/cdk/jest.config.js` sets `roots: ['<rootDir>/../../test']` (= demo-root `test/`). A
  file under `infrastructure/cdk/test/` would be silently ignored and the §11.3 assertions would never run
  (AC-12 would be unsatisfiable). See §11.3.

**Not changed (explicitly):** `investigation-workflow.ts` (state machine + downstream Lambdas),
`investigation-callback/index.ts`, `opscenter-creator`, `notifier`, `health-event-analyzer-stack.ts`
(the new rule/table live inside the existing `EventIngestion` construct it already instantiates — no stack
wiring change needed), `scripts/bundle-lambdas.js` (no new Lambda function is added; the Router entry already
exists). `mapCategoryToPriority` is unchanged (§2.2).

---

## 9. Test fixtures (FR-10, OI-5, AC-9)

- **Rename** `events/test-security-event.json` → `events/test-iam-security-health-event.json`. It currently
  holds an IAM *Health* event (`source: aws.health`, `detail-type: AWS Health Event`), which is misleading
  under the current name. Rename only; content unchanged. (OI-5 resolved: rename, not repurpose — the IAM
  Health fixture is still useful as a Health-path example and should not lose its content.)
- **Create** `events/test-securityhub-findings-event.json`: a `Security Hub Findings - Imported` EventBridge
  envelope with `source: aws.securityhub`, `account: "123456789012"`, `region: "us-east-1"`, and
  `detail.findings[]` containing **at least one CRITICAL and one HIGH** finding. Each finding has realistic
  ASFF: `Id` (ARN), `Severity.Label` (`CRITICAL` / `HIGH`), `Workflow.Status: "NEW"`, `RecordState: "ACTIVE"`,
  `Title`, `Description`, `Types`, `CreatedAt`/`FirstObservedAt`, and `Resources[].{Id,Type,Region,Tags}` with
  real ARNs (e.g. a Lambda function ARN and an EC2/S3 ARN to exercise topology correlation). A third,
  non-qualifying finding (e.g. `Severity.Label: LOW` or `RecordState: ARCHIVED`) SHOULD be included to let the
  Router/pattern filter tests assert it is skipped.

---

## 10. Documentation (FR-11, AC-10)

- **`docs/security-hub-integration.md`** (new) — technical/implementation-focused (no marketing, no Workshop
  Studio refs, per repo content-distribution rules). Covers: the EventBridge event pattern; the in-pattern vs.
  Event-Router filter split and *why* (array any-element semantics, §3.2); the HIGH/CRITICAL severity filter;
  the NEW/ACTIVE status gating; per-finding fan-out (one execution per finding) and the `securityhub-` naming;
  the dedup table (`health-analyzer-securityhub-dedup`, PK `findingId`, 6h TTL, PAY_PER_REQUEST,
  conditional-write) and the HIGH→CRITICAL-within-TTL tradeoff; the `[EVENT_TYPE:securityhub-finding]` prompt
  tag; and the prerequisite of **enabling AWS Security Hub** in the account/region.
- **`ARCHITECTURE.md`** — extend the "Event Ingestion Layer" component details and the architecture diagram to
  show the second rule and the dedup table feeding the existing Router/state machine. Keep the existing Health
  description intact.

---

## 11. Verification plan (AC-12)

Commands are run from the CDK project directory
`observability/proactive-health-event-impact-analyzer/infrastructure/cdk/` (npm scripts resolved from its
`package.json`). Note the jest/test-file location is **not** under that directory: jest's `roots` points up two
levels to the demo-root `test/` folder (review finding #1), so the new test file lives at
`observability/proactive-health-event-impact-analyzer/test/securityhub-ingestion.test.ts` even though the
`npm test` command is invoked from `infrastructure/cdk/`.

1. **Bundle:** `npm run bundle` — esbuild compiles all Lambda handlers (including the modified
   `event-router` and `investigation-trigger`) to `dist/lambda/<name>/index.js`. Must succeed with no new
   function entries required (the Router entry already exists in `scripts/bundle-lambdas.js`).
2. **Type build:** `npm run build` (`tsc`) — confirms the TypeScript in the construct, the Router, and the
   Trigger compiles (interface widening for the Router return type and the optional event-source signal must
   type-check).
3. **Tests:** `npm test` (jest; `pretest` runs `npm run bundle`). The existing suite in the demo-root `test/`
   folder MUST stay green (Health rule, IAM-least-privilege, DynamoDB-not-wildcard, log retention,
   category/severity/correlation logic). The new file is
   `observability/proactive-health-event-impact-analyzer/test/securityhub-ingestion.test.ts` (demo-root
   `test/`, picked up by `roots: ['<rootDir>/../../test']`; review finding #1). It adds:
   - **CDK assertions** (via `aws-cdk-lib/assertions` `Template.fromStack`) on a synthesized template: a new
     `AWS::Events::Rule` whose `EventPattern` has `source: ['aws.securityhub']`,
     `detail-type: ['Security Hub Findings - Imported']`, and the rendered nested `detail.findings.{Severity.Label,
     Workflow.Status, RecordState}` shape from §3.1/§3.2; a new `AWS::DynamoDB::Table` named
     `health-analyzer-securityhub-dedup` with PK `findingId`, TTL attribute `ttl`,
     `BillingMode: PAY_PER_REQUEST`; and an IAM policy statement granting `dynamodb:PutItem` on that table ARN
     scoped (not `Resource: '*'`) to the Router role. (Satisfies "new rule + dedup resource + IAM additions
     appear in `cdk synth`" and the existing wildcard-guard test.)
   - **Router-logic unit tests — pure-function style, with a hard constraint (review finding #2):** these tests
     MUST **NOT** `import` the `event-router` handler module. The handler imports `@aws-sdk/client-sfn` and
     (newly) `@aws-sdk/client-dynamodb`, **neither of which is installed** in
     `infrastructure/cdk/node_modules/@aws-sdk/`; ts-jest (which compiles the demo-root `test/*.ts` and maps
     `^@aws-sdk/(.*)$` to `./node_modules/@aws-sdk/$1`) would fail module resolution and break the whole suite,
     regressing the currently-green tests. **Chosen approach:** mirror the pure normalizer/filter/sanitizer
     logic **inline** in the test file, exactly as `lambda-logic.test.ts` already re-implements
     `extractCorrelationKey`/`mapCategoryToPriority` inline, and assert against handler source read as a string
     via `fs.readFileSync` where a source-level check is wanted. (An equally acceptable implementation is to
     extract the normalizer/filter/execution-name helpers into a pure module under `event-router/` that imports
     no `@aws-sdk/*` and import only that module — but the inline-mirror approach is the one this design
     specifies, to match the established pattern and avoid any transitive SDK import.) The logic assertions:
     per-finding filter keeps only NEW/ACTIVE/HIGH/CRITICAL and drops LOW/non-NEW/ARCHIVED; a multi-finding
     batch yields N normalized inputs each with **exactly** the §2 contract keys (asserted as an exact key set,
     locking AC-4 and review finding #5), `eventId === f.Id`,
     `affectedResources[].resourceId === Resources[].Id`, `sourceAccountId === event.account`,
     `service === 'SecurityHub'`, `startTime === null` && `endTime === null`; the execution-name sanitizer
     output matches `^securityhub-[0-9A-Za-z_-]+$` and is ≤80 chars; a batch with zero qualifying findings
     yields zero starts.
4. **Synth:** `npx cdk synth` (via `npm run cdk -- synth`) — confirms the full stack synthesizes and that the
   new rule, the dedup table, and the IAM additions appear in the synthesized CloudFormation.
5. **Cleanup:** remove any scratch artifacts; `dist/` and `cdk.out/` are already gitignored.

Everything above is unit/synth-level (no live AWS). The Health path's continued green test run is the
regression guard for NFR-7 / AC-6.

---

## 12. Error handling (per operation)

- **Router — malformed/empty `detail.findings`:** treat as empty array → zero executions, `200`. Not an error
  (a batch could legitimately contain only non-qualifying findings). Logged at info.
- **Router — per-finding filter miss:** skip silently (debug log). Not an error.
- **Router — dedup `ConditionalCheckFailedException`:** expected duplicate → skip finding, info log. Not fatal.
- **Router — dedup other DynamoDB error (throttle, 5xx) on a qualifying finding:** rethrow → invocation fails →
  async retry (`retryAttempts: 2`, `maxEventAge: 6h` on the EventInvokeConfig) then the existing
  `health-analyzer-event-router-dlq` + DLQ alarm. Recoverable via retry; terminal failures surface on the DLQ
  (NFR-4). Logged at error.
- **Router — `StartExecution` transient failure:** rethrow → async retry re-delivers the batch; dedup keys
  already written make re-processing idempotent (already-started findings are skipped on retry). Logged at
  error. `ExecutionAlreadyExists` (same name) is treated as success (idempotent) and logged at warn.
- **Trigger — new tag emission:** pure string building; cannot fail independently. The optional field read is
  null-safe.
- **Skill — resources not in topology:** the "no impact" path (not an error) → `NO_IMPACT`.

Validation of external input (ASFF): every field read is defensive (`?.` + fallbacks per §2.1) so a finding
missing optional ASFF fields still normalizes to a valid `workflowInput` with non-empty `description` and a
possibly-empty `affectedResources` (the skill handles the empty case as no-impact). Required for a qualifying
finding: `Id`, `Severity.Label`, `Workflow.Status`, `RecordState` — all guaranteed present by the filter that
admitted the finding. A qualifying finding with no `Id` is impossible under ASFF (always present); defensively,
a finding lacking `Id` is skipped and logged at warn.

---

## 13. Invariants and ownership

- **"Only NEW/ACTIVE/HIGH/CRITICAL findings are investigated"** — owned by the Event Router per-finding filter
  (authoritative), with the EventBridge pattern as a coarse pre-filter (§3.2). Owned in the Lambda because the
  pattern's any-element array semantics cannot guarantee it per-finding.
- **"Exactly one investigation per finding per TTL window"** — owned by the DynamoDB conditional write (§5.3).
  Owned at the data layer because only an atomic write-if-absent resolves concurrent duplicate deliveries
  deterministically.
- **"Normalized contract shape is identical for both paths"** — owned by `event-router/index.ts` (both
  branches emit the same key set); guarded by the new Router-logic unit test asserting the exact keys.
- **"Health path unchanged"** — owned by the `source`-branch isolation in the Router and the untouched
  `investigation-workflow.ts`; guarded by the existing test suite.

---

## 14. Resolved open items

- **OI-1 (EventBridge array filtering):** resolved in §3.2 — all three filters are expressible in-pattern
  (any-element match), but are re-enforced per-finding in the Router because any-element ≠ same-element; dual
  enforcement documented.
- **OI-2 (section heading):** resolved in §6.3 — standardize on the verified `## Jira Tracking`; no rename.
- **OI-3 (ASFF field choices):** pinned in §2.1 (`service='SecurityHub'`, `eventType=Types[0]|GeneratorId`,
  `category` from severity, `description=Title+Description`).
- **OI-4 (dedup placement):** resolved in §5.1 — new dedicated table in `EventIngestion`, Router-scoped IAM.
- **OI-5 (misnamed fixture):** resolved in §9 — rename `test-security-event.json` →
  `test-iam-security-health-event.json`; add a correctly-named ASFF fixture.

---

## 15. Responses to review findings (`design-review.json` / `design-review.md`)

All nine findings are **addressed** (none backlogged, none ignored). Each resolution is consistent with
`requirements.md`.

- **#1 (HIGH — test file path vs. jest `roots`): addressed.** Verified `infrastructure/cdk/jest.config.js` sets
  `roots: ['<rootDir>/../../test']`. §8 and §11 now state the new test is created at the demo-root
  `observability/proactive-health-event-impact-analyzer/test/securityhub-ingestion.test.ts` (same dir as
  `lambda-logic.test.ts`), and clarify that `npm test` is invoked from `infrastructure/cdk/` while the file
  lives two levels up. No cdk-local `test/` path remains.

- **#2 (HIGH — tests must not import the handler): addressed.** Verified neither `@aws-sdk/client-sfn` nor
  `@aws-sdk/client-dynamodb` is in `node_modules`, that `tsconfig.json` excludes `lambda`, and that
  `lambda-logic.test.ts` re-implements pure functions inline (never imports a handler). §11.3 now hard-forbids
  importing the `event-router` module and pins the inline-mirror + `fs.readFileSync` strategy (with the
  pure-helper-module extraction noted as an acceptable alternative).

- **#3 (MEDIUM — wrong justification for no package.json change): addressed.** §1 rewritten to cite the two
  verified mechanisms: esbuild `external: ['@aws-sdk/*']` for `event-router` (no disk resolution at bundle
  time) + the `lambda` exclusion in `tsconfig.json` (no `tsc` type-check of handler imports). Conclusion (no
  package.json edit) kept.

- **#4 (MEDIUM — EventBridge nested-array filtering asserted, no fallback): addressed.** §3.2 no longer claims
  "Confirmed"; it cites the AWS "Content filtering → Arrays / Matching with arrays" basis and marks the exact
  in-pattern nesting **implementation-time-verified** per FR-1a. It adds an explicit fallback — since the
  Router is the authoritative gate, any non-expressible leaf is dropped from the pattern (keeping
  `source`+`detail-type`+expressible leaves) with no net behavior change — and documents the literal CDK
  rendering of the nested `detail` object plus a synth-test assertion on that shape.

- **#5 (MEDIUM — §7 contradictory `eventSource`): addressed.** The `eventSource` option is removed entirely.
  §7 now states the normalized object carries **only** the §2 contract keys and the Trigger gates the
  `[EVENT_TYPE:securityhub-finding]` tag solely on `healthEvent.service === 'SecurityHub'` (an existing contract
  key — no interface change). §11.3 adds an exact-key-set assertion to lock this.

- **#6 (NIT — title-fallback rationale inaccurate): addressed.** §4.3 corrected: the `securityhub-` SFN
  execution-name prefix is for observability only and does **not** affect title correlation; the title carries
  the `health-SecurityHub-...` incidentId (which does start with `health-`), so the fallback would match but is
  never reached because `[INVESTIGATION_ID]` (unique, keyed) is matched first.

- **#7 (NIT — no-impact finding still holds a dedup key): addressed.** §5.2 tradeoff list now includes the
  topology-change case: a `NO_IMPACT`/`LOW` finding still consumes a 6h dedup key, so a re-import within the
  window after the resource joins a monitored workload is suppressed until expiry — acceptable for a demo,
  documented in the integration doc.

- **#8 (NIT — ambiguous Jira wording): addressed.** §6.4 decides option (a): reuse the Health Jira conventions
  **verbatim** (`[Health]` summary prefix, `["aws-health-event","auto-created"]` labels, unchanged JQL) for
  zero contract risk; de-dup still works via the JQL `text ~` clause matching the unique `eventId` (= finding
  `Id`). The accepted cost is the cosmetic `[Health]` label. All "may" wording removed; §8 marks SKILL.md Step
  6 as unchanged.

- **#9 (NIT — misleading "Maintenance Window" label): addressed.** §2.1 now sets `startTime`/`endTime` to
  `null` for the security path (so the line reads `Maintenance Window: Not specified`) **and** §6.2 instructs
  the agent to ignore the maintenance-window line for security findings. Both measures stated.
