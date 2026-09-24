# Security FAQ

Frequently asked security questions for engineers and administrators evaluating whether to deploy this assistant in their environment.

This document describes the assistant's security posture as of the commit referenced below. It is derived from source — every claim points to a specific file or CDK construct you can inspect and verify yourself. It is not a compliance certification or a threat model, and it does not substitute for your own security review.

_Last updated against commit `705505a` — verify current source against the repository before relying on these answers._

---

## A. What the tool reads

### 1. What AWS APIs does the tool call, and on which services?

The assistant makes read-only calls to five AWS services (plus Amazon Bedrock for the LLM and AWS Lambda for internal fan-out):

| Service | Operations |
|---|---|
| **Security Hub** | `GetFindings`, `DescribeHub`, `ListEnabledProductsForImport` |
| **IAM Access Analyzer** | `ValidatePolicy`, `CheckAccessNotGranted`, `ListAnalyzers` |
| **CloudTrail** | `LookupEvents` |
| **IAM** | `GetRole`, `GetPolicy`, `GetPolicyVersion`, `GetRolePolicy`, `GetUser`, `GetUserPolicy`, `GetGroupPolicy`, `GetAccountSummary`, `GetAccessKeyLastUsed`, `ListRoles`, `ListPolicies`, `ListAttachedRolePolicies`, `ListRolePolicies`, `ListEntitiesForPolicy`, `ListUsers`, `ListAttachedUserPolicies`, `ListUserPolicies`, `ListGroupsForUser`, `ListAttachedGroupPolicies`, `ListGroupPolicies`, `ListAccessKeys` |
| **S3** | `PutObject`, `ListObjectsV2`, `HeadObject`, `GetObject` — scoped to the tool's own reports bucket only |
| **Amazon Bedrock** | `Converse` (LLM inference) |
| **AWS Lambda** | `Invoke` — conversation handler calling the 10 tool functions |

**Verify:** every `boto3.client(...)` in `src/tools/*.py`, `src/agent.py`, `src/capabilities.py`, `src/download.py`.

### 2. Does the tool access any resource outside the deployment account?

**No.** There is no `sts:AssumeRole` call anywhere in the code, no hardcoded external account IDs, and no cross-account ARN references. Every AWS API call executes against the deployment account using the Lambda's own execution role.

**Verify:** full-tree grep for `assume_role` and `boto3.client("sts")` returns zero hits.

### 3. What IAM permissions does the tool's execution role require?

Four distinct execution roles, each least-privileged for its function:

- **`ToolExecutionRole`** (shared by the 10 tool Lambdas, `infrastructure/cdk/stacks/tools_construct.py:31-95`) — read-only IAM / Security Hub / Access Analyzer / CloudTrail (the operations in Q1), plus S3 read/write scoped to the reports bucket only.
- **`ConversationHandler` role** (`api_construct.py:38-78`) — `bedrock:InvokeModel` on the Anthropic Claude family only, plus `lambda:InvokeFunction` scoped to the 10 tool Lambda ARNs.
- **`CapabilitiesProbe` role** (`api_construct.py:83-121`) — the read-only control-plane calls used by the session-start capability probe.
- **`DownloadHandler` role** (`api_construct.py:191-215`) — S3 read on the reports bucket only. No list, no write.

No `iam:PassRole`, no `iam:Simulate*`, no `Create*` / `Put*` / `Update*` / `Delete*` / `Attach*` / `Detach*` on IAM anywhere. `resources=["*"]` appears on the tool role for control-plane calls (IAM read, Security Hub, Access Analyzer, CloudTrail) — this is unavoidable because AWS does not support resource-level authorization for those specific operations; see the justification comment at `tools_construct.py:41-47`.

## B. What the tool writes

### 4. Does the tool modify any IAM roles, policies, or user configurations?

**No.** There are zero IAM writes anywhere in the codebase. Zero Security Hub writes. Zero Access Analyzer writes. The assistant is strictly advisory — it recommends changes and can generate policy documents and change requests, but it never applies them, deactivates keys, deletes users, or attaches/detaches policies.

### 5. What AWS resources does deploying the tool create in my account?

A single CDK stack (`IamAnalyzerAssistantStack-{region}`) creates:

- **Cognito** — 1 User Pool, 1 User Pool Client, 1 Identity Pool.
- **S3** — 2 buckets: the reports bucket (SSE-S3, `BlockPublicAccess.BLOCK_ALL`, `enforce_ssl=True`, versioned, 90-day lifecycle) and the frontend hosting bucket (SSE-S3, `BlockPublicAccess.BLOCK_ALL`).
- **CloudFront** — 1 distribution fronting the hosting bucket via Origin Access Control (SIGV4). Viewer policy `REDIRECT_TO_HTTPS`.
- **API Gateway** — 1 REST API with a Cognito authorizer, 4 routes: `POST /conversation`, `GET /conversations`, `GET /capabilities`, `GET /downloads/{proxy+}`.
- **Lambda** — 13 functions: 10 tool Lambdas plus conversation handler, capabilities probe, and download handler. All Python 3.14. None VPC-attached.
- **IAM** — 2 authored roles (`ToolExecutionRole` plus a legacy `PresignerRole` marked deprecated) plus 3 CDK-generated Lambda execution roles.

**Not deployed:** No DynamoDB. Conversation history is not persisted. No customer-managed KMS keys. No Secrets Manager or Parameter Store secrets. No AWS WAF on CloudFront or API Gateway. No VPC.

**Verify:** `infrastructure/cdk/stacks/*.py`.

## C. Where data goes

### 6. Does customer data leave the deployment account?

Only for Amazon Bedrock. Bedrock is Amazon-owned and stays within AWS, but the default model ID is a **US cross-region inference profile** (`us.anthropic.claude-sonnet-4-5-...`), which means Bedrock may transparently route a single invocation to any US region in the profile — so requests are not pinned to a single region. Every other data-plane call (Security Hub, IAM, Access Analyzer, CloudTrail, S3, Lambda) stays in the deployment account and region. There are no third-party HTTP calls anywhere in the code. If regional-residency is a requirement, swap the model ID to a single-region variant in `infrastructure/cdk/stacks/api_construct.py:47`.

### 7. What data is sent to Amazon Bedrock, and in what region?

Sent on every turn: the assistant's system prompt (static English, no customer data), the last 8 messages of conversation history (2000-char cap per message), the current user prompt, and **all tool outputs from this turn as structured JSON** — which includes IAM role names, user names, ARNs, resolved policy documents, CloudTrail event snippets, and account IDs pulled by the tools. Maximum of 2 tool rounds per turn.

Region: no explicit region is set — the Bedrock client uses the Lambda's runtime region. However, because the model ID uses a US cross-region inference profile, Bedrock may execute the invocation in any US region within the profile. See Q6 for regional-residency implications.

## D. Access to the assistant itself

### 8. Who can use the deployed assistant, and how is authentication configured?

As shipped, the assistant uses a standalone Cognito User Pool with defaults intentionally set for demo and pilot deployments:

- **Self-signup is enabled** — anyone with the sign-up URL can create an account.
- **MFA is not required.**
- Password policy: minimum length 8, uppercase / lowercase / digit required, symbols optional.
- Sign-in flows: `USER_SRP` and `USER_PASSWORD` (email + password).
- **No external identity provider (SAML, OIDC, or IAM Identity Center) is configured.**

Every API Gateway route is protected by the Cognito User Pool authorizer — there is no unauthenticated access to any endpoint.

**For hardened deployments**, edit `infrastructure/cdk/stacks/auth_construct.py` to:
1. Set `self_sign_up_enabled=False`.
2. Set `mfa=cognito.Mfa.REQUIRED`.
3. Add a `cognito.UserPoolIdentityProviderSaml` or `UserPoolIdentityProviderOidc` to federate with your existing IdP (or IAM Identity Center).

The README walks through each step in the "Identity & Authentication (Hardened Deployments)" section.

## E. Infrastructure security

### 9. Is data encrypted at rest and in transit?

**At rest:**
- Reports bucket: SSE-S3 (AWS-managed keys), `BlockPublicAccess.BLOCK_ALL`, `enforce_ssl=True` bucket policy denying non-TLS access, versioning enabled.
- Frontend hosting bucket: SSE-S3, `BlockPublicAccess.BLOCK_ALL`. Access is fronted by CloudFront plus Origin Access Control — no direct public access.
- No customer-managed KMS keys are provisioned. For SSE-KMS with a CMK, replace `s3.BucketEncryption.S3_MANAGED` with `s3.BucketEncryption.KMS` in `storage_construct.py`.

**In transit:**
- CloudFront: `viewer_protocol_policy=REDIRECT_TO_HTTPS`. HTTP is redirected. Uses the default CloudFront certificate and the platform-default minimum TLS version (`TLSv1`). Pin to `TLSv1.2_2021` or better in `frontend_construct.py` for stricter posture.
- API Gateway: HTTPS-only by default (execute-api endpoints are TLS).
- All AWS SDK calls (Bedrock, S3, IAM, etc.) use HTTPS via boto3 defaults.

## F. Compliance and audit

### 10. Are all tool actions logged in CloudTrail?

Yes — every AWS API call the tool makes goes through the Lambda's execution role, so all calls appear in CloudTrail with the role ARN as the identity, the tool Lambda's function ARN as the invoker, and the target resource ARN. This lets a security engineer audit exactly what the assistant read, when, and on whose behalf.

Additionally, each Lambda emits CloudWatch Logs. Logged content includes: tool names, tool inputs (which may include IAM role names, ARNs, and filters), S3 export keys (which encode target role names), and AWS `ClientError` messages. **Not logged:** raw user prompts, Bedrock request or response bodies, or exported policy content. No PII redaction is applied — if a user includes sensitive strings in their prompt they will not appear in CloudWatch, but tool inputs are logged verbatim.

## G. Offboarding

### 11. How do I fully remove the tool from my account? Does anything stay behind?

Run `cdk destroy IamAnalyzerAssistantStack-{region}` from `infrastructure/cdk/`. Because both S3 buckets are provisioned with `RemovalPolicy.DESTROY` and `auto_delete_objects=True`, the destroy operation removes everything the stack created — including all exports and the frontend assets.

**What stays behind:**
- CloudWatch Log Groups (Lambda logs) — retained per your account default. Delete manually via `aws logs delete-log-group` if desired.
- CloudTrail events — permanent record per your CloudTrail retention.

**Hardening note:** `RemovalPolicy.DESTROY` with `auto_delete_objects=True` is fine for evaluation but wrong for deployments carrying data you want to survive a stack error. Change to `RemovalPolicy.RETAIN` in `storage_construct.py` before running under real workload data.

## H. Cost and quotas

### 12. What drives cost? What order of magnitude for typical use?

Dominant cost is **Amazon Bedrock token usage**. Each customer turn issues up to 2 Bedrock `Converse` calls (planner plus synthesizer per tool round), each capped at 2048 output tokens. Realistic session cost with Claude Sonnet 4.5 at published token pricing is roughly a few cents per turn, though this depends entirely on input size (conversation history, tool outputs) and how many tool rounds fire.

Secondary costs are small and mostly fixed:
- Lambda invocations (13 functions, mostly sub-second cold starts amortized quickly)
- API Gateway requests
- S3 storage for exports (bounded by the 90-day lifecycle rule)
- CloudFront viewer requests (frontend assets)

No cost-generating resources run 24/7 — everything is on-demand except the reports bucket and the frontend distribution. Steady-state cost for a small workgroup with light-to-moderate usage typically lands in the low-dollar-per-month range plus Bedrock tokens.
