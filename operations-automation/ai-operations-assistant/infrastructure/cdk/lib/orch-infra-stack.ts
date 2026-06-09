import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { BaseInfraStack } from './base-infra-stack';

/**
 * Props for the OrchInfraStack.
 *
 * The Conversations table ARN is plumbed through so the orchestration
 * runtime IAM role can read/write Capture_Conversation_Context entries
 * (Task 36, Reqs 9.20 / 17.9). Made optional for backwards
 * compatibility with deployments that haven't been updated yet —
 * when omitted, the orchestration agent's persistence layer becomes
 * a no-op.
 */
export interface OrchInfraStackProps extends cdk.StackProps {
  conversationsTableArn?: string;
}

/**
 * G.O.A.T. OrchInfraStack — ECR, S3, CodeBuild, and IAM for the Orchestration Agent.
 * IAM role scoped to AgentCore invoke (calling sub-agent runtimes via @tool functions),
 * Bedrock model invocation (any Bedrock-supported foundation model selected via
 * the `ORCH_MODEL_ID` environment variable on the OrchRuntime — see Req 9.9, 9.13),
 * and DynamoDB read/write on the Conversations table for
 * Capture_Conversation_Context persistence (Task 36, Reqs 9.20 / 17.9).
 */
export class OrchInfraStack extends BaseInfraStack {
  constructor(scope: Construct, id: string, props?: OrchInfraStackProps) {
    // Build the policy list incrementally so the conversations-table
    // permission is only attached when the prop is supplied. CDK
    // rejects empty Resource arrays in IAM policy statements, so an
    // unconditional always-present block would break deployments
    // that haven't been updated yet.
    const policies: iam.PolicyStatement[] = [
      // AgentCore runtime invocation (call sub-agent runtimes)
      new iam.PolicyStatement({
        sid: 'AgentCoreInvoke',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:InvokeAgentRuntime',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:runtime/*`,
        ],
      }),
      // Bedrock model invocation — Req 9.13.
      //
      // The Orchestration Agent's foundation model is selected at deploy time
      // via the `ORCH_MODEL_ID` env var on the OrchRuntime (default
      // `global.amazon.nova-pro-v1:0`, see Req 9.9). Operators may switch to
      // any Bedrock-supported identifier (Anthropic Claude, Amazon Nova, Meta
      // Llama, Mistral, AI21, Cohere, or any future-published model) without
      // editing this policy.
      //
      // The resource ARNs below cover that entire surface:
      //   • `arn:aws:bedrock:*::foundation-model/*` — every foundation model
      //     in every region and from every vendor. Foundation-model ARNs are
      //     AWS-managed (no account ID), so the empty `::` is the correct
      //     pattern.
      //   • `arn:aws:bedrock:<region>:<account>:inference-profile/*` —
      //     same-region inference profiles owned by this account.
      //   • `arn:aws:bedrock:*:<account>:inference-profile/*` — cross-region
      //     (system-defined or account-owned) inference profiles, including
      //     `global.*`, `us.*`, `eu.*`, `apac.*` profiles.
      //
      // Together these wildcards prevent `AccessDeniedException` when the
      // operator changes `ORCH_MODEL_ID` and redeploys, regardless of which
      // vendor or region the new model belongs to.
      new iam.PolicyStatement({
        sid: 'BedrockModelInvocation',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
          'bedrock:Converse',
          'bedrock:ConverseStream',
        ],
        resources: [
          'arn:aws:bedrock:*::foundation-model/*',
          `arn:aws:bedrock:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:inference-profile/*`,
          `arn:aws:bedrock:*:${cdk.Aws.ACCOUNT_ID}:inference-profile/*`,
        ],
      }),
      // AWS Marketplace for Bedrock model access
      new iam.PolicyStatement({
        sid: 'MarketplaceAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'aws-marketplace:ViewSubscriptions',
          'aws-marketplace:Subscribe',
          'aws-marketplace:Unsubscribe',
        ],
        resources: ['*'],
      }),
      // Step Functions DescribeExecution — the orchestration agent polls
      // the Network Agent's transformation workflow progress via
      // poll_transform_execution, which calls states:DescribeExecution.
      new iam.PolicyStatement({
        sid: 'StepFunctionsDescribeExecution',
        effect: iam.Effect.ALLOW,
        actions: ['states:DescribeExecution'],
        resources: [`arn:${cdk.Aws.PARTITION}:states:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:execution:*`],
      }),
    ];

    if (props?.conversationsTableArn) {
      // Capture_Conversation_Context persistence (Task 36, Reqs 9.20 /
      // 17.9). The orchestration agent reads, writes, and updates a
      // small set of CTX#CAPTURE# rows in the DataStack's
      // Conversations table to remember the most recently created
      // capture_id per conversation. The permission set below is the
      // minimum required by the agent's ``state.py`` helpers:
      //
      //   • GetItem  — load_capture_context
      //   • PutItem  — record_capture_context (replaces on new capture)
      //   • UpdateItem — update_capture_context_status
      //   • Query    — reserved for future enumeration of all
      //                CTX#CAPTURE# rows for a user (the row layout
      //                already supports this access pattern via the
      //                shared PK and SK-prefix scheme).
      //   • DeleteItem — reserved for future cleanup logic.
      //
      // The resources list is scoped to the table ARN itself plus
      // ``index/*`` to allow Query against a future GSI without a
      // policy update. The orchestration agent does not yet use any
      // GSI, but pre-allowing it removes a deployment redo when
      // Task 41 (Support_Case_Investigation) lands.
      policies.push(
        new iam.PolicyStatement({
          sid: 'ConversationsTableCaptureContext',
          effect: iam.Effect.ALLOW,
          actions: [
            'dynamodb:GetItem',
            'dynamodb:PutItem',
            'dynamodb:UpdateItem',
            'dynamodb:Query',
            'dynamodb:DeleteItem',
            'dynamodb:DescribeTable',
          ],
          resources: [
            props.conversationsTableArn,
            `${props.conversationsTableArn}/index/*`,
          ],
        }),
      );
    }

    super(scope, id, {
      domainName: 'orch',
      exportPrefix: 'GOATOrchAgent',
      imageTag: 'goat_orch_agent',
      domainPolicies: policies,
    }, props);
  }
}
