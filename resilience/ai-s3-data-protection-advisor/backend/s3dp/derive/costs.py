"""Cost optimization items (estimates, never ranked above protection gaps)."""
from s3dp.model import GB, PRICE, PRICE_NOTE, size_label

IT_MONITORING_PER_1000 = 0.0025
TRANSITION_PER_1000 = 0.01


def items(buckets):
    out, savings = [], {}
    for b in buckets:
        cls = b.get("by_class") or {}
        std = cls.get("STANDARD", 0)
        if std < 100 * GB or set(cls) - {"STANDARD"} or b.get("transitions") is not False:
            continue
        gb = std / GB
        monthly = round(gb * (PRICE["STANDARD"] - PRICE["STANDARD_IA"]), 2)
        objs = b.get("objects")
        if objs is None:
            worst = one_time = None
            fees = "monitoring fee and transition cost not estimated (object count not measured)"
        else:
            worst = round(objs / 1000 * IT_MONITORING_PER_1000, 2)
            one_time = round(objs / 1000 * TRANSITION_PER_1000, 2)
            fees = f"monitoring fee up to ~${worst:,.0f}/month; ~${one_time:,.0f} one-time transition"
        out.append({"title": f"Evaluate S3 Intelligent-Tiering for {b['name']} ({size_label(b.get('size_tb'))} in S3 Standard)",
                    "resources": [b["name"]], "impact": "saving", "effort": "low", "up_to": True,
                    "monthly_usd": monthly, "one_time_usd": one_time, "worst_case_usd": worst,
                    "savings": f"Up to ~${monthly:,.0f}/month if every object becomes infrequently accessed; "
                               f"{fees}, estimate",
                    "description": "Access pattern is not visible in storage metrics. Intelligent-Tiering keeps "
                                   "millisecond access and has no retrieval fee, so the downside is the monitoring fee.",
                    "basis": f"STANDARD minus STANDARD_IA list price per GB-month, all bytes ({PRICE_NOTE})"})
        savings[b["name"]] = {"monthly_usd": monthly,
                              "action": f"Evaluate S3 Intelligent-Tiering (save up to ~${monthly:,.0f}/month)"}
    return out, savings
