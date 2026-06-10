"""
Property-Based Tests for Service_Config schema validation.

Feature: extended-coverage-and-health-integration, Property 1: Config schema validation

**Validates: Requirements 1.3, 10.1, 10.3**

Tests verify:
- For any valid Service_Config at the existing format (without health_event_mapping),
  validation SHALL succeed.
- For any valid Service_Config with the new format (with health_event_mapping),
  validation SHALL also succeed.
- For any entry missing one or more required fields (name, documentation_urls,
  extraction_focus, item_properties, required_fields), validation SHALL signal error
  and identify the missing fields.
"""
import sys
import os
from unittest.mock import MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock heavy dependencies that are not needed for config validation
sys.modules['bs4'] = MagicMock()
sys.modules['boto3'] = MagicMock()
sys.modules['botocore'] = MagicMock()
sys.modules['botocore.exceptions'] = MagicMock()
sys.modules['requests'] = MagicMock()
sys.modules['aws_utils'] = MagicMock()
sys.modules['database_reads'] = MagicMock()
sys.modules['service_filters'] = MagicMock()

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from data_extractor import validate_service_config, SERVICE_CONFIG_REQUIRED_FIELDS


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for generating non-empty text (used for string fields)
non_empty_text = st.text(min_size=1, max_size=100).filter(lambda s: s.strip() != "")

# Strategy for generating valid URL-like strings
url_strategy = st.text(min_size=10, max_size=200).map(
    lambda s: f"https://docs.aws.amazon.com/{s.replace(chr(0), '')}"
)

# Strategy for non-empty list of URLs
documentation_urls_strategy = st.lists(url_strategy, min_size=1, max_size=5)

# Strategy for item_properties (non-empty dict of string -> string)
item_properties_strategy = st.dictionaries(
    keys=non_empty_text,
    values=non_empty_text,
    min_size=1,
    max_size=10,
)

# Strategy for required_fields (non-empty list of strings)
required_fields_strategy = st.lists(non_empty_text, min_size=1, max_size=10)

# Strategy for a valid Service_Config in OLD format (no health_event_mapping)
valid_old_format_config = st.fixed_dictionaries({
    "name": non_empty_text,
    "documentation_urls": documentation_urls_strategy,
    "extraction_focus": non_empty_text,
    "item_properties": item_properties_strategy,
    "required_fields": required_fields_strategy,
})

# Strategy for health_event_mapping value (uppercase service names)
health_event_mapping_strategy = st.text(
    alphabet=st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZ_"),
    min_size=1,
    max_size=30,
)

# Strategy for a valid Service_Config in NEW format (with health_event_mapping)
valid_new_format_config = st.fixed_dictionaries({
    "name": non_empty_text,
    "documentation_urls": documentation_urls_strategy,
    "extraction_focus": non_empty_text,
    "item_properties": item_properties_strategy,
    "required_fields": required_fields_strategy,
    "health_event_mapping": health_event_mapping_strategy,
})

# Strategy for optional fields to add on top of a valid config
optional_fields_strategy = st.fixed_dictionaries(
    {},
    optional={
        "schema_key": non_empty_text,
        "enabled": st.booleans(),
        "last_extraction": non_empty_text,
        "extraction_count": st.integers(min_value=0, max_value=10000),
    },
)

# Strategy: pick a non-empty subset of required fields to remove
required_field_names = st.sampled_from(SERVICE_CONFIG_REQUIRED_FIELDS)
fields_to_remove_strategy = st.lists(
    required_field_names,
    min_size=1,
    max_size=len(SERVICE_CONFIG_REQUIRED_FIELDS),
    unique=True,
)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestPropertyConfigSchemaValidation:
    """
    Property 1: Config schema validation (backward & forward compatible)

    Feature: extended-coverage-and-health-integration, Property 1: Config schema validation
    **Validates: Requirements 1.3, 10.1, 10.3**
    """

    @given(config=valid_old_format_config)
    @settings(max_examples=100)
    def test_valid_old_format_always_passes(self, config):
        """
        For any valid Service_Config at the existing format (without
        health_event_mapping), validation SHALL succeed without error.

        **Validates: Requirements 1.3, 10.1, 10.3**
        """
        is_valid, errors = validate_service_config(config)
        assert is_valid is True, f"Valid old-format config rejected: {errors}"
        assert errors == [], f"Expected no errors, got: {errors}"

    @given(config=valid_new_format_config)
    @settings(max_examples=100)
    def test_valid_new_format_always_passes(self, config):
        """
        For any valid Service_Config with the new format (with
        health_event_mapping), validation SHALL also succeed.

        **Validates: Requirements 1.3, 10.1, 10.3**
        """
        is_valid, errors = validate_service_config(config)
        assert is_valid is True, f"Valid new-format config rejected: {errors}"
        assert errors == [], f"Expected no errors, got: {errors}"

    @given(config=valid_old_format_config, optional=optional_fields_strategy)
    @settings(max_examples=100)
    def test_valid_config_with_optional_fields_always_passes(self, config, optional):
        """
        For any valid Service_Config extended with optional fields,
        validation SHALL succeed.

        **Validates: Requirements 10.1**
        """
        merged = {**config, **optional}
        is_valid, errors = validate_service_config(merged)
        assert is_valid is True, f"Valid config with optional fields rejected: {errors}"
        assert errors == [], f"Expected no errors, got: {errors}"

    @given(
        config=valid_old_format_config,
        fields_to_remove=fields_to_remove_strategy,
    )
    @settings(max_examples=100)
    def test_missing_required_fields_always_fail(self, config, fields_to_remove):
        """
        For any entry missing one or more required fields (name,
        documentation_urls, extraction_focus, item_properties,
        required_fields), validation SHALL signal error and identify
        the missing fields.

        **Validates: Requirements 10.3**
        """
        # Remove the selected required fields
        for field in fields_to_remove:
            config.pop(field, None)

        is_valid, errors = validate_service_config(config)

        # Validation must fail
        assert is_valid is False, (
            f"Config missing {fields_to_remove} was incorrectly accepted"
        )

        # Each removed field must be identified in the errors
        for field in fields_to_remove:
            assert any(field in e for e in errors), (
                f"Missing field '{field}' not identified in errors: {errors}"
            )

    @given(
        config=valid_new_format_config,
        fields_to_remove=fields_to_remove_strategy,
    )
    @settings(max_examples=100)
    def test_missing_required_fields_in_new_format_always_fail(self, config, fields_to_remove):
        """
        For any new-format entry missing required fields, validation SHALL
        still signal error even if health_event_mapping is present.

        **Validates: Requirements 10.1, 10.3**
        """
        # Remove the selected required fields (keep health_event_mapping)
        for field in fields_to_remove:
            config.pop(field, None)

        is_valid, errors = validate_service_config(config)

        # Validation must fail
        assert is_valid is False, (
            f"New-format config missing {fields_to_remove} was incorrectly accepted"
        )

        # Each removed field must be identified in the errors
        for field in fields_to_remove:
            assert any(field in e for e in errors), (
                f"Missing field '{field}' not identified in errors: {errors}"
            )
