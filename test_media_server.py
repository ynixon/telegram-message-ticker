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


# ── copy of _media_wsgi from telegram_message_ticker.py ────────────────────
# If this logic is wrong here, it is wrong in production too.

def make_media_wsgi(media_folder):
    def _media_wsgi(environ, start_response):
        path = environ.get("PATH_INFO", "").lstrip("/")
        if not path:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Not Found"]

        media_path = os.path.join(media_folder, path)

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

    return _media_wsgi


# ── WSGI unit tests (no HTTP, no sockets) ─────────────────────────────────

def run_wsgi_unit_tests(tmpdir, jpg_bytes, mp4_bytes):
    print("\n── Unit tests (direct WSGI calls) ─────────────────────────────")
    wsgi = make_media_wsgi(tmpdir)

    def call(path, range_header=""):
        status_holder = []
        headers_holder = []

        def start_response(status, headers):
            status_holder.append(status)
            headers_holder.extend(headers)

        environ = {"PATH_INFO": path}
        if range_header:
            environ["HTTP_RANGE"] = range_header

        body = b"".join(wsgi(environ, start_response))
        hdrs = dict(headers_holder)
        return status_holder[0], hdrs, body

    # 1 – simple GET of a JPEG
    status, hdrs, body = call("/test.jpg")
    check("JPEG: status 200 OK",       status == "200 OK",       status)
    check("JPEG: Content-Type",        hdrs.get("Content-Type") == "image/jpeg",
          hdrs.get("Content-Type"))
    check("JPEG: Accept-Ranges",       hdrs.get("Accept-Ranges") == "bytes")
    check("JPEG: full content",        body == jpg_bytes,
          f"got {len(body)} bytes, expected {len(jpg_bytes)}")

    # 2 – simple GET of MP4
    status, hdrs, body = call("/test.mp4")
    check("MP4:  status 200 OK",       status == "200 OK",       status)
    check("MP4:  Content-Type",        hdrs.get("Content-Type") == "video/mp4",
          hdrs.get("Content-Type"))
    check("MP4:  full content",        body == mp4_bytes,
          f"{len(body)} vs {len(mp4_bytes)}")

    # 3 – Range: bytes=0-  (whole file as 206)
    status, hdrs, body = call("/test.mp4", "bytes=0-")
    check("Range 0-: status 206",      status == "206 Partial Content", status)
    check("Range 0-: full content",    body == mp4_bytes,
          f"{len(body)} vs {len(mp4_bytes)}")
    check("Range 0-: Content-Range",   "Content-Range" in hdrs)

    # 4 – Partial range
    status, hdrs, body = call("/test.mp4", "bytes=100-199")
    check("Range 100-199: status 206",        status == "206 Partial Content", status)
    check("Range 100-199: Content-Length 100", hdrs.get("Content-Length") == "100",
          hdrs.get("Content-Length"))
    sz = len(mp4_bytes)
    check("Range 100-199: Content-Range",
          hdrs.get("Content-Range") == f"bytes 100-199/{sz}",
          hdrs.get("Content-Range"))
    check("Range 100-199: slice correct",     body == mp4_bytes[100:200])

    # 5 – Range: bytes=0-1023 (first 1 KB)
    status, hdrs, body = call("/test.mp4", "bytes=0-1023")
    check("Range 0-1023: length 1024",
          hdrs.get("Content-Length") == "1024", hdrs.get("Content-Length"))
    check("Range 0-1023: data correct",       body == mp4_bytes[:1024])

    # 6 – 404 for missing file
    status, hdrs, body = call("/nonexistent.jpg")
    check("404 for missing file",      status.startswith("404"), status)

    # 7 – 404 for empty path
    status, hdrs, body = call("/")
    check("404 for empty path",        status.startswith("404"), status)

    # 8 – 204 for zero-byte file
    zero = os.path.join(tmpdir, "empty.mp4")
    open(zero, "wb").close()
    status, hdrs, body = call("/empty.mp4")
    check("204 for zero-byte file",    status == "204 No Content", status)


# ── HTTP integration tests (real TCP socket via eventlet) ──────────────────

def run_http_tests(port, tmpdir, jpg_bytes, mp4_bytes):
    print("\n── Integration tests (HTTP via eventlet.wsgi) ──────────────────")

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

    # 1 – JPEG
    status, hdrs, body = get("/test.jpg")
    check("HTTP JPEG: 200",            status == 200, status)
    check("HTTP JPEG: content-type",   hdrs.get("Content-Type") == "image/jpeg",
          hdrs.get("Content-Type"))
    check("HTTP JPEG: Accept-Ranges",  hdrs.get("Accept-Ranges") == "bytes")
    check("HTTP JPEG: data correct",   body == jpg_bytes,
          f"{len(body)} vs {len(jpg_bytes)}")

    # 2 – MP4 full
    status, hdrs, body = get("/test.mp4")
    check("HTTP MP4: 200",             status == 200, status)
    check("HTTP MP4: content-type",    hdrs.get("Content-Type") == "video/mp4",
          hdrs.get("Content-Type"))
    check("HTTP MP4: data correct",    body == mp4_bytes)

    # 3 – Range bytes=0-
    status, hdrs, body = get("/test.mp4", range_header="bytes=0-")
    check("HTTP Range 0-: 206",        status == 206, status)
    check("HTTP Range 0-: full data",  body == mp4_bytes)

    # 4 – Partial range
    status, hdrs, body = get("/test.mp4", range_header="bytes=0-511")
    check("HTTP Range 0-511: 206",     status == 206, status)
    check("HTTP Range 0-511: 512 B",   len(body) == 512, len(body))
    check("HTTP Range 0-511: slice",   body == mp4_bytes[:512])

    # 5 – 404
    code, _, _ = get("/nope.jpg", expect_error=True)
    check("HTTP 404 for missing",      code == 404, code)


# ── main ───────────────────────────────────────────────────────────────────

def main():
    # Create temp media dir with test files
    tmpdir = tempfile.mkdtemp(prefix="media_test_")

    jpg_bytes = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e "
        b"\xff\xd9"  # minimal JPEG end
    )
    # 64 KB MP4-like data (ftyp header + padding)
    mp4_bytes = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * (64 * 1024)

    with open(os.path.join(tmpdir, "test.jpg"), "wb") as f:
        f.write(jpg_bytes)
    with open(os.path.join(tmpdir, "test.mp4"), "wb") as f:
        f.write(mp4_bytes)

    # WSGI unit tests (no network)
    run_wsgi_unit_tests(tmpdir, jpg_bytes, mp4_bytes)

    # Start media server on a free port
    port = 18765
    wsgi = make_media_wsgi(tmpdir)
    listener = eventlet.listen(("127.0.0.1", port))
    eventlet.spawn(eventlet.wsgi.server, listener, wsgi, log_output=False)
    eventlet.sleep(0.3)  # let the green thread start accepting

    run_http_tests(port, tmpdir, jpg_bytes, mp4_bytes)

    # Summary
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
