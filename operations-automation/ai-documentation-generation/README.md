# AI-Powered Documentation Generation

Generate comprehensive technical documentation from any codebase using AWS Transform with custom organizational guidance. This demo deploys a CodeBuild project that runs AWS Transform's comprehensive codebase analysis with additional context to guide the output structure and content.

## How It Works

This demo uses AWS Transform's `AWS/early-access-comprehensive-codebase-analysis` transformation with custom `additionalPlanContext` to:
- **Leverage AWS expertise**: Uses AWS-managed transformation logic for deep technical analysis
- **Add organizational guidance**: Provides custom instructions based on your organization's guidelines and preferences for documentation structure and priorities
- **Enable customization**: Allows you to modify the guidance without changing transformation logic

## Processing Time

The comprehensive analysis takes **around 1 hour** depending on repository size and complexity. This includes deep AI-powered analysis of code structure, architecture, and technical patterns.

## What This Demo Shows

This demo provides a **CodeBuild-based AWS Transform solution** that generates comprehensive documentation from any Git repository. Infrastructure is deployed via CDK, making it easy to deploy and integrate into CI/CD pipelines.

## What Gets Generated

AWS Transform analyzes your codebase and generates comprehensive documentation. The exact structure and content depend on your codebase, but typically includes:

**Architecture Analysis**
- System architecture overview and component relationships
- Technology stack analysis and architectural patterns
- Data flow documentation and integration points

**Technical Documentation**
- Code structure and organization analysis
- API documentation and interface specifications
- Configuration and deployment information

**Operational Insights**
- Technical debt identification and recommendations
- Security analysis and best practices
- Performance considerations and optimization opportunities

**Custom Guidance Applied**
Our `additionalPlanContext` requests:
- Structured organization with clear sections (architecture/, api/, deployment/, operations/, etc.)
- Role-based content for different audiences (developers, architects, DevOps, managers)
- Operational focus with deployment guides and troubleshooting information
- Technical debt categorization with effort estimates
- Mermaid diagrams for visual architecture representation

**Note**: The actual output structure and content are determined by AWS Transform based on your codebase and our guidance. Results may vary depending on the project type, size, and complexity.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Git Repo      │───->│   CodeBuild      │────>│   S3 Bucket     │
│   (any URL)     │     │   + AWS Transform│     │   (docs output) │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

Components deployed via CDK:
- CodeBuild Project: Runs AWS Transform CLI in a managed build environment
- S3 Bucket: Stores generated documentation and buildspec
- IAM Role: Minimal permissions for Transform, S3, and CloudWatch

## Prerequisites

The deployment script automatically checks for all required prerequisites:

- AWS CLI version 2.31.13 or later (includes AWS Transform CLI)
- Python 3.10+ (for CDK)
- Node.js 20+ (for CDK)
- PowerShell (Windows) or Bash (Linux/macOS)
- AWS credentials configured with appropriate permissions

### Required AWS Permissions

- CloudFormation (CDK deployment)
- CodeBuild (create projects, start builds)
- S3 (create buckets, read/write objects)
- IAM (create roles and policies)
- CloudWatch Logs (create log groups, write logs)
- AWS Transform (`transform-custom:*` permissions)

## Customizing Documentation Output

You can guide AWS Transform's output by editing the `enhanced-transformation/additional-plan-context.md` file. This file contains instructions that influence how AWS Transform structures and presents the documentation.

### What You Can Customize

**Documentation Structure**: Request specific folder organization and file types
```markdown
STRUCTURE REQUIREMENTS:
- Organize documentation into clear sections: architecture/, api/, deployment/, operations/, security/, analysis/
- Create a comprehensive README.md with role-based navigation
- Generate Mermaid diagrams for architecture visualization
```

**Content Emphasis**: Specify what aspects to prioritize
```markdown
CONTENT PRIORITIES:
1. OPERATIONAL FOCUS: Emphasize deployment procedures, monitoring setup, troubleshooting guides
2. BUSINESS ALIGNMENT: Connect technical decisions to business value and operational impact  
3. ACTIONABLE INSIGHTS: Provide clear effort estimates and prioritized recommendations
4. MULTI-AUDIENCE: Serve developers, architects, DevOps engineers, and engineering managers
```

**Analysis Approach**: Guide how technical issues are categorized
```markdown
TECHNICAL DEBT ANALYSIS:
- Categorize issues as High/Medium/Low priority with effort estimates (e.g., "1-2 weeks", "3-5 days")
- Focus on production readiness and operational concerns
- Include cost optimization opportunities
```

**Presentation Style**: Request specific formatting and organization
```markdown
OUTPUT FORMAT:
- Use scannable format with clear headings, bullet points, and tables
- Include quick start guides for different roles (developers, architects, DevOps)
- Balance comprehensive technical detail with executive accessibility
```

### How to Make Changes

1. **Edit the guidance**: Modify `enhanced-transformation/additional-plan-context.md`
2. **Redeploy**: Run the deployment script to upload your updated guidance
3. **Test**: Generate documentation to see how AWS Transform interprets your instructions

### Important Notes

- **Guidance, not guarantees**: The context file provides instructions to AWS Transform, but the actual output depends on your codebase and how AWS Transform interprets the guidance
- **Iterative process**: You may need to refine your instructions based on the generated results
- **Codebase dependent**: Different types of projects may produce different documentation structures even with the same guidance

## Quick Start

### Deploy and Analyze Your Repository

```powershell
# PowerShell (Windows)
cd operations-automation/ai-documentation-generation
.\generate-docs.ps1 -RepositoryUrl "https://github.com/owner/repo"
```

```bash
# Bash (Linux/macOS)
cd operations-automation/ai-documentation-generation
./generate-docs.sh -r "https://github.com/owner/repo"
```

### Deploy with Default Sample Repository

If you don't specify a repository URL, the script uses a default sample repository for testing:

```powershell
# PowerShell - uses default sample repo
.\generate-docs.ps1
```

```bash
# Bash - uses default sample repo
./generate-docs.sh
```

The script will:
1. Validate AWS credentials, CLI version, Python, and Node.js
2. Deploy CDK stack (S3 bucket, IAM role, CodeBuild project)
3. Upload buildspec to S3
4. Start a build with the specified (or default) repository

### Deploy Infrastructure Only

```powershell
# PowerShell - skip setup and retrieve existing stack outputs
.\generate-docs.ps1 -SkipSetup
```

```bash
# Bash - skip setup and retrieve existing stack outputs
./generate-docs.sh -s
```

## Usage

### Generate Documentation for Any Repository

```bash
# Get the current region
REGION=$(aws configure get region)

# Get the actual project name from CloudFormation
PROJECT_NAME=$(aws cloudformation describe-stacks \
  --stack-name "DocumentationGeneratorStack-$REGION" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='CodeBuildProjectName'].OutputValue" \
  --output text)

# Start a documentation generation job
aws codebuild start-build \
  --project-name $PROJECT_NAME \
  --region "$REGION" \
  --environment-variables-override \
    name=REPOSITORY_URL,value=https://github.com/owner/repo \
    name=JOB_ID,value=my-job-123
```

### Monitor Build Progress

```bash
# Check build status (replace <build-id> with actual build ID)
aws codebuild batch-get-builds --region us-east-1 --ids <build-id>

# Stream build logs (use actual project name)
aws logs tail /aws/codebuild/$PROJECT_NAME --follow --region us-east-1
```

### Retrieve Generated Documentation

The deployment script offers to wait for the build to complete and automatically download the documentation to your local machine. If you choose not to wait, you can download manually:

```bash
# List generated documentation
aws s3 ls s3://doc-gen-output-<account-id>-<region>/documentation/ --recursive

# Download documentation to local folder
aws s3 cp s3://doc-gen-output-<account-id>-<region>/documentation/<job-id>/ ./generated-docs --recursive
```

The downloaded documentation will be in a `generated-docs-<job-id>` folder in your current directory.

## Environment Variables (CodeBuild)

- `REPOSITORY_URL` (required): Git repository URL to analyze. Default: Serverless Digital Asset Payments sample
- `OUTPUT_BUCKET` (required): S3 bucket for documentation. Auto-created by CDK (retrieved from stack outputs)
- `JOB_ID` (optional): Unique job identifier. Default: `doc-gen-<timestamp>`

## CI/CD Integration Examples

### GitHub Actions

```yaml
name: Generate Documentation
on:
  push:
    branches: [main]

jobs:
  documentation:
    runs-on: ubuntu-latest
    steps:
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-region: us-east-1
          
      - name: Generate Documentation
        run: |
          aws codebuild start-build \
            --project-name aws-transform-doc-generator \
            --region us-east-1 \
            --no-cli-pager \
            --environment-variables-override \
              name=REPOSITORY_URL,value=${{ github.server_url }}/${{ github.repository }} \
              name=JOB_ID,value=${{ github.run_id }}
```

### AWS CodePipeline

```yaml
version: 0.2
phases:
  build:
    commands:
      - |
        aws codebuild start-build \
          --project-name aws-transform-doc-generator \
          --region us-east-1 \
          --no-cli-pager \
          --environment-variables-override \
            name=REPOSITORY_URL,value=$CODEBUILD_SOURCE_REPO_URL \
            name=JOB_ID,value=$CODEBUILD_BUILD_ID
```

### GitLab CI

```yaml
generate_docs:
  stage: build
  script:
    - |
      aws codebuild start-build \
        --project-name aws-transform-doc-generator \
        --region us-east-1 \
        --no-cli-pager \
        --environment-variables-override \
          name=REPOSITORY_URL,value=$CI_REPOSITORY_URL \
          name=JOB_ID,value=$CI_PIPELINE_ID
```

## Cost Estimates

### AWS Transform Custom Pricing

AWS Transform custom charges per **agent minute** at **$0.035/minute**. Agent minutes are only counted when the agent is actively working (planning, reasoning, analyzing, modifying code), not during builds or idle time.

### Per Documentation Job (Estimated)
- CodeBuild (BUILD_GENERAL1_MEDIUM, ~50 min): ~$0.33
- AWS Transform analysis (~30-60 agent min): ~$1.05 - $2.10
- S3 storage (100MB documentation): ~$0.002
- Total per job: ~$1.40 - $2.50

### Monthly Estimates
- Light (10 jobs/month): ~$15-25/month
- Medium (50 jobs/month): ~$70-125/month
- Production (100 jobs/month): ~$140-250/month

### Cost Optimization Tips
- Use AWS Budgets to set limits on agent minutes
- Set up S3 lifecycle policies to archive old documentation
- Test on representative repos first to establish baseline costs

Note: Costs vary by codebase complexity. The same transformation can cost different amounts on different codebases.

## AWS Transform CLI Options

The transformation uses `atx custom def exec` with these available flags:
- `-x` / `--non-interactive`: No user prompts (used)
- `-t` / `--trust-all-tools`: Auto-trust all tools (used)
- `-d` / `--do-not-learn`: Skip knowledge extraction, faster (used)
- `-g` / `--configuration`: Config file (YAML/JSON)
- `--tv` / `--transformation-version`: Use specific version

### Environment Variables
- `ATX_SHELL_TIMEOUT`: Override shell timeout (default 900s)
- `ATX_DISABLE_UPDATE_CHECK`: Skip update checks at startup

### Performance Notes

The **45-90 minute runtime** is inherent to comprehensive AI analysis. The transformation performs deep analysis of:
- Code structure and architecture patterns
- Component relationships and dependencies  
- Technical debt and improvement opportunities
- Documentation generation based on findings

**There is no "fast mode"** - this is the nature of comprehensive AI-powered codebase analysis. Runtime varies by repository size and complexity.

### Tips for Development/Testing

1. Use smaller test repos during development/testing
2. Add `-d` flag to skip knowledge extraction (already enabled)
3. Set `ATX_DISABLE_UPDATE_CHECK=true` to skip update checks
4. Plan for async workflows - trigger builds and check results later

## Buildspec Configuration

The CodeBuild project uses a buildspec stored in S3. Key phases:

```yaml
phases:
  install:
    # Install AWS Transform CLI and dependencies
    
  pre_build:
    # Clone target repository
    
  build:
    # Run AWS Transform comprehensive codebase analysis
    # Uses: atx custom def exec -n "..." -p "." -x -t -d
    
  post_build:
    # Upload generated documentation to S3
```

See `buildspec.yml` for the complete configuration.

## Troubleshooting

### CDK Deployment Fails
- Ensure Python 3.10+ and Node.js 20+ are installed
- Check that CDK is bootstrapped: `cdk bootstrap aws://<account>/<region>`
- Verify AWS credentials have CloudFormation permissions

### Build Fails During Install Phase
- Ensure AWS CLI version 2.31.13+ is available
- Check that the CodeBuild service role has internet access

### AWS Transform Analysis Fails
- Verify the repository URL is publicly accessible
- Check CloudWatch logs for detailed error messages
- Some project types may not be fully supported

### Documentation Not Appearing in S3
- Verify the S3 bucket exists and IAM role has write permissions
- Check the post_build phase logs for upload errors

### Debugging Commands

```bash
# View detailed build logs
aws logs get-log-events \
  --log-group-name /aws/codebuild/aws-transform-doc-generator \
  --log-stream-name <build-id> \
  --region us-east-1 \
  --no-cli-pager

# Check IAM role permissions
aws iam get-role --role-name CodeBuildDocGenRole --no-cli-pager
```

## Cleanup

Remove all resources using CDK:

```powershell
# PowerShell (Windows)
cd operations-automation/ai-documentation-generation/infrastructure/cdk
npx -y cdk destroy --no-cli-pager
```

```bash
# Bash (Linux/macOS)
cd operations-automation/ai-documentation-generation/infrastructure/cdk
npx -y cdk destroy --no-cli-pager
```

## Project Structure

```
ai-documentation-generation/
├── generate-docs.ps1              # PowerShell deployment script
├── generate-docs.sh               # Bash deployment script
├── buildspec.yml                  # CodeBuild build specification
├── README.md                      # This file
├── ARCHITECTURE.md                # Technical architecture details
├── enhanced-transformation/
│   └── additional-plan-context.md # Customizable transformation guidance
└── infrastructure/
    └── cdk/
        ├── app.py                 # CDK app entry point
        ├── stack.py               # CDK stack definition
        ├── cdk.json               # CDK configuration
        └── requirements.txt       # Python dependencies
```

### Shared Scripts

This demo uses the shared scripts for prerequisite validation and CDK deployment:

```
shared/
└── scripts/
    ├── check-prerequisites.ps1    # Shared prereq validation (Windows)
    ├── check-prerequisites.sh     # Shared prereq validation (Linux/macOS)
    ├── deploy-cdk.ps1             # Shared CDK deployment (Windows)
    └── deploy-cdk.sh              # Shared CDK deployment (Linux/macOS)
```

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
