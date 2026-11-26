from s3dp.ai import classify


class FakeBedrock:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def converse(self, **kw):
        self.calls.append(kw)
        return {"output": {"message": {"content": [{"toolUse": {"name": "record_roles",
                                                                 "input": {"buckets": self.answers.pop(0)}}}]}}}


def test_batches_of_50_and_validates_roles():
    items = [{"name": f"b{i}", "account_name": "a", "region": "eu-west-1", "tags": {}, "size_tb": 1.0,
              "storage_class": "STANDARD"} for i in range(60)]
    first = [{"name": f"b{i}", "role": "production", "reason": "r", "confidence": "high"} for i in range(50)]
    second = [{"name": "b50", "role": "spaceship", "reason": "r", "confidence": "high"},
              {"name": "b51", "role": "non_production", "reason": "looks like a test", "confidence": "low"},
              {"name": "not-asked", "role": "production", "reason": "r", "confidence": "high"}]
    fake = FakeBedrock([first, second])
    out = classify.classify(items, client=fake, model_id="m")
    assert len(fake.calls) == 2
    assert out["b0"] == ("production", "r", "high")
    assert out["b51"] == ("non_production", "looks like a test", "low")
    assert "b50" not in out and "not-asked" not in out


def test_bedrock_failure_returns_nothing():
    class Broken:
        def converse(self, **kw):
            raise RuntimeError("throttled")
    assert classify.classify([{"name": "x", "account_name": "a", "region": "r", "tags": {}, "size_tb": None,
                               "storage_class": "UNKNOWN"}], client=Broken(), model_id="m") == {}


def test_replica_is_not_an_ai_role():
    assert "replica" not in classify.ALLOWED
    assert "replica" not in classify.SCHEMA["properties"]["buckets"]["items"]["properties"]["role"]["enum"]
    fake = FakeBedrock([[{"name": "x", "role": "replica", "reason": "r", "confidence": "high"}]])
    assert classify.classify([{"name": "x"}], client=fake, model_id="m") == {}


def test_failure_logs_the_type_not_the_message(caplog):
    class Broken:
        def converse(self, **kw):
            raise RuntimeError("secret-bucket-name")
    item = {"name": "x", "account_name": "a", "region": "r", "tags": {}, "size_tb": None, "storage_class": "UNKNOWN"}
    classify.classify([item], Broken(), "m")
    assert "RuntimeError" in caplog.text and "secret-bucket-name" not in caplog.text
