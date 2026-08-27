# Architecture — AI Permission Boundary Generator

## Overview

The AI Permission Boundary Generator is a deploy-and-run tool that analyzes IAM usage patterns via CloudTrail, then leverages Amazon Bedrock (Claude) to produce least-privilege permission boundaries. The architecture is intentionally minimal: a single S3 bucket is deployed for artifact storage, while all analysis runs locally.

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              Local Machine                                        │
│                                                                                  │
│  ┌────────────────────┐    ┌─────────────────────┐    ┌──────────────────────┐  │
│  │ generate-boundaries│───▶│ cloudtrail_analyzer  │───▶│ policy_extractor     │  │
│  │ .sh                │    │ .py                  │    │ .py                  │  │
│  └────────────────────┘    └─────────────────────┘    └──────────────────────┘  │
│                                      │                           │               │
│                                      ▼                           ▼               │
│                            ┌─────────────────────┐    ┌──────────────────────┐  │
│                            │ boundary_generator   │───▶│ report_builder       │  │
│                            │ .py                  │    │ .py                  │  │
│                            └─────────────────────┘    └──────────────────────┘  │
│                                      │                           │               │
│                                      ▼                           ▼               │
│                            ┌─────────────────────┐    ┌──────────────────────┐  │
│                            │ iac_formatter        │    │ output/              │  │
│                            │ .py                  │───▶│ (local artifacts)    │  │
│                            └─────────────────────┘    └──────────────────────┘  │
│                                                                  │               │
└──────────────────────────────────────────────────────────────────┼───────────────┘
                                                                   │
                         ┌─────────────────────────────────────────┼──────────┐
                         │                 AWS Cloud                │          │
                         │                                         ▼          │
                         │  ┌───────────────┐  ┌──────────────────────────┐   │
                         │  │ CloudTrail    │  │ S3 Bucket               │   │
                         │  │ (read logs)   │  │ (artifact storage)      │   │
                         │  └───────────────┘  └──────────────────────────┘   │
                         │                                                    │
                         │  ┌───────────────┐  ┌──────────────────────────┐   │
                         │  │ IAM           │  │ Amazon Bedrock           │   │
                         │  │ (read policies)│  │ (Claude - generation)   │   │
                         │  └───────────────┘  └──────────────────────────┘   │
                         │                                                    │
                         └────────────────────────────────────────────────────┘
```

## CDK Stack

The CDK stack (`lib/boundary-stack.ts`) deploys a single resource:

| Resource | Type | Purpose |
|----------|------|---------|
| Artifact Bucket | `aws-s3::Bucket` | Stores generated boundary policies and analysis reports |

Bucket configuration:
- Versioning enabled (to track boundary policy iterations)
- Server-side encryption with S3-managed keys (SSE-S3)
- Block all public access
- Lifecycle rule: expire objects after 90 days

## Components

### generate-boundaries.sh / generate_boundaries.py

Entry point that orchestrates the analysis pipeline. Parses CLI arguments, invokes each stage sequentially, and handles errors.

### cloudtrail_analyzer.py

Queries the CloudTrail `LookupEvents` API for all events attributed to the target IAM principal over the specified time window. Extracts unique `eventName` values (API actions) and maps them to IAM permission strings.

### policy_extractor.py

Retrieves the complete permission set for the target principal:
- Inline policies (role/user)
- Attached managed policies (AWS and customer-managed)
- Permission boundaries (if any already exist)
- Group policies (for users)

Normalizes all permissions into a flat set of `service:action` strings.

### boundary_generator.py

Sends the analysis to Amazon Bedrock (Claude) with a structured prompt:
- Input: observed permissions, granted permissions, headroom percentage
- Output: a JSON IAM policy document representing the permission boundary
- Includes reasoning about why certain permissions are retained or excluded

### report_builder.py

Generates the before/after comparison report:
- Total granted permissions count
- Total observed (used) permissions count
- Boundary permissions count (observed + headroom)
- Attack surface reduction percentage
- List of permissions removed by the boundary

### iac_formatter.py

Converts the generated boundary policy JSON into deployable IaC:
- CDK TypeScript construct using `aws-iam::ManagedPolicy`
- CloudFormation YAML resource

## Data Flow

```
1. User invokes: ./generate-boundaries.sh --role-name MyAppRole --days 30
                              │
2. CloudTrail query:          ▼
   cloudtrail:LookupEvents ──▶ List of API actions used by MyAppRole
                              │
3. IAM policy retrieval:      ▼
   iam:Get*/List* ──────────▶ Complete set of granted permissions
                              │
4. Gap analysis:              ▼
   Used permissions ∪ Headroom vs. Granted permissions
                              │
5. Bedrock generation:        ▼
   bedrock:InvokeModel ─────▶ Least-privilege boundary policy JSON
                              │
6. Output formatting:         ▼
   Policy JSON ─────────────▶ CDK construct + CFN resource + report
                              │
7. Upload to S3:              ▼
   s3:PutObject ────────────▶ Artifacts stored in deployed bucket
```

## Security Design

### Principle of Least Privilege (for the tool itself)

The tool requires only read access to operate:

| Permission | Purpose |
|------------|---------|
| `cloudtrail:LookupEvents` | Read historical API activity |
| `iam:ListAttachedRolePolicies` | Enumerate attached managed policies |
| `iam:ListRolePolicies` | Enumerate inline policies |
| `iam:GetRolePolicy` | Read inline policy documents |
| `iam:GetPolicy` | Read managed policy metadata |
| `iam:GetPolicyVersion` | Read managed policy documents |
| `iam:ListGroupsForUser` | Enumerate user group memberships |
| `bedrock:InvokeModel` | Generate boundary via Claude |
| `s3:PutObject` | Upload artifacts to the output bucket |

### Data Handling

- CloudTrail data is processed in-memory and not persisted beyond the summary
- No credentials or secrets are stored in output files
- Generated policies contain only permission statements, no resource ARNs with account IDs in plaintext
- S3 bucket is encrypted at rest and blocks public access

### Generated Boundary Safety

- The boundary always includes observed permissions (no breakage risk)
- Headroom adds a configurable buffer (default 20%) of related permissions
- The tool generates but never applies the boundary — human review is required before deployment
- Output includes a diff report so reviewers can verify what changes

## Error Handling

| Scenario | Behavior |
|----------|----------|
| No CloudTrail events found | Exits with warning; suggests increasing `--days` |
| Bedrock throttling | Retries with exponential backoff (3 attempts) |
| IAM access denied | Exits with clear message identifying the missing permission |
| S3 upload failure | Saves artifacts locally; warns about upload failure |

## Extensibility

- Add new output formats by extending `iac_formatter.py`
- Support additional AI models by changing `--model-id`
- Integrate with CI/CD by consuming the JSON output programmatically
- Chain with IAM Access Analyzer findings for richer analysis
