"""The classify / score / narrative / render sequence, shared by the CLI and the pipeline so they cannot drift.

The Amazon Bedrock client is not serializable, so the result of the probe travels between steps as `ai_state`:
{"classification": bool, "narrative": "off" | "unavailable", "reason": str (unavailable only)}."""
import json
import tempfile
from pathlib import Path

from s3dp.ai import bedrock, classify, narrative
from s3dp.derive import inventory
from s3dp.engine import instructions, scoring
from s3dp.report import excel, html


def classify_roles(raw, bedrock_client, model):
    """(ai_roles, ai_state). No client: rules only. A failed probe is reported, never treated as "no roles"."""
    if bedrock_client is None:
        return {}, {"classification": False, "narrative": "off"}
    err = bedrock.probe(bedrock_client, model)
    if err:
        return {}, {"classification": False, "narrative": "unavailable", "reason": f"{model}: {err}"}
    return classify.classify(inventory.undecided(raw), bedrock_client, model), \
        {"classification": True, "narrative": "pending"}


def score(raw, ai_roles, ai_state):
    """(inventory, data, stats)."""
    inv = inventory.build(raw, ai_roles)
    classified = bool(ai_state["classification"])
    inv["run"]["ai"] = {"classification": classified, "narrative": "pending" if classified else "off"}
    data, stats = scoring.build(inv)
    return inv, data, stats


def narrate(inv, data, bedrock_client, model, ai_state=None):
    """Narrative text (when the probe passed), then the AI notice and the instructions tab, which describe what the
    generative AI assistant actually did. `ai_state` is only needed to report an unavailable assistant."""
    if bedrock_client is not None and inv["run"]["ai"]["classification"]:
        narrative.apply(data, bedrock_client, model)
        inv["run"]["ai"]["narrative"] = data["meta_ai"]["narrative"]
        data["meta"]["ai"] = dict(inv["run"]["ai"])
        data["tabs"][0] = instructions.build_tab(inv, data)
    elif inv["run"]["ai"]["classification"]:
        # roles were classified but no client reached this step: say so rather than leave "pending"
        inv["run"]["ai"]["narrative"] = "unavailable"
        data["meta_ai"] = {"narrative": "unavailable", "reason": "no model configured"}
        data["meta"]["ai"] = dict(inv["run"]["ai"])
        data["tabs"][0] = instructions.build_tab(inv, data)
    elif ai_state and ai_state.get("narrative") == "unavailable":
        data["meta_ai"] = {"narrative": "unavailable", "reason": ai_state["reason"]}
    return data


def render(data):
    """(data.json bytes, report.xlsx bytes, report.html bytes)."""
    js = json.dumps(data, indent=1, ensure_ascii=False, default=str).encode()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", dir="/tmp") as f:    # write_xlsx takes a path
        excel.write_xlsx(data, f.name)
        xlsx = Path(f.name).read_bytes()
    return js, xlsx, html.render(data).encode()

