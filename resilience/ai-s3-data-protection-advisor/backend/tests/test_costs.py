import pytest

from s3dp.derive import costs

GB = 1024 ** 3


def test_intelligent_tiering_candidate():
    b = {"name": "research-data", "by_class": {"STANDARD": 10_000 * GB}, "transitions": False, "objects": 2_000_000,
         "size_tb": 10_000 / 1024, "role": "production"}
    items, savings = costs.items([b])
    assert len(items) == 1 and items[0]["impact"] == "saving"
    assert items[0]["monthly_usd"] == pytest.approx(10_000 * (0.023 - 0.0125))
    assert items[0]["worst_case_usd"] == pytest.approx(2_000_000 / 1000 * 0.0025)
    assert savings["research-data"]["monthly_usd"] == items[0]["monthly_usd"] == round(items[0]["monthly_usd"], 2)
    assert "US East (N. Virginia) list prices" in items[0]["basis"]


def test_small_or_tiered_buckets_are_skipped():
    small = {"name": "s", "by_class": {"STANDARD": 50 * GB}, "transitions": False, "objects": 10, "size_tb": 0.05,
             "role": "production"}
    tiered = {"name": "t", "by_class": {"STANDARD": 500 * GB, "INTELLIGENT_TIERING": 1 * GB}, "transitions": False,
              "objects": 10, "size_tb": 0.5, "role": "production"}
    unread = {"name": "u", "by_class": {"STANDARD": 500 * GB}, "transitions": None, "objects": 10, "size_tb": 0.5,
              "role": "production"}
    assert costs.items([small, tiered, unread]) == ([], {})


def test_unmeasured_object_count_is_not_zero_cost():
    b = {"name": "big", "by_class": {"STANDARD": 1000 * GB}, "transitions": False, "objects": None,
         "size_tb": 1000 / 1024, "role": "production"}
    items, _ = costs.items([b])
    assert items[0]["worst_case_usd"] is None and items[0]["one_time_usd"] is None
    assert "monitoring fee and transition cost not estimated (object count not measured)" in items[0]["savings"]
