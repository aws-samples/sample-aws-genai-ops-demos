"""
Tests that the MCP server is installed from this repository.

The install is pinned to a commit of this repository with `--from`, so the code that runs
is the code in this repo. A bare `uvx aws-chaos-engineering` or `pip install
aws-chaos-engineering` must not come back in the config, the docs or the install links.
"""
import base64
import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

DEMO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "aws-chaos-engineering"
SOURCE_RE = re.compile(
    r"^git\+https://github\.com/aws-samples/sample-aws-genai-ops-demos"
    r"@[0-9a-f]{40}#subdirectory=resilience/ai-chaos-engineering-with-fis$"
)
# A command that installs or runs the package by its bare name.
BARE_NAME_RE = re.compile(r"(?:uvx|pip3? install|-m pip install)\s+aws-chaos-engineering\b")
DOCS = ["README.md", "power/POWER.md", "ARCHITECTURE.md"]


def _assert_pinned(args):
    assert args[:1] == ["--from"], f"args must start with --from, got {args}"
    assert SOURCE_RE.match(args[1]), f"--from must be a commit-pinned in-repo git URL, got {args[1]}"
    assert args[2:] == [PACKAGE], f"after the source only the package name is expected, got {args[2:]}"


def test_power_mcp_json_launches_from_pinned_repo_source():
    config = json.loads((DEMO_ROOT / "power" / "mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"][PACKAGE]
    assert server["command"] == "uvx"
    _assert_pinned(server["args"])


@pytest.mark.parametrize("doc", DOCS)
def test_docs_never_use_the_bare_package_name(doc):
    text = (DEMO_ROOT / doc).read_text(encoding="utf-8")
    offenders = [line.strip() for line in text.splitlines() if BARE_NAME_RE.search(line)]
    assert offenders == [], f"{doc} installs by bare name: {offenders}"


def test_readme_install_links_use_the_pinned_source():
    """The Kiro, Cursor and VS Code deep links carry their own copy of the config."""
    text = (DEMO_ROOT / "README.md").read_text(encoding="utf-8")
    values = re.findall(r"[?&]config=([^)\s&]+)", text)
    assert len(values) == 3, f"expected the Kiro, Cursor and VS Code links, found {len(values)}"
    for value in values:
        if value.startswith("%7B"):
            raw = unquote(value)
        else:
            raw = base64.b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
        _assert_pinned(json.loads(raw)["args"])


def test_pyproject_is_marked_do_not_upload():
    """Sample code is not published to PyPI; this classifier makes PyPI refuse an upload."""
    tomllib = pytest.importorskip("tomllib")
    data = tomllib.loads((DEMO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "Private :: Do not Upload" in data["project"]["classifiers"]
