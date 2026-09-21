#!/usr/bin/env python3
"""
Signal live API

Serves the last few seconds of activity from the Redis window as JSON,
for the dashboard to poll. Read-only: it never writes to Redis.

    python3 signal_live_api.py              serve on port 8787
    curl localhost:8787/api/live            what the dashboard receives
"""

import json
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import redis

STREAM = "signal:live"
PORT = 8787
r = redis.Redis(host="localhost", port=6379)


def window():
    now = time.time() * 1000
    events = []
    for eid, f in r.xrange(STREAM):
        f = {k.decode(): v.decode() for k, v in f.items()}
        f["age"] = round((now - int(eid.decode().split("-")[0])) / 1000, 1)
        events.append(f)

    fired = lambda k: sum(1 for e in events if e.get(k))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events": list(reversed(events))[:200],
        "summary": {
            "count": len(events),
            "sessions": len({e.get("session") for e in events if e.get("session")}),
            "by_tool": dict(Counter(e.get("tool", "unknown") for e in events)),
            "by_type": dict(Counter(e.get("type", "unknown") for e in events)),
            "failures": sum(1 for e in events
                            if e.get("success") == "0" or "failed" in e.get("type", "")),
            "sensitive": sum(1 for e in events if int(e.get("tier", "1") or 1) >= 3),
            "blocked": fired("rule"),
            "tokens": sum(int(e.get("tokens", "0") or 0) for e in events),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "http://localhost:5173")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") == "/api/live":
            try:
                self._send(200, window())
            except redis.exceptions.ConnectionError:
                self._send(503, {"error": "redis is not reachable"})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass    # quiet: the dashboard polls every two seconds


if __name__ == "__main__":
    r.ping()
    print(f"serving the live window on http://localhost:{PORT}/api/live")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
