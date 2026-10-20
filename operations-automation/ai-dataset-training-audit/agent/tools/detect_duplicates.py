"""Detect exact and near-duplicate records.

Local compute only (no AWS). Exact duplicates via SHA-256 of the record text;
near-duplicates via MinHash LSH (datasketch, ``num_perm=128``) over character shingles with
a Jaccard ``similarity_threshold``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from datasketch import MinHash, MinHashLSH

from config import Config
from tools._validation import cleanup_tmp, resolve_to_local_file
from tools.profile_dataset import _record_text, detect_format, load_records

_SHINGLE_SIZE = 5


def _char_shingles(text: str, size: int = _SHINGLE_SIZE) -> set[str]:
    """Character n-gram shingles for a piece of text."""
    text = " ".join(text.split())
    if len(text) <= size:
        return {text} if text else set()
    return {text[i : i + size] for i in range(len(text) - size + 1)}


def _minhash(shingles: set[str], num_perm: int) -> MinHash:
    """Build a MinHash signature from a shingle set."""
    mh = MinHash(num_perm=num_perm)
    for shingle in shingles:
        mh.update(shingle.encode("utf-8"))
    return mh


def _text_hash(text: str) -> str:
    """SHA-256 hex digest of normalised record text."""
    normalised = " ".join(text.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def detect_duplicates(
    dataset_path: str,
    similarity_threshold: float = 0.85,
) -> dict[str, Any]:
    """Count exact and near-duplicate records in ``dataset_path``.

    Returns exact/near/total duplicate counts, duplicate_rate, and the threshold used.
    """
    local_path, is_tmp = resolve_to_local_file(dataset_path)
    try:
        fmt = detect_format(local_path)
        records = load_records(local_path, fmt)
        total_records = len(records)

        texts = [_record_text(r) for r in records]

        # Exact duplicates: count records beyond the first occurrence of each hash.
        seen_hashes: dict[str, int] = {}
        exact_duplicate_count = 0
        exact_dup_indices: set[int] = set()
        for idx, text in enumerate(texts):
            digest = _text_hash(text)
            if digest in seen_hashes:
                exact_duplicate_count += 1
                exact_dup_indices.add(idx)
            else:
                seen_hashes[digest] = idx

        # Near duplicates via MinHash LSH over the non-exact-duplicate records.
        num_perm = Config.MINHASH_NUM_PERM
        lsh = MinHashLSH(threshold=similarity_threshold, num_perm=num_perm)
        near_duplicate_count = 0
        near_dup_indices: set[int] = set()
        for idx, text in enumerate(texts):
            if idx in exact_dup_indices:
                continue
            shingles = _char_shingles(text)
            if not shingles:
                continue
            mh = _minhash(shingles, num_perm)
            matches = lsh.query(mh)
            if matches:
                near_duplicate_count += 1
                near_dup_indices.add(idx)
            else:
                lsh.insert(f"rec-{idx}", mh)

        total_duplicate_count = exact_duplicate_count + near_duplicate_count
        duplicate_rate = (
            total_duplicate_count / total_records if total_records else 0.0
        )

        return {
            "exact_duplicate_count": exact_duplicate_count,
            "near_duplicate_count": near_duplicate_count,
            "total_duplicate_count": total_duplicate_count,
            "duplicate_rate": round(duplicate_rate, 4),
            "similarity_threshold_used": similarity_threshold,
            "total_records": total_records,
        }
    finally:
        if is_tmp:
            cleanup_tmp(local_path)


def duplicate_indices(texts: list[str], similarity_threshold: float, num_perm: int) -> set[int]:
    """Return the set of record indices that are exact- or near-duplicates.

    Shared helper used by :mod:`tools.apply_safe_fixes` so fix removal matches detection.
    """
    seen_hashes: dict[str, int] = {}
    dup_indices: set[int] = set()
    non_exact: list[int] = []
    for idx, text in enumerate(texts):
        digest = _text_hash(text)
        if digest in seen_hashes:
            dup_indices.add(idx)
        else:
            seen_hashes[digest] = idx
            non_exact.append(idx)

    lsh = MinHashLSH(threshold=similarity_threshold, num_perm=num_perm)
    for idx in non_exact:
        shingles = _char_shingles(texts[idx])
        if not shingles:
            continue
        mh = _minhash(shingles, num_perm)
        if lsh.query(mh):
            dup_indices.add(idx)
        else:
            lsh.insert(f"rec-{idx}", mh)
    return dup_indices
