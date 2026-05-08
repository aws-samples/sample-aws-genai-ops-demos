import { Finding } from './api';

export interface FrameworkDef {
  key: string;
  label: string;
  description: string;
  match: RegExp;
}

// Hand-curated framework catalog. Match against OCSF unmapped.compliance keys,
// check_id, and check_title so we catch findings even when the ingestion
// pipeline didn't tag the framework explicitly.
export const FRAMEWORKS: FrameworkDef[] = [
  { key: 'CIS',       label: 'CIS AWS',   description: 'Center for Internet Security AWS Foundations Benchmark',    match: /\bCIS\b/i },
  { key: 'PCI',       label: 'PCI DSS',   description: 'Payment Card Industry Data Security Standard',              match: /\bPCI\b/i },
  { key: 'NIST',      label: 'NIST',      description: 'NIST 800-53 and NIST CSF security controls',                match: /\bNIST\b/i },
  { key: 'AWS-FSBP',  label: 'AWS FSBP',  description: 'AWS Foundational Security Best Practices',                  match: /\b(FSBP|AWS-Foundational)\b/i },
  { key: 'HIPAA',     label: 'HIPAA',     description: 'Health Insurance Portability and Accountability Act',       match: /\bHIPAA\b/i },
  { key: 'ISO27001',  label: 'ISO 27001', description: 'ISO/IEC 27001 Information Security Management',             match: /\bISO[- ]?27001\b/i },
  { key: 'SOC2',      label: 'SOC 2',     description: 'System and Organization Controls (AICPA Trust Services)',   match: /\bSOC[- ]?2\b/i },
  { key: 'GDPR',      label: 'GDPR',      description: 'EU General Data Protection Regulation',                     match: /\bGDPR\b/i },
];

export function matchesFramework(f: Finding, match: RegExp): boolean {
  const blob = [
    ...(f.compliance_frameworks || []),
    f.check_id || '',
    f.check_title || '',
  ].join(' ').toUpperCase();
  return match.test(blob);
}

export function getFrameworkByKey(key: string): FrameworkDef | undefined {
  return FRAMEWORKS.find((f) => f.key === key);
}

/** Short labels shown in the Findings table Compliance column. */
export function frameworkLabelsForFinding(f: Finding): string[] {
  return FRAMEWORKS.filter((fw) => matchesFramework(f, fw.match)).map((fw) => fw.label);
}
