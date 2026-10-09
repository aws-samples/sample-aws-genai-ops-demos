# Security Hub findings ingestion path for the health-event analyzer

The change adds a second EventBridge ingestion path that feeds HIGH/CRITICAL AWS Security Hub findings into the existing investigation pipeline, reusing everything from `TriggerInvestigation` onward. A new rule (`aws.securityhub`) targets the existing Event Router Lambda, which now branches on `event.source`: the Health path is extracted verbatim into `handleHealthEvent`, and a new `handleSecurityHubEvent` fans a multi-finding batch out into one Step Functions execution per qualifying finding, each carrying the exact same normalized contract the Health path emits. A dedicated DynamoDB dedup table (conditional write, 6h TTL) guarantees one investigation per finding Id. The DevOps Agent skill gains an additive Security Hub path selected by a new `[EVENT_TYPE:securityhub-finding]` prompt tag, reusing the existing output contract unchanged.

Watch for: nothing blocking. The normalized contract matches the Health branch byte-for-byte (confirmed against the pre-change source), the parser headings in SKILL.md are preserved exactly, IAM is ARN-scoped, and the Health path is untouched. Verification evidence (bundle/build/286-test suite/synth all green) is present and credible.

**Verdict**: APPROVED

## High-level view

The dispatch is a clean `source`-based branch at the top of the handler. The pre-change Health body was lifted into `handleHealthEvent` without modification — the 14-key `workflowInput` it builds (`eventId … ingestedAt`, no `priority`) is identical to what `normalizeFinding` now emits for findings, so the downstream Step Functions / Investigation Trigger contract is satisfied by both paths. The one benign asymmetry is `region`: Health uses `event.region || detail.region`, the finding path uses `event.region`; this is a deliberate design decision (envelope region is authoritative for findings) and is documented.

Filtering is dual-enforced. The EventBridge pattern filters `Severity.Label`/`Workflow.Status`/`RecordState` under the `findings[]` array, which EventBridge matches with any-element semantics, so it is only a coarse batch-admission gate. The Router re-applies the identical three-way filter per finding as the authoritative gate. Both the code and the integration doc document this split and the FR-1a fallback (drop a leaf, Router still enforces). Synth accepted the nested pattern, so no fallback was needed.

Dedup is a conditional `PutItem` keyed on the finding Id, issued before `StartExecution`, with `ConditionalCheckFailedException` → skip and any other DynamoDB error → rethrow (engaging the existing async-retry/DLQ). Write-before-start trades a rare dropped investigation for never emitting a duplicate — the documented and defensible choice. Execution names are `securityhub-`-prefixed, sanitized, timestamp+random, budgeted to ≤80 chars.

The SKILL.md change is additive: a frontmatter tweak, a top-of-body "Which path to run" note, and one new `## Security Hub Finding Impact Assessment` section inserted between Step 6 and `## Output Format`. The six parser headings (`## Summary`, `## Key Findings`, `## Answers to Your Questions`, `## Recommended Actions`, `## Notification Routing`, `## Jira Tracking`) remain exactly once each and unrenamed; the callback parser keys off `## Summary` / `## Key Findings`, both preserved. The Jira sub-step is reused verbatim, so the JQL de-dup and labels are untouched.

IAM grants the Router `dynamodb:PutItem` on the dedup table ARN only; the suite's wildcard-guard test asserts no `Resource: '*'`. The dedup table mirrors the existing task-token table (PAY_PER_REQUEST, TTL, PITR, production RETAIN/deletion-protection), and `isProduction` is derived the same way the workflow construct does. The ASFF fixture is realistic (CRITICAL + HIGH qualifying + a LOW/ARCHIVED non-qualifying finding with real ARNs and tags). The integration doc is technical and accurate with no Workshop Studio reference.

<details>
<summary>Issues (0 blocking, 1 nit)</summary>

1. **Exported Router helpers unused by tests (nit, non-blocking)** — `normalizeFinding` and `buildExecutionName` are `export`ed from `event-router/index.ts`, but per the design the test deliberately mirrors the logic inline rather than importing the handler (to avoid resolving the uninstalled `@aws-sdk/*` imports). The exports are harmless — esbuild bundles the handler as an entry point and the test never imports them — but they are dead API surface. Optional: drop the `export` keywords, or leave as-is.

</details>

<details>
<summary>Details</summary>

### Normalization produces the exact internal contract

`handleHealthEvent` is the pre-change body moved under an `else`-equivalent branch; comparing against `git show main:…/event-router/index.ts` confirms the Health `workflowInput` is unchanged (same 14 keys, same `health-${Date.now()}-${requestId.slice(0,8)}` execution name, same `{ statusCode, executionArn }` return). `normalizeFinding` emits an object whose `WorkflowInput` interface lists exactly those 14 keys in the same shape — `eventId, service, eventType, category, region, availabilityZone, startTime, endTime, status, description, affectedResources[{resourceId,tags,status}], sourceAccountId, rawEvent, ingestedAt` — and sets no `priority`. The inline test asserts the exact sorted key set equals `CONTRACT_KEYS` and that `priority` is absent, locking AC-4. The state machine passes the whole object through as `healthEvent`, and the Investigation Trigger reads only these fields, so the finding path satisfies the downstream contract.

The only cross-path difference is `region` (`event.region || detail.region` for Health vs `event.region` for findings). This is intentional per design §2.1 (the ASFF per-resource region may vary; the envelope region is authoritative) and does not change the key set or break any consumer.

### Severity/status filtering, fan-out, and dedup

The authoritative per-finding gate in `handleSecurityHubEvent` is `(severity === 'HIGH' || 'CRITICAL') && Workflow.Status === 'NEW' && RecordState === 'ACTIVE'`, with non-qualifying findings skipped (debug log) and Id-less findings skipped (warn log). One `StartExecutionCommand` is issued per surviving finding; a zero-qualifying batch returns `{ statusCode: 200, startedExecutions: [] }` with no throw — matching the "zero findings is success, no DLQ" requirement. The inline tests exercise LOW, non-NEW, and ARCHIVED drops and the zero-qualifying case against the fixture.

Dedup is a conditional `PutItem` on `findingId` with `attribute_not_exists(findingId)`, written before `StartExecution`. `ConditionalCheckFailedException` (detected via a name check) → skip; any other error on a qualifying finding is rethrown so the async-retry/DLQ path engages, and re-delivery is idempotent because already-written keys make re-processed findings a no-op. The TTL is a single named constant (`DEDUP_TTL_SECONDS = 21600`) with a comment. This is correct and the tradeoffs (HIGH→CRITICAL escalation and no-impact-then-topology-change suppression within the window) are documented in both the table comment and the integration doc.

### EventBridge pattern

The rule renders `source: ['aws.securityhub']`, `detailType: ['Security Hub Findings - Imported']`, and the nested `detail.findings.{Severity.Label, Workflow.Status, RecordState}` literally — CDK does not transform the nesting. Verification confirms synth accepted the nested array-element pattern (the FR-1a fallback was not needed) and the rendered template matches. The rule targets the same Router with the same `retryAttempts: 185` / `maxEventAge: 24h` convention as the Health rule, and `aws.securityhub` is disjoint from `aws.health`, so no event is double-delivered.

### SKILL.md additive change and output contract

The diff touches only the frontmatter description, a "Which path to run" note at the top of the body, and a new `## Security Hub Finding Impact Assessment` section placed after Step 6 and before `## Output Format`. The heading inventory confirms the six parser headings each appear once and unrenamed, and `## Jira Tracking` is two words / title case as the design reconciled (the step brief's "JIRA TRACKING" was a paraphrase). The callback parser (`investigation-callback/index.ts`) matches `## Summary` and `## Key Findings`; both are preserved verbatim and the security path explicitly reuses the existing Output Format / Heading rules / no-impact blocks, so the callback parser is unaffected. The Jira sub-step is reused verbatim (same `[Health]` prefix, `["aws-health-event","auto-created"]` labels, same JQL), so the de-dup contract is untouched.

### IAM, CDK conventions, and Health-path isolation

`findingDedupTable.grant(this.eventRouter, 'dynamodb:PutItem')` produces a table-ARN-scoped statement; the suite's wildcard-guard test asserts a `dynamodb:PutItem` statement exists and that no such statement uses `Resource: '*'`. The dedup table mirrors `health-analyzer-task-tokens` (PAY_PER_REQUEST, TTL on `ttl`, PITR, production RETAIN + deletion protection), and `isProduction = props.deployEnvironment === 'production'` matches the workflow construct. The Router's existing `grantStartExecution` is unchanged, and the Health rule is left intact (asserted by a dedicated test). No downstream Lambda or the state machine was modified.

### Investigation Trigger tag

`buildDescription` gains one additive line: `eventTypeTag = service === 'SecurityHub' ? '[EVENT_TYPE:securityhub-finding]\n' : ''`, inserted between `[INVESTIGATION_ID:…]` and `${jiraTag}`. For Health events it is `''`, so the Health prompt is byte-identical and the existing correlation-tag and Secrets-Manager tests still pass (per verification). No new interface field was added — the tag is gated solely on the existing `service` contract key.

### Test fixture and verification

`events/test-securityhub-findings-event.json` is a realistic `Security Hub Findings - Imported` envelope with a CRITICAL Lambda finding, a HIGH EC2 security-group finding (both NEW/ACTIVE with real ARNs and Team/Environment tags), and a non-qualifying LOW/ARCHIVED/RESOLVED S3 finding whose description states its purpose. The old `test-security-event.json` was renamed via `git mv` to `test-iam-security-health-event.json` (content unchanged, still an `aws.health` IAM event). Verification records `npm run bundle`, `npm run build`, `npm test` (8 suites / 286 tests green including the new file), and `npm run cdk -- synth` all passing, with the new rule, dedup table, and scoped IAM confirmed in the synthesized template. Evidence is specific and consistent with the diff; no re-run was necessary.

</details>

<details>
<summary>File map</summary>

- `infrastructure/cdk/lambda/event-router/index.ts` — `source` dispatch; `handleHealthEvent` (unchanged body) + new `handleSecurityHubEvent`, `normalizeFinding`, `buildExecutionName`, ASFF types, dedup client/const.
- `infrastructure/cdk/lambda/investigation-trigger/index.ts` — additive `[EVENT_TYPE:securityhub-finding]` tag in `buildDescription`.
- `infrastructure/cdk/lib/constructs/event-ingestion.ts` — new EventBridge rule, dedup DynamoDB table, scoped `dynamodb:PutItem` grant, `FINDING_DEDUP_TABLE` env var.
- `devops-agent-skill/SKILL.md` — frontmatter tweak, "Which path to run" note, new `## Security Hub Finding Impact Assessment` section; parser contract unchanged.
- `events/test-securityhub-findings-event.json` — new ASFF fixture.
- `events/test-security-event.json` → `events/test-iam-security-health-event.json` — rename only.
- `test/securityhub-ingestion.test.ts` — new CDK + inline Router-logic tests.
- `ARCHITECTURE.md` — Security Hub path added to diagram, component table, data flow.
- `docs/security-hub-integration.md` — new technical doc.

Full diff: `git -C <worktree> -P diff main`.

</details>
