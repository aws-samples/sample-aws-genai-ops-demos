"""Self-contained HTML export. Report data is embedded as JSON and rendered with textContent only."""
import html
import json

from s3dp.model import ai_notice

CSS = """body{font:14px/1.45 system-ui,sans-serif;margin:0;color:#16191f}header{background:#232f3e;color:#fff;padding:12px 20px}
nav{display:flex;gap:4px;padding:8px 20px;border-bottom:1px solid #e9ebed;flex-wrap:wrap}nav button{border:0;background:none;
padding:6px 10px;cursor:pointer;border-radius:4px}nav button[aria-selected=true]{background:#e9ebed;font-weight:600}
main{padding:16px 20px}.alert{background:#f2f8fd;border-left:4px solid #0972d3;padding:8px 12px;margin:8px 20px}
table{border-collapse:collapse;width:100%;margin:8px 0}th,td{border-bottom:1px solid #e9ebed;padding:6px 8px;text-align:left;
vertical-align:top}th{background:#fafafa}.HIGH,.unprotected{color:#d91515;font-weight:600}.MEDIUM,.partial{color:#8d6605}
.protected,.protected_governance,.protected_lock{color:#037f0c}.unknown{color:#5f6b7a}"""

SCRIPT = r"""
const D = JSON.parse(document.getElementById('report-data').textContent);
const el = (t, txt, cls) => { const e = document.createElement(t); if (txt != null) e.textContent = String(txt); if (cls) e.className = cls; return e; };
const COV = {protected:'Protected', protected_governance:'Protected (Governance)', protected_lock:'Protected (per-object)', likely:'Likely protected', partial:'Partial', unprotected:'Unprotected', unknown:'Unconfirmed'};
function table(cols, rows) {
  const t = el('table'), h = t.createTHead().insertRow();
  cols.forEach(c => h.appendChild(el('th', c[0])));
  const b = t.createTBody();
  rows.forEach(r => { const tr = b.insertRow(); cols.forEach(c => { const v = c[1](r); tr.appendChild(el('td', v, c[2] ? c[2](r) : '')); }); });
  return t;
}
const R = {
  instructions: t => { const f = document.createDocumentFragment(); (t.sections||[]).forEach(s => { f.appendChild(el('h3', s.title));
      (s.items||[]).forEach(i => f.appendChild(el('p', i)));
      if (s.table) f.appendChild(table(s.table.columns.map((c, i) => [c, r => r[i]]), s.table.rows));
      if (s.note) f.appendChild(el('p', s.note)); }); return f; },
  cards: t => table([['Priority', r => r.row.tier + ' (' + r.row.score + ')'], ['Bucket', r => r.row.bucket], ['Account', r => r.row.account_id],
      ['Region', r => r.row.region], ['Role', r => r.row.role], ['Size', r => r.row.size_human],
      ['Human error', r => COV[r.row.he], r => r.row.he], ['Malicious activity', r => COV[r.row.ma], r => r.row.ma],
      ['Regional disruption', r => COV[r.row.rd], r => r.row.rd], ['Status', r => r.row.status], ['Recommended action', r => r.row.action]], t.cards),
  'operational-review': t => { const f = document.createDocumentFragment(); (t.resources||[]).forEach(res => { f.appendChild(el('h3', res.name));
      res.pillars.forEach(p => { f.appendChild(el('h4', p.name + ' ' + p.pass + '/' + p.total));
        f.appendChild(table([['Check', c => c.label], ['Severity', c => c.severity || '', c => c.severity || ''], ['Detail', c => c.description]], p.checks)); }); }); return f; },
  recommendations: t => table([['Severity', r => r.severity, r => r.severity], ['Account', r => r.account], ['Bucket', r => r.bucket], ['Size', r => r.size_human || ''],
      ['Recommendation', r => r.recommendation], ['Description', r => r.description || '']], t.items),
  inconsistencies: t => table([['Severity', r => r.severity, r => r.severity], ['Family', r => r.family], ['Setting', r => r.setting],
      ['Members', r => r.members.map(m => m.bucket + ' (' + m.region + '): ' + m.value).join('; ')], ['Recommendation', r => r.recommendation]], t.items),
  governance: t => { const f = document.createDocumentFragment(); f.appendChild(el('p', t.summary));
      f.appendChild(table([['Dimension', d => d.name], ['Level', d => d.level == null ? 'Not assessed' : d.level], ['Current', d => d.current], ['Next level', d => d.next]], t.dimensions));
      f.appendChild(table([['Rule', p => p.rule], ['Compliant', p => p.total ? p.compliant + ' of ' + p.total : (p.note || '')], ['Failing', p => p.failing.join(', ')]], t.policies));
      f.appendChild(table([['Severity', x => x.severity, x => x.severity], ['Finding', x => x.title], ['Improvement', x => x.improvement]], t.findings)); return f; },
  'cost-optimization': t => table([['Opportunity', r => r.title], ['Savings (estimate)', r => r.savings], ['Detail', r => r.description]], t.items),
};
const nav = document.querySelector('nav'), main = document.querySelector('main');
function show(t) { main.replaceChildren(el('h2', t.label), (R[t.type] || (() => el('p', 'No renderer')))(t));
  nav.querySelectorAll('button').forEach(b => b.setAttribute('aria-selected', b.dataset.id === t.id)); }
D.tabs.forEach(t => { const b = el('button', t.label); b.dataset.id = t.id; b.onclick = () => show(t); nav.appendChild(b); });
show(D.tabs[0]);
"""


def render(data):
    meta = data.get("meta", {})
    title = f"S3 data protection report: {meta.get('customerName', '')} ({str(meta.get('generated', ''))[:10]})"
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
            f"<header>{html.escape(title)}</header>"
            f"<div class=\"alert\">{html.escape(ai_notice(data))}</div>"
            f"<nav></nav><main></main>"
            f"<script type=\"application/json\" id=\"report-data\">{payload}</script>"
            f"<script>{SCRIPT}</script></body></html>")
