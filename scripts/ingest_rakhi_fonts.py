#!/usr/bin/env python3
"""Extract verified 20-face Rakhi ZIP and switch catalog slots to live URLs."""
import json
import re
import shutil
import zipfile
from pathlib import Path
from fontTools.ttLib import TTFont
from verify_rakhi import stamp,verify
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"fonts/rakhi"
INCOMING=BASE/"incoming"
CATALOG=ROOT/"fonts-catalog.json"

def norm(text):
    return re.sub(r"[^a-z0-9]","",text.casefold())

def main():
    doc=json.loads(CATALOG.read_text("utf-8"))
    expected=[f for f in doc["fonts"] if f.get("group")=="rakhi"]
    if len(expected)!=20:raise SystemExit("Need exactly 20 planned Rakhi slots")
    by_style={norm(f["style"]):f for f in expected}
    zips=sorted(INCOMING.glob("*.zip"))
    if len(zips)>1:raise SystemExit("Upload only one Rakhi ZIP at a time")
    if zips:
        archive=zips[0]
        with zipfile.ZipFile(archive) as z:
            license_files=[n for n in z.namelist() if Path(n).name.lower() in ("ofl.txt","license.ofl","open_font_license.txt")]
            if len(license_files)!=1:
                raise SystemExit("Rakhi ZIP must include its original Mandali OFL.txt")
            license_bytes=z.read(license_files[0])
            lic=license_bytes.decode("utf-8-sig",errors="replace")
            if "SIL OPEN FONT LICENSE Version 1.1" not in lic or "Copyright" not in lic:
                raise SystemExit("Original OFL 1.1 license and copyright are required")
            items=[n for n in z.namelist() if n.lower().endswith(".ttf") and not n.endswith("/")]
            if len(items)!=20:raise SystemExit("Expected exactly 20 TTF binaries in ZIP, found "+str(len(items)))
            if any(n.startswith("/") or ".." in Path(n).parts for n in items):
                raise SystemExit("Unsafe ZIP paths")
            styles={}
            for name in items:
                raw=z.read(name)
                if len(raw)>20*1024*1024:raise SystemExit("Suspicious TTF size")
                # Write to staging; never publish an unverified file.
                scratch=BASE/(".checking-"+str(len(styles))+".ttf")
                scratch.write_bytes(raw)
                try:
                    with TTFont(scratch) as ft:
                        sub=ft["name"].getDebugName(17) or ft["name"].getDebugName(2) or ""
                        # Named font styles are authoritative: never pretend that 20 duplicates are 20 styles.
                        matched=by_style.get(norm(sub))
                        if not matched:raise SystemExit("Unknown style in "+name+": "+sub)
                        if matched["id"] in styles:raise SystemExit("Duplicate style: "+sub)
                        stamp(ft,"Rakhi Ornamental",matched["style"])
                        ft.save(scratch)
                    verify(scratch,matched["style"])
                    styles[matched["id"]]=scratch.read_bytes()
                finally:
                    scratch.unlink(missing_ok=True)
            if len(styles)!=20:raise SystemExit("Expected 20 distinct styles")
        # Validate everything before making the license and binaries visible.
        for f in expected:
            (ROOT/f["target_path"]).write_bytes(styles[f["id"]])
        (BASE/"OFL.txt").write_bytes(license_bytes)
    # Direct TTF uploads must supply the actual upstream OFL license separately.
    # Support canonical direct TTF uploads, but activate only after all 20 pass.
    files=[ROOT/f["target_path"] for f in expected]
    if not all(p.is_file() for p in files):
        print("Rakhi scaffold ready; awaiting 20 validated TTF files")
        return
    license_path=BASE/"OFL.txt"
    if not license_path.is_file():
        raise SystemExit("Original Mandali OFL.txt is required with the 20 TTF files")
    source_license=license_path.read_text(encoding="utf-8-sig")
    if "SIL OPEN FONT LICENSE Version 1.1" not in source_license or "Copyright" not in source_license:
        raise SystemExit("Missing valid upstream OFL license copyright")
    for f,path in zip(expected,files):
        verify(path,expected_style=f["style"])
    digests=[__import__("hashlib").sha256(p.read_bytes()).hexdigest() for p in files]
    if len(set(digests))!=20:
        raise SystemExit("20 distinct styles must not reuse identical binaries")
    for f in expected:
        f["pending_until_available"]=False
        f["preview_ready"]=True
        f["validated_after_upload"]=True
        f["license"]="SIL OFL 1.1 — Mandali derivative"
    doc["pending_rakhi_activation_count"]=0
    doc["active_total"]=len(doc["fonts"])
    doc["rakhi_plan"]["status"]="active-after-fonttools-harfbuzz-validation"
    doc["rakhi_plan"]["actual_binaries"]=20
    CATALOG.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Rakhi 20/20 activated in fonts-catalog.json")

if __name__=="__main__":main()
