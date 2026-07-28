# IAM Security Assistant — Steering Document

## Demo Metadata

| Field | Value |
|-------|-------|
| Demo Name | IAM Security Assistant |
| Pillar | Security |
| Pattern | Infrastructure deployment (deploy-all.ps1 / deploy-all.sh) |
| Primary AWS Service | Amazon Bedrock |
| Supporting Services | IAM Access Analyzer, Security Hub, CloudTrail, Lambda, API Gateway, Cognito, CloudFront, S3 |
| Repository Path | security/ai-iam-access-analyzer-assistant/ |
| Status | In Development |
| Estimated Effort | 3 weeks, 1 engineer |

## Press Release

**AWS Launches IAM Security Assistant — Conversational Least-Privilege Policy Management**

*Security teams can now ask natural language questions about their IAM posture and get actionable least-privilege recommendations in seconds*

**Seattle, WA** — Today, AWS announced the IAM Security Assistant, an open-source demo that combines Amazon Bedrock with IAM Access Analyzer and Security Hub to deliver a conversational interface for IAM security posture management.

Security teams spend hours manually reviewing IAM Access Analyzer findings, cross-referencing CloudTrail logs, and crafting least-privilege policies. The IAM Security Assistant eliminates this toil by letting users ask plain-English questions like "What are my critical IAM findings?", "Generate a least-privilege policy for role X based on the last 90 days of activity", or "What other roles depend on this policy before I modify it?"

The assistant deploys as a self-contained web application using React, Cloudscape, and Amazon Cognito for authentication. Behind the scenes, Amazon Bedrock orchestrates tool calls to Lambda functions that query Security Hub findings, analyze CloudTrail access patterns, map role dependencies, and validate proposed policy changes — all without users needing to understand the underlying API calls.

"IAM is consistently the most complex and error-prone area for our customers," said a Solutions Architect at AWS. "This demo shows how GenAI can transform a multi-hour manual review into a five-minute conversation while maintaining the rigor of least-privilege principles."

The demo deploys in under 15 minutes via CDK and works in any AWS account that has Security Hub and IAM Access Analyzer already enabled.

## Frequently Asked Questions

### Customer FAQ

**Q: What specific problem does this solve?**
A: It eliminates the manual, error-prone process of reviewing IAM Access Analyzer findings, correlating them with CloudTrail activity, and writing least-privilege policies. Instead of navigating multiple console pages and writing JSON by hand, users have a natural conversation that produces validated, ready-to-apply IAM policies.

**Q: Who is this for?**
A: Security engineers, cloud architects, DevOps teams, and platform engineers who manage IAM policies at scale. Anyone responsible for maintaining least-privilege access in AWS accounts benefits from the conversational interface and automated analysis.

**Q: How does it work?**
A: The frontend (React + Cloudscape) sends user messages to a Lambda-backed API. The Lambda function uses Amazon Bedrock's Converse API with toolConfig to orchestrate multiple tools: querying Security Hub for IAM Access Analyzer findings, analyzing CloudTrail logs for actual API usage, mapping role and policy dependencies, and validating proposed policy changes. Bedrock decides which tools to invoke based on the user's question and synthesizes the results into a coherent response.

**Q: What permissions does it need?**
A: The deployed Lambda functions need read access to Security Hub findings, IAM Access Analyzer, CloudTrail (for policy generation lookback), and IAM (for dependency mapping). The CDK stack creates least-privilege IAM roles automatically. Users accessing the web UI authenticate via Cognito.

**Q: What prerequisites must already be in place?**
A: Before deploying this demo, the target account must have: (1) Security Hub enabled with IAM Access Analyzer integration, (2) IAM Access Analyzer enabled with an active analyzer, (3) CloudTrail logging enabled (for the policy generation lookback window), and (4) Amazon Bedrock model access enabled for Claude.

**Q: Is the output production-ready?**
A: The generated policies are a starting point validated against IAM policy grammar and dependency analysis. They should be reviewed by a human before applying to production roles. The demo includes a validation tool that checks for overly broad permissions, missing conditions, and dependency conflicts.

**Q: What does it cost?**
A: Infrastructure costs are minimal (~$5-10/month for Lambda, API Gateway, Cognito, CloudFront, S3). Per-interaction costs come from Bedrock API calls (typically $0.01-0.05 per conversation depending on complexity and model choice). Security Hub and IAM Access Analyzer have their own pricing based on findings volume.

**Q: Can I use this with AWS Organizations and multi-account setups?**
A: The current demo operates within a single account. However, the architecture supports extension to multi-account scenarios by aggregating Security Hub findings from a delegated administrator account.
