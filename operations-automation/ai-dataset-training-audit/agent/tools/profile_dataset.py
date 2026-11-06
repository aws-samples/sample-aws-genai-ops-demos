"""Profile a fine-tuning dataset: format, encoding, schema, record and token stats.

Local compute only (no AWS). Uses Polars to load JSONL/CSV/Parquet, tiktoken
(``cl100k_base``) for token counts across all model families, and chardet to sniff the
encoding of the first 64 KB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from config import DEFAULT_MODEL_ID, KNOWN_SCHEMAS
from tools._validation import cleanup_tmp, resolve_to_local_file

_SNIFF_BYTES = 64 * 1024
_TEXT_FIELD_HINTS = (
    "instruction",
    "response",
    "prompt",
    "completion",
    "input",
    "output",
    "question",
    "answer",
    "text",
    "content",
)

# A single shared encoder — created lazily so import stays cheap.
_ENCODER = None


def _get_encoder() -> Any:
    global _ENCODER
    if _ENCODER is None:
        import tiktoken

        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def count_tokens(text: str) -> int:
    """Count tokens in ``text`` using the shared cl100k_base encoder."""
    if not text:
        return 0
    return len(_get_encoder().encode(text, disallowed_special=()))


def detect_format(path: Path) -> str:
    """Infer dataset format from the file suffix."""
    suffix = path.suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        return "jsonl"
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    if suffix in (".parquet", ".pq"):
        return "parquet"
    return "jsonl"  # default assumption for LLM fine-tuning data


def sniff_encoding(path: Path) -> dict[str, Any]:
    """Sniff encoding of the first 64 KB; return encoding + optional warning."""
    import chardet

    with open(path, "rb") as handle:
        raw = handle.read(_SNIFF_BYTES)
    if raw:
        result = chardet.detect(raw)
        encoding = (result.get("encoding") or "utf-8").lower()
        confidence = result.get("confidence") or 0.0
    else:
        encoding, confidence = "utf-8", 1.0

    warning: str | None = None
    if encoding not in ("utf-8", "ascii"):
        warning = f"Non-UTF-8 encoding detected: {encoding} (confidence {confidence:.2f})"
    elif confidence < 0.5:
        warning = f"Low-confidence encoding detection ({confidence:.2f}); assuming utf-8"
    return {"encoding": encoding, "encoding_warning": warning}


def load_dataframe(local_path: Path, fmt: str) -> pl.DataFrame:
    """Load a dataset file into a Polars DataFrame."""
    if fmt in ("jsonl", "json"):
        return pl.read_ndjson(local_path) if fmt == "jsonl" else pl.read_json(local_path)
    if fmt == "csv":
        return pl.read_csv(local_path)
    if fmt == "parquet":
        return pl.read_parquet(local_path)
    return pl.read_ndjson(local_path)


def load_records(local_path: Path, fmt: str) -> list[dict[str, Any]]:
    """Load a dataset file as a list of dict records."""
    frame = load_dataframe(local_path, fmt)
    return frame.to_dicts()


def detect_schema(columns: list[str]) -> dict[str, Any]:
    """Match the column set against KNOWN_SCHEMAS, returning the best pattern."""
    col_set = set(columns)
    for pattern, required in KNOWN_SCHEMAS.items():
        if set(required).issubset(col_set):
            return {
                "schema_valid": True,
                "detected_schema_pattern": pattern,
                "required_fields": required,
                "columns": columns,
            }
    return {
        "schema_valid": False,
        "detected_schema_pattern": None,
        "required_fields": [],
        "columns": columns,
    }


def _record_text(record: dict[str, Any]) -> str:
    """Concatenate the string-valued fields of a record for text stats."""
    parts: list[str] = []
    for value in record.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.extend(v for v in item.values() if isinstance(v, str))
    return "\n".join(parts)


def _response_field(record: dict[str, Any], pattern: str | None) -> str:
    """Return the record's primary 'answer/response/output/completion' text, if any."""
    order = {
        "instruction_response": "response",
        "prompt_completion": "completion",
        "input_output": "output",
        "question_answer": "answer",
    }
    field = order.get(pattern or "")
    if field and isinstance(record.get(field), str):
        return record[field]
    for hint in ("response", "completion", "output", "answer"):
        if isinstance(record.get(hint), str):
            return record[hint]
    return ""


def profile_dataset(
    dataset_path: str,
    target_model: str = DEFAULT_MODEL_ID,
) -> dict[str, Any]:
    """Profile ``dataset_path`` for the given ``target_model``.

    Returns format, total_records, encoding (+warning), schema, detected_schema_pattern,
    text_stats (token counts), empty_record_count, and quality_flags.
    """
    local_path, is_tmp = resolve_to_local_file(dataset_path)
    try:
        fmt = detect_format(local_path)
        enc = sniff_encoding(local_path)
        frame = load_dataframe(local_path, fmt)
        records = frame.to_dicts()
        columns = list(frame.columns)
        schema = detect_schema(columns)
        pattern = schema["detected_schema_pattern"]

        total_records = len(records)
        token_counts: list[int] = []
        response_token_counts: list[int] = []
        empty_record_count = 0

        for record in records:
            text = _record_text(record)
            tokens = count_tokens(text)
            token_counts.append(tokens)
            response = _response_field(record, pattern)
            response_token_counts.append(count_tokens(response))
            if not text.strip() or not response.strip():
                empty_record_count += 1

        total_tokens = sum(token_counts)
        max_tokens = max(token_counts) if token_counts else 0
        avg_tokens = (total_tokens / total_records) if total_records else 0.0

        quality_flags: list[str] = []
        if enc["encoding_warning"]:
            quality_flags.append("encoding")
        if empty_record_count:
            quality_flags.append("empty_records")
        if not schema["schema_valid"]:
            quality_flags.append("unknown_schema")

        return {
            "format": fmt,
            "total_records": total_records,
            "encoding": enc["encoding"],
            "encoding_warning": enc["encoding_warning"],
            "schema": schema,
            "detected_schema_pattern": pattern,
            "text_stats": {
                "total_tokens": total_tokens,
                "max_record_tokens": max_tokens,
                "avg_record_tokens": round(avg_tokens, 2),
                "min_response_tokens": min(response_token_counts)
                if response_token_counts
                else 0,
            },
            "empty_record_count": empty_record_count,
            "quality_flags": quality_flags,
            "target_model": target_model,
        }
    finally:
        if is_tmp:
            cleanup_tmp(local_path)
