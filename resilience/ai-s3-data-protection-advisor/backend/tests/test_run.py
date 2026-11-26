import json
from pathlib import Path

from s3dp import run

from .test_cli import RAW, FakeBedrock

# data_small_ai.json and data_small_unavailable.json are the data.json written by the f7a5190 CLI (before the sequence
# moved to s3dp.run). Never regenerate them from run.* or cli: they are the oracle.
FIX = Path(__file__).parent / "fixtures"
MODEL = "eu.amazon.nova-lite-v1:0"


def _sequence(client):
    raw = json.loads(json.dumps(RAW))
    roles, state = run.classify_roles(raw, client, MODEL)
    inv, data, stats = run.score(raw, roles, state)
    run.narrate(inv, data, client, MODEL, state)
    return inv, data, stats, state


def _same(data, oracle):
    # the oracle is the data.json the CLI wrote before the sequence moved to s3dp.run
    assert json.loads(json.dumps(data, ensure_ascii=False, default=str)) == json.loads(
        (FIX / oracle).read_text())


def test_sequence_with_a_working_client_matches_the_cli_output():
    inv, data, stats, state = _sequence(FakeBedrock())
    _same(data, "data_small_ai.json")
    assert state["classification"] is True and stats["buckets"] == 4
    assert inv["run"]["ai"]["narrative"] == "complete"


def test_probe_failure_gives_unavailable_state_and_rules_only_report():
    bedrock = FakeBedrock(fail="AccessDeniedException")
    roles, state = run.classify_roles(json.loads(json.dumps(RAW)), bedrock, MODEL)
    assert roles == {} and len(bedrock.calls) == 1
    assert state == {"classification": False, "narrative": "unavailable",
                     "reason": f"{MODEL}: AccessDeniedException"}
    _same(_sequence(FakeBedrock(fail="AccessDeniedException"))[1], "data_small_unavailable.json")


def test_no_client_is_rules_only_and_unavailable_marker_is_absent():
    roles, state = run.classify_roles(json.loads(json.dumps(RAW)), None, None)
    assert (roles, state) == ({}, {"classification": False, "narrative": "off"})
    inv, data, _ = run.score(json.loads(json.dumps(RAW)), roles, state)
    run.narrate(inv, data, None, None, state)
    assert data["meta"]["ai"] == {"classification": False, "narrative": "off"} and "meta_ai" not in data


def test_render_returns_json_xlsx_and_html():
    data = _sequence(None)[1]
    js, xlsx, page = run.render(data)
    assert json.loads(js)["meta"]["ai"]["narrative"] == "off"
    assert xlsx[:2] == b"PK" and b"<html" in page


def test_classified_but_no_client_is_unavailable_not_pending():
    raw = json.loads(json.dumps(RAW))
    state = {"classification": True, "narrative": "pending"}
    inv, data, _ = run.score(raw, {}, state)
    run.narrate(inv, data, None, None, state)
    assert data["meta_ai"] == {"narrative": "unavailable", "reason": "no model configured"}
    assert data["meta"]["ai"]["narrative"] == "unavailable"
