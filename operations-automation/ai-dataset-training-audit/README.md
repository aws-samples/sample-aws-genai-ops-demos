# AI Dataset Training Audit (DART)

Pre-flight validation for LLM fine-tuning datasets. DART scans a dataset
(JSONL/CSV/Parquet, local or `s3://`), runs seven analysis checks — schema,
duplicates, PII (via Amazon Comprehend), training viability, cost, and safe
fixes — and returns a **GO / GO WITH FIXES / NO-GO** verdict with a **0–100
quality score** and a Markdown report, **before any GPU compute is spent** on
training. It is read-only on customer data by default.

The agent is a Strands Agents Python 3.12 app running as a short-lived **ECS
Fargate** batch task. Amazon Bedrock (Claude Sonnet, configurable) writes the
report narrative; Amazon Comprehend detects PII. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the diagram, the reproducible scoring
formula, and the data-handling invariants.

## Quick Start

**Two IaC options — CDK (default) or Terraform. Both produce the same result.**
Only one path may be deployed per account+region.

```bash
# CDK (default)
./deploy-all.sh            # macOS/Linux
.\deploy-all.ps1           # Windows

# Terraform
./deploy-all-terraform.sh  # macOS/Linux
.\deploy-all-terraform.ps1 # Windows
```

Each script runs the shared prerequisites check first, deploys, and ends with a
summary block plus the exact `aws ecs run-task` command to launch a scan.

## Prerequisites

- **AWS CLI >= 2.31.13**, configured with credentials and a region.
- **Docker** (running). The container image is built locally — by `cdk deploy`
  on the CDK path, and by `docker build`/`docker push` on the Terraform path.
- **Node.js 20+ / AWS CDK** (CDK path).
- **Terraform >= 1.5.0** (Terraform path only).
- **Amazon Bedrock** model access for the configured Claude Sonnet model enabled
  in your account/region, and **Amazon Comprehend** available in the region.

## Cost estimate

- **No standing network cost**: the VPC has **no NAT gateway** (see the
  networking tradeoff below). S3 and DynamoDB use free gateway endpoints.
- **Per run**: a Fargate task (4 vCPU / 8 GB) for the minutes it runs, one or
  more Amazon Comprehend `DetectPiiEntities` calls per scanned document, and one
  Amazon Bedrock inference for the report narrative. A typical small-dataset
  audit costs a few cents; Comprehend cost scales with the number of records
  scanned (every string field of every record is scanned — no sampling).
- **Standing**: a KMS key, an (empty until used) S3 bucket, a PAY_PER_REQUEST
  DynamoDB table, and a CloudWatch log group (1-week retention). These are
  effectively free when idle.

Running the demo in your own account will incur AWS charges; tear it down when
done (see Cleanup).

## Model choice — Claude Sonnet by default (deliberate)

This demo defaults to **Claude Sonnet** for the report narrative, which is a
deliberate deviation from the repository's Nova-default guidance: the narrative
benefits from Sonnet's summarization quality, and this preserves fidelity with
the original DART agent. The model is **fully configurable** via the `MODEL_ID`
container environment variable (CDK injects the CRIS-prefixed id; Terraform takes
`var.model_id`). Any model you choose must be in the approved list in
`cdk/lib/dataset-training-audit-stack.ts` / `terraform/main.tf`, or the task role
will be denied `bedrock:InvokeModel`.

## Networking tradeoff — no NAT gateway

The task runs in a **public subnet** with a public IP and an explicit security
group (no ingress; egress TCP 443 only), reaching Bedrock and Comprehend over the
public internet while S3 and DynamoDB stay on free gateway endpoints. This gives
**zero standing network cost**. A production deployment would typically use
private subnets with a NAT gateway (or PrivateLink interface endpoints for
Bedrock/Comprehend), trading cost for the task never holding a public IP. See
[ARCHITECTURE.md](ARCHITECTURE.md#networking-tradeoff-no-nat-gateway).

## Running a scan

After deployment, the script prints an `aws ecs run-task` command. Its
`--network-configuration` **must** pass all three of `subnets`, `securityGroups`,
and `assignPublicIp=ENABLED` (the script fills these in from the stack outputs) —
without the security group the public-subnet task cannot reach
Bedrock/Comprehend.

By default the task can read its **own output bucket**
(`s3://dart-agent-output-<account>-<region>/`), so the zero-config workflow is:
upload a dataset there, then run a scan against that path. Local-file input also
works for a credential-free smoke (see below).

## Scanning a dataset in your own S3 bucket

By default the task role has **no read access to arbitrary buckets** — it reads
only its own output bucket. To scan a dataset that lives in a customer/source
bucket, grant scoped read access at deploy time (this is an opt-in):

```bash
# CDK: comma-separated bucket names
./deploy-all.sh    # then redeploy with the context key, e.g.
cd cdk && npx -y cdk deploy DatasetTrainingAudit-<region> \
  --context inputBuckets=my-dataset-bucket,another-bucket
```

```hcl
# Terraform: terraform.tfvars
input_buckets = ["my-dataset-bucket", "another-bucket"]
```

When non-empty, the stack/module adds **one scoped statement per named bucket**
granting `s3:GetObject` + `s3:ListBucket` on `arn:<partition>:s3:::<bucket>` and
`.../*` — never `*`. Set the `ALLOWED_S3_BUCKETS` container env var (CDK) /
`allowed_s3_buckets` variable (Terraform) to the **same** list so path
validation and the IAM grant agree. If the bucket is KMS-encrypted, also grant
`kms:Decrypt` on its key.

Writing a cleaned/redacted copy **back** to a bucket is a separate opt-in — see
[MUTATING_ACTIONS.md](MUTATING_ACTIONS.md).

## Credential-free local smoke (offline mode)

You can render a report locally with **no AWS credentials** using offline mode.
Run from the demo root:

```bash
DART_OFFLINE=1 python agent/agent.py \
  "Check tests/fixtures/sample_train.jsonl for llama3-70b fine-tuning"
```

Offline mode bypasses the agent's Bedrock reasoning loop, uses a scripted
Comprehend response, and emits the templated narrative. The bundled fixture
produces a **NO-GO** verdict (it contains fake `example.com` PII). Offline mode
is a verification/demo convenience and is never set on the deployed task.

## Terraform vs CDK (one path per account+region)

The CDK and Terraform paths provision the **same** resources with the same names,
IAM boundaries, and outputs; they **cannot both be deployed into the same
account+region**. Terraform mirrors CDK on top of it — CDK is the primary,
documented-first path. The Terraform path builds and pushes the container image
itself in a deterministic two-pass apply (ECR repo first, build/push, then the
rest); a bare `terraform apply` without the deploy script is unsupported because
`var.image_tag` has no default.

## Solution adoption metrics (opt-out)

Each deployment creates a tracking marker so it is counted in AWS solution
adoption metrics. On the CDK path this is the stack description; on the Terraform
path it is a **zero-cost CloudFormation marker stack** gated by
`enable_deployment_metrics` (default `true`). Set
`enable_deployment_metrics = false` in `terraform.tfvars` to opt out.

## Keeping `aws_utils.py` in sync

`agent/aws_utils.py` is a container-local copy of `shared/utils/aws_utils.py`
(the sanctioned container-local copy, since the shared module lives outside the
Docker build context). The CDK stack and Terraform `locals` replicate the same
region→CRIS-prefix mapping inline. **When the shared util's default model or
prefix mapping changes, update `agent/aws_utils.py`, the inline mapping in
`cdk/lib/dataset-training-audit-stack.ts`, and `terraform/main.tf` together.**

## Cleanup

```bash
# CDK
cd cdk && npx -y cdk destroy DatasetTrainingAudit-<region> --force -c imageTag=destroy

# Terraform
cd terraform && terraform destroy -var image_tag=<tag> -var region=<region> -auto-approve
```

The `-c imageTag=destroy` flag makes the destroy re-synth reference an existing
ECR image via the stack's imageTag escape hatch instead of building the
container image, so destroy needs no Docker daemon. (Do not use
`-c aws:cdk:bundling-stacks=[]`: the current CDK CLI rejects user-provided
`aws:`-prefixed context keys.) Both paths remove the bucket (with its objects),
the DynamoDB table, the log group, and the ECR repo.

## Catalog

| Demo | Pillar | Description |
|---|---|---|
| AI Dataset Training Audit | Operations Automation | Pre-flight validation for LLM fine-tuning datasets: seven checks (schema, duplicates, PII via Comprehend, viability, cost) producing a GO/NO-GO/GO-WITH-FIXES verdict and 0–100 quality score before any GPU spend. |

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
