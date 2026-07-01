# AI Permission Boundary Generator

**Problem**: IAM roles and users in AWS environments accumulate permissions over time, often far exceeding what's actually needed. This "permission drift" expands the blast radius of compromised credentials, yet manually auditing and trimming policies across dozens of roles is tedious and error-prone.

**Solution**: This demo analyzes CloudTrail logs for a specific IAM role or user over a configurable period (default 30 days), identifies which permissions are actually exercised versus merely granted, and uses Amazon Bedrock (Claude) to generate a least-privilege permission boundary with reasonable headroom. The output is a ready-to-deploy CDK construct or CloudFormation resource that constrains the role without breaking existing workloads.

## What You Get

- 🔍 **CloudTrail Analysis** — Extracts actually-used API actions for any IAM role or user over your chosen time window
- 📊 **Before/After Comparison** — Shows current permissions vs. recommended boundary with attack surface reduction metrics
- 🤖 **AI-Generated Boundaries** — Uses Claude to craft a permission boundary that covers observed usage plus reasonable headroom for operational flexibility
- 📦 **Infrastructure-as-Code Output** — Produces the boundary as a CDK construct or CloudFormation resource, ready to deploy
- 📈 **Risk Reduction Report** — Quantifies the reduction in attack surface as a percentage of removed unused permissions

## How It Works

1. **Deploy Infrastructure** — CDK deploys an S3 bucket to store generated boundaries and reports
2. **Analyze CloudTrail** — The local Python analyzer queries CloudTrail for all API calls made by the target role/user
3. **Extract Current Permissions** — Retrieves all attached policies and inline policies for the target identity
4. **Identify Gaps** — Compares granted permissions against actually-used permissions to find unused access
5. **Generate Boundary** — Sends the analysis to Bedrock (Claude) which produces a least-privilege permission boundary with headroom
6. **Produce Output** — Generates the boundary as IaC, a before/after comparison report, and uploads artifacts to S3

> ⏱️ **Processing Time**: Typically 30–90 seconds depending on the volume of CloudTrail events and the number of attached policies.

## Example Output

After running the generator, you'll find these files in `output/`:

```
output/
├── MyAppRole-boundary-policy.json        # The generated permission boundary
├── MyAppRole-boundary-cdk.ts             # CDK construct to deploy the boundary
├── MyAppRole-boundary-cfn.yaml           # CloudFormation resource alternative
├── MyAppRole-analysis-report.md          # Before/after comparison with metrics
└── MyAppRole-cloudtrail-summary.json     # Raw analysis of observed API calls
```

## Architecture

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐     ┌───────────┐
│  CloudTrail     │────▶│  Analyzer           │────▶│  Amazon Bedrock │────▶│  S3 Bucket│
│  Logs           │     │  (local Python)     │     │  (Claude)       │     │  (output) │
└─────────────────┘     └─────────────────────┘     └─────────────────┘     └───────────┘
                                │                                                   ▲
                                │          ┌──────────────────┐                     │
                                └─────────▶│  IAM APIs        │                     │
                                           │  (read policies) │                     │
                                           └──────────────────┘                     │
                                                                                    │
                                        output/ (local files) ──────────────────────┘
```

### What Gets Deployed

| Resource | Purpose |
|----------|---------|
| S3 Bucket | Stores generated boundary policies and analysis reports |

That's it — just one S3 bucket via CDK. All analysis runs locally on your machine.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| AWS CLI | v2.31.13 or later |
| Python | 3.10 or later |
| Node.js | 20 or later (for CDK) |
| Amazon Bedrock | Claude model enabled in your account/region |
| CloudTrail | Enabled (standard in most AWS accounts) |
| IAM Permissions | `cloudtrail:LookupEvents`, `iam:List*`, `iam:Get*`, `bedrock:InvokeModel` |

## Quick Start

### Linux/macOS

```bash
# Clone the repository
git clone https://github.com/example/sample-aws-genai-ops-demos.git
cd sample-aws-genai-ops-demos/security/ai-permission-boundary-generator

# Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install

# Deploy infrastructure (S3 bucket)
npx cdk deploy

# Generate a permission boundary for a role
./generate-boundaries.sh --role-name MyAppRole --days 30
```

### Windows

```powershell
# Clone the repository
git clone https://github.com/example/sample-aws-genai-ops-demos.git
cd sample-aws-genai-ops-demos\security\ai-permission-boundary-generator

# Install dependencies
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
npm install

# Deploy infrastructure (S3 bucket)
npx cdk deploy

# Generate a permission boundary for a role
python generate_boundaries.py --role-name MyAppRole --days 30
```

## Parameters

| Parameter | Description | Default | Required |
|-----------|-------------|---------|----------|
| `--role-name` | IAM role or user name to analyze | — | Yes |
| `--days` | Number of days of CloudTrail history to analyze | `30` | No |
| `--region` | AWS region for CloudTrail and Bedrock calls | `us-east-1` | No |
| `--headroom` | Percentage of headroom to add beyond observed usage | `20` | No |
| `--output-format` | Output format: `cdk`, `cfn`, or `both` | `both` | No |
| `--model-id` | Bedrock model ID to use | `anthropic.claude-3-sonnet-20240229-v1:0` | No |
| `--bucket-name` | Override the S3 bucket name for output storage | Auto-generated | No |

## Relationship to IAM Access Analyzer

This tool complements [IAM Access Analyzer](https://docs.aws.amazon.com/IAM/latest/UserGuide/what-is-access-analyzer.html) rather than replacing it. IAM Access Analyzer identifies unused permissions and generates findings about external access, but it does not produce a ready-to-deploy replacement policy.

The AI Permission Boundary Generator picks up where Access Analyzer leaves off:

| Capability | IAM Access Analyzer | This Tool |
|------------|--------------------:|----------:|
| Identify unused permissions | ✅ | ✅ |
| Generate replacement boundary policy | ❌ | ✅ |
| Add operational headroom | ❌ | ✅ |
| Produce IaC output (CDK/CFN) | ❌ | ✅ |
| Before/after risk reporting | ❌ | ✅ |
| External access findings | ✅ | ❌ |

Use them together: Access Analyzer for continuous monitoring and external access alerts, this tool for generating actionable permission boundaries when you're ready to tighten a role.

## Cost

| Component | Estimated Cost |
|-----------|---------------|
| Bedrock API calls (Claude) | ~$0.10–$0.50 per run |
| S3 storage | Negligible (small JSON/YAML files) |

Total estimated cost: **~$0.10–$0.50 per run**. Costs vary based on the volume of CloudTrail events and the size of the analyzed policies.

## Cleanup

Remove all deployed resources:

```bash
npx cdk destroy
```

This removes the S3 bucket. Local output files in `output/` are not affected — delete them manually if desired.

## Project Structure

```
ai-permission-boundary-generator/
├── README.md
├── ARCHITECTURE.md
├── .gitignore
├── generate-boundaries.sh          # Entry point (Linux/macOS)
├── generate_boundaries.py          # Main Python analyzer
├── requirements.txt                # Python dependencies
├── package.json                    # Node.js dependencies (CDK)
├── cdk.json                        # CDK configuration
├── lib/
│   └── boundary-stack.ts           # CDK stack (S3 bucket)
├── src/
│   ├── cloudtrail_analyzer.py      # CloudTrail log extraction
│   ├── policy_extractor.py         # Current IAM policy retrieval
│   ├── boundary_generator.py       # Bedrock integration for policy generation
│   ├── report_builder.py           # Before/after comparison reports
│   └── iac_formatter.py            # CDK/CFN output formatting
├── templates/
│   ├── boundary-cdk-template.ts    # CDK construct template
│   └── boundary-cfn-template.yaml  # CloudFormation template
└── output/                         # Generated artifacts (git-ignored)
```

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
