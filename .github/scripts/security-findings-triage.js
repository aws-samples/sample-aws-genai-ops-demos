// Security Findings Triage: turns open Dependabot alerts into one GitHub issue
// per demo, assigned to the demo's owners from CODEOWNERS.
//
// Runs inside actions/github-script (see .github/workflows/security-findings-triage.yml).
// CommonJS, no npm dependencies. The pure helpers are exported so they can be
// unit-tested with `node --test .github/scripts/`.

'use strict';

const PILLARS = [
  'cost-optimization',
  'observability',
  'operations-automation',
  'resilience',
  'security',
];
const SHARED_KEY = 'shared';
const DEFAULT_MAINTAINERS = ['bquintas', 'benlec', 'emenguy'];
const LABEL = 'security-finding';
const MARKER_RE = /<!-- security-triage:(\S+?) -->/;
const ALERTS_MARKER_RE = /<!-- security-triage-alerts:([\d,]*) -->/;
const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low'];
const CODEOWNERS_PATHS = ['.github/CODEOWNERS', 'CODEOWNERS', 'docs/CODEOWNERS'];

// ---------------------------------------------------------------------------
// CODEOWNERS
// ---------------------------------------------------------------------------

/** Convert a CODEOWNERS (gitignore-style) pattern to an anchored RegExp. */
function patternToRegex(pattern) {
  let p = pattern;
  const dirOnly = p.endsWith('/');
  if (dirOnly) p = p.replace(/\/+$/, '');
  // A leading slash, or a slash in the middle, anchors the pattern to the repo root.
  const anchored = p.startsWith('/') || p.includes('/');
  p = p.replace(/^\/+/, '');

  let re = '';
  for (let i = 0; i < p.length; i++) {
    const c = p[i];
    if (c === '*') {
      if (p[i + 1] === '*') {
        const prevSlash = i === 0 || p[i - 1] === '/';
        const nextSlash = p[i + 2] === '/';
        if (prevSlash && nextSlash) { re += '(?:.*/)?'; i += 2; continue; } // "**/"
        if (prevSlash && i + 2 === p.length) { re += '.*'; i += 1; continue; } // trailing "/**"
        re += '.*'; i += 1; continue;
      }
      re += '[^/]*';
    } else if (c === '?') {
      re += '[^/]';
    } else {
      re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    }
  }
  const prefix = anchored ? '^' : '^(?:.*/)?';
  // "dir/" matches only things below dir; "name" matches the file or anything below it.
  const suffix = dirOnly ? '/.*$' : '(?:/.*)?$';
  return new RegExp(prefix + re + suffix);
}

/** Parse CODEOWNERS text into ordered rules: { pattern, owners: [login], regex }. */
function parseCodeowners(text) {
  const rules = [];
  for (const raw of String(text || '').split(/\r?\n/)) {
    const line = raw.replace(/(^|\s)#.*$/, '').trim();
    if (!line) continue;
    const [pattern, ...tokens] = line.split(/\s+/);
    // Users only: teams (@org/team) and e-mails cannot be issue assignees.
    const owners = tokens
      .filter((t) => t.startsWith('@') && !t.includes('/'))
      .map((t) => t.slice(1));
    rules.push({ pattern, owners, regex: patternToRegex(pattern) });
  }
  return rules;
}

/** Owners for a path: last matching rule wins. Returns null when no rule matches. */
function ownersFor(rules, path) {
  const p = String(path).replace(/^\/+/, '');
  let match = null;
  for (const rule of rules) if (rule.regex.test(p)) match = rule;
  return match ? match.owners : null;
}

/** Maintainers = owners of the /.github/ line; fallback to the default pair. */
function maintainersFrom(rules) {
  const gh = rules.filter((r) => r.pattern.replace(/^\/+|\/+$/g, '') === '.github').pop();
  return gh && gh.owners.length ? gh.owners : DEFAULT_MAINTAINERS.slice();
}

// ---------------------------------------------------------------------------
// Grouping and ownership
// ---------------------------------------------------------------------------

/** Demo key = "<pillar>/<demo>" for paths under a pillar folder, otherwise "shared". */
function demoKey(manifestPath) {
  const parts = String(manifestPath || '').replace(/^\/+/, '').split('/');
  if (parts.length >= 3 && PILLARS.includes(parts[0])) return `${parts[0]}/${parts[1]}`;
  return SHARED_KEY;
}

function severityOf(alert) {
  return String(
    (alert.security_vulnerability && alert.security_vulnerability.severity) ||
      (alert.security_advisory && alert.security_advisory.severity) ||
      'unknown',
  ).toLowerCase();
}

function severityRank(sev) {
  const i = SEVERITY_ORDER.indexOf(sev);
  return i === -1 ? SEVERITY_ORDER.length : i;
}

/** Group alerts by demo. Returns Map<demo, alert[]> sorted by severity then number. */
function groupAlerts(alerts) {
  const groups = new Map();
  for (const a of alerts) {
    const key = demoKey(a.dependency && a.dependency.manifest_path);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(a);
  }
  for (const list of groups.values()) {
    list.sort((x, y) => severityRank(severityOf(x)) - severityRank(severityOf(y)) || x.number - y.number);
  }
  return new Map([...groups.entries()].sort(([a], [b]) => a.localeCompare(b)));
}

const lower = (s) => s.toLowerCase();

function uniqueCi(list) {
  const seen = new Set();
  return list.filter((u) => (seen.has(lower(u)) ? false : (seen.add(lower(u)), true)));
}

/**
 * Owners and authors of a demo. Authors = owners minus maintainers.
 * "shared" and unmatched paths route to the maintainers.
 */
function resolveOwners(demo, manifestPaths, rules, maintainers) {
  if (demo === SHARED_KEY) return { owners: maintainers.slice(), authors: [] };
  let owners = [];
  for (const path of manifestPaths) {
    const o = ownersFor(rules, path);
    if (o && o.length) owners.push(...o);
  }
  owners = uniqueCi(owners);
  if (!owners.length) owners = maintainers.slice();
  const m = new Set(maintainers.map(lower));
  const authors = owners.filter((o) => !m.has(lower(o)));
  return { owners, authors };
}

/**
 * Decide who gets assigned and who is only @-mentioned.
 * isAssignable: (login) => boolean.
 */
function planAssignees({ owners, authors, maintainers }, isAssignable) {
  const unassignableAuthors = authors.filter((a) => !isAssignable(a));
  const assignableAuthors = authors.filter((a) => isAssignable(a));
  if (assignableAuthors.length) {
    return { assignees: assignableAuthors, mentions: unassignableAuthors };
  }
  // No assignable author: fall back to the maintainers (the owners themselves when no author exists).
  const pool = authors.length ? maintainers : owners;
  return { assignees: uniqueCi(pool).filter((u) => isAssignable(u)), mentions: unassignableAuthors };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function cell(value) {
  return String(value == null || value === '' ? 'n/a' : value)
    .replace(/[\r\n]+/g, ' ')
    .replace(/`/g, "'")
    // Escape backslashes first so an input ending in "\" can't cancel the pipe escape.
    .replace(/\\/g, '\\\\')
    .replace(/\|/g, '\\|');
}

function alertNumbers(alerts) {
  return alerts.map((a) => a.number).sort((x, y) => x - y);
}

function issueTitle(demo) {
  return `Dependabot alerts: ${demo}`;
}

function renderBody({ demo, alerts, mentions, maintainers }) {
  const nums = alertNumbers(alerts);
  const owner = mentions.length
    ? ` Owner: ${mentions.map((u) => `@${u}`).join(' ')} (not assignable, maintainers assigned).`
    : '';
  const rows = alerts.map((a) => {
    const v = a.security_vulnerability || {};
    const patched = v.first_patched_version && v.first_patched_version.identifier;
    return `| [#${a.number}](${a.html_url}) | ${cell(severityOf(a))} | ${cell(a.dependency.package && a.dependency.package.name)} | \`${cell(a.dependency.manifest_path)}\` | \`${cell(v.vulnerable_version_range)}\` | ${patched ? `\`${cell(patched)}\`` : 'none'} |`;
  });
  const who = maintainers.map((u) => `@${u}`).join(' or ');
  return [
    `<!-- security-triage:${demo} -->`,
    `<!-- security-triage-alerts:${nums.join(',')} -->`,
    `Open Dependabot alerts for \`${demo}\`.${owner}`,
    '',
    '| Alert | Severity | Package | Manifest | Vulnerable | First patched |',
    '|---|---|---|---|---|---|',
    ...rows,
    '',
    '- [ ] Real: open a PR that bumps the package.',
    `- [ ] Doesn't apply: comment why with a reason (\`not_used\`, \`tolerable_risk\`, \`inaccurate\`) and assign ${who}, who dismiss the alert.`,
    '',
    'Please don\'t close it by hand: a new one is opened next week while the alerts are still open.',
    'This issue closes automatically when all alerts for this demo are fixed or dismissed.',
    '',
  ].join('\n');
}

function parseDemoMarker(body) {
  const m = MARKER_RE.exec(body || '');
  return m ? m[1] : null;
}

function parseAlertSet(body) {
  const m = ALERTS_MARKER_RE.exec(body || '');
  if (!m) return null;
  return m[1] ? m[1].split(',').map(Number) : [];
}

// ---------------------------------------------------------------------------
// Planning (pure): what to create / update / close
// ---------------------------------------------------------------------------

/**
 * desired: Map<demo, { title, body, alertNumbers, assignees }>
 * existing: [{ number, body, assignees: [login] }] open issues carrying the label.
 * Returns an ordered list of actions.
 */
function planActions(desired, existing) {
  const byDemo = new Map();
  for (const issue of [...existing].sort((a, b) => a.number - b.number)) {
    const demo = parseDemoMarker(issue.body);
    if (demo && !byDemo.has(demo)) byDemo.set(demo, issue);
  }
  const actions = [];
  for (const [demo, want] of desired) {
    const issue = byDemo.get(demo);
    if (!issue) {
      actions.push({ type: 'create', demo, ...want });
      continue;
    }
    const before = parseAlertSet(issue.body) || [];
    const added = want.alertNumbers.filter((n) => !before.includes(n));
    const resolved = before.filter((n) => !want.alertNumbers.includes(n));
    const have = new Set((issue.assignees || []).map(lower));
    const addAssignees = want.assignees.filter((u) => !have.has(lower(u)));
    const bodyChanged = normalizeBody(issue.body) !== normalizeBody(want.body);
    if (!bodyChanged && !addAssignees.length) {
      actions.push({ type: 'noop', demo, number: issue.number });
      continue;
    }
    const setChanged = added.length > 0 || resolved.length > 0;
    actions.push({
      type: 'update',
      demo,
      number: issue.number,
      body: bodyChanged ? want.body : null,
      addAssignees,
      comment: setChanged ? renderChangeComment(added, resolved) : null,
    });
  }
  for (const [demo, issue] of byDemo) {
    if (!desired.has(demo)) {
      actions.push({
        type: 'close',
        demo,
        number: issue.number,
        comment: `All Dependabot alerts for \`${demo}\` are fixed or dismissed. Closing automatically.`,
      });
    }
  }
  return actions;
}

// Ignore line endings and ticked checkboxes so a human ticking the checklist
// does not make every rerun rewrite the body.
function normalizeBody(body) {
  return String(body || '')
    .replace(/\r\n/g, '\n')
    .replace(/^- \[[xX]\] /gm, '- [ ] ')
    .trim();
}

function renderChangeComment(added, resolved) {
  const parts = [];
  // No "#N": GitHub would link it to issue/PR N and post a cross-reference on it.
  if (added.length) parts.push(`new: ${added.join(', ')}`);
  if (resolved.length) parts.push(`resolved: ${resolved.join(', ')}`);
  return `Dependabot alerts changed (${parts.join('; ')}). Table updated.`;
}

// ---------------------------------------------------------------------------
// Runtime (GitHub API)
// ---------------------------------------------------------------------------

async function readCodeowners(github, owner, repo, ref) {
  for (const path of CODEOWNERS_PATHS) {
    try {
      const { data } = await github.rest.repos.getContent({ owner, repo, path, ref });
      if (data && data.content) return { path, text: Buffer.from(data.content, 'base64').toString('utf8') };
    } catch (e) {
      if (e.status !== 404) throw e;
    }
  }
  return { path: null, text: '' };
}

async function main({ github, context, core, dryRun = false, codeownersText }) {
  const { owner, repo } = context.repo;
  const tag = dryRun ? '[dry-run] would ' : '';
  const defaultBranch =
    (context.payload && context.payload.repository && context.payload.repository.default_branch) || 'main';

  let alerts;
  try {
    alerts = await github.paginate(github.rest.dependabot.listAlertsForRepo, {
      owner, repo, state: 'open', per_page: 100,
    });
  } catch (e) {
    if (e.status === 403 || e.status === 404) {
      core.setFailed(
        `Cannot read Dependabot alerts (HTTP ${e.status}): grant the job "vulnerability-alerts: read" and make sure Dependabot alerts are enabled for ${owner}/${repo}.`,
      );
      return { actions: [] };
    }
    throw e;
  }

  let source = 'input';
  if (codeownersText === undefined) {
    const found = await readCodeowners(github, owner, repo, defaultBranch);
    codeownersText = found.text;
    source = found.path ? `${found.path}@${defaultBranch}` : 'none';
  }
  const rules = parseCodeowners(codeownersText);
  const maintainers = maintainersFrom(rules);
  core.info(`Open alerts: ${alerts.length}. CODEOWNERS: ${source} (${rules.length} rules). Maintainers: ${maintainers.join(', ')}`);

  const assignableCache = new Map();
  const checkAssignable = async (login) => {
    const k = lower(login);
    if (!assignableCache.has(k)) {
      let ok = false;
      try {
        await github.rest.issues.checkUserCanBeAssigned({ owner, repo, assignee: login });
        ok = true;
      } catch (e) {
        if (e.status !== 404) throw e;
      }
      assignableCache.set(k, ok);
    }
    return assignableCache.get(k);
  };

  const groups = groupAlerts(alerts);
  const desired = new Map();
  for (const [demo, list] of groups) {
    const who = resolveOwners(demo, list.map((a) => a.dependency.manifest_path), rules, maintainers);
    for (const u of uniqueCi([...who.owners, ...maintainers])) await checkAssignable(u);
    const plan = planAssignees({ ...who, maintainers }, (u) => assignableCache.get(lower(u)) === true);
    if (!plan.assignees.length) core.warning(`${demo}: nobody assignable among ${who.owners.join(', ')}`);
    desired.set(demo, {
      title: issueTitle(demo),
      body: renderBody({ demo, alerts: list, mentions: plan.mentions, maintainers }),
      alertNumbers: alertNumbers(list),
      assignees: plan.assignees,
      mentions: plan.mentions,
    });
  }

  const openIssues = (
    await github.paginate(github.rest.issues.listForRepo, {
      owner, repo, state: 'open', labels: LABEL, per_page: 100,
    })
  )
    .filter((i) => !i.pull_request)
    .map((i) => ({ number: i.number, body: i.body || '', assignees: (i.assignees || []).map((a) => a.login) }));

  const actions = planActions(desired, openIssues);

  if (actions.some((a) => a.type === 'create')) {
    let exists = true;
    try {
      await github.rest.issues.getLabel({ owner, repo, name: LABEL });
    } catch (e) {
      if (e.status !== 404) throw e;
      exists = false;
    }
    if (!exists) {
      core.info(`${tag}create label "${LABEL}"`);
      if (!dryRun) {
        await github.rest.issues.createLabel({
          owner, repo, name: LABEL, color: 'b60205', description: 'Open Dependabot alerts for a demo',
        });
      }
    }
  }

  const at = (list) => (list.length ? list.map((u) => `@${u}`).join(', ') : 'none');
  for (const a of actions) {
    const nums = a.alertNumbers ? a.alertNumbers.map((n) => `#${n}`).join(', ') : '';
    if (a.type === 'create') {
      core.info(`${tag}create issue "${a.title}" alerts=${nums} assignees=${at(a.assignees)} mentions=${at(a.mentions)}`);
      if (dryRun) {
        core.info(a.body);
      } else {
        await github.rest.issues.create({ owner, repo, title: a.title, body: a.body, labels: [LABEL], assignees: a.assignees });
      }
    } else if (a.type === 'update') {
      core.info(
        `${tag}update issue #${a.number} (${a.demo}) body=${a.body ? 'changed' : 'unchanged'} addAssignees=${at(a.addAssignees)} comment=${a.comment ? `"${a.comment}"` : 'none'}`,
      );
      if (!dryRun) {
        if (a.body) await github.rest.issues.update({ owner, repo, issue_number: a.number, body: a.body });
        if (a.addAssignees.length) await github.rest.issues.addAssignees({ owner, repo, issue_number: a.number, assignees: a.addAssignees });
        if (a.comment) await github.rest.issues.createComment({ owner, repo, issue_number: a.number, body: a.comment });
      }
    } else if (a.type === 'close') {
      core.info(`${tag}close issue #${a.number} (${a.demo}) with comment "${a.comment}"`);
      if (!dryRun) {
        await github.rest.issues.createComment({ owner, repo, issue_number: a.number, body: a.comment });
        await github.rest.issues.update({ owner, repo, issue_number: a.number, state: 'closed', state_reason: 'completed' });
      }
    } else {
      core.info(`#${a.number} (${a.demo}) unchanged, nothing to do`);
    }
  }
  const count = (t) => actions.filter((a) => a.type === t).length;
  core.info(`Summary${dryRun ? ' (dry run, nothing written)' : ''}: create=${count('create')} update=${count('update')} close=${count('close')} unchanged=${count('noop')}`);
  return { actions };
}

module.exports = {
  main,
  patternToRegex,
  parseCodeowners,
  ownersFor,
  maintainersFrom,
  demoKey,
  groupAlerts,
  resolveOwners,
  planAssignees,
  renderBody,
  parseDemoMarker,
  parseAlertSet,
  planActions,
  issueTitle,
  LABEL,
  SHARED_KEY,
  DEFAULT_MAINTAINERS,
};
