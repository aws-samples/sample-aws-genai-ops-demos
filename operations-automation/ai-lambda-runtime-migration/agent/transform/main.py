"""
Lambda Runtime Migration Assistant — Phase 3: Transform Agent

Uses Amazon Nova 2 Lite (Converse API) to generate migrated code,
then validates via AgentCore Code Interpreter (boto3 session management),
and saves migrated files directly to S3.

Note: Strands Agent framework would simplify this (automatic tool loop,
Code Interpreter as a tool, no manual session management) but requires
container deployment due to package size. For zip-based code deployment,
we use plain boto3. Consider Strands Graph with Loops pattern for a
more structured approach:
https://strandsagents.com/docs/examples/python/graph_loops_example/

Deployed to AgentCore as lambdaruntime_transform.
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = BedrockAgentCoreApp()

TABLE_NAME = os.environ.get("TABLE_NAME", "lambda-runtime-migration")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
REGION = os.environ["AWS_DEFAULT_REGION"]  # Set by AgentCore environment variables — no fallback

TRANSFORM_MODEL_ID = "global.amazon.nova-2-lite-v1:0"
MAX_RETRIES = 3

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


def _extract_function_name(arn: str) -> str:
    parts = arn.split(":")
    return parts[6] if len(parts) >= 7 else arn


# ---------------------------------------------------------------------------
# Step 1: Load context from S3 + DynamoDB
# ---------------------------------------------------------------------------

def load_context(function_arn: str) -> dict[str, Any]:
    """Load original source code from S3 and Phase 2 assessment from DynamoDB."""
    table = _get_table()
    s3 = boto3.client("s3")
    function_name = _extract_function_name(function_arn)

    item = table.get_item(Key={"function_arn": function_arn}).get("Item", {})
    if not item:
        raise ValueError(f"No DynamoDB record for {function_arn}. Run Phase 1 first.")

    findings = {}
    try:
        resp = s3.get_object(Bucket=BUCKET_NAME, Key=f"functions/{function_name}/analysis.json")
        findings = json.loads(resp["Body"].read())
    except Exception as e:
        logger.warning("No Phase 2 findings in S3: %s", e)

    # Extensions that are actual source code (not source maps, configs, etc.)
    SOURCE_EXTS = {".py", ".js", ".ts", ".mjs", ".cjs", ".java", ".rb", ".cs", ".go"}
    SKIP_SUFFIXES = {".map", ".d.ts.map", ".js.map", ".min.js", ".min.css"}

    source_files = {}
    code_prefix = f"functions/{function_name}/original/"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=code_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel_path = key[len(code_prefix):]
            if not rel_path or obj["Size"] >= 500_000:
                continue
            # Skip source maps and non-source files
            if any(rel_path.endswith(s) for s in SKIP_SUFFIXES):
                continue
            # Only include actual source code files
            ext = "." + rel_path.rsplit(".", 1)[-1] if "." in rel_path else ""
            if ext not in SOURCE_EXTS:
                continue
            try:
                body = s3.get_object(Bucket=BUCKET_NAME, Key=key)["Body"].read()
                source_files[rel_path] = body.decode("utf-8", errors="ignore")
            except Exception as e:
                logger.warning("Failed to read S3 object %s: %s", key, e)

    if not source_files:
        raise ValueError(f"No source code in S3 for {function_name}. Run Phase 2 first.")

    logger.info("Loaded %d source files for %s", len(source_files), function_name)
    return {
        "db_record": item,
        "findings": findings,
        "source_files": source_files,
        "function_name": function_name,
        "runtime": str(item.get("runtime", "unknown")),
        "target_runtime": str(item.get("target_runtime", findings.get("target_runtime", "unknown"))),
    }


# ---------------------------------------------------------------------------
# Step 2: Generate migrated code — Nova 2 Lite via Converse API
# ---------------------------------------------------------------------------

def generate_migrated_code(context: dict, validation_errors: list[str] | None = None) -> tuple[dict[str, str], str]:
    """Generate migrated code file-by-file. One LLM call per file — no parsing needed.
    
    Returns (migrated_files dict, changelog string).
    """
    from botocore.config import Config
    bedrock = boto3.client("bedrock-runtime", region_name=REGION,
                          config=Config(read_timeout=300, retries={"max_attempts": 2}))
    findings = context.get("findings", {})
    db = context.get("db_record", {})

    # Shared assessment context for all files
    assessment = f"""Current runtime: {context['runtime']}
Target runtime: {context['target_runtime']}
Deprecated APIs: {findings.get('deprecated_apis', db.get('deprecated_apis', []))}
Breaking Changes: {findings.get('breaking_changes', db.get('breaking_changes', []))}
Dependency Issues: {findings.get('dependency_issues', db.get('dependency_issues', []))}
Summary: {findings.get('summary', db.get('assessment_summary', ''))}"""

    system_prompt = """You are an expert AWS Lambda Runtime Migration Engineer.
You will receive a single source file to migrate to a new runtime.
Output ONLY the complete migrated file content — no markdown, no code fences, no explanations.
Preserve original logic — only change what's necessary for runtime compatibility.
Add inline comments explaining each change you make."""

    migrated_files = {}

    for filename, original_content in context["source_files"].items():
        retry_hint = ""
        if validation_errors:
            # Find errors relevant to this file
            file_errors = [e for e in validation_errors if filename in e]
            if file_errors:
                retry_hint = f"\n\nPrevious validation errors for this file — fix these:\n" + "\n".join(file_errors)

        prompt = f"""## Assessment:
{assessment}

## File to migrate: {filename}
{original_content}
{retry_hint}
Output the complete migrated file content. Nothing else — no markdown fences, no filename header, no commentary."""

        logger.info("Generating migrated code for %s (%d chars)...", filename, len(original_content))
        try:
            response = bedrock.converse(
                modelId=TRANSFORM_MODEL_ID,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"temperature": 0.1, "maxTokens": 8192},
            )

            text = ""
            for block in response.get("output", {}).get("message", {}).get("content", []):
                if "text" in block:
                    text += block["text"]

            if text:
                # Strip any accidental code fences the model might add despite instructions
                content = text.strip()
                if content.startswith("```"):
                    # Remove opening fence (```python, ```js, ```filename, etc.)
                    first_newline = content.find("\n")
                    if first_newline != -1:
                        content = content[first_newline + 1:]
                if content.endswith("```"):
                    content = content[:-3].rstrip()
                migrated_files[filename] = content
                logger.info("Generated %s: %d chars", filename, len(content))
            else:
                logger.warning("Empty response for %s", filename)
        except Exception as e:
            logger.error("Failed to generate %s: %s", filename, e)

    # Generate changelog in a separate call
    changelog = ""
    if migrated_files:
        try:
            changelog_prompt = f"""## Assessment:
{assessment}

## Files migrated: {list(migrated_files.keys())}

Write a brief markdown changelog summarizing the migration changes.
List what was changed per file and why. Be concise."""

            response = bedrock.converse(
                modelId=TRANSFORM_MODEL_ID,
                system=[{"text": "You are a technical writer. Output only the changelog in markdown. No code fences."}],
                messages=[{"role": "user", "content": [{"text": changelog_prompt}]}],
                inferenceConfig={"temperature": 0.1, "maxTokens": 2048},
            )
            for block in response.get("output", {}).get("message", {}).get("content", []):
                if "text" in block:
                    changelog += block["text"]
            changelog = changelog.strip()
            logger.info("Generated changelog: %d chars", len(changelog))
        except Exception as e:
            logger.warning("Changelog generation failed: %s", e)

    logger.info("Generated %d files: %s", len(migrated_files), list(migrated_files.keys()))
    return migrated_files, changelog


# ---------------------------------------------------------------------------
# Step 3: Validate via Code Interpreter
# ---------------------------------------------------------------------------

def validate_with_code_interpreter(migrated_files: dict[str, str], target_runtime: str) -> dict[str, Any]:
    """Validate migrated code using AgentCore Code Interpreter (boto3 session)."""
    ci = boto3.client("bedrock-agentcore", region_name=REGION)

    # Only validate Python files
    py_files = {k: v for k, v in migrated_files.items() if k.endswith(".py")}
    if not py_files:
        return {"valid": True, "errors": [], "skipped": True, "note": "No Python files to validate — JS/TS validation not supported"}

    # Build validation script — ast.parse for syntax + import completeness check
    script_parts = ["import ast", "import json", "import builtins", "results = []"]
    for filename, content in py_files.items():
        escaped = json.dumps(content)
        script_parts.append(f"""
with open("{filename}", "w") as f:
    f.write({escaped})
try:
    tree = ast.parse({escaped})
    errors = []
    
    # Collect all imported names
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split('.')[0])
            for alias in node.names:
                imported.add(alias.asname or alias.name)
    
    # Collect all top-level names used (function calls, attribute access)
    used_modules = set()
    for node in ast.walk(tree):
        # Catch module.something() patterns like threading.Thread, configparser.ConfigParser
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used_modules.add(node.value.id)
    
    # Check for standard library modules used but not imported
    stdlib_modules = {{'threading', 'configparser', 'xml', 'ET', 'asyncio', 'ssl', 'json', 
                      'os', 're', 'logging', 'io', 'sys', 'hashlib', 'hmac', 'base64',
                      'locale', 'shlex', 'subprocess', 'importlib', 'collections',
                      'typing', 'functools', 'itertools', 'pathlib', 'tempfile', 'time'}}
    builtin_names = set(dir(builtins))
    
    # Names defined in the file (functions, classes, assignments)
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
    
    missing = used_modules & stdlib_modules - imported - builtin_names - defined
    if missing:
        errors.append(f"Missing imports: {{', '.join(sorted(missing))}}")
    
    if errors:
        results.append({{"file": "{filename}", "status": "fail", "error": "; ".join(errors)}})
    else:
        results.append({{"file": "{filename}", "status": "pass"}})
except SyntaxError as e:
    results.append({{"file": "{filename}", "status": "fail", "error": f"Line {{e.lineno}}: {{e.msg}}"}})
""")
    script_parts.append('print(json.dumps({"valid": all(r["status"]=="pass" for r in results), "errors": [f"{r[\'file\']}: {r.get(\'error\',\'\')}" for r in results if r["status"]=="fail"], "results": results}))')
    script = "\n".join(script_parts)

    try:
        # Start session
        session_resp = ci.start_code_interpreter_session(
            codeInterpreterIdentifier="aws.codeinterpreter.v1",
            name="migration-validation",
            sessionTimeoutSeconds=300,
        )
        session_id = session_resp["sessionId"]
        logger.info("Code Interpreter session started: %s", session_id)

        try:
            # Execute validation
            exec_resp = ci.invoke_code_interpreter(
                codeInterpreterIdentifier="aws.codeinterpreter.v1",
                sessionId=session_id,
                name="executeCode",
                arguments={"language": "python", "code": script},
            )

            # Parse streaming response
            output = ""
            for event in exec_resp.get("stream", []):
                if "result" in event:
                    for item in event["result"].get("content", []):
                        if item.get("type") == "text":
                            output += item["text"]

            logger.info("Code Interpreter output: %s", output[:500])
            return json.loads(output.strip())

        finally:
            ci.stop_code_interpreter_session(
                codeInterpreterIdentifier="aws.codeinterpreter.v1",
                sessionId=session_id,
            )
            logger.info("Code Interpreter session stopped")

    except Exception as e:
        logger.error("Code Interpreter validation failed: %s", e)
        return {"valid": False, "errors": [f"Code Interpreter error: {e}"], "ci_error": True}


# ---------------------------------------------------------------------------
# Step 4: Save migrated files to S3
# ---------------------------------------------------------------------------

def save_to_s3(function_name: str, migrated_files: dict[str, str], changelog: str,
               validation: dict) -> str:
    """Save migrated files, changelog, and validation report to S3."""
    s3 = boto3.client("s3")
    base = f"functions/{function_name}"

    for path, content in migrated_files.items():
        s3.put_object(Bucket=BUCKET_NAME, Key=f"{base}/migrated/{path}", Body=content.encode("utf-8"))
        logger.info("Saved: s3://%s/%s/migrated/%s", BUCKET_NAME, base, path)

    if changelog:
        s3.put_object(Bucket=BUCKET_NAME, Key=f"{base}/changelog.md",
                      Body=changelog.encode("utf-8"), ContentType="text/markdown")

    s3.put_object(Bucket=BUCKET_NAME, Key=f"{base}/validation.json",
                  Body=json.dumps(validation, indent=2).encode("utf-8"), ContentType="application/json")

    return f"s3://{BUCKET_NAME}/{base}/migrated/"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle_transform(payload: dict) -> dict:
    """Run Phase 3: Generate → Validate → Retry → Save.
    
    1. Load original code from S3 + Phase 2 assessment from DynamoDB (no AI)
    2. Generate migrated code via Nova 2 Lite (AI)
    3. Validate via Code Interpreter (deterministic)
    4. If fails → feed errors back to Nova → re-generate → re-validate (max 3)
    5. Save to S3 (no AI)
    6. Update DynamoDB (no AI)
    """
    function_arn = payload.get("function_arn")
    if not function_arn:
        raise ValueError("Missing function_arn in payload")

    function_name = _extract_function_name(function_arn)
    logger.info("=== Phase 3: Transform %s ===", function_name)
    table = _get_table()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Guardrail: ensure Phase 2 (Assess) has been completed BEFORE setting TRANSFORMING
        item = table.get_item(Key={"function_arn": function_arn}).get("Item", {})
        if not item:
            msg = f"No record found for {function_arn}. Run Phase 1 (Discover) first."
            logger.warning(msg)
            return {"phase": "transform", "status": "error", "error": msg, "function_arn": function_arn}

        current_status = item.get("migration_status", "DISCOVERED")

        # Only allow transform from ASSESSED, READY_TO_MIGRATE (re-run), TRANSFORM_FAILED (retry), or TRANSFORMING (stuck retry)
        allowed_statuses = {"ASSESSED", "READY_TO_MIGRATE", "TRANSFORM_FAILED", "TRANSFORMING"}
        if current_status not in allowed_statuses:
            msg = f"Cannot transform: function is in '{current_status}' status. Please run the assessment (Phase 2) first."
            logger.warning(msg)
            return {"phase": "transform", "status": "error", "error": msg, "function_arn": function_arn}

        # Also check if Phase 2 actually produced source code in S3
        if not item.get("s3_original_code_path"):
            msg = "Cannot transform: no source code found in S3. Please run the assessment (Phase 2) first."
            logger.warning(msg)
            return {"phase": "transform", "status": "error", "error": msg, "function_arn": function_arn}

        # All guardrails passed — NOW set TRANSFORMING
        table.update_item(
            Key={"function_arn": function_arn},
            UpdateExpression="SET migration_status = :s",
            ExpressionAttributeValues={":s": "TRANSFORMING"},
        )

        # Step 1: Load context
        logger.info("Step 1: Loading context...")
        context = load_context(function_arn)

        # Step 2+3: Generate and validate with retry loop
        migrated_files = {}
        changelog = ""
        validation = {"valid": False, "errors": ["Not yet validated"]}
        validation_errors = None
        successful_attempt = 0

        for attempt in range(1, MAX_RETRIES + 1):
            logger.info("Step 2: Generating code (attempt %d/%d)...", attempt, MAX_RETRIES)
            migrated_files, changelog = generate_migrated_code(context, validation_errors)

            if not migrated_files:
                logger.warning("No files generated on attempt %d", attempt)
                validation_errors = ["No files were generated. Generate all source files."]
                continue

            logger.info("Step 3: Validating via Code Interpreter...")
            validation = validate_with_code_interpreter(migrated_files, context["target_runtime"])

            if validation.get("valid", False):
                successful_attempt = attempt
                logger.info("Validation PASSED on attempt %d", attempt)
                break
            else:
                validation_errors = validation.get("errors", ["Unknown error"])
                logger.warning("Validation FAILED on attempt %d: %s", attempt, validation_errors)

        # Enrich validation with transparency metadata
        py_count = sum(1 for f in migrated_files if f.endswith(".py"))
        validation["attempts"] = successful_attempt if successful_attempt else MAX_RETRIES
        validation["max_attempts"] = MAX_RETRIES
        validation["passed_on_attempt"] = successful_attempt if successful_attempt else 0
        validation["files_validated"] = py_count
        validation["checks"] = ["syntax (ast.parse)", "import completeness (AST walk)"] if py_count > 0 else []

        # Step 4: Save to S3
        success = validation.get("valid", False) and len(migrated_files) > 0
        s3_path = ""
        if migrated_files:
            logger.info("Step 4: Saving to S3...")
            s3_path = save_to_s3(function_name, migrated_files, changelog, validation)

        # Step 5: Update DynamoDB
        status = "READY_TO_MIGRATE" if success else "TRANSFORM_FAILED"
        logger.info("Step 5: Updating DynamoDB — %s", status)

        update_expr = "SET migration_status = :s, transformed_at = :t, s3_migrated_code_path = :p, migrated_files_list = :mf, changelog = :cl, validation_result = :vr"
        expr_values: dict[str, Any] = {":s": status, ":t": now, ":p": s3_path, ":mf": list(migrated_files.keys()), ":cl": changelog or "", ":vr": _convert_floats(validation)}

        if not success:
            error_msg = "; ".join(validation.get("errors", ["No files generated"]))
            update_expr += ", transform_error = :e"
            expr_values[":e"] = error_msg
        else:
            update_expr += " REMOVE transform_error"

        table.update_item(
            Key={"function_arn": function_arn},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

        return {
            "phase": "transform",
            "status": "complete" if success else "validation_failed",
            "function_arn": function_arn,
            "migration_status": status,
            "migrated_files": list(migrated_files.keys()),
            "validation": validation,
            "s3_path": s3_path,
            "changelog": changelog,
            "transformed_at": now,
        }

    except Exception as e:
        logger.exception("Transform failed for %s", function_arn)
        error_msg = str(e)
        try:
            table.update_item(
                Key={"function_arn": function_arn},
                UpdateExpression="SET migration_status = :s, transform_error = :e, transformed_at = :t",
                ExpressionAttributeValues={":s": "TRANSFORM_FAILED", ":e": error_msg, ":t": now},
            )
        except Exception as db_err:
            logger.error("Failed to update DynamoDB after transform error: %s", db_err)
        return {"phase": "transform", "status": "error", "error": error_msg, "function_arn": function_arn}


@app.entrypoint
def invoke(payload):
    """Main entry point for the transform agent."""
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
        return handle_transform(payload)
    except Exception as e:
        logger.exception("Error processing transform request")
        return {"error": str(e)}


if __name__ == "__main__":
    app.run()
