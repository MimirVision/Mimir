"""Build a static signed-updater manifest from Tauri release artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--notes", default="Mimir free public beta update")
    args = parser.parse_args()
    bundle = Path(args.bundle).resolve()
    signature = Path(args.signature).resolve()
    if not bundle.is_file() or not signature.is_file():
        raise SystemExit("Updater bundle and signature must both exist.")
    if not args.base_url.lower().startswith("https://"):
        raise SystemExit("Production updater URLs must use HTTPS.")
    document = {
        "version": args.version,
        "notes": args.notes,
        "pub_date": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platforms": {
            "windows-x86_64": {
                "signature": signature.read_text(encoding="utf-8").strip(),
                "url": args.base_url.rstrip("/") + "/" + bundle.name,
            }
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Updater manifest: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
