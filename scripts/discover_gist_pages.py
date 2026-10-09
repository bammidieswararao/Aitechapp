#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()

def extract(label: str, body: str) -> str:
    pattern = rf"{re.escape(label)}\s*:\s*(?:</?[^>]+>\s*)*([^<\r\n]+)"
    match = re.search(pattern, body, flags=re.I)
    return clean(match.group(1)) if match else ""

def discover_one(session: requests.Session, font_id: int) -> dict | None:
    url = f"https://www.telugufont.com/download.php?id={font_id}"
    response = session.get(url, timeout=(10, 25), headers={"User-Agent": "AiTechApp-GIST-MetadataAudit/1"})
    if response.status_code != 200:
        return None
    body = response.text
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.I | re.S)
    title = clean(title_match.group(1)) if title_match else ""
    if "GIST-TLOT" not in body and "GIST-TLOT" not in title:
        return None

    family = extract("Font Family", body)
    style = extract("Font Subfamily", body)
    psname = extract("Postscript Name", body)
    full_name = extract("Full Name", body)
    copyright_text = extract("Copyright", body)

    filename_match = re.search(r"Short Details of\s*([^<]+?\.(?:ttf|otf))", body, flags=re.I)
    filename = clean(filename_match.group(1)) if filename_match else ""

    return {
        "id": font_id,
        "page_url": url,
        "title": title,
        "family": family,
        "style": style,
        "full_name": full_name,
        "postscript_name": psname,
        "filename": filename,
        "copyright": copyright_text,
        "requires_human_verification": "captcha" in body.casefold() or "verify" in body.casefold(),
        "binary_downloaded": False,
        "note": "Metadata GET only. This crawler does not solve, OCR, submit, or bypass the verification challenge.",
    }

def main() -> int:
    parser = argparse.ArgumentParser(description="Discover public GIST-TLOT metadata pages without bypassing CAPTCHA.")
    parser.add_argument("--start-id", type=int, default=737650)
    parser.add_argument("--end-id", type=int, default=737730)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--output", default="font-ingest/gist-discovery.json")
    args = parser.parse_args()

    session = requests.Session()
    found = []
    for font_id in range(args.start_id, args.end_id + 1):
        try:
            row = discover_one(session, font_id)
            if row:
                found.append(row)
                print(font_id, row.get("family"), row.get("style"))
        except Exception as exc:
            print("WARN", font_id, exc)
        time.sleep(max(args.delay, 0))

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema_version": 1,
        "range": [args.start_id, args.end_id],
        "count": len(found),
        "records": found,
        "policy": "No automated verification-code/CAPTCHA solving or form submission.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(found)} GIST metadata records to {output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
