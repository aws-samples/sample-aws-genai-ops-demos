import { App } from 'aws-cdk-lib';
import { getRegion } from '../../../../../shared/utils/aws-utils';

/**
 * Deployment configuration, read from CDK context (cdk.json or `-c key=value`).
 * Nothing account- or deployment-specific is committed in cdk.json.
 */
export interface AppConfig {
  /** Name prefix for resources. */
  project: string;
  /** Value of the `project` tag; also scopes the agent's application context. */
  projectTag: string;
  primaryRegion: string;
  secondaryRegion: string;
  primaryVpcCidr: string;
  secondaryVpcCidr: string;
  /** Trusted admin CIDR (e.g. <YOUR_IP>/32). Only this source can reach the ALBs. */
  adminCidr: string;
  instanceType: string;
  ddbTableName: string;
  /** Run each region on a multi-AZ Auto Scaling Group. */
  useAsg: boolean;
  /** Keep a single EC2 instance attached to the target group (single-instance baseline). */
  keepSingleInstance: boolean;
  /**
   * Web tier shape per Region. Defaults carry two deliberate gaps:
   *  - primary: Auto Scaling group confined to ONE Availability Zone (zonal SPOF;
   *    the ALB still spans two AZs, so ARC zonal shift has nowhere to move traffic).
   *  - secondary: fixed capacity of ONE instance (min = max = 1), unable to absorb
   *    the primary Region's traffic on failover (no static stability).
   */
  web: Record<'primary' | 'secondary', WebTierShape>;
  /** Public FQDN served over HTTPS. Empty = plain HTTP:80, no ACM certificate. */
  appDomainName: string;
  /** Public Route 53 hosted zone that owns appDomainName. */
  route53ZoneName: string;
  /** Extra SANs for the ACM certificate (must live in route53ZoneName). */
  appDomainAltNames: string[];
  /**
   * Create the ACM DNS validation CNAMEs from the primary stack. Set false if the
   * CNAMEs already exist in the zone.
   */
  createCertValidationRecords: boolean;
  /** Global Accelerator ID for the optional dashboard row. Empty = skip. */
  globalAcceleratorId: string;
  /**
   * CloudWatch dimension values of both ALBs and target groups, passed by the
   * deploy script from the region stack outputs. Empty until both regions exist.
   */
  dashboard: {
    primaryAlbFullName: string;
    primaryTgFullName: string;
    secondaryAlbFullName: string;
    secondaryTgFullName: string;
  };
}

export interface WebTierShape {
  /** Number of Availability Zones the Auto Scaling group may launch into (1 or 2). */
  azCount: number;
  asgMinSize: number;
  asgMaxSize: number;
}

/**
 * Fallback admin CIDR when `adminCidr` is not passed: a single private address,
 * which allows no public access. `cdk destroy` synthesizes without context, so
 * the synth must not fail when the value is missing.
 */
const NO_ACCESS_CIDR = '10.0.0.0/32';

function ctx(app: App, key: string): unknown {
  return app.node.tryGetContext(key);
}

function str(app: App, key: string, fallback = ''): string {
  const v = ctx(app, key);
  return v === undefined || v === null ? fallback : String(v);
}

// `-c key=true` arrives as the string "true"; cdk.json values arrive as booleans.
function bool(app: App, key: string, fallback: boolean): boolean {
  const v = ctx(app, key);
  if (v === undefined || v === null || v === '') return fallback;
  if (typeof v === 'boolean') return v;
  return String(v).toLowerCase() === 'true';
}

function num(app: App, key: string, fallback: number): number {
  const v = ctx(app, key);
  if (v === undefined || v === null || v === '') return fallback;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 0) throw new Error(`Context "${key}" must be a non-negative integer, got "${v}"`);
  return n;
}

// Accepts a JSON array (cdk.json) or a comma-separated string (`-c appDomainAltNames=a.example.com,b.example.com`).
function list(app: App, key: string): string[] {
  const v = ctx(app, key);
  if (v === undefined || v === null || v === '') return [];
  if (Array.isArray(v)) return v.map(String);
  return String(v).split(',').map((s) => s.trim()).filter(Boolean);
}

const CIDR_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;
const REGION_RE = /^[a-z]{2}(-[a-z]+)+-\d$/;

function assertCidr(key: string, value: string): void {
  const m = CIDR_RE.exec(value);
  const ok = !!m && m.slice(1, 5).every((o) => Number(o) <= 255) && Number(m[5]) <= 32;
  if (!ok) throw new Error(`Context "${key}" must be a valid IPv4 CIDR (e.g. <YOUR_IP>/32), got "${value}"`);
}

/**
 * Workload Region (the primary Region for `multi`): `WA_DEMO_PRIMARY_REGION` when
 * set (the deploy scripts pin it, because they switch AWS_REGION to bootstrap and
 * deploy the other Regions), otherwise the shared getRegion() resolution
 * (AWS_DEFAULT_REGION, AWS_REGION, CLI config). Any Region: only the agent
 * profile is limited to the hosting Regions.
 */
export function resolvePrimaryRegion(): string {
  return process.env.WA_DEMO_PRIMARY_REGION || getRegion();
}

/**
 * Region of the shared agent profile and of every application context stack:
 * `WA_AGENT_REGION` when set, otherwise the workload Region. The deploy scripts get
 * it from the shared prerequisites check (`wellarchitected-agent`), which holds the
 * hosting-Region list and stops in a Region that cannot host a profile. A manual
 * synth is not checked against that list: CloudFormation rejects the profile instead.
 */
export function resolveAgentRegion(workloadRegion: string): string {
  const agentRegion = process.env.WA_AGENT_REGION || workloadRegion;
  if (!REGION_RE.test(agentRegion)) {
    throw new Error(`WA_AGENT_REGION must be an AWS Region code, got "${agentRegion}"`);
  }
  return agentRegion;
}

/**
 * Regions the shared profile scans: `WA_AGENT_SCAN_REGIONS` (comma-separated), which
 * the deploy scripts fill with the account's enabled Regions, or `-c scanRegions=...`.
 * Without either (a manual synth) it falls back to the profile Region alone, which
 * only reviews applications deployed there.
 */
export function resolveScanRegions(app: App, agentRegion: string): string[] {
  const raw = String(app.node.tryGetContext('scanRegions') ?? process.env.WA_AGENT_SCAN_REGIONS ?? '');
  const regions = raw.split(',').map((r) => r.trim()).filter(Boolean);
  for (const r of regions) {
    if (!REGION_RE.test(r)) throw new Error(`Scan Region "${r}" is not an AWS Region code`);
  }
  return regions.length > 0 ? [...new Set(regions)].sort() : [agentRegion];
}

export function loadConfig(app: App): AppConfig {
  const primaryRegion = resolvePrimaryRegion();
  // Precedence: -c secondaryRegion, then WA_DEMO_SECONDARY_REGION. The app keeps
  // no list and picks nothing: the deploy scripts choose the secondary Region
  // (the user's, or one read from the account's enabled Regions) and set the
  // variable, so `cdk destroy` sees the same value.
  const secondaryRegion = str(app, 'secondaryRegion', process.env.WA_DEMO_SECONDARY_REGION || '');
  if (!secondaryRegion) {
    throw new Error(
      'No secondary Region. Deploy with the scripts (they choose one), or pass ' +
        '-c secondaryRegion=<region> / set WA_DEMO_SECONDARY_REGION for a manual synth.',
    );
  }
  if (!REGION_RE.test(secondaryRegion) || secondaryRegion === primaryRegion) {
    throw new Error(`Context "secondaryRegion" must be a Region different from "${primaryRegion}", got "${secondaryRegion}"`);
  }

  const cfg: AppConfig = {
    project: str(app, 'project', 'wa-agent-multi'),
    projectTag: str(app, 'projectTag', 'well-architected-agent-review-multiregion'),
    primaryRegion,
    secondaryRegion,
    primaryVpcCidr: str(app, 'primaryVpcCidr', '10.0.0.0/16'),
    secondaryVpcCidr: str(app, 'secondaryVpcCidr', '10.1.0.0/16'),
    adminCidr: str(app, 'adminCidr', NO_ACCESS_CIDR),
    instanceType: str(app, 'instanceType', 't3.micro'),
    ddbTableName: str(app, 'ddbTableName', 'wa-agent-multi-guestbook'),
    useAsg: bool(app, 'useAsg', true),
    keepSingleInstance: bool(app, 'keepSingleInstance', false),
    web: {
      primary: {
        azCount: num(app, 'primaryWebAzCount', 1),
        asgMinSize: num(app, 'primaryAsgMinSize', 2),
        asgMaxSize: num(app, 'primaryAsgMaxSize', 4),
      },
      secondary: {
        azCount: num(app, 'secondaryWebAzCount', 2),
        asgMinSize: num(app, 'secondaryAsgMinSize', 1),
        asgMaxSize: num(app, 'secondaryAsgMaxSize', 1),
      },
    },
    appDomainName: str(app, 'appDomainName'),
    route53ZoneName: str(app, 'route53ZoneName'),
    appDomainAltNames: list(app, 'appDomainAltNames'),
    createCertValidationRecords: bool(app, 'createCertValidationRecords', true),
    globalAcceleratorId: str(app, 'globalAcceleratorId'),
    dashboard: {
      primaryAlbFullName: str(app, 'primaryAlbFullName'),
      primaryTgFullName: str(app, 'primaryTgFullName'),
      secondaryAlbFullName: str(app, 'secondaryAlbFullName'),
      secondaryTgFullName: str(app, 'secondaryTgFullName'),
    },
  };

  assertCidr('adminCidr', cfg.adminCidr);
  assertCidr('primaryVpcCidr', cfg.primaryVpcCidr);
  assertCidr('secondaryVpcCidr', cfg.secondaryVpcCidr);

  if (cfg.appDomainName && !cfg.route53ZoneName) {
    throw new Error('Context "route53ZoneName" is required when "appDomainName" is set.');
  }
  if (!cfg.useAsg && !cfg.keepSingleInstance) {
    throw new Error('Nothing would serve traffic: set useAsg=true and/or keepSingleInstance=true.');
  }
  for (const role of ['primary', 'secondary'] as const) {
    const w = cfg.web[role];
    if (w.azCount !== 1 && w.azCount !== 2) {
      throw new Error(`${role}WebAzCount must be 1 or 2, got ${w.azCount}.`);
    }
    if (w.asgMinSize < 1) {
      throw new Error(`${role}AsgMinSize must be at least 1.`);
    }
    if (w.asgMaxSize < w.asgMinSize) {
      throw new Error(`${role}AsgMaxSize must be >= ${role}AsgMinSize.`);
    }
  }
  return cfg;
}
