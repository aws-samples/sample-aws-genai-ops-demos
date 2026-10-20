"""DART agent entry points.

Exposes the seven Strands ``@tool`` wrappers, ``build_agent()``, the ``run_preflight`` /
``handle_request`` / ``lambda_handler`` entry points, and a CLI. Offline mode
(``DART_OFFLINE=1``) bypasses the Strands reasoning loop and drives the fixed FR-4.1 tool
chain directly with a scripted Comprehend client and the templated-narrative fallback, so
the CLI runs with no AWS credentials (AC-V4).
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from config import DEFAULT_MODEL_ID, Config

# Tool implementations (imported with a private alias; the public names below are the
# Strands @tool wrappers).
from tools import generate_preflight_report as _report_mod
from tools import scan_pii as _scan_pii_mod
from tools.apply_safe_fixes import apply_safe_fixes as _apply_safe_fixes
from tools.check_training_viability import (
    check_training_viability as _check_training_viability,
)
from tools.detect_duplicates import detect_duplicates as _detect_duplicates
from tools.estimate_training_cost import estimate_training_cost as _estimate_training_cost
from tools.profile_dataset import profile_dataset as _profile_dataset

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(message)s",
)
logger = logging.getLogger("dart.agent")

_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


def _load_system_prompt() -> str:
    """Read the system prompt at import time; empty string if absent."""
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


SYSTEM_PROMPT = _load_system_prompt()


# ---------------------------------------------------------------------------
# Strands @tool wrappers (exact signatures). The import of `tool` is deferred so the
# module stays importable (and offline/tests work) even if strands is not installed.
# ---------------------------------------------------------------------------

try:
    from strands import tool
except Exception:  # pragma: no cover - strands optional at import time

    def tool(func=None, **tool_kwargs):  # type: ignore[no-redef,misc]
        """No-op fallback decorator when strands is unavailable."""
        if func is None:
            return lambda f: f
        return func


@tool
def profile_dataset(
    dataset_path: str,
    target_model: str = DEFAULT_MODEL_ID,
) -> dict[str, Any]:
    """Profile a dataset: format, encoding, schema, record and token statistics."""
    return _profile_dataset(dataset_path, target_model)


@tool
def detect_duplicates(
    dataset_path: str,
    similarity_threshold: float = 0.85,
) -> dict[str, Any]:
    """Detect exact (SHA-256) and near (MinHash LSH) duplicate records."""
    return _detect_duplicates(dataset_path, similarity_threshold)


@tool
def scan_pii(
    dataset_path: str,
    redaction_strategy: str = "mask",
    produce_redacted_copy: bool = False,
    output_path: str = "",
) -> dict[str, Any]:
    """Scan for PII with Amazon Comprehend; never reports an incomplete scan as clean."""
    return _scan_pii_mod.scan_pii(
        dataset_path, redaction_strategy, produce_redacted_copy, output_path
    )


@tool
def check_training_viability(
    dataset_path: str,
    target_model: str = DEFAULT_MODEL_ID,
    validation_path: str = "",
) -> dict[str, Any]:
    """Check token budget, train/val leakage, size, and schema viability."""
    return _check_training_viability(dataset_path, target_model, validation_path)


@tool
def estimate_training_cost(
    total_tokens: int,
    target_model: str = DEFAULT_MODEL_ID,
    instance_type: str = "ml.p4d.24xlarge",
    num_nodes: int = 4,
    epochs: int = 3,
    duplicate_rate: float = 0.0,
    empty_record_rate: float = 0.0,
    use_bedrock_managed: bool = False,
) -> dict[str, Any]:
    """Estimate fine-tuning cost before and after safe fixes."""
    return _estimate_training_cost(
        total_tokens,
        target_model,
        instance_type,
        num_nodes,
        epochs,
        duplicate_rate,
        empty_record_rate,
        use_bedrock_managed,
    )


@tool
def apply_safe_fixes(
    dataset_path: str,
    output_path: str = "",
    remove_exact_duplicates: bool = True,
    remove_empty_records: bool = True,
    fix_encoding: bool = True,
) -> dict[str, Any]:
    """Write a NEW cleaned file (original never modified) removing dupes/empties."""
    return _apply_safe_fixes(
        dataset_path,
        output_path,
        remove_exact_duplicates,
        remove_empty_records,
        fix_encoding,
    )


@tool
def generate_preflight_report(
    dataset_path: str,
    target_model: str,
    profile_result: Any,
    duplicates_result: Any,
    pii_result: Any,
    viability_result: Any,
    cost_result: Any,
    run_id: str = "",
) -> dict[str, Any]:
    """Build the verdict/score/findings/narrative report from the five tool outputs.

    The five upstream results arrive from the agent as JSON strings; parse them before
    delegating to the implementation.
    """

    def _parse(value: Any) -> dict[str, Any]:
        return json.loads(value) if isinstance(value, str) else value

    return _report_mod.generate_preflight_report(
        dataset_path,
        target_model,
        _parse(profile_result),
        _parse(duplicates_result),
        _parse(pii_result),
        _parse(viability_result),
        _parse(cost_result),
        run_id,
    )


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


def build_agent() -> Any:
    """Construct the Strands ``Agent`` wired with the model, prompt, and seven tools."""
    from strands import Agent
    from strands.models import BedrockModel

    model = BedrockModel(
        model_id=Config.MODEL_ID,
        region_name=Config.AWS_REGION,
        max_tokens=Config.MAX_TOKENS,
    )
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            profile_dataset,
            detect_duplicates,
            scan_pii,
            check_training_viability,
            estimate_training_cost,
            apply_safe_fixes,
            generate_preflight_report,
        ],
    )


def _extract_dataset_path(user_message: str) -> str:
    """Pull the first s3:// URI or local path token out of the user message."""
    for token in user_message.replace(",", " ").split():
        if token.startswith("s3://") or token.endswith(
            (".jsonl", ".ndjson", ".json", ".csv", ".parquet", ".pq")
        ):
            return token
    raise ValueError("Could not find a dataset path in the request message.")


def _extract_target_model(user_message: str) -> str:
    """Infer a target model hint from the message, defaulting to the demo default."""
    lowered = user_message.lower()
    hints = {
        "llama3-70b": "meta.llama3-70b-instruct-v1:0",
        "llama3-8b": "meta.llama3-8b-instruct-v1:0",
        "nova-pro": "amazon.nova-pro-v1:0",
        "haiku": "anthropic.claude-haiku-4-5",
    }
    for needle, model in hints.items():
        if needle in lowered:
            return model
    return DEFAULT_MODEL_ID


# ---------------------------------------------------------------------------
# Offline deterministic tool chain (AC-V4) — no agent loop, no live AWS
# ---------------------------------------------------------------------------


def _offline_comprehend_client() -> Any:
    """Canned Comprehend client: returns an EMAIL entity for any document containing '@'."""

    class _CannedComprehend:
        def detect_pii_entities(self, Text: str, LanguageCode: str) -> dict[str, Any]:  # noqa: N803
            at = Text.find("@")
            if at == -1:
                return {"Entities": []}
            # Expand to the surrounding token to cover the email address span.
            start = at
            while start > 0 and Text[start - 1] not in (" ", "\n", "\t", '"', "'"):
                start -= 1
            end = at
            while end < len(Text) and Text[end] not in (" ", "\n", "\t", '"', "'"):
                end += 1
            return {
                "Entities": [
                    {"Type": "EMAIL", "Score": 0.99, "BeginOffset": start, "EndOffset": end}
                ]
            }

    return _CannedComprehend()


def _run_offline_chain(dataset_path: str, target_model: str, run_id: str) -> dict[str, Any]:
    """Run the fixed FR-4.1 tool chain directly with scripted AWS seams."""
    profile_result = _profile_dataset(dataset_path, target_model)
    duplicates_result = _detect_duplicates(
        dataset_path, Config.DEDUP_SIMILARITY_THRESHOLD
    )

    # Inject the scripted Comprehend client so no real DetectPiiEntities is attempted.
    original_factory = _scan_pii_mod._comprehend_client_factory
    _scan_pii_mod._comprehend_client_factory = _offline_comprehend_client
    try:
        pii_result = _scan_pii_mod.scan_pii(dataset_path)
    finally:
        _scan_pii_mod._comprehend_client_factory = original_factory

    viability_result = _check_training_viability(dataset_path, target_model)

    total_tokens = int(profile_result.get("text_stats", {}).get("total_tokens", 0))
    cost_result = _estimate_training_cost(
        total_tokens,
        target_model=target_model,
        duplicate_rate=float(duplicates_result.get("duplicate_rate", 0.0)),
    )

    # Force the templated-narrative fallback (no bedrock-runtime call).
    original_bedrock = _report_mod._bedrock_client_factory

    def _raise_bedrock() -> Any:
        raise RuntimeError("offline mode: Bedrock disabled")

    _report_mod._bedrock_client_factory = _raise_bedrock
    try:
        report = _report_mod.generate_preflight_report(
            dataset_path,
            target_model,
            profile_result,
            duplicates_result,
            pii_result,
            viability_result,
            cost_result,
            run_id,
        )
    finally:
        _report_mod._bedrock_client_factory = original_bedrock

    return report


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _persist_report_to_s3(run_id: str, report_markdown: str) -> None:
    """Best-effort persistence of the Markdown report to the output bucket."""
    if not Config.OUTPUT_BUCKET:
        logger.warning(
            json.dumps(
                {"event": "report_persist_skipped", "run_id": run_id, "reason": "no_bucket"}
            )
        )
        return
    try:
        import boto3

        client = boto3.client("s3", region_name=Config.AWS_REGION)
        client.put_object(
            Bucket=Config.OUTPUT_BUCKET,
            Key=f"reports/{run_id}.md",
            Body=report_markdown.encode("utf-8"),
            ContentType="text/markdown",
        )
        logger.info(json.dumps({"event": "report_persisted", "run_id": run_id}))
    except Exception as err:  # noqa: BLE001 - best-effort, never fails the run
        logger.error(
            json.dumps(
                {"event": "report_persist_error", "run_id": run_id, "error": str(err)}
            )
        )


def run_preflight(user_message: str, run_id: str | None = None) -> str:
    """Run a pre-flight audit and return the Markdown report text.

    In offline mode (``Config.OFFLINE``) the Strands agent loop is bypassed and the fixed
    tool chain runs directly with scripted AWS seams. Otherwise the Strands agent is
    invoked, then the report is best-effort persisted to S3 and an audit record written.
    """
    Config.validate()
    if run_id is None:
        run_id = f"dart-{uuid.uuid4().hex[:8]}"

    if Config.OFFLINE:
        dataset_path = _extract_dataset_path(user_message)
        target_model = _extract_target_model(user_message)
        report = _run_offline_chain(dataset_path, target_model, run_id)
        return _render_report(report)

    agent = build_agent()
    result = agent(user_message)
    report_text = str(result)

    _persist_report_to_s3(run_id, report_text)
    try:
        from audit import write_audit_record

        write_audit_record(run_id=run_id, status="completed")
    except Exception as err:  # noqa: BLE001 - audit import/call is best-effort
        logger.error(
            json.dumps({"event": "audit_error", "run_id": run_id, "error": str(err)})
        )
    return report_text


def _render_report(report: dict[str, Any]) -> str:
    """Render a report dict to Markdown (narrative + a score/verdict header line)."""
    narrative = report.get("narrative", "")
    header = (
        f"Quality score: {report.get('quality_score')}/100 — "
        f"Recommendation: {report.get('recommendation')}"
    )
    return f"{header}\n\n{narrative}"


def handle_request(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Request handler: always generates a server-side run_id, ignoring caller-supplied ids."""
    message = (event or {}).get("message")
    if not message or not isinstance(message, str):
        return {
            "statusCode": 400,
            "error": "Missing 'message' field in request.",
        }

    run_id = f"dart-{uuid.uuid4().hex[:8]}"
    try:
        response = run_preflight(message, run_id=run_id)
        return {"statusCode": 200, "run_id": run_id, "response": response}
    except Exception as err:  # noqa: BLE001 - surface a generic error, log full detail
        logger.error(
            json.dumps(
                {"event": "handle_request_error", "run_id": run_id, "error": repr(err)}
            )
        )
        try:
            from audit import write_audit_record

            write_audit_record(run_id=run_id, status="error")
        except Exception:  # noqa: BLE001 - best-effort audit
            pass
        return {
            "statusCode": 500,
            "run_id": run_id,
            "error": "Internal error. See CloudWatch logs for this run_id.",
        }


# Lambda-style alias (FR-5.4).
lambda_handler = handle_request


def _main(argv: list[str]) -> int:
    """CLI: ``python agent.py "<message>"`` — prints the report and the run_id."""
    if len(argv) < 2 or not argv[1].strip():
        print('Usage: python agent.py "<request message>"', file=sys.stderr)
        return 2
    run_id = f"dart-{uuid.uuid4().hex[:8]}"
    report = run_preflight(argv[1], run_id=run_id)
    print(report)
    print(f"\n[run_id: {run_id}]")
    return 0


if __name__ == "__main__":
    # Re-read env so DART_OFFLINE / region set on the command line take effect.
    Config.refresh()
    raise SystemExit(_main(sys.argv))
