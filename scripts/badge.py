#!/usr/bin/env python3
"""Generate shields.io-style SVG badges from CI results. Standard library only.

    badge.py coverage <coverage.xml> <out.svg> [--label coverage]
    badge.py tests    <junit.xml>    <out.svg> [--label tests]
    badge.py status   <job result>   <out.svg> --label build

`coverage` combines lines and branches the same way `coverage report` does, so the badge shows the
same TOTAL. `tests` reads pytest's JUnit XML. `status` takes a GitHub Actions job result (success,
failure, cancelled, skipped). A missing or unreadable input file produces a grey "unknown" badge
rather than an error, so one failed job never stops the other badges being published.
"""
import argparse
import sys
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path

COLORS = {
    "brightgreen": "#4c1",
    "green": "#97ca00",
    "yellowgreen": "#a4a61d",
    "yellow": "#dfb317",
    "orange": "#fe7d37",
    "red": "#e05d44",
    "grey": "#9f9f9f",
    "blue": "#007ec6",
}

# Approximate Verdana 11px advance widths; good enough to size the badge boxes.
NARROW, WIDE = set("fijlrt1 .,:;!|()'"), set("mwMW%@")


def text_width(text: str) -> int:
    return sum(4 if c in NARROW else 10 if c in WIDE else 7 for c in text)


def svg(label: str, message: str, color: str) -> str:
    lw, mw = text_width(label) + 10, text_width(message) + 10
    total = lw + mw
    fill = COLORS.get(color, color)
    label, message = escape(label), escape(message)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{label}: {message}">
  <title>{label}: {message}</title>
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw}" height="20" fill="#555"/>
    <rect x="{lw}" width="{mw}" height="20" fill="{fill}"/>
    <rect width="{total}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{lw / 2}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{lw / 2}" y="14">{label}</text>
    <text x="{lw + mw / 2}" y="15" fill="#010101" fill-opacity=".3">{message}</text>
    <text x="{lw + mw / 2}" y="14">{message}</text>
  </g>
</svg>
"""


def coverage_badge(path: Path) -> tuple[str, str]:
    root = ET.parse(path).getroot()
    lines, lines_hit = int(root.get("lines-valid", 0)), int(root.get("lines-covered", 0))
    branches, branches_hit = int(root.get("branches-valid", 0)), int(root.get("branches-covered", 0))
    total = lines + branches
    pct = 100.0 * (lines_hit + branches_hit) / total if total else 100.0
    scale = ((95, "brightgreen"), (90, "green"), (80, "yellowgreen"), (70, "yellow"), (60, "orange"))
    color = next((c for floor, c in scale if pct >= floor), "red")
    # Truncate, like `coverage report`'s TOTAL, so 94.9% never shows as a rounded-up 95%.
    return f"{int(pct)}%", color


def tests_badge(path: Path) -> tuple[str, str]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    count = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    failed = count["failures"] + count["errors"]
    passed = count["tests"] - failed - count["skipped"]
    parts = [f"{failed} failed"] if failed else []
    parts.append(f"{passed} passed")
    if count["skipped"]:
        parts.append(f"{count['skipped']} skipped")
    return ", ".join(parts), "red" if failed else "brightgreen"


def status_badge(result: str) -> tuple[str, str]:
    return {
        "success": ("passing", "brightgreen"),
        "failure": ("failing", "red"),
        "cancelled": ("cancelled", "grey"),
        "skipped": ("skipped", "grey"),
    }.get(result, (result or "unknown", "grey"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["coverage", "tests", "status"])
    parser.add_argument("source", help="coverage.xml, junit.xml, or a job result")
    parser.add_argument("out", type=Path)
    parser.add_argument("--label")
    args = parser.parse_args(argv)

    label = args.label or args.kind
    if args.kind == "status":
        message, color = status_badge(args.source)
    else:
        try:
            message, color = (coverage_badge if args.kind == "coverage" else tests_badge)(Path(args.source))
        except (OSError, ET.ParseError) as e:
            print(f"{args.source}: {e}; writing an 'unknown' badge", file=sys.stderr)
            message, color = "unknown", "grey"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg(label, message, color))
    print(f"{args.out}: {label} | {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
