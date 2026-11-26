"""Governance maturity assessment.

Scores six governance dimensions from collected signals, measures example protection rules
against the actual estate ("if these were your rules"), and derives governance findings.
"""
import re

LEVELS = [
    (1, 'Ad hoc', 'Protection is decided bucket by bucket by whoever creates it. No written standard; results depend on the team and the day.'),
    (2, 'Repeatable', 'Some teams reuse templates and protect well, but the rules are informal. Nothing verifies them, so drift goes unnoticed.'),
    (3, 'Defined', 'A written data protection standard maps each data class (e.g. legal, clinical, production) to required controls. IaC modules apply it by default and buckets carry classification tags.'),
    (4, 'Managed', 'Guardrails prevent weakening protection (SCPs, organization backup policies), detective controls cover every account, exceptions are recorded and time-boxed, and restores are tested.'),
    (5, 'Optimized', 'Compliance with the standard is measured continuously and reported to leadership, drift is remediated automatically, and recovery is exercised regularly from an isolated backup account.'),
]

DIMENSIONS = {
    'ownership': ('Ownership & data classification',
                  ['No owner or classification on buckets; protection needs are tribal knowledge.',
                   'Some buckets have an owner tag; classification is implied by names.',
                   'Owner and data-classification tags on most buckets; a tag policy defines the allowed values.',
                   'Tags are required at creation (tag policy enforced, IaC) and drive protection automatically.',
                   'Ownership and classification are complete, audited, and used in posture reporting.']),
    'standard': ('Protection standard & consistency',
                 ['Under half of production buckets meet a basic baseline; settings vary by team.',
                  'Most production buckets meet a baseline, but same-workload buckets drift between regions.',
                  'A written baseline per data class; production buckets meet it and twins match.',
                  'Exceptions to the baseline are documented, approved and time-boxed.',
                  'Baseline compliance is a tracked KPI with a target and trend.']),
    'preventive': ('Preventive guardrails',
                   ['Nothing prevents turning protection off or deleting backups.',
                    'One guardrail in place (e.g. account-level Block Public Access everywhere).',
                    'SCPs deny suspending versioning / deleting backup vaults; organization backup policies exist.',
                    'Guardrails cover every account, including backup immutability (Vault Lock, Object Lock defaults).',
                    'Guardrails are tested and reviewed; break-glass access is monitored.']),
    'detective': ('Detective controls',
                  ['No continuous checks of S3 data protection settings.',
                   'AWS Config or Security Hub in some accounts, without data protection rules.',
                   'Data protection rules (versioning, replication, Object Lock, backup coverage) in every account.',
                   'Findings route to owners with automatic remediation for the baseline.',
                   'Posture is reported to leadership with trends (e.g. AWS Backup Audit Manager reports).']),
    'iac': ('Standardization (infrastructure as code)',
            ['Buckets are mostly created by hand in the console.',
             'Some workloads use IaC; others are console-created and drift from them.',
             'Most buckets are IaC-managed from shared modules that embed the protection baseline.',
             'All production buckets come from approved modules; drift is detected.',
             'Policy-as-code checks protection settings before deployment (e.g. proactive controls, cfn-guard).']),
    'recovery': ('Backup & recovery governance',
                 ['No isolated backup layer; recovery depends on the production account.',
                  'A central backup account or vendor exists, but backups are mutable or untested.',
                  'Backups are immutable (Vault Lock / Object Lock) and copied cross-account or cross-region.',
                  'Restore testing runs on a schedule against documented RPO/RTO per data class.',
                  'Recovery is exercised in game days; results feed the standard.']),
}

def _pct(n, d):
    return round(100 * n / d) if d else 0


def _scaled(checks, top):
    """1 + passed checks on a 1..top scale, scaled to the known checks (None = unknown, not scored).
    None (not assessed) when fewer than half the checks are known."""
    known = [c for c in checks if c is not None]
    if 2 * len(known) < len(checks):         # fewer than half the signals known: extrapolating would mislead
        return None
    return min(top, 1 + int(sum(known) * len(checks) / len(known) + 0.5))


def _drift_origin(drifted, outside, unknown=()):
    """Where the drift comes from: only buckets not IaC-managed are said to be created outside IaC. unknown: drifted
    buckets whose tags (hence IaC management) could not be read."""
    inside = [b for b in drifted if b not in outside and b not in unknown]
    tail = f" IaC management not verified (tags not readable): {', '.join(unknown)}." if unknown else ''
    if not outside:
        if not inside:
            return tail.strip()
        return f"all drifted buckets ({', '.join(inside)}) are IaC-managed: the drift is between IaC templates." + tail
    text = f"drifted buckets ({', '.join(outside)}) were created outside IaC"
    return text + (f"; IaC-managed but drifted (templates differ): {', '.join(inside)}." if inside else '.') + tail


def assess(inv, cards, families, inconsistencies):
    g = inv['governance']
    accts = inv['accounts']
    rows = {c['row']['bucket']: c['row'] for c in cards}
    scoped = [b for b in inv['buckets'] if b['role'] not in ('non_production', 'tooling', 'access_logs')]
    prod = [b for b in inv['buckets'] if b['role'] == 'production']
    # Same predicate as the bucket assessment (regulated name or classification tag), so the two never disagree.
    regulated = [b for b in prod if b.get('regional_required')
                 or any(re.search(rf'(^|[-_.]){w}', b['name']) for w in g['regulated_naming'])]
    drifted = sorted({w for r in inconsistencies if r['severity'] in ('HIGH', 'MEDIUM') for w in r['weaker']})
    drift_fams = {r['family'] for r in inconsistencies if r['severity'] in ('HIGH', 'MEDIUM')}

    # ---- "If these were your rules": example policies measured against the estate
    def rule(name, scope, ok, *rest):
        """rule(name, scope, failing, why) or rule(name, scope, failing, unconfirmed, why)."""
        unknown, why = rest if len(rest) == 2 else ((), rest[0])
        """ok: failing members. unknown: members whose compliance could not be read, excluded from the counts."""
        n = len(scope) - len(unknown)
        # Nothing measurable (empty scope, or every member unconfirmed): no percentage, never a 0% failure.
        out = dict(rule=name, scope=scope, compliant=n - len(ok), total=n,
                   pct=_pct(n - len(ok), n) if n else None, failing=ok, why=why)
        if unknown:
            out['unconfirmed'] = list(unknown)
        if not n:
            out['note'] = 'Not verified: settings not readable' if unknown else 'Not applicable: nothing in scope'
        return out
    # A coverage that could not be read ('unknown') is unconfirmed for the rule: neither compliant nor failing.
    cov = lambda b, k: rows[b['name']][k]
    def split(scope, ok):
        """(failing, unconfirmed) for a coverage test ok(b) -> True | False | None (unknown)."""
        res = {b['name']: ok(b) for b in scope}
        return [n for n, v in res.items() if v is False], [n for n, v in res.items() if v is None]
    tri = lambda state, good: None if state == 'unknown' else state in good
    immutable = lambda b: tri(cov(b, 'ma'), ('protected', 'protected_governance'))

    def second_copy(b):
        backup, rd = b.get('aws_backup', 'None'), cov(b, 'rd')
        if rd == 'protected' or backup not in ('None', 'NotVerified'):
            return True
        return False if backup == 'None' and rd == 'unprotected' else None
    targets_ = [b for b in inv['buckets'] if b['role'] == 'backup_target']
    audits = [b for b in inv['buckets'] if b['role'] == 'audit_trail']
    tags_known = lambda b: b.get('tags_read') is not False
    tagged = lambda b: None if not tags_known(b) else (
        b['name'] in g['tags']['owner'] and b['name'] in g['tags']['data-classification'])
    bpa = lambda a: None if accts[a]['account_bpa'] not in ('on', 'off') else accts[a]['account_bpa'] == 'on'
    acct_label = lambda a: f"{a} ({accts[a]['name']})"
    policies = [
        rule('All production buckets have versioning enabled', prod,
             *split(prod, lambda b: tri(cov(b, 'he'), ('protected',))),
             'Protects against accidental or scripted deletion and overwrite.'),
        rule('Legal and regulated data is replicated across two regions', regulated,
             *split(regulated, lambda b: tri(cov(b, 'rd'), ('protected',))),
             f"Identified by name (legal, clinical, payment, invoice…) or classification tag: {len(g['tags']['data-classification'])} buckets carry a classification tag."),
        rule('Backup targets are immutable (Object Lock)', targets_, *split(targets_, immutable),
             'The copy of last resort must survive compromised credentials.'),
        rule('Audit trails are tamper-evident (Object Lock)', audits, *split(audits, immutable),
             'An attacker should not be able to erase the record of what they did.'),
        rule('Production data has a second copy (AWS Backup or cross-region replication)', prod,
             *split(prod, second_copy), 'Versioning alone lives in the same bucket, account and region.'),
        rule('Versioned buckets expire noncurrent versions', [b for b in scoped if b['versioning'] in ('Enabled', 'Suspended')],
             [b['name'] for b in scoped if b['versioning'] in ('Enabled', 'Suspended') and b.get('noncurrent_expiration') is False],
             'Keeps the cost of versioning bounded.'),
        rule('Buckets of the same workload share one configuration in every region',
             [f['family'] for f in families], sorted(drift_fams),
             'Divergence between twins is drift, not design.'),
        rule('Every production bucket has an owner and a data-classification tag', prod, *split(prod, tagged),
             'Rules per data class can only be automated if the class is on the bucket.'),
        rule('Account-level Block Public Access is on in every account', list(accts),
             [acct_label(a) for a in accts if bpa(a) is False], [acct_label(a) for a in accts if bpa(a) is None],
             'The guardrail that catches a misconfigured bucket policy.'),
    ]
    for p in policies:
        p['scope'] = len(p['scope']) if isinstance(p['scope'], list) else p['scope']

    # ---- Dimension levels from evidence
    # Buckets whose tags could not be read are left out of the tag-based percentages (owner, classification, IaC).
    tag_scope = [b for b in scoped if tags_known(b)]
    owner_pct = _pct(sum(b['name'] in g['tags']['owner'] for b in tag_scope), len(tag_scope))
    class_pct = _pct(sum(b['name'] in g['tags']['data-classification'] for b in tag_scope), len(tag_scope))
    tag_cov = min(owner_pct, class_pct)
    # AWS Organizations data (SCPs, backup and tag policies) is not collected in v1: None = not read, never "none".
    org = g.get('organizations')
    scps = None if org is None else org.get('scps', [])
    org_backup_policies = None if org is None else org.get('backup_policies', 0)
    tag_policies = None if org is None else org.get('tag_policies', [])
    tag_enforced = None if tag_policies is None else any(
        t['enforced'] and 'data-classification' in t['keys'] for t in tag_policies)
    # No in-scope bucket with readable tags: ownership is not assessed (None), never level 1.
    lvl_owner = None if not tag_scope else (
        1 if tag_cov < 25 and owner_pct < 25 else 2 if tag_cov < 50 else 3 if tag_cov < 80 or tag_enforced is not True else 4)

    ver_pct = policies[0]['pct']            # None: no production bucket with a readable versioning status
    lvl_std = None if ver_pct is None else 1 if ver_pct < 50 else 2 if ver_pct < 80 or drift_fams else 3

    bpa_known = [bpa(a) for a in accts if bpa(a) is not None]
    guards = [False if False in bpa_known else None if len(bpa_known) < len(accts) else True,
              None if scps is None else any('versioning' in s['protects'] for s in scps),
              None if scps is None else any('backup' in s['protects'] for s in scps),
              None if org_backup_policies is None else org_backup_policies > 0]
    known_guards = [x for x in guards if x is not None]       # an unreadable guard is not scored
    if 2 * len(known_guards) >= len(guards):  # fewer than half the guards known: not assessed
        n_guards = int(sum(known_guards) * len(guards) / len(known_guards) + 0.5)
        lvl_prev = 1 + n_guards if n_guards < 2 else 3 if n_guards < 4 else 4
    else:
        lvl_prev = None

    cfg = g['config']
    dp_of = lambda rules: [r for r in rules or [] if any(k in r for k in ('versioning', 'replication', 'object-lock', 'backup'))]
    dp_rules = dp_of(cfg['s3_rules'])           # estate-wide list, for the evidence text only
    # Accounts whose AWS Config (or Security Hub) status could not be read are unknown: excluded, never a gap.
    rec_unknown, rules_unknown = cfg.get('recording_unknown', []), cfg.get('s3_rules_unknown', [])
    sh_unknown = g['security_hub'].get('unknown_accounts', [])
    rec_known = set(accts) - set(rec_unknown)
    all_accts = bool(rec_known) and set(cfg['recording_accounts']) >= rec_known
    rec_gap = bool(rec_known - set(cfg['recording_accounts']))      # an account is known not to record
    # Data protection rules are judged per account: one account's rule covers no other account.
    by_acct = cfg.get('s3_rules_by_account')
    if by_acct is None:     # inventory without per-account rules: the estate list stands for every readable account
        by_acct = {a: None if a in rules_unknown else cfg['s3_rules'] for a in accts}
    rules_read = [a for a in accts if by_acct.get(a) is not None]
    no_dp = [a for a in rules_read if not dp_of(by_acct[a])]       # read, and no data protection rule
    dp_every = bool(rules_read) and not no_dp
    rule_gap = bool(no_dp)
    missing_dp = (f"{len(no_dp)} of {len(rules_read)} accounts ({', '.join(acct_label(a) for a in no_dp[:5])}"
                  f"{', ...' if len(no_dp) > 5 else ''})")
    # Above level 3 each rung is scored only when known: automatic remediation (not collectable in v1), then posture
    # reporting (AWS Backup Audit Manager frameworks). An unknown rung neither adds a level nor stops the climb.
    lvl_det = (1 if not cfg['recording_accounts'] and not g['security_hub']['enabled_accounts']
               else 2 if not (all_accts and dp_every) else 3)
    if lvl_det == 3:
        frameworks = g.get('backup_audit_manager_frameworks')
        for rung in (cfg.get('auto_remediation'), None if frameworks is None else frameworks > 0):
            if rung is False:
                break
            lvl_det += rung is True

    iac_pct = _pct(sum(b['name'] in g['iac_managed'] for b in tag_scope), len(tag_scope))
    untagged = {b['name'] for b in inv['buckets'] if not tags_known(b)}
    drift_outside_iac = [b for b in drifted if b not in g['iac_managed'] and b not in untagged]
    lvl_iac = None if not tag_scope else (
        1 if iac_pct < 30 else 2 if iac_pct < 60 or drift_outside_iac else 3 if iac_pct < 90 else 4)

    posture = inv['backup_posture']
    vaults = posture.get('vaults')
    copy_plans = posture.get('plans_with_copy_actions')
    restore = g.get('restore_tests_last_12_months')
    targets = targets_
    # None = not collectable / not readable: the check is not scored (it neither raises nor lowers the level).
    rec_checks = [None if g.get('central_backup_account') is None else bool(g['central_backup_account']),
                  None if vaults is None else any(v['locked'] for v in vaults),
                  None if copy_plans is None else copy_plans > 0,
                  None if policies[2]['pct'] is None else policies[2]['pct'] >= 80,   # no backup target: n/a
                  None if restore is None else restore > 0]
    # Same 1-5 scale as when every check is known (1 + passed, capped at 5), scaled to the known checks.
    lvl_rec = _scaled(rec_checks, 5)
    locked = None if vaults is None else sum(bool(v['locked']) for v in vaults)
    vault_s = 'Vault Lock: not readable' if vaults is None else f"Vault Lock on {locked} of {len(vaults)} vaults"
    copy_s = 'plans with copy rules: not readable' if copy_plans is None else f"{copy_plans} plans with copy rules"

    no_tags = ('No in-scope buckets' if not scoped else
               'Tags not verified: no in-scope bucket with readable tags')
    tag_policy_s = ('Tag policies: not readable from this account (AWS Organizations data is not collected)'
                    if tag_enforced is None else 'Data-classification tag enforced by a tag policy' if tag_enforced
                    else 'No tag policy enforces a data-classification tag')
    evidence = {
        'ownership': ([f'{owner_pct}% of in-scope buckets have an owner tag'
                       + (f' (tags not readable on {len(scoped) - len(tag_scope)}, excluded)' if len(tag_scope) < len(scoped) else ''),
                       f'{class_pct}% have a data-classification tag'] if tag_scope else [no_tags]) + [tag_policy_s],
        'standard': [('No production buckets in scope' if not prod else
                      'Versioning not verified on any production bucket' if ver_pct is None else
                      f"{policies[0]['compliant']} of {policies[0]['total']} production buckets versioned ({ver_pct}%)"),
                     f"{len(drift_fams)} of {len(families)} bucket families drifted between regions",
                     'Exceptions register: not collectable'],
        'preventive': [f"Account-level BPA on in {sum(m['account_bpa'] == 'on' for m in accts.values())} of {len(accts)} accounts"
                       + (f" (not readable in {len(accts) - len(bpa_known)})" if len(bpa_known) < len(accts) else ''),
                       'SCPs: not readable from this account (AWS Organizations data is not collected)' if scps is None
                       else f"{len(scps)} SCPs, {sum(bool(s['protects']) for s in scps)} protecting versioning, Object Lock or backups",
                       'Organization backup policies: not readable from this account' if org_backup_policies is None
                       else f"{org_backup_policies} organization backup policies"],
        'detective': [f"AWS Config recording in {len(cfg['recording_accounts'])} of {len(accts)} accounts"
                      + (f" (not readable in {len(rec_unknown)})" if rec_unknown else ''),
                      ('S3 rules: not readable' if len(rules_unknown) == len(accts) else
                       f"S3 rules: {', '.join(cfg['s3_rules']) or 'none'}"
                       + ('' if dp_rules else ' (no versioning, replication or backup rules)')
                       + (f"; no data protection rule in {missing_dp}" if dp_rules and no_dp else '')
                       + (f"; not readable in {len(rules_unknown)} accounts" if rules_unknown else '')),
                      'Security Hub enabled' if g['security_hub']['enabled_accounts'] else
                      'Security Hub: not readable' if sh_unknown else 'Security Hub not enabled',
                      f"AWS Backup Audit Manager frameworks: {'not readable' if g.get('backup_audit_manager_frameworks') is None else g['backup_audit_manager_frameworks']}",
                      'Automatic remediation: not collectable' if cfg.get('auto_remediation') is None
                      else f"Automatic remediation: {'yes' if cfg['auto_remediation'] else 'no'}"],
        'iac': [f'{iac_pct}% of in-scope buckets are IaC-managed (CloudFormation/Terraform tags)' if tag_scope
                else no_tags,
                f"Drifted buckets created outside IaC: {', '.join(drift_outside_iac) or 'none'}"],
        'recovery': ["Central backup account: not collectable from configuration" if g.get('central_backup_account') is None
                     else f"Central backup account: {g['central_backup_account'] or 'none identified'}",
                     f"{vault_s}; {copy_s}",
                     ('No backup targets in scope' if not targets else
                      f"{policies[2]['compliant']} of {len(targets)} backup targets immutable"
                      + (f" ({len(policies[2]['unconfirmed'])} not verified)" if policies[2].get('unconfirmed') else '')),
                     f"Restore testing plans: {'not readable' if restore is None else restore}; RPO/RTO per data class: not collectable"]
                    + ([f"Production buckets with a second copy not verified: {', '.join(policies[4]['unconfirmed'])}"]
                       if policies[4].get('unconfirmed') else []),
    }
    level = {'ownership': lvl_owner, 'standard': lvl_std, 'preventive': lvl_prev, 'detective': lvl_det,
             'iac': lvl_iac, 'recovery': lvl_rec}
    # level None = not assessed (nothing in scope or nothing readable): left out of the overall level.
    dims = [dict(id=k, name=DIMENSIONS[k][0], level=level[k], evidence=evidence[k],
                 current=(DIMENSIONS[k][1][level[k] - 1] if level[k]
                          else 'Not assessed: nothing in scope or nothing readable for this dimension.'),
                 next=(DIMENSIONS[k][1][level[k]] if level[k] and level[k] < 5 else 'Top level reached.' if level[k]
                       else ''))
            for k in DIMENSIONS]
    assessed = {k: v for k, v in level.items() if v is not None}
    overall = round(sum(assessed.values()) / len(assessed), 1) if assessed else 1.0
    ov = LEVELS[int(overall + 0.5) - 1 if overall >= 1 else 0]

    # ---- Governance findings
    findings = []
    def add(sev, title, ev, action, dim):
        findings.append(dict(severity=sev, title=title, evidence=ev, improvement=action,
                             dimension=DIMENSIONS[dim][0]))
    if drifted:
        add('HIGH', 'Protection depends on who created the bucket',
            f"{len(drift_fams)} workloads differ between regions; " + _drift_origin(drifted, drift_outside_iac, [b for b in drifted if b in untagged]),
            'Publish a shared S3 module (CloudFormation/Terraform/CDK) that embeds the baseline per data class, and require it for production buckets.',
            'iac')
    suspended = [b['name'] for b in prod if b['versioning'] == 'Suspended']
    if guards[1] is False:                  # None = SCPs not read: no finding
        add('HIGH', 'Nothing stops a protection from being turned off',
            (f"Versioning is Suspended on {', '.join(suspended)}; " if suspended else '') +
            'no SCP denies suspending versioning or deleting backups.',
            'Add SCPs that deny s3:PutBucketVersioning (Suspended), s3:PutBucketObjectLockConfiguration changes and backup vault deletion outside a break-glass role.',
            'preventive')
    if policies[2]['total'] and policies[2]['pct'] < 80:
        add('HIGH', 'Backups are not governed separately from production',
            f"{len(policies[2]['failing'])} of {policies[2]['total']} backup targets are mutable"
            + (' (vault locks not readable).' if locked is None else f" and {locked} vaults are locked."),
            'Make the backup account a governed boundary: Object Lock on every backup target, Vault Lock, an organization backup policy with cross-account copy, and separate break-glass credentials.',
            'recovery')
    if tag_scope and class_pct < 50:          # tags not readable anywhere: no tag finding
        add('MEDIUM', 'Data classification is not machine-readable',
            f'{class_pct}% of buckets carry a data-classification tag; regulated data is recognisable only by name.',
            'Define a data-classification tag (e.g. public / internal / regulated / legal) in a tag policy and require it at creation; map each value to required controls.',
            'ownership')
    if lvl_det <= 2 and (rec_gap or rule_gap):          # never raised from unreadable signals alone
        add('MEDIUM', 'No continuous check of the data protection baseline',
            f"AWS Config records in {len(cfg['recording_accounts'])} of {len(accts)} accounts"
            + (f" (not readable in {len(rec_unknown)})" if rec_unknown else '')
            + ('' if not rule_gap else ' with no versioning, replication or backup rules' if not dp_rules
               else f"; no versioning, replication or backup rule in {missing_dp}")
            + ('; Security Hub is off.' if not g['security_hub']['enabled_accounts'] and not sh_unknown else '.'),
            'Deploy AWS Config rules org-wide (s3-bucket-versioning-enabled, s3-bucket-replication-enabled, s3-bucket-default-lock-enabled, s3-resources-protected-by-backup-plan) and enable Security Hub S3 controls; report with AWS Backup Audit Manager.',
            'detective')
    if restore == 0:                        # None = not readable: no finding
        add('MEDIUM', 'Recoverability is assumed, not tested',
            'No AWS Backup restore testing plan; RPO/RTO per data class is not visible in configuration.',
            'Agree RPO/RTO per data class, then schedule AWS Backup restore testing.',
            'recovery')
    if tag_scope and owner_pct < 80:
        add('LOW', 'Many buckets have no accountable owner',
            f'{100 - owner_pct}% of in-scope buckets have no owner tag, so findings have nobody to go to.',
            'Require an owner tag (team or cost center) and route posture findings to it.', 'ownership')

    skipped = len(level) - len(assessed)
    pick = assessed or {k: 1 for k in level}
    summary = (f"Governance maturity is assessed at level {ov[0]} of 5 ({ov[1]}), average {overall}"
               + (f" over {len(assessed)} of {len(level)} dimensions ({skipped} not assessed)" if skipped else '') + ". "
               f"Strongest dimension: {DIMENSIONS[max(pick, key=pick.get)][0].lower()}. "
               f"Weakest: {DIMENSIONS[min(pick, key=pick.get)][0].lower()}. "
               f"The bucket fixes in this report close today's gaps; the governance items stop them from coming back.")
    return dict(overall=overall, overall_level=ov[0], overall_label=ov[1], summary=summary,
                levels=[dict(level=l, label=n, description=d) for l, n, d in LEVELS],
                dimensions=dims, policies=policies, findings=findings)
