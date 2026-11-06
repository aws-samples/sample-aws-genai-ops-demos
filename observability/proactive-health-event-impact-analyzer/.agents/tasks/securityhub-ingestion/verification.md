# Verification — Security Hub Findings Ingestion

Feature implemented in the worktree:
`c:\Users\umancini\sviluppi-new\sample-aws-genai-ops-demos\.worktrees\securityhub-ingestion\observability\proactive-health-event-impact-analyzer`
Branch: `feature/securityhub-ingestion`. First iteration (no `review.json` present).

All commands run from
`.../.worktrees/securityhub-ingestion/observability/proactive-health-event-impact-analyzer/infrastructure/cdk`.
No deployment and no live AWS calls were made.

## Commands run and results

| Step | Command | Result |
|---|---|---|
| Install (node_modules was missing) | `npm install` | exit 0 — 332 packages added |
| Bundle Lambdas | `npm run bundle` | exit 0 — all 6 handlers bundled, incl. modified `event-router` (8.1 KB) and `investigation-trigger` (12.7 KB). The new `@aws-sdk/client-dynamodb` import in event-router is marked external, so bundling succeeds without that package on disk. |
| TypeScript build | `npm run build` (`tsc`) | exit 0 — no type errors across CDK + (non-excluded) sources |
| Full test suite | `npm test` (jest; pretest re-bundles) | exit 0 — **8 suites, 286 tests, all passing**, including the new `test/securityhub-ingestion.test.ts`. Existing Health rule / IAM-least-privilege / DynamoDB-not-wildcard / log-retention / category / severity / correlation tests all still green. |
| Synth | `npm run cdk -- synth` | exit 0 — "Successfully synthesized". Two stacks synthesized. |

(The "worker process failed to exit gracefully" line from jest is a pre-existing
teardown warning unrelated to this change; the suite still reports all green.)

## New resources confirmed in `cdk synth` output

Verified in `cdk.out/HealthEventAnalyzerStack-us-east-1.template.json`:

- **EventBridge rule** `EventIngestionSecurityHubFindingRule...`
  (`Name: health-analyzer-securityhub-capture`): `EventPattern.source = ["aws.securityhub"]`,
  `detail-type = ["Security Hub Findings - Imported"]`, and the nested
  `detail.findings.{Severity.Label:["HIGH","CRITICAL"], Workflow.Status:["NEW"], RecordState:["ACTIVE"]}`
  rendered exactly as designed. Synth accepted the nested array-element pattern,
  so the §3.2 FR-1a fallback was NOT needed.
- **DynamoDB table** `EventIngestionSecurityHubFindingDedupTable...`:
  `TableName: health-analyzer-securityhub-dedup`, PK `findingId` (HASH),
  TTL attribute `ttl` enabled, `BillingMode: PAY_PER_REQUEST`, PITR enabled.
- **IAM**: the Event Router role policy grants `dynamodb:PutItem` scoped to the
  dedup table ARN via `Fn::GetAtt [...DedupTable, "Arn"]` — NOT `Resource: "*"`.
  Confirmed by both the template and the suite's wildcard-guard test.

## Health path intact

- The existing `aws.health` EventBridge rule, the Health normalization branch in
  `event-router`, and the entire downstream workflow (`investigation-workflow.ts`,
  callback, opscenter-creator, notifier) are unchanged.
- The Investigation Trigger change is additive: `[EVENT_TYPE:securityhub-finding]`
  is emitted only when `service === 'SecurityHub'`; for Health events the tag is
  `''`, so the Health prompt is byte-identical. The existing
  "Description Correlation Tag" and Secrets-Manager source-string tests pass.
- `mapCategoryToPriority` was not modified.

## SKILL.md

- Added exactly one new top-level section `## Security Hub Finding Impact Assessment`
  (between Step 6 and `## Output Format`), a top-of-body "Which path to run" note,
  and a frontmatter description tweak.
- The parser contract sections `## Output Format`, `### Heading rules`, and
  `### What "no impact" looks like` are unchanged. The six parser headings
  (`## Summary`, `## Key Findings`, `## Answers to Your Questions`,
  `## Recommended Actions`, `## Notification Routing`, `## Jira Tracking`) appear
  once each in the fenced Output Format template and were not renamed.

## Fixtures

- `events/test-security-event.json` renamed via `git mv` to
  `events/test-iam-security-health-event.json` (content unchanged; it is an IAM
  Health event, `source: aws.health`).
- Created `events/test-securityhub-findings-event.json`: a
  `Security Hub Findings - Imported` envelope (`source: aws.securityhub`,
  `account: 123456789012`, `region: us-east-1`) with one CRITICAL + one HIGH
  qualifying finding and one non-qualifying LOW/ARCHIVED finding. Validated as
  parseable JSON.

## Docs

- `ARCHITECTURE.md`: Event Ingestion Layer table + diagram + Data Flow extended
  with the Security Hub rule, dedup table, and per-finding fan-out. Health
  description kept intact.
- Created `docs/security-hub-integration.md` (technical only; no Workshop Studio
  reference — confirmed).

## Cleanup

`dist/` and `cdk.out/` are gitignored; no scratch files were committed.
