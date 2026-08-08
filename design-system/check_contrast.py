"""Recompute every documented contrast ratio from tokens.css.

Written because the first draft of README.md claimed 16.1:1 for --mimir-text
when the real figure is 18.0:1. A design system that publishes accessibility
numbers has to be able to prove them, or it is just a nicer-looking guess.

Reads the colours out of tokens.css rather than repeating them here, so
changing a token and forgetting to update the docs is a test failure instead
of a slow drift into fiction.

    python design-system/check_contrast.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOKENS = Path(__file__).resolve().parent / "tokens.css"
README = Path(__file__).resolve().parent / "README.md"

# Text tokens must clear WCAG AA for normal text.
TEXT_MIN = 4.5
# Severity colours are dots, bars and borders -- meaning carried by colour,
# but not text, so the non-text minimum applies.
NON_TEXT_MIN = 3.0

TEXT_TOKENS = ["--mimir-text", "--mimir-text-muted", "--mimir-text-subtle", "--mimir-accent"]
SEVERITY_TOKENS = [
    "--mimir-severity-important",
    "--mimir-severity-review",
    "--mimir-severity-ignore",
    "--mimir-severity-ok",
]
BACKGROUND = "--mimir-bg"


def read_tokens() -> dict[str, str]:
    text = TOKENS.read_text(encoding="utf-8")
    found = {}
    for name, value in re.findall(r"(--mimir-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", text):
        found[name] = value
    return found


def _channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    colour = colour.lstrip("#")
    r, g, b = (int(colour[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(foreground: str, background: str) -> float:
    a, b = luminance(foreground), luminance(background)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def main() -> int:
    tokens = read_tokens()
    missing = [
        name
        for name in [BACKGROUND, *TEXT_TOKENS, *SEVERITY_TOKENS]
        if name not in tokens
    ]
    if missing:
        print(f"Not found in tokens.css: {', '.join(missing)}")
        return 1

    background = tokens[BACKGROUND]
    readme = README.read_text(encoding="utf-8")
    failures: list[str] = []

    print(f"Against {BACKGROUND} ({background})\n")
    print(f"{'token':<30}{'ratio':>8}   floor   documented")
    print("-" * 62)

    for name in TEXT_TOKENS:
        ratio = contrast(tokens[name], background)
        row = f"{name:<30}{ratio:>8.2f}   {TEXT_MIN:<7.1f}"

        if ratio < TEXT_MIN:
            failures.append(f"{name} is {ratio:.2f}:1, below the {TEXT_MIN} floor for text")
            row += " FAILS"
        else:
            # The README publishes these to one decimal place. Check the
            # published figure is the true one rather than a stale one.
            published = re.search(
                rf"`{re.escape(name)}`\s*\|\s*([0-9.]+):1", readme
            )
            if not published:
                row += " (not in README)"
            elif abs(float(published.group(1)) - ratio) >= 0.1:
                failures.append(
                    f"README says {name} is {published.group(1)}:1; it is {ratio:.1f}:1"
                )
                row += f" STALE ({published.group(1)})"
            else:
                row += " ok"
        print(row)

    print()
    for name in SEVERITY_TOKENS:
        ratio = contrast(tokens[name], background)
        ok = ratio >= NON_TEXT_MIN
        if not ok:
            failures.append(f"{name} is {ratio:.2f}:1, below the {NON_TEXT_MIN} non-text floor")
        print(f"{name:<30}{ratio:>8.2f}   {NON_TEXT_MIN:<7.1f} {'ok' if ok else 'FAILS'}")

    if failures:
        print("\nFAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nAll documented contrast figures are correct.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
