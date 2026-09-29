"""
Validate a demo's Lab definitions before the first deploy.

    python shared/lab/lambda/validate.py <demo>/lab

Loads <demo>/lab/scenarios.yaml and <demo>/lab/handlers.py and reports every problem
the Lab would otherwise hit at runtime: a `handler` with no registry entry, an unquoted
walkthrough line (a "text: more" list item parses as a mapping and crashes the card),
an alarm-driven scenario with no `alarm.envVar`, duplicate ids, a scenario without its
with/without-capability statement (hard rule 3), a missing auto-revert.

Exit code 0 when clean, 1 with the list of problems.
"""

import importlib
import os
import re
import sys
import time
from typing import Any, Dict, List

ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')
LIST_KEYS = ('incidentChain', 'customerImpact', 'demoFlow')
STATUSES = {'success', 'error', 'warning', 'pending', 'stopped', 'in-progress', 'info', 'loading'}


def problems(data: Dict[str, Any], handlers_module) -> List[str]:
    out: List[str] = []
    registry = getattr(handlers_module, 'HANDLERS', None)
    if not isinstance(registry, dict):
        out.append('handlers.py: no HANDLERS dict')
        registry = {}
    for name, h in registry.items():
        for fn in ('inject', 'revert', 'probe'):
            if not callable(getattr(h, fn, None)):
                out.append(f'handlers.py: HANDLERS[{name!r}] has no callable {fn}()')

    if data.get('schemaVersion') != 1:
        out.append('schemaVersion must be 1')
    cap = data.get('capability') or {}
    if cap.get('source') not in ('inline', 'agent-tools'):
        out.append("capability.source must be 'inline' or 'agent-tools'")
    if cap.get('source') == 'agent-tools' and not cap.get('ref'):
        out.append('capability.ref is required when source is agent-tools')

    seen = set()
    for s in data.get('scenarios') or []:
        sid = s.get('id', '<no id>')
        tag = f'scenario {sid}'
        if not ID_RE.match(str(sid)):
            out.append(f'{tag}: id must match [a-z0-9-] (it is also the route)')
        if sid in seen:
            out.append(f'{tag}: duplicate id')
        seen.add(sid)
        if len(f'{sid}-{int(time.time())}') > 64:
            out.append(f'{tag}: id too long for a durable execution name (max 64 with the timestamp)')
        if s.get('handler') not in registry:
            out.append(f"{tag}: unknown handler {s.get('handler')!r} (not in HANDLERS)")
        dem = s.get('demonstrates') or {}
        for k in ('check', 'withCapability', 'withoutCapability'):
            if not dem.get(k):
                out.append(f'{tag}: demonstrates.{k} is required (hard rule 3: no difference, no scenario)')
        if s.get('triggersAlarm') and not (s.get('alarm') or {}).get('envVar'):
            out.append(f'{tag}: triggersAlarm needs alarm.envVar')
        if not isinstance(s.get('autoRevertSeconds'), int) or s['autoRevertSeconds'] <= 0:
            out.append(f'{tag}: autoRevertSeconds must be a positive integer (hard rule 4)')
        for key in LIST_KEYS:
            for line in s.get(key) or []:
                if not isinstance(line, str):
                    out.append(f'{tag}: {key} has a non-string line, quote it in the YAML -> {line!r}')

    for sk in data.get('skills') or []:
        src = sk.get('source', 'inline')
        if src == 'inline' and not (sk.get('description') and sk.get('instructions')):
            out.append(f"skill {sk.get('name')}: inline skills need description and instructions")
        if src == 'agent-tools' and not sk.get('ref'):
            out.append(f"skill {sk.get('name')}: agent-tools skills need a ref")
    return out


def main(lab_dir: str) -> int:
    lab_dir = os.path.abspath(lab_dir)
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (here, lab_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ['SCENARIOS_FILE'] = os.path.join(lab_dir, 'scenarios.yaml')
    import scenarios  # noqa: E402  (shared loader, reads SCENARIOS_FILE)
    try:
        handlers_module = importlib.import_module('handlers')
    except Exception as e:  # import errors are problems too
        print(f'handlers.py: cannot import ({e})')
        return 1
    found = problems(scenarios.load(), handlers_module)
    if found:
        print('\n'.join(found))
        return 1
    print(f"OK: {len(scenarios.all_scenarios())} scenario(s), {len(handlers_module.HANDLERS)} handler(s)")
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
