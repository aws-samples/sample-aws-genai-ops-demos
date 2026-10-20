"""Minimal test for the mock python Lambda handler.

Exists so the python mock fixture mirrors the AWS "Automate Lambda runtime
upgrades with AWS Transform custom" blog, whose sample repo carries tests and
passes validationCommands=pytest. With this test present, the first migration
rehearsal exercises the tested happy path (atx runs the transform, then runs
pytest to validate the upgrade) rather than the undocumented no-tests case.
The nodejs and dotnet fixtures deliberately carry no tests, to exercise the
no-tests path once its behavior is settled empirically.
"""

from lambda_function import handler


def test_handler_returns_200():
    result = handler({}, None)
    assert result["statusCode"] == 200
    assert "hello" in result["body"].lower()
