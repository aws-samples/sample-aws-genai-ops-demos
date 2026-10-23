// Unit tests for security-findings-triage.js. Run: node --test .github/scripts/
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const t = require('./security-findings-triage.js');

const CODEOWNERS_241 = `
# Demo owners
/operations-automation/ai-lambda-runtime-migration/  @benlec @bquintas
/security/prowler-security-findings-agent/           @albertjh @bquintas @benlec
/resilience/ai-load-test-generation-with-dlt/        @OHsechun @hunmini @bquintas @benlec
/security/ai-iam-access-analyzer-assistant/          @jgq-amazon @bquintas @benlec
# Governance (keep LAST)
/.kiro/          @bquintas @benlec
/.github/        @bquintas @benlec
`;
const CODEOWNERS_MAIN = `
/.kiro/          @bquintas @benlec
/.github/        @bquintas @benlec
`;

function alert(number, manifest, severity = 'medium', pkg = 'vite') {
  return {
    number,
    html_url: `https://github.com/o/r/security/dependabot/${number}`,
    dependency: { manifest_path: manifest, package: { name: pkg } },
    security_vulnerability: {
      severity,
      vulnerable_version_range: '<= 6.4.2',
      first_patched_version: { identifier: '6.4.3' },
    },
  };
}

const NOT_ASSIGNABLE = new Set(['rahulnarya', 'ohsechun', 'hunmini', 'albertjh']);
const isAssignable = (u) => !NOT_ASSIGNABLE.has(u.toLowerCase());

test('CODEOWNERS: last matching line wins', () => {
  const rules = t.parseCodeowners(`
/security/            @pillar-owner
/security/demo-a/     @alice
*                     @everyone
/security/demo-b/     @bob
`);
  // "*" comes after /security/demo-a/, so it wins for demo-a.
  assert.deepEqual(t.ownersFor(rules, 'security/demo-a/package.json'), ['everyone']);
  // /security/demo-b/ is the last match for demo-b.
  assert.deepEqual(t.ownersFor(rules, 'security/demo-b/frontend/package.json'), ['bob']);
  // A later empty rule unsets ownership.
  const unset = t.parseCodeowners('/a/ @x\n/a/b/\n');
  assert.deepEqual(t.ownersFor(unset, 'a/b/c.txt'), []);
});

test('CODEOWNERS: directory patterns are anchored and do not prefix-match siblings', () => {
  const rules = t.parseCodeowners('/security/demo/ @alice\n# comment line\n');
  assert.deepEqual(t.ownersFor(rules, 'security/demo/frontend/package.json'), ['alice']);
  assert.equal(t.ownersFor(rules, 'security/demo-two/package.json'), null);
  assert.equal(t.ownersFor(rules, 'other/security/demo/package.json'), null);
  // Directory pattern does not match a file with the same name.
  assert.equal(t.ownersFor(rules, 'security/demo'), null);
  // Unanchored and glob patterns.
  const g = t.parseCodeowners('*.js @js\ndocs/** @docs\n');
  assert.deepEqual(t.ownersFor(g, 'a/b/c.js'), ['js']);
  assert.deepEqual(t.ownersFor(g, 'docs/x/y.md'), ['docs']);
  assert.equal(t.ownersFor(g, 'a/docs/y.md'), null);
});

test('CODEOWNERS: teams and e-mails are not treated as assignable users', () => {
  const rules = t.parseCodeowners('/x/ @org/team user@example.com @alice\n');
  assert.deepEqual(t.ownersFor(rules, 'x/y'), ['alice']);
});

test('maintainers come from the /.github/ line, with a default fallback', () => {
  assert.deepEqual(t.maintainersFrom(t.parseCodeowners('/.github/ @m1 @m2\n')), ['m1', 'm2']);
  assert.deepEqual(t.maintainersFrom(t.parseCodeowners('/x/ @a\n')), ['bquintas', 'benlec']);
  assert.deepEqual(t.maintainersFrom([]), ['bquintas', 'benlec']);
});

test('grouping: demo = first two segments; outside the pillars goes to shared', () => {
  assert.equal(t.demoKey('security/prowler-security-findings-agent/frontend/package.json'), 'security/prowler-security-findings-agent');
  assert.equal(t.demoKey('shared/utils/requirements.txt'), 'shared');
  assert.equal(t.demoKey('package.json'), 'shared');
  assert.equal(t.demoKey('security/package.json'), 'shared');
  const groups = t.groupAlerts([
    alert(6, 'operations-automation/ai-lambda-runtime-migration/frontend/package.json'),
    alert(16, 'operations-automation/ai-lambda-runtime-migration/frontend/package.json', 'high'),
    alert(13, 'security/prowler-security-findings-agent/frontend/package.json'),
    alert(30, 'shared/utils/requirements.txt', 'low', 'boto3'),
  ]);
  assert.deepEqual([...groups.keys()], [
    'operations-automation/ai-lambda-runtime-migration',
    'security/prowler-security-findings-agent',
    'shared',
  ]);
  // Sorted by severity, then number.
  assert.deepEqual(groups.get('operations-automation/ai-lambda-runtime-migration').map((a) => a.number), [16, 6]);
});

test('owners: author vs maintainers, unmatched and shared fall back to maintainers', () => {
  const rules = t.parseCodeowners(CODEOWNERS_241);
  const m = t.maintainersFrom(rules);
  const prowler = t.resolveOwners('security/prowler-security-findings-agent',
    ['security/prowler-security-findings-agent/frontend/package.json'], rules, m);
  assert.deepEqual(prowler.authors, ['albertjh']);
  const lambda = t.resolveOwners('operations-automation/ai-lambda-runtime-migration',
    ['operations-automation/ai-lambda-runtime-migration/frontend/package.json'], rules, m);
  assert.deepEqual(lambda.authors, []);
  assert.deepEqual(lambda.owners, ['benlec', 'bquintas']);
  const unmatched = t.resolveOwners('observability/new-demo', ['observability/new-demo/package.json'], rules, m);
  assert.deepEqual(unmatched, { owners: ['bquintas', 'benlec'], authors: [] });
  const shared = t.resolveOwners('shared', ['shared/x/package.json'], rules, m);
  assert.deepEqual(shared, { owners: ['bquintas', 'benlec'], authors: [] });
  // Pre-#241 CODEOWNERS: nothing matches a demo, maintainers own it.
  const pre = t.parseCodeowners(CODEOWNERS_MAIN);
  const preProwler = t.resolveOwners('security/prowler-security-findings-agent',
    ['security/prowler-security-findings-agent/frontend/package.json'], pre, t.maintainersFrom(pre));
  assert.deepEqual(preProwler, { owners: ['bquintas', 'benlec'], authors: [] });
});

test('assignees: assignable author is assigned, maintainers excluded', () => {
  const p = t.planAssignees({ owners: ['jgq-amazon', 'bquintas', 'benlec'], authors: ['jgq-amazon'], maintainers: ['bquintas', 'benlec'] }, isAssignable);
  assert.deepEqual(p, { assignees: ['jgq-amazon'], mentions: [] });
});

test('assignees: unassignable author -> maintainers assigned and author mentioned', () => {
  const p = t.planAssignees({ owners: ['albertjh', 'bquintas', 'benlec'], authors: ['albertjh'], maintainers: ['bquintas', 'benlec'] }, isAssignable);
  assert.deepEqual(p, { assignees: ['bquintas', 'benlec'], mentions: ['albertjh'] });
  const two = t.planAssignees({ owners: ['OHsechun', 'hunmini', 'bquintas', 'benlec'], authors: ['OHsechun', 'hunmini'], maintainers: ['bquintas', 'benlec'] }, isAssignable);
  assert.deepEqual(two, { assignees: ['bquintas', 'benlec'], mentions: ['OHsechun', 'hunmini'] });
});

test('assignees: no author -> owners (maintainers) assigned, nobody mentioned', () => {
  const p = t.planAssignees({ owners: ['benlec', 'bquintas'], authors: [], maintainers: ['bquintas', 'benlec'] }, isAssignable);
  assert.deepEqual(p, { assignees: ['benlec', 'bquintas'], mentions: [] });
});

test('body: marker, table, checklist, auto-close line, mention', () => {
  const alerts = t.groupAlerts([
    alert(13, 'security/prowler-security-findings-agent/frontend/package.json'),
    alert(18, 'security/prowler-security-findings-agent/frontend/package.json', 'high'),
  ]).get('security/prowler-security-findings-agent');
  const body = t.renderBody({ demo: 'security/prowler-security-findings-agent', alerts, mentions: ['albertjh'], maintainers: ['bquintas', 'benlec'] });
  assert.equal(t.parseDemoMarker(body), 'security/prowler-security-findings-agent');
  assert.deepEqual(t.parseAlertSet(body), [13, 18]);
  assert.match(body, /Open Dependabot alerts for `security\/prowler-security-findings-agent`\. Owner: @albertjh/);
  assert.match(body, /\| \[#18\]\(https:\/\/github\.com\/o\/r\/security\/dependabot\/18\) \| high \| vite \| `security\/prowler-security-findings-agent\/frontend\/package\.json` \| `<= 6\.4\.2` \| `6\.4\.3` \|/);
  assert.ok(body.indexOf('[#18]') < body.indexOf('[#13]'), 'high severity first');
  assert.match(body, /- \[ \] Real: open a PR that bumps the package\./);
  assert.match(body, /not_used.*tolerable_risk.*inaccurate.*assign @bquintas or @benlec, who dismiss the alert\./);
  assert.match(body, /Please don't close it by hand/);
  assert.match(body.trim(), /closes automatically when all alerts for this demo are fixed or dismissed\.$/);
  const noMention = t.renderBody({ demo: 'shared', alerts, mentions: [], maintainers: ['bquintas', 'benlec'] });
  assert.doesNotMatch(noMention, /Owner:/);
});

test('body: table cells escape pipes and missing patched version', () => {
  const a = alert(1, 'security/d/package.json');
  a.security_vulnerability.vulnerable_version_range = '< 1.0 | >= 2.0';
  a.security_vulnerability.first_patched_version = null;
  const body = t.renderBody({ demo: 'security/d', alerts: [a], mentions: [], maintainers: ['m'] });
  assert.match(body, /`< 1\.0 \\\| >= 2\.0` \| none \|/);
});

test('body: backslashes are escaped before pipes', () => {
  const a = alert(1, 'security/d/package.json');
  a.security_vulnerability.vulnerable_version_range = 'a\\|b';
  const body = t.renderBody({ demo: 'security/d', alerts: [a], mentions: [], maintainers: ['m'] });
  // "a\|b" must render as "a\\\|b": escaped backslash, then escaped pipe.
  assert.ok(body.includes('`a\\\\\\|b`'), body);
});

function desiredFor(demo, alerts, assignees = ['bquintas', 'benlec']) {
  const body = t.renderBody({ demo, alerts, mentions: [], maintainers: ['bquintas', 'benlec'] });
  return [demo, { title: t.issueTitle(demo), body, alertNumbers: alerts.map((x) => x.number).sort((x, y) => x - y), assignees, mentions: [] }];
}

test('plan: create when no issue, close when no alerts left', () => {
  const d = new Map([desiredFor('security/a', [alert(1, 'security/a/p.json')])]);
  const stale = { number: 50, body: '<!-- security-triage:security/gone -->\n<!-- security-triage-alerts:9 -->', assignees: [] };
  const actions = t.planActions(d, [stale]);
  assert.deepEqual(actions.map((a) => [a.type, a.demo]), [['create', 'security/a'], ['close', 'security/gone']]);
  assert.match(actions[1].comment, /fixed or dismissed/);
  // No alerts at all: every tracked issue closes, nothing is created.
  assert.deepEqual(t.planActions(new Map(), [stale]).map((a) => a.type), ['close']);
  // Issues without the marker are ignored.
  assert.deepEqual(t.planActions(new Map(), [{ number: 7, body: 'hand-written', assignees: [] }]), []);
});

test('plan: unchanged rerun is a noop (even with ticked checkboxes), changed set edits and comments', () => {
  const alerts = [alert(1, 'security/a/p.json'), alert(2, 'security/a/p.json')];
  const [demo, want] = desiredFor('security/a', alerts);
  const ticked = want.body.replace('- [ ] Real', '- [x] Real');
  const same = t.planActions(new Map([[demo, want]]), [{ number: 10, body: ticked, assignees: ['bquintas', 'benlec'] }]);
  assert.deepEqual(same.map((a) => a.type), ['noop']);

  const oldBody = t.renderBody({ demo, alerts: [alerts[0], alert(3, 'security/a/p.json')], mentions: [], maintainers: ['bquintas', 'benlec'] });
  const changed = t.planActions(new Map([[demo, want]]), [{ number: 10, body: oldBody, assignees: ['bquintas', 'benlec'] }]);
  assert.equal(changed[0].type, 'update');
  assert.equal(changed[0].body, want.body);
  assert.equal(changed[0].comment, 'Dependabot alerts changed (new: 2; resolved: 3). Table updated.');
  // Plain "#N" would be autolinked to issue/PR N by GitHub.
  assert.doesNotMatch(changed[0].comment, /#\d/);
  assert.deepEqual(changed[0].addAssignees, []);
});

test('plan: same alert set but new owner assignable -> add assignee, no comment', () => {
  const [demo, want] = desiredFor('security/a', [alert(1, 'security/a/p.json')], ['alice']);
  const actions = t.planActions(new Map([[demo, want]]), [{ number: 10, body: want.body, assignees: ['bquintas'] }]);
  assert.equal(actions[0].type, 'update');
  assert.equal(actions[0].body, null);
  assert.equal(actions[0].comment, null);
  assert.deepEqual(actions[0].addAssignees, ['alice']);
});

// ---------------------------------------------------------------------------
// main() with a fake client: dry run writes nothing; real run calls the write APIs.
// ---------------------------------------------------------------------------

function fakeGithub({ alerts, issues = [], labelExists = true, notAssignable = NOT_ASSIGNABLE, alertsStatus }) {
  const writes = [];
  const err = (status) => Object.assign(new Error(`HTTP ${status}`), { status });
  const write = (name) => async (params) => { writes.push([name, params]); return { data: {} }; };
  const rest = {
    dependabot: { listAlertsForRepo: async () => { if (alertsStatus) throw err(alertsStatus); return { data: alerts }; } },
    repos: { getContent: async () => { throw err(404); } },
    issues: {
      listForRepo: async () => ({ data: issues }),
      checkUserCanBeAssigned: async ({ assignee }) => { if (notAssignable.has(assignee.toLowerCase())) throw err(404); return { status: 204 }; },
      getLabel: async () => { if (!labelExists) throw err(404); return { data: {} }; },
      createLabel: write('createLabel'),
      create: write('create'),
      update: write('update'),
      addAssignees: write('addAssignees'),
      createComment: write('createComment'),
    },
  };
  return { writes, github: { rest, paginate: async (fn, params) => (await fn(params)).data } };
}

const ctx = { repo: { owner: 'o', repo: 'r' }, payload: { repository: { default_branch: 'main' } } };
function fakeCore() {
  const lines = [];
  return { lines, info: (m) => lines.push(m), warning: (m) => lines.push(`WARN ${m}`), setFailed: (m) => lines.push(`FAILED ${m}`) };
}
const LIVE = [
  alert(6, 'operations-automation/ai-lambda-runtime-migration/frontend/package.json'),
  alert(16, 'operations-automation/ai-lambda-runtime-migration/frontend/package.json', 'high'),
  alert(17, 'operations-automation/ai-lambda-runtime-migration/frontend/package.json'),
  alert(13, 'security/prowler-security-findings-agent/frontend/package.json'),
  alert(18, 'security/prowler-security-findings-agent/frontend/package.json', 'high'),
  alert(19, 'security/prowler-security-findings-agent/frontend/package.json'),
];

test('main: dry run plans 2 issues and writes nothing', async () => {
  const { github, writes } = fakeGithub({ alerts: LIVE, labelExists: false });
  const core = fakeCore();
  const { actions } = await t.main({ github, context: ctx, core, dryRun: true, codeownersText: CODEOWNERS_241 });
  assert.deepEqual(writes, []);
  assert.deepEqual(actions.map((a) => [a.type, a.demo, a.assignees, a.mentions]), [
    ['create', 'operations-automation/ai-lambda-runtime-migration', ['benlec', 'bquintas'], []],
    ['create', 'security/prowler-security-findings-agent', ['bquintas', 'benlec'], ['albertjh']],
  ]);
  assert.ok(core.lines.some((l) => l.startsWith('[dry-run] would create label')));
});

test('main: real run creates label and issues; pre-#241 CODEOWNERS falls back to maintainers', async () => {
  const { github, writes } = fakeGithub({ alerts: LIVE, labelExists: false });
  const { actions } = await t.main({ github, context: ctx, core: fakeCore(), codeownersText: CODEOWNERS_MAIN });
  assert.deepEqual(writes.map(([n]) => n), ['createLabel', 'create', 'create']);
  assert.deepEqual(actions.map((a) => [a.assignees, a.mentions]), [[['bquintas', 'benlec'], []], [['bquintas', 'benlec'], []]]);
  assert.deepEqual(writes[1][1].labels, ['security-finding']);
});

test('main: unreadable alerts fails with one clear line and writes nothing', async () => {
  const { github, writes } = fakeGithub({ alerts: [], alertsStatus: 403 });
  const core = fakeCore();
  await t.main({ github, context: ctx, core, codeownersText: '' });
  assert.deepEqual(writes, []);
  assert.equal(core.lines.filter((l) => l.startsWith('FAILED')).length, 1);
  assert.match(core.lines.find((l) => l.startsWith('FAILED')), /vulnerability-alerts: read/);
});
