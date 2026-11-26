import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from s3dp import cli

ROOT = Path(__file__).resolve().parents[2]
RAW = json.loads((Path(__file__).parent / "fixtures" / "raw_small.json").read_text())


def test_sample_command_writes_report_to_out(tmp_path):
    out = tmp_path / "sample.json"
    cli.main(["sample", "--out", str(out)])
    data = json.loads(out.read_text())
    assert data["meta"]["sample"] is True
    assert [t["id"] for t in data["tabs"]][0] == "instructions"
    assert data == json.loads((ROOT / "sample-data" / "anycompany-data.json").read_text())


def test_nova_lite_profile_per_region(monkeypatch):
    monkeypatch.delenv("MODEL_ID", raising=False)
    m = "amazon.nova-lite-v1:0"
    assert cli.model_for("us-west-2") == f"us.{m}"
    assert cli.model_for("ca-central-1") == f"ca.{m}"
    assert cli.model_for("eu-west-1") == f"eu.{m}"
    assert cli.model_for("il-central-1") == f"eu.{m}"
    assert cli.model_for("ap-southeast-2") == f"apac.{m}"
    for region in ("us-gov-west-1", "cn-north-1", "sa-east-1", "me-central-1", "af-south-1", "mx-central-1"):
        assert cli.model_for(region) is None, region
    monkeypatch.setenv("MODEL_ID", "custom-profile")
    assert cli.model_for("sa-east-1") == "custom-profile"


def _no_region_env(monkeypatch, tmp_path):
    for k in ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "MODEL_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))


def test_scan_requires_a_region(monkeypatch, tmp_path):
    _no_region_env(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="region"):
        cli.main(["scan", "--out", str(tmp_path / "out")])


def test_region_from_the_session_and_no_model_exits_before_the_scan(monkeypatch, tmp_path):
    _no_region_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "sa-east-1")
    monkeypatch.setattr(cli, "collect_account", lambda *a, **k: pytest.fail("scan started"))
    with pytest.raises(SystemExit) as e:
        cli.main(["scan", "--out", str(tmp_path / "out")])
    assert str(e.value) == "No Amazon Nova Lite inference profile known for sa-east-1: set MODEL_ID or use --no-ai"


class FakeBedrock:
    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise ClientError({"Error": {"Code": self.fail, "Message": "no"}}, "Converse")
        if "toolConfig" in kw:
            name = kw["toolConfig"]["tools"][0]["toolSpec"]["name"]
            key = "buckets" if name == "record_roles" else "texts"
            return {"output": {"message": {"content": [{"toolUse": {"name": name, "input": {key: []}}}]}}}
        return {"output": {"message": {"content": [{"text": "ok"}]}}}


class FakeSession:
    def __init__(self, bedrock, region="eu-west-1"):
        self.region_name, self.bedrock, self.clients = region, bedrock, []

    def client(self, name, **kw):
        self.clients.append((name, kw))
        if name == "sts":
            class Sts:
                def get_caller_identity(self):
                    return {"Account": "111122223333"}
            return Sts()
        assert name == "bedrock-runtime" and "region_name" not in kw
        return self.bedrock


def _scan(monkeypatch, tmp_path, bedrock):
    monkeypatch.delenv("MODEL_ID", raising=False)
    session = FakeSession(bedrock)
    monkeypatch.setattr(cli.boto3, "Session", lambda **kw: session)
    monkeypatch.setattr(cli, "collect_account", lambda *a, **k: json.loads(json.dumps(RAW)))
    out = tmp_path / "out"
    cli.main(["scan", "--out", str(out)])
    return session, json.loads((out / "data.json").read_text()), (out / "report.html").read_text()


def test_failed_bedrock_probe_continues_rules_only(monkeypatch, tmp_path, capsys):
    bedrock = FakeBedrock(fail="AccessDeniedException")
    session, data, page = _scan(monkeypatch, tmp_path, bedrock)
    line = next(x for x in capsys.readouterr().out.splitlines() if "Amazon Bedrock" in x)
    assert "eu.amazon.nova-lite-v1:0" in line and "AccessDeniedException" in line
    assert len(bedrock.calls) == 1                         # the probe only
    assert data["meta_ai"]["narrative"] == "unavailable" and "AccessDeniedException" in data["meta_ai"]["reason"]
    assert data["meta"]["ai"] == {"classification": False, "narrative": "off"}
    assert "Rules-based report: no generative AI was used." in page
    text = json.dumps(data["tabs"][0])
    assert "generative AI assistant" not in text


def test_working_bedrock_runs_the_ai_steps_from_the_session(monkeypatch, tmp_path):
    bedrock = FakeBedrock()
    session, data, page = _scan(monkeypatch, tmp_path, bedrock)
    assert ("bedrock-runtime", ) == session.clients[-1][:1]
    assert len(bedrock.calls) >= 2 and "toolConfig" not in bedrock.calls[0]      # probe first
    assert data["meta"]["ai"] == {"classification": True, "narrative": "complete"}
    assert "Text written by a generative AI assistant" in page
    assert "generative AI assistant" in json.dumps(data["tabs"][0])
