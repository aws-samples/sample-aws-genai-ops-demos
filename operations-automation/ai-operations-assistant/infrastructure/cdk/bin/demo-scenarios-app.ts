#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { getRegion } from '../../../../../shared/utils/aws-utils';
import { DemoScenarioAccountHealthStack } from '../lib/demo-scenario-account-health-stack';
import { DemoScenarioTlsFragmentationStack } from '../lib/demo-scenario-tls-fragmentation-stack';
import { DemoScenarioDiagnosticsGLStack } from '../lib/demo-scenario-diagnostics-gl-stack';

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
// Scenario A: Account Health Check (primary stack — includes adoption tracking)
// ---------------------------------------------------------------------------
const scenarioA = new DemoScenarioAccountHealthStack(app, `GOATDemoScenarioA-${region}`, {
  env,
  description: 'G.O.A.T. Demo Scenario A - Account Health Check resources (uksb-do9bhieqqh)(tag:goat-demo-scenarios,operations-automation)',
});

// ---------------------------------------------------------------------------
// Scenario C: Connectivity
// Uses the existing GOAT network infra VPC (from deploy-all.ps1) so that
// the traffic mirror target (NLB → collector) can reach the app instance's ENI.
// The VPC ID is imported from the GOATNetworkAgentVpcId CloudFormation export.
// ---------------------------------------------------------------------------
new DemoScenarioTlsFragmentationStack(app, `GOATDemoScenarioC-${region}`, {
  env,
  goatVpcExportName: 'GOATNetworkAgentVpcId',
  description: 'G.O.A.T. Demo Scenario C - Network connectivity investigation topology',
});

// ---------------------------------------------------------------------------
// Scenarios G–L: Network Troubleshooting (diagnostics tools evaluation)
// Reuses the shared GOAT VPC and imports TGW/NFW from Scenario C.
// ---------------------------------------------------------------------------
new DemoScenarioDiagnosticsGLStack(app, `GOATDemoScenariosGL-${region}`, {
  env,
  goatVpcExportName: 'GOATNetworkAgentVpcId',
  goatTgwExportName: 'GOATDemoScenarioCTransitGatewayId',
  goatFirewallEndpointExportName: 'GOATDemoScenarioCInspectionVpcId',
  description: 'G.O.A.T. Demo Scenarios G-L - Network troubleshooting diagnostics tools',
});

// ---------------------------------------------------------------------------
// App-level tagging — applies goat-demo=true to all resources in all stacks
// ---------------------------------------------------------------------------
cdk.Tags.of(app).add('goat-demo', 'true');

app.synth();
