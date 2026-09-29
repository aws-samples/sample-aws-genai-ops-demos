"""Fake demo handlers for the shared Lab tests: record calls, touch nothing."""

from handler_contract import Handler
from facts import fact

CALLS = []


def _inject():
    CALLS.append('inject')
    return {'message': 'broken'}


def _revert():
    CALLS.append('revert')
    return {'message': 'fixed'}


def _probe():
    injected = 'inject' in CALLS and 'revert' not in CALLS
    return {'injected': injected, 'facts': [fact('Thing', 'broken' if injected else 'fine',
                                                 status='error' if injected else 'success')]}


HANDLERS = {
    'thing_breaks': Handler(_inject, _revert, _probe),
    'other_thing_breaks': Handler(_inject, _revert, _probe),
}


def environment():
    return [fact('Environment', 'fixture')]
