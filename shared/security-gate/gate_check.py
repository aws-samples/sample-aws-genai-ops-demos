#!/usr/bin/env python3
"""
OpsCatalyst Public Content Security Review — Threat Model Gate Check

Parses .threatmodel/ output from threat-modeling-mcp-server and returns verdict:
  PASS  (exit 0) — all threats mitigated
  WARN  (exit 0) — some unmitigated threats exist
  BLOCK (exit 1) — too many unmitigated threats, fails the pipeline

Supports:
  - Threat Composer JSON format (mitigationLinks-based)
  - Flat format with severity field per threat

Usage:
  python gate_check.py .threatmodel/
  python gate_check.py .threatmodel/radar-threat-model.json
"""
import json
import sys
import glob
import os

# Thresholds
UNMITIGATED_BLOCK_THRESHOLD = 3  # 3+ unmitigated threats → BLOCK
UNMITIGATED_WARN_THRESHOLD = 1   # 1+ unmitigated threats → WARN


def load_threat_model(path):
    """Load threat model JSON from path (file or directory)."""
    if os.path.isdir(path):
        files = glob.glob(os.path.join(path, "*.json"))
        if not files:
            print(f"ERROR: No JSON files found in {path}")
            sys.exit(2)
        path = files[0]
    with open(path) as f:
        return json.load(f)


def evaluate_threat_composer(model):
    """Evaluate Threat Composer format (mitigationLinks-based)."""
    threats = model.get("threats", [])
    mitigations = model.get("mitigations", [])
    links = model.get("mitigationLinks", [])

    # Build set of threat IDs that have at least one linked mitigation
    mitigated_threat_ids = set()
    for link in links:
        linked_threats = link.get("linkedId") if isinstance(link.get("linkedId"), str) else None
        # mitigationLinks format: [{mitigationId, linkedId (threat)}]
        if "linkedId" in link:
            mitigated_threat_ids.add(link["linkedId"])

    # Also check assumptionLinks — threats covered by assumptions
    for link in model.get("assumptionLinks", []):
        if "linkedId" in link:
            mitigated_threat_ids.add(link["linkedId"])

    unmitigated = [t for t in threats if t["id"] not in mitigated_threat_ids]
    mitigated = [t for t in threats if t["id"] in mitigated_threat_ids]

    return threats, mitigated, unmitigated


def evaluate_flat(model):
    """Evaluate flat format with severity per threat."""
    threats = model.get("threats", model.get("threat_model", {}).get("threats", []))
    blocking = []
    for t in threats:
        severity = (t.get("severity") or t.get("risk_level") or "low").lower()
        status = (t.get("mitigation_status") or t.get("status") or "").lower()
        if severity in ("critical", "high") and status not in ("resolved", "mitigated", "accepted"):
            blocking.append(t)
    return threats, [], blocking


def main():
    if len(sys.argv) < 2:
        print("Usage: gate_check.py <path-to-threatmodel>")
        sys.exit(2)

    path = sys.argv[1]
    model = load_threat_model(path)

    # Detect format
    is_threat_composer = "mitigationLinks" in model

    if is_threat_composer:
        threats, mitigated, unmitigated = evaluate_threat_composer(model)
    else:
        threats, mitigated, unmitigated = evaluate_flat(model)

    if not threats:
        print("✅ PASS — No threats identified")
        sys.exit(0)

    print(f"\n{'='*50}")
    print(f"  THREAT MODEL GATE CHECK")
    print(f"{'='*50}")
    print(f"  Total threats:  {len(threats)}")
    print(f"  Mitigated:      {len(mitigated)}")
    print(f"  Unmitigated:    {len(unmitigated)}")
    print(f"{'='*50}")

    if len(unmitigated) >= UNMITIGATED_BLOCK_THRESHOLD:
        print(f"\n❌ BLOCK — {len(unmitigated)} unmitigated threat(s):\n")
        for t in unmitigated:
            title = t.get("threatAction") or t.get("title") or "Unnamed"
            print(f"  • {title}")
        print(f"\nPipeline blocked. Mitigate threats or document accepted risk.")
        sys.exit(1)
    elif len(unmitigated) >= UNMITIGATED_WARN_THRESHOLD:
        print(f"\n⚠️  WARN — {len(unmitigated)} unmitigated threat(s):")
        for t in unmitigated:
            title = t.get("threatAction") or t.get("title") or "Unnamed"
            print(f"  • {title}")
        print(f"\nPipeline continues but review recommended.")
        sys.exit(0)
    else:
        print(f"\n✅ PASS — All {len(threats)} threats have mitigations linked")
        sys.exit(0)


if __name__ == "__main__":
    main()
