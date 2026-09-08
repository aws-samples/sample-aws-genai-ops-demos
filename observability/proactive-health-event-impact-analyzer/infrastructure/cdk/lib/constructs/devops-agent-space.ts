import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as devopsagent from 'aws-cdk-lib/aws-devopsagent';
import { Construct } from 'constructs';
import * as path from 'path';

/**
 * DevOpsAgentSpace — a reusable construct that provisions a complete, ready-to-use
 * AWS DevOps Agent Agent Space:
 *
 *   1. AgentSpaceRole  — assumed by the DevOps Agent service to monitor the account
 *                        (AIDevOpsAgentAccessPolicy + Resource Explorer SLR creation)
 *   2. OperatorRole    — assumed for the operator app / web console
 *                        (AIDevOpsOperatorAppAccessPolicy)
 *   3. Agent Space     — AWS::DevOpsAgent::AgentSpace, operator app enabled (IAM auth)
 *   4. AWS association — AWS::DevOpsAgent::Association (accountType "monitor"),
 *                        wiring the account's resources into the space's topology
 *   5. Webhook         — a generic eventChannel webhook for triggering investigations,
 *                        provisioned by a Lambda-backed custom resource (see below)
 *
 * Why the webhook needs a custom resource
 * ---------------------------------------
 * The webhook's HMAC secret is returned ONLY in the AssociateService create response —
 * no Describe/List API ever returns it again, and AWS::DevOpsAgent::Association exposes
 * no webhook attributes. CloudFormation therefore cannot surface the secret. The custom
 * resource performs RegisterService(eventChannel) + AssociateService at deploy time,
 * writes the secret straight into a Secrets Manager secret (it never enters
 * CloudFormation state or outputs), and returns only the webhook URL.
 * (RegisterService also cannot be expressed in CloudFormation: AWS::DevOpsAgent::Service
 * has no eventChannel service details type.)
 *
 * Any property change on the custom resource rotates the webhook (replacement =
 * delete + recreate), which is the only correct behavior given the secret cannot
 * be re-read.
 *
 * This mirrors the equivalent construct in observability/eks-investigation-devops-agent
 * (lib/constructs/devops-agent-space.ts), which the maintainers already reviewed and
 * merged for the same purpose — replacing the imperative `aws devops-agent` CLI flow
 * that scripts/setup-wizard.ts previously drove by hand.
 */
export interface DevOpsAgentSpaceProps {
  /** Agent Space name. Also used as the prefix for the two IAM role names. */
  readonly name: string;

  /** Human-readable description shown in the DevOps Agent console. */
  readonly description?: string;

  /**
   * Deployment environment — drives the webhook provisioner Lambda's log
   * retention (production=90d, non-production=14d), matching the repo-wide
   * ProductionValidationAspect enforced in bin/app.ts across every stack.
   */
  readonly deployEnvironment: string;

  /**
   * AWS account to associate for monitoring. Defaults to the containing stack's
   * account. The DevOps Agent discovers and maps resources across ALL regions of
   * this account — no per-region association is needed.
   */
  readonly monitorAccountId?: string;

  /**
   * Enable the operator app (web console, IAM auth). Default: true.
   */
  readonly enableOperatorApp?: boolean;

  /**
   * Provision the generic eventChannel webhook (URL + HMAC secret in Secrets
   * Manager). Default: true.
   */
  readonly enableWebhook?: boolean;
}

export class DevOpsAgentSpace extends Construct {
  /** The underlying AWS::DevOpsAgent::AgentSpace resource. */
  public readonly agentSpace: devopsagent.CfnAgentSpace;
  /** Agent Space ID (CFN token). */
  public readonly agentSpaceId: string;
  /** Agent Space ARN (CFN token). */
  public readonly agentSpaceArn: string;
  /** Monitoring role the DevOps Agent assumes to inspect the account. */
  public readonly agentSpaceRole: iam.Role;
  /** Operator app (web console) role. */
  public readonly operatorRole: iam.Role;
  /**
   * Webhook URL for triggering investigations (CFN token).
   * Empty string when `enableWebhook` is false.
   */
  public readonly webhookUrl: string;
  /**
   * Secrets Manager secret holding the webhook HMAC secret. The value is written
   * by the provisioner Lambda during deployment — it never passes through
   * CloudFormation parameters, outputs, or resource state.
   * Undefined when `enableWebhook` is false.
   */
  public readonly webhookSecret?: secretsmanager.Secret;

  constructor(scope: Construct, id: string, props: DevOpsAgentSpaceProps) {
    super(scope, id);

    const stack = cdk.Stack.of(this);
    const monitorAccountId = props.monitorAccountId ?? stack.account;
    const enableOperatorApp = props.enableOperatorApp ?? true;
    const enableWebhook = props.enableWebhook ?? true;

    // ─── Trust policy shared by both roles ─────────────────────────────────
    // aidevops.amazonaws.com, scoped to this account and to Agent Spaces in
    // this region (ArnLike on aws:SourceArn). Matches the trust the AWS CLI
    // onboarding flow (previously driven by scripts/setup-wizard.ts) established.
    const aidevopsTrustConditions = {
      StringEquals: { 'aws:SourceAccount': stack.account },
      ArnLike: {
        'aws:SourceArn': `arn:${stack.partition}:aidevops:${stack.region}:${stack.account}:agentspace/*`,
      },
    };
    const aidevopsTrust = () =>
      new iam.ServicePrincipal('aidevops.amazonaws.com').withConditions(aidevopsTrustConditions);
    const addTagSessionTrust = (role: iam.Role) => {
      role.assumeRolePolicy?.addStatements(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['sts:TagSession'],
        principals: [aidevopsTrust()],
      }));
    };

    // ─── 1. Monitoring role ────────────────────────────────────────────────
    this.agentSpaceRole = new iam.Role(this, 'AgentSpaceRole', {
      roleName: `${props.name}-AgentSpaceRole`,
      assumedBy: aidevopsTrust(),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AIDevOpsAgentAccessPolicy'),
      ],
      description: `Monitoring role assumed by AWS DevOps Agent for Agent Space '${props.name}'`,
    });
    addTagSessionTrust(this.agentSpaceRole);

    // The agent creates the Resource Explorer service-linked role on first
    // topology discovery. Scoped to SLR paths only.
    this.agentSpaceRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowCreateServiceLinkedRoles',
      effect: iam.Effect.ALLOW,
      actions: ['iam:CreateServiceLinkedRole'],
      resources: [`arn:${stack.partition}:iam::${stack.account}:role/aws-service-role/*`],
    }));

    // ─── 2. Operator app role ──────────────────────────────────────────────
    this.operatorRole = new iam.Role(this, 'OperatorRole', {
      roleName: `${props.name}-OperatorRole`,
      assumedBy: aidevopsTrust(),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AIDevOpsOperatorAppAccessPolicy'),
      ],
      description: `Operator app (web console) role for Agent Space '${props.name}'`,
    });
    addTagSessionTrust(this.operatorRole);

    // ─── 3. Agent Space ────────────────────────────────────────────────────
    this.agentSpace = new devopsagent.CfnAgentSpace(this, 'AgentSpace', {
      name: props.name,
      description: props.description,
      ...(enableOperatorApp ? {
        operatorApp: {
          iam: { operatorAppRoleArn: this.operatorRole.roleArn },
        },
      } : {}),
    });
    // Roles (and their policies) must be fully in place first: the service
    // validates assumability during resource creation, and IAM is eventually
    // consistent — the same class of race the wizard's retry-with-backoff loop
    // used to work around for `associate-service`.
    this.agentSpace.node.addDependency(this.agentSpaceRole);
    this.agentSpace.node.addDependency(this.operatorRole);

    this.agentSpaceId = this.agentSpace.attrAgentSpaceId;
    this.agentSpaceArn = this.agentSpace.attrArn;

    // ─── 4. AWS account association (monitor) ──────────────────────────────
    const awsAssociation = new devopsagent.CfnAssociation(this, 'AwsMonitorAssociation', {
      agentSpaceId: this.agentSpaceId,
      // For Aws/SourceAws configurations the service id is the literal "aws".
      serviceId: 'aws',
      configuration: {
        aws: {
          accountId: monitorAccountId,
          accountType: 'monitor',
          assumableRoleArn: this.agentSpaceRole.roleArn,
        },
      },
    });
    awsAssociation.node.addDependency(this.agentSpaceRole);

    // ─── 5. Webhook (custom resource) ──────────────────────────────────────
    if (!enableWebhook) {
      this.webhookUrl = '';
      return;
    }

    // Holds the HMAC secret. No explicit name: avoids collisions with
    // scheduled-deletion name retention on destroy/redeploy cycles.
    this.webhookSecret = new secretsmanager.Secret(this, 'WebhookSecret', {
      description: `DevOps Agent webhook HMAC secret for Agent Space '${props.name}'`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const provisionerFn = new lambda.Function(this, 'WebhookProvisioner', {
      runtime: lambda.Runtime.NODEJS_24_X,
      handler: 'index.handler',
      // Pre-bundled by `npm run bundle` (scripts/bundle-lambdas.js).
      // @aws-sdk/client-devops-agent and @aws-sdk/client-secrets-manager are
      // bundled into the artifact rather than left external, since the newer
      // devops-agent client is not guaranteed to ship with the Lambda runtime.
      code: lambda.Code.fromAsset(
        path.join(__dirname, '..', '..', 'dist', 'lambda', 'devops-agent-webhook-provisioner'),
      ),
      timeout: cdk.Duration.minutes(2),
      description: 'Custom resource: provisions the DevOps Agent eventChannel webhook and stores its HMAC secret in Secrets Manager',
      logGroup: new logs.LogGroup(this, 'WebhookProvisionerLogs', {
        // Matches the 90d/14d rule the repo-wide ProductionValidationAspect
        // enforces across every stack (see bin/app.ts, lib/aspects/production-validation.ts).
        retention: props.deployEnvironment === 'production'
          ? logs.RetentionDays.THREE_MONTHS
          : logs.RetentionDays.TWO_WEEKS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // RegisterService/ListServices are account-scoped calls without a resource
    // ARN in their request; AssociateService/DisassociateService target the
    // space and a generated service id. The aidevops resource-level scoping for
    // these registration APIs is not documented, so this statement uses '*'
    // with the actions enumerated tightly.
    provisionerFn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'DevOpsAgentWebhookLifecycle',
      effect: iam.Effect.ALLOW,
      actions: [
        'aidevops:RegisterService',
        'aidevops:ListServices',
        'aidevops:AssociateService',
        'aidevops:DisassociateService',
        'aidevops:ListAssociations',
      ],
      resources: ['*'],
    }));
    this.webhookSecret.grantWrite(provisionerFn);

    const webhook = new cdk.CustomResource(this, 'Webhook', {
      resourceType: 'Custom::DevOpsAgentWebhook',
      serviceToken: provisionerFn.functionArn,
      properties: {
        AgentSpaceId: this.agentSpaceId,
        SecretArn: this.webhookSecret.secretArn,
      },
    });
    webhook.node.addDependency(this.agentSpace);

    this.webhookUrl = webhook.getAttString('WebhookUrl');
  }
}
