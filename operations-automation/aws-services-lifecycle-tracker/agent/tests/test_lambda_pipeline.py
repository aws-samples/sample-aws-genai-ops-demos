"""
Local tests for the durable refresh pipeline (issue #139).

Runs the real durable handler through DurableFunctionTestRunner with the
step BODIES mocked (Bedrock, boto3, DynamoDB), so what is under test is the
orchestration: mode routing, phase order, failure tolerance, reconciliation
scoping, and the summary/notification contract.
"""
import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Heavy modules are stubbed before import so the test needs no AWS access.
for mod in ("bedrock_agentcore", "requests", "bs4"):
    sys.modules.setdefault(mod, MagicMock())
os.environ.setdefault("AWS_REGION", "eu-central-1")

from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner
from aws_durable_execution_sdk_python.execution import InvocationStatus
from aws_durable_execution_sdk_python.lambda_service import OperationType

import lambda_pipeline as lp


def _run(event: dict):
    runner = DurableFunctionTestRunner(handler=lp.handler)
    with runner:
        return runner.run(input=json.dumps(event), timeout=60)


def _result(res) -> dict:
    return json.loads(res.result) if isinstance(res.result, str) else res.result


def _step_names(res) -> set:
    # get_all_operations() walks into map children (per-cell steps live one level down)
    return {op.name for op in res.get_all_operations() if op.operation_type == OperationType.STEP and op.name}


def _fake_extract(service_name, force_refresh, refresh_origin):
    if service_name == "broken":
        raise RuntimeError("Bedrock exploded")
    return {"success": service_name != "amplify", "total_items_extracted": 5,
            "extraction_duration": 1.5, "error": None if service_name != "amplify" else "no items"}


def _fake_scanner_ok(region, index):
    return [{"service_name": "lambda", "item_id": "inventory#nodejs14.x", "status": "deprecated"}]


def _fake_scanner_boom(region, index):
    raise RuntimeError("AccessDenied")


@pytest.fixture
def mocks():
    with patch.object(lp, "get_all_enabled_services", return_value=["lambda", "amplify", "broken"]), \
         patch.object(lp, "extract_service_lifecycle", side_effect=_fake_extract), \
         patch.object(lp.account_discovery, "LifecycleIndex", return_value=MagicMock()), \
         patch.object(lp.account_discovery, "save_to_dynamodb",
                      return_value={"success": True, "items_saved": 1, "stale_removed": 0}) as save, \
         patch.dict(lp.SCANNERS, {"Lambda": _fake_scanner_ok, "EKS": _fake_scanner_boom}, clear=True), \
         patch.dict(lp.account_discovery.SCANNER_SERVICE_KEYS,
                    {"Lambda": ["lambda"], "EKS": ["eks"]}, clear=True), \
         patch("boto3.client") as boto_client:
        yield {"save": save, "boto": boto_client}


class TestFullPipeline:
    def test_full_mode_runs_all_phases_and_tolerates_failures(self, mocks):
        res = _run({"mode": "full", "refresh_origin": "Auto"})
        assert res.status is InvocationStatus.SUCCEEDED
        out = _result(res)

        # Extraction: 3 services, one agent-reported failure, one raised
        assert out["extract"]["total"] == 3
        assert out["extract"]["succeeded"] == 1
        assert sorted(out["extract"]["failed"]) == ["amplify", "broken"]
        assert out["extract"]["items_extracted"] == 5

        # Scan: 2 cells (function region only), one raised
        assert out["scan"]["cells_total"] == 2
        assert out["scan"]["cells_succeeded"] == 1
        assert out["scan"]["failed_cells"] == ["EKS@eu-central-1"]
        assert out["scan"]["items_discovered"] == 1
        assert out["scan"]["needs_attention"] == 1

        # Steps exist by name (never by index)
        names = _step_names(res)
        assert {"start-run", "extract-lambda", "extract-amplify", "scan-Lambda-eu-central-1",
                "reconcile-inventory", "summarize-and-notify"} <= names

    def test_reconciliation_scoped_to_succeeded_scanner_cells(self, mocks):
        _run({"mode": "scan"})
        kwargs = mocks["save"].call_args.kwargs
        # EKS scanner failed -> its scope must NOT be reconciled
        assert kwargs["scanned_services"] == ["lambda"]
        assert kwargs["run_id"]
        items = mocks["save"].call_args.args[0]
        assert items[0]["item_id"] == "inventory#nodejs14.x"

    def test_run_id_is_stable_and_present_in_summary(self, mocks):
        out = _result(_run({"mode": "scan"}))
        assert out["run_id"] == mocks["save"].call_args.kwargs["run_id"]


class TestModes:
    def test_extract_mode_skips_scan(self, mocks):
        res = _run({"mode": "extract"})
        out = _result(res)
        assert out["extract"]["total"] == 3
        assert out["scan"]["cells_total"] == 0
        assert out["inventory"] == {}
        mocks["save"].assert_not_called()
        assert not any(n.startswith("scan-") for n in _step_names(res))

    def test_scan_mode_skips_extract(self, mocks):
        res = _run({"mode": "scan"})
        out = _result(res)
        assert out["extract"]["total"] == 0
        assert out["scan"]["cells_total"] == 2
        assert not any(n.startswith("extract-") for n in _step_names(res))

    def test_services_subset_limits_both_phases(self, mocks):
        res = _run({"mode": "full", "services": ["lambda"]})
        out = _result(res)
        assert out["extract"]["total"] == 1
        # Only the Lambda scanner emits 'lambda' rows -> EKS cell not built
        assert out["scan"]["cells_total"] == 1
        assert out["scan"]["failed_cells"] == []

    def test_regions_multiply_scan_cells(self, mocks):
        out = _result(_run({"mode": "scan", "regions": ["eu-central-1", "us-west-2"]}))
        assert out["scan"]["cells_total"] == 4
        assert out["regions"] == ["eu-central-1", "us-west-2"]

    def test_invalid_mode_fails_execution(self, mocks):
        res = _run({"mode": "yolo"})
        assert res.status is InvocationStatus.FAILED


class TestNotification:
    def test_publishes_to_sns_when_topic_configured(self, mocks):
        sns = MagicMock()
        mocks["boto"].return_value = sns
        with patch.dict(os.environ, {"NOTIFICATION_TOPIC_ARN": "arn:aws:sns:eu-central-1:123456789012:t"}):
            out = _result(_run({"mode": "extract"}))
        assert out["notified"] is True
        msg = sns.publish.call_args.kwargs["Message"]
        assert "Facts (web extraction): 1/3" in msg
        assert "amplify" in msg and "broken" in msg

    def test_no_topic_no_publish(self, mocks):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOTIFICATION_TOPIC_ARN", None)
            out = _result(_run({"mode": "extract"}))
        assert "notified" not in out
        mocks["boto"].return_value.publish.assert_not_called()


class TestPureHelpers:
    def test_normalize_defaults(self):
        assert lp.normalize_spec({}) == {"mode": "full", "services": None, "regions": None,
                                         "refresh_origin": "manual"}

    def test_normalize_rejects_bad_types(self):
        with pytest.raises(ValueError):
            lp.normalize_spec({"services": "all"})
        with pytest.raises(ValueError):
            lp.normalize_spec({"regions": "eu-central-1"})
