"""Serve an httpx MockTransport-style handler on a real localhost port (for the stdio tests)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx


def serve(handler):
    class H(BaseHTTPRequestHandler):
        def _any(self):
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            req = httpx.Request(self.command, f"http://127.0.0.1{self.path}",
                                headers=dict(self.headers), content=body)
            resp = handler(req)
            data = resp.content
            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                    self.send_header(k, v)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _any

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
