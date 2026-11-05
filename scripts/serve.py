#!/usr/bin/env python3
"""Serve the dashboard locally, without browser caching, plus the voice-assistant language model endpoint.

Usage (from the gulf-watch folder):
    python3 scripts/serve.py [port]
    ANTHROPIC_API_KEY=sk-... python3 scripts/serve.py      # turns on the language model for voice notes

Then open http://127.0.0.1:8765/

Endpoints
    GET  /api/status      {"available": bool, "model": str|null}
    POST /api/interpret   {"text": "...", "context": {...}} -> structured JSON (type, asset, alerts, place, kind, ...)

The API key stays on the server (environment variable). The browser never sees it. Without a key, /api/status
says available=false and the page uses its offline rule-based reader. The model only reads the note: the
browser checks every id it returns against the real data before anything is shown or saved.
"""
import functools
import http.server
import json
import os
import re
import sys
import urllib.error
import urllib.request

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

SYSTEM = """You read short spoken or typed radio-style notes from a maritime operations room in the Gulf of Guinea
and turn them into one JSON object. Reply with JSON only, no other text.

Fields (use null when the note does not say):
  "type":    "dispatch" (send a patrol asset to an alert) | "report" (a sighting or incident) |
             "resolve" (close an alert) | "refuel" (fuel added to an asset)
  "asset":   id of one asset from the context list, or null
  "alerts":  list of alert ids from the context list (for example ["D-041"]), or []
  "place":   {"name": str, "lat": number, "lon": number} for a report, or null
  "kind":    one of the allowed kinds in the context, for a report, or null
  "outcome": "false_alarm" | "confirmed" for resolve, or null
  "litres":  number for refuel, or null

Only use ids that appear in the context. If the note is ambiguous (for example "the interceptor" when there are
two), use null so the assistant can ask. Spoken numbers may be in words ("four hundred litres" = 400)."""


def call_model(text, context):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise PermissionError("no API key")
    body = json.dumps({
        "model": MODEL, "max_tokens": 400, "system": SYSTEM,
        "messages": [{"role": "user", "content": f"Context:\n{json.dumps(context)}\n\nNote:\n{text}"}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.load(resp)
    out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    match = re.search(r"\{.*\}", out, re.S)
    if not match:
        raise ValueError("model did not return JSON")
    return json.loads(match.group(0))


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/health":  # tells the page it runs without the GulfEye backend (static mode)
            return self._json(200, {"ok": True, "backend": False})
        if self.path.split("?")[0] == "/api/status":
            has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
            return self._json(200, {"available": has_key, "model": MODEL if has_key else None})
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/interpret":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = str(payload.get("text", ""))[:600]
            context = payload.get("context", {})
            return self._json(200, call_model(text, context))
        except PermissionError:
            return self._json(503, {"error": "language model not configured"})
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            return self._json(502, {"error": str(exc)})

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(Handler, directory=WEB)
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        mode = "language model ON" if os.environ.get("ANTHROPIC_API_KEY") else "language model off (rule-based reader)"
        print(f"Gulf Watch: http://127.0.0.1:{port}/   [{mode}]")
        httpd.serve_forever()
