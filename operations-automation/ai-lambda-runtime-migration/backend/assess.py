"""
Assess: download a function's code and run the Nova migration assessment
(complexity, deprecated APIs, breaking changes, dependency issues, risks).

The target runtime is NOT assessed: it is fixed per language family by the
managed transformation (transform_runtimes.TRANSFORMS[...]["defaultTarget"]).

Plain functions called from inside the pipeline's durable steps: no handler of
its own, no AWS client created at import time.
"""

import io
import logging
import os
import tempfile
import zipfile
from typing import Any, Dict, List, Optional
from urllib.request import urlopen

import boto3

from aws_utils import get_region  # staged copy of shared/utils/aws_utils.py
from common import convert_floats, extract_region, extract_function_name
from model_id import model_id as _model_id  # computed once at init from staged aws_utils

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("TABLE_NAME", "lambda-runtime-migration")

# Directories to exclude (dependencies, not user code).
EXCLUDED_DIRS = {"node_modules", "__pycache__", ".git", ".aws-sam", "vendor",
                 "dist", "build", ".serverless", "layer", "opt"}

SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".mjs", ".cjs", ".java", ".rb", ".cs", ".go"}

# Max source code chars to send to the model (token budget).
MAX_CODE_CHARS = 50_000

# Dependency manifests: appended after the source (within the same budget) so
# the model can name the package versions the upgrade has to deal with. Lock
# files are never sent (huge, and they only restate the manifest).
MANIFEST_NAMES = {"package.json", "requirements.txt", "pyproject.toml", "Pipfile"}
MANIFEST_EXTENSIONS = {".csproj", ".fsproj"}
LOCK_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
              "Pipfile.lock", "packages.lock.json"}
MANIFEST_MAX_CHARS = 4_000


def _is_excluded(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    return any(p in EXCLUDED_DIRS for p in parts)


def _is_manifest(rel_path: str) -> bool:
    """True for a dependency manifest (by basename or extension), never for a
    lock file. Pure."""
    base = os.path.basename(rel_path.replace("\\", "/"))
    if base in LOCK_FILES:
        return False
    return base in MANIFEST_NAMES or os.path.splitext(base)[1].lower() in MANIFEST_EXTENSIONS


# ---------------------------------------------------------------------------
# Download + read source
# ---------------------------------------------------------------------------

def download_function_code(session: boto3.session.Session, function_arn: str, default_region: str) -> Dict[str, Any]:
    """Download and extract a Lambda deployment package.

    Returns {skipped: True, reason} for Image package type (not migratable via
    code), otherwise the extracted source file list.
    """
    region = extract_region(function_arn, default_region)
    function_name = extract_function_name(function_arn)
    lambda_client = session.client("lambda", region_name=region)

    response = lambda_client.get_function(FunctionName=function_arn)
    if response.get("Configuration", {}).get("PackageType", "Zip") == "Image":
        return {"skipped": True, "reason": "Image package type — cannot analyze source",
                "function_name": function_name}

    code_location = response.get("Code", {}).get("Location")
    if not code_location:
        raise ValueError(f"No Code.Location for {function_name}.")
    if not code_location.startswith("https://"):
        raise ValueError(f"Unexpected URL scheme in Code.Location for {function_name}")

    with urlopen(code_location) as resp:  # nosec B310 — AWS Lambda GetFunction presigned HTTPS URL, scheme validated  # nosemgrep: dynamic-urllib-use-detected
        zip_bytes = resp.read()

    extract_dir = tempfile.mkdtemp(prefix=f"lambda_{function_name}_")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(extract_dir)

    source_files = []
    for root, _dirs, filenames in os.walk(extract_dir):
        for fname in filenames:
            rel = os.path.relpath(os.path.join(root, fname), extract_dir)
            if not _is_excluded(rel):
                source_files.append(rel)
    return {"skipped": False, "function_name": function_name,
            "extract_path": extract_dir, "source_files": source_files}


def _read_text(extract_path: str, rel_path: str) -> Optional[str]:
    try:
        with open(os.path.join(extract_path, rel_path), "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as exc:  # an unreadable file is skipped, not fatal
        logger.warning("could not read %s: %s", rel_path, exc)
        return None


def read_source_code(extract_path: str, source_files: List[str]) -> Dict[str, Any]:
    """Read source files, count lines, build the code blob for the model.

    Source files (SOURCE_EXTENSIONS) come first, in sorted order, within the
    MAX_CODE_CHARS budget. Dependency manifests (_is_manifest) follow, each cut
    at MANIFEST_MAX_CHARS, appended only while they fit in the remaining budget.
    lines_of_code / num_source_files count source files only; num_manifests
    counts the manifests actually appended. source_truncated is True when any
    source file was cut or skipped by the budget, a manifest was cut, or a
    manifest was dropped for lack of budget.
    """
    code_blocks, total_lines, total_chars, num_source_files = [], 0, 0, 0
    num_manifests, source_truncated = 0, False
    for rel_path in sorted(source_files):
        if os.path.splitext(rel_path)[1].lower() not in SOURCE_EXTENSIONS:
            continue
        content = _read_text(extract_path, rel_path)
        if content is None:
            continue
        num_source_files += 1
        total_lines += content.count("\n") + 1
        if total_chars < MAX_CODE_CHARS:
            truncated = content[: MAX_CODE_CHARS - total_chars]
            if len(truncated) < len(content):
                source_truncated = True
            code_blocks.append(f"--- {rel_path} ({content.count(chr(10)) + 1} lines) ---\n{truncated}")
            total_chars += len(truncated)
        else:
            source_truncated = True       # no budget left for this file

    for rel_path in sorted(rel for rel in source_files if _is_manifest(rel)):
        content = _read_text(extract_path, rel_path)
        if content is None:
            continue
        if len(content) > MANIFEST_MAX_CHARS:
            content = content[:MANIFEST_MAX_CHARS] + f"\n[manifest truncated at {MANIFEST_MAX_CHARS} chars]"
            source_truncated = True
        block = f"### Manifest: {rel_path}\n{content}"
        if total_chars + len(block) > MAX_CODE_CHARS:
            source_truncated = True       # dropped: no budget left for manifests
            break
        code_blocks.append(block)
        total_chars += len(block)
        num_manifests += 1

    code_content = "\n\n".join(code_blocks)
    if source_truncated:
        code_content += f"\n\n[TRUNCATED — total source exceeds {MAX_CODE_CHARS} chars]"
    return {"lines_of_code": total_lines, "num_source_files": num_source_files,
            "code_content": code_content, "num_manifests": num_manifests,
            "source_truncated": source_truncated}


# ---------------------------------------------------------------------------
# AI assessment
# ---------------------------------------------------------------------------
# The source is read from the extracted package in /tmp and never copied
# anywhere: Assess has no S3 dependency. (An earlier design kept a copy of the
# original source in the demo bucket; nothing consumed it, and the Transform
# phase works from the Git repository, so it was removed.)

def assess_with_ai(session: boto3.session.Session, function_arn: str,
                   runtime: str, code_info: dict) -> Dict[str, Any]:
    """Send source code to Nova for a migration assessment (constrained decoding).

    Bedrock runs in the Lambda's OWN region, NOT the function's region. The model
    id (_model_id) is a CRIS inference profile resolved for the deploy region
    (e.g. eu.amazon.nova-2-lite-v1:0 in eu-west-3); invoking it against a
    bedrock-runtime endpoint in some other region (the scanned function's region)
    fails with "invalid model identifier". Assessment only reads code, so the
    function's region is irrelevant here — use hub credentials + the deploy
    region so the endpoint and the profile prefix always match.
    """
    bedrock = boto3.client("bedrock-runtime", region_name=get_region())

    tool_config = {
        "tools": [{
            "toolSpec": {
                "name": "submit_assessment",
                "description": "Submit the complete migration assessment for a Lambda function",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "complexity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"],
                                       "description": "Migration complexity classification"},
                        "deprecated_apis": {"type": "array", "items": {"type": "string"},
                                            "description": "Deprecated APIs/imports/patterns found"},
                        "breaking_changes": {"type": "array", "items": {"type": "string"},
                                             "description": "Breaking changes migrating to the next supported major runtime of this language"},
                        "dependency_issues": {"type": "array", "items": {"type": "string"},
                                              "description": "Dependency compatibility concerns"},
                        "summary": {"type": "string",
                                    "description": "What the function does and what the upgrade will touch. Two sentences maximum."},
                        "migration_risks": {"type": "string",
                                            "description": "Only things that could break during or after the upgrade, or the literal 'none'."},
                    },
                    "required": ["complexity", "deprecated_apis",
                                 "breaking_changes", "dependency_issues", "summary", "migration_risks"],
                }},
            }
        }]
    }

    system_prompt = """You are an expert AWS Lambda Runtime Migration Analyst.
Your job is to analyze Lambda function source code and assess migration complexity.
You MUST use the submit_assessment tool to return your analysis. DO NOT respond with plain text.
You have deep knowledge of all AWS Lambda supported runtimes and their deprecation history."""

    context_lines = [
        f"- Function: {function_arn}",
        f"- Current Runtime: {runtime}",
        f"- Lines of Code: {code_info['lines_of_code']}",
        f"- Source Files: {code_info['num_source_files']}",
        f"- Manifests: {code_info.get('num_manifests', 0)}",
    ]
    if code_info.get("source_truncated"):
        context_lines.append("- Source truncated: yes (assessment is partial)")

    prompt = f"""## Task Summary:
Analyze this AWS Lambda function's source code and assess its runtime upgrade.
Your findings are handed to an automated upgrade agent (AWS Transform) as advisory context, so name the concrete modules, APIs and package versions it can act on. Never invent issues.

## Context Information:
{chr(10).join(context_lines)}

## Source Code:
{code_info['code_content']}

## Model Instructions:
- Identify ALL deprecated APIs, imports, modules, or patterns in the code that will break or are removed in the next supported major runtime of this language
- Identify ALL breaking changes between the current runtime and the next supported major runtime of this language that affect this code
- Flag dependency compatibility issues (pinned versions, known incompatible packages)
- Classify complexity as LOW (no breaking changes, simple bump), MEDIUM (some deprecated APIs to update), or HIGH (major refactoring needed)
- Provide a clear summary explaining your reasoning
- If the code is clean with no issues, say so explicitly — do not invent problems

## Response style and format requirements:
- You MUST use the submit_assessment tool
- DO NOT respond with plain text
- Be specific — reference actual code patterns or module names you found
- If no issues found, return empty arrays for deprecated_apis, breaking_changes, dependency_issues"""

    response = bedrock.converse(
        modelId=_model_id(),
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"temperature": 0},
        toolConfig=tool_config,
    )
    for block in response.get("output", {}).get("message", {}).get("content", []):
        if "toolUse" in block:
            return block["toolUse"].get("input", {})
    raise RuntimeError("Nova did not return a toolUse block in the assessment response")


# ---------------------------------------------------------------------------
# Step entry point (called inside a durable step, per function)
# ---------------------------------------------------------------------------

def _table():
    """The inventory table ALWAYS lives in the hub account's deploy region.

    Assess runs per-function across whatever region the function lives in (and
    possibly an assumed spoke session), but the single inventory table is in the
    hub/deploy region. So DynamoDB must NOT follow the scan session: every table
    access uses the Lambda's own credentials and own region (same rule as
    discover._table).
    """
    return boto3.resource("dynamodb", region_name=get_region()).Table(TABLE_NAME)


def assess_one(session: boto3.session.Session, target: dict, when: str) -> dict:
    """Assess ONE function. target = {item_key, function_arn, account_id, region, runtime}.

    Writes the assessment back to the item_key row and returns bounded facts.
    """
    function_arn = target["function_arn"]
    region = target.get("region") or extract_region(function_arn, "")
    runtime = target.get("runtime", "unknown")
    key = target["item_key"]
    table = _table()

    download = download_function_code(session, function_arn, region)
    if download.get("skipped"):
        table.update_item(
            Key={"item_key": key},
            UpdateExpression="SET migration_status = :s, assessment_summary = :m, assessed_at = :t",
            ExpressionAttributeValues={":s": "SKIPPED", ":m": download["reason"], ":t": when},
        )
        return {"item_key": key, "function_arn": function_arn, "status": "SKIPPED",
                "reason": download["reason"]}

    code_info = read_source_code(download["extract_path"], download["source_files"])
    assessment = assess_with_ai(session, function_arn, runtime, code_info)

    updates = convert_floats({
        "migration_status": "ASSESSED",
        "migration_complexity": assessment.get("complexity", "MEDIUM"),
        "assessment_summary": assessment.get("summary", ""),
        "migration_risks": assessment.get("migration_risks", ""),
        "deprecated_apis": assessment.get("deprecated_apis", []),
        "breaking_changes": assessment.get("breaking_changes", []),
        "dependency_issues": assessment.get("dependency_issues", []),
        "lines_of_code": code_info["lines_of_code"],
        "num_source_files": code_info["num_source_files"],
        "num_manifests": code_info.get("num_manifests", 0),
        "source_truncated": bool(code_info.get("source_truncated", False)),
        "assessed_at": when,
    })

    # Field-scoped SET of the attributes the assessment owns (mirrors
    # migrate._set_fields); anything else on the fn# row is left untouched.
    set_parts, attr_names, attr_values = [], {}, {}
    for idx, (k, v) in enumerate(updates.items()):
        pn, pv = f"#a{idx}", f":v{idx}"
        set_parts.append(f"{pn} = {pv}")
        attr_names[pn] = k
        attr_values[pv] = v
    table.update_item(
        Key={"item_key": key},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
    )

    return {
        "item_key": key,
        "function_arn": function_arn,
        "status": "ASSESSED",
        "complexity": assessment.get("complexity", "MEDIUM"),
    }
