"""Build the DART pre-flight report: findings, quality score, verdict, and narrative.

AWS dependency: Amazon Bedrock for the Markdown narrative (with a templated fallback on any
ClientError). The report NEVER sends raw dataset records to Bedrock — only up to eight
finding summaries plus dataset metadata.

Scoring (design.md section 2.3) is the weighted six-category model with a critical-finding
cap at 49, so the quality score and the GO / GO WITH FIXES / NO-GO verdict are mutually
consistent by construction.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config import Config

logger = logging.getLogger("dart.report")

# Category weights (sum = 100).
CATEGORY_WEIGHTS = {
    "schema": 20,
    "completeness": 15,
    "uniqueness": 20,
    "pii_safety": 20,
    "viability": 15,
    "cost_efficiency": 10,
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_CRITICAL_CAP = 49
_MAX_BEDROCK_FINDINGS = 8


def _bedrock_client_factory() -> Any:
    """Return a bedrock-runtime client. Overridable seam for tests/offline."""
    import boto3

    return boto3.client("bedrock-runtime", region_name=Config.AWS_REGION)


# ---------------------------------------------------------------------------
# Findings catalog (design.md section 2.3 — preserved verbatim)
# ---------------------------------------------------------------------------


def _finding(
    fid: str,
    severity: str,
    category: str,
    title: str,
    detail: str,
    action: str,
    auto_fixable: bool,
    record_count: int,
) -> dict[str, Any]:
    return {
        "id": fid,
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
        "action": action,
        "auto_fixable": auto_fixable,
        "record_count": record_count,
    }


def _build_findings(
    profile_result: dict[str, Any],
    duplicates_result: dict[str, Any],
    pii_result: dict[str, Any],
    viability_result: dict[str, Any],
    cost_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the findings list from the five upstream tool outputs."""
    findings: list[dict[str, Any]] = []

    # --- PII (critical) ---
    if pii_result.get("pii_found"):
        findings.append(
            _finding(
                "PII-001",
                "critical",
                "pii",
                "PII detected in dataset",
                f"PII entities found in {pii_result.get('affected_record_count', 0)} record(s): "
                f"{pii_result.get('entity_type_counts', {})}",
                "Redact or remove PII (requires explicit approval) before training.",
                False,
                int(pii_result.get("affected_record_count", 0)),
            )
        )
    elif pii_result.get("scan_complete") is False:
        findings.append(
            _finding(
                "PII-000",
                "critical",
                "pii",
                "PII scan did not complete",
                "The PII scan could not complete ("
                f"{pii_result.get('scan_error')}); the dataset cannot be certified clean.",
                "Grant Comprehend DetectPiiEntities access and re-run the scan.",
                False,
                0,
            )
        )

    # --- Leakage (critical) ---
    leakage = viability_result.get("leakage_check", {})
    if leakage.get("leakage_detected"):
        findings.append(
            _finding(
                "LEAK-001",
                "critical",
                "viability",
                "Train/validation leakage",
                f"{leakage.get('overlap_count', 0)} record(s) overlap with the validation set.",
                "Remove overlapping records from the training set.",
                False,
                int(leakage.get("overlap_count", 0)),
            )
        )

    # --- Token budget (critical) ---
    token_budget = viability_result.get("token_budget", {})
    exceeding = int(token_budget.get("records_exceeding_context", 0))
    if exceeding > 0:
        findings.append(
            _finding(
                "TOKEN-001",
                "critical",
                "viability",
                "Records exceed model context window",
                f"{exceeding} record(s) exceed the "
                f"{token_budget.get('context_limit')}-token limit.",
                "Split or truncate over-long records before training.",
                False,
                exceeding,
            )
        )

    # --- Duplicates (high) ---
    total_dupes = int(duplicates_result.get("total_duplicate_count", 0))
    if total_dupes > 0:
        findings.append(
            _finding(
                "DEDUP-001",
                "high",
                "uniqueness",
                "Duplicate records detected",
                f"{duplicates_result.get('exact_duplicate_count', 0)} exact and "
                f"{duplicates_result.get('near_duplicate_count', 0)} near-duplicate record(s) "
                f"(rate {duplicates_result.get('duplicate_rate', 0)}).",
                "Remove duplicates with apply_safe_fixes.",
                True,
                total_dupes,
            )
        )

    # --- Empty records (high) ---
    empty = int(profile_result.get("empty_record_count", 0))
    if empty > 0:
        findings.append(
            _finding(
                "EMPTY-001",
                "high",
                "completeness",
                "Empty or near-empty records",
                f"{empty} record(s) have empty or sub-threshold response text.",
                "Remove empty records with apply_safe_fixes.",
                True,
                empty,
            )
        )

    # --- Schema (medium) ---
    schema_check = viability_result.get("schema_check", {})
    if schema_check.get("schema_valid") is False:
        findings.append(
            _finding(
                "SCHEMA-001",
                "medium",
                "schema",
                "Unrecognized dataset schema",
                "The dataset columns do not match a known fine-tuning schema pattern.",
                "Reshape the dataset to a supported schema (e.g. instruction/response).",
                False,
                0,
            )
        )

    # --- Size (medium) ---
    size_check = viability_result.get("size_check", {})
    if size_check.get("meets_minimum") is False:
        findings.append(
            _finding(
                "SIZE-001",
                "medium",
                "viability",
                "Dataset smaller than recommended",
                f"{size_check.get('total_records', 0)} records; "
                f"{size_check.get('minimum_recommended', 0)} recommended.",
                "Collect more training examples for better results.",
                False,
                int(size_check.get("total_records", 0)),
            )
        )

    # --- Encoding (low) ---
    if profile_result.get("encoding_warning"):
        findings.append(
            _finding(
                "ENC-001",
                "low",
                "schema",
                "Encoding warning",
                str(profile_result.get("encoding_warning")),
                "Normalize the dataset to UTF-8 with apply_safe_fixes.",
                True,
                0,
            )
        )

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 99))
    return findings


# ---------------------------------------------------------------------------
# Scoring (design.md section 2.3)
# ---------------------------------------------------------------------------


def _compute_quality_score(
    profile_result: dict[str, Any],
    duplicates_result: dict[str, Any],
    pii_result: dict[str, Any],
    viability_result: dict[str, Any],
    cost_result: dict[str, Any],
    findings: list[dict[str, Any]],
) -> int:
    """Weighted six-category score in [0, 100] with a critical-finding cap at 49."""
    total_records = max(int(profile_result.get("total_records", 0)), 0)
    empty = int(profile_result.get("empty_record_count", 0))
    duplicate_rate = float(duplicates_result.get("duplicate_rate", 0.0) or 0.0)

    schema_check = viability_result.get("schema_check", {})
    token_budget = viability_result.get("token_budget", {})
    leakage = viability_result.get("leakage_check", {})
    size_check = viability_result.get("size_check", {})

    # Schema validity.
    schema_sub = 1.0 if schema_check.get("schema_valid") else 0.4

    # Completeness.
    if total_records > 0:
        completeness_sub = 1.0 - min(1.0, empty / total_records)
    else:
        completeness_sub = 1.0

    # Uniqueness.
    uniqueness_sub = 1.0 - min(1.0, max(0.0, duplicate_rate))

    # PII safety: 0.0 if PII found OR scan incomplete.
    if pii_result.get("pii_found") or pii_result.get("scan_complete") is False:
        pii_sub = 0.0
    else:
        pii_sub = 1.0

    # Training viability.
    if int(token_budget.get("records_exceeding_context", 0)) > 0 or leakage.get(
        "leakage_detected"
    ):
        viability_sub = 0.0
    elif size_check.get("meets_minimum"):
        viability_sub = 1.0
    else:
        viability_sub = 0.5

    # Cost efficiency: high savings_percent => currently wasteful => lower efficiency.
    savings_percent = cost_result.get("savings_percent")
    if savings_percent is None:
        cost_sub = 1.0
    else:
        clamped = min(100.0, max(0.0, float(savings_percent)))
        cost_sub = 1.0 - min(1.0, clamped / 100.0)

    weighted = (
        CATEGORY_WEIGHTS["schema"] * schema_sub
        + CATEGORY_WEIGHTS["completeness"] * completeness_sub
        + CATEGORY_WEIGHTS["uniqueness"] * uniqueness_sub
        + CATEGORY_WEIGHTS["pii_safety"] * pii_sub
        + CATEGORY_WEIGHTS["viability"] * viability_sub
        + CATEGORY_WEIGHTS["cost_efficiency"] * cost_sub
    )
    score = round(100 * weighted / 100)
    score = max(0, min(100, score))

    # Critical-finding cap: any critical finding forces NO-GO band.
    if any(f["severity"] == "critical" for f in findings):
        score = min(score, _CRITICAL_CAP)

    return score


def _determine_recommendation(score: int) -> str:
    """Map a 0-100 score to a verdict band."""
    if score >= 80:
        return "GO"
    if score >= 50:
        return "GO WITH FIXES"
    return "NO-GO"


# ---------------------------------------------------------------------------
# Bedrock narrative (findings summaries only — never raw records)
# ---------------------------------------------------------------------------


def _call_bedrock_narrative(
    dataset_path: str,
    target_model: str,
    score: int,
    recommendation: str,
    findings: list[dict[str, Any]],
    cost_result: dict[str, Any],
    profile_result: dict[str, Any],
) -> str:
    """Generate a Markdown narrative via Bedrock; templated fallback on ClientError."""
    from botocore.exceptions import ClientError

    finding_summaries = [
        {
            "severity": f["severity"],
            "title": f["title"],
            "detail": f["detail"],
            "action": f["action"],
            "auto_fixable": f["auto_fixable"],
        }
        for f in findings[:_MAX_BEDROCK_FINDINGS]
    ]
    findings_payload = {
        "dataset_metadata": {
            "dataset_path": dataset_path,
            "target_model": target_model,
            "total_records": profile_result.get("total_records"),
            "quality_score": score,
            "recommendation": recommendation,
            "cost_before_fixes": cost_result.get("cost_before_fixes"),
            "cost_after_fixes": cost_result.get("cost_after_fixes"),
            "savings_usd": cost_result.get("savings_usd"),
        },
        "findings": finding_summaries,
    }

    prompt = (
        "You are DART, a dataset readiness auditor. Write a concise Markdown narrative "
        "summarizing this pre-flight audit for an engineer. Use only the structured data "
        "below; do not invent record contents.\n\n"
        + json.dumps(findings_payload, ensure_ascii=False)
    )

    try:
        client = _bedrock_client_factory()
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": Config.MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        response = client.invoke_model(modelId=Config.MODEL_ID, body=body)
        payload = json.loads(response["body"].read())
        content = payload.get("content", [])
        if content and isinstance(content, list):
            return str(content[0].get("text", "")).strip() or _templated_narrative(
                score, recommendation, findings
            )
        return _templated_narrative(score, recommendation, findings)
    except (ClientError, KeyError, ValueError, TypeError, RuntimeError) as err:
        logger.warning(
            json.dumps({"event": "bedrock_narrative_fallback", "error": str(err)})
        )
        return _templated_narrative(score, recommendation, findings)


def _templated_narrative(
    score: int, recommendation: str, findings: list[dict[str, Any]]
) -> str:
    """Deterministic Markdown narrative used when Bedrock is unavailable."""
    lines = [
        "## DART Pre-flight Audit",
        "",
        f"**Verdict:** {recommendation}  ",
        f"**Quality score:** {score}/100",
        "",
    ]
    if findings:
        lines.append("### Findings")
        for f in findings:
            lines.append(
                f"- **[{f['severity'].upper()}] {f['id']} — {f['title']}**: "
                f"{f['detail']} _Action:_ {f['action']}"
            )
    else:
        lines.append("No issues detected. The dataset looks ready for fine-tuning.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_preflight_report(
    dataset_path: str,
    target_model: str,
    profile_result: dict[str, Any],
    duplicates_result: dict[str, Any],
    pii_result: dict[str, Any],
    viability_result: dict[str, Any],
    cost_result: dict[str, Any],
    run_id: str = "",
) -> dict[str, Any]:
    """Assemble the pre-flight report dict.

    Returns run_id, recommendation, quality_score, findings, findings_count, cost_summary,
    dataset_summary, narrative.
    """
    findings = _build_findings(
        profile_result,
        duplicates_result,
        pii_result,
        viability_result,
        cost_result,
    )
    score = _compute_quality_score(
        profile_result,
        duplicates_result,
        pii_result,
        viability_result,
        cost_result,
        findings,
    )
    recommendation = _determine_recommendation(score)
    narrative = _call_bedrock_narrative(
        dataset_path,
        target_model,
        score,
        recommendation,
        findings,
        cost_result,
        profile_result,
    )

    return {
        "run_id": run_id,
        "recommendation": recommendation,
        "quality_score": score,
        "findings": findings,
        "findings_count": len(findings),
        "cost_summary": {
            "cost_before_fixes": cost_result.get("cost_before_fixes"),
            "cost_after_fixes": cost_result.get("cost_after_fixes"),
            "savings_usd": cost_result.get("savings_usd"),
            "savings_percent": cost_result.get("savings_percent"),
        },
        "dataset_summary": {
            "dataset_path": dataset_path,
            "target_model": target_model,
            "total_records": profile_result.get("total_records"),
            "format": profile_result.get("format"),
            "detected_schema_pattern": profile_result.get("detected_schema_pattern"),
        },
        "narrative": narrative,
    }
