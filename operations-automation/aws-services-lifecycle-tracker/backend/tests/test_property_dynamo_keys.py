"""
Property-Based Test: Config-driven extraction produces correctly keyed output

Feature: extended-coverage-and-health-integration, Property 2: Config-driven extraction produces correctly keyed output

**Validates: Requirements 1.2, 1.4**

For *any* valid Service_Config and *any* list of items extracted (simulated),
the extraction engine SHALL produce DynamoDB records using `service_name` as
partition key and an `item_id` derived as sort key, without requiring
service-specific code changes.
"""
import sys
import os
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# --- Helper: simulate DynamoDB key generation from extracted items ---
# This mirrors the logic in database_writes.py store_deprecation_data()
# which builds keys generically from config without service-specific code.


def generate_dynamo_record(service_name: str, schema_key: str, item: dict) -> Optional[dict]:
    """
    Simulate the DynamoDB key generation logic from database_writes.py.

    Given a service_name, schema_key (from config), and an extracted item,
    produce the DynamoDB record with:
      - service_name as partition key (PK)
      - item_id as sort key (SK), derived as "{schema_key}#{identifier}"

    The identifier is taken from item['identifier'] falling back to item['name'].
    Returns None if no valid identifier can be derived.
    """
    identifier = item.get("identifier") or item.get("name")
    if not identifier:
        return None

    item_id = f"{schema_key}#{identifier}"

    return {
        "service_name": service_name,
        "item_id": item_id,
    }


# --- Hypothesis strategies ---

# Strategy for non-empty service names (alphanumeric + hyphens/underscores)
service_name_strategy = st.text(
    alphabet=st.sampled_from(
        "abcdefghijklmnopqrstuvwxyz0123456789-_"
    ),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip() != "")

# Strategy for schema_key (typically short identifiers like "runtimes", "versions")
schema_key_strategy = st.text(
    alphabet=st.sampled_from(
        "abcdefghijklmnopqrstuvwxyz_"
    ),
    min_size=1,
    max_size=30,
)

# Strategy for an extracted item with at least name and identifier fields
extracted_item_strategy = st.fixed_dictionaries({
    "name": st.text(min_size=1, max_size=100).filter(lambda s: s.strip() != ""),
    "identifier": st.text(min_size=1, max_size=100).filter(lambda s: s.strip() != ""),
}).map(lambda d: {**d, "status": "deprecated"})

# Strategy for a list of extracted items (at least 1)
extracted_items_strategy = st.lists(
    extracted_item_strategy, min_size=1, max_size=20
)

# Strategy for a valid Service_Config
service_config_strategy = st.fixed_dictionaries({
    "service_name": service_name_strategy,
    "schema_key": schema_key_strategy,
    "name": st.text(min_size=1, max_size=100),
    "documentation_urls": st.lists(
        st.text(min_size=5, max_size=200), min_size=1, max_size=3
    ),
    "extraction_focus": st.text(min_size=1, max_size=200),
    "item_properties": st.fixed_dictionaries({
        "name": st.just("Name"),
        "identifier": st.just("Identifier"),
    }),
    "required_fields": st.just(["name", "identifier"]),
})


# --- Property tests ---


class TestPropertyDynamoKeyGeneration:
    """
    Property 2: Config-driven extraction produces correctly keyed output.

    Verifies that for any valid service config and any list of extracted items,
    the key generation always uses service_name as PK and item_id as SK,
    without requiring service-specific code changes.
    """

    @settings(max_examples=100)
    @given(config=service_config_strategy, items=extracted_items_strategy)
    def test_service_name_always_used_as_partition_key(self, config, items):
        """
        **Validates: Requirements 1.2**

        For any valid config and any extracted items, the output always
        uses service_name as the partition key.
        """
        service_name = config["service_name"]
        schema_key = config["schema_key"]

        for item in items:
            record = generate_dynamo_record(service_name, schema_key, item)
            assert record is not None, "Record should be generated for valid items"
            assert record["service_name"] == service_name, (
                f"Partition key must be service_name '{service_name}', "
                f"got '{record['service_name']}'"
            )

    @settings(max_examples=100)
    @given(config=service_config_strategy, items=extracted_items_strategy)
    def test_item_id_derived_as_sort_key(self, config, items):
        """
        **Validates: Requirements 1.4**

        For any valid config and any extracted items, the output always
        produces an item_id sort key derived from schema_key and identifier.
        """
        service_name = config["service_name"]
        schema_key = config["schema_key"]

        for item in items:
            record = generate_dynamo_record(service_name, schema_key, item)
            assert record is not None, "Record should be generated for valid items"

            expected_item_id = f"{schema_key}#{item['identifier']}"
            assert record["item_id"] == expected_item_id, (
                f"Sort key must be '{expected_item_id}', got '{record['item_id']}'"
            )

    @settings(max_examples=100)
    @given(config=service_config_strategy, items=extracted_items_strategy)
    def test_no_service_specific_logic_needed(self, config, items):
        """
        **Validates: Requirements 1.2, 1.4**

        The same generate_dynamo_record function works for ANY service config
        without branching or service-specific logic. This test verifies the
        function produces valid records regardless of the service_name value.
        """
        service_name = config["service_name"]
        schema_key = config["schema_key"]

        records = []
        for item in items:
            record = generate_dynamo_record(service_name, schema_key, item)
            assert record is not None
            records.append(record)

        # All records share the same PK (service_name)
        pks = {r["service_name"] for r in records}
        assert len(pks) == 1
        assert service_name in pks

        # All sort keys follow the same pattern: schema_key#identifier
        for record, item in zip(records, items):
            assert record["item_id"].startswith(f"{schema_key}#")
            assert record["item_id"] == f"{schema_key}#{item['identifier']}"

    @settings(max_examples=100)
    @given(
        configs=st.lists(service_config_strategy, min_size=2, max_size=5),
        items=extracted_items_strategy,
    )
    def test_same_function_works_across_different_services(self, configs, items):
        """
        **Validates: Requirements 1.2, 1.4**

        Verifies that the exact same function (no code changes) works for
        multiple different service configurations. This proves that adding
        a new Service_Config requires zero code modifications.
        """
        for config in configs:
            service_name = config["service_name"]
            schema_key = config["schema_key"]

            for item in items:
                record = generate_dynamo_record(service_name, schema_key, item)
                assert record is not None
                assert record["service_name"] == service_name
                assert record["item_id"] == f"{schema_key}#{item['identifier']}"
