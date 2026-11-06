"""
Loader for lab/scenarios.yaml (bundled next to this file at synth time).

The YAML is the single source of truth for the Lab: the engine takes handler names
and auto-revert timeouts from it, the API serves it to the frontend unchanged.
"""

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml

SCENARIOS_FILE = os.environ.get('SCENARIOS_FILE', os.path.join(os.path.dirname(__file__), 'scenarios.yaml'))
DEFAULT_AUTO_REVERT_SECONDS = 600


@lru_cache(maxsize=1)
def load() -> Dict[str, Any]:
    with open(SCENARIOS_FILE, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    data.setdefault('scenarios', [])
    data.setdefault('skills', [])
    data.setdefault('notes', [])
    return data


def all_scenarios() -> List[Dict[str, Any]]:
    return load()['scenarios']


def get(scenario_id: str) -> Optional[Dict[str, Any]]:
    return next((s for s in all_scenarios() if s.get('id') == scenario_id), None)


def auto_revert_seconds(scenario: Dict[str, Any]) -> int:
    return int(scenario.get('autoRevertSeconds') or DEFAULT_AUTO_REVERT_SECONDS)


def alarm_name(scenario: Dict[str, Any]) -> str:
    """Alarm names are deployment-specific: the YAML names the env var CDK fills in."""
    env_var = (scenario.get('alarm') or {}).get('envVar')
    return os.environ.get(env_var, '') if env_var else ''
