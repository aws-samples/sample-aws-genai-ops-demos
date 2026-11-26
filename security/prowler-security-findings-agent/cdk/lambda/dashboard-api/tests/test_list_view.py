"""GET /findings?view=list: one parallel scan of slim items, gzipped when the browser accepts it."""
import base64
import gzip
import json
import threading

from boto3.dynamodb.types import TypeSerializer

import index

_ser = TypeSerializer()


class FakeClient:
    """Low-level DynamoDB scan: honours Segment/TotalSegments, projection, and pages of `page` items."""

    def __init__(self, items, page):
        self.items, self.page = items, page
        self.calls = []
        self.lock = threading.Lock()

    def scan(self, TableName, Segment, TotalSegments, ProjectionExpression, ExpressionAttributeNames,
             ExclusiveStartKey=None):
        with self.lock:
            self.calls.append((Segment, ExclusiveStartKey))
        fields = [ExpressionAttributeNames[n.strip()] for n in ProjectionExpression.split(',')]
        mine = [it for i, it in enumerate(self.items) if i % TotalSegments == Segment]
        start = int(ExclusiveStartKey['finding_uid']['S'].split('_')[1]) + 1 if ExclusiveStartKey else 0
        start = next((j for j, it in enumerate(mine) if int(it['finding_uid'].split('_')[1]) >= start), len(mine))
        chunk = mine[start:start + self.page]
        resp = {'Items': [{k: _ser.serialize(v) for k, v in it.items() if k in fields} for it in chunk]}
        if start + len(chunk) < len(mine):
            resp['LastEvaluatedKey'] = {'finding_uid': {'S': chunk[-1]['finding_uid']}}
        return resp


class FakeTable:
    def __init__(self, client):
        self.name = 'prowler-security-findings'
        self.meta = type('Meta', (), {'client': client})()


def _items(n):
    return [{
        'finding_uid': f'uid_{i:05d}', 'check_id': 'c', 'severity': 'HIGH', 'status': 'FAIL',
        'status_history': [{'scan_id': 's1', 'status': 'FAIL', 'last_seen_at': 't'}],
        'compliance_frameworks': ['CIS-2.0'],
        'remediation_guidance': 'x' * 300, 'risk_details': 'y' * 300, 'raw': 'z' * 1000,
    } for i in range(n)]


def _event(qs, gzip_ok=True):
    headers = {'accept-encoding': 'gzip, deflate, br'} if gzip_ok else {}
    return {'requestContext': {'http': {'method': 'GET', 'path': '/findings'}},
            'queryStringParameters': qs, 'headers': headers}


def _body(resp):
    raw = base64.b64decode(resp['body']) if resp.get('isBase64Encoded') else resp['body'].encode()
    if resp['headers'].get('content-encoding') == 'gzip':
        raw = gzip.decompress(raw)
    return json.loads(raw)


def test_list_view_returns_every_finding_slim(monkeypatch):
    client = FakeClient(_items(2345), page=37)
    monkeypatch.setattr(index, 'TABLE', FakeTable(client))
    resp = index.handler(_event({'view': 'list'}), None)
    body = _body(resp)
    assert resp['statusCode'] == 200
    assert body['count'] == 2345 and body['next'] is None
    assert len({i['finding_uid'] for i in body['items']}) == 2345
    item = body['items'][0]
    assert set(item) <= set(index._LIST_VIEW_FIELDS)
    assert item['status_history'][0]['status'] == 'FAIL' and item['compliance_frameworks'] == ['CIS-2.0']
    assert {seg for seg, _ in client.calls} == set(range(index._SCAN_SEGMENTS))


def test_list_view_is_gzipped_only_when_accepted(monkeypatch):
    monkeypatch.setattr(index, 'TABLE', FakeTable(FakeClient(_items(500), page=100)))
    zipped = index.handler(_event({'view': 'list'}), None)
    assert zipped['isBase64Encoded'] and zipped['headers']['content-encoding'] == 'gzip'
    plain = index.handler(_event({'view': 'list'}, gzip_ok=False), None)
    assert not plain.get('isBase64Encoded') and 'content-encoding' not in plain['headers']
    assert _body(zipped) == _body(plain)


def test_small_responses_are_not_gzipped():
    resp = index._maybe_gzip(_event({}), index._json(200, {'ok': True}))
    assert 'content-encoding' not in resp['headers']


def test_filters_keep_the_paged_path(monkeypatch):
    called = {}
    monkeypatch.setattr(index, '_list_all_findings_slim', lambda: called.setdefault('slim', True))

    class Paged:
        def query(self, **kw):
            return {'Items': [{'finding_uid': 'a', 'severity': 'HIGH'}]}

    monkeypatch.setattr(index, 'TABLE', Paged())
    body = json.loads(index._list_findings({'view': 'list', 'severity': 'HIGH'})['body'])
    assert body['count'] == 1 and 'slim' not in called
