#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DatasetTrainingAuditStack } from '../lib/dataset-training-audit-stack';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Region from the shared util (env -> AWS CLI -> us-east-1 fallback). No hardcoded
// account/region literals: account comes from the ambient CDK_DEFAULT_ACCOUNT.
const region = getRegion();
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };

new DatasetTrainingAuditStack(app, `DatasetTrainingAudit-${region}`, {
  env,
  description:
    'DART pre-flight dataset audit for LLM fine-tuning on ECS Fargate (uksb-do9bhieqqh)(tag:ai-dataset-training-audit,operations-automation)',
});

app.synth();
