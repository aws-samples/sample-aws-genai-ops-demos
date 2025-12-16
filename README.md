# AWS GenAI Operations Demos

This repository contains deployable code samples demonstrating practical applications of generative AI for cloud operations, spanning a wide range of AWS services and use cases. Each demo provides working implementations that can be deployed in your AWS environment.

## Available Demos

### Security
| Demo Name | Description | Repository |
|-----------|-------------|------------|
| Secure Coding with Kiro Hooks | Automated security analysis in development workflows | security/secure-coding-kiro-hooks/ |
| Compliance Gap Analysis with AI | Find compliance gaps and get remediation guidance instantly | security/compliance-gap-analysis/ |

### Cost Optimization
| Demo Name | Description | Repository |
|-----------|-------------|------------|
| GenAI Cost Optimization MCP Server | Proactive cost analysis for AI workloads | cost-optimization/genai-cost-optimization-mcp/ |

### Observability
| Demo Name | Description | Repository |
|-----------|-------------|------------|
| AI-Powered Incident Analysis | Automated root cause analysis using AI | observability/incident-analysis/ |
| GenAI Workload Observability & Anomaly Detection | Spot GenAI usage anomalies before they become incidents | observability/genai-workload-monitoring/ |
| Automated Ticket Investigation with DevOps Agent | Offload New Relic and ServiceNow ticket investigations to AI | observability/automated-ticket-investigation/ |

### Resilience
| Demo Name | Description | Repository |
|-----------|-------------|------------|
| Automated Resilience Testing & Recovery | Test failure scenarios without the chaos using Amazon Bedrock and AWS FIS | resilience/automated-resilience-testing/ |
| GenAI-Powered Capacity Management | Manage GenAI capacity to prevent outages and performance degradation | resilience/genai-capacity-management/ |

### Operations Automation
| Demo Name | Description | Repository |
|-----------|-------------|------------|
| AWS Services Lifecycle Tracker | Automated monitoring of AWS service deprecations and end-of-life notices | [External Repository](https://github.com/aws-samples/sample-genai-powered-end-of-life-tracker) |
| Model Upgrade Evaluator | Automated testing framework for AI model migrations | operations-automation/model-upgrade-evaluator/ |
| Automated Documentation Generation | Generate comprehensive technical documentation from any codebase using AWS Transform | [operations-automation/ai-documentation-generation/](operations-automation/ai-documentation-generation/README.md) |
| AI-Powered Legacy System Automation | Automate workflows on legacy systems using Nova Act with AgentCore Browser Tool | [operations-automation/ai-legacy-system-browser-automation/](operations-automation/ai-legacy-system-browser-automation/README.md) |
| AI Password Reset Chatbot | Conversational password reset using Amazon Nova 2 Lite and AgentCore with Cognito integration | [operations-automation/ai-password-reset-chatbot/](operations-automation/ai-password-reset-chatbot/README.md) |

## Repository Structure

```
security/
├── secure-coding-kiro-hooks/
├── compliance-gap-analysis/
cost-optimization/
├── genai-cost-optimization-mcp/
observability/
├── incident-analysis/
├── genai-workload-monitoring/
├── automated-ticket-investigation/
resilience/
├── automated-resilience-testing/
├── genai-capacity-management/
operations-automation/
├── model-upgrade-evaluator/
├── ai-documentation-generation/
└── ai-legacy-system-browser-automation/
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

1. Choose a demo from the list above
2. Navigate to the demo's folder: `[pillar]/[demo-name]/`
3. Follow the README.md for deployment instructions
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

