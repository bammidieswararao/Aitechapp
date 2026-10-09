#!/usr/bin/env python3
from __future__ import annotations

"""
Download GIST-TLOT assets after a HUMAN completes each verification challenge.

Safety / site-respect note:
- This script DOES NOT OCR, solve, guess, or bypass CAPTCHA / verification codes.
- It preserves a requests.Session(), extracts the form and challenge image,
  saves the image locally, and asks the operator to type the displayed code.
- After a valid human-supplied code is submitted, the script can download the
  returned TTF/OTF, validate Telugu Unicode cmap coverage, and hand the files to
  the existing AiTechApp ingest/dedupe pipeline.

The downstream ingest pipeline remains responsible for redistribution/licensing
approval before publishing files under fonts/gist/ and fonts-catalog.json.
"""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
TELUGU_MIN, TELUGU_MAX = 0x0C00, 0x0C7F
MAX_FONT_BYTES = 25 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024

VERIFY_WORDS = ("captcha", "verify", "verification", "security", "code", "secret")
STYLE_ALIASES = {
    "normal": "regular",
    "regular": "regular",
    "bold": "bold",
    "italic": "italic",
    "bold italic": "bold italic",
    "bolditalic": "bold italic",
}


def canon(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def norm_style(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "Regular").strip().casefold())
    return STYLE_ALIASES.get(cleaned, cleaned)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-") or "font"


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        tag = tag.lower()

        if tag == "form":
            self._current = {
                "action": attrs.get("action", ""),
                "method": (attrs.get("method") or "post").lower(),
                "inputs": [],
                "images": [],
                "links": [],
            }
            self.forms.append(self._current)
            return

        current = self._current
        if current is None:
            return

        if tag == "input":
            current["inputs"].append(attrs)
        elif tag == "img":
            current["images"].append(attrs)
        elif tag == "a":
            current["links"].append(attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._current = None


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        href = attrs.get("href", "")
        if href:
            self.links.append(href)


@dataclass
class FormInfo:
    action_url: str
    method: str
    hidden_fields: dict[str, str] = field(default_factory=dict)
    code_field: str = ""
    image_url: str = ""


def font_header_kind(data: bytes) -> str | None:
    sig = data[:4]
    if sig == b"OTTO":
        return "OTF"
    if sig in (b"\x00\x01\x00\x00", b"true", b"typ1"):
        return "TTF"
    return None


def get_name(font: TTFont, name_id: int, fallback: str = "") -> str:
    if "name" not in font:
        return fallback

    records = [n for n in font["name"].names if n.nameID == name_id]
    records.sort(
        key=lambda n: (
            0 if n.platformID == 3 else 1,
            0 if getattr(n, "langID", 0) in (0, 0x409) else 1,
        )
    )

    for record in records:
        try:
            text = record.toUnicode().strip()
            if text:
                return text
        except Exception:
            pass
    return fallback


def inspect_font(data: bytes, fallback_family: str) -> dict[str, Any]:
    kind = font_header_kind(data)
    if not kind:
        raise ValueError("response is not a valid TTF/OTF sfnt font")

    with TTFont(io.BytesIO(data), lazy=False) as font:
        family = get_name(font, 16) or get_name(font, 1) or fallback_family
        style = get_name(font, 17) or get_name(font, 2) or "Regular"
        postscript_name = get_name(font, 6) or f"{family}-{style}"

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
        "postscript_name": postscript_name.strip(),
        "weight": weight,
        "type": kind,
        "telugu_codepoints": telugu_count,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def safe_filename(postscript_name: str, font_type: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", postscript_name).strip(".-") or "font"
    return stem + (".otf" if font_type == "OTF" else ".ttf")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_rows(path: Path) -> list[dict[str, Any]]:
    doc = load_json(path)
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for key in ("faces", "records", "fonts"):
            if isinstance(doc.get(key), list):
                return list(doc[key])
    raise ValueError(f"unsupported candidate manifest structure: {path}")


def discovery_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    doc = load_json(path)
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict) and isinstance(doc.get("records"), list):
        return list(doc["records"])
    return []


def page_id_from_url(url: str) -> str:
    try:
        return (parse_qs(urlparse(url).query).get("id") or [""])[0]
    except Exception:
        return ""


def resolve_page_url(candidate: dict[str, Any], discovery: list[dict[str, Any]]) -> str:
    direct = str(candidate.get("page_url") or candidate.get("source_url") or "").strip()
    if direct:
        return direct

    cid = candidate.get("page_id") or candidate.get("source_id") or candidate.get("download_id")
    if cid:
        return f"https://www.telugufont.com/download.php?id={cid}"

    family = canon(candidate.get("family") or candidate.get("name"))
    style = norm_style(candidate.get("style") or "Regular")

    for row in discovery:
        rfamily = canon(row.get("family") or row.get("name") or row.get("full_name"))
        rstyle = norm_style(row.get("style") or row.get("subfamily") or "Regular")
        if family and rfamily == family and rstyle == style:
            url = str(row.get("page_url") or "").strip()
            if url:
                return url

    return ""


def choose_form(page_url: str, html: str) -> FormInfo:
    parser = FormParser()
    parser.feed(html)

    if not parser.forms:
        raise ValueError("no HTML form found on page")

    def score(form: dict[str, Any]) -> int:
        text = " ".join(
            [
                str(form.get("action", "")),
                *[
                    " ".join(
                        [
                            inp.get("name", ""),
                            inp.get("id", ""),
                            inp.get("class", ""),
                            inp.get("placeholder", ""),
                            inp.get("type", ""),
                        ]
                    )
                    for inp in form.get("inputs", [])
                ],
                *[
                    " ".join([img.get("src", ""), img.get("id", ""), img.get("class", ""), img.get("alt", "")])
                    for img in form.get("images", [])
                ],
            ]
        ).casefold()
        return sum(10 for word in VERIFY_WORDS if word in text) + len(form.get("inputs", []))

    form = max(parser.forms, key=score)
    action_url = urljoin(page_url, form.get("action") or page_url)
    method = str(form.get("method") or "post").lower()

    hidden: dict[str, str] = {}
    code_field = ""

    for inp in form.get("inputs", []):
        name = inp.get("name", "").strip()
        if not name:
            continue
        typ = (inp.get("type") or "text").lower()
        if typ == "hidden":
            hidden[name] = inp.get("value", "")
            continue

        hay = " ".join(
            [
                name,
                inp.get("id", ""),
                inp.get("class", ""),
                inp.get("placeholder", ""),
                inp.get("aria-label", ""),
            ]
        ).casefold()

        if not code_field and typ in ("text", "tel", "number", "password"):
            if any(word in hay for word in VERIFY_WORDS):
                code_field = name

    # If the form has exactly one writable text-like input, use that.
    if not code_field:
        writable = []
        for inp in form.get("inputs", []):
            typ = (inp.get("type") or "text").lower()
            name = inp.get("name", "").strip()
            if name and typ in ("text", "tel", "number", "password"):
                writable.append(name)
        if len(writable) == 1:
            code_field = writable[0]

    image_url = ""
    image_candidates = []
    for img in form.get("images", []):
        src = img.get("src", "").strip()
        if not src:
            continue
        hay = " ".join(
            [
                src,
                img.get("id", ""),
                img.get("class", ""),
                img.get("alt", ""),
                img.get("title", ""),
            ]
        ).casefold()
        image_candidates.append((sum(10 for w in VERIFY_WORDS if w in hay), src))

    if image_candidates:
        _, best_src = max(image_candidates, key=lambda item: item[0])
        image_url = urljoin(page_url, best_src)

    if not code_field:
        raise ValueError("could not identify verification-code form field")
    if not image_url:
        raise ValueError("could not identify verification image URL")

    return FormInfo(
        action_url=action_url,
        method=method,
        hidden_fields=hidden,
        code_field=code_field,
        image_url=image_url,
    )


def read_limited(response: requests.Response, max_bytes: int) -> bytes:
    output = bytearray()
    for chunk in response.iter_content(128 * 1024):
        if not chunk:
            continue
        output.extend(chunk)
        if len(output) > max_bytes:
            raise ValueError("response exceeded configured size limit")
    return bytes(output)


def save_challenge_image(session: requests.Session, image_url: str, path: Path) -> None:
    response = session.get(image_url, timeout=(10, 25), stream=True)
    response.raise_for_status()
    data = read_limited(response, MAX_IMAGE_BYTES)
    if not data:
        raise ValueError("empty verification image")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def load_codes(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise ValueError("--codes-file must be a JSON object")
    return {str(k): str(v) for k, v in raw.items()}


def human_code(
    candidate: dict[str, Any],
    page_url: str,
    image_path: Path,
    codes: dict[str, str],
) -> str:
    keys = [
        str(candidate.get("id") or ""),
        page_id_from_url(page_url),
        str(candidate.get("family") or candidate.get("name") or ""),
    ]

    for key in keys:
        if key and key in codes:
            code = codes[key].strip()
            if re.fullmatch(r"\d{4}", code):
                return code
            raise ValueError(f"stored code for {key!r} is not exactly four digits")

    if not sys.stdin.isatty():
        raise RuntimeError(
            "verification requires human input; run interactively or supply a human-entered --codes-file"
        )

    print()
    print("Verification image saved to:")
    print(" ", image_path)
    print("Open that image and type the 4-digit number shown.")
    code = input("4-digit verification code: ").strip()
    if not re.fullmatch(r"\d{4}", code):
        raise ValueError("verification code must contain exactly four digits")
    return code


def extract_direct_font_link(base_url: str, html: str) -> str:
    parser = LinkParser()
    parser.feed(html)

    for href in parser.links:
        absolute = urljoin(base_url, href)
        if re.search(r"\.(ttf|otf)(?:\?|$)", absolute, flags=re.I):
            return absolute
    return ""


def submit_form(
    session: requests.Session,
    info: FormInfo,
    page_url: str,
    code: str,
) -> tuple[bytes, str]:
    payload = dict(info.hidden_fields)
    payload[info.code_field] = code

    # Preserve page id when the form expects it but omitted it from hidden inputs.
    page_id = page_id_from_url(page_url)
    if page_id and "id" not in payload:
        payload["id"] = page_id

    if info.method == "get":
        response = session.get(info.action_url, params=payload, timeout=(12, 45), stream=True)
    else:
        response = session.post(info.action_url, data=payload, timeout=(12, 45), stream=True)

    response.raise_for_status()
    content_type = (response.headers.get("content-type") or "").casefold()

    if "text/html" not in content_type:
        data = read_limited(response, MAX_FONT_BYTES)
        if font_header_kind(data):
            return data, response.url

    # HTML response may contain the authorized font link after successful verification.
    if "text/html" in content_type:
        html = response.text
        font_url = extract_direct_font_link(response.url, html)
        if font_url:
            font_response = session.get(font_url, timeout=(12, 45), stream=True)
            font_response.raise_for_status()
            data = read_limited(font_response, MAX_FONT_BYTES)
            if font_header_kind(data):
                return data, font_response.url

    raise ValueError("verification submission did not return or expose a TTF/OTF binary")


def run_checked(args: list[str]) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Human-assisted GIST form downloader with strict Telugu font validation."
    )
    parser.add_argument("--candidates", default="font-ingest/gist-candidates.json")
    parser.add_argument("--discovery", default="font-ingest/gist-discovery.json")
    parser.add_argument("--codes-file", default="")
    parser.add_argument("--challenge-dir", default="font-ingest/challenges")
    parser.add_argument("--incoming-dir", default="fonts/incoming/gist")
    parser.add_argument("--report", default="font-ingest/gist-manual-download-report.json")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "bammidieswararao/Aitechapp"))
    parser.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", "main"))
    parser.add_argument("--sleep", type=float, default=1.5)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--skip-integrate", action="store_true")
    parser.add_argument("--fetch-existing-hashes", action="store_true")
    args = parser.parse_args()

    candidates_path = ROOT / args.candidates
    discovery_path = ROOT / args.discovery
    challenge_dir = ROOT / args.challenge_dir
    incoming_dir = ROOT / args.incoming_dir
    report_path = ROOT / args.report
    codes_path = (ROOT / args.codes_file) if args.codes_file else None

    candidates = candidate_rows(candidates_path)
    discovery = discovery_rows(discovery_path)
    codes = load_codes(codes_path)

    challenge_dir.mkdir(parents=True, exist_ok=True)
    incoming_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Linux; Android 15) AiTechApp-GIST-HumanVerification/1",
            "Accept-Language": "en-IN,en;q=0.9",
        }
    )

    report: dict[str, Any] = {
        "policy": "Verification codes are supplied by a human. No OCR/guessing/bypass is performed.",
        "downloaded": [],
        "skipped": [],
        "failed": [],
    }

    for index, candidate in enumerate(candidates, start=1):
        label = str(candidate.get("family") or candidate.get("name") or candidate.get("id") or f"candidate-{index}")
        style = str(candidate.get("style") or "Regular")
        page_url = resolve_page_url(candidate, discovery)

        if not page_url:
            report["skipped"].append(
                {
                    "candidate": candidate.get("id"),
                    "name": label,
                    "style": style,
                    "reason": "no page_url/page_id and no matching discovery record",
                }
            )
            continue

        success = False
        last_error = ""

        for attempt in range(1, max(1, args.max_retries) + 1):
            try:
                page = session.get(page_url, timeout=(12, 35))
                page.raise_for_status()

                info = choose_form(page.url, page.text)

                page_id = page_id_from_url(page.url) or slug(str(candidate.get("id") or label))
                image_path = challenge_dir / f"{page_id}-{slug(label)}-{slug(style)}-try{attempt}.png"
                save_challenge_image(session, info.image_url, image_path)

                code = human_code(candidate, page.url, image_path, codes)
                data, final_url = submit_form(session, info, page.url, code)
                meta = inspect_font(data, label)

                filename = safe_filename(meta["postscript_name"], meta["type"])
                dest = incoming_dir / filename

                if dest.exists():
                    existing_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
                    if existing_hash != meta["sha256"]:
                        suffix = meta["sha256"][:8]
                        filename = safe_filename(meta["postscript_name"] + "-" + suffix, meta["type"])
                        dest = incoming_dir / filename

                dest.write_bytes(data)

                report["downloaded"].append(
                    {
                        "candidate": candidate.get("id"),
                        "page_url": page.url,
                        "final_url": final_url,
                        "path": dest.relative_to(ROOT).as_posix(),
                        **meta,
                    }
                )

                print(f"[{index}/{len(candidates)}] downloaded {label} {style} -> {dest.relative_to(ROOT)}")
                success = True
                break

            except Exception as exc:
                last_error = str(exc)
                print(f"[{index}/{len(candidates)}] attempt {attempt} failed for {label} {style}: {exc}")
                if attempt < args.max_retries:
                    time.sleep(max(args.sleep, 0.0))

        if not success:
            report["failed"].append(
                {
                    "candidate": candidate.get("id"),
                    "name": label,
                    "style": style,
                    "page_url": page_url,
                    "error": last_error,
                }
            )

        time.sleep(max(args.sleep, 0.0))

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print()
    print(
        f"Downloaded {len(report['downloaded'])}; "
        f"skipped {len(report['skipped'])}; "
        f"failed {len(report['failed'])}"
    )

    if args.skip_integrate:
        return 0

    ingest_cmd = [
        sys.executable,
        "scripts/ingest_external_fonts.py",
        "--repo",
        args.repo,
        "--branch",
        args.branch,
    ]
    if args.fetch_existing_hashes:
        ingest_cmd.append("--fetch-existing-hashes")

    run_checked(ingest_cmd)
    run_checked([sys.executable, "scripts/sync_client_fallback.py"])
    run_checked([sys.executable, "scripts/check_telugu_fonts_ui.py"])

    print("Catalog integration completed. Review git diff before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
