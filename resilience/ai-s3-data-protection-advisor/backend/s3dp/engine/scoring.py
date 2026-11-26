"""Deterministic scoring: inventory -> data.json. Ported from the S3 data protection advisor sample engine."""
import re
from collections import Counter, defaultdict

from s3dp.engine import governance, instructions
from s3dp.model import AI_OFF, CRITICAL_NAMING, NONPROD_NAMING, RISK_LABEL, RISKS, ROLE_LABEL, size_label as tb

SEV = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2, 'INFO': 3}


# ---------- Risk coverage ----------
def malicious_coverage(versioning, object_lock):
    """Return the strongest verified protection without erasing weaker evidence."""
    versioning_baseline = ('partial' if versioning in ('Enabled', 'Suspended')
                           else 'unprotected' if versioning == 'Never enabled'
                           else 'unknown')
    object_lock = object_lock or 'NotVerified'
    lock = object_lock.upper()
    if lock.startswith('COMPLIANCE'):
        return 'protected'
    if lock.startswith('GOVERNANCE'):
        return 'protected_governance'
    if ('PER-OBJECT RETENTION VERIFIED' in lock
            or 'ACTIVE PER-OBJECT RETENTION' in lock):
        return 'protected_lock'
    if object_lock == 'NotVerified' or lock.startswith('ENABLED'):
        return versioning_baseline
    return versioning_baseline


def coverage(b):
    v, lock, rep = b['versioning'], b['object_lock'], b['replication']
    he = {'Enabled': 'protected', 'Suspended': 'partial', 'Never enabled': 'unprotected'}.get(v, 'unknown')
    ma = malicious_coverage(v, lock)
    if v == 'Never enabled':
        rd = 'unprotected'                  # replication requires versioning
    elif isinstance(rep, dict):
        rd = 'protected'                    # CRR source, or the regional copy itself
    else:
        rd = {'None': 'unprotected'}.get(rep, 'unknown')
    return {'human_error': he, 'malicious_activity': ma, 'regional_disruption': rd}


def controls_matrix(b, third_party=''):
    """Protection matrix for the bucket detail view: one row per control, one cell per threat.

    Cell = [state, text] with state in ok | partial | missing | unknown | na (control does not address the threat).
    Only controls linked to a threat appear here; other evidence (encryption, storage class...) stays as text.
    """
    v, lock, rep = b['versioning'], b['object_lock'] or 'NotVerified', b['replication']
    na = lambda t='': ['na', t]
    rows = []
    # Versioning: point-in-time recovery; against malicious activity it only makes deletes recoverable.
    vs = {'Enabled': ('ok', 'Enabled'), 'Suspended': ('partial', 'Suspended: new writes unprotected'),
          'Never enabled': ('missing', 'Never enabled')}.get(v, ('unknown', 'Not verified'))
    vma = {'ok': ['partial', 'Deletes recoverable, not immune'], 'partial': ['partial', 'Older versions only'],
           'missing': ['missing', 'No versions to recover'], 'unknown': ['unknown', 'Not verified']}[vs[0]]
    rows.append(dict(control='Versioning', value=v, cells={'he': list(vs), 'ma': vma,
                                                          'rd': na('Prerequisite for replication')}))
    # Object Lock: write-once retention.
    L = lock.upper()
    if L.startswith('COMPLIANCE'):
        ol = ['ok', lock]
    elif L.startswith('GOVERNANCE'):
        ol = ['ok', lock + ' (bypassable with special permission)']
    elif 'PER-OBJECT RETENTION VERIFIED' in L or 'ACTIVE PER-OBJECT RETENTION' in L:
        ol = ['ok', lock]
    elif L.startswith('ENABLED'):
        ol = ['partial', 'Enabled, retention not verified']
    elif lock == 'NotVerified':
        ol = ['missing', 'Not possible without versioning'] if v == 'Never enabled' else ['unknown', 'Not verified']
    else:
        ol = ['missing', 'Not enabled']
    rows.append(dict(control='Object Lock', value=lock, cells={'he': na(), 'ma': ol, 'rd': na()}))
    # Delete guard: delete-deny bucket policy or MFA Delete (only shown when collected).
    pol = b.get('policy') if isinstance(b.get('policy'), dict) else {}
    dd, mfa = pol.get('delete_deny'), b.get('mfa_delete')
    if isinstance(dd, bool) or isinstance(mfa, bool):
        on = [n for n, x in (('delete-deny policy', dd), ('MFA Delete', mfa)) if x is True]
        unread = [n for n, x in (('delete-deny policy', dd), ('MFA Delete', mfa)) if x is None]
        g = (['partial', 'In place: ' + ', '.join(on)] if on
             else ['unknown', 'Not verified: ' + ', '.join(unread)] if unread       # unread is never "absent"
             else ['missing', 'No delete-deny policy or MFA Delete'])
        rows.append(dict(control='Delete guard (policy / MFA Delete)',
                         value=', '.join(on) or ('not verified' if unread else 'None'),
                         cells={'he': g, 'ma': g, 'rd': na()}))
    # Cross-region replication.
    if isinstance(rep, dict) and 'replica_of' in rep:
        rr = ['ok', f"Is the regional copy of {rep['replica_of']}"]
    elif isinstance(rep, dict) and rep.get('srr'):
        rr = ['partial', f"Same-region only → {rep.get('destination', '?')}"]
    elif isinstance(rep, dict):
        dest = f"→ {rep.get('destination', '?')} ({rep.get('destination_region') or 'region not visible'})"
        rr = ['partial' if rep.get('inferred') else 'ok', dest + (' — inferred, confirm' if rep.get('inferred') else '')]
    elif v == 'Never enabled' or rep == 'None':
        rr = ['missing', 'Not configured' + (' (needs versioning)' if v == 'Never enabled' else '')]
    else:
        rr = ['unknown', 'Not verified']
    if isinstance(rep, dict) and 'destination' in rep and rep.get('delete_markers') is None:
        rmal = ['unknown', 'Delete-marker replication not verified']     # rule format not recognised
    elif isinstance(rep, dict) and rep.get('delete_markers'):
        rmal = ['missing', 'Deletes replicate to the copy']
    else:
        rmal = na('A replica is not a backup')
    rows.append(dict(control='Cross-region replication', value=rr[1], cells={'he': na(), 'ma': rmal, 'rd': rr}))
    # AWS Backup and third-party backup.
    bk = b.get('aws_backup', 'None')
    if b.get('role') in ('backup_target', 'replica'):        # tier-aware: never flag a copy for lacking its own backup
        t = 'Is itself a backup copy' if b['role'] == 'backup_target' else 'Is itself a replica'
        cells = {'he': na(t), 'ma': na(t), 'rd': na(t)}
    elif bk.startswith('n/a'):
        cells = {'he': na(bk), 'ma': na(), 'rd': na()}
    elif bk == 'NotVerified':                                # AWS Backup protected resources not readable
        cells = {k: ['unknown', 'Not verified'] for k in ('he', 'ma', 'rd')}
    elif bk != 'None':
        cells = {'he': ['ok', bk], 'ma': ['partial', 'Separate copy; immutable only in a locked vault'],
                 'rd': ['unknown', 'Only with a cross-region copy rule']}
    elif third_party:
        cells = {k: ['unknown', f'{third_party} may cover it: confirm'] for k in ('he', 'ma')}
        cells['rd'] = ['unknown', f'{third_party} may cover it: confirm']
    else:
        cells = {'he': ['missing', 'Not covered'], 'ma': ['missing', 'Not covered'], 'rd': ['missing', 'Not covered']}
    rows.append(dict(control='Backup copy (AWS Backup' + (f' / {third_party}' if third_party else '') + ')',
                     value='not verified' if bk == 'NotVerified' else bk, cells=cells))
    return rows


def expectations(b):
    """(required, recommended) risks for the bucket's role."""
    role = b['role']
    if role == 'production':
        rd_req = b.get('regional_required', False)
        return (['human_error'] + (['regional_disruption'] if rd_req else []),
                ['malicious_activity'] + ([] if rd_req else ['regional_disruption']))
    return {'backup_target': (['human_error', 'malicious_activity'], ['regional_disruption']),
            'audit_trail': (['malicious_activity'], ['human_error']),
            'access_logs': ([], ['malicious_activity']),
            'replica': ([], ['human_error', 'malicious_activity'])}.get(role, ([], []))


def is_ok(state):
    return state in ('protected', 'protected_governance', 'protected_lock')


# ---------- Bucket families ----------
REGION_RE = re.compile(r'(af|ap|ca|eu|il|me|mx|sa|us)-(north|south|east|west|central|northeast|northwest|southeast|southwest)-\d')
COMPACT_RE = re.compile(r'^(euw|euc|eun|eus|use|usw|apse|apne|aps|cac|sae)\d$|^(euwest|eucentral|useast|uswest|apsoutheast|apnortheast)\d$')
GEO = {'us', 'eu', 'euro', 'emea', 'amer', 'apac', 'aspac', 'ireland', 'frankfurt', 'virginia', 'oregon', 'paris',
       'tokyo', 'sydney', 'singapore', 'london', 'stockholm', 'milan', 'zurich'}
REPLICA = {'crr', 'srr', 'replica', 'dr', 'copy'}


def family_key(name):
    s = REGION_RE.sub('', name.lower())
    out = []
    for t in re.split(r'[-._]+', s):
        if not t or t in GEO or t in REPLICA or COMPACT_RE.match(t) or re.fullmatch(r'\d{12}', t):
            continue
        if re.fullmatch(r'[0-9a-f]{7,8}', t) and re.search(r'\d', t) and re.search(r'[a-f]', t):
            continue                        # IaC random suffix
        out.append(t)
    return '-'.join(out)


def series_key(key):
    parts = key.split('-')
    if parts and parts[-1].isdigit():
        return '-'.join(parts[:-1])
    if parts and re.search(r'[a-z]\d+$', parts[-1]):
        return '-'.join(parts[:-1] + [re.sub(r'\d+$', '', parts[-1])])
    return key


def setting_values(b, inv):
    """Comparable settings: name -> (rank or None if unknown, display value)."""
    v = b['versioning']
    lock = b['object_lock']
    enc = b.get('encryption', 'NotVerified')
    ncv = b.get('noncurrent_expiration', b.get('lifecycle'))
    vals = {
        'Versioning': ({'Enabled': 3, 'Suspended': 2, 'Never enabled': 1}.get(v), v),
        'Object Lock': (None if lock == 'NotVerified' else 3 if lock.startswith('COMPLIANCE')
                        else 2 if lock.startswith('GOVERNANCE') else 1,
                        'not verified' if lock == 'NotVerified' else lock),
        # Unread Block Public Access is unknown, never "off".
        'Block Public Access': ({'on': 2, 'off': 1}.get(b.get('bucket_bpa', 'on')),
                                b.get('bucket_bpa', 'on') if b.get('bucket_bpa', 'on') in ('on', 'off') else 'not verified'),
        'Encryption type': ({'DSSE-KMS': 3, 'SSE-KMS': 2, 'SSE-S3': 1}.get(enc),
                            enc if enc in ('DSSE-KMS', 'SSE-KMS', 'SSE-S3') else 'not verified'),
        # An unread lifecycle is unknown, never "absent".
        'Lifecycle (noncurrent expiration)': (None if ncv is None else 2 if ncv else 1,
                                              'not verified' if ncv is None else 'present' if ncv else 'absent'),
    }
    if b['role'] != 'replica':
        backup = b.get('aws_backup', 'None')
        if backup == 'NotVerified':
            vals['AWS Backup coverage'] = (None, 'not verified')
        elif not backup.startswith('n/a'):
            vals['AWS Backup coverage'] = (2 if backup != 'None' else 1, backup)
        rep = b['replication']
        vals['Replication'] = (None if rep == 'NotVerified' else 2 if isinstance(rep, dict) else 1,
                               'not verified' if rep == 'NotVerified'
                               else f"CRR → {rep.get('destination_region') or 'region not visible'}"
                               if isinstance(rep, dict) else 'none')
    return vals


SETTING_SEV = {'Versioning': 'HIGH', 'Object Lock': 'HIGH', 'Block Public Access': 'HIGH',
               'AWS Backup coverage': 'MEDIUM', 'Replication': 'MEDIUM', 'Encryption type': 'MEDIUM',
               'Lifecycle (noncurrent expiration)': 'LOW'}
SETTING_VERB = {'Versioning': 'Enable versioning on {w}', 'Object Lock': 'Enable Object Lock ({v}) on {w}',
                'Block Public Access': 'Enable Block Public Access on {w}',
                'AWS Backup coverage': "Add {w} to AWS Backup plan '{v}'",
                'Replication': 'Add a replication rule on {w}', 'Encryption type': 'Switch default encryption to {v} on {w}',
                'Lifecycle (noncurrent expiration)': 'Add a NoncurrentVersionExpiration rule on {w}'}


def find_families(buckets, inv):
    # Unknown-role buckets are not compared: their expected protection is not known.
    comparable = [b for b in buckets if b['role'] not in ('non_production', 'tooling', 'access_logs', 'unknown')]
    by_name = {b['name']: b for b in comparable}
    fams = []
    # Replication pairs first: they get relation replication_pair even when names match
    paired = set()
    for b in comparable:
        rep = b['replication']
        if isinstance(rep, dict) and rep.get('destination') in by_name:
            fams.append(dict(members=[b, by_name[rep['destination']]], match='replication pair',
                             relation='replication_pair', basis='Confirmed CRR source and destination'))
            paired |= {b['name'], rep['destination']}
    groups, sgroups = defaultdict(list), defaultdict(list)
    for b in comparable:
        if b['name'] in paired:
            continue
        groups[family_key(b['name'])].append(b)
    used = set()
    for k, ms in groups.items():
        if len({m['region'] for m in ms}) >= 2:
            fams.append(dict(members=ms, match='exact', relation='peer',
                             basis='Same name apart from the region / geography token'))
            used |= {m['name'] for m in ms}
    for k, ms in groups.items():
        if not set(m['name'] for m in ms) & used:
            sgroups[series_key(k)].extend(ms)
    for k, ms in sgroups.items():
        if len(ms) >= 2 and len({m['region'] for m in ms}) >= 2:
            fams.append(dict(members=sorted(ms, key=lambda m: m['name']), match='similar', relation='peer',
                             basis='Names differ only by a trailing ordinal: confirm these are the same workload'))
    for f in fams:
        f['family'] = (f"{f['members'][0]['name']} → {f['members'][1]['name'].rsplit('-', 1)[-1]}"
                       if f['relation'] == 'replication_pair' else
                       series_key(family_key(f['members'][0]['name'])) + '-NNN' if f['match'] == 'similar' else
                       family_key(f['members'][0]['name']))
    return fams


def compare_family(f, inv, third_party_present):
    rows = []
    vals = {m['name']: setting_values(m, inv) for m in f['members']}
    settings = set.intersection(*(set(v) for v in vals.values()))
    for s in sorted(settings, key=lambda s: (SEV[SETTING_SEV[s]], s)):
        if f['relation'] == 'replication_pair' and s in ('Replication', 'AWS Backup coverage'):
            continue
        known = {n: vals[n][s] for n in vals if vals[n][s][0] is not None}
        unknown = [n for n in vals if vals[n][s][0] is None]
        ranks = {r for r, _ in known.values()}
        members = [dict(account=m['account'], bucket=m['name'], region=m['region'], value=vals[m['name']][s][1])
                   for m in f['members']]
        base = dict(family=f['family'], match=f['match'], relation=f['relation'], basis=f['basis'],
                    members=members, setting=s)
        if len(ranks) > 1:
            best = max(known, key=lambda n: known[n][0])
            weaker = [n for n in known if known[n][0] < known[best][0]]
            sev = SETTING_SEV[s]
            if s == 'AWS Backup coverage' and third_party_present:
                sev = 'LOW'
            rows.append(dict(base, severity=sev, align_to=best, weaker=weaker,
                             recommendation=SETTING_VERB[s].format(w=', '.join(weaker), v=known[best][1])
                             + f' to match {best}.'))
        elif unknown and known:
            rows.append(dict(base, severity='INFO', align_to=None, weaker=[],
                             recommendation=f"Confirm {s.lower()} on {', '.join(unknown)}; "
                                            f"the other member{'s' if len(known) > 1 else ''} "
                                            f"{'are' if len(known) > 1 else 'is'} {next(iter(known.values()))[1]}."))
    return rows


# ---------- AWS Backup posture ----------
def _unknown_check(label, description):
    """A check whose input could not be read: shown, never counted as passed or failed, no severity."""
    return {'label': f'{label}: not readable', 'pass': True, 'unknown': True,
            'description': f'{description}. Could not be read: confirm'}


def backup_posture_checks(posture):
    """Estate AWS Backup checks. A posture value that could not be read (None) gives an unknown check."""
    vaults = posture.get('vaults')
    total, copy = posture.get('plans_total'), posture.get('plans_with_copy_actions')
    restore = posture.get('restore_testing_plans')
    opt_off = sorted({r for _, r in posture.get('opt_in_off') or []}
                     | {r for r, on in posture.get('s3_opt_in', {}).items() if on is False})
    opt_unknown = sorted(({r for _, r in posture.get('opt_in_unknown') or []}
                          | {r for r, on in posture.get('s3_opt_in', {}).items() if on is None}) - set(opt_off))
    checks = []
    if vaults is None:
        checks.append(_unknown_check('Vault Lock', 'Recovery points in an unlocked vault can be deleted'))
        checks.append(_unknown_check('Logically air-gapped vault', 'Isolated recovery copy'))
    else:
        locked = sum(bool(v['locked']) for v in vaults)
        checks.append({'label': f"Vault Lock: {locked} of {len(vaults)} vaults locked",
                       'pass': bool(vaults) and locked == len(vaults), 'severity': 'MEDIUM',
                       'description': 'Recovery points in an unlocked vault can be deleted'})
        air = posture.get('air_gapped_vault_present')
        checks.append({'label': 'Logically air-gapped vault' + ('' if air else ': none'), 'pass': bool(air),
                       'severity': 'LOW', 'description': 'Isolated recovery copy'})
    if total is None or copy is None:
        checks.append(_unknown_check('Copy rules', 'Without copies, recovery points share the source blast radius'))
    else:
        checks.append({'label': f"Copy rules: {copy} of {total} plans", 'pass': total > 0 and copy == total,
                       'severity': 'MEDIUM', 'description': 'Without copies, recovery points share the source blast radius'})
    if restore is None:
        checks.append(_unknown_check('Restore testing plans', 'Recoverability tested'))
    else:
        checks.append({'label': f"Restore testing plans: {restore}", 'pass': restore > 0, 'severity': 'LOW',
                       'description': 'Recoverability tested'})
    if posture.get('opt_in_off') is None and posture.get('opt_in_unknown') is None and 's3_opt_in' not in posture:
        checks.append(_unknown_check('S3 opt-in for AWS Backup', 'Backup selections silently skip S3 where opt-in is off'))
    elif opt_off or not opt_unknown:
        checks.append({'label': 'S3 opt-in for AWS Backup' + (f": off in {', '.join(opt_off)}" if opt_off else ': on')
                       + (f"; not readable in {', '.join(opt_unknown)}" if opt_unknown else ''),
                       'pass': not opt_off, 'severity': 'MEDIUM',
                       'description': 'Backup selections silently skip S3 where opt-in is off'})
    else:
        checks.append(_unknown_check(f"S3 opt-in for AWS Backup in {', '.join(opt_unknown)}",
                                     'Backup selections silently skip S3 where opt-in is off'))
    for c in checks:
        if c['pass']:
            c.pop('severity', None)
    return checks


# ---------- Build ----------
# Stored as a level (1-5), not as '$$$': a renderer that inserts the data with String.replace turns '$$' into '$'.
MAGNITUDE = [(10, 1, 'single dollars'), (100, 2, 'tens of dollars'), (1000, 3, 'hundreds of dollars'),
             (10000, 4, 'thousands of dollars'), (float('inf'), 5, 'tens of thousands of dollars or more')]


def magnitude(usd):
    for limit, level, label in MAGNITUDE:
        if usd < limit:
            return dict(level=level, label=f'{label} per month')


def cost_items(items):
    out = []
    for it in items:
        it = dict(it, magnitude=magnitude(it['monthly_usd']), annual_usd=it['monthly_usd'] * 12)
        if it['impact'] == 'saving':
            it['estimatedSaving'] = it['monthly_usd']   # read by the template's built-in renderer
        out.append(it)
    return sorted(out, key=lambda i: (i['impact'] == 'cost', -i['monthly_usd']))


def build(inv):
    accts = inv['accounts']
    tp_vendors = [t['vendor'] for t in inv['third_party']]
    buckets = inv['buckets']
    posture = inv['backup_posture']

    families = find_families(buckets, inv)
    inconsistencies = []
    consistent = []
    for f in families:
        rows = compare_family(f, inv, bool(tp_vendors))
        if rows:
            inconsistencies += rows
        else:
            consistent.append(dict(family=f['family'], note=f"{f['match']}: all compared settings match"))
    inc_by_bucket = defaultdict(list)
    for r in inconsistencies:
        if r['severity'] != 'INFO':
            for w in r['weaker']:
                inc_by_bucket[w].append(r)

    cards, rec_rows, exposures = [], [], []
    cov_count = {r: Counter() for r in RISKS}
    for b in buckets:
        cov = coverage(b)
        req, rec = expectations(b)
        gap = [r for r in req if cov[r] == 'unprotected']
        size_m = b['size_tb']
        size = size_m or 0
        excluded = b['role'] in ('non_production', 'tooling', 'access_logs')
        findings = b.get('findings', [])
        third_party = (', '.join(tp_vendors) if tp_vendors and b['role'] == 'production'
                       and b.get('aws_backup', 'None') == 'None' else '')
        if not excluded and b['role'] != 'unknown':      # role to confirm: listed apart, not counted
            for r in RISKS:
                cov_count[r][cov[r].replace('protected_governance', 'protected')] += 1

        # Status
        if b['role'] == 'unknown':
            status, color = 'CONFIRM ROLE', 'grey'
        elif b['role'] == 'non_production':
            status, color = 'NON-PROD', 'grey'
        elif b['role'] in ('tooling', 'access_logs'):
            status, color = 'LOG/TOOLING', 'grey'
        elif gap:
            status, color = ('GAP — CONFIRM 3P', 'orange') if third_party else ('RISK GAP', 'red')
        elif any(cov[r] == 'unknown' for r in req):
            status, color = 'CONFIRM', 'grey'
        elif any(cov[r] == 'partial' for r in req) or (
                'malicious_activity' in rec and cov['malicious_activity'] in ('unprotected', 'partial')):
            status, color = 'PARTIAL', 'yellow'
        elif any(f['severity'] in ('HIGH', 'MEDIUM') for f in findings):
            status, color = 'ISSUE', 'orange'
        else:
            status, color = 'PROTECTED', 'green'

        # Priority focus score
        score, reasons = 0, []
        for r in req:
            if cov[r] == 'unprotected':
                score += 50; reasons.append(f'Missing required: {RISK_LABEL[r]}')
            elif cov[r] == 'partial':
                score += 15; reasons.append(f'Partial: {RISK_LABEL[r]}')
            elif cov[r] == 'unknown':
                score += 5; reasons.append(f'Unconfirmed: {RISK_LABEL[r]}')
        for lim, pts in ((100, 30), (10, 20), (1, 12), (0.1, 5)):
            if size >= lim:
                score += pts; reasons.append(f'Large: {tb(size)}' if lim >= 1 else f'Size: {tb(size)}'); break
        if b['role'] in ('backup_target', 'audit_trail'):
            score += 15; reasons.append('Backup target: copy of last resort' if b['role'] == 'backup_target'
                                        else 'Audit trail: tamper-evidence required')
        elif b['role'] == 'production':
            score += 8
        elif b['role'] == 'non_production':
            score -= 30
        crit = [w for w in CRITICAL_NAMING if re.search(rf'(^|[-_.]){w}', b['name'])]
        if crit:
            score += 10; reasons.append(f'Business-critical naming: {crit[0]}')
        if any(re.search(rf'(^|[-_.]){w}', b['name']) for w in NONPROD_NAMING):
            score -= 15
        if any(f['severity'] == 'HIGH' for f in findings):
            score += 10; reasons.append('HIGH finding')
        if b.get('cost_saving'):
            reasons.append(f"Cost saving: ~${b['cost_saving']['monthly_usd']:,.0f}/month")
        if b['name'] in inc_by_bucket:
            peer = inc_by_bucket[b['name']][0]['align_to']
            reasons.append(f'Inconsistent with {peer}')
        tier = 'Critical' if score >= 80 else 'High' if score >= 60 else 'Medium' if score >= 35 else 'Low'
        spotlight = not excluded and (score >= 60 or (gap and size >= 1) or size >= 100 or (size >= 10 and crit and b['role'] != 'replica'))

        # Recommended action (derived from coverage vs role)
        acts = []
        if cov['human_error'] == 'partial':
            acts.append('Re-enable versioning (currently Suspended)')
        elif cov['human_error'] == 'unprotected' and ('human_error' in req + rec):
            acts.append('Enable versioning')
        if not is_ok(cov['malicious_activity']) and cov['malicious_activity'] != 'unknown' and 'malicious_activity' in req + rec:
            acts.append('Object Lock (Governance) or vendor-native immutability' if b['role'] == 'backup_target'
                        else 'Add Object Lock (Governance)')
        if cov['regional_disruption'] == 'unprotected' and 'regional_disruption' in req:
            acts.append('Add cross-region replication')
        elif cov['regional_disruption'] == 'unprotected' and 'regional_disruption' in rec and size >= 10:
            acts.append('Evaluate cross-region replication for critical prefixes')
        for r in req:
            if cov[r] == 'unknown':
                acts.append(f'Confirm {RISK_LABEL[r].lower()} protection')
        if b['versioning'] in ('Enabled', 'Suspended') and b.get('noncurrent_expiration') is False and not excluded:
            acts.append('add NoncurrentVersionExpiration lifecycle')
        action = '; '.join(acts) if acts and not excluded else 'None — protected for its role'
        saving = b.get('cost_saving')
        if saving:
            action = saving['action'] + ('' if action.startswith('None') else '; ' + action)

        vce = b.get('vce')
        vce_s = (f"${vce['monthly_usd']:.2f}/mo (${vce['range'][0]:.2f}–${vce['range'][1]:.2f}) · {vce['basis']}"
                 if vce else '')
        rep = b['replication']
        nv = lambda k: f"Not verified ({b.get('errors', {}).get(k, 'not readable')})"
        rep_s = (f"CRR → {rep['destination']} ({rep.get('destination_region') or 'region not visible'}), delete-marker replication "
                 f"{'unknown' if rep.get('delete_markers') is None else 'on' if rep['delete_markers'] else 'off'}"
                 if isinstance(rep, dict) and 'destination' in rep
                 else f"is the regional copy of {rep['replica_of']}" if isinstance(rep, dict)
                 else 'not configured (verified)' if rep == 'None' else nv('replication'))
        evidence = [
            dict(label='Role', value=f"{ROLE_LABEL[b['role']]}: {b.get('role_reason', '')}"
                 + (' (inferred by the generative AI assistant)' if b.get('role_source') == 'ai' else '')),
            dict(label='Versioning', value=nv('versioning') if b['versioning'] == 'NotVerified' else b['versioning']),
            dict(label='Object Lock', value=nv('object_lock') if b['object_lock'] == 'NotVerified' else b['object_lock']),
            dict(label='Replication', value=rep_s),
            dict(label='Encryption', value=(nv('encryption') if b.get('encryption') == 'NotVerified' else b.get('encryption', '—'))
                 + (' + Bucket Keys' if b.get('bucket_key') else '')),
            dict(label='Lifecycle', value=nv('lifecycle') if b.get('lifecycle') is None else 'yes' if b['lifecycle'] else 'none'),
            dict(label='AWS Backup', value=('Not verified (AWS Backup protected resources not readable)'
                                            if b.get('aws_backup') == 'NotVerified' else b.get('aws_backup', 'None')) +
                 (f' — {third_party} present in estate, coverage unconfirmed' if third_party else '')),
            dict(label='Storage class', value=b['storage_class']),
        ]
        if b.get('write_pattern'):
            evidence.append(dict(label='Write pattern (30d)', value=b['write_pattern']))
        if b.get('access_pattern'):
            evidence.append(dict(label='Access pattern (90d)', value=b['access_pattern']))
        if vce:
            evidence.append(dict(label='Versioning cost est.',
                                 value=f"${vce['monthly_usd']:.2f}/mo (range ${vce['range'][0]:.2f}–${vce['range'][1]:.2f}) — basis: "
                                       f"{vce['basis']}, {vce['churn_pct']}% churn, {vce.get('retention_days', 30)}d noncurrent retention, estimate"))
        if b.get('vendor'):
            evidence.append(dict(label='Third-party target', value=b['vendor']))
        if b.get('note'):
            evidence.append(dict(label='Note', value=b['note']))
        for r in inc_by_bucket.get(b['name'], []):
            evidence.append(dict(label='Inconsistency', value=f"{r['setting']}: {r['recommendation']}"))

        chips = ([f'Missing required: {RISK_LABEL[r].lower()}' for r in gap]
                 + ([f'Inconsistent with {inc_by_bucket[b["name"]][0]["align_to"]}'] if b['name'] in inc_by_bucket else [])
                 + ([f"Save ~${saving['monthly_usd']:,.0f}/month"] if saving else [])
                 + [tb(size_m)])[:3]
        acct_name = accts[b['account']]['name']
        row = dict(bucket=b['name'], account=acct_name, account_id=b['account'], region=b['region'],
                   role=ROLE_LABEL[b['role']], size_bytes=None if size_m is None else int(size_m * 1024 ** 4), size_human=tb(size_m), size_short=tb(size_m),
                   chips=chips, he=cov['human_error'], ma=cov['malicious_activity'], rd=cov['regional_disruption'],
                   req=req, rec=rec, gap=gap, status=status, status_color=color, action=action, vce=vce_s,
                   vce_usd=vce['monthly_usd'] if vce else None, score=score, tier=tier, spotlight=bool(spotlight),
                   reasons=reasons, third_party=third_party,
                   findings=[dict(severity=f['severity'], title=f['title'], description=f['description']) for f in findings],
                   evidence=evidence, controls=controls_matrix(b, third_party))
        cards.append(dict(id=f"{b['account']}-{b['name']}", title=b['name'], subtitle=b['region'],
                          badge=dict(text=status, color=color), sortValue=row['size_bytes'],
                          details=[dict(label=e['label'], value=e['value']) for e in evidence],
                          recommendations=row['findings'], row=row))

        # Recommendation rows: one per finding with an action, else the derived action
        base = dict(account=b['account'], account_name=acct_name, bucket=b['name'], region=b['region'],
                    size_bytes=row['size_bytes'], size_human=row['size_human'],
                    tier=ROLE_LABEL[b['role']], status=status)
        added = False
        for f in findings:
            if f.get('action'):
                sev = f['severity']
                if gap and sev != 'HIGH' and any(k in f['action'].lower() for k in ('versioning', 'object lock', 'immutab')):
                    sev = 'HIGH'            # risk_gap drives severity for the role-required control
                rec_rows.append(dict(base, severity=sev, recommendation=f['action'], why=f['title'],
                                     detail=f['description']))
                added = True
        if not added and not action.startswith('None'):
            sev = {'RISK GAP': 'HIGH', 'GAP — CONFIRM 3P': 'MEDIUM', 'CONFIRM': 'INFO'}.get(status, 'LOW')
            rec_rows.append(dict(base, severity=sev, recommendation=action, why=f'Status: {status}', detail=''))

        # Critical exposures
        if b['role'] == 'production':
            failed, unconfirmed = [], []
            # An unreadable setting (aws_backup NotVerified, coverage 'unknown') is never counted as failed, and a
            # third-party backup tool in the estate may cover the bucket: its backup coverage is unconfirmed.
            if b.get('aws_backup', 'None') == 'None' and cov['regional_disruption'] == 'unprotected':
                (unconfirmed if third_party else failed).append('backup coverage')
            if cov['human_error'] in ('partial', 'unprotected'):
                failed.append('point-in-time protection')
            if not is_ok(cov['malicious_activity']) and cov['malicious_activity'] != 'unknown':
                failed.append('delete protection')
            if len(failed) >= 2:
                exposures.append(dict(bucket=b['name'], account=b['account'], size=tb(size_m), failed=failed,
                                      unconfirmed=unconfirmed))
        elif b['role'] == 'backup_target' and not is_ok(cov['malicious_activity']) \
                and cov['malicious_activity'] != 'unknown':
            exposures.append(dict(bucket=b['name'], account=b['account'], size=tb(size_m),
                                  failed=['copy immutability'] + (['point-in-time protection']
                                                                  if cov['human_error'] in ('partial', 'unprotected') else [])))
    exposures.sort(key=lambda e: (-len(e['failed']), e['bucket']))

    # Inconsistencies → recommendation rows (annotate the row recommending the same change, else add one)
    kw = {'Versioning': 'versioning', 'Object Lock': 'object lock', 'Encryption type': 'kms',
          'Lifecycle (noncurrent expiration)': 'noncurrent', 'AWS Backup coverage': 'aws backup',
          'Block Public Access': 'public access', 'Replication': 'replicat'}
    for r in inconsistencies:
        if r['severity'] not in ('HIGH', 'MEDIUM'):
            continue
        for w in r['weaker']:
            m = next(x for x in r['members'] if x['bucket'] == w)
            note = f"Inconsistent with {r['align_to']} ({r['setting'].lower()})"
            hit = [x for x in rec_rows if x['bucket'] == w and kw[r['setting']] in x['recommendation'].lower()]
            if hit:
                for x in hit:
                    x['severity'] = min(x['severity'], r['severity'], key=SEV.get)
                    x['inconsistency'] = '; '.join(filter(None, [x.get('inconsistency'), note]))
            else:
                sized = next((x for x in rec_rows if x['bucket'] == w and 'size_bytes' in x), {})
                rec_rows.append(dict(account=m['account'], account_name=accts[m['account']]['name'], bucket=w,
                                     region=m['region'], size_bytes=sized.get('size_bytes'),
                                     size_human=sized.get('size_human'), tier='', status='', severity=r['severity'],
                                     recommendation=r['recommendation'], why=note, detail='', inconsistency=note))

    # Account-level rows
    al = '— (account-level)'
    for a, meta in accts.items():
        vaults = [v for v in posture['vaults'] or [] if v['account'] == a]     # None = vaults not readable: no row
        if vaults and not any(v['locked'] for v in vaults):
            points = [v.get('recovery_points') for v in vaults]
            points = (f"{sum(points)} recovery points" if None not in points
                      else 'Recovery points (count not readable)')
            copy = posture.get('plans_with_copy_actions')
            copy = ('no cross-region copy rules' if copy == 0 else 'copy rules not readable' if copy is None
                    else f'{copy} plans with copy rules')
            rec_rows.append(dict(account=a, account_name=meta['name'], bucket=al, region='all', tier='', status='',
                                 severity='MEDIUM', why=f"{points} in unlocked vaults, {copy}",
                                 recommendation='Enable AWS Backup Vault Lock (Governance first) and add a cross-region or cross-account copy rule',
                                 detail='Without Vault Lock the backups themselves can be deleted by anyone with backup:Delete* permissions.'))
        if meta['account_bpa'] == 'off':
            rec_rows.append(dict(account=a, account_name=meta['name'], bucket=al, region='all', tier='', status='',
                                 severity='MEDIUM', recommendation='Enable account-level Block Public Access',
                                 why='Account-level BPA is off; it is the guardrail that catches misconfigured buckets', detail=''))
        # BKP-ACC-06 (HIGH). s3_opt_in: {region: bool} for every account; opt_in_off: [[account, region], ...] per account.
        off_regions = ({r for r, on in posture.get('s3_opt_in', {}).items() if not on}
                       | {r for acc, r in posture.get('opt_in_off') or [] if acc == a})
        off = sorted(r for r in off_regions if any(b['account'] == a and b['region'] == r for b in buckets))
        if off:
            rec_rows.append(dict(account=a, account_name=meta['name'], bucket=al, region=', '.join(off), tier='', status='',
                                 severity='HIGH', recommendation=f"Opt S3 in to AWS Backup in {', '.join(off)}",
                                 why='S3 resource-type opt-in disabled: backup selections silently skip these buckets', detail=''))
    if posture.get('restore_testing_plans') == 0:         # None = not readable: no finding
        rec_rows.append(dict(account=inv['hub_account'], account_name=accts[inv['hub_account']]['name'], bucket=al,
                             region='all', tier='', status='', severity='LOW',
                             recommendation='Create an AWS Backup restore testing plan for production S3 recovery points',
                             why='No restore testing plan exists: recoverability is assumed, not proven', detail=''))

    gov = governance.assess(inv, cards, families, inconsistencies)
    for f in gov['findings']:
        rec_rows.append(dict(account=inv['hub_account'], account_name=accts[inv['hub_account']]['name'],
                             bucket='— (organization-level)', region='all', tier='governance', status='',
                             severity=f['severity'], recommendation=f['improvement'], why=f"Governance: {f['title']}",
                             detail=f['evidence']))

    rec_rows.sort(key=lambda x: (SEV[x['severity']], x['account'], x['bucket'].lower()))
    for i, x in enumerate(rec_rows, 1):
        x.update(priority=i, resource=f"{x['account']} — {x['bucket']}", title=x['recommendation'],
                 description=x['why'] + ('. ' + x['detail'] if x['detail'] else ''))

    # Risk Protection Review (operational-review tab)
    backup_checks = backup_posture_checks(posture)
    known_backup = [c for c in backup_checks if not c.get('unknown')]

    def counts_pillar(r, name):
        c = cov_count[r]
        checks = [dict(label=f'Protected: {c["protected"]} buckets', **{'pass': True}, description='Evidence-backed'),
                  dict(label=f'Partial: {c["partial"]} buckets', **{'pass': False}, severity='MEDIUM',
                       description='e.g. versioning without Object Lock: recoverable, not immune'),
                  dict(label=f'Unprotected: {c["unprotected"]} buckets', **{'pass': False}, severity='HIGH',
                       description='Evidence shows the control is absent'),
                  dict(label=f'Unconfirmed: {c["unknown"]} buckets (excluded from score)', **{'pass': True},
                       description='Setting could not be read: confirm')]
        return {'name': name, 'pass': c['protected'], 'total': c['protected'] + c['partial'] + c['unprotected'], 'checks': checks}
    ops_resources = [
        dict(name='Estate risk overview: 3 risks', region='all', pillars=[
            counts_pillar('human_error', 'Human or application error protection'),
            counts_pillar('malicious_activity', 'Malicious activity protection'),
            counts_pillar('regional_disruption', 'Regional disruption protection')]),
        dict(name=f'Critical exposures ({len(exposures)})', region='all', pillars=[{
            'name': 'Role-expected protection', 'pass': 0, 'total': len(exposures),
            'checks': [{'label': f"{e['bucket']} ({e['size']})", 'pass': False, 'severity': 'HIGH',
                        'description': 'Fails: ' + ', '.join(e['failed'])
                                       + ('; unconfirmed: ' + ', '.join(e['unconfirmed'])
                                          + f" ({', '.join(tp_vendors)} may cover it)" if e.get('unconfirmed') else '')}
                       for e in exposures]}]),
        dict(name='AWS Backup posture', region='all', pillars=[{'name': 'AWS Backup posture', 'pass': sum(c['pass'] for c in known_backup), 'total': len(known_backup), 'checks': backup_checks}]),
    ]
    for a, meta in accts.items():
        mine = [c['row'] for c in cards if c['row']['account_id'] == a]
        bs = [r for r in mine if r['status'] not in ('NON-PROD', 'LOG/TOOLING', 'CONFIRM ROLE')]
        to_confirm = [r['bucket'] for r in mine if r['status'] == 'CONFIRM ROLE']
        checks = [{'label': r['bucket'], 'pass': r['status'] == 'PROTECTED',
                   **({} if r['status'] == 'PROTECTED' else {'severity': 'HIGH' if r['gap'] else 'MEDIUM'}),
                   'description': f"{r['role']} · {r['status']} · {r['action']}"} for r in bs]
        role_checks = checks + ([{'label': f'Role to confirm: {len(to_confirm)} buckets', 'pass': True, 'unknown': True,
                                  'severity': 'INFO',
                                  'description': 'Not scored until the role is confirmed: ' + ', '.join(to_confirm)}]
                                if to_confirm else [])
        public = ([{'label': 'Account-level Block Public Access',
                    'pass': meta['account_bpa'] == 'on', **({} if meta['account_bpa'] == 'on' else {'severity': 'MEDIUM'}),
                    'description': meta['account_bpa']}] if meta['account_bpa'] in ('on', 'off')
                  else [_unknown_check('Account-level Block Public Access', 'Guardrail against public buckets')])
        ops_resources.append(dict(name=f"{meta['name']} ({a})", region='all', pillars=[
            {'name': 'Role-expected protection', 'pass': sum(c['pass'] for c in checks), 'total': len(checks), 'checks': role_checks},
            {'name': 'Public access', 'pass': sum(c['pass'] for c in public if not c.get('unknown')),
             'total': sum(not c.get('unknown') for c in public), 'checks': public}]))

    data = {
        'meta': {'service': 'S3 Data Protection Advisor', 'account': inv['hub_account'],
                 'customerName': inv['customer'], 'generated': inv['scan_date'], 'audience': 'customer',
                 'ai': dict(inv.get('run', {}).get('ai') or AI_OFF)},
        'tabs': [
            {'id': 'resources', 'label': 'Buckets', 'type': 'cards', 'cards': cards},
            {'id': 'operational-review', 'label': 'Risk Review', 'type': 'operational-review',
             'description': ('Risk-based S3 data protection review: human or application errors → S3 Versioning · '
                             'malicious activity → S3 Object Lock · regional disruption → S3 Replication.'
                             + (f" Third-party backup detected: {', '.join(tp_vendors)}; buckets without AWS Backup "
                                'may be protected by it, confirm.' if tp_vendors else '')),
             'pillars': ['Human or application error protection', 'Malicious activity protection',
                         'Regional disruption protection', 'Role-expected protection', 'AWS Backup posture',
                         'Public access'],
             'resources': ops_resources},
            {'id': 'recommendations', 'label': 'Recommendations', 'type': 'recommendations', 'items': rec_rows},
            {'id': 'inconsistencies', 'label': 'Inconsistencies', 'type': 'inconsistencies',
             'families_compared': len(families), 'items': sorted(inconsistencies, key=lambda r: (SEV[r['severity']], r['family'])),
             'consistent_families': consistent,
             'note': 'Log, tooling and non-production buckets are not compared.'},
            dict({'id': 'governance', 'label': 'Governance', 'type': 'governance'}, **gov),
            {'id': 'cost-optimization', 'label': 'Cost Optimization', 'type': 'cost-optimization', 'items': cost_items(inv['cost_optimization'])},
        ],
    }
    data['tabs'].insert(0, instructions.build_tab(inv, data))
    return data, dict(buckets=len(buckets), recs=Counter(r['severity'] for r in rec_rows),
                      inconsistencies=Counter(r['severity'] for r in inconsistencies), families=len(families),
                      governance=f"L{gov['overall_level']} {gov['overall_label']} ({gov['overall']})", dims={d['id']: d['level'] for d in gov['dimensions']}, exposures=len(exposures), focus=sum(c['row']['spotlight'] for c in cards))
