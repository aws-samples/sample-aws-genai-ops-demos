# AWS GenAI for Operations Demos

This repository contains deployable code samples demonstrating how generative AI drives operational excellence across security, cost optimization, resilience, and automation. Each demo provides working implementations that solve real operational challenges - deploy as-is with one click, or adapt to your specific environment and business needs with minimal customization effort.

## Available Demos

| Demo Name | Pillar | Description | Repository |
|-----------|--------|-------------|------------|
| AI-Powered Graviton Migration Assessment | Cost Optimization | Get comprehensive migration assessment with cost analysis and ready-to-use migration artifacts for any codebase | [cost-optimization/ai-graviton-migration-assessment/](cost-optimization/ai-graviton-migration-assessment/README.md) |
| AI-Powered Technical Documentation Generation | Operations Automation | Generate comprehensive technical documentation with architecture analysis, API docs, and operational guides from any codebase | [operations-automation/ai-documentation-generation/](operations-automation/ai-documentation-generation/README.md) |
| AI-Powered Legacy System Automation | Operations Automation | Automate complex web workflows on legacy systems using cloud-based browser automation with session recording and live monitoring | [operations-automation/ai-legacy-system-browser-automation/](operations-automation/ai-legacy-system-browser-automation/README.md) |
| AI Password Reset Chatbot | Operations Automation | Conversational password reset with streaming responses, session persistence, and secure Cognito integration for anonymous access | [operations-automation/ai-password-reset-chatbot/](operations-automation/ai-password-reset-chatbot/README.md) |
| AWS Services Lifecycle Tracker | Operations Automation | Automated monitoring and intelligent categorization of AWS service deprecations with real-time dashboard and admin interface | [operations-automation/aws-services-lifecycle-tracker/](operations-automation/aws-services-lifecycle-tracker/README.md) |
| AWS GenAI Cost Optimization Kiro Power | Cost Optimization | MCP server for static code analysis of AWS GenAI service usage patterns with cost optimization recommendations and Kiro IDE integration | [cost-optimization/aws-genai-cost-optimization-mcp-server/](cost-optimization/aws-genai-cost-optimization-mcp-server/README.md) |
| AI Lambda Runtime Migration Assistant | Operations Automation | Discover, assess, and transform Lambda functions running deprecated runtimes using Amazon Bedrock AgentCore and Nova 2 Lite with a React dashboard | [operations-automation/ai-lambda-runtime-migration/](operations-automation/ai-lambda-runtime-migration/README.md) |
| Natural Language Chaos Engineering with AWS FIS | Resilience | Transform natural language descriptions into validated AWS FIS experiment templates with current capabilities and intelligent caching | [resilience/ai-chaos-engineering-with-fis/](resilience/ai-chaos-engineering-with-fis/README.md) |
| Intelligent EKS Incident Investigation with Amazon DevOps Agent | Observability | Automatically detect, investigate, and diagnose EKS infrastructure incidents using Amazon DevOps Agent — reducing mean time to resolution from hours to minutes | [observability/eks-investigation-devops-agent/](observability/eks-investigation-devops-agent/README.md) |
| Intelligent Site-to-Site VPN Tunnel Investigation with Amazon DevOps Agent | Networking | Automatically detect, investigate, and diagnose Site-to-Site VPN tunnel failures with BGP routing using Amazon DevOps Agent — reducing mean time to resolution from hours to minutes | [networking/vpn-tunnel-investigation-devops-agent/](networking/vpn-tunnel-investigation-devops-agent/README.md) |

## Roadmap (Coming Soon)

| Demo Name | Pillar | Description | Status |
|-----------|--------|-------------|--------|
| AWS Health and Support Case Analyzer | Resilience | AI-powered analysis of AWS Health events and Support Cases with intelligent categorization and actionable recommendations | Planned |

## Repository Structure

```
cost-optimization/
├── ai-graviton-migration-assessment/
└── aws-genai-cost-optimization-mcp-server/
operations-automation/
├── ai-documentation-generation/
├── ai-lambda-runtime-migration/
├── ai-legacy-system-browser-automation/
├── ai-password-reset-chatbot/
├── anycompany-it-demo-portal/
└── aws-services-lifecycle-tracker/
observability/
└── eks-investigation-devops-agent/
networking/
└── vpn-tunnel-investigation-devops-agent/
resilience/
└── ai-chaos-engineering-with-fis/
shared/
├── scripts/                # Common prerequisite checks
└── utils/                  # Shared region/account utilities
```

Each demo folder typically contains:
```
[demo-name]/
├── README.md              # Deployment guide
├── ARCHITECTURE.md        # Technical design
├── deploy-*.ps1           # PowerShell deployment script
├── deploy-*.sh            # Bash deployment script
└── [additional files]     # Demo-specific resources
```

## Getting Started

1. Browse the available demos in the table above
2. Click on the repository link for your chosen demo
3. Follow the demo's README.md for detailed deployment instructions
4. Deploy using the provided Infrastructure as Code scripts

## Prerequisites

- AWS CLI configured with appropriate permissions
- AWS CDK or Terraform (depending on demo)
- Node.js 20+ (for CDK-based demos)
- Python 3.10+ (for Python-based demos)

## Technology Stack

### Core AI Services
- **Amazon Bedrock** - Foundation model access and management
- **Amazon Nova Models** - Latest generation AI models
- **Amazon Bedrock AgentCore** - Multi-step AI workflow orchestration
- **AWS Transform** - AI-powered code transformation and documentation generation

### Integration Frameworks
- **Model Context Protocol (MCP) Servers** - Standardized tool integration
- **Kiro** - AI-assisted development workflows

### Supporting Services
- **AWS Lambda** - Serverless compute
- **Amazon CloudWatch** - Monitoring and logging
- **AWS Systems Manager** - Configuration management
- **Amazon S3** - Object storage
- **Amazon DynamoDB** - NoSQL database

## Cost Considerations

Each demo includes detailed cost estimates and optimization recommendations. Typical costs range from $10-50/month depending on usage patterns. See individual demo READMEs for specific cost breakdowns.

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.

## 👏 Contributors

Shout out to these awesome contributors:

<a href="https://github.com/aws-samples/sample-aws-genai-ops-demos/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=aws-samples/sample-aws-genai-ops-demos" />
</a>

