"""
Unit tests for Service_Config validation in data_extractor.py

Tests validate_service_config() for:
- Valid configs (old format without health_event_mapping)
- Valid configs (new format with health_event_mapping)
- Missing required fields
- Invalid field types
- Optional fields accepted without error
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

from data_extractor import validate_service_config


def _make_valid_config(**overrides):
    """Helper to create a valid base config for testing."""
    config = {
        "name": "AWS Lambda",
        "documentation_urls": [
            "https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html"
        ],
        "extraction_focus": "Extract runtime deprecation data",
        "item_properties": {
            "name": "Runtime name",
            "identifier": "Runtime ID",
        },
        "required_fields": ["name", "identifier"],
    }
    config.update(overrides)
    return config


class TestValidateServiceConfigValid:
    """Tests for valid configs that should pass validation."""

    def test_valid_minimal_config(self):
        """A config with only required fields should be valid."""
        config = _make_valid_config()
        is_valid, errors = validate_service_config(config)
        assert is_valid is True
        assert errors == []

    def test_valid_config_with_health_event_mapping(self):
        """A config with optional health_event_mapping should be valid."""
        config = _make_valid_config(health_event_mapping="LAMBDA")
        is_valid, errors = validate_service_config(config)
        assert is_valid is True
        assert errors == []

    def test_valid_config_with_all_optional_fields(self):
        """A config with all optional fields should be valid."""
        config = _make_valid_config(
            health_event_mapping="LAMBDA",
            schema_key="runtimes",
            enabled=True,
            last_extraction="2025-01-01T00:00:00Z",
            extraction_count=5,
        )
        is_valid, errors = validate_service_config(config)
        assert is_valid is True
        assert errors == []

    def test_valid_config_backward_compatible(self):
        """Old-format config without new optional fields should be valid."""
        config = {
            "name": "Amazon EKS",
            "documentation_urls": [
                "https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html"
            ],
            "extraction_focus": "Extract Kubernetes version lifecycle",
            "item_properties": {"name": "Version", "identifier": "Version number"},
            "required_fields": ["name", "identifier"],
        }
        is_valid, errors = validate_service_config(config)
        assert is_valid is True
        assert errors == []

    def test_valid_config_multiple_urls(self):
        """A config with multiple documentation URLs should be valid."""
        config = _make_valid_config(
            documentation_urls=[
                "https://docs.aws.amazon.com/page1.html",
                "https://docs.aws.amazon.com/page2.html",
            ]
        )
        is_valid, errors = validate_service_config(config)
        assert is_valid is True
        assert errors == []


class TestValidateServiceConfigMissingFields:
    """Tests for configs with missing required fields."""

    def test_missing_name(self):
        config = _make_valid_config()
        del config["name"]
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("name" in e for e in errors)

    def test_missing_documentation_urls(self):
        config = _make_valid_config()
        del config["documentation_urls"]
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("documentation_urls" in e for e in errors)

    def test_missing_extraction_focus(self):
        config = _make_valid_config()
        del config["extraction_focus"]
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("extraction_focus" in e for e in errors)

    def test_missing_item_properties(self):
        config = _make_valid_config()
        del config["item_properties"]
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("item_properties" in e for e in errors)

    def test_missing_required_fields(self):
        config = _make_valid_config()
        del config["required_fields"]
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("required_fields" in e for e in errors)

    def test_missing_multiple_fields(self):
        config = _make_valid_config()
        del config["name"]
        del config["extraction_focus"]
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert len(errors) >= 2

    def test_empty_dict(self):
        is_valid, errors = validate_service_config({})
        assert is_valid is False
        assert len(errors) == 5  # All 5 required fields missing


class TestValidateServiceConfigTypeErrors:
    """Tests for configs with invalid field types."""

    def test_name_not_string(self):
        config = _make_valid_config(name=123)
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("name" in e and "string" in e for e in errors)

    def test_documentation_urls_not_list(self):
        config = _make_valid_config(documentation_urls="not a list")
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("documentation_urls" in e and "list" in e for e in errors)

    def test_documentation_urls_empty_list(self):
        config = _make_valid_config(documentation_urls=[])
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("documentation_urls" in e and "empty" in e for e in errors)

    def test_extraction_focus_not_string(self):
        config = _make_valid_config(extraction_focus=["not", "a", "string"])
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("extraction_focus" in e and "string" in e for e in errors)

    def test_item_properties_not_dict(self):
        config = _make_valid_config(item_properties="not a dict")
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("item_properties" in e and "dictionary" in e for e in errors)

    def test_required_fields_not_list(self):
        config = _make_valid_config(required_fields="not a list")
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("required_fields" in e and "list" in e for e in errors)

    def test_required_fields_empty_list(self):
        config = _make_valid_config(required_fields=[])
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("required_fields" in e and "empty" in e for e in errors)

    def test_not_a_dict(self):
        is_valid, errors = validate_service_config("not a dict")
        assert is_valid is False
        assert any("dictionary" in e for e in errors)

    def test_none_input(self):
        is_valid, errors = validate_service_config(None)
        assert is_valid is False
        assert any("dictionary" in e for e in errors)


class TestValidateServiceConfigOptionalFields:
    """Tests for optional field handling."""

    def test_health_event_mapping_valid(self):
        config = _make_valid_config(health_event_mapping="LAMBDA")
        is_valid, errors = validate_service_config(config)
        assert is_valid is True

    def test_health_event_mapping_invalid_type(self):
        config = _make_valid_config(health_event_mapping=123)
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("health_event_mapping" in e for e in errors)

    def test_schema_key_valid(self):
        config = _make_valid_config(schema_key="runtimes")
        is_valid, errors = validate_service_config(config)
        assert is_valid is True

    def test_enabled_valid(self):
        config = _make_valid_config(enabled=False)
        is_valid, errors = validate_service_config(config)
        assert is_valid is True

    def test_enabled_invalid_type(self):
        config = _make_valid_config(enabled="yes")
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("enabled" in e for e in errors)

    def test_extraction_count_valid(self):
        config = _make_valid_config(extraction_count=10)
        is_valid, errors = validate_service_config(config)
        assert is_valid is True

    def test_extraction_count_invalid_type(self):
        config = _make_valid_config(extraction_count="ten")
        is_valid, errors = validate_service_config(config)
        assert is_valid is False
        assert any("extraction_count" in e for e in errors)

    def test_unknown_fields_do_not_cause_errors(self):
        """Unknown/extra fields should not cause validation failures."""
        config = _make_valid_config(
            some_future_field="value",
            another_field=42,
        )
        is_valid, errors = validate_service_config(config)
        assert is_valid is True
        assert errors == []
