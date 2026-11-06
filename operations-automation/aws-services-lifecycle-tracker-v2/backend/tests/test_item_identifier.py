"""
Unit tests for the item key derivation in store_deprecation_data().

Regression: the Glue extraction returned
{'identifier': None, 'name': 'Zeppelin notebook', ...}. The key lookup used
item.get('identifier', item.get('name', '')), which returns None when the key
is present with a None value, so the 'name' fallback never ran and the row was
rejected as "Item missing 'identifier'/'name'" on every run that emitted it.

The engine+version fallback had the same flaw: str(None) is 'None', so an item
with engine=None and version=None got the made-up key 'none-None' instead of
being rejected.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database_writes  # noqa: E402

CONFIG = {
    "schema_key": "item",
    "required_fields": ["name", "identifier"],
    "item_properties": {"name": {}, "identifier": {}, "end_of_support_date": {}},
}


def _store(items):
    """Run store_deprecation_data() against a mocked config and table."""
    table = MagicMock()
    with patch.object(database_writes, "get_service_config", return_value=CONFIG), \
            patch.object(database_writes, "lifecycle_table", table):
        result = database_writes.store_deprecation_data("glue", items)
    item_ids = [c.kwargs["Item"]["item_id"] for c in table.put_item.call_args_list]
    return result, item_ids


class TestNullIdentifierFallsBackToName:
    def test_none_identifier_uses_name(self):
        item = {"identifier": None, "name": "Zeppelin notebook", "end_of_support_date": "9/30/2022"}
        result, item_ids = _store([item])
        assert result["errors"] == []
        assert result["stored_count"] == 1
        assert item_ids == ["item#Zeppelin notebook"]

    def test_none_identifier_is_backfilled_from_name(self):
        item = {"identifier": None, "name": "Zeppelin notebook"}
        _store([item])
        assert item["identifier"] == "Zeppelin notebook"

    def test_blank_identifier_uses_name(self):
        result, item_ids = _store([{"identifier": "", "name": "Glue 0.9"}])
        assert result["stored_count"] == 1
        assert item_ids == ["item#Glue 0.9"]

    def test_present_identifier_still_wins_over_name(self):
        _, item_ids = _store([{"identifier": "glue-0.9", "name": "Glue 0.9"}])
        assert item_ids == ["item#glue-0.9"]


class TestNullEngineVersionIsNotAKey:
    def test_none_engine_and_version_are_rejected_not_keyed_none_none(self):
        item = {"identifier": None, "name": None, "engine": None, "version": None}
        result, item_ids = _store([item])
        assert item_ids == []
        assert result["stored_count"] == 0
        assert any("missing 'identifier'/'name'" in e for e in result["errors"])

    def test_engine_and_version_still_derive_a_key(self):
        config = {**CONFIG, "required_fields": ["identifier"]}
        table = MagicMock()
        with patch.object(database_writes, "get_service_config", return_value=config), \
                patch.object(database_writes, "lifecycle_table", table):
            result = database_writes.store_deprecation_data(
                "aurora", [{"identifier": None, "name": None, "engine": "Aurora PostgreSQL", "version": "13.9"}]
            )
        assert result["stored_count"] == 1
        assert table.put_item.call_args.kwargs["Item"]["item_id"] == "item#aurora-postgresql-13.9"
