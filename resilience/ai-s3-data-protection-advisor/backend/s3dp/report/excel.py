"""Excel edition of the report: one worksheet per tab, built from the same data.json.

to_workbook_spec(data) turns a report data.json into a sheet spec; write_xlsx() writes it with openpyxl. Tabs that hold
several tables (Governance) get one extra worksheet per table, named "<Tab> - <table>".

    python -m s3dp.report.excel <data.json> <report.xlsx>
"""
import json
import re
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from s3dp.model import ai_notice

RED = {'bg_color': 'FFC7CE', 'font_color': '9C0006'}
ORANGE = {'bg_color': 'FFEB9C', 'font_color': '9C5700'}
YELLOW = {'bg_color': 'FFF2CC', 'font_color': '7F6000'}
GREEN = {'bg_color': 'C6EFCE', 'font_color': '006100'}
GREY = {'bg_color': 'EDEDED', 'font_color': '595959'}
# The generator matches rules as case-insensitive substrings, first match wins: "unprotected" before "protected".
SEVERITY_CF = {'CRITICAL': RED, 'HIGH': RED, 'MEDIUM': ORANGE, 'LOW': YELLOW, 'INFO': GREY, 'OK': GREEN}
COVERAGE_CF = {'unprotected': RED, 'partial': ORANGE, 'unconfirmed': GREY, 'likely': YELLOW, 'protected': GREEN}
TIER_CF = {'Critical': RED, 'High': ORANGE, 'Medium': YELLOW, 'Low': GREY}
COVERAGE = {'protected': 'protected', 'protected_governance': 'protected (Object Lock GOVERNANCE, bypassable)',
            'protected_lock': 'protected (Object Lock, per-object retention)', 'likely': 'likely protected (confirm)',
            'partial': 'partial', 'unprotected': 'unprotected', 'unknown': 'unconfirmed'}
RISK = {'human_error': 'Human/application errors', 'malicious_activity': 'Malicious activity',
        'regional_disruption': 'Regional disruption'}


def _txt(v, sep='; '):
    if v is None:
        return ''
    if isinstance(v, (list, tuple)):
        return sep.join(_txt(i, ', ') for i in v)
    if isinstance(v, dict):
        return sep.join(f'{k}: {_txt(x)}' for k, x in v.items())
    return v if isinstance(v, (int, float)) else str(v)


def _magnitude(m):
    return m.get('label', '') if isinstance(m, dict) else (m or '')


def _tb(b):
    """Bytes -> TiB (1024**4), the unit of every size label in the report."""
    return round(b / 1024 ** 4, 3) if isinstance(b, (int, float)) else None


def _sheet(name, header, rows, widths=None, wrap=None, formats=None, cf=None):
    cell = lambda v: v if v is None or isinstance(v, (str, int, float)) else _txt(v)
    s = {'name': name, 'data': [header] + [[cell(v) for v in r] for r in rows], 'freeze_panes': 'A2', 'auto_filter': True}
    if widths:
        s['column_widths'] = widths
    if wrap:
        s['wrap_text'] = wrap
    if formats:
        s['number_formats'] = formats
    if cf:
        s['conditional_formatting'] = [{'column': c, 'rules': r} for c, r in cf]
    return s


def _instructions(t):
    rows = []
    for sec in t.get('sections', []):
        for i in sec.get('items', []):
            rows.append([sec['title'], i, ''])
        tbl = sec.get('table')
        if tbl:
            cols = tbl.get('columns', [])
            for r in tbl.get('rows', []):
                rows.append([sec['title'], _txt(r[0]), ' · '.join(f'{c}: {_txt(v)}' for c, v in zip(cols[1:], r[1:]))])
        if sec.get('note'):
            rows.append([sec['title'], sec['note'], ''])
    if t.get('intro'):
        rows.insert(0, [t.get('heading', 'Introduction'), t['intro'], ''])
    return [_sheet(t['label'], ['Section', 'Item', 'Detail'], rows, {'Section': 28, 'Item': 80, 'Detail': 80},
                   ['Item', 'Detail'])]


def _buckets(t):
    rows = []
    for c in t.get('cards', []):
        r = c.get('row')
        if not r:
            continue
        rows.append([r.get('tier'), r.get('score'), 'yes' if r.get('spotlight') else '', r.get('bucket'),
                     r.get('account'), r.get('account_id'), r.get('region'), r.get('role'),
                     next((e['value'] for e in r.get('evidence', []) if e['label'] == 'Role'), ''), r.get('size_human'),
                     _tb(r.get('size_bytes')), COVERAGE.get(r.get('he'), r.get('he')),
                     COVERAGE.get(r.get('ma'), r.get('ma')), COVERAGE.get(r.get('rd'), r.get('rd')),
                     _txt([RISK.get(x, x) for x in r.get('req', [])]), _txt([RISK.get(x, x) for x in r.get('gap', [])]),
                     r.get('status'), r.get('action'), r.get('vce'), r.get('vce_usd'), _txt(r.get('reasons')),
                     r.get('third_party') or '',
                     _txt([f"[{f.get('severity')}] {f.get('title')}" for f in r.get('findings', [])]),
                     _txt([f"{e.get('label')}: {e.get('value')}" for e in r.get('evidence', [])])])
    hdr = ['Priority', 'Score', 'Priority focus', 'Bucket', 'Account', 'Account ID', 'Region', 'Role', 'Role reason', 'Size', 'Size (TiB)',
           'Human error', 'Malicious activity', 'Regional disruption', 'Required protection',
           'Missing required', 'Status', 'Recommended action', 'Versioning cost', 'Versioning cost (USD/month)',
           'Why prioritized', 'Third-party backup in estate', 'Findings', 'Evidence']
    return [_sheet(t['label'], hdr, rows,
                   {'Bucket': 40, 'Recommended action': 60, 'Why prioritized': 50, 'Findings': 60, 'Evidence': 80,
                    'Account ID': 15},
                   ['Recommended action', 'Why prioritized', 'Findings', 'Evidence'],
                   {'Size (TiB)': '#,##0.000', 'Versioning cost (USD/month)': 'currency', 'Account ID': 'text'},
                   [('Priority', TIER_CF), ('Human error', COVERAGE_CF), ('Malicious activity', COVERAGE_CF),
                    ('Regional disruption', COVERAGE_CF)])]


def _governance(t):
    name = t['label']
    out = [_sheet(name, ['Dimension', 'Level', 'Current state', 'Next level', 'Evidence'],
                  [['Overall', t.get('overall'), t.get('overall_label'), '', t.get('summary')]] +
                  [[d.get('name'), 'Not assessed' if d.get('level') is None else d.get('level'), d.get('current'),
                    d.get('next'), _txt(d.get('evidence'))] for d in t.get('dimensions', [])],
                  {'Dimension': 34, 'Current state': 50, 'Next level': 50, 'Evidence': 60},
                  ['Current state', 'Next level', 'Evidence'])]
    if t.get('levels'):
        out.append(_sheet(f'{name} - Levels', ['Level', 'Label', 'Description'],
                          [[l.get('level'), l.get('label'), l.get('description')] for l in t['levels']],
                          {'Description': 90}, ['Description']))
    if t.get('policies'):
        out.append(_sheet(f'{name} - Policies', ['Rule', 'Compliant', 'Total', 'Compliance', 'Failing buckets',
                                                 'Unconfirmed', 'Note', 'Why'],
                          [[p.get('rule'), p.get('compliant'), p.get('total'),
                            (p['pct'] / 100) if isinstance(p.get('pct'), (int, float)) else None,
                            _txt(p.get('failing'), ', '), _txt(p.get('unconfirmed'), ', '), p.get('note', ''),
                            p.get('why')] for p in t['policies']],
                          {'Rule': 50, 'Failing buckets': 70, 'Unconfirmed': 40, 'Note': 35, 'Why': 50},
                          ['Rule', 'Failing buckets', 'Unconfirmed', 'Why'],
                          {'Compliance': 'percent_whole'}))
    if t.get('findings'):
        out.append(_sheet(f'{name} - Findings', ['Severity', 'Finding', 'Dimension', 'Evidence', 'Improvement'],
                          [[f.get('severity'), f.get('title'), f.get('dimension'), f.get('evidence'),
                            f.get('improvement')] for f in t['findings']],
                          {'Finding': 45, 'Evidence': 60, 'Improvement': 60}, ['Finding', 'Evidence', 'Improvement'],
                          cf=[('Severity', SEVERITY_CF)]))
    return out


def _risk_review(t):
    rows = []
    for res in t.get('resources', []):
        for p in res.get('pillars', []):
            for c in p.get('checks', []):
                rows.append([res.get('name'), res.get('region'), p.get('name'), f"{p.get('pass')}/{p.get('total')}",
                             c.get('label'), 'unconfirmed' if c.get('unknown') else 'pass' if c.get('pass') else 'fail', c.get('severity') or '',
                             c.get('description')])
    return [_sheet(t['label'], ['Scope', 'Region', 'Pillar', 'Score', 'Check', 'Result', 'Severity', 'Description'],
                   rows, {'Scope': 40, 'Pillar': 32, 'Check': 50, 'Description': 70}, ['Check', 'Description'],
                   cf=[('Severity', SEVERITY_CF), ('Result', {'fail': RED, 'pass': GREEN})])]


def _recommendations(t):
    rows = [[i.get('priority'), i.get('severity'), i.get('bucket') or i.get('resource'), i.get('account_name'),
             i.get('account'), i.get('region'), i.get('size_human'), _tb(i.get('size_bytes')), i.get('tier'),
             i.get('status'), i.get('recommendation') or i.get('title'), i.get('description'),
             _txt(i.get('inconsistency'))] for i in t.get('items', [])]
    # Description (why + detail) is the text the narrative step rewrites, so it is the one shown.
    return [_sheet(t['label'], ['Priority', 'Severity', 'Bucket', 'Account', 'Account ID', 'Region', 'Size',
                                'Size (TiB)', 'Role', 'Status', 'Recommendation', 'Description', 'Inconsistency'],
                   rows, {'Bucket': 40, 'Recommendation': 60, 'Description': 80, 'Inconsistency': 40,
                          'Account ID': 15},
                   ['Recommendation', 'Description', 'Inconsistency'],
                   {'Size (TiB)': '#,##0.000', 'Account ID': 'text'}, [('Severity', SEVERITY_CF)])]


def _inconsistencies(t):
    rows = [[i.get('severity'), i.get('family'), i.get('setting'), i.get('match'), i.get('relation'),
             _txt([f"{m.get('bucket')} ({m.get('region')}): {m.get('value')}" for m in i.get('members', [])], '\n'),
             i.get('align_to'), _txt(i.get('weaker'), ', '), i.get('recommendation'), i.get('basis')]
            for i in t.get('items', [])]
    rows += [['OK', c.get('family'), 'all compared settings', '', '', '', '', '', c.get('note'), '']
             for c in t.get('consistent_families', [])]
    return [_sheet(t['label'], ['Severity', 'Family', 'Setting', 'Match', 'Relation', 'Members', 'Align to',
                                'Weaker', 'Recommendation', 'Basis'], rows,
                   {'Family': 40, 'Members': 70, 'Align to': 35, 'Weaker': 35, 'Recommendation': 60, 'Basis': 40},
                   ['Members', 'Weaker', 'Recommendation', 'Basis'], cf=[('Severity', SEVERITY_CF)])]


def _cost_optimization(t):
    rows = [[i.get('title'), _txt(i.get('resources'), '\n'), i.get('impact'), i.get('effort'), i.get('savings'),
             i.get('monthly_usd'), i.get('annual_usd'), i.get('one_time_usd'), i.get('worst_case_usd'),
             _magnitude(i.get('magnitude')), i.get('description'), i.get('basis')] for i in t.get('items', [])]
    return [_sheet(t['label'], ['Opportunity', 'Resources', 'Impact', 'Effort', 'Savings', 'Monthly (USD)',
                                'Annual (USD)', 'One-time (USD)', 'Worst case (USD/month)', 'Magnitude', 'Description',
                                'Basis'], rows,
                   {'Opportunity': 50, 'Resources': 50, 'Savings': 50, 'Description': 70, 'Basis': 50},
                   ['Opportunity', 'Resources', 'Savings', 'Description', 'Basis'],
                   {k: 'currency_whole' for k in ('Monthly (USD)', 'Annual (USD)', 'One-time (USD)',
                                                  'Worst case (USD/month)')})]


def _generic(t):
    """Fallback for a tab type without a dedicated layout: one row per item, one column per key."""
    items = [i for i in t.get('items', []) if isinstance(i, dict)]
    if not items:
        return []
    keys = list(dict.fromkeys(k for i in items for k in i))
    return [_sheet(t.get('label', t['id']), keys, [[_txt(i.get(k)) for k in keys] for i in items])]


BUILDERS = {'instructions': _instructions, 'cards': _buckets, 'governance': _governance,
            'operational-review': _risk_review, 'recommendations': _recommendations,
            'inconsistencies': _inconsistencies, 'cost-optimization': _cost_optimization}


def _sheet_name(name, used):
    """Excel sheet names: max 31 characters, none of []:*?/\\, unique (case-insensitive), not 'Report Info'."""
    base = re.sub(r'[\[\]:*?/\\]', '', name).strip()[:31] or 'Sheet'
    n, i = base, 2
    while n.lower() in used:
        n = f'{base[:28]} {i}'
        i += 1
    used.add(n.lower())
    return n


def to_workbook_spec(data):
    meta = data.get('meta', {})
    sheets, used = [], {'report info'}
    for t in data.get('tabs', []):
        build = BUILDERS.get(t.get('type'), _generic)
        for s in build(t):
            s['name'] = _sheet_name(s['name'], used)
            sheets.append(s)
    return {'metadata': {'title': f"{meta.get('service', 'S3 Data Protection Advisor')} - {meta.get('customerName', '')}",
                         'date': str(meta.get('generated', ''))[:10], 'customer': meta.get('customerName', ''),
                         'account': meta.get('account', 'N/A'), 'notice': ai_notice(data)},
            'sheets': sheets}


FORMULA_START = ('=', '+', '-', '@', '\t', '\r')
FORMATS = {'currency': '$#,##0.00', 'currency_whole': '$#,##0', 'percent_whole': '0%', 'text': '@'}


def _literal(cell):
    """Text that starts like a formula is stored as literal text, never evaluated."""
    if isinstance(cell.value, str) and cell.value.startswith(FORMULA_START):
        cell.data_type = 's'


def write_xlsx(data, out_path):
    """Write the Excel edition of data to out_path. Returns out_path.

    Layout: Report Info sheet first, blue header, frozen header row, filters. Text is never evaluated as a formula, on
    any sheet including Report Info: values starting with = + - @ are stored as literal text."""
    spec = to_workbook_spec(data)
    head_font, head_fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', start_color='4472C4')
    top = Alignment(vertical='top')
    wb = Workbook()
    info = wb.active
    info.title = 'Report Info'
    for k, v in spec['metadata'].items():
        info.append([k.replace('_', ' ').capitalize(), v])
        info.cell(info.max_row, 1).font = Font(bold=True)
        _literal(info.cell(info.max_row, 2))
    info.column_dimensions['A'].width, info.column_dimensions['B'].width = 18, 110
    for s in spec['sheets']:
        ws = wb.create_sheet(s['name'])
        rows = s['data']
        hdr = rows[0]
        for r in rows:
            ws.append(r)
        for c in range(1, len(hdr) + 1):
            ws.cell(1, c).font, ws.cell(1, c).fill = head_font, head_fill
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                _literal(cell)
                cell.alignment = top
        wrap = set(s.get('wrap_text', []))
        widths = s.get('column_widths', {})
        for ci, name in enumerate(hdr, 1):
            col = get_column_letter(ci)
            longest = max((len(str(r[ci - 1])) for r in rows if ci - 1 < len(r) and r[ci - 1] is not None), default=8)
            ws.column_dimensions[col].width = widths.get(name) or min(max(longest + 2, 10), 50)
            fmt = s.get('number_formats', {}).get(name)
            for (cell,) in ws.iter_rows(min_row=2, min_col=ci, max_col=ci):
                if name in wrap:
                    cell.alignment = Alignment(vertical='top', wrap_text=True)
                if fmt:
                    cell.number_format = FORMATS.get(fmt, fmt)
        for cf in s.get('conditional_formatting', []):
            if cf['column'] not in hdr:
                continue
            ci = hdr.index(cf['column']) + 1
            for (cell,) in ws.iter_rows(min_row=2, min_col=ci, max_col=ci):
                val = str(cell.value or '').lower()
                for match, st in cf['rules'].items():
                    if match.lower() in val:
                        cell.fill = PatternFill('solid', start_color=st['bg_color'])
                        cell.font = Font(bold=True, color=st['font_color'])
                        break
        ws.freeze_panes = s.get('freeze_panes', 'A2')
        if s.get('auto_filter') and len(rows) > 1:
            ws.auto_filter.ref = ws.dimensions
    wb.save(out_path)
    return out_path


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(write_xlsx(json.load(open(sys.argv[1], encoding='utf-8')), sys.argv[2]))
