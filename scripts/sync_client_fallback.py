#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    parser = argparse.ArgumentParser(description="Update only the embedded FALLBACK_FONTS constant.")
    parser.add_argument("--catalog", default="fonts-catalog.json")
    parser.add_argument("--client", default="telugu-fonts-client.js")
    args = parser.parse_args()

    catalog = json.loads((ROOT / args.catalog).read_text(encoding="utf-8"))
    fonts = catalog["fonts"] if isinstance(catalog, dict) else catalog
    client_path = ROOT / args.client
    source = client_path.read_text(encoding="utf-8")

    compact = json.dumps(fonts, ensure_ascii=False, separators=(",", ":"))
    pattern = r"const FALLBACK_FONTS = .*?;\nconst DEFAULT_TEXT = "
    replacement = "const FALLBACK_FONTS = " + compact + ";\nconst DEFAULT_TEXT = "
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.S)
    if count != 1:
        raise SystemExit("Could not locate the FALLBACK_FONTS constant safely")

    client_path.write_text(updated, encoding="utf-8")
    print(f"Embedded {len(fonts)} catalog rows without changing UI markup")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
