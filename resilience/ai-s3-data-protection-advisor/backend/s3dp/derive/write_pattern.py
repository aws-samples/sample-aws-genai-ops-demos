"""Write pattern classification and versioning cost estimate (VCE). Estimates only: labelled with their basis and
confidence. "Static" (0% churn) needs size evidence as well as a flat object count: a missing or too-short size
series leaves the pattern unknown."""
from s3dp.model import GB, PRICE, TIB

RETENTION_DAYS = 30
SIZE_CHANGE = 0.01          # size moved more than 1% over the window
LARGE_TB = 10               # prior-basis estimates above this size are low confidence


def size_changed(size_series):
    """True/False: total size moved more than 1% over the window; None when the series is missing or too short."""
    s = [x for x in size_series or [] if x is not None]
    if len(s) < 2:
        return None
    return (max(s) - min(s)) / max(s[0], 1) > SIZE_CHANGE


def _dips(s):
    return sum(a - b for a, b in zip(s, s[1:]) if b < a)


def _requests_complete(requests, s):
    """Puts below the net object growth mean the request series is incomplete."""
    growth = (s[-1] - s[0]) if len(s) >= 2 else 0
    return requests.get("puts", 0) >= growth


def _storage_pattern(s, size_change):
    """Pattern from storage metrics only. size_change: True/False/None (see size_changed)."""
    if len(s) < 2:
        return "unknown_pattern"
    growth = (s[-1] - s[0]) / max(s[0], 1)
    if abs(growth) <= 0.01 and _dips(s) == 0:
        return "static_or_inactive" if size_change is False else "unknown_pattern"
    if all(b >= a for a, b in zip(s, s[1:])):
        return "append_likely"
    return "unknown_pattern"


def classify(objects_series, size_series, requests):
    """Pattern from 30 days of daily metrics. size_series (daily total bytes) is required to call a bucket static."""
    s = [x for x in objects_series if x is not None]
    if requests:
        puts, deletes, gets = requests.get("puts", 0), requests.get("deletes", 0), requests.get("gets", 0)
        if puts / 30 < 1 and deletes / 30 < 1:
            return {"pattern": "inactive" if gets == 0 else "read_mostly_static", "approximate": False}
        if len(s) < 2:
            return {"pattern": "unknown_pattern", "approximate": True}
        if _requests_complete(requests, s):
            growth = s[-1] - s[0]
            if growth > 0 and growth >= puts * 0.7:
                return {"pattern": "write_once_append" if deletes <= max(1.0, 0.01 * puts) else "high_churn",
                        "approximate": False}
            if abs(growth) <= 0.05 * max(s[0], 1) and deletes < puts * 0.1:
                return {"pattern": "overwrite_heavy", "approximate": False}
            return {"pattern": "high_churn", "approximate": False}
    return {"pattern": _storage_pattern(s, size_changed(size_series)), "approximate": True}


SENTENCE = {
    "static_or_inactive": "Static: object count and size stable over 30 days (storage metrics only)",
    "append_likely": "Append: object count only grows over 30 days (storage metrics only)",
    "unknown_pattern": "Unknown: storage metrics cannot rule out overwrites",
    "inactive": "Inactive: no requests over 30 days",
    "read_mostly_static": "Read-mostly: reads, no writes over 30 days",
    "write_once_append": "Write-once: new objects only, no deletes over 30 days",
    "overwrite_heavy": "Overwrite-heavy: writes replace existing objects",
    "high_churn": "High churn: frequent writes and deletes",
}


def _monthly(by_class_bytes, churn):
    return sum(churn * (b / GB) * PRICE.get(c, PRICE["STANDARD"]) * RETENTION_DAYS / 30
               for c, b in by_class_bytes.items())


def _out(m, rng, churn, basis, confidence):
    return {"monthly_usd": round(m, 2), "range": [round(x, 2) for x in rng], "churn_pct": round(churn * 100, 2),
            "basis": basis, "confidence": confidence, "retention_days": RETENTION_DAYS}


def vce(by_class_bytes, objects_series, requests, size_series_changed=None, size_tb=None):
    """{'monthly_usd', 'range', 'churn_pct' (percent), 'basis', 'confidence', 'retention_days'} or None without
    metrics. size_series_changed: True/False, or None when the size series is missing or too short (unknown).
    size_tb defaults to the sum of by_class_bytes."""
    s = [x for x in objects_series if x is not None]
    if not by_class_bytes or not s:
        return None
    if size_tb is None:
        size_tb = sum(by_class_bytes.values()) / TIB
    mean = sum(s) / len(s)
    if requests and mean > 0 and _requests_complete(requests, s):
        churn = max(0.0, (requests.get("puts", 0) - max(0.0, s[-1] - s[0]) + requests.get("deletes", 0)) / mean)
        m = _monthly(by_class_bytes, churn)
        return _out(m, [m, m], churn, "measured", "high")
    dips = _dips(s)
    if dips > 0 and mean > 0:
        churn = dips / mean + 0.01        # plus 1 point for overwrites invisible in storage metrics
        m = _monthly(by_class_bytes, churn)
        return _out(m, [_monthly(by_class_bytes, dips / mean), 2 * m], churn, "bounded", "medium")
    pattern = _storage_pattern(s, size_series_changed)
    if pattern == "unknown_pattern":
        m = _monthly(by_class_bytes, 0.10)
        return _out(m, [_monthly(by_class_bytes, 0.02), _monthly(by_class_bytes, 0.25)], 0.10, "prior", "low")
    churn = 0.0 if pattern == "static_or_inactive" else 0.01
    m = _monthly(by_class_bytes, churn)
    return _out(m, [m, m], churn, "prior", "low" if size_tb > LARGE_TB else "medium")
