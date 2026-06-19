#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { getRegion } from '../../../../../../shared/utils/aws-utils';
import { GOATDevOpsIntegrationStack } from '../lib/devops-integration-stack';

// ---------------------------------------------------------------------------
// Region detection via shared utilities
// ---------------------------------------------------------------------------
const region = getRegion();
const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region,
};

const app = new cdk.App();

// ---------------------------------------------------------------------------
// GOATDevOpsIntegration Stack
//
// Deploys the DevOps Agent integration endpoint as a new CDK stack that
// imports existing NetworkInfra and NetworkRuntime stack CfnOutput exports
// and instantiates the AgentIntegrationTemplate with Network Agent action
// definitions.
//
// The IAM role scoped to bedrock-agent-runtime:InvokeAgentRuntime on the
// Network Agent ARN and execute-api:Invoke on the integration endpoint is
// created within the AgentIntegrationTemplate construct (see task 8.1).
//
// Requirements: 5.2, 5.4, 5.5, 5.6
// ---------------------------------------------------------------------------
new GOATDevOpsIntegrationStack(app, `GOATDevOpsIntegration-${region}`, {
  env,
  description:
    'GOAT Network Agent DevOps Agent Integration: packet-level L7 diagnostics for AWS DevOps Agent (uksb-do9bhieqqh)(tag:goat-devops-integration,operations-automation)',
});

app.synth();
