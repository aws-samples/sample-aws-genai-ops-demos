#!/usr/bin/env python3
"""A deliberately awkward target used to prove the generated JMX measures reality.

It reproduces the failure modes that a status-code-only assertion misses:

  POST /login          -> 200, token in body. Wrong password still returns 200.
  GET  /orders         -> 200 with {"resultCode":"0000"} when the token is valid,
                          200 with {"resultCode":"9401"} when it is not.
  POST /orders         -> 201 on success.
  GET  /flaky          -> 200 with an error envelope every 3rd call.
  GET  /empty          -> 204, no body.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VALID_TOKEN = "tok-abc123"

_counter_lock = threading.Lock()
_counter = {"flaky": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, payload: dict | None) -> None:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Trace-Id", "trace-9f8e7d")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _token_ok(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {VALID_TOKEN}"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._send(400, {"resultCode": "9000", "message": "bad json"})
            return

        if self.path == "/login":
            if payload.get("password") == "correct":
                self._send(200, {"resultCode": "0000", "accessToken": VALID_TOKEN})
            else:
                # The trap: authentication failure with HTTP 200 and a null token.
                self._send(200, {"resultCode": "9100", "accessToken": None})
            return

        if self.path == "/orders":
            if not self._token_ok():
                self._send(200, {"resultCode": "9401", "message": "unauthorized"})
                return
            self._send(201, {"resultCode": "0000", "orderId": "ord-555"})
            return

        self._send(404, {"resultCode": "9404"})

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path == "/orders":
            if not self._token_ok():
                self._send(200, {"resultCode": "9401", "message": "unauthorized"})
                return
            self._send(200, {"resultCode": "0000", "orders": [{"id": "ord-555"}]})
            return

        if path == "/flaky":
            with _counter_lock:
                _counter["flaky"] += 1
                n = _counter["flaky"]
            if n % 3 == 0:
                self._send(200, {"resultCode": "9500", "message": "downstream timeout"})
            else:
                self._send(200, {"resultCode": "0000", "value": n})
            return

        if path == "/empty":
            self._send(204, None)
            return

        self._send(404, {"resultCode": "9404"})

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18110
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock target on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
