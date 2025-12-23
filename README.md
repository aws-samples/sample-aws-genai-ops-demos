# AWS GenAI Operations Demos

This repository contains deployable code samples demonstrating practical applications of generative AI for cloud operations, spanning a wide range of AWS services and use cases. Each demo provides working implementations that can be deployed in your AWS environment.

## Available Demos

| Demo Name | Pillar | Description | Repository |
|-----------|--------|-------------|------------|
| AI-Powered Graviton Migration Assessment | Cost Optimization | Analyze codebases for Graviton migration opportunities and drive cost optimization using AWS Transform custom | [cost-optimization/ai-graviton-migration-assessment/](cost-optimization/ai-graviton-migration-assessment/README.md) |
| AI-Powered Technical Documentation Generation | Operations Automation | Generate comprehensive technical documentation from any codebase using AWS Transform custom | [operations-automation/ai-documentation-generation/](operations-automation/ai-documentation-generation/README.md) |
| AI-Powered Legacy System Automation | Operations Automation | Automate workflows on legacy systems using Nova Act with AgentCore Browser Tool | [operations-automation/ai-legacy-system-browser-automation/](operations-automation/ai-legacy-system-browser-automation/README.md) |
| AI Password Reset Chatbot | Operations Automation | Conversational password reset using Amazon Nova 2 Lite and AgentCore with Cognito integration | [operations-automation/ai-password-reset-chatbot/](operations-automation/ai-password-reset-chatbot/README.md) |
| AWS Services Lifecycle Tracker | Operations Automation | Automated monitoring of AWS service deprecations and end-of-life notices | [External Repository](https://github.com/aws-samples/sample-genai-powered-end-of-life-tracker) |

## Roadmap (Coming Soon)

| Demo Name | Pillar | Description | Status |
|-----------|--------|-------------|--------|
| Secure Coding with Kiro Hooks | Security | Automated security analysis in development workflows | Planned |
| AWS GenAI Cost Optimization Kiro Power | Cost Optimization | MCP server for static code analysis of AWS GenAI service usage patterns with cost optimization recommendations | Planned |
| Intelligent System Troubleshooting with AWS DevOps Agent | Observability | Identify root causes of system issues and accelerate incident response using AWS DevOps Agent through a realistic Unicorn Rentals microservices architecture | Planned |
| Natural Language Chaos Engineering with AWS FIS | Resilience | Define AWS FIS experiment templates using natural language descriptions, automatically converted to safe chaos engineering tests via Amazon Bedrock | Planned |
| AWS Health and Support Case Analyzer | Resilience | AI-powered analysis of AWS Health events and Support Cases with intelligent categorization and actionable recommendations | Planned |
| Agentic Prompt Evaluator | Operations Automation | Agentic AI tool for Bedrock model migration assessment with AgentCore Code Interpreter, automated prompt optimization, and comparative model evaluation | Planned |

## Repository Structure

```
cost-optimization/
└── ai-graviton-migration-assessment/
operations-automation/
├── ai-documentation-generation/
├── ai-legacy-system-browser-automation/
└── ai-password-reset-chatbot/
shared/
└── scripts/                # Common utilities and scripts
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
- Node.js 18+ (for CDK-based demos)
- Python 3.9+ (for Python-based demos)

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



## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.

