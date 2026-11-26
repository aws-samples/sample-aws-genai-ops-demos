/**
 * Shared colours and severity helpers. Custom elements read Cloudscape design tokens through
 * the `--soc-*` aliases below, so light/dark mode and the One Theme values flow through.
 */
import * as awsui from '@cloudscape-design/design-tokens';
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
export const SEVERITY_ORDER: Record<string, number> = Object.fromEntries([
  ...SEVERITIES.map((s, i): [string, number] => [s, i]),
  ['UNKNOWN', SEVERITIES.length],
]);

export function severityRank(sev: string): number {
  return SEVERITY_ORDER[sev] ?? SEVERITIES.length + 1;
}

// Declared on <body>, next to Cloudscape's own variables, so they resolve per colour mode.
const TOKEN_ALIASES: Record<string, string> = {
  '--soc-font-sans': awsui.fontFamilyBase,
  '--soc-font-mono': awsui.fontFamilyMonospace,
  '--soc-bg': awsui.colorBackgroundLayoutMain,
  '--soc-bg-elev': awsui.colorBackgroundContainerContent,
  '--soc-bg-elev-2': awsui.colorBackgroundCellShaded,
  '--soc-bg-sunken': awsui.colorBackgroundCellShaded,
  '--soc-row-hover-bg': awsui.colorBackgroundDropdownItemHover,
  '--soc-selected-bg': awsui.colorBackgroundItemSelected,
  '--soc-border': awsui.colorBorderDividerDefault,
  '--soc-border-soft': awsui.colorBorderDividerSecondary,
  '--soc-border-strong': awsui.colorBorderInputDefault,
  '--soc-focus': awsui.colorBorderItemFocused,
  '--soc-fg': awsui.colorTextBodyDefault,
  '--soc-fg-heading': awsui.colorTextHeadingDefault,
  '--soc-fg-muted': awsui.colorTextBodySecondary,
  '--soc-fg-dim': awsui.colorTextStatusInactive,
  '--soc-accent': awsui.colorTextAccent,
  '--soc-link': awsui.colorTextLinkDefault,
  '--soc-track': awsui.colorBackgroundProgressBarDefault,
  '--soc-progress': awsui.colorBackgroundProgressBarValueDefault,
  '--soc-critical': awsui.colorChartsStatusCritical,
  '--soc-high': awsui.colorChartsStatusHigh,
  '--soc-medium': awsui.colorChartsStatusMedium,
  '--soc-low': awsui.colorChartsStatusLow,
  '--soc-info': awsui.colorChartsStatusNeutral,
  '--soc-ok': awsui.colorTextStatusSuccess,
  '--soc-error-text': awsui.colorTextStatusError,
  '--soc-warning-text': awsui.colorTextStatusWarning,
  '--soc-info-text': awsui.colorTextStatusInfo,
  '--soc-status-info-bg': awsui.colorBackgroundStatusInfo,
  '--soc-status-success-bg': awsui.colorBackgroundStatusSuccess,
  '--soc-status-error-bg': awsui.colorBackgroundStatusError,
  '--soc-status-warning-bg': awsui.colorBackgroundStatusWarning,
  '--soc-code-bg': awsui.colorBackgroundCellShaded,
  '--soc-code-fg': awsui.colorTextBodyDefault,
  '--soc-popover-bg': awsui.colorBackgroundPopover,
  '--soc-popover-border': awsui.colorBorderPopover,
  '--soc-cat-1': awsui.colorChartsPaletteCategorical1,
  '--soc-cat-2': awsui.colorChartsPaletteCategorical2,
  '--soc-cat-3': awsui.colorChartsPaletteCategorical3,
  '--soc-radius': awsui.borderRadiusContainer,
  '--soc-radius-item': awsui.borderRadiusItem,
  '--soc-radius-badge': awsui.borderRadiusBadge,
  '--soc-radius-popover': awsui.borderRadiusPopover,
  '--soc-backdrop': awsui.colorBackgroundBackdrop,
  '--soc-shadow-raised': awsui.shadowContainerActive,
};

let themeApplied = false;

/** Exposes Cloudscape design tokens to the custom CSS. */
export function applyBrandTheme(): void {
  if (themeApplied) return;
  themeApplied = true;
  const body = document.body.style;
  for (const [name, value] of Object.entries(TOKEN_ALIASES)) body.setProperty(name, value);
}
