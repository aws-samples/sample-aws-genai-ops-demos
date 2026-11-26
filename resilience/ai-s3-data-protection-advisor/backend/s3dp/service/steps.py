"""The pipeline steps as plain functions: no durable imports, store and session passed in. Each returns a small
JSON-serializable dict (it becomes a durable checkpoint, so well under the 256 KB checkpoint limit) and writes bulk data to the store.
`run` is the dict returned by `accounts`: {"run_id", "started_at", "account_id", "account_name"}."""
from s3dp import run as seq
from s3dp.ai import bedrock
from s3dp.collect import scan as collector


def _key(run, name):
    return f"runs/{run['run_id']}/{name}"


def accounts(store, session, run_id, started_at):
    account_id = session.client("sts").get_caller_identity()["Account"]
    # The IAM alias is only known once the overview is read (step 2); until then the id is the name.
    store.update_run(run_id, status="RUNNING", scope=f"1 account ({account_id})",
                     accounts=[{"id": account_id, "name": account_id, "outcome": "scanning"}])
    return {"run_id": run_id, "started_at": started_at, "account_id": account_id, "account_name": account_id}


def regions(store, session, run):
    """Bounded result for any estate size: {"unit_count", "buckets", "regions"}. The handler maps `collect` over
    range(unit_count); the unit plan itself lives in overview.json."""
    ov = collector.account_overview(session, run["account_id"])
    store.put_json(_key(run, "overview.json"), ov)
    units = collector.plan_units(ov)
    name = ov["alias"] or run["account_id"]
    store.update_run(run["run_id"], scope=f"1 account ({name})",
                     accounts=[{"id": run["account_id"], "name": name, "outcome": "scanning"}])
    return {"unit_count": len(units), "buckets": len(ov["buckets"]), "regions": ov["regions"]}


def collect(store, session, run, n):
    # plan_units on the stored overview, never a recomputed one: unit numbers stay stable on replay
    units = collector.plan_units(store.get_json(_key(run, "overview.json")))
    res = collector.collect_unit(session, run["account_id"], units[n])
    store.put_json(_key(run, f"units/{n}.json"), res)
    return {"n": n, "items": len(res["items"])}


def assemble(store, run, unit_count, failed):
    ov = store.get_json(_key(run, "overview.json"))
    failed = set(failed)
    results = [None if n in failed else store.get_json(_key(run, f"units/{n}.json")) for n in range(unit_count)]
    raw = collector.assemble(ov, results)
    store.put_json(_key(run, "raw.json"), raw)
    return {"buckets": len(raw["buckets"]), "api_errors": raw["run"]["api_errors"]}


def _client(session, model):
    return bedrock.client(session) if model else None


def classify(store, session, run, model):
    raw = store.get_json(_key(run, "raw.json"))
    ai_roles, state = seq.classify_roles(raw, _client(session, model), model)
    store.put_json(_key(run, "ai-roles.json"), {"roles": ai_roles, "state": state})
    return dict(state, classified=len(ai_roles))


def score(store, run):
    raw = store.get_json(_key(run, "raw.json"))
    saved = store.get_json(_key(run, "ai-roles.json"))
    roles = {k: tuple(v) for k, v in saved["roles"].items()}    # JSON turned the tuples into lists
    inv, data, stats = seq.score(raw, roles, saved["state"])
    store.put_json(_key(run, "inventory.json"), inv)
    store.put_json(_key(run, "data-rules.json"), data)    # pre-narrative copy: narrative always restarts from it
    store.put_json(_key(run, "data.json"), data)
    return {"buckets": stats["buckets"], "recs": dict(stats["recs"]), "governance": stats["governance"]}


FINAL = ("complete", "partial", "unavailable")


def _narrative_status(inv, data):
    # meta_ai is the truth when present: it says "unavailable" when the probe failed, where inv still says "off"
    return (data.get("meta_ai") or {}).get("narrative") or inv["run"]["ai"]["narrative"]


def narrative(store, session, run, model):
    """Idempotent: always narrates from the rules-only data written by `score`, so a retry after a partial failure
    never stacks AI text on AI text. A stored data.json that already has a final narrative status is kept."""
    stored = store.get_json(_key(run, "data.json"))
    status = (stored.get("meta_ai") or {}).get("narrative")
    if status in FINAL:
        return {"narrative": status}
    inv = store.get_json(_key(run, "inventory.json"))
    data = store.get_json(_key(run, "data-rules.json"))
    state = store.get_json(_key(run, "ai-roles.json"))["state"]
    seq.narrate(inv, data, _client(session, model), model, state)
    store.put_json(_key(run, "data.json"), data)
    return {"narrative": _narrative_status(inv, data)}


def publish(store, run, finished_at):
    data = store.get_json(_key(run, "data.json"))
    raw = store.get_json(_key(run, "raw.json"))
    js, xlsx, page = seq.render(data)
    store.put_bytes(_key(run, "data.json"), js, "application/json")
    store.put_bytes(_key(run, "report.xlsx"), xlsx,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    store.put_bytes(_key(run, "report.html"), page, "text/html; charset=utf-8")
    recs = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for tab in data["tabs"]:
        if tab.get("id") == "recommendations":
            for item in tab["items"]:
                if item["severity"] in recs:
                    recs[item["severity"]] += 1
    meta = raw["accounts"].get(run["account_id"], {})
    ai = data["meta"].get("ai") or {"classification": False, "narrative": "off"}
    ai = {"classification": bool(ai.get("classification")),
          "narrative": (data.get("meta_ai") or {}).get("narrative") or ai.get("narrative", "off")}
    store.update_run(run["run_id"], status="SUCCEEDED", finished_at=finished_at, buckets=len(raw["buckets"]),
                     regions=len(raw["run"]["regions_scanned"]), recs=recs, ai=ai,
                     scope=raw["run"]["scope"],
                     accounts=[{"id": run["account_id"], "name": meta.get("name", run["account_name"]),
                                "outcome": meta.get("outcome", "scanned")}])
    return {"report": True, "recs": recs}


def fail(store, run_id, error_code, finished_at):
    """`error_code` is an error type or code chosen by the handler. Never pass str(exception): messages can carry
    account data, and this value is shown in the dashboard."""
    store.update_run(run_id, status="FAILED", error=str(error_code)[:200], finished_at=finished_at)
