from s3dp.ai import narrative


def test_numbers_must_come_from_the_input():
    assert narrative.numbers_ok("3 of 5 buckets", "Three facts: 3 buckets out of 5")
    assert not narrative.numbers_ok("12 buckets", "only 3 buckets")
    assert narrative.numbers_ok("$1,250.50 per month", "costs $1250.50/month")
    assert narrative.numbers_ok("about 20 PB", "20 PB")


class Fake:
    def __init__(self, out):
        self.out = out

    def converse(self, **kw):
        return {"output": {"message": {"content": [{"toolUse": {"name": "record_texts", "input": self.out}}]}}}


def data():
    return {"tabs": [
        {"id": "governance", "summary": "Level 2 of 5, average 1.8.", "dimensions": [
            {"id": "ownership", "current": "Some owner tags.", "next": "Owner tags on most buckets."}]},
        {"id": "recommendations", "items": [
            {"priority": 1, "severity": "HIGH", "description": "20 PB unversioned.", "recommendation": "Enable versioning"},
            {"priority": 2, "severity": "LOW", "description": "No cleanup.", "recommendation": "Add rule"}]}]}


def test_rewrites_accepted_and_rejected():
    d = data()
    fake = Fake({"texts": [
        {"id": "governance.summary", "text": "Governance sits at level 2 of 5 (average 1.8)."},
        {"id": "governance.ownership.current", "text": "Only 40% of buckets have an owner."},
        {"id": "rec.1", "text": "This 20 PB bucket can be deleted with one call."},
        {"id": "rec.2", "text": "should not be asked"}]})
    stats = narrative.apply(d, client=fake, model_id="m")
    gov, recs = d["tabs"][0], d["tabs"][1]["items"]
    assert gov["summary"] == "Governance sits at level 2 of 5 (average 1.8)."
    assert gov["dimensions"][0]["current"] == "Some owner tags."          # 40% not in the input
    assert recs[0]["description"] == "This 20 PB bucket can be deleted with one call."
    assert recs[1]["description"] == "No cleanup."                        # LOW is not sent
    assert stats == {"accepted": 2, "rejected": 1}
    assert d["meta_ai"]["narrative"] == "partial"


def test_model_failure_keeps_rule_text():
    class Broken:
        def converse(self, **kw):
            raise RuntimeError("no access")
    d = data()
    narrative.apply(d, client=Broken(), model_id="m")
    assert d["tabs"][0]["summary"] == "Level 2 of 5, average 1.8."
    assert d["meta_ai"]["narrative"] == "unavailable"
