#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "telugu-fonts.html").read_text(encoding="utf-8")

required = {
    "Preview FREE badge": ["badge-1", "fa-magic", "Preview", "FREE"],
    "PLP Files FREE badge": ["badge-2", "fa-folder-open", "PLP Files", "FREE"],
    "Center AI Hub": ["btn-fab", "fab-circle", "fa-plus", "AI Hub"],
    "Active Telugu Fonts": ["btn-action active", "badge-3", "fa-font", "Telugu Fonts"],
    "Anu Chat FREE badge": ["badge-4", "fa-comment-dots", "Anu Chat", "FREE"],
}
missing = []
for label, markers in required.items():
    absent = [m for m in markers if m not in html]
    if absent:
        missing.append((label, absent))
if missing:
    raise SystemExit("Bottom-navigation UI contract failed: " + repr(missing))
print("Bottom-navigation UI contract OK")
