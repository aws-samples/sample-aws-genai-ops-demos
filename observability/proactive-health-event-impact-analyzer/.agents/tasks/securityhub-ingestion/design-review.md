# Design Review — Security Hub Findings Ingestion (revision pass)

Reviewed: `design.md` (revision pass responding to the prior 2 HIGH / 3 MEDIUM / 4 NIT)
against `requirements.md` and the actual source under the feature worktree
`c:\Users\umancini\sviluppi-new\sample-aws-genai-ops-demos\.worktrees\securityhub-ingestion\observability\proactive-health-event-impact-analyzer`.

This review was performed fresh, reading the real source rather than trusting the design's claims.
Every claim the step brief flagged as high-risk (the internal normalized-event contract, the SKILL.md
output section names the callback parser depends on, the per-finding fan-out / dedup / IAM scoping, the
EventBridge nested-array filtering, and preservation of the existing Health path) was verified against
source.

**Verdict: APPROVED** — zero HIGH, zero MEDIUM. Two NITs are recorded below; neither blocks.

---

## Findings

1. **NIT — §6.3 overstates which sections the Callback parser consumes.**
   §6.3 says the new path "MUST emit, verbatim, the headings the Callback parser
   (`investigation-callback/index.ts :: parseAgentAnalysis`) depends on" and then lists all six headings
   including `## Notification Routing` and `## Jira Tracking`. Verified against source: `parseAgentAnalysis`
   only pattern-matches `## Summary`, `## Key Findings`, `## Answers to Your Questions`,
   `## Recommended Actions` (also `## Actions` / `### 4.`), and the no-impact keyword set. It does **not**
   parse `## Notification Routing` or `## Jira Tracking` at all — those are output-contract sections the
   skill emits for humans/Jira, not parser inputs.
   This does not change behavior: the design reuses all six existing SKILL.md sections verbatim, and
   `requirements.md` itself lists the same six as "verified section headings verbatim," so the net
   instruction to the implementer is correct.
   CONCRETE FIX: in §6.3, change "the headings the Callback parser depends on" to "the existing SKILL.md
   output-contract headings — of which `## Summary`, `## Key Findings`, `## Answers to Your Questions`, and
   `## Recommended Actions` are parsed by `parseAgentAnalysis`, while `## Notification Routing` and
   `## Jira Tracking` are emitted for downstream consumers." No section list change is needed.

2. **NIT — §2 field table omits that `interface HealthEvent` does not read `rawEvent`, and `sourceAccountId`
   is optional in that interface.**
   §2 lists the Trigger's read fields and states "`rawEvent` is carried but not read by the Trigger," which
   is correct. For completeness: verified in `investigation-trigger/index.ts` that `interface HealthEvent`
   has no `rawEvent` key and declares `sourceAccountId?` (optional). The normalizer still must emit
   `rawEvent` because it is part of the Health branch's `workflowInput` key set and the design's "exactly
   these keys" invariant (AC-4) includes it. The design is internally consistent; this is only a
   documentation precision note.
   CONCRETE FIX: add one clause to §2 noting `interface HealthEvent` declares `sourceAccountId` optional and
   has no `rawEvent` member, so emitting `rawEvent` satisfies the Router/workflowInput key set without being
   consumed by the Trigger — purely to pre-empt an implementer "trimming" `rawEvent`.

---

## Verified Assumptions (checked against source)

- **Internal normalized-event contract (§2).** `event-router/index.ts` Health branch emits exactly
  `eventId, service, eventType, category, region, availabilityZone, startTime, endTime, status,
  description, affectedResources:[{resourceId,tags,status}], sourceAccountId, rawEvent, ingestedAt`. The
  design's §2 key set matches byte-for-byte. CONFIRMED.
- **`TriggerInvestigation` passes the whole object (§2).** `investigation-workflow.ts` →
  `payload: sfn.TaskInput.fromObject({ 'taskToken': sfn.JsonPath.taskToken, 'healthEvent.$': '$' })`. The
  design's claim that `workflowInput` *is* `healthEvent` is correct. CONFIRMED.
- **Error-catch Pass states read `$.eventId` as `healthEventArn` (§2).** All three
  Catch Pass states use `'healthEventArn.$': '$.eventId'`. CONFIRMED.
- **`mapCategoryToPriority` mapping (§2.2).** `issue→CRITICAL`, `scheduledChange→HIGH`,
  `accountNotification→MEDIUM`, default→`MEDIUM`. The §2.2 severity→category mapping (CRITICAL→`issue`,
  HIGH→`scheduledChange`) reuses these unchanged. CONFIRMED.
- **Incident title / incidentId shape (§4.3, §7).** Trigger builds
  `incidentId = health-${service}-${Date.now()}-${randomHex}` and
  title `[${incidentId}] AWS Health: ${service} ${eventType} in ${region}`. For `service === 'SecurityHub'`
  this yields `health-SecurityHub-...` and a title starting `[health-SecurityHub-...]`. The §4.3/§7
  correlation reasoning (title-fallback `startsWith('health-')` would match but is never reached because
  `[INVESTIGATION_ID]` is matched first) is accurate: `extractCorrelationKey` checks `[INVESTIGATION_ID:`
  first, then `[CORRELATION_ID:`, then the title fallback gated on `startsWith('health-')`. CONFIRMED.
- **`buildDescription` maintenance-window line (§2.1).** Emits a literal
  `Maintenance Window: ${maintenanceWindow}`; `maintenanceWindow` is `'Not specified'` when
  `startTime`/`endTime` are null. The §2.1 decision (both null → `Maintenance Window: Not specified`) is
  correct. CONFIRMED.
- **SKILL.md output section names (§6.3), verbatim.** `## Summary`, `## Key Findings`,
  `## Answers to Your Questions`, `## Recommended Actions`, `## Notification Routing`, `## Jira Tracking`
  (two words, title case) all appear exactly in `SKILL.md`'s Output Format and Heading rules. CONFIRMED.
- **Jira Step 6 contract (§6.4), verbatim.** `SKILL.md` Step 6 uses summary prefix
  `[Health] <eventTypeCode> — <severity>`, labels `["aws-health-event", "auto-created"]`, and the JQL
  `labels = "aws-health-event" AND text ~ "<eventArn or eventTypeCode>" AND statusCategory != Done`, with
  the "do NOT call ssm:GetParameter" rule and MEDIUM+ threshold. §6.4's reuse-verbatim decision quotes these
  correctly. CONFIRMED.
- **No-impact keywords (§6.5).** `SKILL.md` and the callback both reference
  `no operational impact` / `no immediate operational` / `no workloads` (the callback list also includes
  `no current operational impact` / `not used by any`). The three the design quotes exist. CONFIRMED.
- **`parseAgentAnalysis` parser regexes.** `## Summary`, `## Key Findings`, `## Answers to Your Questions`,
  `## Recommended Actions`/`## Actions`/`### 4.` and the `**P1..P4**` table first column, plus the
  no-impact keyword scoring, all match the design's described contract. CONFIRMED (see NIT #1 for the one
  overstatement).
- **jest config (§8, §11.3).** `jest.config.js` sets `roots: ['<rootDir>/../../test']` and
  `moduleNameMapper '^@aws-sdk/(.*)$' → '<rootDir>/node_modules/@aws-sdk/$1'`, confirming the new test MUST
  live in the demo-root `test/` folder (same dir as `lambda-logic.test.ts`). CONFIRMED (resolves prior HIGH #1).
- **No `@aws-sdk` clients in node_modules (§1, §11.3).** `infrastructure/cdk/node_modules/@aws-sdk/` does
  not exist — neither `client-sfn` nor `client-dynamodb` is installed. CONFIRMED (resolves prior HIGH #2 /
  MEDIUM #3).
- **`tsconfig.json` excludes `lambda` (§1).** `"exclude": ["node_modules","build","lambda"]`. CONFIRMED.
- **esbuild marks `@aws-sdk/*` external for `event-router` (§1).** `scripts/bundle-lambdas.js` lists
  `event-router` with `external: SDK_PROVIDED_BY_RUNTIME = ['@aws-sdk/*']`. Adding a `@aws-sdk/client-dynamodb`
  import to the Router therefore will not break `npm run bundle`. CONFIRMED.
- **`lambda-logic.test.ts` inline-reimplementation pattern (§11.3).** The test defines `extractTag` /
  `extractCorrelationKey` inline and imports only `fs`/`path`; it never imports a handler module. The
  design's hard constraint ("new Router-logic tests MUST NOT import the handler") matches the established
  pattern. CONFIRMED.
- **DynamoDB wildcard-guard test (§5.4).** `health-event-analyzer.test.ts` ("DynamoDB access uses
  grantReadData/grantReadWriteData", Req 1.6) asserts every statement with a `dynamodb:` action has
  `Resource !== '*'`. `findingDedupTable.grant(this.eventRouter, 'dynamodb:PutItem')` produces a
  table-ARN-scoped statement, satisfying this. CONFIRMED.
- **`EventIngestion` already receives `deployEnvironment` and `alarmTopic` (§5.2, §8).** `EventIngestionProps`
  declares `deployEnvironment: string` and `alarmTopic: sns.ITopic`; `isProduction` can be derived locally
  exactly as `InvestigationWorkflow` does (`props.deployEnvironment === 'production'`). The new rule and
  dedup table live inside the already-instantiated `EventIngestion` construct, so no stack-wiring change is
  required. CONFIRMED.
- **`HealthEventRule` target convention (§3.1).** `event-ingestion.ts` targets the Router with
  `retryAttempts: 185, maxEventAge: cdk.Duration.hours(24)`; the new rule copies this. CONFIRMED.
- **Health-path isolation / disjoint sources (§3.2, §4.1, §13).** `HealthEventRule` matches `aws.health`;
  the new rule matches `aws.securityhub` — disjoint, no double delivery (FR-2/AC-6). The Router branch is
  additive (`if source === 'aws.securityhub'` else existing). CONFIRMED.
- **OI-5 misnamed fixture (§9).** `events/test-security-event.json` holds an IAM *Health* event
  (`source: aws.health`, `detail-type: AWS Health Event`), confirming the rename-not-repurpose decision is
  correctly grounded. CONFIRMED.

## Unverified / Wrong Assumptions

- **EventBridge nested-array content filtering inside `detail.findings[]` (§3.1, §3.2).** NOT verifiable from
  the repository, and the design does not claim it is. It is correctly marked **implementation-time-verified**
  per FR-1a/OI-1, with (a) a cited AWS "Matching with arrays" any-element basis, (b) an explicit deploy-time
  fallback (drop any non-expressible leaf from the pattern, keeping `source`+`detail-type`+expressible leaves
  and relying on the authoritative Router per-finding filter), and (c) a synth-test assertion on the rendered
  nested `detail` shape. Because the Router filter is the authoritative gate, correctness does not depend on
  the in-pattern filter succeeding. This is the correct way to handle an unverifiable external behavior; it is
  not a blocking gap. The implementer must still perform the `cdk synth`/deploy verification the design calls
  for.
