"""A loopback-only explorer. Intentionally not a production web server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import json
import sqlite3

from . import store
from .ranking import PROFILES

STATIC = Path(__file__).with_name("static")


def handler_for(db_path):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content, content_type="application/json"):
            body = json.dumps(content).encode() if content_type == "application/json" else content
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {"error": "Loopback host required"})
            parsed = urlsplit(self.path)
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if parsed.path in static:
                filename, mime = static[parsed.path]
                return self.send(200, (STATIC / filename).read_bytes(), mime)
            try:
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                with store.connect(db_path) as db:
                    if parsed.path == "/api/domains":
                        result = store.listing(db, query=params.get("q", ""),
                            budget=float(params["budget"]) if params.get("budget") else None,
                            days=int(params.get("days", 30)), review=params.get("review", ""),
                            limit=int(params.get("limit", 50)), offset=int(params.get("offset", 0)),
                            profile=params.get("profile", "general"))
                    elif parsed.path == "/api/stats":
                        result = store.stats(db)
                    elif parsed.path == "/api/profiles":
                        result = PROFILES
                    else:
                        return self.send(404, {"error": "Not found"})
                self.send(200, result)
            except (ValueError, OverflowError) as exc:
                self.send(400, {"error": str(exc)})
            except sqlite3.Error:
                self.send(503, {"error": "Index temporarily unavailable"})

        def do_POST(self):
            if not self.valid_host():
                return self.send(403, {"error": "Loopback host required"})
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self.send(403, {"error": "Same-origin requests only"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "Expected application/json"})
            if self.path != "/api/review":
                return self.send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid body size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or not isinstance(body.get("domain"), str) or "review" not in body:
                    raise ValueError("Expected domain and review")
                with store.connect(db_path) as db:
                    store.review_domain(db, body["domain"], body["review"])
                self.send(200, {"ok": True})
            except (ValueError, TypeError) as exc:
                self.send(400, {"error": str(exc)})
            except sqlite3.Error:
                self.send(503, {"error": "Index temporarily unavailable"})

    return Handler


def serve(db_path, port=8000):
    with store.connect(db_path):
        pass
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(db_path))
    print(f"Gooddomains → http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
