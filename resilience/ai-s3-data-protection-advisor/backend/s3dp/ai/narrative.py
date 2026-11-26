"""Plain-language rewrite of selected report texts. Numbers are never invented: a rewrite whose numbers are not all
in its source text is rejected and the rule-written text stays."""
import json
import logging
import re

from s3dp.ai.bedrock import call_tool, error_code

log = logging.getLogger(__name__)
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
SYSTEM = (
    "You rewrite short texts from an Amazon S3 data protection report so a non-specialist understands them. Keep every "
    "fact, keep every number exactly as written, add no number, no product claim and no recommendation that is not in "
    "the source. Plain sentences, no marketing tone, no exclamation marks, no emoji, at most 3 sentences per text. "
    "Return one entry per id you were given.")
SCHEMA = {"type": "object", "required": ["texts"], "properties": {"texts": {"type": "array", "items": {
    "type": "object", "required": ["id", "text"], "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}}}}


def _nums(s):
    return {n.replace(",", "") for n in NUM.findall(s)}


def numbers_ok(new, source):
    return _nums(new) <= _nums(source)


def _targets(data):
    """id -> (container dict, key) for every text to rewrite."""
    tabs = {t["id"]: t for t in data["tabs"]}
    out = {}
    gov = tabs.get("governance")
    if gov:
        out["governance.summary"] = (gov, "summary")
        for d in gov.get("dimensions", []):
            for key in ("current", "next"):
                if d.get(key) and not ("level" in d and d["level"] is None):   # not assessed: keep the text
                    out[f"governance.{d['id']}.{key}"] = (d, key)
    for r in tabs.get("recommendations", {}).get("items", []):
        if r.get("severity") in ("HIGH", "MEDIUM") and r.get("description"):
            out[f"rec.{r['priority']}"] = (r, "description")
    return out


def apply(data, client, model_id, batch=40):
    targets = _targets(data)
    ids = list(targets)
    accepted = rejected = failed = 0
    for i in range(0, len(ids), batch):
        chunk = {k: targets[k][0][targets[k][1]] for k in ids[i:i + batch]}
        try:
            res = call_tool(client, model_id, SYSTEM, "Rewrite these texts:\n" + json.dumps(chunk), "record_texts",
                            SCHEMA, max_tokens=8000)
        except Exception as e:  # noqa: BLE001 - keep the rule-written text
            # code only: the message can echo prompt text, which holds bucket names
            log.warning("narrative failed for %d texts: %s", len(chunk), error_code(e))
            failed += len(chunk)
            continue
        for x in res.get("texts", []):
            k = x.get("id")
            if k not in chunk or not isinstance(x.get("text"), str) or not x["text"].strip():
                continue
            if numbers_ok(x["text"], chunk[k]):
                obj, key = targets[k]
                obj[key] = x["text"].strip()
                accepted += 1
            else:
                rejected += 1
    status = "unavailable" if failed == len(ids) and ids else "partial" if (rejected or failed) else "complete"
    data["meta_ai"] = {"narrative": status, "accepted": accepted, "rejected": rejected}
    return {"accepted": accepted, "rejected": rejected}
