"""Shared pytest fixtures and environment setup.

Sets dummy persistence/region env vars so ``Config.validate()`` passes in non-offline
tests, exposes the synthetic fixture path, and provides mocked boto3 clients for
Comprehend / Bedrock-runtime / S3 / DynamoDB. No real AWS clients are ever constructed, so
the suite runs with no AWS credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# Set required/region env BEFORE config is imported anywhere so Config picks them up.
os.environ.setdefault("OUTPUT_BUCKET", "dart-test-bucket")
os.environ.setdefault("AUDIT_TABLE", "dart-test-table")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_train.jsonl"


@pytest.fixture
def fixture_path() -> str:
    """Absolute path to the synthetic training fixture."""
    return str(FIXTURE_PATH)


@pytest.fixture(autouse=True)
def _refresh_config() -> None:
    """Refresh Config from env before each test (env is set above)."""
    from config import Config

    Config.refresh()


class FakeComprehendClient:
    """Comprehend stub. Returns an EMAIL entity for any document containing '@'."""

    def __init__(self, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error
        self.calls: list[str] = []

    def detect_pii_entities(self, Text: str, LanguageCode: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append(Text)
        if self._raise_error is not None:
            raise self._raise_error
        at = Text.find("@")
        if at == -1:
            return {"Entities": []}
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


class _FakeStreamingBody:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data


class FakeBedrockClient:
    """Bedrock-runtime stub returning a canned narrative, or raising on demand."""

    def __init__(self, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

    def invoke_model(self, modelId: str, body: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append({"modelId": modelId, "body": body})
        if self._raise_error is not None:
            raise self._raise_error
        payload = {"content": [{"text": "Canned Bedrock narrative for tests."}]}
        return {"body": _FakeStreamingBody(payload)}


class FakeS3Client:
    """S3 stub recording put_object calls; optionally raises."""

    def __init__(self, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        if self._raise_error is not None:
            raise self._raise_error
        self.puts.append(kwargs)
        return {}


class FakeDynamoDBClient:
    """DynamoDB stub recording put_item calls; optionally raises."""

    def __init__(self, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error
        self.items: list[dict[str, Any]] = []

    def put_item(self, **kwargs: Any) -> dict[str, Any]:
        if self._raise_error is not None:
            raise self._raise_error
        self.items.append(kwargs)
        return {}


@pytest.fixture
def fake_comprehend() -> FakeComprehendClient:
    return FakeComprehendClient()


@pytest.fixture
def fake_bedrock() -> FakeBedrockClient:
    return FakeBedrockClient()
