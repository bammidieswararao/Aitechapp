#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unicodedata
import urllib.parse
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
TELUGU_MIN, TELUGU_MAX = 0x0C00, 0x0C7F
MAX_DOWNLOAD = 25 * 1024 * 1024
MAX_ZIP_MEMBER = 20 * 1024 * 1024
MAX_ZIP_TOTAL = 200 * 1024 * 1024

PERMISSIVE_MARKERS = (
    "sil open font license",
    "open font license",
    "ofl-1.1",
    "ofl 1.1",
    "apache license",
    "apache-2.0",
    "mit license",
)

def slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower() or "font"

def canon(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

def face_key(name: str, style: str, weight: int, width: float = 100) -> str:
    return f"{canon(name)}|{canon(style or 'Regular')}|{int(weight or 400)}|{float(width or 100):g}"

def raw_url(repo: str, branch: str, rel: Path) -> str:
    path = "/".join(urllib.parse.quote(part, safe="") for part in rel.as_posix().split("/"))
    return f"https://raw.githubusercontent.com/{repo}/{urllib.parse.quote(branch, safe='')}/{path}"

def name_value(font: TTFont, name_id: int, fallback: str = "") -> str:
    if "name" not in font:
        return fallback
    records = [n for n in font["name"].names if n.nameID == name_id]
    records.sort(key=lambda n: (
        0 if n.platformID == 3 else 1,
        0 if getattr(n, "langID", 0) in (0, 0x409) else 1,
    ))
    for rec in records:
        try:
            text = rec.toUnicode().strip()
            if text:
                return text
        except Exception:
            pass
    return fallback

def inspect_font(data: bytes, fallback_family: str = "Unknown") -> dict[str, Any]:
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
        raise ValueError("not a valid sfnt TTF/OTF header")

    with TTFont(io.BytesIO(data), lazy=False) as font:
        family = name_value(font, 16) or name_value(font, 1) or fallback_family
        style = name_value(font, 17) or name_value(font, 2) or "Regular"
        psname = name_value(font, 6) or f"{family}-{style}"
        copyright_text = name_value(font, 0)
        license_text = name_value(font, 13)
        license_url = name_value(font, 14)

        weight = 400
        width_class = 5
        if "OS/2" in font:
            weight = int(getattr(font["OS/2"], "usWeightClass", 400) or 400)
            width_class = int(getattr(font["OS/2"], "usWidthClass", 5) or 5)

        codepoints: set[int] = set()
        if "cmap" in font:
            for table in font["cmap"].tables:
                if table.isUnicode():
                    codepoints.update(table.cmap.keys())
        telugu_count = sum(1 for cp in codepoints if TELUGU_MIN <= cp <= TELUGU_MAX)
        if telugu_count == 0:
            raise ValueError("font has no Unicode Telugu cmap entries")

        variable = "fvar" in font
        axes: dict[str, Any] = {}
        if variable:
            for axis in font["fvar"].axes:
                axes[axis.axisTag] = {
                    "min": float(axis.minValue),
                    "default": float(axis.defaultValue),
                    "max": float(axis.maxValue),
                }

    # CSS width axis uses percentages. This maps the OS/2 width class to a practical default.
    width_map = {1:50, 2:62.5, 3:75, 4:87.5, 5:100, 6:112.5, 7:125, 8:150, 9:200}
    return {
        "family": family.strip(),
        "style": style.strip() or "Regular",
        "postscript_name": psname.strip(),
        "weight": weight,
        "width": width_map.get(width_class, 100),
        "copyright": copyright_text.strip(),
        "license_text": license_text.strip(),
        "license_url": license_url.strip(),
        "variable": variable,
        "axes": axes,
        "telugu_codepoints": telugu_count,
    }

def permissive_from_metadata(meta: dict[str, Any]) -> bool:
    hay = " ".join([
        meta.get("license_text", ""),
        meta.get("license_url", ""),
        meta.get("copyright", ""),
    ]).casefold()
    return any(marker in hay for marker in PERMISSIVE_MARKERS)

def load_allowlist(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    return list(doc.get("entries", []))

def allowlisted(meta: dict[str, Any], sha256: str, entries: list[dict[str, Any]]) -> tuple[bool, dict[str, Any] | None]:
    for item in entries:
        if item.get("redistribution_ok") is not True:
            continue
        if item.get("sha256") and item["sha256"].lower() == sha256.lower():
            return True, item
        if item.get("postscript_name") and canon(item["postscript_name"]) == canon(meta["postscript_name"]):
            return True, item
    return False, None

def iter_incoming(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".ttf", ".otf", ".zip"):
            yield path

def safe_zip_fonts(path: Path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        total = sum(max(0, m.file_size) for m in members)
        if total > MAX_ZIP_TOTAL:
            raise ValueError(f"ZIP expands beyond {MAX_ZIP_TOTAL} bytes")
        for member in members:
            if member.is_dir():
                continue
            member_path = Path(member.filename.replace("\\", "/"))
            if member.filename.startswith("/") or ".." in member_path.parts:
                continue
            ext = member_path.suffix.lower()
            if ext not in (".ttf", ".otf"):
                continue
            if member.file_size <= 0 or member.file_size > MAX_ZIP_MEMBER:
                continue
            yield f"{path.as_posix()}::{member.filename}", ext, archive.read(member)

def candidate_stream(incoming_root: Path):
    for path in iter_incoming(incoming_root):
        if path.suffix.lower() == ".zip":
            yield from safe_zip_fonts(path)
        else:
            yield path.as_posix(), path.suffix.lower(), path.read_bytes()

def fetch_bytes(session: requests.Session, url: str) -> bytes:
    response = session.get(url, timeout=(12, 45), stream=True, headers={"User-Agent": "AiTechApp-FontAudit/3"})
    response.raise_for_status()
    out = bytearray()
    for chunk in response.iter_content(256 * 1024):
        if not chunk:
            continue
        out.extend(chunk)
        if len(out) > MAX_DOWNLOAD:
            raise ValueError(f"remote font exceeds {MAX_DOWNLOAD} bytes")
    return bytes(out)

def existing_fingerprints(fonts: list[dict[str, Any]], fetch_remote: bool) -> tuple[set[str], set[str], set[str], list[dict[str, Any]]]:
    face_keys = {
        face_key(f.get("name", ""), f.get("style", "Regular"), int(f.get("weight", 400) or 400), float(f.get("width", 100) or 100))
        for f in fonts
    }
    hashes = {str(f.get("sha256", "")).lower() for f in fonts if f.get("sha256")}
    postscript = {canon(f.get("postscript_name")) for f in fonts if f.get("postscript_name")}
    report: list[dict[str, Any]] = []

    if not fetch_remote:
        return face_keys, hashes, postscript, report

    session = requests.Session()
    cache: dict[str, bytes] = {}
    for font in fonts:
        url = str(font.get("font_raw_url") or "")
        if not url or url in cache:
            continue
        try:
            data = fetch_bytes(session, url)
            cache[url] = data
            sha = hashlib.sha256(data).hexdigest()
            hashes.add(sha)
            meta = inspect_font(data, font.get("name", "Existing"))
            # Do not use PS-name dedupe for variable fonts because one variable binary backs many virtual faces.
            if not meta.get("variable"):
                postscript.add(canon(meta.get("postscript_name")))
            report.append({"url": url, "sha256": sha, "postscript_name": meta.get("postscript_name"), "variable": meta.get("variable")})
        except Exception as exc:
            report.append({"url": url, "status": "fingerprint-failed", "error": str(exc)})
    return face_keys, hashes, postscript, report

def output_folder(source_label: str, meta: dict[str, Any]) -> str:
    text = f"{source_label} {meta.get('family','')}".casefold()
    return "gist" if "gist-tlot" in text or "/gist/" in text or "\\gist\\" in text else "unicode"

def clean_filename(meta: dict[str, Any], ext: str) -> str:
    base = meta.get("postscript_name") or f"{meta.get('family','Font')}-{meta.get('style','Regular')}"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-") or "font"
    return base + ext.lower()

def main() -> int:
    parser = argparse.ArgumentParser(description="Strict Telugu font ingest with binary/metadata dedupe.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "bammidieswararao/Aitechapp"))
    parser.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", "main"))
    parser.add_argument("--catalog", default="fonts-catalog.json")
    parser.add_argument("--incoming", default="fonts/incoming")
    parser.add_argument("--allowlist", default="font-ingest/rehost-allowlist.json")
    parser.add_argument("--report", default="font-ingest/ingest-report.json")
    parser.add_argument("--fetch-existing-hashes", action="store_true")
    args = parser.parse_args()

    catalog_path = ROOT / args.catalog
    incoming_root = ROOT / args.incoming
    allowlist_path = ROOT / args.allowlist
    report_path = ROOT / args.report

    doc = json.loads(catalog_path.read_text(encoding="utf-8"))
    fonts = list(doc["fonts"] if isinstance(doc, dict) else doc)
    allow_entries = load_allowlist(allowlist_path)
    existing_faces, existing_hashes, existing_ps, remote_report = existing_fingerprints(fonts, args.fetch_existing_hashes)

    candidates: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "starting_catalog_count": len(fonts),
        "remote_fingerprints": remote_report,
        "scanned": [],
        "rejected": [],
        "imported": [],
    }

    if incoming_root.exists():
        for source_label, ext, data in candidate_stream(incoming_root):
            sha = hashlib.sha256(data).hexdigest()
            try:
                meta = inspect_font(data, Path(source_label.split("::", 1)[0]).stem)
            except Exception as exc:
                report["rejected"].append({"source": source_label, "reason": "invalid-or-non-telugu-font", "error": str(exc)})
                continue

            candidate = {
                "source": source_label,
                "ext": ext,
                "data": data,
                "sha256": sha,
                **meta,
            }
            report["scanned"].append({
                "source": source_label,
                "sha256": sha,
                "family": meta["family"],
                "style": meta["style"],
                "weight": meta["weight"],
                "postscript_name": meta["postscript_name"],
                "license_text": meta["license_text"],
            })
            candidates.append(candidate)

    # Prefer TTF over OTF for the same logical face. If format ties, prefer the smaller binary.
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        grouped[face_key(item["family"], item["style"], item["weight"], item["width"])].append(item)

    selected: list[dict[str, Any]] = []
    for key, group in grouped.items():
        group.sort(key=lambda x: (0 if x["ext"] == ".ttf" else 1, len(x["data"]), x["source"].casefold()))
        winner = group[0]
        selected.append(winner)
        for dropped in group[1:]:
            report["rejected"].append({
                "source": dropped["source"],
                "reason": "duplicate-logical-face-ttf-preferred",
                "selected": winner["source"],
                "face_key": key,
            })

    incoming_hash_seen: set[str] = set()
    for item in selected:
        fkey = face_key(item["family"], item["style"], item["weight"], item["width"])
        pskey = canon(item["postscript_name"])

        if item["sha256"] in incoming_hash_seen:
            report["rejected"].append({"source": item["source"], "reason": "duplicate-incoming-binary", "sha256": item["sha256"]})
            continue
        incoming_hash_seen.add(item["sha256"])

        if item["sha256"] in existing_hashes:
            report["rejected"].append({"source": item["source"], "reason": "binary-already-in-catalog", "sha256": item["sha256"]})
            continue
        if fkey in existing_faces:
            report["rejected"].append({"source": item["source"], "reason": "logical-face-already-in-catalog", "face_key": fkey})
            continue
        if not item.get("variable") and pskey and pskey in existing_ps:
            report["rejected"].append({"source": item["source"], "reason": "postscript-name-already-in-catalog", "postscript_name": item["postscript_name"]})
            continue

        metadata_permits = permissive_from_metadata(item)
        listed, allow = allowlisted(item, item["sha256"], allow_entries)
        if not (metadata_permits or listed):
            report["rejected"].append({
                "source": item["source"],
                "reason": "redistribution-permission-not-established",
                "sha256": item["sha256"],
                "postscript_name": item["postscript_name"],
                "license_text": item.get("license_text", ""),
                "license_url": item.get("license_url", ""),
            })
            continue

        folder = output_folder(item["source"], item)
        dest_dir = ROOT / "fonts" / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = clean_filename(item, item["ext"])
        dest = dest_dir / filename

        # Never silently overwrite a different binary.
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != item["sha256"]:
            filename = f"{dest.stem}-{item['sha256'][:8]}{dest.suffix}"
            dest = dest_dir / filename
        dest.write_bytes(item["data"])

        license_label = (
            (allow or {}).get("license")
            or item.get("license_text")
            or "Permissive redistribution confirmed"
        )
        family = item["family"]
        style = item["style"]
        entry = {
            "id": slug(f"{family}-{style}-{item['weight']}-{item['postscript_name']}"),
            "name": family,
            "style": style,
            "weight": int(item["weight"]),
            "width": item["width"],
            "group": "third-party",
            "category": "GIST / Unicode",
            "categories": ["GIST / Unicode", "Third-party"],
            "author": (allow or {}).get("author", "External Telugu font source"),
            "license": license_label,
            "font_raw_url": raw_url(args.repo, args.branch, dest.relative_to(ROOT)),
            "download_type": item["ext"][1:].upper(),
            "preview_ready": True,
            "variable": bool(item.get("variable")),
            "axes": item.get("axes", {}),
            "file_name": filename,
            "sha256": item["sha256"],
            "postscript_name": item["postscript_name"],
            "source": item["source"],
        }
        fonts.append(entry)
        existing_faces.add(fkey)
        existing_hashes.add(item["sha256"])
        if not item.get("variable"):
            existing_ps.add(pskey)
        report["imported"].append(entry)

    # Final strict logical-face dedupe, preserving existing catalog rows before new rows.
    final: list[dict[str, Any]] = []
    seen_faces: set[str] = set()
    for font in fonts:
        key = face_key(font.get("name", ""), font.get("style", "Regular"), int(font.get("weight", 400) or 400), float(font.get("width", 100) or 100))
        if key in seen_faces:
            continue
        seen_faces.add(key)
        final.append(font)

    for index, font in enumerate(final, 1):
        font["serial"] = index

    if isinstance(doc, dict):
        doc["schema_version"] = max(int(doc.get("schema_version", 1) or 1), 3)
        doc["total"] = len(final)
        doc["fonts"] = final
        doc["ingest_rule"] = "TTF preferred; OTF fallback; SHA-256 + family/style/weight/width + PostScript dedupe"
        catalog_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        catalog_path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report["ending_catalog_count"] = len(final)
    report["imported_count"] = len(report["imported"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Catalog: {report['starting_catalog_count']} -> {report['ending_catalog_count']}; imported {report['imported_count']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
