import pytest

from s3dp.derive import write_pattern as wp

GB = 1024 ** 3


def test_static_bucket_costs_zero():
    p = wp.classify([1000.0] * 30, [100 * GB] * 30, None)
    assert p["pattern"] == "static_or_inactive" and p["approximate"] is True
    v = wp.vce({"STANDARD": 100 * GB}, [1000.0] * 30, None, size_series_changed=False)
    assert v["basis"] == "prior" and v["churn_pct"] == 0 and v["monthly_usd"] == 0
    assert v["confidence"] == "medium"


def test_append_only_prior_one_percent():
    series = [1000.0 + 10 * i for i in range(30)]
    assert wp.classify(series, None, None)["pattern"] == "append_likely"
    v = wp.vce({"STANDARD": 1000 * GB}, series, None)
    assert v["basis"] == "prior" and v["churn_pct"] == 1.0
    assert v["monthly_usd"] == pytest.approx(1000 * 0.01 * 0.023, rel=1e-3)


def test_dips_give_bounded_estimate():
    series = [1000.0] * 10 + [950.0] + [1000.0] * 19
    v = wp.vce({"STANDARD": 1000 * GB}, series, None)
    assert v["basis"] == "bounded"
    assert v["churn_pct"] == pytest.approx(100 * (50 / 998.33 + 0.01), rel=1e-2)
    assert v["range"][0] == pytest.approx(1000 * (50 / (29950 / 30)) * 0.023, abs=0.01)
    assert v["range"][1] == pytest.approx(2 * v["monthly_usd"], abs=0.01)
    assert v["confidence"] == "medium"


def test_request_metrics_give_measured_estimate():
    v = wp.vce({"GLACIER": 1000 * GB}, [1000.0] * 30, {"puts": 100.0, "deletes": 50.0, "gets": 0.0})
    assert v["basis"] == "measured" and v["churn_pct"] == pytest.approx(15.0)
    assert v["monthly_usd"] == pytest.approx(1000 * 0.15 * 0.0036, rel=1e-3)
    assert v["confidence"] == "high"


def test_unknown_pattern_has_range():
    series = [1000.0, 1000.0, 1000.0]
    v = wp.vce({"STANDARD": 1000 * GB}, series, None, size_series_changed=True)
    assert v["basis"] == "prior" and v["churn_pct"] == 10.0 and v["range"] == pytest.approx([0.46, 5.75])


def test_no_metrics_no_estimate():
    assert wp.vce({}, [], None) is None


def test_incomplete_request_series_falls_back_to_storage_metrics():
    series = [1000.0 + 10 * i for i in range(30)]              # net growth 290 objects
    v = wp.vce({"STANDARD": 1000 * GB}, series, {"puts": 100.0, "deletes": 0.0, "gets": 0.0})
    assert v["basis"] == "prior" and v["churn_pct"] == 1.0
    assert wp.classify(series, None, {"puts": 100.0, "deletes": 0.0, "gets": 0.0})["pattern"] == "append_likely"


def test_measured_churn_is_never_negative():
    v = wp.vce({"STANDARD": 1000 * GB}, [1000.0, 1000.0], {"puts": 0.0, "deletes": 0.0, "gets": 5.0})
    assert v["churn_pct"] >= 0 and v["monthly_usd"] >= 0


def test_static_needs_size_evidence():
    flat = [1000.0] * 30
    assert wp.classify(flat, None, None)["pattern"] == "unknown_pattern"
    assert wp.classify(flat, [5 * GB], None)["pattern"] == "unknown_pattern"
    assert wp.classify(flat, [5 * GB] * 15 + [6 * GB] * 15, None)["pattern"] == "unknown_pattern"
    v = wp.vce({"STANDARD": 100 * GB}, flat, None)
    assert v["churn_pct"] == 10.0 and v["range"] == pytest.approx([0.05, 0.57], abs=0.01) and v["confidence"] == "low"
    assert wp.vce({"STANDARD": 100 * GB}, flat, None, size_series_changed=True)["churn_pct"] == 10.0
    assert wp.size_changed([100.0, 100.5]) is False and wp.size_changed([100.0, 102.0]) is True
    assert wp.size_changed([100.0]) is None and wp.size_changed([]) is None


def test_dips_are_not_static():
    assert wp.classify([1000.0, 500.0, 1000.0], [GB] * 3, None)["pattern"] == "unknown_pattern"


def test_request_patterns():
    req = {"puts": 300.0, "deletes": 0.0, "gets": 0.0}
    assert wp.classify([], None, req)["pattern"] == "unknown_pattern"
    grow = [1000.0, 1300.0]
    assert wp.classify(grow, None, req)["pattern"] == "write_once_append"
    assert wp.classify(grow, None, dict(req, deletes=60.0))["pattern"] == "high_churn"


def test_prior_on_large_buckets_is_low_confidence():
    series = [1000.0 + 10 * i for i in range(30)]
    assert wp.vce({"STANDARD": 20 * 1024 * GB}, series, None)["confidence"] == "low"
    assert wp.vce({"STANDARD": GB}, series, None, size_tb=11)["confidence"] == "low"
    assert wp.vce({"STANDARD": GB}, series, None)["confidence"] == "medium"
