"""Record how many people actually download Mimir, over time.

The decision on whether to buy a code-signing certificate was deferred until
there is evidence of how many people the SmartScreen warning turns away. That
evidence does not collect itself, and Mimir deliberately has no telemetry, so
this is the one number available without instrumenting the app: GitHub reports
a download count per release asset.

What this can tell you:
  * how many people got far enough to download,
  * the rate over time, and whether a change to the page moved it.

What it cannot tell you, and no amount of massaging will:
  * how many of those downloads survived the SmartScreen warning,
  * how many installed, scanned anything, or came back.

For that, the observable proxies are feedback emails and contributed clips.
A download that never produces either is indistinguishable from one that was
abandoned at the warning -- which is exactly why the certificate decision has
to be read alongside the feedback inbox, not from this number alone.

Usage:
    python scripts/download_stats.py --repo owner/name
    python scripts/download_stats.py --repo owner/name --show
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HISTORY = Path(__file__).resolve().parents[1] / "release_assets" / "download_stats.json"


def fetch_releases(repo: str, token: str = "") -> list[dict]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "mimir-download-stats",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, list) else []


def read_history(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def snapshot(releases: list[dict]) -> dict:
    assets = []
    for release in releases:
        if release.get("draft"):
            continue
        for asset in release.get("assets", []):
            name = str(asset.get("name") or "")
            if not name.lower().endswith(".exe"):
                continue
            assets.append(
                {
                    "release": str(release.get("tag_name") or ""),
                    "asset": name,
                    "downloads": int(asset.get("download_count") or 0),
                    "published_at": str(release.get("published_at") or ""),
                }
            )
    return {
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "total_downloads": sum(item["downloads"] for item in assets),
        "assets": assets,
    }


def show(history: list[dict]) -> None:
    if not history:
        print("No history recorded yet.")
        return

    print(f"{'recorded':<22} {'total':>7}  {'change':>7}")
    previous = None
    for entry in history:
        total = int(entry.get("total_downloads") or 0)
        change = "" if previous is None else f"+{total - previous}"
        print(f"{entry.get('recorded_at', ''):<22} {total:>7}  {change:>7}")
        previous = total

    latest = history[-1]
    if latest.get("assets"):
        print("\nlatest by asset:")
        for asset in sorted(latest["assets"], key=lambda item: -item["downloads"]):
            print(f"  {asset['downloads']:>6}  {asset['release']}  {asset['asset']}")

    print(
        "\nDownloads only. Installs, and how many survived the SmartScreen warning,\n"
        "are not measurable without telemetry Mimir does not have -- read this\n"
        "alongside the feedback inbox before deciding on a certificate."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="", help="owner/name of the releases repository")
    parser.add_argument("--token", default="", help="GitHub token (only needed for private repos or rate limits)")
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--show", action="store_true", help="print recorded history without fetching")
    args = parser.parse_args()

    history_path = Path(args.history)
    history = read_history(history_path)

    if args.show:
        show(history)
        return 0

    if not args.repo:
        parser.error("--repo is required unless --show is used")

    try:
        releases = fetch_releases(args.repo, args.token)
    except urllib.error.HTTPError as error:
        print(f"GitHub returned {error.code}: {error.reason}")
        return 1
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"Could not reach GitHub: {error}")
        return 1

    current = snapshot(releases)
    if not current["assets"]:
        print(f"No published .exe release assets found in {args.repo}.")
        print("If the release is still a draft, publish it first -- drafts report no downloads.")
        return 1

    history.append(current)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {current['total_downloads']} total downloads to {history_path}")
    show(history)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
