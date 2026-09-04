#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { FinOpsGamificationStack } from '../lib/finops-gamification-stack';
import { getRegion } from '../../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Get region using shared utility
const region = getRegion();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: region,
};

// Main stack with solution adoption tracking
new FinOpsGamificationStack(app, `FinOpsGamificationConsole-${region}`, {
  env,
  description: 'FinOps Gamification Console: Ownership, accountability, and gamification layer for AWS FinOps Agent (uksb-do9bhieqqh)(tag:finops-gamification,cost-optimization)',
});

app.synth();
