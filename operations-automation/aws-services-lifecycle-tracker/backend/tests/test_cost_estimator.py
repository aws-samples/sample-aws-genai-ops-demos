"""
Unit tests for the RDS/Aurora Extended Support cost estimate (cost_estimator.py, #142).

Offline: Price List and EC2 clients are fakes serving canned answers.
"""
import json
from datetime import date

import pytest

from cost_estimator import (
    PriceBook, VcpuBook, estimate_resource, forecast_12_months, major_version, row_exposure,
)


def _sku(usagetype, usd):
    return json.dumps({
        "product": {"attributes": {"usagetype": usagetype}},
        "terms": {"OnDemand": {"k1": {"priceDimensions": {"k1.d": {"pricePerUnit": {"USD": str(usd)}}}}}},
    })


class _Paginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        year = next(f["Value"] for f in kwargs["Filters"] if f["Field"] == "extendedSupportPricingYear")
        return iter(self._pages.get(year, []))


class FakePricing:
    def __init__(self):
        self._pages = {
            "Year 1, Year 2": [{"PriceList": [
                _sku("EUC1-ExtendedSupport:Yr1-Yr2:PostgreSQL11", 0.122),
                _sku("EUC1-ExtendedSupport:Yr1-Yr2:MySQL5.7", 0.122),
                _sku("EUC1-ExtendedSupport:Yr1-Yr2:ASv2:AuroraPostgreSQL13", 0.104),
                _sku("EUC1-ASv2-ExtendedSupport:Yr1-Yr2:AuroraPostgreSQL14", 0.104),
            ]}],
            "Year 3": [{"PriceList": [
                _sku("EUC1-ExtendedSupport:Yr3:PostgreSQL11", 0.244),
                _sku("EUC1-ExtendedSupport:Yr3:ASv2:AuroraPostgreSQL13", 0.207),
            ]}],
        }

    def get_paginator(self, name):
        assert name == "get_products"
        return _Paginator(self._pages)


class FakeEc2:
    VCPUS = {"t4g.micro": 2, "r6g.xlarge": 4}

    def describe_instance_types(self, InstanceTypes):
        t = InstanceTypes[0]
        if t not in self.VCPUS:
            raise Exception("InvalidInstanceType")
        return {"InstanceTypes": [{"InstanceType": t, "VCpuInfo": {"DefaultVCpus": self.VCPUS[t]}}]}


@pytest.fixture
def books():
    return PriceBook("eu-central-1", pricing_client=FakePricing()), VcpuBook("eu-central-1", ec2_client=FakeEc2())


def test_major_version_follows_rds_conventions():
    assert major_version("mysql", "8.4.5") == "8.4"
    assert major_version("mysql", "5.7.44") == "5.7"
    assert major_version("postgres", "14.18") == "14"
    assert major_version("aurora-postgresql", "14.17") == "14"
    assert major_version("aurora-mysql", "8.0.mysql_aurora.3.08.2") == "3"
    assert major_version("aurora-mysql", "5.7.mysql_aurora.2.12.6") == "2"


def test_pricebook_parses_usagetypes_and_falls_back_to_family(books):
    prices, _ = books
    exact = prices.lookup("PostgreSQL", "11", False)
    assert exact == {"yr1_2": 0.122, "yr3": 0.244, "source": "sku"}
    # no SKU for PostgreSQL 14 provisioned -> family rate, flagged
    fam = prices.lookup("PostgreSQL", "14", False)
    assert fam["yr1_2"] == 0.122 and fam["yr3"] == 0.244 and fam["source"] == "family-estimate"
    # both ASv2 usagetype layouts are recognised; missing Yr3 falls back to Yr1-2
    assert prices.lookup("AuroraPostgreSQL", "14", True) == {"yr1_2": 0.104, "yr3": 0.104, "source": "sku"}
    assert prices.lookup("Oracle", "19", False) is None


def test_provisioned_multi_az_doubles_vcpus(books):
    prices, vcpus = books
    row = {"end_of_support_date": "2027-02-28", "end_of_extended_support_date": "2030-02-28"}
    est = estimate_resource({"engine": "postgres", "engine_version": "14.18", "instance_class": "db.r6g.xlarge", "multi_az": True},
                            prices, vcpus, row, today=date(2026, 9, 11))
    assert est["eligible"] and est["billable_vcpus"] == 8 and est["unit"] == "vCPU-hour"
    assert est["monthly_yr1_2"] == pytest.approx(8 * 0.122 * 730, abs=0.01)
    assert est["monthly_yr3"] == pytest.approx(8 * 0.244 * 730, abs=0.01)
    assert est["extended_support_start"] == "2027-03-01" and est["year3_start"] == "2029-03-01"
    assert est["in_extended_support"] is False
    # Sep 2026 -> Aug 2027 window: Mar..Aug 2027 billable = 6 months at Yr1-2 rate
    assert est["forecast_12m"] == pytest.approx(6 * 8 * 0.122 * 730, abs=0.5)


def test_serverless_v2_prices_acu_range(books):
    prices, vcpus = books
    row = {"end_of_support_date": "2026-06-30", "end_of_extended_support_date": "2029-06-30"}
    est = estimate_resource({"engine": "aurora-postgresql", "engine_version": "14.17", "instance_class": "db.serverless",
                             "serverless_v2": {"min_acu": 0.5, "max_acu": 4}}, prices, vcpus, row, today=date(2026, 9, 11))
    assert est["eligible"] and est["unit"] == "ACU-hour" and est["serverless"] is True
    assert est["monthly_yr1_2_min"] == pytest.approx(0.5 * 0.104 * 730, abs=0.01)
    assert est["monthly_yr1_2"] == pytest.approx(4 * 0.104 * 730, abs=0.01)
    assert est["in_extended_support"] is True  # standard support ended June 2026
    assert est["forecast_12m"] == pytest.approx(12 * 4 * 0.104 * 730, abs=0.5)


def test_engines_without_extended_support_and_unknown_classes(books):
    prices, vcpus = books
    maria = estimate_resource({"engine": "mariadb", "engine_version": "10.6.22", "instance_class": "db.t4g.micro"}, prices, vcpus, None)
    assert maria["eligible"] is False and maria["reason"] == "no_extended_support"
    odd = estimate_resource({"engine": "mysql", "engine_version": "5.7.44", "instance_class": "db.zz.huge"}, prices, vcpus, None)
    assert odd["eligible"] is False and odd["reason"] == "unknown_instance_class"


def test_forecast_respects_extended_support_end_and_year3():
    # standard end 2024-06-30 -> Yr3 from 2026-07-01; extended end 2027-06-30
    f = forecast_12_months(100.0, 200.0, date(2024, 6, 30), date(2027, 6, 30), today=date(2026, 9, 11))
    assert f == pytest.approx(10 * 200.0, abs=0.5)  # Sep 2026..Jun 2027 at Yr3, Jul/Aug 2027 free
    assert forecast_12_months(100.0, 200.0, None, None) == 0.0


def test_row_exposure_aggregates_only_priced_resources():
    details = [
        {"extended_support": {"eligible": True, "monthly_yr1_2": 10.0, "monthly_yr3": 20.0, "forecast_12m": 60.0, "in_extended_support": True, "price_source": "sku"}},
        {"extended_support": {"eligible": True, "monthly_yr1_2": 5.0, "monthly_yr3": 10.0, "forecast_12m": 0.0, "price_source": "family-estimate"}},
        {"extended_support": {"eligible": False, "reason": "no_extended_support"}},
    ]
    agg = row_exposure(details)
    assert agg == {"currency": "USD", "resources_priced": 2, "resources_total": 3, "monthly": 15.0, "monthly_yr3": 30.0,
                   "forecast_12m": 60.0, "in_extended_support": 1, "estimated": 1}


def test_leap_day_end_of_support_does_not_crash(books):
    prices, vcpus = books
    row = {'end_of_support_date': '2028-02-29', 'end_of_extended_support_date': '2031-02-28'}
    est = estimate_resource({'engine': 'mysql', 'engine_version': '8.0.40', 'instance_class': 'db.t4g.micro'}, prices, vcpus, row, today=date(2026, 9, 11))
    assert est['eligible'] and est['extended_support_start'] == '2028-03-01' and est['year3_start'] == '2030-03-01'
    assert est['forecast_12m'] == 0.0  # nothing billable before Mar 2028

