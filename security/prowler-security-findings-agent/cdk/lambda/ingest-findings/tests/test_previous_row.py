"""Re-ingesting a finding keeps what other components wrote on its row.

ocsf_finding.json is one sanitized finding from a real Prowler 5.52.0 report.
"""
import copy

import pytest
from botocore.exceptions import ClientError

import index
from conftest import UID, finding

USER_FIELDS = {
    'suppressed_at': '2026-11-25T19:00:00Z',
    'suppress_reason': 'accepted risk, tracked in JIRA-123',
    'suppressed_by': 'arn:aws:sts::111122223333:assumed-role/dashboard-user/alice',
    'remediation_s3_key': 'remediations/example.md',
    'remediation_generated_at': 'c0ffee00-0000-4000-8000-000000000000',
    'remediation_model': 'global.amazon.nova-2-lite-v1:0',
    'triage_owner': 'secops',
}


def test_reingest_keeps_suppression_remediation_and_unknown_fields(ingest):
    ingest.run([finding('FAIL')])
    ingest.table.rows[UID].update(USER_FIELDS)

    ingest.run([finding('PASS')])

    row = ingest.row(UID)
    assert {k: row.get(k) for k in USER_FIELDS} == USER_FIELDS
    assert row['status'] == 'PASS'
    assert [h['status'] for h in row['status_history']] == ['FAIL', 'PASS']


def test_unsuppressed_finding_stays_unsuppressed(ingest):
    ingest.run([finding('FAIL')])
    ingest.table.rows[UID].update(USER_FIELDS)
    for key in ('suppressed_at', 'suppress_reason', 'suppressed_by'):
        del ingest.table.rows[UID][key]

    ingest.run([finding('FAIL')])

    assert 'suppressed_at' not in ingest.row(UID)
    assert ingest.row(UID)['remediation_s3_key'] == USER_FIELDS['remediation_s3_key']


def test_report_fields_are_replaced_not_carried(ingest):
    ingest.run([finding('FAIL')])
    assert ingest.row(UID)['notes']
    stale = finding('PASS', status_detail='now passing')
    del stale['unmapped']['notes']

    ingest.run([stale])

    row = ingest.row(UID)
    assert 'notes' not in row
    assert row['status_extended'] == 'now passing'


def test_first_seen_at_survives_more_than_two_scans(ingest):
    ingest.run([finding('FAIL')])
    first = ingest.row(UID)['first_seen_at']
    ingest.run([finding('FAIL')])
    ingest.run([finding('PASS')])

    assert ingest.row(UID)['first_seen_at'] == first
    assert ingest.row(UID)['last_seen_at'] > first


def test_new_finding_gets_no_carried_fields(ingest):
    ingest.run([finding('FAIL')])

    row = ingest.row(UID)
    assert row['first_seen_at'] == row['last_seen_at']
    assert set(row) <= index.INGEST_FIELDS


def test_every_report_field_is_declared_as_ingest_owned():
    item = index._to_item(finding('FAIL'), '20261125T060000Z', '2026-11-25T06:05:00Z')

    assert set(item) | {'status_history', 'first_seen_at'} <= index.INGEST_FIELDS


def test_duplicate_findings_in_a_report_are_fetched_once(ingest):
    ingest.run([finding('FAIL')])
    ingest.table.rows[UID].update(USER_FIELDS)

    ingest.run([finding('FAIL'), finding('FAIL')])

    assert ingest.table.batch_gets[-1] == [UID]
    assert ingest.row(UID)['suppress_reason'] == USER_FIELDS['suppress_reason']


def test_unprocessed_keys_are_read_again(ingest):
    ingest.run([finding('FAIL')])
    ingest.table.rows[UID].update(USER_FIELDS)
    ingest.table.unprocessed_once = {UID}

    ingest.run([finding('PASS')])

    assert ingest.table.batch_gets[-2:] == [[UID], [UID]]
    assert ingest.row(UID)['suppressed_at'] == USER_FIELDS['suppressed_at']


def test_unreadable_previous_rows_fail_before_anything_is_written(ingest):
    ingest.run([finding('FAIL')])
    ingest.table.rows[UID].update(USER_FIELDS)
    before = copy.deepcopy(ingest.table.rows)
    puts = len(ingest.table.puts)
    ingest.table.fail_batch_get = ClientError(
        {'Error': {'Code': 'ProvisionedThroughputExceededException', 'Message': 'slow down'}}, 'BatchGetItem')

    with pytest.raises(ClientError):
        ingest.run([finding('PASS')])

    assert ingest.table.rows == before
    assert len(ingest.table.puts) == puts


def test_keys_that_stay_unprocessed_fail_the_ingest(ingest):
    ingest.run([finding('FAIL')])

    class AlwaysUnprocessed(set):
        def __contains__(self, uid):
            return True

        def __isub__(self, other):
            return self

    ingest.table.unprocessed_once = AlwaysUnprocessed()

    with pytest.raises(RuntimeError, match='1 previous rows unread'):
        ingest.run([finding('PASS')])
    assert len(ingest.table.batch_gets) == 1 + index.BATCH_GET_ATTEMPTS


def test_more_than_100_findings_are_fetched_in_chunks(ingest):
    many = []
    for n in range(150):
        f = finding('FAIL')
        f['resources'] = [dict(f['resources'][0], uid=f'arn:aws:iam::111122223333:policy/example-{n}')]
        many.append(f)
    ingest.run(many)
    for row in ingest.table.rows.values():
        row['suppressed_at'] = '2026-11-25T19:00:00Z'

    ingest.run(many)

    assert [len(uids) for uids in ingest.table.batch_gets[-2:]] == [100, 50]
    assert all(row.get('suppressed_at') for row in ingest.table.rows.values())
