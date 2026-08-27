"""
Property-based tests for service mapping and event filtering.

Feature: extended-coverage-and-health-integration, Property 6: Service mapping and event filtering

**Validates: Requirements 3.3, 4.2**

For *any* set of Health events and *any* set of Service_Configs with
health_event_mapping fields, the filtering SHALL only return events whose
service field corresponds to a configured health_event_mapping. No event
concerning a non-configured service SHALL pass the filter.
"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from health_enricher import HealthEnricher


# --- Strategies ---

# Strategy for generating valid service keys (internal service names)
service_key_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=2,
    max_size=20,
).filter(lambda s: s.strip() != "" and not s.startswith("_"))

# Strategy for generating health_event_mapping values (uppercase service identifiers)
health_mapping_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "N"), whitelist_characters="_"),
    min_size=2,
    max_size=15,
).filter(lambda s: s.strip() != "")

# Strategy for a single service config entry with health_event_mapping
def service_config_entry_strategy():
    """Generate a single service config dict entry with a health_event_mapping."""
    return st.fixed_dictionaries({
        'name': st.text(min_size=3, max_size=30),
        'health_event_mapping': health_mapping_strategy,
        'documentation_urls': st.just(['https://docs.aws.amazon.com/example']),
        'extraction_focus': st.just('Extract lifecycle data'),
        'item_properties': st.just({}),
        'required_fields': st.just(['name', 'identifier']),
        'enabled': st.just(True),
    })


# Strategy for a service_configs dict (1 to 8 configured services)
@st.composite
def service_configs_strategy(draw):
    """Generate a dictionary of service configs with unique keys and unique mappings."""
    num_services = draw(st.integers(min_value=1, max_value=8))
    configs = {}
    used_mappings = set()

    for _ in range(num_services):
        key = draw(service_key_strategy)
        if key in configs:
            continue
        entry = draw(service_config_entry_strategy())
        mapping = entry['health_event_mapping']
        # Ensure unique mappings
        if mapping.upper() in used_mappings:
            continue
        used_mappings.add(mapping.upper())
        configs[key] = entry

    assume(len(configs) >= 1)
    return configs


# Strategy for a Health event that matches a given mapping
def matching_event_strategy(mapping: str):
    """Generate a Health event whose health_service matches the given mapping."""
    return st.fixed_dictionaries({
        'event_arn': st.builds(
            lambda r: f"arn:aws:health:{r}::event/12345",
            r=st.sampled_from(['us-east-1', 'eu-west-1', 'ap-southeast-1']),
        ),
        'health_service': st.just(mapping),
        'event_type_code': st.just(f"AWS_{mapping}_OPERATIONAL_ISSUE"),
        'event_type_category': st.sampled_from(['issue', 'scheduledChange', 'accountNotification']),
        'region': st.sampled_from(['us-east-1', 'eu-west-1', 'ap-southeast-1']),
        'status_code': st.sampled_from(['open', 'closed', 'upcoming']),
        'start_time': st.just('2025-06-01T00:00:00Z'),
        'end_time': st.just(''),
        'last_updated_time': st.just('2025-06-01T01:00:00Z'),
        'availability_zone': st.just(''),
        'description': st.just('Test event description'),
        'collected_at': st.just('2025-06-01T00:00:00Z'),
        'ttl': st.just(0),
    })


# Strategy for a Health event with an unmapped service (will NOT match any config)
unmapped_event_strategy = st.fixed_dictionaries({
    'event_arn': st.builds(
        lambda r, suffix: f"arn:aws:health:{r}::event/{suffix}",
        r=st.sampled_from(['us-east-1', 'eu-west-1', 'ap-southeast-1']),
        suffix=st.text(alphabet='abcdef0123456789', min_size=5, max_size=10),
    ),
    'health_service': st.text(
        alphabet=st.characters(whitelist_categories=("Lu",), whitelist_characters="_"),
        min_size=8,
        max_size=20,
    ).filter(lambda s: s.strip() != ""),
    'event_type_code': st.just('AWS_UNKNOWN_ISSUE'),
    'event_type_category': st.sampled_from(['issue', 'scheduledChange', 'accountNotification']),
    'region': st.sampled_from(['us-east-1', 'eu-west-1', 'ap-southeast-1']),
    'status_code': st.sampled_from(['open', 'closed', 'upcoming']),
    'start_time': st.just('2025-06-01T00:00:00Z'),
    'end_time': st.just(''),
    'last_updated_time': st.just('2025-06-01T01:00:00Z'),
    'availability_zone': st.just(''),
    'description': st.just('Unmapped event description'),
    'collected_at': st.just('2025-06-01T00:00:00Z'),
    'ttl': st.just(0),
})


# Composite strategy: events with a mix of matching and non-matching services
@st.composite
def mixed_events_strategy(draw, service_configs):
    """
    Generate a list of Health events: some matching configured services,
    some with unmapped services that should be filtered out.
    """
    events = []
    mappings = [
        config['health_event_mapping']
        for config in service_configs.values()
        if config.get('health_event_mapping')
    ]

    # Add 0 to 5 matching events
    num_matching = draw(st.integers(min_value=0, max_value=5))
    for _ in range(num_matching):
        if mappings:
            mapping = draw(st.sampled_from(mappings))
            event = draw(matching_event_strategy(mapping))
            events.append(event)

    # Add 0 to 5 unmapped events (should be filtered out)
    num_unmapped = draw(st.integers(min_value=0, max_value=5))
    for _ in range(num_unmapped):
        event = draw(unmapped_event_strategy)
        # Ensure the generated unmapped service doesn't accidentally match a config
        all_mappings_upper = {m.upper() for m in mappings}
        all_keys_lower = {k.lower() for k in service_configs.keys()}
        health_service_upper = event['health_service'].upper()
        health_service_lower = event['health_service'].lower()
        if health_service_upper in all_mappings_upper or health_service_lower in all_keys_lower:
            continue  # Skip this event if it accidentally matches
        events.append(event)

    return events


# --- Helper ---

def _get_configured_mappings(service_configs: dict) -> set:
    """Get the set of all configured health_event_mapping values (uppercased)."""
    mappings = set()
    for key, config in service_configs.items():
        mapping = config.get('health_event_mapping', '')
        if mapping:
            mappings.add(mapping.upper())
        # Also add the service key itself for case-insensitive fallback matching
        mappings.add(key.upper())
    return mappings


# --- Property Tests ---

class TestServiceMappingFilteringProperty:
    """
    Feature: extended-coverage-and-health-integration, Property 6: Service mapping and event filtering

    For *any* set of Health events and *any* set of Service_Configs with
    health_event_mapping fields, the filtering SHALL only return events whose
    service field corresponds to a configured health_event_mapping. No event
    concerning a non-configured service SHALL pass the filter.
    """

    @given(data=st.data())
    @settings(max_examples=150)
    @patch('health_enricher.list_deprecations', create=True)
    def test_only_mapped_events_pass_through_filter(self, mock_list_deprecations, data):
        """
        For ANY set of service configs and ANY set of events, enrich_events()
        SHALL only return events whose health_service maps to a configured service.

        **Validates: Requirements 3.3**
        """
        mock_list_deprecations.return_value = {'items': []}

        service_configs = data.draw(service_configs_strategy())
        events = data.draw(mixed_events_strategy(service_configs))

        enricher = HealthEnricher()

        with patch('health_enricher.list_deprecations', return_value={'items': []}, create=True):
            with patch.dict('sys.modules', {'database_reads': MagicMock(list_deprecations=MagicMock(return_value={'items': []}))}):
                result = enricher.enrich_events(events, service_configs)

        # Every returned event MUST have a service_name that is a key in service_configs
        for enriched_event in result:
            assert enriched_event['service_name'] in service_configs, (
                f"Enriched event has service_name '{enriched_event['service_name']}' "
                f"which is NOT in the configured services: {list(service_configs.keys())}"
            )

    @given(data=st.data())
    @settings(max_examples=150)
    def test_unmapped_events_are_excluded(self, data):
        """
        For ANY event whose health_service does NOT correspond to any configured
        health_event_mapping, the event SHALL NOT appear in the output.

        **Validates: Requirements 3.3, 4.2**
        """
        service_configs = data.draw(service_configs_strategy())

        # Generate only unmapped events
        all_mappings_upper = set()
        all_keys_lower = set()
        for key, config in service_configs.items():
            mapping = config.get('health_event_mapping', '')
            if mapping:
                all_mappings_upper.add(mapping.upper())
            all_keys_lower.add(key.lower())

        num_events = data.draw(st.integers(min_value=1, max_value=5))
        unmapped_events = []
        for _ in range(num_events):
            event = data.draw(unmapped_event_strategy)
            health_service_upper = event['health_service'].upper()
            health_service_lower = event['health_service'].lower()
            # Only use events that truly don't match
            if health_service_upper not in all_mappings_upper and health_service_lower not in all_keys_lower:
                unmapped_events.append(event)

        assume(len(unmapped_events) >= 1)

        enricher = HealthEnricher()

        with patch.dict('sys.modules', {'database_reads': MagicMock(list_deprecations=MagicMock(return_value={'items': []}))}):
            result = enricher.enrich_events(unmapped_events, service_configs)

        assert len(result) == 0, (
            f"Expected 0 events to pass filter for unmapped services, "
            f"but got {len(result)}. "
            f"Unmapped services: {[e['health_service'] for e in unmapped_events]}. "
            f"Configured mappings: {all_mappings_upper}"
        )

    @given(data=st.data())
    @settings(max_examples=150)
    def test_all_matching_events_are_included(self, data):
        """
        For ANY event whose health_service matches a configured health_event_mapping,
        the event SHALL appear in the enriched output.

        **Validates: Requirements 4.2**
        """
        service_configs = data.draw(service_configs_strategy())

        mappings = [
            config['health_event_mapping']
            for config in service_configs.values()
            if config.get('health_event_mapping')
        ]
        assume(len(mappings) >= 1)

        # Generate only matching events
        num_events = data.draw(st.integers(min_value=1, max_value=5))
        matching_events = []
        for _ in range(num_events):
            mapping = data.draw(st.sampled_from(mappings))
            event = data.draw(matching_event_strategy(mapping))
            matching_events.append(event)

        enricher = HealthEnricher()

        with patch.dict('sys.modules', {'database_reads': MagicMock(list_deprecations=MagicMock(return_value={'items': []}))}):
            result = enricher.enrich_events(matching_events, service_configs)

        assert len(result) == len(matching_events), (
            f"Expected all {len(matching_events)} matching events to pass filter, "
            f"but only {len(result)} passed. "
            f"Event services: {[e['health_service'] for e in matching_events]}. "
            f"Configured mappings: {mappings}"
        )

    @given(data=st.data())
    @settings(max_examples=100)
    def test_mapping_is_case_insensitive(self, data):
        """
        The _map_service_name function SHALL match health_event_mapping
        values in a case-insensitive manner.

        **Validates: Requirements 3.3, 4.2**
        """
        service_configs = data.draw(service_configs_strategy())

        mappings = [
            config['health_event_mapping']
            for config in service_configs.values()
            if config.get('health_event_mapping')
        ]
        assume(len(mappings) >= 1)

        mapping = data.draw(st.sampled_from(mappings))

        # Apply a random case transformation
        case_variant = data.draw(st.sampled_from([
            mapping.upper(),
            mapping.lower(),
            mapping.capitalize(),
        ]))

        enricher = HealthEnricher()
        result = enricher._map_service_name(case_variant, service_configs)

        assert result is not None, (
            f"Case-insensitive mapping failed: '{case_variant}' was not mapped "
            f"to any configured service. "
            f"Available mappings: {mappings}"
        )
        assert result in service_configs, (
            f"Mapped service_name '{result}' is not a key in service_configs"
        )

    @given(data=st.data())
    @settings(max_examples=100)
    def test_empty_health_service_returns_none(self, data):
        """
        For an event with an empty health_service field, _map_service_name
        SHALL return None (event is filtered out).

        **Validates: Requirements 3.3**
        """
        service_configs = data.draw(service_configs_strategy())

        enricher = HealthEnricher()
        result = enricher._map_service_name('', service_configs)

        assert result is None, (
            f"Expected None for empty health_service, but got '{result}'"
        )
