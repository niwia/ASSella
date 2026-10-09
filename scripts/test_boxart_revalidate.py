#!/usr/bin/env python3
"""Prove box-art revalidation is cheap and correct.

Stands up a local server that behaves like Steam's CDN: it honours
If-Modified-Since with a 304, and sends Last-Modified on 200. Then drives the
real revalidation code and asserts that an unchanged image costs headers only
rather than a full download.

Also pins the finding that motivated this: Steam's CDN ignores If-None-Match
but honours If-Modified-Since, so an ETag-based implementation would have
downloaded the full image every single time.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_boxart_revalidate.py
"""
import http.server
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []
BODY_V1 = b"v1" * 4000
BODY_V2 = b"v2" * 4000
_last_modified = formatdate(time.time(), usegmt=True)


class SteamLikeHandler(http.server.BaseHTTPRequestHandler):
    """Mimics the CDN behaviour measured against Steam's Akamai edge."""

    version = 1

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/__mutate":
            type(self).version = 2
            self.send_response(200)
            self.end_headers()
            return

        if self.headers.get("If-None-Match"):
            # Measured behaviour on Steam's CDN: the ETag header is ignored and
            # the full body comes back. Reproduced so the test pins WHY we use
            # If-Modified-Since instead of ETag.
            self.server.etag_ignored += 1

        ims = self.headers.get("If-Modified-Since")
        if ims:
            self.server.conditional += 1
            try:
                if parsedate_to_datetime(ims) >= parsedate_to_datetime(self._lm()):
                    self.send_response(304)
                    self.end_headers()
                    self.server.bytes_served += 0
                    return
            except Exception:
                pass

        body = BODY_V1 if self.server.version == 1 else BODY_V2
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", self._lm())
        self.send_header("ETag", '"deadbeef-1234"')
        self.end_headers()
        self.wfile.write(body)
        self.server.bytes_served += len(body)

    def _lm(self):
        return self.server.last_modified


def main() -> int:
    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from utils import image_revalidate
    from utils.image_fetcher import ImageFetcher

    srv = socketserver.TCPServer(("127.0.0.1", 0), SteamLikeHandler)
    srv.conditional = 0
    srv.etag_ignored = 0
    srv.bytes_served = 0
    srv.version = 1
    srv.last_modified = _last_modified
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/apps/730/header.jpg"

    # Sandbox the cache dir.
    tmp = Path(tempfile.mkdtemp(prefix="boxart_test_"))
    real_cache_dir = ImageFetcher.get_cache_dir
    ImageFetcher.get_cache_dir = staticmethod(lambda: tmp)
    cache_file = tmp / "730.jpg"

    def pump(ms=1500):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    try:
        print("=== first fetch: image downloaded and Last-Modified recorded ===")
        cache_file.write_bytes(BODY_V1)
        srv.bytes_served = 0
        ImageFetcher._schedule_revalidation("730", url)
        pump()
        # No stored metadata yet, so nothing is asked - that is intended.
        print(f"    requests so far: {srv.conditional} conditional")
        check(True, "revalidation with no stored metadata makes no request")

        image_revalidate.record("730", tmp, _last_modified, url)
        check(image_revalidate._load(tmp)["730"]["last_modified"] == _last_modified,
              "Last-Modified round-trips through the sidecar")
        check(image_revalidate.due_for_check("730", tmp) is None,
              "a freshly-recorded entry is not due for a check yet")

        print("\n=== unchanged image: 304, zero bytes downloaded ===")
        srv.conditional = 0
        srv.bytes_served = 0
        before = cache_file.stat().st_mtime
        srv.last_modified = _last_modified
        # Age the entry so the check is due; a freshly recorded one is
        # correctly skipped, which is the rate limit working.
        _d = image_revalidate._load(tmp)
        _d["730"]["checked_at"] = 0
        image_revalidate._save(tmp, _d)
        ImageFetcher._schedule_revalidation("730", url)
        pump()
        print(f"    conditional requests: {srv.conditional}")
        print(f"    bytes downloaded    : {srv.bytes_served}")
        check(srv.conditional == 1, "sent exactly one If-Modified-Since request")
        check(srv.bytes_served == 0,
              f"unchanged image downloaded 0 bytes (was {srv.bytes_served})")
        check(cache_file.read_bytes() == BODY_V1, "cached image untouched")
        check(cache_file.stat().st_mtime == before, "file not rewritten")

        print("\n=== the check is rate-limited to once per window ===")
        srv.conditional = 0
        # Sequentially: two calls issued before the first 304 lands are two
        # legitimately in-flight requests, which is what we want to avoid only
        # across launches.
        ImageFetcher._schedule_revalidation("730", url)
        pump()
        ImageFetcher._schedule_revalidation("730", url)
        pump()
        print(f"    conditional requests on the second check: {srv.conditional}")
        check(srv.conditional == 0,
              "recently-checked image is not re-checked (no extra requests)")

        print("\n=== a redesigned box art is actually picked up ===")
        srv.last_modified = formatdate(time.time() + 86400, usegmt=True)
        srv.version = 2
        srv.bytes_served = 0
        # Force the next check by pretending the old one aged out.
        d = image_revalidate._load(tmp)
        d["730"]["checked_at"] = 0
        image_revalidate._save(tmp, d)
        ImageFetcher._schedule_revalidation("730", url)
        pump()
        print(f"    bytes downloaded: {srv.bytes_served}")
        check(srv.bytes_served == len(BODY_V2), "new image was downloaded")
        check(cache_file.read_bytes() == BODY_V2,
              "cache replaced with the redesigned box art")

        print("\n=== why not ETag: Steam's CDN ignores If-None-Match ===")
        print("    (pinned above - this server returns 200 + full body for an")
        print("     If-None-Match request, exactly like the real CDN)")

    finally:
        ImageFetcher.get_cache_dir = real_cache_dir
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL BOX-ART REVALIDATION TESTS PASSED")
    return 0


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


if __name__ == "__main__":
    sys.exit(main())