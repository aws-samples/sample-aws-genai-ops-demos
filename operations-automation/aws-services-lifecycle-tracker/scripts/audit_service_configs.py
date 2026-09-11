#!/usr/bin/env python3
"""
Audit service_configs.json: does each documentation URL actually contain
lifecycle (deprecation / end-of-support) tables the extractor can work with?

For every enabled service and URL the script:
  1. fetches the page with the same session settings as the extractor,
  2. finds HTML tables the same way agent/data_extractor.py does,
  3. flags tables whose headers mention lifecycle or date terms,
  4. counts lifecycle keywords in the page text,
  5. (optional, --check-stored) loads the items currently stored in the facts
     table and reports how many identifiers actually occur on the page - a low
     ratio means the model invented rows.

Verdicts:
  OK        lifecycle table(s) found
  WEAK      tables found but none with lifecycle/date headers
  NO-TABLE  page has no tables at all (extractor sends nothing real to the model)
  ERROR     fetch failed

Usage:
    python scripts/audit_service_configs.py [--check-stored] [--service NAME ...]

Run from the demo root or the scripts/ folder. Needs: requests, beautifulsoup4
(and boto3 for --check-stored; region comes from your AWS CLI configuration).
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

LIFECYCLE_WORDS = re.compile(
    r"deprecat|retire|end[- ]of[- ](standard[- ])?(support|life)|extended support|"
    r"no longer supported|sunset|discontinu|block (function )?(create|update)",
    re.IGNORECASE,
)
DATE_HEADER_WORDS = re.compile(r"date|deprecat|retire|support|end|eol|lifecycle|expir", re.IGNORECASE)

USER_AGENT = "Mozilla/5.0 (compatible; AWS-Lifecycle-Tracker-Audit/1.0)"


def load_configs() -> dict:
    path = Path(__file__).parent / "service_configs.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)["services"]


def find_tables(soup: BeautifulSoup):
    """Same discovery order as DataExtractor._fetch_html_tables."""
    divs = soup.find_all("div", class_=["table-contents disable-scroll", "table-contents"])
    tables = [d.find("table") for d in divs]
    tables = [t for t in tables if t is not None]
    if not tables:
        tables = soup.find_all("table")
    return tables


def table_headers(table) -> list:
    first = table.find("tr")
    if not first:
        return []
    return [c.get_text(strip=True) for c in first.find_all(["th", "td"])]


def audit_url(session: requests.Session, url: str) -> dict:
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:  # network / 4xx / 5xx
        return {"url": url, "verdict": "ERROR", "detail": str(exc)[:120], "text": ""}

    soup = BeautifulSoup(resp.content, "html.parser")
    text = soup.get_text(" ", strip=True)
    tables = find_tables(soup)
    lifecycle_tables = []
    for t in tables:
        headers = table_headers(t)
        rows = len(t.find_all("tr")) - 1
        if rows > 0 and any(DATE_HEADER_WORDS.search(h) for h in headers):
            lifecycle_tables.append((headers, rows))

    keyword_hits = len(LIFECYCLE_WORDS.findall(text))
    if lifecycle_tables:
        verdict = "OK"
        detail = "; ".join(f"{rows} rows: {' | '.join(h)[:70]}" for h, rows in lifecycle_tables[:3])
    elif tables:
        verdict = "WEAK"
        detail = f"{len(tables)} table(s), none with lifecycle/date headers"
    else:
        verdict = "NO-TABLE"
        detail = "no HTML tables on page"
    return {"url": url, "verdict": verdict, "detail": detail, "keywords": keyword_hits, "text": text}


def stored_identifiers(service_name: str, table_name: str) -> list:
    import boto3
    from boto3.dynamodb.conditions import Key

    table = boto3.resource("dynamodb").Table(table_name)
    items, kwargs = [], {"KeyConditionExpression": Key("service_name").eq(service_name)}
    while True:
        page = table.query(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    # Each stored item yields the candidate strings that should appear on the
    # page if the row is real: identifier, name and the item_id suffix.
    candidates = []
    for it in items:
        ss = it.get("service_specific") or {}
        vals = {str(ss.get("identifier") or ""), str(ss.get("name") or ""), str(ss.get("version") or ""),
                it["item_id"].split("#", 1)[-1]}
        candidates.append([v for v in vals if v])
    return candidates


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "", s.lower())


def identifier_hit_ratio(candidates: list, page_text: str) -> tuple:
    """How many stored items have at least one candidate string on the page.

    Matching is done on a normalized form (lowercase, alphanumerics and dots
    only) so 'Python 3.8' matches 'python3.8' and slugs still match their
    source text. Parenthesised suffixes such as '(Extended Support)' are
    dropped first.
    """
    if not candidates:
        return 0, 0
    haystack = _norm(page_text)
    hits = 0
    for cands in candidates:
        variants = set()
        for c in cands:
            core = re.split(r"\s*[\(\[]", c)[0].strip()
            variants.add(core)
            # configs often prefix identifiers ("neptune-1.2.1.0", "lambda-edge-nodejs16");
            # the page shows the bare value, so also try without the leading word-
            variants.add(re.sub(r"^[a-z]+[-_]", "", core, flags=re.IGNORECASE))
        if any(len(_norm(v)) >= 3 and _norm(v) in haystack for v in variants):
            hits += 1
    return hits, len(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-stored", action="store_true",
                        help="compare identifiers stored in the facts table with the page text")
    parser.add_argument("--table", default="aws-services-lifecycle", help="facts table name (with --check-stored)")
    parser.add_argument("--service", nargs="*", help="only audit these service keys")
    parser.add_argument("--include-disabled", action="store_true")
    args = parser.parse_args()

    configs = load_configs()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    print(f"{'service':<18}{'verdict':<10}{'kw':>4}  {'stored-on-page':<15} url / detail")
    summary = {"OK": 0, "WEAK": 0, "NO-TABLE": 0, "ERROR": 0}
    for name, cfg in sorted(configs.items()):
        if args.service and name not in args.service:
            continue
        if not cfg.get("enabled", True) and not args.include_disabled:
            continue
        page_text = ""
        results = [audit_url(session, u) for u in cfg.get("documentation_urls", [])]
        for r in results:
            page_text += " " + r.get("text", "")
        # worst verdict across URLs (a service is only as good as its weakest page)
        order = ["OK", "WEAK", "NO-TABLE", "ERROR"]
        service_verdict = max((r["verdict"] for r in results), key=order.index) if results else "ERROR"
        summary[service_verdict] += 1

        ratio = ""
        if args.check_stored:
            try:
                hits, total = identifier_hit_ratio(stored_identifiers(name, args.table), page_text)
                ratio = f"{hits}/{total}" if total else "0 stored"
            except Exception as exc:
                ratio = f"err: {str(exc)[:12]}"

        for i, r in enumerate(results):
            head = f"{name:<18}{service_verdict:<10}{sum(x.get('keywords', 0) for x in results):>4}  {ratio:<15} " if i == 0 else " " * 49
            print(f"{head}{r['url']}")
            print(f"{'':<49}  -> {r['verdict']}: {r['detail']}")

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in summary.items()))
    return 0 if summary["NO-TABLE"] == 0 and summary["ERROR"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
