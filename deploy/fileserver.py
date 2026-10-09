"""Static file server for ~/vision-zero-work/public with HTTP Range support (so <video> can seek).

Serves only files whose real path stays inside public/ or the work dir it links into.
"""
import mimetypes
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.realpath(os.path.expanduser("~/vision-zero-work/public"))
ALLOWED = os.path.realpath(os.path.expanduser("~/vision-zero-work"))
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("audio/mpeg", ".mp3")


class Handler(BaseHTTPRequestHandler):
    server_version = "VZFiles/0.1"

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        rel = urllib.parse.unquote(urllib.parse.urlparse(self.path).path).lstrip("/")
        path = os.path.join(ROOT, rel)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        real = os.path.realpath(path)
        if not (os.path.abspath(path).startswith(ROOT + os.sep) and real.startswith(ALLOWED + os.sep)) \
                or not os.path.isfile(real):
            self.send_error(404)
            return
        size = os.path.getsize(real)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            if a:
                start, end = int(a), int(b) if b else size - 1
            elif b:
                start = max(0, size - int(b))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(real)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        if head:
            return
        with open(real, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


if __name__ == "__main__":
    host, port = (sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"), int(sys.argv[2] if len(sys.argv) > 2 else 8088)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
