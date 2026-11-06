"""Partner webhook receiver (mock fixture, python3.9).

Accepts a JSON webhook from a partner, checks the content type and the
partner's client version, runs an optional enrichment plugin and returns the
accepted event.

Written the way a python3.9 function typically was, ON PURPOSE: every pattern
below works on Python 3.9 and breaks on Python 3.13. AWS Transform is expected
to find and fix each one (see docs/mock-fixture.md in the demo):
  - distutils (LooseVersion): removed in Python 3.12 (PEP 632)
  - imp (load_source): removed in Python 3.12
  - configparser.SafeConfigParser: removed in Python 3.12
  - cgi (parse_header): removed in Python 3.13 (PEP 594)
"""

import cgi
import configparser
import imp
import json
import os
from distutils.version import LooseVersion

_HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_HERE, "settings.ini")


def load_settings(path=SETTINGS_FILE):
    """Read the bundled webhook settings."""
    parser = configparser.SafeConfigParser()
    parser.read(path)
    return {
        "min_client_version": parser.get("webhook", "min_client_version"),
        "plugin": parser.get("webhook", "plugin", fallback=""),
    }


def load_plugin(filename):
    """Load the optional enrichment plugin shipped next to the handler."""
    if not filename:
        return None
    return imp.load_source("webhook_plugin", os.path.join(_HERE, filename))


def parse_content_type(header):
    """Split a Content-Type header into the media type and its charset."""
    media_type, params = cgi.parse_header(header or "")
    return media_type, params.get("charset", "utf-8")


def client_supported(version, minimum):
    """True when the partner's client version is at least `minimum`."""
    return LooseVersion(version) >= LooseVersion(minimum)


def _response(status_code, body):
    return {"statusCode": status_code, "body": json.dumps(body)}


def handler(event, context):
    settings = load_settings()
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    media_type, charset = parse_content_type(headers.get("content-type"))
    if media_type != "application/json":
        return _response(415, {"error": "expected application/json"})

    version = headers.get("x-client-version", "0")
    if not client_supported(version, settings["min_client_version"]):
        return _response(426, {"error": f"client {version} is older than {settings['min_client_version']}"})

    body = event.get("body") or "{}"
    payload = json.loads(body.decode(charset) if isinstance(body, bytes) else body)

    plugin = load_plugin(settings["plugin"])
    if plugin is not None:
        payload = plugin.enrich(payload)

    return _response(200, {"accepted": payload})
