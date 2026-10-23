"""Unit tests for transform_runtimes.py (Phase 2 constants, design §Runtime->Transform).

Pure reference-data module: family resolution by prefix, default targets (the
only target the migration uses) and the python-only contextPrefix.
"""
import pytest

import transform_runtimes
from transform_runtimes import transform_for_runtime, TRANSFORMS


@pytest.mark.parametrize("runtime,family", [
    ("python3.8", "python"),
    ("python3.9", "python"),
    ("python3.10", "python"),
    ("nodejs16.x", "nodejs"),
    ("nodejs18.x", "nodejs"),
    ("dotnet6", "dotnet"),
    ("dotnetcore3.1", "dotnet"),
    ("PYTHON3.12", "python"),   # case-insensitive
])
def test_family_resolution(runtime, family):
    entry = transform_for_runtime(runtime)
    assert entry is TRANSFORMS[family]


@pytest.mark.parametrize("runtime", ["go1.x", "ruby2.7", "java8", "", "provided.al2", None, 3])
def test_unsupported_runtimes_return_none(runtime):
    assert transform_for_runtime(runtime) is None


def test_transform_names():
    assert TRANSFORMS["python"]["transformName"] == "AWS/python-version-upgrade"
    assert TRANSFORMS["nodejs"]["transformName"] == "AWS/lambda-nodejs-runtime-upgrade"
    assert TRANSFORMS["dotnet"]["transformName"] == "AWS/dotnet-lambda-upgrade"


def test_default_targets():
    """Each default is the runtime its managed transformation is built for, and
    belongs to its own family (it is the only target the migration uses)."""
    assert TRANSFORMS["python"]["defaultTarget"] == "python3.13"
    assert TRANSFORMS["nodejs"]["defaultTarget"] == "nodejs24.x"
    assert TRANSFORMS["dotnet"]["defaultTarget"] == "dotnet10"
    for family, entry in TRANSFORMS.items():
        assert transform_runtimes._family(entry["defaultTarget"]) == family


def test_context_prefix_only_for_python():
    assert transform_for_runtime("python3.9")["contextPrefix"] == transform_runtimes.PYTHON_CONTEXT_PREFIX
    assert transform_for_runtime("python3.9")["contextPrefix"]
    assert transform_for_runtime("nodejs18.x")["contextPrefix"] is None
    assert transform_for_runtime("dotnet6")["contextPrefix"] is None


def test_python_context_prefix_is_short_and_lambda_specific():
    """Only what a generic language upgrade cannot infer: handler convention,
    the runtime line in the IaC template, no handler rename. Under ~600 chars."""
    prefix = transform_runtimes.PYTHON_CONTEXT_PREFIX
    assert len(prefix) < 600
    assert "handler" in prefix
    assert "(event, context)" in prefix
    assert "SAM" in prefix and "CDK" in prefix and "Serverless" in prefix
    assert "Do not rename the handler" in prefix


def test_build_command_empty_for_all_families():
    assert TRANSFORMS["python"]["buildCommand"] == ""
    assert TRANSFORMS["nodejs"]["buildCommand"] == ""
    assert TRANSFORMS["dotnet"]["buildCommand"] == ""
