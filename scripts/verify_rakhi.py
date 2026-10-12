#!/usr/bin/env python3
"""Verify and optionally stamp Rakhi Ornamental TTF files; keep source OFL notices."""
import argparse
import hashlib
import re
from pathlib import Path
from fontTools.ttLib import TTFont
import uharfbuzz as hb

SAMPLES=("తెలుగు","శ్రీరామ","క్షేత్రం","ప్రకాశం","క్రాంతి")
OFL_URL="https://openfontlicense.org"

def name(font, ident):
    table=font["name"]
    value=table.getDebugName(ident)
    return str(value or "").strip()

def stamp(font, family, subfamily):
    # Never overwrite upstream copyright text. The user's modifications are appended.
    names=font["name"]
    copyright=name(font,0)
    if not copyright:
        raise ValueError("Original source copyright missing; refuse attribution changes")
    credit="Modifications copyright (c) 2026 Bammidi Eswara Rao / AiTechApps"
    if credit not in copyright:
        copyright=copyright+"; "+credit
    for n,value in (
        (0,copyright),(1,family),(2,subfamily),(3,family+" "+subfamily+" 2026 AiTechApps"),
        (4,family+" "+subfamily),(6,re.sub(r"[^A-Za-z0-9-]","",family.replace(" ","")+"-"+subfamily.replace(" ",""))),
        (9,"Bammidi Eswara Rao / AiTechApps"),(13,"Licensed under the SIL Open Font License, Version 1.1."),
        (14,OFL_URL),(16,family),(17,subfamily)):
        for platform,encoding,lang in ((3,1,0x409),(1,0,0)):
            names.setName(value,n,platform,encoding,lang)

def verify(path,expected_style=None):
    data=path.read_bytes()
    if not data.startswith((bytes.fromhex("00010000"),b"true")):
        raise ValueError("Not a TrueType TTF: "+str(path))
    font=TTFont(path,lazy=False,recalcBBoxes=False)
    try:
        for table in ("head","hhea","hmtx","maxp","cmap","name","OS/2","GSUB","GPOS"):
            if table not in font:raise ValueError(f"{path}: missing {table}")
        if not any(t.isUnicode() and any(0x0C00<=cp<=0x0C7f for cp in t.cmap) for t in font["cmap"].tables):
            raise ValueError(str(path)+": no Telugu Unicode coverage")
        for kind in ("GSUB","GPOS"):
            obj=font[kind].table
            lookups=obj.LookupList.Lookup if obj.LookupList is not None else []
            if not lookups:raise ValueError(str(path)+": "+kind+" has no lookups")
            for lookup in lookups:
                if not lookup.SubTable:raise ValueError(str(path)+": empty "+kind+" lookup")
            font[kind].compile(font) # table graph, indices, glyph references checked by compiler
        if not name(font,0):raise ValueError("Missing original copyright metadata")
        if not name(font,13) or "OFL" not in name(font,13):
            raise ValueError("Missing OFL license metadata")
        if expected_style and name(font,2).lower().replace(" ","")!=expected_style.lower().replace(" ",""):
            raise ValueError(str(path)+": family/style mismatch: "+name(font,2))
    finally:
        font.close()
    # Shape Indic clusters through HarfBuzz, not only by checking cmap labels.
    face=hb.Face(data);f=hb.Font(face);f.scale=(face.upem,face.upem)
    for sample in SAMPLES:
        buf=hb.Buffer();buf.add_str(sample)
        buf.direction="ltr";buf.script="Telu";buf.language="te"
        hb.shape(f,buf)
        if not buf.glyph_infos or any(g.codepoint==0 for g in buf.glyph_infos):
            raise ValueError(f"{path}: invalid HarfBuzz shaping for {sample!r}")
    return hashlib.sha256(data).hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("files",nargs="+",type=Path)
    p.add_argument("--stamp",action="store_true")
    p.add_argument("--family",default="Rakhi Ornamental")
    p.add_argument("--style",default=None)
    a=p.parse_args()
    for path in a.files:
        if a.stamp:
            with TTFont(path) as ft:
                stamp(ft,a.family,a.style or name(ft,2) or "Regular")
                ft.save(path)
        digest=verify(path,expected_style=a.style)
        print(path.name,digest,"GSUB/GPOS + Telugu shaping OK")

if __name__=="__main__":main()
