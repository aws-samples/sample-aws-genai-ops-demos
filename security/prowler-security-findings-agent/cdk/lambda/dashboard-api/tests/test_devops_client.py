"""dashboard-api reads DevOps Agent through boto3's `devops-agent` client.

The fixtures are sanitized wire responses recorded from a real Agent Space. These
tests pin what the previous hand-signed client did: the same REST routes and
request bodies, and response values passed through unchanged (timestamps exactly
as sent on the wire).
"""
from conftest import AGENT_SPACE_ID, FINDINGS, get

POLICY_UID = FINDINGS[0]['finding_uid']
DEPUTY_UID = FINDINGS[1]['finding_uid']
TASKS_PATH = f'/backlog/agent-space/{AGENT_SPACE_ID}/tasks/list'
EXECUTIONS_PATH = f'/journal/agent-space/{AGENT_SPACE_ID}/executions'
JOURNAL_PATH = f'/journal/agent-space/{AGENT_SPACE_ID}/journalRecords'


def test_investigation_uses_the_same_routes_and_passes_values_through(wire):
    status, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert status == 200
    assert body['status'] == 'completed'
    assert body['executionId'] == 'exe-ops1-confused-deputy-0002'
    assert body['tasks'] == [{
        'taskId': 'task-confused-deputy-0002',
        'executionId': 'exe-ops1-confused-deputy-0002',
        'title': 'Prowler finding: IAM service role prevents cross-service confused deputy attack',
        'status': 'COMPLETED',
        'priority': 'HIGH',
        'createdAt': '2026-06-23T14:21:57.522Z',
        'updatedAt': '2026-06-23T14:23:21.32Z',
    }]
    recorded = wire.journal['exe-ops1-confused-deputy-0002']['records']
    assert [r['content'] for r in body['journal']] == [r['content'] for r in recorded]
    assert ('ListBacklogTasks', TASKS_PATH, {}) in wire.calls
    assert ('ListJournalRecords', JOURNAL_PATH, {'executionId': 'exe-ops1-confused-deputy-0002'}) in wire.calls


def test_task_without_execution_id_resolves_it_with_list_executions(wire):
    task = next(t for t in wire.tasks if t['taskId'] == 'task-confused-deputy-0002')
    del task['executionId']

    status, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert status == 200
    assert body['tasks'][0]['executionId'] is None
    assert ('ListExecutions', EXECUTIONS_PATH, {'taskId': 'task-confused-deputy-0002'}) in wire.calls
    assert ('ListJournalRecords', JOURNAL_PATH, {'executionId': 'exe-ops1-confused-deputy-0002'}) in wire.calls
    assert len(body['journal']) == len(wire.journal['exe-ops1-confused-deputy-0002']['records'])


def test_investigations_list_keeps_wire_timestamps(wire):
    status, body = get('/investigations')

    assert status == 200
    assert body['agentSpaceId'] == AGENT_SPACE_ID
    by_uid = {i['finding_uid']: i for i in body['investigations']}
    assert set(by_uid) == {POLICY_UID, DEPUTY_UID}
    assert by_uid[POLICY_UID]['createdAt'] == '2026-11-25T18:46:34.765Z'
    assert by_uid[POLICY_UID]['updatedAt'] == '2026-11-25T18:52:16.974Z'
    assert by_uid[DEPUTY_UID]['updatedAt'] == '2026-06-23T14:23:21.32Z'
    assert [i['finding_uid'] for i in body['investigations']] == [POLICY_UID, DEPUTY_UID]


def test_service_error_is_reported_not_raised(wire):
    wire.errors['ListBacklogTasks'] = (403, {'__type': 'AccessDeniedException', 'message': 'not authorized'})

    status, body = get(f'/findings/{POLICY_UID}/investigation')

    assert status == 200
    assert body['status'] == 'error'
    assert 'AccessDeniedException' in body['error']
    assert body['tasks'] == [] and body['journal'] == []


def test_unconfigured_space_skips_the_api(wire, monkeypatch):
    monkeypatch.setattr('index._DEVOPS_AGENT_SPACE_ID_FALLBACK', '')

    status, body = get(f'/findings/{POLICY_UID}/investigation')

    assert status == 200
    assert body['status'] == 'not_configured'
    assert wire.calls == []
