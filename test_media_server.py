#!/usr/bin/env python3
"""
Standalone test suite for the media server logic.
Tests both the _media_wsgi WSGI function directly and end-to-end via HTTP.
Run this on the dev machine before every APK build.
"""

import eventlet
import eventlet.wsgi  # submodule must be imported explicitly
eventlet.monkey_patch()  # must be first, same as production

import os
import re
import sys
import mimetypes
import tempfile
import urllib.request
import urllib.error

# ── helpers ────────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0


def ok(label):
    global PASS
    PASS += 1
    print(f"  \033[32m✓\033[0m {label}")


def fail(label, detail=""):
    global FAIL
    FAIL += 1
    print(f"  \033[31m✗\033[0m {label}" + (f"  [{detail}]" if detail else ""))


def check(label, condition, detail=""):
    if condition:
        ok(label)
    else:
        fail(label, detail)


# ── MediaMiddleware — exact copy from telegram_message_ticker.py ───────────
# This is THE class under test. If it's wrong here it's wrong in production.

class MediaMiddleware:
    """WSGI middleware: intercepts /media/<file> requests and serves files
    directly with Range support, completely bypassing Flask's routing and
    error handlers.  No second port, no redirect."""

    def __init__(self, flask_app, get_media_folder):
        self.flask_app = flask_app
        self.get_media_folder = get_media_folder  # callable → current folder

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith("/media/"):
            return self._serve(path[7:], environ, start_response)
        return self.flask_app(environ, start_response)

    def _serve(self, raw_filename, environ, start_response):
        filename = os.path.basename(raw_filename)   # prevents path traversal
        if not filename:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Not Found"]

        media_path = os.path.join(self.get_media_folder(), filename)

        if not os.path.isfile(media_path):
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Not Found"]

        try:
            file_size = os.path.getsize(media_path)
            if file_size == 0:
                start_response("204 No Content", [])
                return [b""]

            mimetype, _ = mimetypes.guess_type(media_path)
            if not mimetype:
                mimetype = "application/octet-stream"

            range_header = environ.get("HTTP_RANGE", "")
            byte1, byte2, status = 0, file_size - 1, "200 OK"

            if range_header:
                m = re.search(r"bytes=(\d+)-(\d*)", range_header)
                if m:
                    byte1 = int(m.group(1))
                    byte2 = int(m.group(2)) if m.group(2) else file_size - 1
                byte2 = min(byte2, file_size - 1)
                status = "206 Partial Content"

            length = byte2 - byte1 + 1
            with open(media_path, "rb") as f:
                f.seek(byte1)
                data = f.read(length)

            start_response(status, [
                ("Content-Type", mimetype),
                ("Content-Length", str(len(data))),
                ("Content-Range", f"bytes {byte1}-{byte2}/{file_size}"),
                ("Accept-Ranges", "bytes"),
            ])
            return [data]

        except Exception as exc:
            start_response("500 Internal Server Error",
                           [("Content-Type", "text/plain")])
            return [str(exc).encode()]


# ── WSGI unit tests (no HTTP, no sockets) ─────────────────────────────────

def run_wsgi_unit_tests(tmpdir, jpg_bytes, mp4_bytes):
    print("\n── Unit tests (direct WSGI calls on MediaMiddleware) ───────────")

    from flask import Flask
    dummy_flask = Flask(__name__)
    folder = tmpdir
    mw = MediaMiddleware(dummy_flask, lambda: folder)

    def call(path, range_header=""):
        status_holder, headers_holder = [], []

        def start_response(status, headers):
            status_holder.append(status)
            headers_holder.extend(headers)

        environ = {"PATH_INFO": path}
        if range_header:
            environ["HTTP_RANGE"] = range_header

        body = b"".join(mw(environ, start_response))
        hdrs = dict(headers_holder)
        return status_holder[0], hdrs, body

    # JPEG full fetch
    status, hdrs, body = call("/media/test.jpg")
    check("JPEG: 200 OK",              status == "200 OK",       status)
    check("JPEG: Content-Type",        hdrs.get("Content-Type") == "image/jpeg",
          hdrs.get("Content-Type"))
    check("JPEG: Accept-Ranges",       hdrs.get("Accept-Ranges") == "bytes")
    check("JPEG: full content",        body == jpg_bytes,
          f"{len(body)} vs {len(jpg_bytes)}")

    # MP4 full fetch
    status, hdrs, body = call("/media/test.mp4")
    check("MP4: 200 OK",               status == "200 OK",       status)
    check("MP4: Content-Type",         hdrs.get("Content-Type") == "video/mp4",
          hdrs.get("Content-Type"))
    check("MP4: full content",         body == mp4_bytes)

    # Range: bytes=0-  (whole file via range)
    status, hdrs, body = call("/media/test.mp4", "bytes=0-")
    check("Range 0-: 206",             status == "206 Partial Content", status)
    check("Range 0-: full content",    body == mp4_bytes)
    check("Range 0-: Content-Range",   "Content-Range" in hdrs)

    # Partial range
    status, hdrs, body = call("/media/test.mp4", "bytes=100-199")
    check("Range 100-199: 206",        status == "206 Partial Content", status)
    check("Range 100-199: len 100",    hdrs.get("Content-Length") == "100",
          hdrs.get("Content-Length"))
    sz = len(mp4_bytes)
    check("Range 100-199: Content-Range",
          hdrs.get("Content-Range") == f"bytes 100-199/{sz}",
          hdrs.get("Content-Range"))
    check("Range 100-199: slice ok",   body == mp4_bytes[100:200])

    # First 1 KB range
    status, hdrs, body = call("/media/test.mp4", "bytes=0-1023")
    check("Range 0-1023: len 1024",    hdrs.get("Content-Length") == "1024",
          hdrs.get("Content-Length"))
    check("Range 0-1023: slice ok",    body == mp4_bytes[:1024])

    # 404 cases
    status, _, _ = call("/media/nonexistent.jpg")
    check("404 missing file",          status.startswith("404"), status)
    status, _, _ = call("/media/")
    check("404 empty filename",        status.startswith("404"), status)

    # 204 for zero-byte file
    open(os.path.join(tmpdir, "empty.mp4"), "wb").close()
    status, _, _ = call("/media/empty.mp4")
    check("204 zero-byte file",        status == "204 No Content", status)

    # Path traversal blocked
    status, _, _ = call("/media/../etc/passwd")
    check("404 path traversal blocked", status.startswith("404"), status)

    # Non-media request passes through to Flask (need full WSGI environ)
    full_environ = {
        "PATH_INFO": "/api/ping",
        "REQUEST_METHOD": "GET",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
        "wsgi.input": __import__("io").BytesIO(b""),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    status_holder2, headers_holder2 = [], []
    def sr2(s, h): status_holder2.append(s); headers_holder2.extend(h)
    mw(full_environ, sr2)
    check("Non-media routed to Flask (not 500)",
          not status_holder2[0].startswith("500"), status_holder2[0])


# ── HTTP integration: MediaMiddleware wrapping a real Flask app ────────────

def run_http_middleware_tests(port, tmpdir, jpg_bytes, mp4_bytes):
    print("\n── Integration tests (MediaMiddleware + Flask, single port) ────")

    from flask import Flask, jsonify
    flask_app = Flask(__name__)

    @flask_app.route("/api/ping")
    def ping():
        return jsonify({"ok": True})

    wrapped = MediaMiddleware(flask_app, lambda: tmpdir)
    listener = eventlet.listen(("127.0.0.1", port))
    eventlet.spawn(eventlet.wsgi.server, listener, wrapped, log_output=False)
    eventlet.sleep(0.3)

    base = f"http://127.0.0.1:{port}"

    def get(path, range_header=None, expect_error=False):
        req = urllib.request.Request(f"{base}{path}")
        if range_header:
            req.add_header("Range", range_header)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            if expect_error:
                return e.code, {}, b""
            raise

    # Image through middleware (simulates <img src="/media/test.jpg">)
    status, hdrs, body = get("/media/test.jpg")
    check("HTTP JPEG: 200",            status == 200,   status)
    check("HTTP JPEG: image/jpeg",     hdrs.get("Content-Type") == "image/jpeg",
          hdrs.get("Content-Type"))
    check("HTTP JPEG: Accept-Ranges",  hdrs.get("Accept-Ranges") == "bytes")
    check("HTTP JPEG: data correct",   body == jpg_bytes,
          f"{len(body)} vs {len(jpg_bytes)}")
    check("HTTP JPEG: no redirect",    body == jpg_bytes)   # not 302

    # Video through middleware (simulates <video><source src="/media/test.mp4">)
    status, hdrs, body = get("/media/test.mp4")
    check("HTTP MP4: 200",             status == 200,   status)
    check("HTTP MP4: video/mp4",       hdrs.get("Content-Type") == "video/mp4",
          hdrs.get("Content-Type"))
    check("HTTP MP4: data correct",    body == mp4_bytes)

    # Range request (Android Chrome sends this for video chunks)
    status, hdrs, body = get("/media/test.mp4", range_header="bytes=0-")
    check("HTTP Range 0-: 206",        status == 206,   status)
    check("HTTP Range 0-: full data",  body == mp4_bytes)

    status, hdrs, body = get("/media/test.mp4", range_header="bytes=0-511")
    check("HTTP Range 0-511: 206",     status == 206,   status)
    check("HTTP Range 0-511: 512 B",   len(body) == 512, len(body))
    check("HTTP Range 0-511: slice",   body == mp4_bytes[:512])

    sz = len(mp4_bytes)
    mid = sz // 2
    status, hdrs, body = get("/media/test.mp4",
                             range_header=f"bytes={mid}-{mid+255}")
    check("HTTP seek range: 206",      status == 206,   status)
    check("HTTP seek range: 256 B",    len(body) == 256, len(body))
    check("HTTP seek range: slice",    body == mp4_bytes[mid:mid+256])

    # Flask route still works for non-media paths
    status, hdrs, body = get("/api/ping")
    check("Flask /api/ping still works", status == 200, status)
    check("Flask /api/ping: JSON",       b'"ok"' in body, body[:40])

    # 404 for missing file
    code, _, _ = get("/media/nope.jpg", expect_error=True)
    check("HTTP 404 missing file",     code == 404, code)

    # MIME types (Python built-in, no system files — relevant for Android)
    print("\n  MIME type checks (Python built-in, works without /etc/mime.types):")
    for ext, expected in [(".jpg", "image/jpeg"), (".mp4", "video/mp4"),
                          (".png", "image/png"), (".gif", "image/gif"),
                          (".webp", "image/webp")]:
        got, _ = mimetypes.guess_type(f"file{ext}")
        check(f"  mimetypes{ext} → {expected}", got == expected, f"got {got!r}")


# ── main ───────────────────────────────────────────────────────────────────

def main():
    tmpdir = tempfile.mkdtemp(prefix="media_test_")

    jpg_bytes = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e "
        b"\xff\xd9"
    )
    mp4_bytes = (b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41"
                 + b"\x00" * (64 * 1024))

    with open(os.path.join(tmpdir, "test.jpg"), "wb") as f:
        f.write(jpg_bytes)
    with open(os.path.join(tmpdir, "test.mp4"), "wb") as f:
        f.write(mp4_bytes)

    run_wsgi_unit_tests(tmpdir, jpg_bytes, mp4_bytes)
    run_http_middleware_tests(18770, tmpdir, jpg_bytes, mp4_bytes)

    total = PASS + FAIL
    print(f"\n{'─'*55}")
    if FAIL == 0:
        print(f"\033[32m  ALL {total} TESTS PASSED\033[0m")
    else:
        print(f"\033[31m  {FAIL}/{total} TESTS FAILED\033[0m")
    print()
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
