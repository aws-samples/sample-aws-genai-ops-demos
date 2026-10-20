"""Configuration for the DART agent.

Loads every runtime setting from environment variables with safe defaults and exposes the
static reference tables (PII entity types, known schemas, per-model context limits and
minimum-record floors). ``Config.validate()`` is the fail-fast gate called at container
entry.

Design contract (design.md section 2.6):

* ``MODEL_ID`` default is the **bare** base id ``anthropic.claude-sonnet-4-5-20250929-v1:0``
  with **no** CRIS (``us.``/``eu.``/``apac.``/``global.``) prefix. The prefix is applied only
  by the IaC at deploy time and injected as the ``MODEL_ID`` env var.
* ``MODEL_CONTEXT_LIMITS`` and ``MODEL_MIN_RECORDS`` are keyed on the **bare** base model id
  and MUST each contain an entry for the exact default id above plus a ``default`` fallback.
* The audit TTL is a hardcoded 365 days (in ``audit.py``), deliberately NOT an env var.
* ``DART_OFFLINE`` (``Config.OFFLINE``) is the verification/demo mocking seam; the deployed
  task never sets it.
"""

from __future__ import annotations

import os

from aws_utils import get_region

# The canonical demo default base model id (bare — no CRIS prefix). Every default in the
# demo keys off this single id (design.md section 3).
DEFAULT_MODEL_ID = "anthropic.claude-sonnet-4-5-20250929-v1:0"


def _as_bool(value: str) -> bool:
    """Interpret an env-var string as a boolean (truthy: 1/true/yes/on)."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Runtime configuration resolved from environment variables."""

    # Region/account. AWS_REGION resolves through get_region()'s us-east-1 fallback and is
    # therefore never part of the Config.validate() raising set.
    AWS_REGION: str = os.environ.get("AWS_REGION") or get_region()
    AWS_ACCOUNT_ID: str | None = os.environ.get("AWS_ACCOUNT_ID") or None

    # Model / inference.
    MODEL_ID: str = os.environ.get("MODEL_ID") or DEFAULT_MODEL_ID
    MAX_TOKENS: int = int(os.environ.get("MAX_TOKENS", "4096"))

    # Agent behaviour.
    DEFAULT_MODE: str = os.environ.get("DEFAULT_MODE", "step-by-step")
    DEDUP_SIMILARITY_THRESHOLD: float = float(
        os.environ.get("DEDUP_SIMILARITY_THRESHOLD", "0.85")
    )
    PII_REDACTION_STRATEGY: str = os.environ.get("PII_REDACTION_STRATEGY", "mask")
    MIN_RESPONSE_TOKENS: int = int(os.environ.get("MIN_RESPONSE_TOKENS", "10"))
    MINHASH_NUM_PERM: int = int(os.environ.get("MINHASH_NUM_PERM", "128"))

    # Persistence targets (required only when not offline — see validate()).
    OUTPUT_BUCKET: str = os.environ.get("OUTPUT_BUCKET", "")
    AUDIT_TABLE: str = os.environ.get("AUDIT_TABLE", "")

    # Path-safety allowlists (comma-separated; empty = allow any).
    ALLOWED_S3_BUCKETS: str = os.environ.get("ALLOWED_S3_BUCKETS", "")
    ALLOWED_S3_PREFIXES: str = os.environ.get("ALLOWED_S3_PREFIXES", "")
    ALLOWED_LOCAL_BASE_DIR: str = os.environ.get("ALLOWED_LOCAL_BASE_DIR", "")

    # Logging.
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    # Verification/demo mocking seam (not a deployed tuning knob). Default off.
    OFFLINE: bool = _as_bool(os.environ.get("DART_OFFLINE", "0"))

    @classmethod
    def validate(cls) -> None:
        """Fail-fast validation of required env vars.

        Raises ``ValueError`` listing missing ``OUTPUT_BUCKET`` / ``AUDIT_TABLE`` ONLY when
        not offline (persistence is active). ``AWS_REGION`` is never in the raising set
        because it resolves via ``get_region()``'s fallback.
        """
        if cls.OFFLINE:
            return

        missing = [
            name
            for name, value in (
                ("OUTPUT_BUCKET", cls.OUTPUT_BUCKET),
                ("AUDIT_TABLE", cls.AUDIT_TABLE),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + ". Set them on the task, or set DART_OFFLINE=1 for a local credential-free run."
            )

    @classmethod
    def refresh(cls) -> None:
        """Re-read environment into the class attributes.

        Useful for tests and for the offline CLI, which may set env vars after import.
        """
        cls.AWS_REGION = os.environ.get("AWS_REGION") or get_region()
        cls.AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID") or None
        cls.MODEL_ID = os.environ.get("MODEL_ID") or DEFAULT_MODEL_ID
        cls.MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
        cls.DEFAULT_MODE = os.environ.get("DEFAULT_MODE", "step-by-step")
        cls.DEDUP_SIMILARITY_THRESHOLD = float(
            os.environ.get("DEDUP_SIMILARITY_THRESHOLD", "0.85")
        )
        cls.PII_REDACTION_STRATEGY = os.environ.get("PII_REDACTION_STRATEGY", "mask")
        cls.MIN_RESPONSE_TOKENS = int(os.environ.get("MIN_RESPONSE_TOKENS", "10"))
        cls.MINHASH_NUM_PERM = int(os.environ.get("MINHASH_NUM_PERM", "128"))
        cls.OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
        cls.AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "")
        cls.ALLOWED_S3_BUCKETS = os.environ.get("ALLOWED_S3_BUCKETS", "")
        cls.ALLOWED_S3_PREFIXES = os.environ.get("ALLOWED_S3_PREFIXES", "")
        cls.ALLOWED_LOCAL_BASE_DIR = os.environ.get("ALLOWED_LOCAL_BASE_DIR", "")
        cls.LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
        cls.OFFLINE = _as_bool(os.environ.get("DART_OFFLINE", "0"))

    @classmethod
    def get_secret(cls, secret_name: str) -> str | None:
        """Secrets Manager helper, retained from the original for completeness.

        Unused by the demo path. Returns the ``SecretString`` for ``secret_name`` or
        ``None`` on any error.
        """
        try:
            import boto3  # local import — keeps module import boto3-free for offline use
            from botocore.exceptions import ClientError

            client = boto3.client("secretsmanager", region_name=cls.AWS_REGION)
            try:
                response = client.get_secret_value(SecretId=secret_name)
            except ClientError:
                return None
            return response.get("SecretString")
        except Exception:  # pragma: no cover - defensive, helper is unused in the demo
            return None


# ---------------------------------------------------------------------------
# Static reference tables (design.md section 2.6 / FR-6.3)
# ---------------------------------------------------------------------------

# Comprehend PII entity types the agent recognises.
PII_ENTITY_TYPES = [
    "EMAIL",
    "PHONE",
    "NAME",
    "ADDRESS",
    "SSN",
    "CREDIT_DEBIT_NUMBER",
    "DATE_TIME",
    "IP_ADDRESS",
    "URL",
]

# Known fine-tuning schema patterns: pattern name -> required field set.
KNOWN_SCHEMAS = {
    "instruction_response": ["instruction", "response"],
    "prompt_completion": ["prompt", "completion"],
    "chat_messages": ["messages"],
    "input_output": ["input", "output"],
    "question_answer": ["question", "answer"],
}

# Per-model context-window limits (tokens), keyed on the BARE base model id. The default
# is a conservative floor; a smaller-than-real limit can only make viability checks more
# cautious, never silently pass an over-budget record.
MODEL_CONTEXT_LIMITS = {
    "anthropic.claude-sonnet-4-5-20250929-v1:0": 200000,
    "anthropic.claude-sonnet-4-20250514-v1:0": 200000,
    "anthropic.claude-haiku-4-5": 200000,
    "amazon.nova-pro-v1:0": 300000,
    "meta.llama3-70b-instruct-v1:0": 8192,
    "meta.llama3-8b-instruct-v1:0": 8192,
    "default": 8192,
}

# Minimum recommended record counts by model size class (FR-6.3).
MODEL_MIN_RECORDS = {
    "7b": 500,
    "8b": 500,
    "13b": 1000,
    "34b": 2000,
    "70b": 3000,
    "default": 500,
}
