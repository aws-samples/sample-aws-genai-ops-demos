"""
RDS / Aurora Extended Support cost exposure (issue #142).

When a major engine version leaves RDS standard support, instances still on it
are billed *Extended Support* on top of their normal price: per vCPU-hour for
provisioned instances, per ACU-hour for Aurora Serverless v2, with a higher
rate from the third year. Teams that miss the date find it on the next bill.

This module prices that surcharge for every scanned RDS/Aurora resource:

  monthly = vCPUs x (2 if Multi-AZ) x $/vCPU-hour x 730        (provisioned)
  monthly = ACU x $/ACU-hour x 730, as a min..max range        (Serverless v2)

Inputs are live, never hardcoded:
  - prices:  AWS Price List Query API (AmazonRDS, extendedSupportPricingYear)
             for the scanned region; versions without their own SKU yet are
             priced at the engine family's rate and flagged 'family-estimate'
  - vCPUs:   ec2:DescribeInstanceTypes on the class minus its 'db.' prefix
  - dates:   the catalog's MAJOR version row (end of standard support, end of
             extended support) through LifecycleIndex, so the forecast follows
             the extraction pipeline

Adapted from aws-samples/rds-extended-support-cost-estimator, whose formula is
kept but whose hardcoded dates and instance map are replaced by the above.
"""
import json
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import boto3

HOURS_PER_MONTH = 730
FORECAST_MONTHS = 12

# RDS engine -> (Price List usagetype family, catalog service key, catalog engine slug)
EXTENDED_SUPPORT_ENGINES = {
    "mysql": ("MySQL", "rds", "mysql"),
    "postgres": ("PostgreSQL", "rds", "postgresql"),
    "aurora-mysql": ("AuroraMySQL", "aurora", "aurora-mysql"),
    "aurora-postgresql": ("AuroraPostgreSQL", "aurora", "aurora-postgresql"),
}

# Engines RDS does not offer Extended Support for: end of standard support means
# a forced upgrade (no surcharge, but no grace period either).
NO_EXTENDED_SUPPORT_HINT = "RDS offers no Extended Support for this engine: it is upgraded automatically at end of standard support"

_USAGETYPE = re.compile(r"ExtendedSupport:(Yr1-Yr2|Yr3):(?:ASv2:)?([A-Za-z]+?)(\d[\d.]*)$")


def major_version(engine: str, version: str) -> str:
    """The version level RDS attaches Extended Support (and its dates) to.

    mysql 8.4.5 -> 8.4 ; postgres 14.18 -> 14 ; aurora-postgresql 14.17 -> 14 ;
    aurora-mysql 8.0.mysql_aurora.3.08.2 -> 3 ; aurora-mysql 8.4.mysql_aurora... -> 8.4
    """
    if "mysql_aurora." in version:
        aurora = version.split("mysql_aurora.", 1)[1]
        return aurora.split(".")[0] if aurora.startswith("2") or aurora.startswith("3") else ".".join(aurora.split(".")[:2])
    parts = version.split(".")
    if engine in ("postgres", "aurora-postgresql"):
        return parts[0]
    return ".".join(parts[:2]) if len(parts) >= 2 else parts[0]


def pricing_api_region(region: str) -> str:
    """Region hosting the Price List Query API for `region`'s partition.

    The API is served from a few regions per partition (botocore knows which);
    prices for ANY region can be queried from any of them via regionCode.
    Prefer the deployment region itself, then one in the same geography, then
    the first available.
    """
    session = boto3.session.Session()
    partition = session.get_partition_for_region(region)
    hosts = session.get_available_regions("pricing", partition_name=partition)
    if region in hosts:
        return region
    geo = region.split("-")[0]
    same_geo = [h for h in hosts if h.startswith(f"{geo}-")]
    return (same_geo or hosts)[0]


class PriceBook:
    """Extended Support unit prices for one region, loaded once per scan.

    prices[(family, major, serverless, year)] = USD per vCPU-hour / ACU-hour
    """

    def __init__(self, region: str, pricing_client=None):
        self.region = region
        self.prices: Dict[tuple, float] = {}
        self.available = False
        self.error: Optional[str] = None
        self._client = pricing_client
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        try:
            client = self._client or boto3.client("pricing", region_name=pricing_api_region(self.region))
            for year in ("Year 1, Year 2", "Year 3"):
                pages = client.get_paginator("get_products").paginate(
                    ServiceCode="AmazonRDS",
                    Filters=[
                        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": self.region},
                        {"Type": "TERM_MATCH", "Field": "extendedSupportPricingYear", "Value": year},
                    ])
                for page in pages:
                    for raw in page.get("PriceList", []):
                        sku = json.loads(raw) if isinstance(raw, str) else raw
                        usagetype = sku.get("product", {}).get("attributes", {}).get("usagetype", "")
                        m = _USAGETYPE.search(usagetype)
                        if not m:
                            continue
                        yr, family, major = m.group(1), m.group(2), m.group(3)
                        serverless = "ASv2" in usagetype
                        price = _on_demand_usd(sku)
                        if price is None:
                            continue
                        self.prices[(family, major, serverless, yr)] = price
            self.available = bool(self.prices)
            if not self.available:
                self.error = f"No Extended Support SKUs published for {self.region}"
        except Exception as e:
            self.available = False
            self.error = f"{type(e).__name__}: {str(e)[:160]}"

    def lookup(self, family: str, major: str, serverless: bool) -> Optional[Dict]:
        """Unit prices for a family/major, falling back to the family's rate."""
        if not self._loaded:
            self._load()
        if not self.available:
            return None
        exact12 = self.prices.get((family, major, serverless, "Yr1-Yr2"))
        exact3 = self.prices.get((family, major, serverless, "Yr3"))
        if exact12 is not None:
            return {"yr1_2": exact12, "yr3": exact3 if exact3 is not None else exact12, "source": "sku"}
        # No SKU for this major yet: use the family's published rate (all
        # majors of a family have carried the same price so far).
        fam12 = [p for (f, _, s, y), p in self.prices.items() if f == family and s == serverless and y == "Yr1-Yr2"]
        fam3 = [p for (f, _, s, y), p in self.prices.items() if f == family and s == serverless and y == "Yr3"]
        if fam12:
            return {"yr1_2": max(fam12), "yr3": max(fam3) if fam3 else max(fam12), "source": "family-estimate"}
        return None


def _on_demand_usd(sku: Dict) -> Optional[float]:
    try:
        term = next(iter(sku["terms"]["OnDemand"].values()))
        dim = next(iter(term["priceDimensions"].values()))
        usd = dim["pricePerUnit"].get("USD")
        return float(usd) if usd is not None else None
    except (KeyError, StopIteration, TypeError, ValueError):
        return None


class VcpuBook:
    """vCPU count per DB instance class via EC2 (db.r6g.large -> r6g.large)."""

    def __init__(self, region: str, ec2_client=None):
        self._client = ec2_client or boto3.client("ec2", region_name=region)
        self._cache: Dict[str, Optional[int]] = {}

    def vcpus(self, instance_class: str) -> Optional[int]:
        if instance_class in self._cache:
            return self._cache[instance_class]
        ec2_type = instance_class[3:] if instance_class.startswith("db.") else instance_class
        value = None
        try:
            resp = self._client.describe_instance_types(InstanceTypes=[ec2_type])
            types = resp.get("InstanceTypes", [])
            if types:
                value = int(types[0]["VCpuInfo"]["DefaultVCpus"])
        except Exception:
            value = None
        self._cache[instance_class] = value
        return value


def _parse_date(value) -> Optional[date]:
    if not value or str(value) in ("N/A", "None", "To be determined"):
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return d.replace(year=d.year + y, month=m + 1, day=1)


def _add_years(d: date, years: int) -> date:
    """d + years, clamping Feb 29 to Feb 28 when the target year is not leap."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def forecast_12_months(monthly_yr1_2: float, monthly_yr3: float, standard_end: Optional[date],
                       extended_end: Optional[date], today: Optional[date] = None) -> float:
    """Surcharge over the next 12 calendar months if nothing changes.

    Extended Support (and billing) starts the day after standard support ends;
    the Year 3 rate applies two years after that; nothing is billed once the
    extended window closes (the version is upgraded by AWS).
    """
    if standard_end is None:
        return 0.0
    today = today or date.today()
    es_start = standard_end + timedelta(days=1)
    yr3_start = _add_years(es_start, 2)
    total = 0.0
    month = today.replace(day=1)
    for _ in range(FORECAST_MONTHS):
        month_end = _add_months(month, 1) - timedelta(days=1)
        billable_from = max(month, es_start)
        billable_to = month_end if extended_end is None else min(month_end, extended_end)
        if billable_to >= billable_from:
            fraction = ((billable_to - billable_from).days + 1) / ((month_end - month).days + 1)
            rate = monthly_yr3 if billable_from >= yr3_start else monthly_yr1_2
            total += rate * fraction
        month = _add_months(month, 1)
    return round(total, 2)


def estimate_resource(res: Dict, prices: PriceBook, vcpu_book: VcpuBook, major_row: Optional[Dict],
                      today: Optional[date] = None) -> Dict:
    """Extended Support estimate for one scanned RDS/Aurora resource."""
    engine = res.get("engine", "")
    version = res.get("engine_version", "")
    out: Dict = {"eligible": False, "currency": "USD"}
    if engine not in EXTENDED_SUPPORT_ENGINES:
        out.update(reason="no_extended_support", note=NO_EXTENDED_SUPPORT_HINT)
        return out
    family, _, _ = EXTENDED_SUPPORT_ENGINES[engine]
    major = major_version(engine, version)
    serverless = res.get("instance_class", "") == "db.serverless" or bool(res.get("serverless_v2"))
    out.update(engine_family=family, major_version=major, serverless=serverless,
               instance_class=res.get("instance_class", ""), multi_az=bool(res.get("multi_az", False)))

    standard_end = _parse_date((major_row or {}).get("end_of_support_date"))
    extended_end = _parse_date((major_row or {}).get("end_of_extended_support_date"))
    out.update(standard_support_end=standard_end.isoformat() if standard_end else None,
               extended_support_start=(standard_end + timedelta(days=1)).isoformat() if standard_end else None,
               year3_start=_add_years(standard_end + timedelta(days=1), 2).isoformat() if standard_end else None,
               extended_support_end=extended_end.isoformat() if extended_end else None)

    unit = prices.lookup(family, major, serverless)
    if unit is None:
        out.update(reason="no_price", note=prices.error or f"No Extended Support price found for {family} {major}")
        return out
    out.update(unit="ACU-hour" if serverless else "vCPU-hour", price_yr1_2=unit["yr1_2"], price_yr3=unit["yr3"],
               price_source=unit["source"])

    if serverless:
        cap = res.get("serverless_v2") or {}
        min_acu, max_acu = float(cap.get("min_acu", 0)), float(cap.get("max_acu", 0))
        out.update(min_acu=min_acu, max_acu=max_acu,
                   monthly_yr1_2_min=round(min_acu * unit["yr1_2"] * HOURS_PER_MONTH, 2),
                   monthly_yr1_2=round(max_acu * unit["yr1_2"] * HOURS_PER_MONTH, 2),
                   monthly_yr3=round(max_acu * unit["yr3"] * HOURS_PER_MONTH, 2))
        if max_acu == 0:
            out.update(reason="no_capacity", note="Serverless v2 capacity unknown; exposure computed as 0")
    else:
        vcpus = vcpu_book.vcpus(out["instance_class"]) if out["instance_class"] else None
        if vcpus is None:
            out.update(reason="unknown_instance_class", note=f"vCPU count unknown for {out['instance_class']}")
            return out
        billable = vcpus * (2 if out["multi_az"] else 1)
        out.update(vcpus=vcpus, billable_vcpus=billable,
                   monthly_yr1_2=round(billable * unit["yr1_2"] * HOURS_PER_MONTH, 2),
                   monthly_yr3=round(billable * unit["yr3"] * HOURS_PER_MONTH, 2))

    out["eligible"] = True
    if standard_end is None:
        out.update(note="End of standard support not in the catalog: monthly rate shown, no dated forecast")
    out["forecast_12m"] = forecast_12_months(out["monthly_yr1_2"], out["monthly_yr3"], standard_end, extended_end, today)
    today = today or date.today()
    out["in_extended_support"] = bool(standard_end and today > standard_end and (extended_end is None or today <= extended_end))
    return out


def row_exposure(details: List[Dict]) -> Dict:
    """Aggregate per-resource estimates into the row-level figure the UI sorts on."""
    priced = [d.get("extended_support") for d in details if (d.get("extended_support") or {}).get("eligible")]
    return {
        "currency": "USD",
        "resources_priced": len(priced),
        "resources_total": len(details),
        "monthly": round(sum(p.get("monthly_yr1_2", 0) for p in priced), 2),
        "monthly_yr3": round(sum(p.get("monthly_yr3", 0) for p in priced), 2),
        "forecast_12m": round(sum(p.get("forecast_12m", 0) for p in priced), 2),
        "in_extended_support": sum(1 for p in priced if p.get("in_extended_support")),
        "estimated": sum(1 for p in priced if p.get("price_source") == "family-estimate"),
    }
