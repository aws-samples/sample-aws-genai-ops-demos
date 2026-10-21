"""Tests for the hybrid async /conversation flow (202 + poll).

Fast deterministic short-circuits must still return 200 inline. Only the slow
model fall-through becomes asynchronous: the dispatcher writes a job row, fires
the worker, and returns 202 + job_id; the worker runs run_model_path and
records the result; the status endpoint returns it. These tests pin that
contract without touching real AWS (jobs' table/lambda clients are patched).
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
# Must be set before importing jobs so module-level reads see them.
os.environ.setdefault("JOBS_TABLE", "test-jobs-table")
os.environ.setdefault("WORKER_FUNCTION_NAME", "test-worker-fn")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent  # noqa: E402
import jobs  # noqa: E402
import agent_worker  # noqa: E402


def _post(message, history=None):
    return {
        "httpMethod": "POST",
        "body": json.dumps(
            {"message": message, "history": history or [], "mode": "guided"}
        ),
    }


class DispatcherTest(unittest.TestCase):
    def test_short_circuit_still_returns_200_inline(self):
        """A fast deterministic prompt must NOT go async — no job created,
        200 inline, no worker dispatch."""
        with patch.object(agent, "invoke_tool", return_value={"action_plan": [], "summary": {"total_findings": 0}}), \
                patch.object(jobs, "create_job") as create, \
                patch.object(jobs, "dispatch_worker") as dispatch, \
                patch.object(agent, "converse_with_tools") as converse:
            resp = agent.handler(_post("generate an action plan"), None)

        self.assertEqual(resp["statusCode"], 200)
        create.assert_not_called()
        dispatch.assert_not_called()
        converse.assert_not_called()

    def test_model_path_returns_202_and_dispatches_worker(self):
        """A prompt that misses every short-circuit must go async: create a
        job, fire the worker, return 202 + job_id. The model must NOT run
        synchronously in the request."""
        with patch.object(jobs, "create_job", return_value="job-123") as create, \
                patch.object(jobs, "dispatch_worker") as dispatch, \
                patch.object(agent, "converse_with_tools") as converse:
            resp = agent.handler(_post("ponder the philosophy of least privilege in depth"), None)

        self.assertEqual(resp["statusCode"], 202)
        body = json.loads(resp["body"])
        self.assertEqual(body["job_id"], "job-123")
        self.assertEqual(body["status"], "RUNNING")
        create.assert_called_once()
        dispatch.assert_called_once_with("job-123")
        # The dispatcher must not run the model itself.
        converse.assert_not_called()

    def test_inline_fallback_when_async_unconfigured(self):
        """If JOBS_TABLE/WORKER_FUNCTION_NAME are absent (legacy/local), the
        dispatcher must fall back to running the model inline and return 200,
        never 500."""
        sample_resp = {"output": {"message": {"content": [{"text": "hi"}]}}, "usage": {}}
        with patch.object(jobs, "JOBS_TABLE", None), \
                patch.object(agent, "converse_with_tools", return_value=(sample_resp, [], None, [])) as converse:
            resp = agent.handler(_post("ponder something novel and unmatched"), None)

        self.assertEqual(resp["statusCode"], 200)
        converse.assert_called_once()
        body = json.loads(resp["body"])
        self.assertEqual(body["response"], "hi")


class StatusEndpointTest(unittest.TestCase):
    def _status_event(self, job_id):
        return {"httpMethod": "GET", "pathParameters": {"job_id": job_id}}

    def test_running(self):
        with patch.object(jobs, "get_job", return_value={"job_id": "j", "status": "RUNNING"}):
            resp = agent.handler(self._status_event("j"), None)
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(json.loads(resp["body"]), {"status": "RUNNING"})

    def test_done_returns_result_envelope(self):
        result = {"response": "answer", "tools_used": [], "coverage": []}
        row = {"job_id": "j", "status": "DONE", "result": json.dumps(result)}
        with patch.object(jobs, "get_job", return_value=row):
            resp = agent.handler(self._status_event("j"), None)
        body = json.loads(resp["body"])
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(body["status"], "DONE")
        self.assertEqual(body["result"], result)

    def test_error(self):
        row = {"job_id": "j", "status": "ERROR", "error": "boom"}
        with patch.object(jobs, "get_job", return_value=row):
            resp = agent.handler(self._status_event("j"), None)
        body = json.loads(resp["body"])
        self.assertEqual(body, {"status": "ERROR", "error": "boom"})

    def test_unknown_job_404(self):
        with patch.object(jobs, "get_job", return_value=None):
            resp = agent.handler(self._status_event("missing"), None)
        self.assertEqual(resp["statusCode"], 404)
        self.assertEqual(json.loads(resp["body"])["status"], "ERROR")

    def test_get_without_job_id_is_conversations_list(self):
        resp = agent.handler({"httpMethod": "GET"}, None)
        self.assertEqual(resp["statusCode"], 200)
        self.assertIn("conversations", json.loads(resp["body"]))


class WorkerTest(unittest.TestCase):
    def test_worker_runs_model_and_completes_job(self):
        request_payload = {"message": "hi", "history": [], "mode": "guided"}
        job_row = {"job_id": "j1", "status": "RUNNING", "request": json.dumps(request_payload)}
        rendered = {"response": "done", "tools_used": [], "coverage": []}
        with patch.object(jobs, "get_job", return_value=job_row), \
                patch.object(agent, "run_model_path", return_value=rendered) as rmp, \
                patch.object(jobs, "complete_job") as complete, \
                patch.object(jobs, "fail_job") as fail:
            out = agent_worker.handler({"job_id": "j1"}, None)

        rmp.assert_called_once_with(request_payload)
        complete.assert_called_once_with("j1", rendered)
        fail.assert_not_called()
        self.assertTrue(out["ok"])

    def test_worker_records_failure(self):
        job_row = {"job_id": "j2", "status": "RUNNING", "request": json.dumps({"message": "x"})}
        with patch.object(jobs, "get_job", return_value=job_row), \
                patch.object(agent, "run_model_path", side_effect=RuntimeError("kaboom")), \
                patch.object(jobs, "complete_job") as complete, \
                patch.object(jobs, "fail_job") as fail:
            out = agent_worker.handler({"job_id": "j2"}, None)

        fail.assert_called_once()
        complete.assert_not_called()
        self.assertFalse(out["ok"])

    def test_worker_no_job_id(self):
        out = agent_worker.handler({}, None)
        self.assertFalse(out["ok"])

    def test_worker_missing_job(self):
        with patch.object(jobs, "get_job", return_value=None):
            out = agent_worker.handler({"job_id": "gone"}, None)
        self.assertFalse(out["ok"])


class JobsHelpersTest(unittest.TestCase):
    def test_status_response_shapes(self):
        self.assertEqual(
            jobs.status_response({"status": "RUNNING"}), {"status": "RUNNING"}
        )
        done = jobs.status_response(
            {"status": "DONE", "result": json.dumps({"response": "r"})}
        )
        self.assertEqual(done["status"], "DONE")
        self.assertEqual(done["result"], {"response": "r"})
        err = jobs.status_response({"status": "ERROR", "error": "e"})
        self.assertEqual(err, {"status": "ERROR", "error": "e"})

    def test_load_request_roundtrip(self):
        payload = {"message": "hi", "mode": "quick"}
        self.assertEqual(jobs.load_request({"request": json.dumps(payload)}), payload)


if __name__ == "__main__":
    unittest.main()
