"""Unit tests for assess.py: the source/manifest blob read_source_code builds for
Nova, and the field-scoped fn# write of assess_one.

No real AWS: read_source_code works on files written under tmp_path; assess_one
is exercised with its collaborators (download, read, Nova call, table)
monkeypatched. MAX_CODE_CHARS is patched on the module to make budgets small.
"""
import os
from unittest.mock import MagicMock

import pytest

import assess
from assess import read_source_code, _is_manifest


# --- helpers ----------------------------------------------------------------

def _write(root, rel_path, content):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return rel_path


def _package(root):
    """A small clean package: two source files + two manifests, as the
    download walk would list them (relative paths, lock files included)."""
    files = [
        _write(root, "handler.py", "import json\n\ndef handler(event, context):\n    return {}\n"),
        _write(root, "lib/util.py", "def helper():\n    return 1\n"),
        _write(root, "requirements.txt", "boto3==1.26.0\nrequests<2.20\n"),
        _write(root, "pyproject.toml", "[project]\nname = 'demo'\n"),
    ]
    return files


# --- read_source_code: manifests --------------------------------------------

def test_manifests_appended_after_source_with_label(tmp_path):
    files = _package(tmp_path)
    info = read_source_code(str(tmp_path), files)
    assert set(info) == {"lines_of_code", "num_source_files", "code_content",
                         "num_manifests", "source_truncated"}
    assert info["num_manifests"] == 2
    # Source counts count source files only, manifests change nothing.
    assert info["num_source_files"] == 2
    assert info["lines_of_code"] == 5 + 3
    content = info["code_content"]
    assert "### Manifest: requirements.txt\nboto3==1.26.0\nrequests<2.20\n" in content
    assert "### Manifest: pyproject.toml\n[project]\nname = 'demo'\n" in content
    # Every manifest block comes after the last source block.
    assert content.rfind("--- ") < content.find("### Manifest: ")
    assert info["source_truncated"] is False


def test_nested_manifest_found_at_depth(tmp_path):
    files = [
        _write(tmp_path, "src/app/index.js", "exports.handler = async () => ({});\n"),
        _write(tmp_path, "src/app/package.json", '{"name": "demo", "dependencies": {"aws-sdk": "^2.0.0"}}\n'),
    ]
    info = read_source_code(str(tmp_path), files)
    assert info["num_manifests"] == 1
    assert "### Manifest: src/app/package.json\n" in info["code_content"]
    assert '"aws-sdk": "^2.0.0"' in info["code_content"]


@pytest.mark.parametrize("lock", sorted(assess.LOCK_FILES))
def test_lock_files_are_never_manifests(tmp_path, lock):
    assert _is_manifest(lock) is False
    assert _is_manifest(f"src/{lock}") is False
    files = [
        _write(tmp_path, "index.js", "module.exports = 1;\n"),
        _write(tmp_path, lock, "LOCKED " * 10),
    ]
    info = read_source_code(str(tmp_path), files)
    assert info["num_manifests"] == 0
    assert "LOCKED" not in info["code_content"]


def test_csproj_detected_by_extension(tmp_path):
    assert _is_manifest("Orders/Orders.csproj") is True
    assert _is_manifest("Orders/Orders.fsproj") is True
    assert _is_manifest("Orders/Function.cs") is False
    files = [
        _write(tmp_path, "Function.cs", "public class Function {}\n"),
        _write(tmp_path, "Orders.csproj", "<Project Sdk=\"Microsoft.NET.Sdk\"><TargetFramework>net6.0</TargetFramework></Project>\n"),
    ]
    info = read_source_code(str(tmp_path), files)
    assert info["num_manifests"] == 1
    assert "### Manifest: Orders.csproj\n<Project" in info["code_content"]
    assert info["num_source_files"] == 1


def test_large_manifest_cut_with_marker_and_flag(tmp_path):
    big = "x" * (assess.MANIFEST_MAX_CHARS + 500)
    files = [
        _write(tmp_path, "handler.py", "print(1)\n"),
        _write(tmp_path, "requirements.txt", big),
    ]
    info = read_source_code(str(tmp_path), files)
    assert info["num_manifests"] == 1
    assert info["source_truncated"] is True
    block_start = info["code_content"].index("### Manifest: requirements.txt\n")
    block = info["code_content"][block_start:]
    assert "x" * assess.MANIFEST_MAX_CHARS + "\n[manifest truncated at 4000 chars]" in block
    assert "x" * (assess.MANIFEST_MAX_CHARS + 1) not in block
    assert f"[TRUNCATED — total source exceeds {assess.MAX_CODE_CHARS} chars]" in info["code_content"]


# --- read_source_code: source budget ----------------------------------------

def test_small_budget_cuts_source_and_drops_manifests(tmp_path, monkeypatch):
    monkeypatch.setattr(assess, "MAX_CODE_CHARS", 60)
    files = [
        _write(tmp_path, "a.py", "a" * 100),
        _write(tmp_path, "b.py", "b" * 100),
        _write(tmp_path, "requirements.txt", "boto3\n"),
    ]
    info = read_source_code(str(tmp_path), files)
    assert info["source_truncated"] is True
    assert info["num_manifests"] == 0
    assert "### Manifest:" not in info["code_content"]
    # Both source files are still counted even though b.py got no budget.
    assert info["num_source_files"] == 2
    assert "--- b.py" not in info["code_content"]
    assert "[TRUNCATED — total source exceeds 60 chars]" in info["code_content"]


def test_clean_small_package_not_truncated_no_footer(tmp_path):
    files = _package(tmp_path)
    info = read_source_code(str(tmp_path), files)
    assert info["source_truncated"] is False
    assert "[TRUNCATED" not in info["code_content"]
    assert "[manifest truncated" not in info["code_content"]


# --- assess_one: the fn# write ----------------------------------------------

def test_assess_one_sets_new_fields_and_removes_stale_attribute(monkeypatch):
    table = MagicMock()
    monkeypatch.setattr(assess, "_table", lambda: table)
    monkeypatch.setattr(assess, "download_function_code",
                        lambda session, arn, region: {"skipped": False, "function_name": "fn",
                                                     "extract_path": "/tmp/x", "source_files": ["h.py"]})
    monkeypatch.setattr(assess, "read_source_code",
                        lambda path, files: {"lines_of_code": 10, "num_source_files": 1,
                                             "code_content": "print(1)", "num_manifests": 1,
                                             "source_truncated": True})
    monkeypatch.setattr(assess, "assess_with_ai",
                        lambda session, arn, runtime, code_info: {
                            "complexity": "LOW", "summary": "s", "migration_risks": "none",
                            "deprecated_apis": [], "breaking_changes": [], "dependency_issues": []})

    target = {"item_key": "fn#111#eu-west-3#arn", "function_arn": "arn:aws:lambda:eu-west-3:111:function:fn",
              "account_id": "111", "region": "eu-west-3", "runtime": "python3.9"}
    out = assess.assess_one(MagicMock(), target, "2024-01-01T00:00:00+00:00")

    assert out["status"] == "ASSESSED"
    assert set(out) == {"item_key", "function_arn", "status", "complexity"}

    kwargs = table.update_item.call_args.kwargs
    expression = kwargs["UpdateExpression"]
    names = kwargs["ExpressionAttributeNames"]
    values = kwargs["ExpressionAttributeValues"]
    assert expression.startswith("SET ")
    assert " REMOVE " in expression
    assert assess._STALE_ASSESSMENT_ATTRS[0] in names.values()
    # The stale attribute is REMOVEd (a #rN name), never SET (a #aN name).
    stale_placeholders = [pn for pn, field in names.items() if field == assess._STALE_ASSESSMENT_ATTRS[0]]
    assert stale_placeholders and all(pn.startswith("#r") for pn in stale_placeholders)
    assert all(pn in expression.split(" REMOVE ", 1)[1] for pn in stale_placeholders)

    written = {field: values[pn.replace("#a", ":v")] for pn, field in names.items() if pn.startswith("#a")}
    assert written["num_manifests"] == 1
    assert written["source_truncated"] is True
    assert written["migration_status"] == "ASSESSED"
    assert written["migration_complexity"] == "LOW"
