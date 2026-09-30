/**
 * DevOpsAgentSpace: a complete, ready-to-use AWS DevOps Agent Agent Space.
 *
 *   1. AgentSpaceRole   assumed by the DevOps Agent service to monitor the account
 *                       (AIDevOpsAgentAccessPolicy + Resource Explorer SLR creation)
 *   2. OperatorRole     assumed for the operator app / web console (AIDevOpsOperatorAppAccessPolicy)
 *   3. Agent Space      AWS::DevOpsAgent::AgentSpace, operator app enabled (IAM auth)
 *   4. AWS association  AWS::DevOpsAgent::Association (accountType "monitor"): the account's
 *                       resources, in every region, become the space's topology
 *   5. Webhook          the generic eventChannel webhook that starts investigations, provisioned
 *                       by a Lambda-backed custom resource (lambda/webhook-provisioner)
 *
 * Facts this construct encodes so no demo rediscovers them:
 * - The webhook's HMAC secret is returned ONLY in the AssociateService create response; no
 *   Describe/List API returns it again and AWS::DevOpsAgent::Association exposes no webhook
 *   attributes. The custom resource writes it straight into a Secrets Manager secret and
 *   returns only the URL; the value never enters CloudFormation state or outputs. Any property
 *   change on the custom resource rotates the webhook (replacement), the only correct behavior.
 * - RegisterService(eventChannel) has no CloudFormation type either (AWS::DevOpsAgent::Service
 *   has no eventChannel service details).
 * - Both roles trust aidevops.amazonaws.com with sts:AssumeRole AND sts:TagSession, scoped by
 *   aws:SourceAccount and an ArnLike aws:SourceArn on agentspace/* in this region.
 * - The Agent Space must depend on both roles: the service validates assumability at creation
 *   and IAM is eventually consistent.
 *
 * Mechanism only, used as is: a demo gives it a name and reads agentSpaceId, webhookUrl and
 * webhookSecret. Deploy it in a region where AWS DevOps Agent is available. Requires
 * aws-cdk-lib >= 2.267.0 (AWS::DevOpsAgent::AgentSpace with OperatorApp). Like every construct
 * under shared/devops-agent, the demo's CDK project maps aws-cdk-lib and constructs to its own
 * copies (see shared/devops-agent/README.md).
 */
import * as cdk from 'aws-cdk-lib';
import * as devopsagent from 'aws-cdk-lib/aws-devopsagent';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import * as path from 'path';

export interface DevOpsAgentSpaceProps {
  /** Agent Space name. Also the prefix of the two IAM role names. */
  readonly name: string;
  /** Shown in the DevOps Agent console. */
  readonly description?: string;
  /** Account to associate for monitoring (all regions). Default: the stack's account. */
  readonly monitorAccountId?: string;
  /** Operator app (web console, IAM auth). Default: true. */
  readonly enableOperatorApp?: boolean;
  /** The eventChannel webhook (URL + HMAC secret in Secrets Manager). Default: true. */
  readonly enableWebhook?: boolean;
}

export class DevOpsAgentSpace extends Construct {
  public readonly agentSpace: devopsagent.CfnAgentSpace;
  public readonly agentSpaceId: string;
  public readonly agentSpaceArn: string;
  /** Monitoring role the DevOps Agent assumes to inspect the account. */
  public readonly agentSpaceRole: iam.Role;
  /** Operator app (web console) role. */
  public readonly operatorRole: iam.Role;
  /** Webhook URL (CFN token); empty string when `enableWebhook` is false. */
  public readonly webhookUrl: string;
  /** Holds the webhook HMAC secret, written by the provisioner at deploy time. Undefined when `enableWebhook` is false. */
  public readonly webhookSecret?: secretsmanager.Secret;

  constructor(scope: Construct, id: string, props: DevOpsAgentSpaceProps) {
    super(scope, id);
    const stack = cdk.Stack.of(this);
    const monitorAccountId = props.monitorAccountId ?? stack.account;
    const enableOperatorApp = props.enableOperatorApp ?? true;
    const enableWebhook = props.enableWebhook ?? true;

    // Trust shared by both roles: the service principal, scoped to this account and to
    // Agent Spaces in this region, for sts:AssumeRole and sts:TagSession.
    const trustConditions = {
      StringEquals: { 'aws:SourceAccount': stack.account },
      ArnLike: { 'aws:SourceArn': `arn:${stack.partition}:aidevops:${stack.region}:${stack.account}:agentspace/*` },
    };
    const aidevops = () => new iam.ServicePrincipal('aidevops.amazonaws.com').withConditions(trustConditions);
    const addTagSessionTrust = (role: iam.Role) => {
      role.assumeRolePolicy?.addStatements(new iam.PolicyStatement({
        effect: iam.Effect.ALLOW, actions: ['sts:TagSession'], principals: [aidevops()],
      }));
    };

    this.agentSpaceRole = new iam.Role(this, 'AgentSpaceRole', {
      roleName: `${props.name}-AgentSpaceRole`,
      assumedBy: aidevops(),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('AIDevOpsAgentAccessPolicy')],
      description: `Monitoring role assumed by AWS DevOps Agent for Agent Space '${props.name}'`,
    });
    addTagSessionTrust(this.agentSpaceRole);
    // The agent creates the Resource Explorer service-linked role on first topology discovery.
    this.agentSpaceRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowCreateServiceLinkedRoles',
      effect: iam.Effect.ALLOW,
      actions: ['iam:CreateServiceLinkedRole'],
      resources: [`arn:${stack.partition}:iam::${stack.account}:role/aws-service-role/*`],
    }));

    this.operatorRole = new iam.Role(this, 'OperatorRole', {
      roleName: `${props.name}-OperatorRole`,
      assumedBy: aidevops(),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('AIDevOpsOperatorAppAccessPolicy')],
      description: `Operator app (web console) role for Agent Space '${props.name}'`,
    });
    addTagSessionTrust(this.operatorRole);

    this.agentSpace = new devopsagent.CfnAgentSpace(this, 'AgentSpace', {
      name: props.name,
      description: props.description,
      ...(enableOperatorApp ? { operatorApp: { iam: { operatorAppRoleArn: this.operatorRole.roleArn } } } : {}),
    });
    this.agentSpace.node.addDependency(this.agentSpaceRole);
    this.agentSpace.node.addDependency(this.operatorRole);
    this.agentSpaceId = this.agentSpace.attrAgentSpaceId;
    this.agentSpaceArn = this.agentSpace.attrArn;

    const awsAssociation = new devopsagent.CfnAssociation(this, 'AwsMonitorAssociation', {
      agentSpaceId: this.agentSpaceId,
      serviceId: 'aws', // literal for the Aws configuration
      configuration: {
        aws: { accountId: monitorAccountId, accountType: 'monitor', assumableRoleArn: this.agentSpaceRole.roleArn },
      },
    });
    awsAssociation.node.addDependency(this.agentSpaceRole);

    if (!enableWebhook) {
      this.webhookUrl = '';
      return;
    }

    // No explicit name: avoids the scheduled-deletion name retention on destroy/redeploy cycles.
    this.webhookSecret = new secretsmanager.Secret(this, 'WebhookSecret', {
      description: `DevOps Agent webhook HMAC secret for Agent Space '${props.name}'`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const provisioner = new lambda.Function(this, 'WebhookProvisioner', {
      runtime: lambda.Runtime.PYTHON_3_14,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'webhook-provisioner')),
      timeout: cdk.Duration.minutes(2),
      description: 'Custom resource: provisions the DevOps Agent eventChannel webhook and stores its HMAC secret in Secrets Manager',
      logGroup: new logs.LogGroup(this, 'WebhookProvisionerLogs', {
        retention: logs.RetentionDays.TWO_WEEKS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });
    // Register/List are account-scoped calls without a resource ARN; the resource-level
    // scoping of the association APIs is not documented, hence '*' with the actions enumerated.
    provisioner.addToRolePolicy(new iam.PolicyStatement({
      sid: 'DevOpsAgentWebhookLifecycle',
      effect: iam.Effect.ALLOW,
      actions: ['aidevops:RegisterService', 'aidevops:ListServices', 'aidevops:AssociateService',
        'aidevops:DisassociateService', 'aidevops:ListAssociations'],
      resources: ['*'],
    }));
    this.webhookSecret.grantWrite(provisioner);

    const webhook = new cdk.CustomResource(this, 'Webhook', {
      resourceType: 'Custom::DevOpsAgentWebhook',
      serviceToken: provisioner.functionArn,
      properties: { AgentSpaceId: this.agentSpaceId, SecretArn: this.webhookSecret.secretArn },
    });
    webhook.node.addDependency(this.agentSpace);
    this.webhookUrl = webhook.getAttString('WebhookUrl');
  }
}
