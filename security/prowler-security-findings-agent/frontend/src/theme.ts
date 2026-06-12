/**
 * Shared theme tokens — all values reference CSS custom properties defined in
 * `index.css`, so switching the theme (light/dark/system) via `applyTheme`
 * automatically repaints every consumer without JS re-renders.
 */
import { SEVERITIES } from './constants';

export const COLOR = {
  fg:        'var(--soc-fg)',
  fgMuted:   'var(--soc-fg-muted)',
  fgDim:     'var(--soc-fg-dim)',
  accent:    'var(--soc-accent)',
  border:    'var(--soc-border)',
  critical:  'var(--soc-critical)',
  high:      'var(--soc-high)',
  medium:    'var(--soc-medium)',
  low:       'var(--soc-low)',
  info:      'var(--soc-info)',
  ok:        'var(--soc-ok)',
} as const;

/** 0 = CRITICAL (top of the list), 5 = INFO (bottom). Used for sorting. */
export const SEVERITY_ORDER: Record<string, number> = Object.fromEntries(
  SEVERITIES.map((s, i) => [s, i]).concat([['UNKNOWN', SEVERITIES.length]]),
);

export function severityRank(sev: string): number {
  return SEVERITY_ORDER[sev] ?? SEVERITIES.length + 1;
}
