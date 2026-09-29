"""
The contract between the shared Lab and a demo's `lab/handlers.py`.

    from handler_contract import Handler

    HANDLERS = {
        'my_failure': Handler(inject=..., revert=..., probe=...),
    }

    def environment() -> list:      # optional: facts shown in the Lab header
        return [fact('Cluster', ...)]

inject()  breaks the environment and returns a dict (a `message` is shown to the presenter).
revert()  puts it back; must be safe to call when nothing is injected.
probe()   reads the LIVE environment and returns {"injected": bool, "facts": [fact, ...]}.
          Never read stored state: a manual fix must show up here.
"""

from typing import Any, Callable, Dict, NamedTuple


class Handler(NamedTuple):
    inject: Callable[[], Dict[str, Any]]
    revert: Callable[[], Dict[str, Any]]
    probe: Callable[[], Dict[str, Any]]
