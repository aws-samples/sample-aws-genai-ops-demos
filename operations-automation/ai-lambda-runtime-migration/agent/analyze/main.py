"""
Lambda Runtime Migration Assistant — Phase 2: Analyze Agent

Downloads Lambda function code, runs deterministic file analysis (counting),
uploads source to S3, then sends the actual code to Amazon Nova 2 Lite
for AI-powered migration assessment (deprecated APIs, breaking changes,
complexity classification, target runtime recommendation).

Deployed to AgentCore as lambdaruntime_analyze.
"""

import io
import json
import logging
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.request import urlopen

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = BedrockAgentCoreApp()

TABLE_NAME = os.environ.get("TABLE_NAME", "lambda-runtime-migration")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
REGION = os.environ["AWS_DEFAULT_REGION"]  # Set by AgentCore environment variables — no fallback

ASSESSMENT_MODEL_ID = "global.amazon.nova-2-lite-v1:0"

# Directories to exclude (dependencies, not user code)
EXCLUDED_DIRS = {"node_modules", "__pycache__", ".git", ".aws-sam", "vendor",
                 "dist", "build", ".serverless", "layer", "opt"}

# Max source code chars to send to AI (avoid token limit issues)
MAX_CODE_CHARS = 50_000

_dynamodb_resource = None


def _get_table():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource.Table(TABLE_NAME)


def _convert_floats(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(item) for item in obj]
    return obj


def _extract_region(arn: str) -> str:
    parts = arn.split(":")
    return parts[3] if len(parts) >= 4 else REGION


def _extract_function_name(arn: str) -> str:
    parts = arn.split(":")
    return parts[6] if len(parts) >= 7 else arn


def _is_excluded(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    return any(p in EXCLUDED_DIRS for p in parts)


# ---------------------------------------------------------------------------
# Step 1: Download and extract function code
# ---------------------------------------------------------------------------

def download_function_code(function_arn: str) -> dict[str, Any]:
    """Download and extract a Lambda function's deployment package."""
    region = _extract_region(function_arn)
    function_name = _extract_function_name(function_arn)
    lambda_client = boto3.client("lambda", region_name=region)

    response = lambda_client.get_function(FunctionName=function_arn)

    pkg_type = response.get("Configuration", {}).get("PackageType", "Zip")
    if pkg_type == "Image":
        raise ValueError(f"{function_name} uses Image package type — cannot analyze.")

    code_location = response.get("Code", {}).get("Location")
    if not code_location:
        raise ValueError(f"No Code.Location for {function_name}.")

    # Validate URL scheme — Lambda API returns HTTPS presigned URLs only
    if not code_location.startswith("https://"):
        raise ValueError(f"Unexpected URL scheme in Code.Location for {function_name}: {code_location[:50]}")

    with urlopen(code_location) as resp:  # nosec B310 — URL is from AWS Lambda GetFunction API, HTTPS scheme validated above  # nosemgrep: dynamic-urllib-use-detected
        zip_bytes = resp.read()

    extract_dir = tempfile.mkdtemp(prefix=f"lambda_{function_name}_")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(extract_dir)

    source_files = []
    for root, _dirs, filenames in os.walk(extract_dir):
        for fname in filenames:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, extract_dir)
            if not _is_excluded(rel):
                source_files.append(rel)

    logger.info("Extracted %d source files for %s", len(source_files), function_name)
    return {
        "function_name": function_name,
        "extract_path": extract_dir,
        "source_files": source_files,
    }


# ---------------------------------------------------------------------------
# Step 2: Upload source code to S3
# ---------------------------------------------------------------------------

def upload_code_to_s3(function_name: str, extract_path: str, source_files: list[str]) -> str:
    s3 = boto3.client("s3")
    prefix = f"functions/{function_name}/original"
    for rel_path in source_files:
        full_path = os.path.join(extract_path, rel_path)
        try:
            s3.upload_file(full_path, BUCKET_NAME, f"{prefix}/{rel_path}")
        except Exception as e:
            logger.error("Failed to upload %s: %s", rel_path, e)
    logger.info("Uploaded %d files to s3://%s/%s/", len(source_files), BUCKET_NAME, prefix)
    return prefix


# ---------------------------------------------------------------------------
# Step 3: Read source code + count lines (deterministic)
# ---------------------------------------------------------------------------

SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".mjs", ".cjs", ".java", ".rb", ".cs", ".go"}


def read_source_code(extract_path: str, source_files: list[str]) -> dict[str, Any]:
    """Read source files, count lines, and build code content for AI analysis."""
    code_blocks = []
    total_lines = 0
    total_chars = 0
    num_source_files = 0

    for rel_path in sorted(source_files):
        ext = os.path.splitext(rel_path)[1].lower()
        if ext not in SOURCE_EXTENSIONS:
            continue

        full_path = os.path.join(extract_path, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        num_source_files += 1
        lines = content.count("\n") + 1
        total_lines += lines

        # Build code block for AI, respecting token budget
        if total_chars < MAX_CODE_CHARS:
            remaining = MAX_CODE_CHARS - total_chars
            truncated = content[:remaining]
            code_blocks.append(f"--- {rel_path} ({lines} lines) ---\n{truncated}")
            total_chars += len(truncated)

    code_content = "\n\n".join(code_blocks)
    if total_chars >= MAX_CODE_CHARS:
        code_content += f"\n\n[TRUNCATED — total source exceeds {MAX_CODE_CHARS} chars]"

    return {
        "lines_of_code": total_lines,
        "num_source_files": num_source_files,
        "code_content": code_content,
    }


# ---------------------------------------------------------------------------
# Step 4: AI Assessment — Nova 2 Lite with constrained decoding
# ---------------------------------------------------------------------------

def assess_with_ai(function_arn: str, runtime: str, code_info: dict) -> dict[str, Any]:
    """Send actual source code to Nova for comprehensive migration assessment."""
    bedrock = boto3.client("bedrock-runtime", region_name=REGION)

    tool_config = {
        "tools": [{
            "toolSpec": {
                "name": "submit_assessment",
                "description": "Submit the complete migration assessment for a Lambda function",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "complexity": {
                                "type": "string",
                                "description": "Migration complexity classification",
                                "enum": ["LOW", "MEDIUM", "HIGH"],
                            },
                            "target_runtime": {
                                "type": "string",
                                "description": "Recommended target AWS Lambda runtime (e.g., python3.13, nodejs22.x, java21)",
                            },
                            "deprecated_apis": {
                                "type": "array",
                                "description": "List of deprecated APIs, imports, or patterns found in the code",
                                "items": {"type": "string"},
                            },
                            "breaking_changes": {
                                "type": "array",
                                "description": "List of breaking changes that will occur when migrating to the target runtime",
                                "items": {"type": "string"},
                            },
                            "dependency_issues": {
                                "type": "array",
                                "description": "List of dependency compatibility concerns for the target runtime",
                                "items": {"type": "string"},
                            },
                            "summary": {
                                "type": "string",
                                "description": "Concise migration assessment summary explaining complexity and key considerations",
                            },
                            "migration_risks": {
                                "type": "string",
                                "description": "Key risks to watch for during migration",
                            },
                        },
                        "required": ["complexity", "target_runtime", "deprecated_apis",
                                     "breaking_changes", "dependency_issues", "summary", "migration_risks"],
                    }
                },
            }
        }]
    }

    system_prompt = """You are an expert AWS Lambda Runtime Migration Analyst.
Your job is to analyze Lambda function source code and assess migration complexity.
You MUST use the submit_assessment tool to return your analysis. DO NOT respond with plain text.
You have deep knowledge of all AWS Lambda supported runtimes and their deprecation history."""

    prompt = f"""## Task Summary:
Analyze this AWS Lambda function's source code and provide a comprehensive migration assessment.

## Context Information:
- Function: {function_arn}
- Current Runtime: {runtime}
- Lines of Code: {code_info['lines_of_code']}
- Source Files: {code_info['num_source_files']}

## Source Code:
{code_info['code_content']}

## Model Instructions:
- Recommend the latest supported AWS Lambda runtime for this language as target_runtime
- Identify ALL deprecated APIs, imports, modules, or patterns in the code that will break or are removed in the target runtime
- Identify ALL breaking changes between the current and target runtime that affect this code
- Flag dependency compatibility issues (pinned versions, known incompatible packages)
- Classify complexity as LOW (no breaking changes, simple bump), MEDIUM (some deprecated APIs to update), or HIGH (major refactoring needed)
- Provide a clear summary explaining your reasoning
- If the code is clean with no issues, say so explicitly — do not invent problems

## Response style and format requirements:
- You MUST use the submit_assessment tool
- DO NOT respond with plain text
- Be specific — reference actual code patterns, line references, or module names you found
- If no issues found, return empty arrays for deprecated_apis, breaking_changes, dependency_issues"""

    response = bedrock.converse(
        modelId=ASSESSMENT_MODEL_ID,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"temperature": 0},
        toolConfig=tool_config,
    )

    content = response.get("output", {}).get("message", {}).get("content", [])
    for block in content:
        if "toolUse" in block:
            result = block["toolUse"].get("input", {})
            logger.info("AI Assessment: %s complexity, target %s, %d deprecated APIs, %d breaking changes",
                        result.get("complexity"), result.get("target_runtime"),
                        len(result.get("deprecated_apis", [])), len(result.get("breaking_changes", [])))
            return result

    raise RuntimeError("Nova did not return a toolUse block in the assessment response")


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle_analyze(payload: dict) -> dict:
    """Run Phase 2: Download code + AI-powered assessment."""
    function_arn = payload.get("function_arn")
    if not function_arn:
        raise ValueError("Missing function_arn in payload")

    logger.info("=== Phase 2: Analyze %s ===", function_arn)
    table = _get_table()

    # Update status to ANALYZING
    table.update_item(
        Key={"function_arn": function_arn},
        UpdateExpression="SET migration_status = :s",
        ExpressionAttributeValues={":s": "ANALYZING"},
    )

    # Get current runtime from DynamoDB
    item = table.get_item(Key={"function_arn": function_arn}).get("Item", {})
    runtime = item.get("runtime", "unknown")

    # Step 1: Download code
    logger.info("Step 1: Downloading function code...")
    download = download_function_code(function_arn)

    # Step 2: Upload source code to S3
    logger.info("Step 2: Uploading %d source files to S3...", len(download["source_files"]))
    s3_prefix = upload_code_to_s3(download["function_name"], download["extract_path"], download["source_files"])

    # Step 3: Read source code + count lines (deterministic)
    logger.info("Step 3: Reading source code...")
    code_info = read_source_code(download["extract_path"], download["source_files"])
    logger.info("Deterministic: %d LOC, %d source files", code_info["lines_of_code"], code_info["num_source_files"])

    # Step 4: AI assessment — send actual code to Nova
    logger.info("Step 4: Running AI assessment on source code...")
    assessment = assess_with_ai(function_arn, runtime, code_info)

    # Step 5: Store findings in S3
    logger.info("Step 5: Storing findings...")
    now = datetime.now(timezone.utc).isoformat()
    s3 = boto3.client("s3")
    combined = {
        "function_arn": function_arn,
        "runtime": runtime,
        "lines_of_code": code_info["lines_of_code"],
        "num_source_files": code_info["num_source_files"],
        **assessment,
        "assessed_at": now,
    }
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"functions/{download['function_name']}/analysis.json",
        Body=json.dumps(combined, indent=2),
        ContentType="application/json",
    )

    # Step 6: Update DynamoDB
    logger.info("Step 6: Updating DynamoDB...")
    updates = _convert_floats({
        "migration_status": "ASSESSED",
        "migration_complexity": assessment.get("complexity", "MEDIUM"),
        "target_runtime": assessment.get("target_runtime", ""),
        "assessment_summary": assessment.get("summary", ""),
        "migration_risks": assessment.get("migration_risks", ""),
        "deprecated_apis": assessment.get("deprecated_apis", []),
        "breaking_changes": assessment.get("breaking_changes", []),
        "dependency_issues": assessment.get("dependency_issues", []),
        "lines_of_code": code_info["lines_of_code"],
        "num_source_files": code_info["num_source_files"],
        "s3_original_code_path": f"s3://{BUCKET_NAME}/functions/{download['function_name']}/original/",
        "s3_findings_path": f"s3://{BUCKET_NAME}/functions/{download['function_name']}/analysis.json",
        "assessed_at": now,
    })

    set_parts, attr_names, attr_values = [], {}, {}
    for idx, (key, value) in enumerate(updates.items()):
        pn, pv = f"#a{idx}", f":v{idx}"
        set_parts.append(f"{pn} = {pv}")
        attr_names[pn] = key
        attr_values[pv] = value

    table.update_item(
        Key={"function_arn": function_arn},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
    )

    # Return response — clearly separate deterministic vs AI fields
    return {
        "phase": "analyze",
        "status": "complete",
        "function_arn": function_arn,
        "current_runtime": runtime,
        # Deterministic fields
        "lines_of_code": code_info["lines_of_code"],
        "num_source_files": code_info["num_source_files"],
        "source_files": download["source_files"],
        "s3_original_code_path": f"s3://{BUCKET_NAME}/functions/{download['function_name']}/original/",
        "s3_findings_path": f"s3://{BUCKET_NAME}/functions/{download['function_name']}/analysis.json",
        "assessed_at": now,
        # AI-generated fields
        "complexity": assessment.get("complexity", "MEDIUM"),
        "target_runtime": assessment.get("target_runtime", ""),
        "deprecated_apis": assessment.get("deprecated_apis", []),
        "breaking_changes": assessment.get("breaking_changes", []),
        "dependency_issues": assessment.get("dependency_issues", []),
        "summary": assessment.get("summary", ""),
        "migration_risks": assessment.get("migration_risks", ""),
    }


@app.entrypoint
def invoke(payload):
    """Main entry point for the analyze agent."""
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
        return handle_analyze(payload)
    except Exception as e:
        logger.exception("Error processing analyze request")
        return {"error": str(e)}


if __name__ == "__main__":
    app.run()
