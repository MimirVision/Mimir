"""Serve the landing page the way GitHub Pages will serve it.

The page cannot simply be served from its own folder. styles.css imports the
shared tokens with `@import "../../design-system/tokens.css"`, which escapes a
server rooted here -- and a 404 on a stylesheet import is silent. The page still
renders, just with every custom property unset, which showed up as black text on
a black background while the deployed site was perfectly fine.

Serving the repository root would make that path resolve, but it would also put
the entire repository on a local HTTP port, including backend/mimir_intake_identity.txt,
which is an encryption secret. Not worth it for a preview.

So this does what .github/workflows/landing-page.yml does: copies the page into a
staging directory, copies tokens.css in beside the stylesheet, rewrites the
import to match, and serves that. A local preview then shows the same file layout
that gets published, which is the only version worth checking against.
"""

from __future__ import annotations

import functools
import http.server
import shutil
import tempfile
from pathlib import Path

PAGE = Path(__file__).resolve().parent
TOKENS = PAGE.parents[1] / "design-system" / "tokens.css"
PORT = 4173


def stage(destination: Path) -> None:
    shutil.copytree(PAGE, destination, dirs_exist_ok=True)
    (destination / Path(__file__).name).unlink(missing_ok=True)
    shutil.copy2(TOKENS, destination / "tokens.css")

    stylesheet = destination / "styles.css"
    text = stylesheet.read_text(encoding="utf-8")
    staged = text.replace("../../design-system/tokens.css", "./tokens.css")
    if staged == text:
        raise SystemExit(
            "styles.css no longer imports ../../design-system/tokens.css. "
            "Update this script and the Pages workflow together."
        )
    stylesheet.write_text(staged, encoding="utf-8")

    if "../" in staged:
        raise SystemExit("styles.css still contains a path that escapes the site root.")


def main() -> None:
    if not TOKENS.is_file():
        raise SystemExit(f"Shared tokens are missing: {TOKENS}")

    with tempfile.TemporaryDirectory(prefix="mimir-landing-") as temporary:
        root = Path(temporary) / "site"
        stage(root)

        # partial, not a subclass with a `directory` class attribute:
        # SimpleHTTPRequestHandler.__init__ takes `directory` as a keyword and
        # falls back to os.getcwd(), so a class attribute of that name is
        # silently overwritten and the server quietly hands out the working
        # directory -- here, the whole repository -- instead of the staged site.
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))

        # Threaded, because SimpleHTTPRequestHandler speaks HTTP/1.1 with
        # keep-alive: a single-threaded server holds the first connection open
        # and every later request queues behind it until the browser gives up.
        class Server(http.server.ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        with Server(("127.0.0.1", PORT), handler) as httpd:
            print(f"Serving the staged landing page on http://127.0.0.1:{PORT}")
            print(f"Staged from {PAGE} with tokens from {TOKENS}")
            httpd.serve_forever()


if __name__ == "__main__":
    main()
