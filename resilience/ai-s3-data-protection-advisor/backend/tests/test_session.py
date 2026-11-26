"""collect/session.for_account: the hub reads with its own session, a member through the assumed spoke role."""
from datetime import datetime, timezone

import boto3
from botocore.stub import Stubber

from s3dp.collect import session as sessions

MEMBER, ROLE = "222233334444", "S3DataProtectionScanRole"


class Hub:
    region_name = "eu-west-1"

    def __init__(self, partition="aws"):
        self.partition, self.sts = partition, boto3.client(
            "sts", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
        self.stub, self.asked = Stubber(self.sts), []

    def client(self, name, region_name=None, **kw):
        self.asked.append((name, region_name))
        return self.sts

    def get_partition_for_region(self, r):
        return self.partition


def test_hub_uses_its_own_session_without_any_call():
    hub = Hub()
    assert sessions.for_account(hub, "111122223333", True, ROLE) == (hub, None) and hub.asked == []


def test_member_assumes_the_spoke_role_in_the_partition_and_regional_sts():
    hub = Hub("aws-cn")
    hub.stub.add_response("assume_role", {"Credentials": {
        "AccessKeyId": "test-key-id-0000", "SecretAccessKey": "test-secret", "SessionToken": "test-token",
        "Expiration": datetime(2026, 11, 26, tzinfo=timezone.utc)}},
        {"RoleArn": f"arn:aws-cn:iam::{MEMBER}:role/{ROLE}", "RoleSessionName": "s3-data-protection-scan",
         "DurationSeconds": 3600})
    hub.stub.activate()
    s, err = sessions.for_account(hub, MEMBER, False, ROLE)
    hub.stub.assert_no_pending_responses()
    assert err is None and isinstance(s, boto3.Session) and s.region_name == "eu-west-1"
    assert hub.asked == [("sts", "eu-west-1")]
    assert s.get_credentials().access_key == "test-key-id-0000"


def test_denied_assume_role_returns_the_code():
    hub = Hub()
    hub.stub.add_client_error("assume_role", "AccessDenied", http_status_code=403)
    hub.stub.activate()
    assert sessions.for_account(hub, MEMBER, False, ROLE) == (None, "AccessDenied")
