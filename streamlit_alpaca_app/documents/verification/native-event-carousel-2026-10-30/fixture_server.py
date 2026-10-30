"""Loopback display fixture for the native carousel review; no broker or account credentials."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import json

HOME = (Path(__file__).parent / "home-fixture.json").read_bytes()
ETAG = '"' + hashlib.sha256(HOME).hexdigest() + '"'

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/home":
            if self.headers.get("If-None-Match") == ETAG:
                self.send_response(304)
                self.send_header("ETag", ETAG)
                self.end_headers()
                return
            payload = HOME
        elif path == "/api/auth/session":
            payload = json.dumps({"authenticated": False, "context": None}).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if path == "/api/home":
            self.send_header("ETag", ETAG)
        self.end_headers()
        self.wfile.write(payload)

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 3191), Handler).serve_forever()
