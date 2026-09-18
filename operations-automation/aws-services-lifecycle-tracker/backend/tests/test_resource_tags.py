"""User tags on scanned resources (#164): fetch, join, best-effort status, row budget."""
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import account_discovery as ad

FN_ARN = "arn:aws:lambda:eu-central-1:123456789012:function:old-fn"
DB_ARN = "arn:aws:rds:eu-central-1:123456789012:db:lt-postgres-14"


class _Paginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **_):
        return iter(self.pages)


class FakeTagging:
    def __init__(self, pages):
        self.pages = pages

    def get_paginator(self, name):
        assert name == "get_resources"
        return _Paginator(self.pages)


class FailingTagging:
    def get_paginator(self, name):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "nope"}}, name)


class _Session:
    """Fake boto3 session: .client() hands back the fake tagging client."""
    def __init__(self, client):
        self._client = client

    def client(self, name, region_name=None):
        assert name == "resourcegroupstaggingapi"
        return self._client


PAGES = [
    {"ResourceTagMappingList": [
        {"ResourceARN": FN_ARN, "Tags": [
            {"Key": "BU", "Value": "LOB1"},
            {"Key": "aws:cloudformation:stack-name", "Value": "noise"},
            {"Key": "AWS:createdBy", "Value": "noise-too"},
        ]},
        {"ResourceARN": "arn:aws:s3:::only-system-tags", "Tags": [
            {"Key": "aws:cloudformation:stack-id", "Value": "x"},
        ]},
    ]},
    {"ResourceTagMappingList": [
        {"ResourceARN": DB_ARN, "Tags": [{"Key": "BU", "Value": "LOB2"}, {"Key": "Team", "Value": "data"}]},
    ]},
]


class TestFetchUserTags:
    def test_drops_aws_prefixed_keys_and_untagged_resources(self):
        tags = ad.fetch_user_tags("eu-central-1", client=FakeTagging(PAGES))
        assert tags == {FN_ARN: {"BU": "LOB1"}, DB_ARN: {"BU": "LOB2", "Team": "data"}}

    def test_uses_the_session_of_the_scanned_account(self):
        tags = ad.fetch_user_tags("eu-central-1", session=_Session(FakeTagging(PAGES)))
        assert FN_ARN in tags


def _items():
    return [
        {"service_name": "lambda", "account_id": "123456789012", "region": "eu-central-1",
         "service_specific": {"affected_resource_details": [
             {"name": "old-fn", "arn": FN_ARN},
             {"name": "no-arn"},
             {"name": "stale", "arn": "arn:aws:lambda:eu-central-1:123456789012:function:stale", "tags": {"BU": "old"}},
         ]}},
        {"service_name": "rds", "account_id": "123456789012", "region": "eu-central-1",
         "service_specific": {"affected_resource_details": [{"name": "lt-postgres-14", "arn": DB_ARN}]}},
        {"service_name": "eks", "service_specific": {}},  # legacy row without details
    ]


class TestApplyTags:
    def test_joins_by_arn_and_clears_stale_tags(self):
        items = _items()
        stats = ad.apply_tags(items, ad.fetch_user_tags("eu-central-1", client=FakeTagging(PAGES)))
        details = items[0]["service_specific"]["affected_resource_details"]
        assert details[0]["tags"] == {"BU": "LOB1"}
        assert "tags" not in details[1]          # no ARN: not tagged
        assert "tags" not in details[2]          # previous run's tags do not survive
        assert items[1]["service_specific"]["affected_resource_details"][0]["tags"] == {"BU": "LOB2", "Team": "data"}
        assert stats == {"resources": 4, "tagged": 2, "keys": {"BU": 2, "Team": 1}}


class TestCollectResourceTags:
    def test_best_effort_per_account_and_status_row(self):
        items = _items()
        saved = {}
        sessions = {"123456789012": None, "999999999999": _Session(FailingTagging())}
        with patch.object(ad, "_caller_identity", return_value={"partition": "aws", "account": "123456789012"}), \
             patch.object(ad, "session_for_account", side_effect=lambda a, r=None: sessions[a]), \
             patch.object(ad.boto3, "client", return_value=FakeTagging(PAGES)), \
             patch.object(ad, "_save_control_row", side_effect=lambda k, s: saved.setdefault(k, s)):
            status = ad.collect_resource_tags(items, scanned_scopes=[
                {"account_id": "123456789012", "region": "eu-central-1", "service_keys": ["lambda"]},
                {"account_id": "999999999999", "region": "eu-central-1", "service_keys": ["lambda"]},
            ])
        assert status["available"] is True
        assert status["tagged"] == 2 and status["resources"] == 4
        assert list(status["keys"]) == ["BU", "Team"]          # ranked by coverage
        assert status["by_account"]["123456789012"]["available"] is True
        assert status["by_account"]["999999999999"]["available"] is False
        assert "AccessDeniedException" in status["by_account"]["999999999999"]["reason"]
        assert ad.TAGS_STATUS_KEY in saved
        # the hub's resources are tagged even though the spoke failed
        assert items[0]["service_specific"]["affected_resource_details"][0]["tags"] == {"BU": "LOB1"}


class TestRowBudget:
    def test_small_row_untouched(self):
        item = {"service_name": "lambda", "item_id": "x", "service_specific": {"affected_resource_details": [{"name": "a"}], "affected_resource_names": ["a"]}}
        before = json.dumps(item)
        ad.fit_row_to_budget(item)
        assert json.dumps(item) == before

    def test_oversized_row_is_trimmed_and_flagged(self):
        details = [{"name": f"fn-{i}", "arn": f"arn:aws:lambda:eu-central-1:123456789012:function:fn-{i}",
                    "tags": {"BU": "LOB1", "Note": "x" * 200}} for i in range(500)]
        item = {"service_name": "lambda", "item_id": "x",
                "service_specific": {"affected_resource_details": details, "affected_resource_names": [d["name"] for d in details],
                                     "total_affected": 500}}
        ad.fit_row_to_budget(item, budget=60_000)
        ss = item["service_specific"]
        assert ad._row_bytes(item) <= 60_000
        assert ss["details_truncated"] is True
        assert ss["details_stored"] == len(ss["affected_resource_details"]) == len(ss["affected_resource_names"])
        assert 0 < ss["details_stored"] < 500
        assert ss["total_affected"] == 500                    # the count stays exact

    def test_save_applies_the_budget(self):
        sink = {"puts": [], "deletes": []}

        class _Batch:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def put_item(self, Item): sink["puts"].append(Item)
            def delete_item(self, Key): sink["deletes"].append(Key)

        table = MagicMock()
        table.batch_writer.side_effect = lambda: _Batch()
        table.query.return_value = {"Items": []}
        details = [{"name": f"fn-{i}", "tags": {"Note": "x" * 900}} for i in range(500)]
        item = {"service_name": "lambda", "item_id": "inventory#x",
                "service_specific": {"affected_resource_details": details, "affected_resource_names": [d["name"] for d in details], "total_affected": 500}}
        with patch.object(ad.boto3, "resource") as mock_resource:
            mock_resource.return_value.Table.return_value = table
            result = ad.save_to_dynamodb([item], run_id="r1", scanned_services=["lambda"])
        assert result["success"] is True
        stored = sink["puts"][0]["service_specific"]
        assert stored["details_truncated"] is True
        assert ad._row_bytes(sink["puts"][0]) <= ad.ROW_BYTE_BUDGET
