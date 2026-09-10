"""
Unit tests for the data-ownership boundaries (issue #116) and the
lifecycle-join discovery (issue #99 I1/I2).

Covers:
- LifecycleIndex matching (exact, prefix, provenance exclusion, no-match)
- build_inventory_item unified row shape and unknown fallback
- RDS candidate generation (engine aliases, SQL Server years, Oracle Nc)
- save_to_dynamodb reconciliation (upsert own rows, delete only stale
  provenance-tagged rows, never touch extraction rows)
- update_service_config rejecting runtime-state fields
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import account_discovery as ad


def _index_with_rows(rows):
    """LifecycleIndex wired to a mocked DynamoDB table returning rows."""
    index = ad.LifecycleIndex.__new__(ad.LifecycleIndex)
    index._cache = {}
    index._table = MagicMock()
    index._table.query.return_value = {"Items": rows}
    return index


EKS_ROWS = [
    {"service_name": "eks", "item_id": "versions#1.31", "status": "extended_support",
     "service_specific": {"identifier": "1.31", "version": "1.31", "name": "Kubernetes 1.31",
                          "end_of_standard_support_date": "November 26, 2025"}},
    {"service_name": "eks", "item_id": "versions#1.34", "status": "supported",
     "service_specific": {"identifier": "1.34", "version": "1.34", "name": "Kubernetes 1.34"}},
    # Inventory row (discovery's own output) - must never act as lifecycle truth
    {"service_name": "eks", "item_id": "inventory#k8s-1.29", "status": "unknown",
     "provenance": "account_discovery", "service_specific": {"identifier": "k8s-1.29"}},
]


class TestLifecycleIndex:
    def test_exact_match(self):
        index = _index_with_rows(EKS_ROWS)
        match = index.lookup("eks", ["1.31", "k8s-1.31"])
        assert match and match["status"] == "extended_support"
        assert match["item_id"] == "versions#1.31"

    def test_provenance_rows_are_excluded(self):
        index = _index_with_rows(EKS_ROWS)
        assert index.lookup("eks", ["1.29", "k8s-1.29"]) is None

    def test_no_match_returns_none(self):
        index = _index_with_rows(EKS_ROWS)
        assert index.lookup("eks", ["9.99"]) is None

    def test_prefix_match(self):
        rows = [{"service_name": "rds", "item_id": "engine_versions#mysql-8.0.35",
                 "status": "supported",
                 "service_specific": {"identifier": "mysql-8.0.35", "version": "8.0.35"}}]
        index = _index_with_rows(rows)
        # Discovery only knows the major version; the row is more specific
        match = index.lookup("rds", ["mysql-8.0"])
        assert match and match["item_id"] == "engine_versions#mysql-8.0.35"

    def test_short_candidates_do_not_prefix_match(self):
        rows = [{"service_name": "glue", "item_id": "versions#12345",
                 "status": "deprecated", "service_specific": {"identifier": "12345"}}]
        index = _index_with_rows(rows)
        assert index.lookup("glue", ["1"]) is None

    def test_query_failure_yields_empty_index(self):
        index = ad.LifecycleIndex.__new__(ad.LifecycleIndex)
        index._cache = {}
        index._table = MagicMock()
        index._table.query.side_effect = Exception("boom")
        assert index.lookup("eks", ["1.31"]) is None


class TestBuildInventoryItem:
    def test_unified_shape_with_match(self):
        index = _index_with_rows(EKS_ROWS)
        item = ad.build_inventory_item(
            service_key="eks", identifier="k8s-1.31", display_name="Kubernetes 1.31",
            candidates=["1.31"], affected_resources="cluster-a", total_affected=1,
            source_url="https://example", index=index)
        assert item["service_name"] == "eks"                      # config key, not display name
        assert item["item_id"] == "inventory#k8s-1.31"            # inventory-prefixed
        assert item["status"] == "extended_support"               # joined verdict
        assert item["service_specific"]["matched_lifecycle_item"] == "versions#1.31"

    def test_unknown_fallback_when_unmatched(self):
        index = _index_with_rows(EKS_ROWS)
        item = ad.build_inventory_item(
            service_key="eks", identifier="k8s-9.99", display_name="Kubernetes 9.99",
            candidates=["9.99"], affected_resources="x", total_affected=1,
            source_url="https://example", index=index)
        assert item["status"] == "unknown"
        assert item["service_specific"]["deprecation_date"] == "N/A"

    def test_custom_fallback_status_for_ec2(self):
        index = _index_with_rows([])
        item = ad.build_inventory_item(
            service_key="ec2", identifier="m1", display_name="EC2 M1 Instance Family",
            candidates=["m1"], affected_resources="i-123", total_affected=1,
            source_url="https://example", index=index, fallback_status="deprecated")
        assert item["status"] == "deprecated"


class TestRdsCandidates:
    def test_postgres_alias(self):
        candidates = ad._rds_match_candidates("postgres", "17.6")
        assert "postgresql-17.6" in candidates and "postgresql-17" in candidates

    def test_sqlserver_year_mapping(self):
        candidates = ad._rds_match_candidates("sqlserver-se", "15.00.4430.1")
        assert candidates[0] == "sqlserver-2019"

    def test_oracle_c_suffix(self):
        candidates = ad._rds_match_candidates("oracle-ee", "19.0.0.0")
        assert "oracle-19c" in candidates


class _FakeBatch:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def put_item(self, Item):
        self.sink["puts"].append(Item)

    def delete_item(self, Key):
        self.sink["deletes"].append(Key)


class TestSaveReconciliation:
    def test_upserts_tagged_and_deletes_only_stale_rows_in_scope(self):
        sink = {"puts": [], "deletes": []}
        table = MagicMock()
        table.batch_writer.side_effect = lambda: _FakeBatch(sink)
        table.query.return_value = {"Items": [
            # Stale inventory row: older run - must be deleted
            {"service_name": "eks", "item_id": "inventory#k8s-1.25",
             "discovery_run_id": "run-1"},
            # Fresh inventory row: written by this run - must survive
            {"service_name": "eks", "item_id": "inventory#k8s-1.31",
             "discovery_run_id": "run-2"},
        ]}
        items = [{"service_name": "eks", "item_id": "inventory#k8s-1.31", "status": "supported"}]

        with patch.object(ad.boto3, "resource") as mock_resource:
            mock_resource.return_value.Table.return_value = table
            result = ad.save_to_dynamodb(items, run_id="run-2", scanned_services=["eks"])

        assert result["success"] is True
        assert result["items_saved"] == 1
        assert result["stale_removed"] == 1
        # Upserted item carries the provenance tag and this run's id
        assert sink["puts"][0]["provenance"] == "account_discovery"
        assert sink["puts"][0]["discovery_run_id"] == "run-2"
        # Only the stale row from the older run was deleted
        assert sink["deletes"] == [{"service_name": "eks", "item_id": "inventory#k8s-1.25"}]
        # Writes default to the dedicated inventory table
        mock_resource.return_value.Table.assert_called_with("aws-account-inventory")

    def test_failed_scanner_scope_is_left_untouched(self):
        """A scanner that failed must keep its previous inventory: only the
        successfully scanned services' scopes are reconciled."""
        sink = {"puts": [], "deletes": []}
        table = MagicMock()
        table.batch_writer.side_effect = lambda: _FakeBatch(sink)
        table.query.return_value = {"Items": []}
        items = [{"service_name": "lambda", "item_id": "inventory#python3.8"}]

        with patch.object(ad.boto3, "resource") as mock_resource:
            mock_resource.return_value.Table.return_value = table
            result = ad.save_to_dynamodb(items, run_id="r1", scanned_services=["lambda"])

        assert result["success"] is True
        # Reconciliation queried ONLY the lambda scope - a failed eks scanner's
        # rows are never even looked at, let alone deleted
        assert table.query.call_count == 1
        assert table.query.call_args[1]["ExpressionAttributeValues"] == {":s": "lambda"}
        assert sink["deletes"] == []


class TestConfigWriteGuard:
    def test_runtime_state_fields_rejected(self):
        import database_writes
        result = database_writes.update_service_config("eks", {"extraction_count": 0})
        assert "error" in result
        assert "runtime state" in result["error"]

    def test_config_fields_accepted(self):
        import database_writes
        with patch.object(database_writes, "config_table") as mock_table:
            result = database_writes.update_service_config("eks", {"enabled": False})
        assert result == {"success": True}
        mock_table.update_item.assert_called_once()
