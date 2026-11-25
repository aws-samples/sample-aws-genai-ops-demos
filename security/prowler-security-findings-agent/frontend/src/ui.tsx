import { Badge } from '@cloudscape-design/components';

export const SEVERITY_LABEL: Record<string, string> = {
  CRITICAL: 'Critical',
  HIGH: 'High',
  MEDIUM: 'Medium',
  LOW: 'Low',
  INFO: 'Info',
  UNKNOWN: 'Unknown',
};

type BadgeColor = 'severity-critical' | 'severity-high' | 'severity-medium' | 'severity-low' | 'severity-neutral';

const SEVERITY_BADGE: Record<string, BadgeColor> = {
  CRITICAL: 'severity-critical',
  HIGH: 'severity-high',
  MEDIUM: 'severity-medium',
  LOW: 'severity-low',
};

export function severityLabel(sev?: string): string {
  if (!sev) return 'Unknown';
  return SEVERITY_LABEL[sev] ?? sev.charAt(0) + sev.slice(1).toLowerCase();
}

/** Cloudscape's severity badge, so severity reads the same as in Security Hub. */
export function SeverityBadge({ severity }: { severity?: string }) {
  const sev = severity || 'UNKNOWN';
  return (
    <span className="soc-sev-badge">
      <Badge color={SEVERITY_BADGE[sev] ?? 'severity-neutral'}>{severityLabel(sev)}</Badge>
    </span>
  );
}

/** Product mark: four bars stepping down Cloudscape's severity palette, the triage queue in miniature. */
export function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect width="24" height="24" rx="4" fill="#161512" />
      <rect x="5" y="5.5" width="14" height="2.6" rx="1.3" fill="#d63f38" />
      <rect x="5" y="9.3" width="11" height="2.6" rx="1.3" fill="#fe6e73" />
      <rect x="5" y="13.1" width="8" height="2.6" rx="1.3" fill="#f89256" />
      <rect x="5" y="16.9" width="5" height="2.6" rx="1.3" fill="#f2cd54" />
    </svg>
  );
}
