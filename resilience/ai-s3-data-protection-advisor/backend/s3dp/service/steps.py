"""The pipeline steps as plain functions: no durable imports, store and session passed in. Each returns a small
JSON-serializable dict (it becomes a durable checkpoint, so well under the 256 KB checkpoint limit) and writes bulk data to the store.
`run` is the dict returned by `accounts`: {"run_id", "started_at", "scope", "account_id" (the hub), "accounts": [ids,
hub first]}. Account names and the organization listing outcome live in the store (scope.json), not the checkpoint.

Blast radius is one account: a member account whose spoke role cannot be assumed, or whose overview step fails, is
reported as not scanned and every other account is scanned as usual."""
import os

from s3dp import run as seq
from s3dp.ai import bedrock
from s3dp.collect import org, scan as collector, session as sessions
from s3dp.permissions import SPOKE_ROLE_NAME

SCOPES = ("account", "organization")


def multi_account():
    """Deployed with --multi-account: the organization scope is allowed."""
    return os.environ.get("MULTI_ACCOUNT", "").lower() == "true"


def _listed_accounts():
    return [a for a in os.environ.get("SCAN_ACCOUNTS", "").split(",") if a.strip()]


def _key(run, name):
    return f"runs/{run['run_id']}/{name}"


def _acct_key(run, account_id, name):
    return f"runs/{run['run_id']}/accounts/{account_id}/{name}"


def _session(session, run, account_id):
    return sessions.for_account(session, account_id, account_id == run["account_id"], SPOKE_ROLE_NAME)


def _scope_label(scope):
    n = len(scope["accounts"])
    if scope["source"] == "hub":
        return f"1 account ({scope['accounts'][0]['id']})"
    return f"{n} accounts ({'organization' if scope['source'] == 'organization' else 'account list'})"


def accounts(store, session, run_id, started_at, scope="account"):
    hub = session.client("sts").get_caller_identity()["Account"]
    if scope == "organization":
        sc = org.scope_accounts(session, hub, _listed_accounts())
    else:
        sc = {"accounts": [{"id": hub, "name": None, "hub": True}], "source": "hub", "unavailable": []}
    run = {"run_id": run_id, "started_at": started_at, "scope": scope, "account_id": hub,
           "accounts": [a["id"] for a in sc["accounts"]]}
    store.put_json(_key(run, "scope.json"), sc)
    # Until the overviews are read, account ids name the accounts; outcomes are final once assemble has run.
    store.update_run(run_id, status="RUNNING", scope=_scope_label(sc),
                     accounts=[{"id": a["id"], "name": a["name"] or a["id"], "outcome": "scanning"}
                               for a in sc["accounts"]])
    return run


def regions(store, session, run, account_id):
    """Overview of one account. Bounded result for any estate size: {"account", "units", "buckets"}, plus "denied"
    (error code) when the spoke role could not be assumed. The unit plan itself lives in overview.json."""
    s, denied = _session(session, run, account_id)
    if denied:
        return {"account": account_id, "units": 0, "buckets": 0, "denied": denied}
    names = {a["id"]: a["name"] for a in store.get_json(_key(run, "scope.json"))["accounts"]}
    ov = collector.account_overview(s, account_id, names.get(account_id))
    store.put_json(_acct_key(run, account_id, "overview.json"), ov)
    return {"account": account_id, "units": len(collector.plan_units(ov)), "buckets": len(ov["buckets"])}


def collect(store, session, run, account_id, n):
    s, denied = _session(session, run, account_id)
    if denied:      # the role answered for the overview minutes ago: fail this unit, assemble reports it
        raise PermissionError(f"AssumeRole {denied}")
    # plan_units on the stored overview, never a recomputed one: unit numbers stay stable on replay
    units = collector.plan_units(store.get_json(_acct_key(run, account_id, "overview.json")))
    res = collector.collect_unit(s, account_id, units[n])
    store.put_json(_acct_key(run, account_id, f"units/{n}.json"), res)
    return {"n": n, "items": len(res["items"])}


def _not_scanned_reason(item):
    if item is None:
        return "collection failed"
    return (f"AssumeRole {item['denied']}: the {SPOKE_ROLE_NAME} role is missing in this account or does not "
            f"trust this hub")


def assemble(store, run, per_account):
    """`per_account` is aligned with run["accounts"]: the `regions` result plus "failed" (unit numbers that did not
    collect), or None when the overview step itself failed."""
    sc = store.get_json(_key(run, "scope.json"))
    names = {a["id"]: a["name"] for a in sc["accounts"]}
    today = run["started_at"][:10]
    raws = []
    for account_id, item in zip(run["accounts"], per_account):
        if item is None or item.get("denied"):
            raws.append(collector.not_scanned(account_id, names.get(account_id), _not_scanned_reason(item), today))
            continue
        ov = store.get_json(_acct_key(run, account_id, "overview.json"))
        failed = set(item.get("failed") or ())
        results = [None if n in failed else store.get_json(_acct_key(run, account_id, f"units/{n}.json"))
                   for n in range(item["units"])]
        raws.append(collector.assemble(ov, results))
    raw = collector.merge(raws, run["account_id"], sc["source"], sc["unavailable"])
    store.put_json(_key(run, "raw.json"), raw)
    store.update_run(run["run_id"], scope=raw["run"]["scope"], accounts=_outcomes(raw))
    return {"buckets": len(raw["buckets"]), "accounts": len(raw["accounts"]), "api_errors": raw["run"]["api_errors"]}


def _outcomes(raw):
    return [{"id": a, "name": m.get("name") or a, "outcome": m.get("outcome", "scanned")}
            for a, m in raw["accounts"].items()]


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
    ai = data["meta"].get("ai") or {"classification": False, "narrative": "off"}
    ai = {"classification": bool(ai.get("classification")),
          "narrative": (data.get("meta_ai") or {}).get("narrative") or ai.get("narrative", "off")}
    store.update_run(run["run_id"], status="SUCCEEDED", finished_at=finished_at, buckets=len(raw["buckets"]),
                     regions=len(raw["run"]["regions_scanned"]), recs=recs, ai=ai,
                     scope=raw["run"]["scope"], accounts=_outcomes(raw))
    return {"report": True, "recs": recs}


def fail(store, run_id, error_code, finished_at):
    """`error_code` is an error type or code chosen by the handler. Never pass str(exception): messages can carry
    account data, and this value is shown in the dashboard."""
    store.update_run(run_id, status="FAILED", error=str(error_code)[:200], finished_at=finished_at)
