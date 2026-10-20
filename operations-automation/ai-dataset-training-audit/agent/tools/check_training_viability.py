"""Check training viability: token budget, leakage, size, and schema.

Local compute only (no AWS). Looks up the target model's context limit and minimum-record
floor from the static tables, degrading safely to the ``default`` entry on a miss.
"""

from __future__ import annotations

import re
from typing import Any

from config import (
    DEFAULT_MODEL_ID,
    MODEL_CONTEXT_LIMITS,
    MODEL_MIN_RECORDS,
)
from tools._validation import cleanup_tmp, resolve_to_local_file
from tools.profile_dataset import (
    _record_text,
    count_tokens,
    detect_format,
    detect_schema,
    load_dataframe,
)

_SIZE_CLASS_RE = re.compile(r"(\d+)\s*b\b", re.IGNORECASE)


def _context_limit(target_model: str) -> int:
    """Context-window limit for ``target_model`` (default floor on a miss)."""
    return MODEL_CONTEXT_LIMITS.get(target_model, MODEL_CONTEXT_LIMITS["default"])


def _min_records(target_model: str) -> int:
    """Minimum recommended records, inferred from the model's size class."""
    match = _SIZE_CLASS_RE.search(target_model)
    if match:
        key = f"{match.group(1)}b"
        if key in MODEL_MIN_RECORDS:
            return MODEL_MIN_RECORDS[key]
    return MODEL_MIN_RECORDS["default"]


def check_training_viability(
    dataset_path: str,
    target_model: str = DEFAULT_MODEL_ID,
    validation_path: str = "",
) -> dict[str, Any]:
    """Assess whether ``dataset_path`` is viable to fine-tune ``target_model``.

    Returns token_budget, schema_check, size_check, leakage_check, overall_viable,
    blocking_issues, warnings.
    """
    local_path, is_tmp = resolve_to_local_file(dataset_path)
    try:
        fmt = detect_format(local_path)
        frame = load_dataframe(local_path, fmt)
        records = frame.to_dicts()
        columns = list(frame.columns)
        schema = detect_schema(columns)

        context_limit = _context_limit(target_model)
        texts = [_record_text(r) for r in records]
        token_counts = [count_tokens(t) for t in texts]
        records_exceeding_context = sum(1 for t in token_counts if t > context_limit)
        max_record_tokens = max(token_counts) if token_counts else 0

        token_budget = {
            "context_limit": context_limit,
            "max_record_tokens": max_record_tokens,
            "records_exceeding_context": records_exceeding_context,
            "within_budget": records_exceeding_context == 0,
        }

        min_records = _min_records(target_model)
        total_records = len(records)
        size_check = {
            "total_records": total_records,
            "minimum_recommended": min_records,
            "meets_minimum": total_records >= min_records,
        }

        leakage_check = _check_leakage(texts, validation_path)

        schema_check = {
            "schema_valid": schema["schema_valid"],
            "detected_schema_pattern": schema["detected_schema_pattern"],
        }

        blocking_issues: list[str] = []
        warnings: list[str] = []
        if records_exceeding_context > 0:
            blocking_issues.append(
                f"{records_exceeding_context} record(s) exceed the "
                f"{context_limit}-token context limit"
            )
        if leakage_check["leakage_detected"]:
            blocking_issues.append(
                f"{leakage_check['overlap_count']} record(s) overlap with the validation set"
            )
        if not schema["schema_valid"]:
            warnings.append("Dataset schema does not match a known fine-tuning pattern")
        if not size_check["meets_minimum"]:
            warnings.append(
                f"Only {total_records} records; {min_records} recommended for {target_model}"
            )

        overall_viable = not blocking_issues

        return {
            "token_budget": token_budget,
            "schema_check": schema_check,
            "size_check": size_check,
            "leakage_check": leakage_check,
            "overall_viable": overall_viable,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "target_model": target_model,
        }
    finally:
        if is_tmp:
            cleanup_tmp(local_path)


def _check_leakage(train_texts: list[str], validation_path: str) -> dict[str, Any]:
    """Detect exact train/validation overlap, if a validation path is supplied."""
    if not validation_path:
        return {"leakage_detected": False, "overlap_count": 0, "checked": False}

    val_local: Any | None = None
    val_is_tmp = False
    try:
        val_local, val_is_tmp = resolve_to_local_file(validation_path)
        val_fmt = detect_format(val_local)
        val_frame = load_dataframe(val_local, val_fmt)
        val_texts = {
            " ".join(_record_text(r).split()) for r in val_frame.to_dicts()
        }
        train_set = {" ".join(t.split()) for t in train_texts}
        overlap = train_set & val_texts
        overlap_count = len(overlap)
        return {
            "leakage_detected": overlap_count > 0,
            "overlap_count": overlap_count,
            "checked": True,
        }
    finally:
        if val_is_tmp and val_local is not None:
            cleanup_tmp(val_local)
