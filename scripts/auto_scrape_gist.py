#!/usr/bin/env python3
from __future__ import annotations

"""
AiTechApp GIST-TLOT authorized-source fetcher.

This script intentionally DOES NOT solve, OCR, submit, or bypass verification/CAPTCHA
challenges. It only downloads font binaries from direct URLs that the repository
owner has explicitly marked as authorized for automated retrieval and redistribution.

Expected manifest: font-ingest/gist-authorized-sources.json
"""

import argparse
import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
TELUGU_MIN, TELUGU_MAX = 0x0C00, 0x0C7F
MAX_FONT_BYTES = 25 * 1024 * 1024


def font_signature_ok(data: bytes) -> bool:
    return data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")


def name_value(font: TTFont, name_id: int, fallback: str = "") -> str:
    if "name" not in font:
        return fallback
    records = [n for n in font["name"].names if n.nameID == name_id]
    records.sort(key=lambda n: (0 if n.platformID == 3 else 1, 0 if getattr(n, "langID", 0) in (0, 0x409) else 1))
    for rec in records:
        try:
            text = rec.toUnicode().strip()
            if text:
                return text
        except Exception:
            pass
    return fallback


def inspect_font(data: bytes, fallback_family: str) -> dict[str, Any]:
    if not font_signature_ok(data):
        raise ValueError("response is not a TTF/OTF binary")

    with TTFont(io.BytesIO(data), lazy=False) as font:
        family = name_value(font, 16) or name_value(font, 1) or fallback_family
        style = name_value(font, 17) or name_value(font, 2) or "Regular"
        psname = name_value(font, 6) or f"{family}-{style}"
        weight = 400
        if "OS/2" in font:
            weight = int(getattr(font["OS/2"], "usWeightClass", 400) or 400)

        codepoints: set[int] = set()
        if "cmap" in font:
            for table in font["cmap"].tables:
                if table.isUnicode():
                    codepoints.update(table.cmap.keys())

        telugu_count = sum(1 for cp in codepoints if TELUGU_MIN <= cp <= TELUGU_MAX)
        if telugu_count == 0:
            raise ValueError("font has no Unicode Telugu cmap entries")

    return {
        "family": family.strip(),
        "style": style.strip() or "Regular",
        "postscript_name": psname.strip(),
        "weight": weight,
        "telugu_codepoints": telugu_count,
    }


def safe_filename(value: str, ext: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "font"
    return stem + ext.lower()


def fetch_direct_font(session: requests.Session, url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError("only HTTP(S) direct URLs are allowed")

    response = session.get(
        url,
        timeout=(12, 45),
        stream=True,
        allow_redirects=True,
        headers={"User-Agent": "AiTechApp-Authorized-GIST-Fetch/1"},
    )
    response.raise_for_status()

    content_type = (response.headers.get("content-type") or "").lower()
    if "text/html" in content_type:
        raise ValueError("URL returned HTML instead of a font binary; protected/verification pages are not automated")

    out = bytearray()
    for chunk in response.iter_content(256 * 1024):
        if not chunk:
            continue
        out.extend(chunk)
        if len(out) > MAX_FONT_BYTES:
            raise ValueError("font response exceeds size limit")

    data = bytes(out)
    if not font_signature_ok(data):
        raise ValueError("downloaded response is not a TTF/OTF binary")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="font-ingest/gist-authorized-sources.json")
    ap.add_argument("--output-dir", default="fonts/incoming/gist")
    ap.add_argument("--report", default="font-ingest/gist-fetch-report.json")
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    manifest_path = ROOT / args.manifest
    output_dir = ROOT / args.output_dir
    report_path = ROOT / args.report

    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = list(doc.get("records", []))
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    report = {"fetched": [], "skipped": [], "failed": []}
    seen_hashes: set[str] = set()

    for row in records:
        label = row.get("name") or row.get("family") or row.get("id") or "GIST font"
        url = str(row.get("direct_url") or "").strip()

        if row.get("authorized_automated_download") is not True:
            report["skipped"].append({"name": label, "reason": "authorized_automated_download is not true"})
            continue
        if row.get("redistribution_ok") is not True:
            report["skipped"].append({"name": label, "reason": "redistribution_ok is not true"})
            continue
        if not re.search(r"\.(ttf|otf)(?:\?|$)", url, flags=re.I):
            report["skipped"].append({"name": label, "reason": "not a direct .ttf/.otf URL"})
            continue

        try:
            data = fetch_direct_font(session, url)
            sha256 = hashlib.sha256(data).hexdigest()
            if sha256 in seen_hashes:
                report["skipped"].append({"name": label, "reason": "duplicate binary in this run", "sha256": sha256})
                continue

            meta = inspect_font(data, label)
            seen_hashes.add(sha256)
            ext = ".otf" if data[:4] == b"OTTO" else ".ttf"
            filename = safe_filename(meta["postscript_name"], ext)
            dest = output_dir / filename

            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != sha256:
                dest = output_dir / safe_filename(meta["postscript_name"] + "-" + sha256[:8], ext)

            dest.write_bytes(data)
            report["fetched"].append({
                "name": label,
                "url": url,
                "path": dest.relative_to(ROOT).as_posix(),
                "sha256": sha256,
                **meta,
            })
        except Exception as exc:
            report["failed"].append({"name": label, "url": url, "error": str(exc)})

        time.sleep(max(0.0, args.delay))

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Fetched {len(report['fetched'])}; skipped {len(report['skipped'])}; failed {len(report['failed'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
