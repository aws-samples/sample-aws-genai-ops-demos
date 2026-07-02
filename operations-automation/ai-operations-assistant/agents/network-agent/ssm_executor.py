"""
Shared SSM Script Executor for G.O.A.T. Network Agent diagnostic tools.

Encapsulates the common pattern used by all SSM-based diagnostic actions:
SendCommand → poll GetCommandInvocation → parse JSON output after marker line.

This module provides:
- ``SSMExecutionError``: Exception with ``error_category`` and ``source_api``
- ``execute_ssm_script``: Execute a script on an EC2 instance via SSM Run Command
  and return the parsed JSON result dict.

Requirements covered: 3.1, 3.2, 3.7, 3.8, 3.9, 3.10, 3.11, 3.13, 3.14
"""

import json
import time

import boto3
from botocore.exceptions import ClientError

from aws_utils import get_region

# ---------------------------------------------------------------------------
# Lazy boto3 SSM client singleton
# ---------------------------------------------------------------------------

_ssm_client = None


def _get_ssm_client():
    """Return a cached boto3 SSM client bound to the detected region.

    The client is created lazily on first use. Tests can reset the
    singleton by setting ``ssm_executor._ssm_client = None`` between cases.
    """
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm", region_name=get_region())
    return _ssm_client


# ---------------------------------------------------------------------------
# SSMExecutionError
# ---------------------------------------------------------------------------


class SSMExecutionError(Exception):
    """Raised when SSM command fails, times out, or output is unparseable.

    Attributes:
        message: Human-readable error description.
        error_category: Classified error type for structured responses.
            One of: ``ssm_not_managed``, ``execution_failed``,
            ``output_parse_error``, ``ssm_api_error``.
        source_api: The AWS API call that triggered the error
            (e.g. ``ssm:SendCommand``, ``ssm:GetCommandInvocation``).
    """

    def __init__(self, message: str, error_category: str, source_api: str):
        super().__init__(message)
        self.message = message
        self.error_category = error_category
        self.source_api = source_api


# ---------------------------------------------------------------------------
# execute_ssm_script
# ---------------------------------------------------------------------------


def execute_ssm_script(
    instance_id: str,
    script_text: str,
    marker_line: str,
    timeout_seconds: int = 120,
    poll_interval: int = 2,
    max_polls: int = 65,
) -> dict:
    """Execute a script on an EC2 instance via SSM Run Command.

    Sends the script as inline text using ``AWS-RunShellScript``, polls for
    completion, and parses the JSON output appearing after *marker_line* in
    the command's stdout.

    Args:
        instance_id: Target EC2 instance ID (e.g. ``i-0abc123def456``).
        script_text: Full shell script content to execute on the instance.
        marker_line: Deterministic marker string that precedes the JSON
            output in stdout. The parser looks for this exact line and
            extracts the JSON payload from the line(s) following it.
        timeout_seconds: SSM command timeout passed to ``TimeoutSeconds``.
            Defaults to 120 (Requirement 3.2).
        poll_interval: Seconds between ``GetCommandInvocation`` polls.
            Defaults to 2 (Requirement 3.11).
        max_polls: Maximum number of poll attempts before giving up.
            Defaults to 65 (Requirement 3.11).

    Returns:
        Parsed JSON dict from the script's stdout output.

    Raises:
        SSMExecutionError: On any failure — invalid instance, timeout,
            script failure, polling exhaustion, or unparseable output.
    """
    client = _get_ssm_client()

    # --- Send the command (Req 3.1, 3.2) ---
    try:
        send_response = client.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [script_text]},
            TimeoutSeconds=timeout_seconds,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        # Req 3.8: InvalidInstanceId → ssm_not_managed
        if error_code == "InvalidInstanceId":
            raise SSMExecutionError(
                message=(
                    f"Instance {instance_id} is not reachable via SSM. "
                    "Ensure the SSM agent is installed and the instance is running."
                ),
                error_category="ssm_not_managed",
                source_api="ssm:SendCommand",
            ) from exc
        # Req 3.13: Other SSM API errors → include first 200 chars of message
        error_msg = str(exc)[:200]
        raise SSMExecutionError(
            message=(
                f"SSM SendCommand API call failed for instance {instance_id}: "
                f"{error_code} - {error_msg}"
            ),
            error_category="ssm_api_error",
            source_api="ssm:SendCommand",
        ) from exc

    command_id = send_response["Command"]["CommandId"]

    # --- Poll for completion (Req 3.11) ---
    status = None
    for _ in range(max_polls):
        time.sleep(poll_interval)
        try:
            invocation = client.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            # InvocationDoesNotExist means command hasn't registered yet
            if error_code == "InvocationDoesNotExist":
                continue
            error_msg = str(exc)[:200]
            raise SSMExecutionError(
                message=(
                    f"SSM GetCommandInvocation API call failed: "
                    f"{error_code} - {error_msg}"
                ),
                error_category="ssm_api_error",
                source_api="ssm:GetCommandInvocation",
            ) from exc

        status = invocation.get("Status", "")

        # Terminal statuses
        if status == "Success":
            stdout_content = invocation.get("StandardOutputContent", "")
            return _parse_output(stdout_content, marker_line)

        # Req 3.9: TimedOut → execution_failed
        if status == "TimedOut":
            raise SSMExecutionError(
                message=(
                    f"SSM command timed out on instance {instance_id} "
                    f"after {timeout_seconds} seconds."
                ),
                error_category="execution_failed",
                source_api="ssm:GetCommandInvocation",
            )

        # Req 3.10: Failed → include first 500 chars of stderr
        if status == "Failed":
            stderr_content = invocation.get("StandardErrorContent", "")
            stderr_snippet = stderr_content[:500] if stderr_content else "(no stderr)"
            raise SSMExecutionError(
                message=(
                    f"SSM command failed on instance {instance_id}. "
                    f"Stderr: {stderr_snippet}"
                ),
                error_category="execution_failed",
                source_api="ssm:GetCommandInvocation",
            )

        # Cancelled or other terminal states
        if status in ("Cancelled", "Cancelling"):
            raise SSMExecutionError(
                message=(
                    f"SSM command was cancelled on instance {instance_id}."
                ),
                error_category="execution_failed",
                source_api="ssm:GetCommandInvocation",
            )

        # InProgress or Pending — continue polling

    # Req 3.11: Max polls exceeded → execution_failed
    raise SSMExecutionError(
        message=(
            f"SSM command status could not be determined after {max_polls} polls "
            f"for instance {instance_id}. Last status: {status}"
        ),
        error_category="execution_failed",
        source_api="ssm:GetCommandInvocation",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_output(stdout: str, marker_line: str) -> dict:
    """Parse JSON output appearing after the marker line in stdout.

    The script is expected to print a deterministic marker line followed
    by a single JSON object on subsequent lines. This function locates
    the marker and attempts to parse everything after it as JSON.

    Args:
        stdout: Full stdout content from the SSM command.
        marker_line: The exact marker string to search for.

    Returns:
        Parsed dict from the JSON payload.

    Raises:
        SSMExecutionError: If the marker is not found or JSON is invalid
            (Requirement 3.14).
    """
    lines = stdout.split("\n")
    marker_index = None

    for i, line in enumerate(lines):
        if line.strip() == marker_line.strip():
            marker_index = i
            break

    if marker_index is None:
        stdout_snippet = stdout[:500] if stdout else "(empty stdout)"
        raise SSMExecutionError(
            message=(
                f"Diagnostic output could not be parsed: marker line not found "
                f"in stdout. Raw output: {stdout_snippet}"
            ),
            error_category="output_parse_error",
            source_api="ssm:GetCommandInvocation",
        )

    # Extract everything after the marker line
    json_text = "\n".join(lines[marker_index + 1:]).strip()

    if not json_text:
        stdout_snippet = stdout[:500] if stdout else "(empty stdout)"
        raise SSMExecutionError(
            message=(
                f"Diagnostic output could not be parsed: no content after "
                f"marker line. Raw output: {stdout_snippet}"
            ),
            error_category="output_parse_error",
            source_api="ssm:GetCommandInvocation",
        )

    try:
        return json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        stdout_snippet = stdout[:500] if stdout else "(empty stdout)"
        raise SSMExecutionError(
            message=(
                f"Diagnostic output could not be parsed: invalid JSON after "
                f"marker line. Raw output: {stdout_snippet}"
            ),
            error_category="output_parse_error",
            source_api="ssm:GetCommandInvocation",
        )
