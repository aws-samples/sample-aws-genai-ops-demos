"""GET /findings/{finding_uid}/investigation reports the newest matching backlog task.

The dashboard keeps polling while status is 'pending' or 'in_progress' and stops on
anything else, so a finished task must never map to either.
"""
import copy

import pytest

from conftest import AGENT_SPACE_ID, FINDINGS, get

DEPUTY_UID = FINDINGS[1]['finding_uid']
TASKS_PATH = f'/backlog/agent-space/{AGENT_SPACE_ID}/tasks/list'
OLD_TASK = 'task-confused-deputy-0002'
OLD_EXECUTION = 'exe-ops1-confused-deputy-0002'


def _task(wire, task_id):
    return next(t for t in wire.tasks if t['taskId'] == task_id)


def _reinvestigate(wire, status, created_at='2026-11-26T09:00:00.5Z', **fields):
    """Add a newer backlog task for the same finding, as a second webhook dispatch creates."""
    task = copy.deepcopy(_task(wire, OLD_TASK))
    task.update({'taskId': 'task-confused-deputy-0004', 'executionId': 'exe-ops1-confused-deputy-0004',
                 'status': status, 'createdAt': created_at, 'updatedAt': created_at, **fields})
    wire.tasks.insert(0, task)
    return task


def test_reinvestigation_reports_the_running_task(wire):
    _reinvestigate(wire, 'IN_PROGRESS')
    wire.journal['exe-ops1-confused-deputy-0004'] = {'records': [
        {'recordType': 'message', 'createdAt': 1795683601.0, 'content': '{"role": "assistant", "content": []}'},
    ]}

    status, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert status == 200
    assert body['status'] == 'in_progress'
    assert body['executionId'] == 'exe-ops1-confused-deputy-0004'
    assert [t['taskId'] for t in body['tasks']] == ['task-confused-deputy-0004', OLD_TASK]
    assert body['journal'][0] == {
        'timestamp': '2026-11-26T09:00:01.000Z', 'type': 'message', 'content': '{"role": "assistant", "content": []}',
    }
    assert len(body['journal']) == 1 + len(wire.journal[OLD_EXECUTION]['records'])


@pytest.mark.parametrize('task_status', ['FAILED', 'CANCELED', 'TIMED_OUT', 'SKIPPED'])
def test_unsuccessful_end_is_terminal(wire, task_status):
    _reinvestigate(wire, task_status)

    _, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert body['status'] == 'failed'
    assert body['tasks'][0]['status'] == task_status


@pytest.mark.parametrize('task_status', ['PENDING_TRIAGE', 'PENDING_START', 'WAITING', 'PENDING_CUSTOMER_APPROVAL'])
def test_queued_or_waiting_task_is_pending(wire, task_status):
    _reinvestigate(wire, task_status)

    _, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert body['status'] == 'pending'


def test_older_completed_task_no_longer_masks_a_newer_one(wire):
    _reinvestigate(wire, 'PENDING_START', executionId=None)

    _, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert body['status'] == 'pending'
    assert body['executionId'] is None


def test_newest_is_decided_by_time_not_by_string_order(wire):
    # The API trims trailing zeros, so '...00Z' sorts after '...00.75Z' as a string but is older.
    _task(wire, OLD_TASK).update(status='FAILED', createdAt='2026-11-26T09:00:00Z')
    _reinvestigate(wire, 'IN_PROGRESS', created_at='2026-11-26T09:00:00.3Z')
    _reinvestigate(wire, 'COMPLETED', created_at='2026-11-26T09:00:00.75Z')
    wire.tasks[0]['taskId'] = 'task-confused-deputy-0005'

    _, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert [t['createdAt'] for t in body['tasks']] == [
        '2026-11-26T09:00:00.75Z', '2026-11-26T09:00:00.3Z', '2026-11-26T09:00:00Z',
    ]
    assert body['status'] == 'completed'


def test_linked_duplicate_reports_its_primary_investigation(wire):
    _task(wire, OLD_TASK)['status'] = 'IN_PROGRESS'
    _reinvestigate(wire, 'LINKED', executionId=None, primaryTaskId=OLD_TASK)

    _, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert body['status'] == 'in_progress'
    assert body['executionId'] == OLD_EXECUTION
    assert body['tasks'][0]['status'] == 'LINKED'
    assert len(body['journal']) == len(wire.journal[OLD_EXECUTION]['records'])


def test_linked_task_whose_primary_is_not_listed_is_finished(wire):
    _reinvestigate(wire, 'LINKED', executionId=None, primaryTaskId='task-not-listed')
    wire.executions['task-confused-deputy-0004'] = {'executions': []}

    _, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert body['status'] == 'completed'


def test_tasks_on_later_pages_are_found(wire):
    wire.task_page_size = 1
    wire.tasks.sort(key=lambda t: t['taskId'] != OLD_TASK)
    _reinvestigate(wire, 'IN_PROGRESS')
    wire.tasks.append(wire.tasks.pop(0))

    status, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert status == 200
    assert body['status'] == 'in_progress'
    assert [t['taskId'] for t in body['tasks']] == ['task-confused-deputy-0004', OLD_TASK]
    pages = [b for op, path, b in wire.calls if op == 'ListBacklogTasks']
    assert pages == [{}] + [{'nextToken': str(i)} for i in range(1, len(wire.tasks))]
    assert all(path == TASKS_PATH for op, path, _ in wire.calls if op == 'ListBacklogTasks')
