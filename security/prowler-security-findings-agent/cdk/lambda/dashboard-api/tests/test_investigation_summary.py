"""Live investigation summary (CONTRACT: `summary: {content, updatedAt, final} | null`).

summary_records.json holds sanitized journal records recorded from one real
investigation: four `ui_investigation_summary` snapshots taken while it ran (a
JSON UI tree, re-emitted every few seconds) and the `investigation_summary_md`
and `mitigation_summary_md` records written when it finished.
"""
import pytest

import index
from conftest import AGENT_SPACE_ID, FINDINGS, get, load_fixture

POLICY_UID = FINDINGS[0]['finding_uid']
DEPUTY_UID = FINDINGS[1]['finding_uid']
IDLE_UID = FINDINGS[2]['finding_uid']
EXECUTION_ID = 'exe-ops1-iam-policy-0001'
RECORDED = load_fixture('summary_records.json')
LIVE = RECORDED['ui_investigation_summary']
INVESTIGATION_MD = RECORDED['investigation_summary_md']['records'][0]
MITIGATION_MD = RECORDED['mitigation_summary_md']['records'][0]

MID_RUN_MARKDOWN = """### Prowler CRITICAL finding on AWSSecurityHubServiceRolePolicy `investigating`

#### Incident

Prowler reported a CRITICAL-severity finding on the AWS-managed policy `AWSSecurityHubServiceRolePolicy` in account 111122223333. The finding text says the policy *does not* allow `*:*`.

#### Root cause `Preliminary`

Not a security issue: Prowler PASS result for AWSSecurityHubServiceRolePolicy (no '*:*'), shown as CRITICAL because that is the check's severity, not a finding.

---

#### Current focus

Finding the finding's status some other way: reading the S3 raw report and the DynamoDB findings record were both denied.

#### Plan

- Find the finding's status (PASS or FAIL) from an accessible source
- Confirm how the alert was triggered (`AUTO_INVESTIGATE=false`)"""


def _set_status(wire, task_id, status):
    next(t for t in wire.tasks if t['taskId'] == task_id)['status'] = status


def _summary_queries(wire):
    return [body for op, _, body in wire.calls if op == 'ListJournalRecords' and body.get('recordType')]


@pytest.fixture
def recorded(wire):
    wire.add_records(EXECUTION_ID, 'ui_investigation_summary', list(LIVE.values()))
    wire.add_records(EXECUTION_ID, 'investigation_summary_md', [INVESTIGATION_MD])
    wire.add_records(EXECUTION_ID, 'mitigation_summary_md', [MITIGATION_MD])
    return wire


def test_live_snapshot_renders_the_summary_card_as_markdown():
    assert index._ui_summary_markdown(LIVE['mid']['content']) == MID_RUN_MARKDOWN


def test_first_snapshot_without_summary_card_renders_the_whole_tree():
    assert index._ui_summary_markdown(LIVE['starting']['content']) == (
        '## Investigation starting…\n\n'
        'The investigation is starting. This summary will update as it progresses.'
    )


def test_final_snapshot_keeps_status_badges_and_leaves_out_other_cards():
    markdown = index._ui_summary_markdown(LIVE['final']['content'])

    assert markdown.startswith('### False alarm: PASS Prowler result on AWSSecurityHubServiceRolePolicy '
                               'opened a CRITICAL investigation `complete` `Severity: low`\n\n#### Incident\n\n')
    assert '#### Root cause `Confirmed`' in markdown
    assert '#### Mitigation `Recommended`' in markdown
    assert 'Agent tasks' not in markdown and 'Findings & evidence' not in markdown


@pytest.mark.parametrize('content, expected', [
    ('Plain **markdown** summary', 'Plain **markdown** summary'),
    ('{"type": "ui_investigation_summary"}', ''),
    ('{"content": {"type": "container", "children": [{"type": "title", "props": {"level": "x"}, "text": 5},'
     ' {"type": "title", "props": {"level": "x"}, "text": "Plan"}, {"type": "markdown", "text": "- step"}, "junk"]}}',
     '#### Plan\n\n- step'),
    (None, ''),
])
def test_unexpected_content_shapes(content, expected):
    assert index._ui_summary_markdown(content) == expected


def test_running_investigation_returns_the_newest_live_snapshot(recorded):
    _set_status(recorded, 'task-iam-policy-0001', 'IN_PROGRESS')

    status, body = get(f'/findings/{POLICY_UID}/investigation')

    assert status == 200
    assert body['status'] == 'in_progress'
    assert body['summary'] == {
        'content': index._ui_summary_markdown(LIVE['final']['content']),
        'updatedAt': '2026-11-25T18:52:04.616Z',
        'final': False,
    }
    assert _summary_queries(recorded) == [{
        'executionId': EXECUTION_ID, 'recordType': 'ui_investigation_summary', 'order': 'DESC', 'limit': 1,
    }]


def test_running_investigation_mid_run(recorded):
    _set_status(recorded, 'task-iam-policy-0001', 'IN_PROGRESS')
    recorded.add_records(EXECUTION_ID, 'ui_investigation_summary', [LIVE['starting'], LIVE['early'], LIVE['mid']])

    _, body = get(f'/findings/{POLICY_UID}/investigation')

    assert body['summary'] == {'content': MID_RUN_MARKDOWN, 'updatedAt': '2026-11-25T18:48:35.348Z', 'final': False}


def test_completed_investigation_returns_the_final_root_cause_and_mitigation(recorded):
    status, body = get(f'/findings/{POLICY_UID}/investigation')

    assert status == 200
    assert body['status'] == 'completed'
    assert body['summary'] == {
        'content': INVESTIGATION_MD['content'].strip() + '\n\n' + MITIGATION_MD['content'].strip(),
        'updatedAt': '2026-11-25T18:52:09.711Z',
        'final': True,
    }
    assert body['summary']['content'].startswith('# Investigation Summary\n')
    assert '# Mitigation Summary\n' in body['summary']['content']


def test_completed_without_final_records_falls_back_to_the_last_snapshot(wire):
    wire.add_records(EXECUTION_ID, 'ui_investigation_summary', list(LIVE.values()))

    _, body = get(f'/findings/{POLICY_UID}/investigation')

    assert body['summary']['final'] is False
    assert body['summary']['updatedAt'] == '2026-11-25T18:52:04.616Z'


def test_settled_summary_is_fetched_once(recorded):
    get(f'/findings/{POLICY_UID}/investigation')
    queries = len(_summary_queries(recorded))
    _, body = get(f'/findings/{POLICY_UID}/investigation')

    assert queries == 2
    assert len(_summary_queries(recorded)) == queries
    assert body['summary']['final'] is True


def test_running_summary_is_fetched_on_every_poll(recorded):
    _set_status(recorded, 'task-iam-policy-0001', 'IN_PROGRESS')
    get(f'/findings/{POLICY_UID}/investigation')
    get(f'/findings/{POLICY_UID}/investigation')

    assert len(_summary_queries(recorded)) == 2


def test_summary_is_null_without_summary_records(wire):
    _, body = get(f'/findings/{DEPUTY_UID}/investigation')

    assert body['status'] == 'completed'
    assert body['summary'] is None


def test_summary_is_null_when_nothing_was_dispatched(wire):
    _, body = get(f'/findings/{IDLE_UID}/investigation')

    assert body['status'] == 'idle'
    assert body['summary'] is None
    assert _summary_queries(wire) == []


def test_summary_lookup_failure_keeps_the_rest_of_the_response(recorded):
    recorded.errors['ListJournalRecords'] = (403, {'__type': 'AccessDeniedException', 'message': 'not authorized'})

    status, body = get(f'/findings/{POLICY_UID}/investigation')

    assert status == 200
    assert body['status'] == 'completed'
    assert body['summary'] is None
    assert len(body['tasks']) == 1


def test_investigations_list_carries_a_summary_per_investigation(recorded):
    status, body = get('/investigations')

    assert status == 200
    assert body['agentSpaceId'] == AGENT_SPACE_ID
    by_uid = {i['finding_uid']: i for i in body['investigations']}
    assert by_uid[POLICY_UID]['summary']['final'] is True
    assert by_uid[POLICY_UID]['summary']['updatedAt'] == '2026-11-25T18:52:09.711Z'
    assert by_uid[DEPUTY_UID]['summary'] is None
