#!/usr/bin/env python3
"""Reduce ASH's GitHub SARIF to the findings the merge gate acts on.

Keeps a result only when:
  - it is not from Checkov (ASH scores every Checkov rule 8.0, so severity can't
    separate real risks from hygiene; Checkov stays in the ASH job summary), and
  - its rule's security-severity is >= 7.0 (GitHub's "high" and "critical").
A rule with no security-severity is kept when its level is "error", so a scoring
gap reports a finding instead of hiding it.

Usage: ash-gate-filter.py <in.sarif> <out.sarif>
"""
import json
import sys
from collections import Counter

HIGH = 7.0


def scanner(rule_id):
    rid = rule_id or ""
    if rid.startswith("CKV"):
        return "checkov"
    if rid.startswith("SECRET"):
        return "detect-secrets"
    if rid[:1] == "B" and rid[1:].isdigit():
        return "bandit"
    if rid.startswith(("CVE-", "GHSA-")):
        return "grype"
    return "semgrep/opengrep"


def main(src, dst):
    sarif = json.load(open(src, encoding="utf-8"))
    kept, dropped = Counter(), Counter()
    for run in sarif.get("runs", []):
        rules = {r.get("id"): r for r in run.get("tool", {}).get("driver", {}).get("rules", [])}
        out = []
        for res in run.get("results", []):
            rid = res.get("ruleId")
            sev = rules.get(rid, {}).get("properties", {}).get("security-severity")
            try:
                high = float(sev) >= HIGH
            except (TypeError, ValueError):
                high = res.get("level") == "error"
            keep = scanner(rid) != "checkov" and high
            (kept if keep else dropped)[scanner(rid)] += 1
            if keep:
                out.append(res)
        run["results"] = out
    json.dump(sarif, open(dst, "w", encoding="utf-8"))
    print(f"kept {sum(kept.values())}: {dict(kept)}")
    print(f"dropped {sum(dropped.values())}: {dict(dropped)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
