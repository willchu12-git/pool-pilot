"""
serve.py -- tiny local server, for when I'm at the PC.

  py engine/serve.py            then open http://127.0.0.1:8778

Serves the local build (out/index.html, data inlined) and gives it the one
endpoint the static cloud build can't have:

  POST /api/write  {path, b64}   file a reading / action / opening step exactly
                                 like the phone does, then ingest + rebuild
                                 immediately -- including running the vision pass
                                 on an uploaded screenshot.

Everything arrives base64-encoded, same as the GitHub Contents API takes it, so
the browser has exactly one write path whether it's talking to GitHub or to this.

Loopback only, no auth: it is only reachable from this machine.
"""
from __future__ import annotations
import base64
import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import check_inbox        # noqa: E402
import checklist          # noqa: E402
import poolcfg            # noqa: E402
import pwa                # noqa: E402
import state as state_mod  # noqa: E402

PORT = int(os.environ.get("PP_PORT", "8778"))
ALLOWED_DIRS = ("inbox",)


class Handler(BaseHTTPRequestHandler):
    server_version = "PoolPilot/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):        # quieter console
        print("  %s" % (fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            p = os.path.join(poolcfg.path_of("out"), "index.html")
            if not os.path.exists(p):
                pwa.main()
            return self._send(200, open(p, "rb").read(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, json.dumps(state_mod.build()))
        return self._send(404, json.dumps({"error": "not found"}))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()

        if path == "/api/write":
            rel = (body.get("path") or "").replace("\\", "/").lstrip("/")
            head = rel.split("/")[0]
            if head not in ALLOWED_DIRS or ".." in rel:
                return self._send(400, json.dumps({"error": "path not allowed"}))
            try:
                raw = base64.b64decode(body.get("b64") or "", validate=True)
            except (ValueError, TypeError):
                return self._send(400, json.dumps({"error": "bad base64"}))
            dest = os.path.join(ROOT, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").write(raw)
            print("  wrote %s (%d bytes)" % (rel, len(raw)))
            check_inbox.main()
            checklist.main()
            state_mod.main()
            pwa.main()
            return self._send(200, json.dumps({"ok": True}))

        return self._send(404, json.dumps({"error": "not found"}))


def main():
    state_mod.main()
    pwa.main()
    url = "http://127.0.0.1:%d" % PORT
    print("serving %s  (Ctrl+C to stop)" % url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
