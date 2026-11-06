# Access-key triage — analysis criteria

**Source of truth for `src/tools/triage_access_keys.py`.** If the Python code
and this document disagree, the discrepancy is a bug in one of the two.

---

## What the collector emits

The `triage_access_keys` tool returns one row per access key. Each row carries
a `risk_flags` list (machine-computed, deterministic — never re-derived in the
prompt), a `priority_class`, and a `suggested_remediation`. The prompt's job
is to present those faithfully, not to re-rank them.

**Read scope and conditions before judging severity.** The `actions` field is
per-statement with resources and conditions inline in brackets — the shape is
`<actions> [<resources> | if <condition>]`. Use the `resource_scope` field
(`WILDCARD` / `SCOPED` / `MIXED` / `NONE`) and `has_condition` (bool) as
first-class signals:

- The **same action is far riskier on `Resource: *` than on a narrow ARN**.
  A `SCOPED` key with write actions on one bucket is usually low risk;
  `WILDCARD` or `MIXED` raises it.
- **Conditions can meaningfully constrain** an otherwise-broad grant — for
  example `aws:CalledVia`, `aws:PassedToService`, source-IP, or tag
  conditions. Factor them in; do not ignore them.
- `ADMIN` (full `*`) overrides all of this — always Critical regardless of
  scope.

## Risk flags

| Flag | Meaning | Severity |
|---|---|---|
| `ADMIN` | Any Allow statement grants `Action: *`, OR AWS-managed `AdministratorAccess` is attached | 🔴 Critical — full account control on a static key |
| `BROAD:<policy>` | An AWS-managed broad policy is attached: `AmazonBedrockFullAccess`, `AmazonS3FullAccess`, `AmazonAthenaFullAccess`, `PowerUserAccess`, `IAMFullAccess`, `AmazonEC2FullAccess`, `ReadOnlyAccess`, `AmazonDynamoDBFullAccess`, `AWSLambda_FullAccess` | 🟠 High |
| `SERVICE_WILDCARD:<svc>` | Any Effective_Policy action equals `<svc>:*` (e.g. `bedrock:*`, `s3:*`) | 🟠 High |
| `RESOURCE_WILDCARD` | At least one Allow statement is on `Resource: *`. Cross-check the `actions` field — broad **write/admin** actions on `*` are dangerous; list/describe on `*` are usually benign | 🟠 Context-dependent |
| `NEVER_USED` | `iam:GetAccessKeyLastUsed` returned no `LastUsedDate` for this key | 🟢 Cleanup — consider deleting once confirmed truly unused |
| `IDLE_<n>d` | `LastUsedDate` exists but the key has not been used for `n > 90` days | 🟢 Cleanup — consider deactivate → monitor → delete |
| `KEY_AGE_<n>d(>1yr)` | Key created ≥ 365 days ago (never rotated) | 🟡 Rotation / migration priority |
| `MULTI_ACTIVE_KEYS` | User has two or more keys with `Status: Active` | 🟡 Incomplete rotation |
| `LASTUSED_UNKNOWN` | `iam:GetAccessKeyLastUsed` raised (throttled, permission denied, service outage). Set **instead of** `NEVER_USED` or `IDLE_*` so a "could not check" answer is not silently reported as "confirmed unused" | ⚪ Coverage gap |

## Prioritization

Rank by combination, worst first:

1. **`ADMIN` + (`IDLE_*` OR `KEY_AGE_*(>1yr)` OR `NEVER_USED`)** — stale or
   forgotten admin keys. The top breach vector.
2. **`ADMIN`** actively used — admin on a live static key. Strong candidate
   for migration.
3. **`BROAD:*` / `SERVICE_WILDCARD:*`** — over-permissioned. Note that
   `AmazonBedrockFullAccess` = `bedrock:*` implies cost abuse (provisioned
   throughput, customization jobs) plus data-exposure risk (invocation-log
   redirect), not just inference.
4. **`NEVER_USED` / `IDLE_*`** on non-admin, non-broad keys — low-risk
   cleanup.
5. **`KEY_AGE_*(>1yr)` / `MULTI_ACTIVE_KEYS`** — rotation hygiene.

The Python code assigns each row exactly one `priority_class` — `Critical`,
`High`, `Cleanup`, or `Rotation` — and sorts the response by
`priority_class`, then by descending `key_age_days` inside each class.

## Remediation mapping

The tool suggests a specific action-off-static-keys path per identity pattern.
Pattern detection is a regex on the IAM user name, with a safe default when
nothing matches:

| Pattern | Suggested_Remediation |
|---|---|
| Human user (contains `@`, dotted first-name.last-name, etc.) | `SSO_Federation` — federate via the customer's SSO provider (SAML, OIDC, or IAM Identity Center); temporary credentials; scope permission sets, never admin |
| AWS-hosted service (`svc-`, `service-`, `-lambda-`, `-eks-`, `-ecs-`, `-ec2-`, `-runner`, `-worker`, `-agent`) | `IAM_Role` — IRSA / Pod Identity / instance / task / execution role |
| CI/CD (`-ci-`, `-cicd-`, `-deployer-`, `-github-actions-`, `-gitlab-`, `-jenkins-`) | `OIDC_Federation` — OIDC federation with the CI/CD provider |
| External SaaS / cross-cloud connector — anything else that does not match above | `Cross_Account_Role_With_External_Id` — a safe default that requires investigation; the third party assumes the role with an external ID |
| On-prem (annotated as such in prose; regex heuristics cannot detect this) | `IAM_Roles_Anywhere` — mentioned in the tool's `usage_lag_caveat` prose so the user can recognize this pattern when the mapping falls back to the default |
| The AWS account **root user** | `Remove_Root_Access_Keys` — Critical, always, regardless of other flags |

**Keys that exist for a technical constraint.** Some long-lived keys are
deliberate — for example, generating presigned URLs that must stay valid
longer than a temporary credential's max session (temp ≤ 12 h vs SigV4's
7-day max). Investigate the reason before assuming neglect; the fix may be
architectural (a re-issuing service, shorter TTLs), not a naive role swap.

## Safety rules for recommendations

These rules are encoded both in the tool's response text and in the
`ACCESS KEY TRIAGE` section of the assistant system prompt:

- **Frame every remediation as a recommendation, not a directive.** Use
  cautious, advisory language — "consider", "candidate for", "recommend",
  "suggest reviewing" — rather than imperatives like "delete" or
  "remove now". The customer or resource owner decides and executes; the
  assistant's job is to surface and advise.
- **Verify before suggesting removal.** Confirm a key is genuinely unused
  (last-used data can lag; a low-frequency workload may back a rare-but-
  critical job that appears idle) and check with the resource owner before
  recommending any change.
- **Never recommend deleting an in-use key outright.** Always suggest
  **deactivate → monitor a full business cycle → delete**.
- **Root access keys** are always Critical → recommend removing them (the
  root user should have none).
- **GovCloud / FedRAMP / mil** accounts are a separate authorization
  regime; flag them and do not assume commercial entitlement.

## Coverage contract

The tool emits `coverage` entries per `#171`'s `{source, state, detail,
count?}` shape:

- `iam` — the top-level user + key inventory (`iam:ListUsers`,
  `iam:ListAccessKeys`, `iam:GetAccessKeyLastUsed` in aggregate,
  `iam:GetAccountSummary` for the root-user special case).
- `iam-policy-resolution` — the effective-policy walk
  (`iam:GetPolicy`, `iam:GetPolicyVersion`, `iam:GetUserPolicy`,
  `iam:ListGroupsForUser` and group traversal).

Per-key `LASTUSED_UNKNOWN` is **not** a top-level coverage state — it is a
risk flag on the individual row so the operator can distinguish "we don't
know when this was last used" from "we know it was never used".
