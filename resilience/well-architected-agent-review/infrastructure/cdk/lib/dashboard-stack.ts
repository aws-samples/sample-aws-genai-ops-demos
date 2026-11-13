import { CfnOutput, Stack, StackProps, aws_cloudwatch as cloudwatch } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { AppConfig } from './config';

export interface DashboardStackProps extends StackProps {
  cfg: AppConfig;
}

/**
 * CloudWatch dashboard (primary region): multi-region / per-region / per-AZ
 * traffic, plus an optional Global Accelerator row.
 *
 * The ALB and target group dimension values come from --context, passed by the
 * deploy script from the region stack outputs. This avoids CDK cross-Region
 * references, whose custom resources would leave orphan log groups behind.
 * Without that context (for example when `cdk destroy` synthesizes the app) the
 * dashboard holds a single text widget, so the synth never fails.
 */
export class DashboardStack extends Stack {
  constructor(scope: Construct, id: string, props: DashboardStackProps) {
    super(scope, id, props);

    const { cfg } = props;
    const pRegion = cfg.primaryRegion;
    const sRegion = cfg.secondaryRegion;
    const pLabel = `Primary (${pRegion})`;
    const sLabel = `Secondary (${sRegion})`;
    const { primaryAlbFullName: pLb, primaryTgFullName: pTg, secondaryAlbFullName: sLb, secondaryTgFullName: sTg } =
      cfg.dashboard;
    const haveDimensions = [pLb, pTg, sLb, sTg].every((v) => v !== '');

    const perAzQuery = (lb: string) =>
      `SELECT SUM(RequestCount) FROM SCHEMA("AWS/ApplicationELB", LoadBalancer, AvailabilityZone) WHERE LoadBalancer = '${lb}' GROUP BY AvailabilityZone`;

    const widgets: unknown[] = haveDimensions
      ? [
          // Requests stacked: total height = multi-region total, each band = one region.
          {
            type: 'metric', x: 0, y: 0, width: 12, height: 6,
            properties: {
              title: 'Requests - multi-region (stacked total) & per region',
              view: 'timeSeries', stacked: true, region: pRegion, period: 60, stat: 'Sum',
              metrics: [
                ['AWS/ApplicationELB', 'RequestCount', 'LoadBalancer', pLb, { region: pRegion, label: pLabel }],
                ['AWS/ApplicationELB', 'RequestCount', 'LoadBalancer', sLb, { region: sRegion, label: sLabel }],
              ],
            },
          },
          {
            type: 'metric', x: 12, y: 0, width: 12, height: 6,
            properties: {
              title: 'ELB 5xx - per region',
              view: 'timeSeries', stacked: false, region: pRegion, period: 60, stat: 'Sum',
              metrics: [
                ['AWS/ApplicationELB', 'HTTPCode_ELB_5XX_Count', 'LoadBalancer', pLb, { region: pRegion, label: pLabel }],
                ['AWS/ApplicationELB', 'HTTPCode_ELB_5XX_Count', 'LoadBalancer', sLb, { region: sRegion, label: sLabel }],
              ],
            },
          },
          // Per-AZ requests (Metrics Insights auto-discovers AZs).
          {
            type: 'metric', x: 0, y: 6, width: 12, height: 6,
            properties: {
              title: `${pLabel} - requests per AZ`,
              view: 'timeSeries', stacked: true, region: pRegion, period: 60,
              metrics: [[{ expression: perAzQuery(pLb), label: '', id: 'q1' }]],
            },
          },
          {
            type: 'metric', x: 12, y: 6, width: 12, height: 6,
            properties: {
              title: `${sLabel} - requests per AZ`,
              view: 'timeSeries', stacked: true, region: sRegion, period: 60,
              metrics: [[{ expression: perAzQuery(sLb), label: '', id: 'q2' }]],
            },
          },
          {
            type: 'metric', x: 0, y: 12, width: 12, height: 6,
            properties: {
              title: 'Healthy hosts - per region',
              view: 'timeSeries', stacked: false, region: pRegion, period: 60, stat: 'Average',
              metrics: [
                ['AWS/ApplicationELB', 'HealthyHostCount', 'TargetGroup', pTg, 'LoadBalancer', pLb, { region: pRegion, label: pLabel }],
                ['AWS/ApplicationELB', 'HealthyHostCount', 'TargetGroup', sTg, 'LoadBalancer', sLb, { region: sRegion, label: sLabel }],
              ],
            },
          },
          {
            type: 'metric', x: 12, y: 12, width: 12, height: 6,
            properties: {
              title: 'Target response time - per region (avg)',
              view: 'timeSeries', stacked: false, region: pRegion, period: 60, stat: 'Average',
              metrics: [
                ['AWS/ApplicationELB', 'TargetResponseTime', 'LoadBalancer', pLb, { region: pRegion, label: pLabel }],
                ['AWS/ApplicationELB', 'TargetResponseTime', 'LoadBalancer', sLb, { region: sRegion, label: sLabel }],
              ],
            },
          },
        ]
      : [
          {
            type: 'text', x: 0, y: 0, width: 24, height: 3,
            properties: {
              markdown:
                'Load balancer dimensions not provided. Deploy with `deploy-all.sh` / `deploy-all.ps1`, which passes them from the region stack outputs.',
            },
          },
        ];

    // Global Accelerator: new flows per endpoint group (= per region). GA is a
    // global service whose CloudWatch metrics are published only in us-west-2
    // (its home Region in the commercial partition), and only while traffic flows.
    if (haveDimensions && cfg.globalAcceleratorId) {
      widgets.push({
        type: 'metric', x: 0, y: 18, width: 24, height: 6,
        properties: {
          title: 'Global Accelerator - new flows per region (endpoint group)',
          view: 'timeSeries', stacked: true, region: 'us-west-2', period: 60,
          metrics: [[{
            expression: `SELECT SUM(NewFlowCount) FROM SCHEMA("AWS/GlobalAccelerator", Accelerator, Listener, EndpointGroup) WHERE Accelerator = '${cfg.globalAcceleratorId}' GROUP BY EndpointGroup`,
            label: '', id: 'g1',
          }]],
        },
      });
    }

    // CloudWatch dashboards are account-wide, so the name carries the primary
    // region to avoid colliding with other deployments of this lab.
    const dashboardName = `${cfg.project}-traffic-${pRegion}`;
    new cloudwatch.CfnDashboard(this, 'TrafficDashboard', {
      dashboardName,
      dashboardBody: JSON.stringify({ widgets }),
    });

    new CfnOutput(this, 'DashboardUrl', {
      description: 'CloudWatch traffic dashboard console URL',
      // The primary Region is always a commercial-partition Region (the agent
      // profile is only available there), hence the commercial console host.
      value: `https://${pRegion}.console.aws.amazon.com/cloudwatch/home?region=${pRegion}#dashboards/dashboard/${dashboardName}`,
    });
  }
}
