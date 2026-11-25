// Cloudscape One Theme on the published npm packages, which do not include it yet.
// When the open-source One Theme ships, replace this folder with the official import.
import { applyTheme } from '@cloudscape-design/components/theming';
import { setThemeClass, Theme } from '@cloudscape-design/global-styles';
import '@fontsource-variable/roboto/index.css';
import '@fontsource-variable/roboto-mono/index.css';
import './one-theme.css';
import { oneThemeTokens } from './tokens';

let applied = false;

/** Must run before the first render: Cloudscape reads the theme class once. */
export function applyOneTheme(): void {
  if (applied) return;
  applied = true;
  setThemeClass(Theme.OneTheme);
  applyTheme({ theme: oneThemeTokens });
}
