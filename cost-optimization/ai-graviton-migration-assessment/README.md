# AI-Powered Graviton Migration Assessment

**Problem**: Should I migrate my application to AWS Graviton? How much will it save? What's involved?

**Solution**: Point this tool at any GitHub repository and get a comprehensive migration assessment with cost analysis and ready-to-use migration artifacts.

## What You Get

🎯 **Executive Summary**: Business case with ROI, timeline, and risk assessment  
💰 **Cost Analysis**: Detailed savings projections (typically 10-20% reduction)  
🔧 **Migration Artifacts**: Ready-to-use Dockerfiles, CI/CD configs, and infrastructure templates  
📋 **Action Plan**: Step-by-step migration roadmap with complexity scoring  

## How It Works

1. **Deploy**: Creates a CodeBuild project with AWS Transform AI analysis
2. **Analyze**: AI examines your code for ARM64 compatibility and cost optimization
3. **Generate**: Produces both strategic assessment and practical migration files
4. **Download**: Get comprehensive reports and ready-to-use artifacts

**Processing Time**: ~60 minutes for thorough analysis

## Example Output

**Assessment Reports**:
- `executive-summary.md` - Business case with 95% confidence recommendations
- `cost-analysis/` - ROI calculations, instance mapping, savings projections  
- `compatibility-analysis/` - Language/dependency compatibility matrix
- `migration-plan/` - Phased approach with complexity scoring

**Migration Artifacts**:
- `containers/Dockerfile.arm64` - Ready-to-use ARM64 Dockerfiles
- `ci-cd/` - Multi-architecture build pipelines (GitHub Actions, CodeBuild)
- `infrastructure/` - Graviton-optimized CDK/Terraform templates
- `scripts/` - Testing and validation automation

## Architecture

```
GitHub Repo → CodeBuild (AWS Transform AI) → S3 (Assessment + Artifacts)
```

**What Gets Deployed**:
- **CodeBuild Project**: Runs AI analysis with Graviton expertise
- **S3 Bucket**: Stores assessments and migration files
- **IAM Role**: Secure permissions for Transform and S3

## Prerequisites

- AWS CLI 2.31.13+ with configured credentials
- Python 3.10+ and Node.js 20+ (for CDK)
- AWS Transform permissions (`transform-custom:*`)

## Quick Start

```bash
# Analyze your repository
cd cost-optimization/ai-graviton-migration-assessment
./assess-graviton.ps1 -RepositoryUrl "https://github.com/owner/repo"

# Or use the default sample (serverless payments app)
./assess-graviton.ps1
```

The script automatically:
1. ✅ Validates prerequisites  
2. 🚀 Deploys infrastructure  
3. 🔍 Starts AI analysis (~60 min)  
4. 📥 Downloads results

## Technical Details

**AI Analysis**: Uses AWS Transform with custom Graviton expertise  
**Language Support**: Python, Java, Go, C/C++, Node.js, Ruby, C#  
**Knowledge Base**: AWS best practices + fresh data from two official AWS repositories  
**Cost Modeling**: Instance mapping with workload-specific projections

### Integration with Official AWS Graviton Resources
This demo enhances AI analysis by dynamically downloading the latest guidance from two official AWS repositories:

#### [AWS Porting Advisor for Graviton](https://github.com/aws/porting-advisor-for-graviton)
- **Library compatibility rules** for 100+ Python packages, Java dependencies, etc.
- **Architecture-specific patterns** for detecting x86 intrinsics and assembly code  
- **Version requirements** for ARM64-compatible library versions

#### [AWS Graviton Getting Started](https://github.com/aws/aws-graviton-getting-started)
- **Performance optimization guidance** including compiler flags and SIMD instructions
- **Software version recommendations** with performance improvements (FFmpeg, HAProxy, etc.)
- **Service-specific patterns** for containers, Lambda, databases, and other AWS services
- **Monitoring and profiling** best practices for ARM64 workloads

**Always Fresh**: Both repositories are downloaded fresh during each assessment to ensure the latest compatibility rules, performance optimizations, and service patterns are available to the AI analysis.

## Cost

**Per Assessment**: ~$1.40-$2.50 (AWS Transform + CodeBuild + S3)  
**Typical Graviton Savings**: 10-20% on compute costs  
**ROI**: Assessment cost recovered within days of migration

## Cleanup

```bash
cd infrastructure/cdk
npx cdk destroy
```

## How It Works Under the Hood

**Why CodeBuild?** Secure, scalable environment for AI analysis  
**Why Knowledge Items?** Feeds Graviton best practices and service patterns to the AI  
**Why Dual Repository Integration?** Gets latest compatibility rules AND performance optimization guidance  
**Why Fresh Downloads?** Ensures analysis uses current software versions and optimization recommendations  
**Why Custom Transform?** Tailored specifically for comprehensive Graviton migration assessment

## Project Structure

```
graviton-migration-assessment/
├── assess-graviton.ps1                    # PowerShell deployment script
├── assess-graviton.sh                     # Bash deployment script
├── buildspec.yml                          # CodeBuild build specification
├── README.md                              # This file
├── ARCHITECTURE.md                        # Technical architecture details
├── graviton-transformation-definition/
│   ├── transformation_definition.md       # Custom transformation logic
│   ├── summaries.md                       # Reference documentation summaries
│   └── document_references/               # Porting Advisor integration docs
├── knowledge-items/
│   ├── graviton-best-practices.md         # AWS Graviton best practices
│   ├── graviton-pricing-guide.md          # Detailed cost analysis guidance
│   ├── graviton-performance-optimization.md # Compiler flags, SIMD, runtime optimization
│   └── graviton-service-patterns.md       # AWS service-specific migration patterns
└── infrastructure/
    └── cdk/
        ├── app.py                         # CDK app entry point
        ├── stack.py                       # CDK stack definition
        ├── cdk.json                       # CDK configuration
        └── requirements.txt               # Python dependencies
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