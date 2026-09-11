"""
Unit tests for normalize_date() / parse_date() and their use in
categorize_item_status() (issue #140).

AWS documentation spells dates several ways on the pages we extract from:
  Neptune            2027-06-03
  Lambda, Aurora MySQL   February 25, 2026
  Aurora PostgreSQL, RDS, DocumentDB   28 February 2027
  Aurora / RDS major-version calendars   April 2032   (month only)
The old code accepted only five strptime formats, so day-first and month-only
values never parsed and, for example, every Aurora PostgreSQL row - including
versions supported until 2030+ - was labelled 'deprecated'.
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from database_writes import normalize_date, parse_date, categorize_item_status


@pytest.mark.parametrize("raw, expected", [
    ("2027-06-03", "2027-06-03"),
    ("February 25, 2026", "2026-02-25"),
    ("28 February 2027", "2027-02-28"),
    ("29 February 2024", "2024-02-29"),
    ("Sept 3, 2026", "2026-09-03"),
    ("2026-07-27T00:00:00Z", "2026-07-27"),
    ("2026-07-27T00:00:00+00:00", "2026-07-27"),
    ("April 2032", "2032-04-30"),        # month only -> last day of month
    ("Feb 2027", "2027-02-28"),
    ("November 2023", "2023-11-30"),
    ("2032", "2032-12-31"),              # year only -> Dec 31
    ("03/31/2028", "2028-03-31"),        # US numeric
    ("  30 March 2026  ", "2026-03-30"),
])
def test_normalize_known_spellings(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "N/A", "n/a", "None", "null", "--", "-", "TBD", "To be determined",
    "Not announced", "no dates available", "not a date at all", "Version 5.0",
])
def test_normalize_placeholders_and_garbage_return_none(raw):
    assert normalize_date(raw) is None


def test_normalize_accepts_date_objects():
    assert normalize_date(datetime(2026, 9, 8, 12, 0)) == "2026-09-08"
    assert normalize_date(datetime(2026, 9, 8).date()) == "2026-09-08"


def test_parse_date_returns_date_object():
    d = parse_date("28 February 2027")
    assert (d.year, d.month, d.day) == (2027, 2, 28)
    assert parse_date("N/A") is None


# ---------------------------------------------------------------------------
# Effect on status categorization
# ---------------------------------------------------------------------------

def _days(n: int) -> datetime:
    return datetime.now(timezone.utc).date() + timedelta(days=n)


def _dayfirst(d) -> str:
    return d.strftime("%d %B %Y")


def _monthfirst(d) -> str:
    return d.strftime("%B %d, %Y")


def test_dayfirst_end_of_standard_support_far_out_is_supported():
    # The Aurora PostgreSQL 17/18 case: supported until 2030+, was 'deprecated'
    item = {"end_of_standard_support_date": _dayfirst(_days(1400))}
    assert categorize_item_status(item, "aurora") == "supported"


def test_dayfirst_end_of_standard_support_passed_is_extended_support():
    item = {"end_of_standard_support_date": _dayfirst(_days(-30))}
    assert categorize_item_status(item, "aurora") == "extended_support"


def test_monthfirst_and_dayfirst_give_same_verdict():
    d = _days(200)
    a = categorize_item_status({"end_of_support_date": _monthfirst(d)}, "rds")
    b = categorize_item_status({"end_of_support_date": _dayfirst(d)}, "rds")
    assert a == b == "extended_support"


def test_month_only_end_of_extended_support_in_past_is_end_of_life():
    last_month = (_days(-40)).strftime("%B %Y")
    item = {"end_of_extended_support_date": last_month}
    assert categorize_item_status(item, "rds") == "end_of_life"


def test_placeholder_dates_do_not_count_as_dates():
    # DocumentDB 4.0 / 5.0 rows: every date column says N/A -> supported
    item = {"end_of_standard_support_date": "N/A", "end_of_extended_support_date": "N/A",
            "release_date": "9 November 2020"}
    assert categorize_item_status(item, "documentdb") == "supported"
