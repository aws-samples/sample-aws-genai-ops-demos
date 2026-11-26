// Regenerates src/one-theme/tokens.ts and src/one-theme/one-theme.css for the
// @cloudscape-design/components version installed in node_modules, from a Cloudscape source build
// that includes One Theme (the published package is built without it).
//
// Prepare the source build once per Cloudscape version (public repo, a few minutes):
//   git clone https://github.com/cloudscape-design/components <dir> && cd <dir>
//   git checkout <GIT_SHA in node_modules/@cloudscape-design/components/internal/environment.js>
//   Edit build-tools/utils/themes.js so the theme matches the published package:
//     primaryThemePath: './visual-refresh/index.js', secondaryThemePaths: ['./one-theme/index.js'],
//     alwaysVisualRefresh: true
//   npm install && INCLUDE_ONE_THEME=true npx gulp quick-build
// Then, from frontend/:
//   node scripts/gen-one-theme.mjs <dir>
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const NPM = path.join(FRONT, 'node_modules/@cloudscape-design/components');
const OUT = path.join(FRONT, 'src/one-theme');
if (!process.argv[2]) {
  console.error('usage: node scripts/gen-one-theme.mjs <cloudscape-components-checkout>');
  process.exit(1);
}
const CS = path.resolve(process.argv[2]);
const REF = path.join(CS, 'lib/components');

function fail(message) {
  console.error(`gen-one-theme: ${message}`);
  process.exit(1);
}

function headSha(repo) {
  const gitDir = path.join(repo, '.git');
  const head = fs.readFileSync(path.join(gitDir, 'HEAD'), 'utf8').trim();
  if (!head.startsWith('ref: ')) return head;
  const ref = head.slice(5);
  const loose = path.join(gitDir, ref);
  if (fs.existsSync(loose)) return fs.readFileSync(loose, 'utf8').trim();
  const packed = fs.readFileSync(path.join(gitDir, 'packed-refs'), 'utf8');
  const line = packed.split('\n').find((l) => l.endsWith(` ${ref}`));
  return line ? line.split(' ')[0] : '';
}

const npmEnv = fs.readFileSync(path.join(NPM, 'internal/environment.js'), 'utf8');
const SOURCE_SHA = (/GIT_SHA = "([0-9a-f]+)"/.exec(npmEnv) || [])[1];
const NPM_VERSION = JSON.parse(fs.readFileSync(path.join(NPM, 'package.json'), 'utf8')).version;
if (!SOURCE_SHA) fail('cannot read GIT_SHA from the installed @cloudscape-design/components');
if (!fs.existsSync(path.join(REF, 'internal/generated/theming/index.cjs'))) fail(`no source build in ${REF}`);
if (!headSha(CS).startsWith(SOURCE_SHA)) {
  fail(`${CS} is not checked out at ${SOURCE_SHA}, the commit @cloudscape-design/components ${NPM_VERSION} was built from`);
}

const require = createRequire(path.join(CS, 'package.json'));
const postcss = require('postcss');
const { resolveTheme, resolveContext } = require(
  path.join(CS, 'node_modules/@cloudscape-design/theming-build/shared/theme/index.js'),
);

const R = require(path.join(REF, 'internal/generated/theming/index.cjs')).preset;
const N = require(path.join(NPM, 'internal/generated/theming/index.cjs')).preset;
const OT = (R.secondary || []).find((t) => t.id === 'one-theme');
const VR = N.theme;
if (!OT) fail('the source build has no One Theme: build it with INCLUDE_ONE_THEME=true');
if (JSON.stringify(R.theme) !== JSON.stringify(N.theme)) {
  fail('the source build does not use the published theme setup: check build-tools/utils/themes.js');
}

// Ember Modern is not public: keep the One Theme stacks and add the bundled open fallbacks.
const FONT_OVERRIDES = {
  fontFamilyBase: "'Ember Modern Text UI', 'Amazon Ember', 'Roboto Variable', Roboto, Arial, sans-serif",
  fontFamilyHeading: "'Ember Modern Text UI', 'Amazon Ember', 'Roboto Variable', Roboto, Arial, sans-serif",
  fontFamilyDisplay: "'Ember Modern Text UI', 'Amazon Ember', 'Roboto Variable', Roboto, Arial, sans-serif",
  fontFamilyMonospace:
    "'Ember Modern Mono', 'Roboto Mono Variable', Monaco, Menlo, Consolas, 'Courier Prime', Courier, 'Courier New', monospace",
};

const themeable = new Set(N.themeable);
const varToToken = Object.fromEntries(Object.entries(N.propertiesMap).map(([t, v]) => [v, t]));
const refVarToToken = Object.fromEntries(Object.entries(R.propertiesMap).map(([t, v]) => [v, t]));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const compact = (v) => {
  if (v && typeof v === 'object') {
    const vals = Object.values(v);
    if (vals.every((x) => x === vals[0])) return vals[0];
  }
  return v;
};

// Global tokens for applyTheme(). A token whose resolved value is unchanged but whose definition
// references a changed token is included too: the npm CSS declares references as var().
const ro = resolveTheme(OT);
const rv = resolveTheme(VR);
const tokens = {};
for (const k of Object.keys(rv)) {
  const rawChanged = !same(OT.tokens[k], VR.tokens[k]);
  if (same(ro[k], rv[k]) && !rawChanged && !(k in FONT_OVERRIDES)) continue;
  if (themeable.has(k)) tokens[k] = k in FONT_OVERRIDES ? FONT_OVERRIDES[k] : compact(ro[k]);
}

// Contexts for applyTheme(), except those the npm build disables under One Theme.
const contexts = {};
const gated = new Set(Object.entries(VR.contexts).filter(([, c]) => c.selector.includes(':not(.awsui-one-theme)')).map(([id]) => id));
for (const [id, ctx] of Object.entries(OT.contexts)) {
  if (gated.has(id) || !VR.contexts[id]) continue;
  const rco = resolveContext(OT, ctx);
  const rcv = resolveContext(VR, VR.contexts[id]);
  const vrCtxTokens = new Set(Object.keys(VR.contexts[id].tokens));
  const ctxTokens = {};
  for (const k of Object.keys(rco)) {
    if (!themeable.has(k) || k in FONT_OVERRIDES) continue;
    if (!same(rco[k], ro[k]) || (vrCtxTokens.has(k) && !same(rco[k], rcv[k]))) ctxTokens[k] = compact(rco[k]);
  }
  if (Object.keys(ctxTokens).length) contexts[id] = { tokens: ctxTokens };
}

const GENERATED = `Generated by scripts/gen-one-theme.mjs from cloudscape-design/components ${SOURCE_SHA} (INCLUDE_ONE_THEME=true) for @cloudscape-design/components ${NPM_VERSION}.`;
const ts = [
  `// ${GENERATED}`,
  "import type { Theme } from '@cloudscape-design/components/theming';",
  '',
  'export const oneThemeTokens: Theme = ' + JSON.stringify({ tokens, contexts }, null, 2).replace(/"([a-zA-Z0-9]+)":/g, '$1:') + ';',
  '',
].join('\n');

// Token declarations applyTheme() cannot set: non-themeable tokens and context values (applyTheme
// drops context values equal to its root value, which lets the npm context rules win). The
// html:not(#\9) prefix lifts them above the npm base theme and the applyTheme() stylesheet.
const keepVar = (prop, rule) => {
  const token = varToToken[prop] || refVarToToken[prop];
  if (!token || token in FONT_OVERRIDES) return false;
  if (!(token in N.propertiesMap)) return true;
  return /awsui-context-/.test(rule.selector) || !themeable.has(token);
};
const tokenRoot = postcss.root();
postcss.parse(fs.readFileSync(path.join(REF, 'internal/base-theme/styles.scoped.css'), 'utf8')).walkRules((rule) => {
  const sels = rule.selectors.filter((s) => s.includes('awsui-one-theme') && !s.includes(':not(.awsui-one-theme)'));
  if (!sels.length) return;
  const decls = rule.nodes.filter((d) => d.type === 'decl' && keepVar(d.prop, rule));
  if (!decls.length) return;
  const r = postcss.rule({ selectors: sels.map((s) => 'html:not(#\\9) ' + s) });
  decls.forEach((d) => r.append(d.clone()));
  const parent = rule.parent;
  if (parent.type === 'atrule' && parent.name === 'media') {
    let at = tokenRoot.nodes.find((n) => n.type === 'atrule' && n.name === 'media' && n.params === parent.params);
    if (!at) {
      at = postcss.atRule({ name: 'media', params: parent.params });
      tokenRoot.append(at);
    }
    at.append(r);
  } else {
    tokenRoot.append(r);
  }
});

// Component rules. One Theme rules sit between Visual Refresh rules in each source file, so later
// rules that set a property family an earlier One Theme rule set are re-emitted in source order.
const CLASS_RE = /\.awsui_([A-Za-z0-9-]+?)_([a-z0-9]{1,5})_([a-z0-9]{1,5})_(\d+)(?![A-Za-z0-9_-])/g;
const stable = (sel) => sel.replace(CLASS_RE, (_m, name, file) => `[class*="awsui_${name}_${file}_"]`);
const FAMILIES = [
  [/^(margin|padding|inset|gap|overflow|outline|background|transition|animation|grid|flex|list-style|text-decoration|font|columns)\b/, 1],
  [/^border(-(top|right|bottom|left|block|inline)(-(start|end))?)?-radius$|^border-(start|end)-(start|end)-radius$/, 'border-radius'],
  [/^border/, 'border'],
  [/^(top|right|bottom|left)$/, 'inset'],
  [/^(row-gap|column-gap)$/, 'gap'],
];
const family = (prop) => {
  if (prop.startsWith('--')) return prop;
  for (const [re, name] of FAMILIES) {
    const m = re.exec(prop);
    if (m) return name === 1 ? m[1] : name;
  }
  return prop;
};
const compRoot = postcss.root();
const keyframesUsed = new Set();
const keyframes = new Map();
const files = [];
(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith('.scoped.css') && !p.includes(path.join('internal', 'base-theme'))) files.push(p);
  }
})(REF);
files.sort();
for (const f of files) {
  const css = postcss.parse(fs.readFileSync(f, 'utf8'));
  css.walkAtRules('keyframes', (at) => {
    keyframes.set(at.params, at);
  });
  const picked = [];
  const otProps = new Set();
  css.walkRules((rule) => {
    if (rule.parent.type === 'atrule' && rule.parent.name === 'keyframes') return;
    const isOt = rule.selector.includes('awsui-one-theme');
    const decls = rule.nodes.filter((d) => d.type === 'decl');
    const kept = isOt ? rule.nodes : decls.filter((d) => otProps.has(family(d.prop)));
    if (!kept.length) return;
    const sels = isOt ? rule.selectors.filter((s) => s.includes('awsui-one-theme')) : rule.selectors;
    const r = postcss.rule({ selectors: sels.map(stable) });
    kept.forEach((d) => {
      r.append(d.clone());
      if (isOt && d.type === 'decl') otProps.add(family(d.prop));
      if (d.type === 'decl' && /^animation(-name)?$/.test(d.prop)) {
        d.value.split(/[\s,]+/).forEach((tok) => keyframesUsed.add(tok));
      }
    });
    let node = r;
    let parent = rule.parent;
    while (parent && parent.type === 'atrule') {
      const wrap = postcss.atRule({ name: parent.name, params: parent.params });
      wrap.append(node);
      node = wrap;
      parent = parent.parent;
    }
    picked.push(node);
  });
  if (picked.length) {
    compRoot.append(postcss.comment({ text: ' ' + path.relative(REF, f).replace(/\/styles\.scoped\.css$/, '') + ' ' }));
    picked.forEach((n) => compRoot.append(n));
  }
}
const kfRoot = postcss.root();
for (const name of keyframesUsed) {
  const at = keyframes.get(name);
  if (at) kfRoot.append(at.clone());
}

const fmt = (root) => root.toString().replace(/\n{3,}/g, '\n\n').trim();
const css = [
  '/*',
  ' * Cloudscape One Theme for the published @cloudscape-design/components package, which is built without it.',
  ` * ${GENERATED}`,
  ' * Source: Apache-2.0.',
  ' */',
  '',
  '/* Design tokens that applyTheme() cannot set: non-themeable tokens and context values. */',
  fmt(tokenRoot),
  '',
  '/* Component rules. Hashed class names are matched by their stable prefix. */',
  fmt(compRoot),
  '',
  fmt(kfRoot),
  '',
  '.awsui-one-theme b,',
  '.awsui-one-theme strong {',
  '  font-weight: 600;',
  '}',
  '',
].join('\n');

fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, 'tokens.ts'), ts);
fs.writeFileSync(path.join(OUT, 'one-theme.css'), css);
console.log(
  `gen-one-theme: ${Object.keys(tokens).length} tokens, ${Object.keys(contexts).length} contexts, ` +
    `${compRoot.nodes.filter((n) => n.type === 'comment').length} component files for ${NPM_VERSION}`,
);
