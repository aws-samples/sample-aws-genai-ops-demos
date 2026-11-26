"""Instructions tab, filled from the run's own data.

build_tab(inv, data) returns the tab dict. Run facts that can't be derived from the buckets (scope, collection date,
unavailable checks, assumptions) come from inv['run']; see RUN_KEYS.
"""
import re
from collections import Counter

from s3dp.model import AI_OFF, size_label

RUN_KEYS = ('scope', 'collected', 'regions_scanned', 'regions_skipped', 'unavailable', 'api_errors', 'assumptions',
            'price_region')

TAB_HELP = {
    'instructions': 'This page: how the report was built and how far it can be trusted.',
    'resources': 'Every bucket, which of the three risks it is protected from, and the recommended change. The Evidence column of the Excel download lists the settings behind each status.',
    'operational-review': 'The headline: protection counts per risk, critical exposures, AWS Backup posture, scope per account.',
    'recommendations': 'One row per bucket and action, sorted by severity. Filter by account or severity.',
    'inconsistencies': 'Buckets of the same workload configured differently across regions or accounts; always aligned to the stronger setting.',
    'governance': 'Why the gaps exist and what would keep them from coming back: maturity level, example rules, findings.',
    'cost-optimization': 'Cost quick wins and the cost of protection, with their order of magnitude and basis.',
}
CUSTOMER_TAB_IDS = ('instructions', 'resources', 'operational-review', 'recommendations', 'inconsistencies', 'governance',
                    'cost-optimization')

GLOSSARY = [
    ['Versioning', 'Keeps every version of an object. Enabled = on; Suspended = was on, old versions kept, new writes unprotected; never enabled = off.'],
    ['Noncurrent version', 'An older version kept after an overwrite or delete. Billed like any object until a lifecycle rule expires it.'],
    ['Object Lock', 'Write-once protection: a locked version cannot be deleted or overwritten until its retention date. Needs versioning.'],
    ['Governance vs Compliance', 'Governance mode can be bypassed by users with a special permission; Compliance mode cannot be shortened by anyone, including the root user.'],
    ['Default vs per-object retention', 'A default rule locks every new object; per-object retention is set by the writing application (for example a backup tool).'],
    ['MFA Delete', 'Requires a second factor to permanently delete versions or change versioning.'],
    ['CRR / SRR', 'Cross-Region / Same-Region Replication: S3 copies new objects to another bucket. Only CRR protects against a regional disruption.'],
    ['Delete-marker replication', 'When on, a delete in the source also hides the object in the replica, so a replica alone is not a backup.'],
    ['AWS Backup plan / vault', 'A plan schedules backups of selected resources; recovery points are stored in a vault.'],
    ['Vault Lock', 'Prevents recovery points in a vault from being deleted before their retention (Governance or Compliance mode).'],
    ['Logically air-gapped vault', 'An AWS Backup vault that is locked by default and shareable to a separate account for isolated recovery.'],
    ['S3 opt-in (AWS Backup)', 'Per-region setting that lets AWS Backup protect S3. When off, backup plans silently skip buckets in that region.'],
    ['Block Public Access', 'Account- or bucket-level guardrail that overrides policies or ACLs granting public access.'],
    ['Storage classes', 'Standard, Standard-IA, Intelligent-Tiering, Glacier Instant Retrieval, Glacier, Deep Archive: lower storage price, higher retrieval cost or delay.'],
]

ROLE_TABLE = {'columns': ['Bucket role', 'Human error', 'Malicious activity', 'Regional disruption'], 'rows': [
    ['Backup target (backup tool, database dumps, archives)', 'Required', 'Required: it is the copy of last resort', 'Recommended'],
    ['Production data', 'Required', 'Recommended', 'Recommended (required if not re-creatable)'],
    ['Audit trail (CloudTrail, Config delivery)', 'Recommended', 'Required: tamper-evidence', 'Optional'],
    ['Access / service logs', 'Optional', 'Recommended', 'No'],
    ['Replica (replication destination)', 'Recommended', 'Recommended', 'It is the regional copy'],
    ['Non-production / tooling', 'Optional', 'Optional', 'No'],
]}


def _n(x):
    return f'{x:,}'


def _tb(tb):
    return size_label(tb) if tb else '0 GiB'


def _facts(inv, data):
    B = inv['buckets']
    gc = lambda b: b.get('group_count', 1)
    tabs = {t['id']: t for t in data['tabs']}
    ops = tabs.get('operational-review', {})
    exp = next((r['name'] for r in ops.get('resources', []) if r['name'].startswith('Critical exposures')), '')
    nexp = int(re.search(r'\((\d+)\)', exp).group(1)) if exp else 0
    rows = [c['row'] for c in tabs['resources']['cards']]
    unk = lambda k: sum(r[k] == 'unknown' for r in rows)
    return dict(
        rows=len(B), buckets=sum(gc(b) for b in B), tb=sum(b['size_tb'] or 0 for b in B),
        accounts=len(inv['accounts']),
        verified=sum(gc(b) for b in B if b.get('verified', True) and gc(b) == 1),
        grouped=sum(gc(b) for b in B if gc(b) > 1),
        assumed_single=sum(1 for b in B if gc(b) == 1 and not b.get('verified', True)),
        unsized=sum(1 for b in B if b['size_tb'] is None),
        grouping=any(gc(b) > 1 or not b.get('verified', True) for b in B),
        nv={'Versioning': sum(b['versioning'] == 'NotVerified' for b in B),
            'Object Lock': sum(b['object_lock'] == 'NotVerified' for b in B),
            'Replication': sum(b['replication'] == 'NotVerified' for b in B),
            'Encryption': sum(b.get('encryption') == 'NotVerified' for b in B),
            'Lifecycle': sum(b.get('lifecycle') is None for b in B),
            'AWS Backup': sum(b.get('aws_backup') == 'NotVerified' for b in B)},
        unknown={'human error': unk('he'), 'malicious activity': unk('ma'), 'regional disruption': unk('rd')},
        basis=Counter(b['vce']['basis'] for b in B if b.get('vce')),
        exposures=nexp, tabs=[t['id'] for t in data['tabs']], labels={t['id']: t['label'] for t in data['tabs']},
        recs=Counter(r['severity'] for r in tabs.get('recommendations', {}).get('items', [])),
        families=tabs.get('inconsistencies', {}).get('families_compared', 0))


def _about(inv, f, run):
    items = [f"Customer: {inv['customer']}",
             f"Scope: {run.get('scope') or str(f['accounts']) + ' accounts'}",
             f"Collected: {run.get('collected') or inv.get('scan_date', '')[:10]} (point-in-time snapshot: changes made after this date are not reflected)",
             f"Reviewed: {_n(f['buckets'])} buckets"
             + (f" ({_n(f['rows'])} rows after grouping identical sets)" if f['grouping'] else '')
             + f", about {_tb(f['tb'])}"]
    if run.get('regions_scanned'):
        items.append(f"Regions scanned: {', '.join(run['regions_scanned'])}")
    if run.get('regions_skipped'):
        items.append(f"Regions skipped: {run['regions_skipped']}")
    return dict(title='About this report', items=items)


def _howto(f):
    ids = [i for i in f['tabs'] if i in CUSTOMER_TAB_IDS]
    return dict(title='How to read this report',
                table={'columns': ['Tab', 'What it answers'], 'rows': [[f['labels'][i], TAB_HELP.get(i, '')] for i in ids]},
                items=['Three risks, three controls: human or application errors → Versioning · malicious activity → Object Lock · regional disruption → cross-region replication.',
                       'Coverage: protected · partial (for example versioning without Object Lock: recoverable, not immune) · unprotected (evidence shows the control is absent) · unconfirmed (the setting could not be read).',
                       'Row status: RISK GAP (a required protection is missing) · GAP — CONFIRM 3P (missing, but a backup tool may cover it) · PARTIAL · CONFIRM (required protection unreadable) · CONFIRM ROLE (the bucket role could not be determined) · PROTECTED · NON-PROD / LOG/TOOLING (not scored).',
                       'Priority focus: buckets to address first (risk + size + business criticality)',
                       'Example: a backup-tool bucket with versioning but no Object Lock is protected against human error and partial against malicious activity, status RISK GAP: for a backup target, protection against deliberate deletion is required, and versioned objects can still be permanently deleted.'])


def _retrieval(f, run):
    unavailable = run.get('unavailable', [])
    items = ['Only configuration metadata was read, with the read-only credentials used for the scan: bucket settings, AWS Backup settings, daily S3 storage metrics (size and object count), S3 spend per region, IAM role names, CloudFormation stack names, AWS Config and Security Hub status.',
             'No object was read, downloaded or listed. No setting was changed.']
    if unavailable:
        items.append('Settings that could not be read, shown as unconfirmed in the report: ' + '; '.join(u[0] for u in unavailable) + '.')
    if f['grouped']:
        items.append(f"Large sets of identically configured buckets ({_n(f['grouped'])} buckets) were checked on a sample; each grouped row says how many were checked.")
    if run.get('api_errors'):
        items.append(f"{_n(run['api_errors'])} API calls were denied or failed; those settings are shown as unconfirmed.")
    return dict(title='How the data was retrieved', items=items)


def _logic(f, ai):
    items = [
        'Bucket roles are assigned from tags, account names, bucket names and replication / backup relationships (first match wins). Each role has an expected protection level (table below).',
        'Buckets no rule can classify are classified by a generative AI assistant from their name, tags and size; the reason is shown in the bucket evidence. A low-confidence suggestion of a role that is not scored (non-production, tooling, logs) is not applied.'
        if ai.get('classification') else
        'Buckets no rule can classify keep the role "unknown" (CONFIRM ROLE) and are not scored until their owner confirms the role.',
        'Coverage comes from evidence only: an explicit "not configured" answer is unprotected; a setting that could not be read is unconfirmed, never unprotected. A stronger control that can\'t be verified never erases a verified weaker one (versioning with unreadable Object Lock stays partial).',
        'Versioning is a prerequisite for Object Lock, replication and AWS Backup of S3: a bucket that never had versioning provably has none of them.',
        f"Critical exposures ({f['exposures']} in this report): production buckets failing two or more of backup coverage, point-in-time protection and delete protection, plus every backup target without immutability.",
        'Priority score: +50 per missing required protection (+15 partial, +5 unconfirmed); size +30 (≥100 TiB), +20 (≥10 TiB), +12 (≥1 TiB), +5 (≥0.1 TiB); backup target or audit trail +15, production +8, non-production −30; business-critical naming (clinical, patient, customer, oracle, sql…) +10, non-production naming −15; a HIGH finding +10. Critical ≥80, High ≥60, Medium ≥35, else Low.',
        'Versioning cost estimate: churn% × size per storage class × class price × (30-day noncurrent retention / 30). Churn basis: measured (request metrics), bounded (daily object-count drops + 1 point for overwrites) or prior (static 0%, append 1%, unknown 10% with a 2–25% range, high churn 25%). Only valid with a NoncurrentVersionExpiration rule.',
        f"Inconsistencies: buckets whose names match once region tokens, account IDs and replica suffixes are removed form a family ({f['families']} compared); differing settings are recommended up to the stronger value, never down.",
    ]
    return dict(title='How it works', items=items, table=ROLE_TABLE)


def _assumptions(run):
    # Each assumption is [assumption, effect] or [assumption, effect, 'internal'] (internal ones are not shown).
    a = [x[:2] for x in run.get('assumptions', []) if not (len(x) > 2 and x[2] == 'internal')]
    return dict(title='Assumptions to confirm',
                table={'columns': ['Assumption', 'Effect on the report'], 'rows': [list(x) for x in a]} if a else None,
                items=[] if a else ['None beyond the defaults described above.'])


def _coverage(f):
    rows = [['Buckets individually verified', _n(f['verified'])]]
    if f['grouping']:                       # only when the inventory groups or assumes rows
        rows += [['Buckets in grouped sets (checked on a sample)', _n(f['grouped'])],
                 ['Single rows assumed from a pattern or bulk scan', _n(f['assumed_single'])]]
    rows.append(['Rows without a size', _n(f['unsized'])])
    rows += [[f'Rows with {k} not verified', _n(v)] for k, v in f['nv'].items()]
    rows += [[f'Rows with {k} coverage unconfirmed', _n(v)] for k, v in f['unknown'].items()]
    if f['basis']:
        rows.append(['Versioning cost basis', ', '.join(f'{k}: {v}' for k, v in f['basis'].items())])
    return dict(title='Coverage and confidence', table={'columns': ['Measure', 'Value'], 'rows': rows},
                note='The more unconfirmed and assumed rows, the more a number is an indication rather than a measurement.')


def _ai_limit(ai):
    if ai.get('narrative') in ('pending', 'complete', 'partial'):
        return 'Text written by a generative AI assistant: verify before acting.'
    if ai.get('classification'):
        return 'Bucket roles marked as inferred by the generative AI assistant: verify before acting.'
    return 'Rules-based report: no generative AI was used.'


def _limits(f, run):
    items = ['Configuration is not behaviour: the report shows what is enabled, not whether restores work or who can actually delete data.',
             'Backup tools outside AWS Backup are detected from names and roles only; which buckets they cover is not known.',
             'Without request metrics, write patterns and versioning costs are inferred from daily storage metrics; overwrites of the same key are invisible.',
             "Not everything could be checked: " + (f"{_n(f['grouped'])} buckets checked on a sample, " if f['grouping'] else '')
             + f"{_n(f['unsized'])} rows without a size, unconfirmed counts per check in the table above.",
             f"Costs are list-price estimates ({run.get('price_region', 'US East (N. Virginia) list prices')}), before discounts, credits and taxes, labelled as estimates.",
             'This is not an audit or a compliance attestation; regulatory retention and availability obligations need your own assessment.',
             _ai_limit(run.get('ai') or AI_OFF)]
    return dict(title='Limits', items=items)


def _next():
    items = ['Start with the critical exposures (Risk Review) and the priority focus buckets (Buckets).',
             'Confirm the unconfirmed items and the assumptions with the bucket owners before planning changes.',
             'Test configuration changes on a non-production bucket first, and add a NoncurrentVersionExpiration rule when enabling versioning.',
             'Some changes are irreversible: Object Lock Compliance-mode retention cannot be shortened by anyone; start with Governance mode.']
    return dict(title='Next steps', items=items)


def build_tab(inv, data):
    run = inv.get('run', {})
    f = _facts(inv, data)
    sections = [_about(inv, f, run), _howto(f), _retrieval(f, run), _logic(f, run.get('ai') or AI_OFF), _assumptions(run), _coverage(f),
                _limits(f, run), dict(title='Glossary', table={'columns': ['Term', 'Meaning'], 'rows': GLOSSARY}), _next()]
    clean = lambda secs: [{k: v for k, v in s.items() if v not in (None, [], '')} for s in secs]
    return dict(id='instructions', label='Instructions', type='instructions', heading='How to use this report',
                intro='Read this first: how the data was collected, how a bucket ends up red or green, and how far the conclusions can be trusted.',
                sections=clean(sections))
