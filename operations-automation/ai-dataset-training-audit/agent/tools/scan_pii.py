"""Scan a dataset for PII with Amazon Comprehend ``DetectPiiEntities``.

Hard AWS dependency (no local fallback). On a terminal Comprehend error
(AccessDenied / Unrecognized / Invalid / UnsupportedLanguage) the scan is marked
incomplete — ``scan_complete=False`` with ``scan_error`` — and ``pii_found`` stays
``False``. A non-completed scan is NEVER reported as clean: the report builder turns an
incomplete scan into the critical ``PII-000`` finding.

Comprehend is invoked one document per call (``en``), in 25-document progress chunks, with
a 4900-byte defensive truncation per document.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import Config
from tools._validation import (
    cleanup_tmp,
    resolve_to_local_file,
    validate_output_path,
)
from tools.profile_dataset import _record_text, detect_format, load_records

logger = logging.getLogger("dart.scan_pii")

_CHUNK_SIZE = 25
_MAX_DOC_BYTES = 4900
_TERMINAL_ERROR_HINTS = (
    "AccessDenied",
    "Unrecognized",
    "Invalid",
    "UnsupportedLanguage",
)


def _comprehend_client_factory() -> Any:
    """Return a Comprehend client.

    Module-level seam: offline mode / tests override this to inject a canned client so no
    real ``comprehend:DetectPiiEntities`` call is attempted.
    """
    import boto3

    return boto3.client("comprehend", region_name=Config.AWS_REGION)


def _truncate(text: str) -> str:
    """Defensively truncate text to 4900 UTF-8 bytes."""
    encoded = text.encode("utf-8")
    if len(encoded) <= _MAX_DOC_BYTES:
        return text
    return encoded[:_MAX_DOC_BYTES].decode("utf-8", errors="ignore")


def _is_terminal_error(err: Exception) -> bool:
    message = str(err)
    return any(hint in message for hint in _TERMINAL_ERROR_HINTS)


def _mask_span(text: str, begin: int, end: int, strategy: str) -> str:
    """Apply mask/remove to a character span of ``text``."""
    if strategy == "remove":
        return text[:begin] + text[end:]
    return text[:begin] + ("*" * (end - begin)) + text[end:]


def scan_pii(
    dataset_path: str,
    redaction_strategy: str = "mask",
    produce_redacted_copy: bool = False,
    output_path: str = "",
) -> dict[str, Any]:
    """Scan ``dataset_path`` for PII with Comprehend.

    Returns pii_found, scan_complete, scan_error, affected_record_count,
    entity_type_counts, redacted_output_path, records_removed.
    """
    local_path, is_tmp = resolve_to_local_file(dataset_path)
    try:
        fmt = detect_format(local_path)
        records = load_records(local_path, fmt)
        texts = [_record_text(r) for r in records]

        result: dict[str, Any] = {
            "pii_found": False,
            "scan_complete": True,
            "scan_error": None,
            "affected_record_count": 0,
            "entity_type_counts": {},
            "redacted_output_path": "",
            "records_removed": 0,
        }

        try:
            client = _comprehend_client_factory()
        except Exception as err:  # pragma: no cover - client construction failure
            logger.error(json.dumps({"event": "scan_pii_client_error", "error": str(err)}))
            result.update(scan_complete=False, scan_error=str(err), pii_found=False)
            return result

        entity_type_counts: dict[str, int] = {}
        affected_records: set[int] = set()
        per_record_entities: dict[int, list[dict[str, Any]]] = {}

        for chunk_start in range(0, len(texts), _CHUNK_SIZE):
            chunk = texts[chunk_start : chunk_start + _CHUNK_SIZE]
            for offset, text in enumerate(chunk):
                idx = chunk_start + offset
                if not text.strip():
                    continue
                document = _truncate(text)
                try:
                    response = client.detect_pii_entities(
                        Text=document, LanguageCode="en"
                    )
                except Exception as err:
                    if _is_terminal_error(err):
                        logger.error(
                            json.dumps(
                                {"event": "scan_pii_terminal_error", "error": str(err)}
                            )
                        )
                        result.update(
                            scan_complete=False,
                            scan_error=str(err),
                            pii_found=False,
                            entity_type_counts={},
                            affected_record_count=0,
                        )
                        return result
                    # Transient per-doc error: treat as no-hit and continue.
                    logger.warning(
                        json.dumps(
                            {
                                "event": "scan_pii_doc_error",
                                "record_index": idx,
                                "error": str(err),
                            }
                        )
                    )
                    continue

                entities = response.get("Entities", [])
                if entities:
                    affected_records.add(idx)
                    per_record_entities[idx] = entities
                    for entity in entities:
                        etype = entity.get("Type", "UNKNOWN")
                        entity_type_counts[etype] = entity_type_counts.get(etype, 0) + 1

        result["entity_type_counts"] = entity_type_counts
        result["affected_record_count"] = len(affected_records)
        result["pii_found"] = bool(affected_records)

        if produce_redacted_copy and result["pii_found"]:
            if not output_path:
                raise ValueError(
                    "produce_redacted_copy=True requires an output_path "
                    "(original is never modified)"
                )
            redacted_path, removed = _write_redacted_copy(
                records,
                texts,
                per_record_entities,
                redaction_strategy,
                output_path,
            )
            result["redacted_output_path"] = str(redacted_path)
            result["records_removed"] = removed

        return result
    finally:
        if is_tmp:
            cleanup_tmp(local_path)


def _write_redacted_copy(
    records: list[dict[str, Any]],
    texts: list[str],
    per_record_entities: dict[int, list[dict[str, Any]]],
    strategy: str,
    output_path: str,
) -> tuple[Path, int]:
    """Write a NEW redacted JSONL copy; never modifies the original."""
    out = validate_output_path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    records_removed = 0
    lines: list[str] = []
    for idx, record in enumerate(records):
        entities = per_record_entities.get(idx)
        if entities and strategy == "remove":
            records_removed += 1
            continue
        if entities:
            record = _redact_record(record, entities)
        lines.append(json.dumps(record, ensure_ascii=False))

    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return out, records_removed


def _redact_record(
    record: dict[str, Any], entities: list[dict[str, Any]]
) -> dict[str, Any]:
    """Mask entity spans inside the string fields of a record.

    Offsets from Comprehend are relative to the concatenated record text; redaction masks
    each detected substring wherever it appears in a string field.
    """
    redacted = dict(record)
    # Reconstruct the detected substrings from the concatenated text, then mask them in
    # each string field by value — robust to field-offset drift.
    concat = _record_text(record)
    substrings = []
    for entity in entities:
        begin, end = entity.get("BeginOffset"), entity.get("EndOffset")
        if begin is None or end is None:
            continue
        substrings.append(concat[begin:end])

    for key, value in redacted.items():
        if not isinstance(value, str):
            continue
        for sub in substrings:
            if sub and sub in value:
                value = value.replace(sub, "*" * len(sub))
        redacted[key] = value
    return redacted
