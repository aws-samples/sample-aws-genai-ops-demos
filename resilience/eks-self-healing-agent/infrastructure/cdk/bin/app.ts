#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AgathaStack } from '../lib/agatha-stack';
import { getRegion } from '../../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Region from the shared util. No hardcoded account or region literals: the
// account comes from the ambient CDK_DEFAULT_ACCOUNT.
const region = getRegion();
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };

// Inputs come from --context (set by deploy-all), never from cdk.json.
// The shared deploy-cdk script runs `cdk bootstrap` without --context, so a
// missing input must not fail synthesis: in that case no stack is defined and
// bootstrap proceeds. deploy-all always passes all three inputs.
const inputs: Record<string, string | undefined> = {
  clusterName: app.node.tryGetContext('clusterName'),
  kubectlRoleArn: app.node.tryGetContext('kubectlRoleArn'),
  oidcIssuer: app.node.tryGetContext('oidcIssuer'),
};
const missing = Object.keys(inputs).filter(
  (key) => !inputs[key] || typeof inputs[key] !== 'string',
);

// `cdk bootstrap` fails on an app with no stacks, so the stack is always
// defined. When inputs are missing (bootstrap), synth-only placeholders are
// used; they are never deployed because deploy-all always passes real values.
if (missing.length > 0) {
  console.warn(
    `Agatha: missing CDK context ${missing.join(', ')}; using synth-only placeholders. ` +
      'This is expected during cdk bootstrap. Deploy only through deploy-all with ' +
      '--cluster-name and --kubectl-role-arn.',
  );
}
const placeholderArn = `arn:aws:iam::${process.env.CDK_DEFAULT_ACCOUNT ?? '000000000000'}:role/agatha-placeholder`;

const stack = new AgathaStack(app, `EksSelfHealingAgent-${region}`, {
  env,
  description:
    'EKS Self-Healing Agent (Agatha): in-cluster agent image, IAM role and Helm release (uksb-do9bhieqqh)(tag:eks-self-healing-agent,resilience)',
  clusterName: inputs.clusterName ?? 'agatha-placeholder-cluster',
  kubectlRoleArn: inputs.kubectlRoleArn ?? placeholderArn,
  oidcIssuer: inputs.oidcIssuer ?? `https://oidc.eks.${region}.amazonaws.com/id/PLACEHOLDER`,
  nodeArchitecture: app.node.tryGetContext('nodeArchitecture'),
  baseModelId: app.node.tryGetContext('bedrockBaseModelId'),
  imageTag: app.node.tryGetContext('imageTag'),
});
if (missing.length > 0) {
  cdk.Annotations.of(stack).addWarningV2(
    'agatha:placeholder-inputs',
    `Synthesized with placeholder inputs (missing: ${missing.join(', ')}). Do not deploy; use deploy-all.`,
  );
}

app.synth();
