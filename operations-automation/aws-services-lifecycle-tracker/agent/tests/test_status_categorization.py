"""
Unit tests for categorize_item_status() (issue #119).

Covers the two reported defects and their fix:
1. MSK returned the 'end_of_support_date' label without checking whether the
   date had passed, and blanket-labeled everything else 'deprecated'.
2. The generic fallback could never return 'supported' and defaulted every
   unmatched item to 'deprecated'; the 180/365 bands were redundant; a
   standard-support date alone was ignored.

All tests are deterministic (dates computed relative to today).
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database_writes import categorize_item_status


def _date(days_from_now: int) -> str:
    """ISO date string N days from today (negative = past)."""
    return (datetime.now(timezone.utc).date() + timedelta(days=days_from_now)).isoformat()


# ---------------------------------------------------------------------------
# MSK (defect 1)
# ---------------------------------------------------------------------------

class TestMskCategorization:
    def test_eos_date_in_past_is_end_of_life(self):
        item = {'end_of_support_date': _date(-30)}
        assert categorize_item_status(item, 'msk') == 'end_of_life'

    def test_eos_date_in_future_is_end_of_support_date(self):
        item = {'end_of_support_date': _date(200)}
        assert categorize_item_status(item, 'msk') == 'end_of_support_date'

    def test_no_eos_date_and_no_signal_is_supported(self):
        # Previously blanket-labeled 'deprecated'
        item = {'kafka_version': '3.6.0'}
        assert categorize_item_status(item, 'msk') == 'supported'

    def test_no_eos_date_but_docs_say_deprecated(self):
        item = {'kafka_version': '1.1.1', 'status': 'Deprecated'}
        assert categorize_item_status(item, 'msk') == 'deprecated'


# ---------------------------------------------------------------------------
# Generic logic (defect 2)
# ---------------------------------------------------------------------------

class TestGenericHardEndDates:
    def test_end_date_in_past_is_end_of_life(self):
        item = {'eol_date': _date(-1)}
        assert categorize_item_status(item, 'bedrock') == 'end_of_life'

    def test_end_date_within_a_year_is_extended_support(self):
        item = {'end_of_support_date': _date(100)}
        assert categorize_item_status(item, 'ecs') == 'extended_support'

    def test_end_date_beyond_a_year_is_announced_not_deprecated(self):
        # Previously fell through to the blanket 'deprecated' fallback
        item = {'end_of_support_date': _date(400)}
        assert categorize_item_status(item, 'ecs') == 'end_of_support_date'


class TestStandardExtendedSupportPair:
    def test_extended_end_in_past_is_end_of_life(self):
        item = {
            'end_of_standard_support_date': _date(-400),
            'end_of_extended_support_date': _date(-10),
        }
        assert categorize_item_status(item, 'eks') == 'end_of_life'

    def test_in_extended_window_is_extended_support(self):
        item = {
            'end_of_standard_support_date': _date(-30),
            'end_of_extended_support_date': _date(300),
        }
        assert categorize_item_status(item, 'eks') == 'extended_support'

    def test_still_in_standard_support_is_supported(self):
        # Previously fell through to the blanket 'deprecated' fallback
        item = {
            'end_of_standard_support_date': _date(200),
            'end_of_extended_support_date': _date(500),
        }
        assert categorize_item_status(item, 'eks') == 'supported'

    def test_standard_date_alone_future_is_supported(self):
        # Previously end_of_standard_support_date alone was ignored entirely
        item = {'end_of_standard_support_date': _date(200)}
        assert categorize_item_status(item, 'rds') == 'supported'

    def test_standard_date_alone_past_is_extended_support(self):
        item = {'end_of_standard_support_date': _date(-10)}
        assert categorize_item_status(item, 'rds') == 'extended_support'


class TestDeprecationSignals:
    def test_deprecation_date_in_past_is_deprecated(self):
        item = {'deprecation_date': _date(-5)}
        assert categorize_item_status(item, 'sagemaker') == 'deprecated'

    def test_deprecation_date_in_future_is_announced(self):
        item = {'deprecation_date': _date(90)}
        assert categorize_item_status(item, 'sagemaker') == 'end_of_support_date'

    def test_status_text_deprecated_without_dates(self):
        item = {'status': 'This version is deprecated'}
        assert categorize_item_status(item, 'glue') == 'deprecated'

    def test_status_text_retired_without_dates(self):
        item = {'status': 'Retired'}
        assert categorize_item_status(item, 'emr') == 'end_of_life'


class TestSupportedFallback:
    def test_no_dates_no_signal_is_supported(self):
        # The core of defect 2: this used to return 'deprecated'
        item = {'name': 'Engine 8.0', 'version': '8.0'}
        assert categorize_item_status(item, 'documentdb') == 'supported'

    def test_unparseable_dates_are_ignored(self):
        item = {'end_of_support_date': 'N/A', 'deprecation_date': '--'}
        assert categorize_item_status(item, 'neptune') == 'supported'

    def test_supported_status_text_stays_supported(self):
        item = {'status': 'Currently supported'}
        assert categorize_item_status(item, 'athena') == 'supported'


# ---------------------------------------------------------------------------
# Existing service-specific behavior must not regress
# ---------------------------------------------------------------------------

class TestLambda:
    # Since #140 the whole runtimes page is extracted (supported + deprecated
    # tables), so the verdict is date-driven instead of a blanket 'deprecated'.
    def test_block_date_in_past_is_end_of_life(self):
        item = {'block_function_create_date': _date(-10)}
        assert categorize_item_status(item, 'lambda') == 'end_of_life'

    def test_config_field_names_block_update_date_is_honoured(self):
        # The lambda config emits block_update_date / block_create_date
        item = {'deprecation_date': _date(-400), 'block_update_date': _date(-10)}
        assert categorize_item_status(item, 'lambda') == 'end_of_life'

    def test_deprecated_but_not_yet_blocked_is_deprecated(self):
        item = {'deprecation_date': _date(-100), 'block_update_date': _date(60)}
        assert categorize_item_status(item, 'lambda') == 'deprecated'

    def test_future_deprecation_within_a_year_is_end_of_support_date(self):
        item = {'deprecation_date': _date(200), 'block_update_date': _date(300)}
        assert categorize_item_status(item, 'lambda') == 'end_of_support_date'

    def test_future_deprecation_far_out_is_supported(self):
        # e.g. python3.13, deprecation 2029
        item = {'deprecation_date': _date(900), 'block_update_date': _date(960)}
        assert categorize_item_status(item, 'lambda') == 'supported'

    def test_no_dates_is_deprecated(self):
        # Only the deprecated table has rows without dates (very old runtimes)
        item = {'name': 'nodejs4.3'}
        assert categorize_item_status(item, 'lambda') == 'deprecated'


class TestElasticBeanstalkUnchanged:
    def test_retirement_date_in_past_is_end_of_life(self):
        item = {'retirement_date': _date(-10)}
        assert categorize_item_status(item, 'elasticbeanstalk') == 'end_of_life'

    def test_retirement_date_in_future_is_deprecated(self):
        item = {'retirement_date': _date(60)}
        assert categorize_item_status(item, 'elasticbeanstalk') == 'deprecated'

    def test_target_retirement_far_out_is_extended_support(self):
        item = {'target_retirement_date': _date(200)}
        assert categorize_item_status(item, 'elasticbeanstalk') == 'extended_support'

    def test_no_dates_is_extended_support(self):
        item = {'name': 'PHP 8.1'}
        assert categorize_item_status(item, 'elasticbeanstalk') == 'extended_support'
