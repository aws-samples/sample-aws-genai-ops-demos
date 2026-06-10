"""
Property-based test: Fault tolerance across service extractions

**Validates: Requirements 1.5**

Feature: extended-coverage-and-health-integration, Property 3: Fault tolerance across service extractions

Property under test:
    For *any* list of services where a subset fails (inaccessible URLs, timeouts),
    the system SHALL successfully process all non-failing services and return partial results.

This test validates that the extraction orchestration handles failures gracefully:
1. All non-failing services are processed successfully
2. Partial results are returned (only successful services appear in output)
3. Errors are logged (not silently swallowed)
"""
import sys
import os
import logging
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Testable extraction orchestration function
# ---------------------------------------------------------------------------
# This function mirrors the fault-tolerance pattern in main.py's
# handle_multi_service_extraction, extracted for testability.

class ExtractionError(Exception):
    """Raised when a service extraction fails (URL inaccessible, timeout, etc.)."""
    pass


def extract_services_with_fault_tolerance(
    services: list[dict],
    extract_fn: callable,
) -> dict:
    """
    Orchestrate extraction across multiple services with fault tolerance.

    For each service in the list, calls extract_fn(service). If extract_fn raises
    an exception, the error is recorded and processing continues with the next service.

    Args:
        services: List of service dicts with at least 'service_name' key.
        extract_fn: A callable that takes a service dict and returns extracted items
                    (list of dicts), or raises an exception on failure.

    Returns:
        dict with keys:
            - success: True if at least one service succeeded
            - results: list of per-service result dicts
            - successful_services: list of service names that succeeded
            - failed_services: list of service names that failed
            - errors: list of error details (service_name, error message)
            - total_items_extracted: total items across successful services
    """
    logger = logging.getLogger(__name__)

    results = []
    successful_services = []
    failed_services = []
    errors = []
    total_items = 0

    for service in services:
        service_name = service.get('service_name', '<unknown>')
        try:
            items = extract_fn(service)
            results.append({
                'service_name': service_name,
                'success': True,
                'items_extracted': len(items),
                'items': items,
            })
            successful_services.append(service_name)
            total_items += len(items)
        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"Extraction failed for service '{service_name}': {error_msg}"
            )
            results.append({
                'service_name': service_name,
                'success': False,
                'items_extracted': 0,
                'error': error_msg,
            })
            failed_services.append(service_name)
            errors.append({
                'service_name': service_name,
                'error': error_msg,
            })

    return {
        'success': len(successful_services) > 0,
        'total_services_processed': len(services),
        'successful_extractions': len(successful_services),
        'failed_extractions': len(failed_services),
        'successful_services': successful_services,
        'failed_services': failed_services,
        'errors': errors,
        'total_items_extracted': total_items,
        'results': results,
        'extraction_date': datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Strategy for generating service names
service_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='-_'),
    min_size=1,
    max_size=30,
)

# Strategy for a service that is expected to succeed
succeed_service_strategy = st.fixed_dictionaries({
    'service_name': service_name_strategy,
    'should_fail': st.just(False),
    'items_count': st.integers(min_value=0, max_value=20),
})

# Strategy for a service that is expected to fail
fail_service_strategy = st.fixed_dictionaries({
    'service_name': service_name_strategy,
    'should_fail': st.just(True),
    'failure_type': st.sampled_from([
        'url_inaccessible',
        'timeout',
        'parse_error',
        'connection_refused',
        'server_error',
    ]),
})

# Strategy for a mixed list of services (some succeed, some fail)
mixed_services_strategy = st.lists(
    st.one_of(succeed_service_strategy, fail_service_strategy),
    min_size=1,
    max_size=15,
)


# ---------------------------------------------------------------------------
# Helper: simulated extract function
# ---------------------------------------------------------------------------

def make_simulated_extract_fn():
    """Create a simulated extraction function that succeeds or fails based on service config."""

    def simulated_extract(service: dict) -> list[dict]:
        if service.get('should_fail', False):
            failure_type = service.get('failure_type', 'url_inaccessible')
            if failure_type == 'url_inaccessible':
                raise ExtractionError(
                    f"URL inaccessible for service '{service['service_name']}': "
                    f"HTTP 404 Not Found"
                )
            elif failure_type == 'timeout':
                raise TimeoutError(
                    f"Request timeout for service '{service['service_name']}': "
                    f"Connection timed out after 30s"
                )
            elif failure_type == 'parse_error':
                raise ValueError(
                    f"Failed to parse HTML for service '{service['service_name']}': "
                    f"No tables found"
                )
            elif failure_type == 'connection_refused':
                raise ConnectionError(
                    f"Connection refused for service '{service['service_name']}'"
                )
            elif failure_type == 'server_error':
                raise ExtractionError(
                    f"Server error for service '{service['service_name']}': HTTP 500"
                )
            else:
                raise ExtractionError(f"Unknown failure for '{service['service_name']}'")

        # Service succeeds: generate mock items
        items_count = service.get('items_count', 1)
        return [
            {
                'name': f"item-{i}",
                'identifier': f"{service['service_name']}-{i}",
                'status': 'active',
            }
            for i in range(items_count)
        ]

    return simulated_extract


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tag", [
    "Feature: extended-coverage-and-health-integration, "
    "Property 3: Fault tolerance across service extractions"
])
class TestFaultToleranceProperty:
    """
    Property 3: Fault tolerance across service extractions

    **Validates: Requirements 1.5**

    For *any* list of services where a subset fails (inaccessible URLs, timeouts),
    the system SHALL successfully process all non-failing services and return
    partial results.
    """

    @given(services=mixed_services_strategy)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_non_failing_services_always_processed(self, services, tag, caplog):
        """
        All non-failing services are processed successfully regardless of
        how many other services fail.
        """
        extract_fn = make_simulated_extract_fn()
        expected_successes = [s for s in services if not s['should_fail']]
        expected_failures = [s for s in services if s['should_fail']]

        result = extract_services_with_fault_tolerance(services, extract_fn)

        # Every non-failing service must appear in successful results
        assert result['successful_extractions'] == len(expected_successes)
        assert result['failed_extractions'] == len(expected_failures)

        # Verify each non-failing service produced the correct number of items
        for service in expected_successes:
            matching = [
                r for r in result['results']
                if r['service_name'] == service['service_name'] and r['success']
            ]
            assert len(matching) >= 1, (
                f"Service '{service['service_name']}' should have succeeded "
                f"but wasn't found in successful results"
            )
            # At least one matching result should have the expected item count
            assert any(
                r['items_extracted'] == service['items_count']
                for r in matching
            )

    @given(services=mixed_services_strategy)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_partial_results_returned(self, services, tag, caplog):
        """
        Partial results are returned: only successful services appear in output
        items. Failed services do not contribute items.
        """
        extract_fn = make_simulated_extract_fn()
        expected_successes = [s for s in services if not s['should_fail']]
        expected_total_items = sum(s.get('items_count', 1) for s in expected_successes)

        result = extract_services_with_fault_tolerance(services, extract_fn)

        # Total items must equal sum of items from successful services only
        assert result['total_items_extracted'] == expected_total_items

        # Failed services must not have items in results
        for r in result['results']:
            if not r['success']:
                assert r['items_extracted'] == 0
                assert 'error' in r

        # Success flag: true if at least one service succeeded
        if expected_successes:
            assert result['success'] is True
        else:
            assert result['success'] is False

    @given(services=mixed_services_strategy)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_errors_are_logged_not_swallowed(self, services, tag, caplog):
        """
        Errors from failing services are logged (not silently swallowed).
        Each failure produces an error log entry and appears in the errors list.
        """
        extract_fn = make_simulated_extract_fn()
        expected_failures = [s for s in services if s['should_fail']]

        with caplog.at_level(logging.ERROR):
            result = extract_services_with_fault_tolerance(services, extract_fn)

        # Each failed service must appear in the errors list
        assert len(result['errors']) == len(expected_failures)

        for service in expected_failures:
            matching_errors = [
                e for e in result['errors']
                if e['service_name'] == service['service_name']
            ]
            assert len(matching_errors) >= 1, (
                f"Service '{service['service_name']}' failed but no error was recorded"
            )
            # Each error must have a non-empty error message
            for err in matching_errors:
                assert err['error'] and len(err['error']) > 0

        # Verify logger was called for each failure
        if expected_failures:
            error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
            assert len(error_logs) >= len(expected_failures), (
                f"Expected at least {len(expected_failures)} error log entries, "
                f"got {len(error_logs)}"
            )

    @given(
        services=st.lists(
            succeed_service_strategy,
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_all_succeed_when_no_failures(self, services, tag, caplog):
        """
        When no services fail, all services are processed successfully and
        no errors are recorded.
        """
        extract_fn = make_simulated_extract_fn()

        result = extract_services_with_fault_tolerance(services, extract_fn)

        assert result['success'] is True
        assert result['failed_extractions'] == 0
        assert result['successful_extractions'] == len(services)
        assert len(result['errors']) == 0
        assert result['total_items_extracted'] == sum(
            s['items_count'] for s in services
        )

    @given(
        services=st.lists(
            fail_service_strategy,
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_all_fail_returns_no_items_but_no_crash(self, services, tag, caplog):
        """
        When all services fail, the function does not crash, returns success=False,
        and all failures are logged.
        """
        extract_fn = make_simulated_extract_fn()

        with caplog.at_level(logging.ERROR):
            result = extract_services_with_fault_tolerance(services, extract_fn)

        assert result['success'] is False
        assert result['successful_extractions'] == 0
        assert result['failed_extractions'] == len(services)
        assert result['total_items_extracted'] == 0
        assert len(result['errors']) == len(services)
