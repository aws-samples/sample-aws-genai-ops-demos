"""Apply safe, non-destructive fixes by writing a NEW cleaned file.

Local compute only (no AWS). The ORIGINAL input is NEVER modified or deleted: the cleaned
dataset is always written to ``output_path`` (auto-generated next to the input when empty).
Removes exact duplicates and empty records and repairs encoding by re-emitting UTF-8 JSONL.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools._validation import (
    cleanup_tmp,
    resolve_to_local_file,
    validate_output_path,
)
from tools.profile_dataset import (
    _record_text,
    _response_field,
    detect_format,
    detect_schema,
    load_dataframe,
)


def _default_output_path(local_path: Path) -> Path:
    """Generate a cleaned-file path next to the input without touching the original."""
    return local_path.with_name(f"{local_path.stem}.cleaned{local_path.suffix or '.jsonl'}")


def _text_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def apply_safe_fixes(
    dataset_path: str,
    output_path: str = "",
    remove_exact_duplicates: bool = True,
    remove_empty_records: bool = True,
    fix_encoding: bool = True,
) -> dict[str, Any]:
    """Write a cleaned copy of ``dataset_path`` to ``output_path``.

    Returns records_before, records_after, records_removed_total, exact_duplicates_removed,
    empty_records_removed, encoding_issues_fixed, output_path.
    """
    local_path, is_tmp = resolve_to_local_file(dataset_path)
    try:
        fmt = detect_format(local_path)
        frame = load_dataframe(local_path, fmt)
        records = frame.to_dicts()
        pattern = detect_schema(list(frame.columns))["detected_schema_pattern"]
        records_before = len(records)

        out = (
            validate_output_path(output_path)
            if output_path
            else _default_output_path(local_path)
        )
        out.parent.mkdir(parents=True, exist_ok=True)

        seen_hashes: set[str] = set()
        exact_duplicates_removed = 0
        empty_records_removed = 0
        kept: list[dict[str, Any]] = []

        for record in records:
            text = _record_text(record)
            response = _response_field(record, pattern)

            # "Empty" matches the profiler's definition: empty whole-record text OR an
            # empty primary response field.
            if remove_empty_records and (not text.strip() or not response.strip()):
                empty_records_removed += 1
                continue

            if remove_exact_duplicates:
                digest = _text_hash(text)
                if digest in seen_hashes:
                    exact_duplicates_removed += 1
                    continue
                seen_hashes.add(digest)

            kept.append(record)

        # fix_encoding: re-emit as UTF-8 JSONL regardless of input encoding.
        encoding_issues_fixed = 1 if fix_encoding else 0

        lines = [json.dumps(record, ensure_ascii=False) for record in kept]
        out.write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )

        records_after = len(kept)
        return {
            "records_before": records_before,
            "records_after": records_after,
            "records_removed_total": records_before - records_after,
            "exact_duplicates_removed": exact_duplicates_removed,
            "empty_records_removed": empty_records_removed,
            "encoding_issues_fixed": encoding_issues_fixed,
            "output_path": str(out),
        }
    finally:
        if is_tmp:
            cleanup_tmp(local_path)
