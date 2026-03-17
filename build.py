#!/usr/bin/env python3
"""
build.py — assemble dmarc-feed.html from source files.

Usage:
  python3 build.py [--output dmarc-feed.html]

Requires:
  npm packages @fontsource/dm-sans and @fontsource/dm-mono in node_modules/
  jszip in node_modules/ (or pre-downloaded as jszip.min.js)

  Install with:
    npm install @fontsource/dm-sans @fontsource/dm-mono jszip
"""

import argparse
import base64
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
SRC  = HERE / "src"


def b64font(path: pathlib.Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def build_fonts_css() -> str:
    base = HERE / "node_modules"
    lines = []

    dm_sans = base / "@fontsource" / "dm-sans" / "files"
    for weight, style in [(300, "normal"), (400, "normal"), (400, "italic"), (500, "normal")]:
        path = dm_sans / f"dm-sans-latin-{weight}-{style}.woff2"
        if not path.exists():
            print(f"[warn] Missing font: {path}", file=sys.stderr)
            continue
        data = b64font(path)
        lines.append(
            f"@font-face{{font-family:'DM Sans';font-style:{style};"
            f"font-weight:{weight};font-display:swap;"
            f"src:url('data:font/woff2;base64,{data}') format('woff2');}}"
        )

    dm_mono = base / "@fontsource" / "dm-mono" / "files"
    for weight in [400, 500]:
        path = dm_mono / f"dm-mono-latin-{weight}-normal.woff2"
        if not path.exists():
            print(f"[warn] Missing font: {path}", file=sys.stderr)
            continue
        data = b64font(path)
        lines.append(
            f"@font-face{{font-family:'DM Mono';font-style:normal;"
            f"font-weight:{weight};font-display:swap;"
            f"src:url('data:font/woff2;base64,{data}') format('woff2');}}"
        )

    return "\n".join(lines)


def find_jszip() -> str:
    candidates = [
        HERE / "node_modules" / "jszip" / "dist" / "jszip.min.js",
        HERE / "jszip.min.js",
    ]
    for p in candidates:
        if p.exists():
            return p.read_text()
    print("[error] jszip.min.js not found. Run: npm install jszip", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build standalone DMARC analyzer HTML")
    parser.add_argument("--output", default=str(HERE / "dmarc-feed.html"))
    args = parser.parse_args()

    print("Building fonts CSS...", file=sys.stderr)
    fonts_css = build_fonts_css()

    print("Loading JSZip...", file=sys.stderr)
    jszip = find_jszip()

    print("Reading source files...", file=sys.stderr)
    template = (SRC / "index.html").read_text()
    style    = (SRC / "style.css").read_text()
    script   = (SRC / "app.js").read_text()

    html = (
        template
        .replace("{{FONTS}}", f"<style>{fonts_css}</style>")
        .replace("{{STYLE}}", style)
        .replace("{{JSZIP}}", jszip)
        .replace("{{SCRIPT}}", script)
    )

    out = pathlib.Path(args.output)
    out.write_text(html)
    print(f"Written: {out} ({len(html):,} bytes / {len(html)//1024} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
