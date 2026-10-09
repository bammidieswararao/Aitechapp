#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "telugu-fonts.html",
    "telugu-fonts-client.js",
    "fonts-catalog.json",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the production Telugu Fonts deployment ZIP."
    )
    parser.add_argument(
        "--output",
        default="downloads/AiTechApp-Telugu-Fonts-Final.zip",
    )
    args = parser.parse_args()

    missing = [p for p in REQUIRED_FILES if not (ROOT / p).is_file()]
    if missing:
        raise SystemExit(f"Missing required production files: {missing}")

    catalog = json.loads((ROOT / "fonts-catalog.json").read_text(encoding="utf-8"))
    fonts = catalog["fonts"] if isinstance(catalog, dict) else catalog
    if not isinstance(fonts, list) or not fonts:
        raise SystemExit("fonts-catalog.json has no font rows")

    # Production sanity checks before packaging.
    ids = set()
    for row in fonts:
        fid = row.get("id")
        if not fid or fid in ids:
            raise SystemExit(f"Duplicate or missing id: {fid!r}")
        ids.add(fid)

        if row.get("preview_ready") is not True:
            raise SystemExit(f"Font not preview-ready: {fid}")

        typ = str(row.get("download_type", "")).upper()
        if typ not in {"TTF", "OTF"}:
            raise SystemExit(f"Invalid download type for {fid}: {typ}")

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    # Deterministic metadata timestamp keeps rebuilds stable where content is unchanged.
    fixed_time = (2026, 10, 9, 0, 0, 0)

    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in REQUIRED_FILES:
            data = (ROOT / relative).read_bytes()
            info = zipfile.ZipInfo(relative, date_time=fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)

    print(
        f"Built {output.relative_to(ROOT)} with "
        f"{len(REQUIRED_FILES)} files and {len(fonts)} font rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
