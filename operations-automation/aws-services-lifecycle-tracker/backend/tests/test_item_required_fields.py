"""
Unit tests for validate_item_against_config(): derived fields are not required.

Regression: the Lambda extraction pulled 42 runtimes from the docs, but every
item was rejected with "Missing required field: status" because the config
listed 'status' as required while the model had omitted it. Nothing was stored
and the refresh reported "Failed: lambda". The stored status is derived from the
lifecycle dates by categorize_item_status(), so the model's value is optional.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database_writes import DERIVED_FIELDS, categorize_item_status, validate_item_against_config

SERVICE_CONFIGS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "service_configs.json",
)


class TestDerivedFieldsAreNotRequired:
    def test_missing_status_passes_even_when_config_requires_it(self):
        config = {"required_fields": ["name", "identifier", "deprecation_date", "status"]}
        item = {"name": "Node.js 16", "identifier": "nodejs16.x", "deprecation_date": "2024-06-12"}
        assert validate_item_against_config(item, config) == (True, [])

    def test_blank_status_passes_when_config_requires_it(self):
        config = {"required_fields": ["name", "identifier", "status"]}
        item = {"name": "Glue 0.9", "identifier": "glue-0.9", "status": ""}
        assert validate_item_against_config(item, config) == (True, [])

    def test_other_required_fields_are_still_enforced(self):
        config = {"required_fields": ["name", "identifier", "deprecation_date", "status"]}
        item = {"name": "Node.js 16", "deprecation_date": "2024-06-12"}  # no identifier, no status
        ok, errors = validate_item_against_config(item, config)
        assert not ok
        assert errors == ["Missing required field: identifier"]

    def test_free_text_status_is_still_accepted_and_untouched(self):
        config = {"required_fields": ["name", "identifier"]}
        item = {"name": "x", "identifier": "x", "status": "End of support"}
        assert validate_item_against_config(item, config)[0]
        assert item["status"] == "End of support"

    def test_valid_status_is_still_normalized_to_lowercase(self):
        config = {"required_fields": ["name", "identifier"]}
        item = {"name": "x", "identifier": "x", "status": "Deprecated"}
        validate_item_against_config(item, config)
        assert item["status"] == "deprecated"

    def test_item_without_status_still_gets_a_date_derived_status(self):
        # The reason 'status' can be optional: the stored value comes from the dates.
        item = {"name": "Node.js 16", "identifier": "nodejs16.x", "deprecation_date": "2024-06-12"}
        assert categorize_item_status(item, "lambda") == "deprecated"


class TestShippedServiceConfigs:
    def test_no_service_requires_a_derived_field(self):
        """Keep service_configs.json and the validator in agreement."""
        with open(SERVICE_CONFIGS, encoding="utf-8") as f:
            services = json.load(f)["services"]
        offenders = {
            name: sorted(DERIVED_FIELDS & set(cfg.get("required_fields", [])))
            for name, cfg in services.items()
            if DERIVED_FIELDS & set(cfg.get("required_fields", []))
        }
        assert offenders == {}, f"derived fields listed as required: {offenders}"
