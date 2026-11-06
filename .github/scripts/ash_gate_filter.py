#!/usr/bin/env python3
"""Reduce ASH's GitHub SARIF to the findings the merge gate acts on, and fingerprint them.

Filter: keep a result only when
  - it is not from Checkov (ASH scores every Checkov rule 8.0, so severity can't
    separate real risks from hygiene; Checkov stays in the ASH job summary), and
  - its rule's security-severity is >= 7.0 (GitHub's "high" and "critical").
    A rule with no security-severity is kept when its level is "error" (ASH gives
    secrets no score), so a scoring gap reports a finding instead of hiding it.

Fingerprint: code scanning tells an existing finding from a new one by
partialFingerprints.primaryLocationLineHash. upload-sarif would compute it from a
checkout of the PR, but the upload job must not check out PR code (it holds a write
token). So it is computed here, in the scan job that already has the files:
  sha256(rule id + path + whitespace-collapsed text of the flagged line), plus an
  index among identical keys in that file, in line order.
The line NUMBER is not part of it, so code moving up or down keeps the same alert.
Main and PRs are both fingerprinted by this script, so they always match.

Usage: ash_gate_filter.py <in.sarif> <out.sarif> [<source-dir>]
"""
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict

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


def location(res):
    try:
        loc = res["locations"][0]["physicalLocation"]
        return loc["artifactLocation"]["uri"], int(loc.get("region", {}).get("startLine") or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return "", 0


class Lines:
    """Reads source lines lazily; a file outside the source dir or unreadable gives ""."""

    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.cache = {}

    def get(self, uri, line):
        if uri not in self.cache:
            path = os.path.realpath(os.path.join(self.root, uri))
            text = []
            if path.startswith(self.root + os.sep) and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read().split("\n")
                except OSError:
                    text = []
            self.cache[uri] = text
        text = self.cache[uri]
        return " ".join(text[line - 1].split()) if 0 < line <= len(text) else ""


def fingerprint(results, lines):
    seen = defaultdict(int)
    for res in sorted(results, key=lambda r: location(r)[::-1]):
        uri, line = location(res)
        key = "\x00".join([res.get("ruleId") or "", uri, lines.get(uri, line)])
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        res.setdefault("partialFingerprints", {})["primaryLocationLineHash"] = f"{digest}:{seen[digest]}"
        seen[digest] += 1


def main(src, dst, source_dir="."):
    sarif = json.load(open(src, encoding="utf-8"))
    kept, dropped = Counter(), Counter()
    lines = Lines(source_dir)
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
        fingerprint(out, lines)
        run["results"] = out
    json.dump(sarif, open(dst, "w", encoding="utf-8"))
    print(f"kept {sum(kept.values())}: {dict(kept)}")
    print(f"dropped {sum(dropped.values())}: {dict(dropped)}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
