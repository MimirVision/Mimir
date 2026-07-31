"""A local stand-in for the real Cloudflare Worker intake endpoint.

Implements the exact contract the real Worker will expose, so the Rust
Outbox/upload logic can be built and tested end-to-end before a Cloudflare
account exists. This file is also the literal reference for writing the real
Worker once Phase 0 (creating the Cloudflare account) is done -- the routes,
headers, size caps, and error vocabulary below should match it exactly.

Contract:
    POST /v1/submit/contribution   -- raw bytes of a .mimir-dataset.age file
    POST /v1/submit/feedback       -- raw bytes of a .mimir-feedback.age file

    Required headers:
        X-Mimir-App-Token: <token>       (filters non-Mimir traffic, not real auth)
        X-Mimir-Package-Id: <32 hex chars>
        Content-Length: <bytes>

    201 {"accepted": true, "object_key": "..."}
    4xx {"accepted": false, "reason": "too_large"|"bad_token"|"invalid_package_id"|
                                       "duplicate"|"bad_content"}

Deliberately has no GET or list route, on any path -- the real Worker won't
either. The client only ever writes; retrieval is a separate, developer-only
path (in the real deployment, a direct R2 API call from mimir_training_ground.py,
never through this server). For local debugging, use this script's `list`
subcommand, which reads the storage directory directly rather than adding an
HTTP route that would defeat the point.

Usage:
    python scripts/dev_intake_mock.py serve --storage-dir ./dev_intake_storage --app-token dev-token
    python scripts/dev_intake_mock.py list --storage-dir ./dev_intake_storage
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGE_MAGIC = b"age-encryption.org/v1"
PACKAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")

ROUTES = {
    "/v1/submit/contribution": {
        "prefix": "contributions",
        "suffix": ".mimir-dataset.age",
        "max_bytes": 2 * 1024 * 1024 * 1024,  # contributions carry raw video
    },
    "/v1/submit/feedback": {
        "prefix": "feedback",
        "suffix": ".mimir-feedback.age",
        "max_bytes": 500 * 1024 * 1024,
    },
}

CHUNK_SIZE = 1024 * 1024


def _make_handler(storage_dir: Path, app_token: str):
    class IntakeHandler(BaseHTTPRequestHandler):
        server_version = "MimirDevIntakeMock/1"

        def _reject(self, status: int, reason: str) -> None:
            body = json.dumps({"accepted": False, "reason": reason}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _accept(self, object_key: str) -> None:
            body = json.dumps({"accepted": True, "object_key": object_key}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server's naming convention
            # No read/list route, ever, on any path -- matches the real Worker.
            self._reject(404, "not_found")

        do_HEAD = do_GET

        def do_POST(self) -> None:  # noqa: N802
            route = ROUTES.get(self.path)
            if route is None:
                self._reject(404, "not_found")
                return

            if self.headers.get("X-Mimir-App-Token", "") != app_token:
                self._reject(401, "bad_token")
                return

            package_id = self.headers.get("X-Mimir-Package-Id", "")
            if not PACKAGE_ID_RE.match(package_id):
                self._reject(400, "invalid_package_id")
                return

            try:
                content_length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                content_length = -1
            if content_length < 0:
                self._reject(411, "length_required")
                return
            if content_length > route["max_bytes"]:
                self._reject(413, "too_large")
                return

            now = datetime.now(timezone.utc)
            object_key = f"{route['prefix']}/{now:%Y}/{now:%m}/{package_id}{route['suffix']}"
            destination = storage_dir / object_key
            if destination.exists():
                self._reject(409, "duplicate")
                return

            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_suffix(destination.suffix + ".partial")
            written = 0
            first_chunk = b""
            try:
                with partial.open("wb") as handle:
                    remaining = content_length
                    while remaining > 0:
                        chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        if not first_chunk:
                            first_chunk = chunk
                        handle.write(chunk)
                        written += len(chunk)
                        remaining -= len(chunk)
                        # Streaming guard: Content-Length is client-reported and
                        # could lie. Abort as soon as the real cap is exceeded,
                        # regardless of what the header claimed.
                        if written > route["max_bytes"]:
                            raise ValueError("stream exceeded size cap")
            except (ValueError, OSError):
                partial.unlink(missing_ok=True)
                self._reject(413, "too_large")
                return

            if written != content_length:
                partial.unlink(missing_ok=True)
                self._reject(400, "incomplete_body")
                return

            # Shape validation only -- this server never has the age private
            # key, so it cannot verify the payload actually decrypts to
            # anything meaningful. It can only reject obvious garbage cheaply.
            if not first_chunk.startswith(AGE_MAGIC):
                partial.unlink(missing_ok=True)
                self._reject(400, "bad_content")
                return

            partial.replace(destination)
            self._accept(object_key)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            sys.stderr.write(f"[dev-intake-mock] {self.address_string()} {format % args}\n")

    return IntakeHandler


def serve(storage_dir: Path, app_token: str, port: int) -> None:
    storage_dir.mkdir(parents=True, exist_ok=True)
    handler = _make_handler(storage_dir, app_token)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"dev intake mock listening on http://127.0.0.1:{port}")
    print(f"storage: {storage_dir}")
    print("routes: POST /v1/submit/contribution, POST /v1/submit/feedback")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def list_storage(storage_dir: Path) -> None:
    if not storage_dir.exists():
        print(f"No storage directory yet: {storage_dir}")
        return
    files = sorted(item for item in storage_dir.rglob("*") if item.is_file() and not item.name.endswith(".partial"))
    if not files:
        print("Nothing stored yet.")
        return
    for path in files:
        size_kb = path.stat().st_size / 1024
        print(f"{size_kb:8.1f} KB  {path.relative_to(storage_dir).as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the mock intake server.")
    serve_parser.add_argument("--storage-dir", required=True)
    serve_parser.add_argument("--app-token", default="dev-token")
    serve_parser.add_argument("--port", type=int, default=8787)

    list_parser = subparsers.add_parser("list", help="List what has been submitted (not exposed over HTTP).")
    list_parser.add_argument("--storage-dir", required=True)

    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(Path(args.storage_dir), args.app_token, args.port)
        return 0
    list_storage(Path(args.storage_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
