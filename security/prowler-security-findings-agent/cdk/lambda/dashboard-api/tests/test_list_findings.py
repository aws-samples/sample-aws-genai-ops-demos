"""GET /findings reads past DynamoDB's 1 MB page limit and returns a cursor."""
import json

import index


class PagedTable:
    """Scan returns at most `page` items per call, like DynamoDB's 1 MB cut-off."""

    def __init__(self, n, page):
        self.items = [{'finding_uid': f'check_{i:04d}:arn', 'severity': 'LOW', 'raw': 'x' * 10} for i in range(n)]
        self.page = page
        self.calls = []

    def scan(self, Limit, ExclusiveStartKey=None):
        self.calls.append((Limit, ExclusiveStartKey))
        start = 0
        if ExclusiveStartKey:
            start = next(i for i, it in enumerate(self.items)
                         if it['finding_uid'] == ExclusiveStartKey['finding_uid']) + 1
        chunk = self.items[start:start + min(Limit, self.page)]
        resp = {'Items': [dict(c) for c in chunk]}
        if start + len(chunk) < len(self.items):
            resp['LastEvaluatedKey'] = {'finding_uid': chunk[-1]['finding_uid']}
        return resp


def _list(qs):
    return json.loads(index._list_findings(qs)['body'])


def test_reads_pages_until_limit(monkeypatch):
    table = PagedTable(n=1200, page=110)
    monkeypatch.setattr(index, 'TABLE', table)
    body = _list({'limit': '500'})
    assert body['count'] == 500
    assert len({i['finding_uid'] for i in body['items']}) == 500
    assert all('raw' not in i for i in body['items'])
    assert body['next']
    assert len(table.calls) == 5


def test_cursor_walks_every_finding_once(monkeypatch):
    monkeypatch.setattr(index, 'TABLE', PagedTable(n=1200, page=110))
    seen, cursor = [], None
    while True:
        qs = {'limit': '500'}
        if cursor:
            qs['next'] = cursor
        body = _list(qs)
        seen += [i['finding_uid'] for i in body['items']]
        cursor = body['next']
        if not cursor:
            break
    assert len(seen) == 1200 == len(set(seen))


def test_small_table_has_no_cursor(monkeypatch):
    monkeypatch.setattr(index, 'TABLE', PagedTable(n=40, page=110))
    body = _list({'limit': '500'})
    assert body['count'] == 40
    assert body['next'] is None


def test_invalid_cursor_is_rejected(monkeypatch):
    monkeypatch.setattr(index, 'TABLE', PagedTable(n=10, page=110))
    resp = index._list_findings({'next': 'not-a-cursor!!'})
    assert resp['statusCode'] == 400
