#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, io, json, os, re, shutil, unicodedata, urllib.parse, zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
import requests
from fontTools.ttLib import TTFont

ROOT=Path(__file__).resolve().parents[1]
SOURCES=ROOT/"sources.json"
SEED=ROOT/"fonts-catalog.seed.json"
OUT=ROOT/"fonts-catalog.json"
REPORT=ROOT/"font-import-report.json"
FONT_DIR=ROOT/"fonts"/"third-party"
TELUGU_MIN,TELUGU_MAX=0x0C00,0x0C7F
MAX_ARCHIVE=50*1024*1024
MAX_MEMBER=20*1024*1024
MAX_TOTAL=150*1024*1024

def slug(v:str)->str:
    v=unicodedata.normalize("NFKD",v or "").encode("ascii","ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+","-",v).strip("-").lower() or "font"

def canonical(v:Any)->str:
    return re.sub(r"[^a-z0-9]+","",str(v or "").casefold())

def face_key(name:str,style:str,weight:int)->str:
    return f"{canonical(name)}|{canonical(style or 'Regular')}|{int(weight or 400)}"

def raw_url(repo:str,branch:str,rel:Path)->str:
    p="/".join(urllib.parse.quote(x,safe="") for x in rel.as_posix().split("/"))
    return f"https://raw.githubusercontent.com/{repo}/{urllib.parse.quote(branch,safe='')}/{p}"

def name_record(font:TTFont,name_id:int,fallback:str="")->str:
    records=[n for n in font["name"].names if n.nameID==name_id]
    records.sort(key=lambda n:(0 if n.platformID==3 else 1,0 if getattr(n,"langID",0) in (0,0x409) else 1))
    for r in records:
        try:
            t=r.toUnicode().strip()
            if t:return t
        except Exception: pass
    return fallback

def inspect_font(data:bytes,fallback_family:str)->dict[str,Any]:
    if data[:4] not in (b"\x00\x01\x00\x00",b"OTTO",b"true",b"typ1"):
        raise ValueError("not a TTF/OTF binary")
    with TTFont(io.BytesIO(data),lazy=False) as f:
        family=name_record(f,16) or name_record(f,1) or fallback_family
        style=name_record(f,17) or name_record(f,2) or "Regular"
        weight=int(getattr(f.get("OS/2"),"usWeightClass",400) or 400) if "OS/2" in f else 400
        cps=set()
        if "cmap" in f:
            for t in f["cmap"].tables:
                if t.isUnicode(): cps.update(t.cmap.keys())
        if not any(TELUGU_MIN<=cp<=TELUGU_MAX for cp in cps):
            raise ValueError("font has no Telugu Unicode cmap")
        ps=name_record(f,6) or f"{family}-{style}"
        variable="fvar" in f
        axes={}
        if variable:
            for a in f["fvar"].axes:
                axes[a.axisTag]={"min":float(a.minValue),"default":float(a.defaultValue),"max":float(a.maxValue)}
    return {"family":family.strip(),"style":style.strip() or "Regular","weight":weight,
            "postscript":ps.strip(),"variable":variable,"axes":axes}

def validate_row(e:dict[str,Any])->None:
    for k in ("id","name","font_raw_url","download_type","preview_ready"):
        if k not in e: raise ValueError(f"missing {k}: {e}")
    typ=str(e["download_type"]).upper()
    if typ not in ("TTF","OTF"): raise ValueError(f"bad type {typ}")
    if e["preview_ready"] is not True: raise ValueError("preview_ready must be true")
    u=urllib.parse.unquote(str(e["font_raw_url"]))
    if "/blob/" in u or u.lower().endswith(".zip") or not re.search(r"\.(ttf|otf)(?:\?|$)",u,re.I):
        raise ValueError(f"not a direct font URL: {u}")

def style_for(w:int)->str:
    return {100:"Thin",200:"ExtraLight",300:"Light",400:"Regular",500:"Medium",600:"SemiBold",700:"Bold",800:"ExtraBold",900:"Black"}[w]

def builtin_seed()->dict[str,Any]:
    fonts=[]
    classics=[
      ("ponnala","Ponnala","google/fonts/main/ofl/ponnala/Ponnala-Regular.ttf","Google Fonts","SIL OFL"),
      ("sree-krushnadevaraya","Sree Krushnadevaraya","google/fonts/main/ofl/sreekrushnadevaraya/SreeKrushnadevaraya-Regular.ttf","Google Fonts","SIL OFL"),
      ("suravaram","Suravaram","google/fonts/main/ofl/suravaram/Suravaram-Regular.ttf","Google Fonts","SIL OFL"),
      ("timmana","Timmana","google/fonts/main/ofl/timmana/Timmana-Regular.ttf","Google Fonts","SIL OFL"),
      ("ravi-prakash","Ravi Prakash","google/fonts/main/ofl/raviprakash/RaviPrakash-Regular.ttf","Google Fonts","SIL OFL"),
      ("dhurjati","Dhurjati","google/fonts/main/ofl/dhurjati/Dhurjati-Regular.ttf","Google Fonts","SIL OFL"),
      ("gidugu","Gidugu","google/fonts/main/ofl/gidugu/Gidugu-Regular.ttf","Google Fonts","SIL OFL"),
      ("gurajada","Gurajada","google/fonts/main/ofl/gurajada/Gurajada-Regular.ttf","Google Fonts","SIL OFL"),
      ("lakki-reddy","Lakki Reddy","google/fonts/main/ofl/lakkireddy/LakkiReddy-Regular.ttf","Google Fonts","SIL OFL"),
      ("mallanna","Mallanna","google/fonts/main/ofl/mallanna/Mallanna-Regular.ttf","Google Fonts","SIL OFL"),
      ("mandali","Mandali","google/fonts/main/ofl/mandali/Mandali-Regular.ttf","Google Fonts","SIL OFL"),
      ("ntr","NTR","google/fonts/main/ofl/ntr/NTR-Regular.ttf","Google Fonts","SIL OFL"),
      ("peddana","Peddana","google/fonts/main/ofl/peddana/Peddana-Regular.ttf","Google Fonts","SIL OFL"),
      ("ramabhadra","Ramabhadra","google/fonts/main/ofl/ramabhadra/Ramabhadra-Regular.ttf","Google Fonts","SIL OFL"),
      ("ramaraja","Ramaraja","google/fonts/main/ofl/ramaraja/Ramaraja-Regular.ttf","Google Fonts","SIL OFL"),
      ("suranna","Suranna","google/fonts/main/ofl/suranna/Suranna-Regular.ttf","Google Fonts","SIL OFL"),
      ("tenali-ramakrishna","Tenali Ramakrishna","google/fonts/main/ofl/tenaliramakrishna/TenaliRamakrishna-Regular.ttf","Google Fonts","SIL OFL"),
      ("tiro-telugu","Tiro Telugu","google/fonts/main/ofl/tirotelugu/TiroTelugu-Regular.ttf","Google Fonts","SIL OFL"),
      ("vemana2000","Vemana2000","ONLYOFFICE/core-fonts/master/fonts-telu-extra/vemana2000.ttf","Dr. Tirumala Krishna Desikacharyulu","GPLv2+ with font exception"),
      ("pothana2000","Pothana 2000","ONLYOFFICE/core-fonts/master/fonts-telu-extra/Pothana2000.ttf","Dr. Tirumala Krishna Desikacharyulu","GPLv2+ with font exception")
    ]
    for fid,name,path,author,lic in classics:
        fonts.append({"id":fid,"name":name,"style":"Regular","weight":400,"width":100,"group":"classic",
          "author":author,"license":lic,"font_raw_url":"https://raw.githubusercontent.com/"+path,
          "download_type":"TTF","preview_ready":True,"variable":False})
    anek="https://raw.githubusercontent.com/google/fonts/main/ofl/anektelugu/AnekTelugu%5Bwdth%2Cwght%5D.ttf"
    for label,width in [("Condensed",75),("SemiCondensed",87.5),("Normal",100),("SemiExpanded",112.5),("Expanded",125)]:
        for w in range(100,801,100):
            fonts.append({"id":f"anek-telugu-{slug(label)}-{w}","name":f"Anek Telugu {label}","style":style_for(w),
              "weight":w,"width":width,"group":"anek","author":"Ek Type / Google Fonts","license":"SIL OFL",
              "font_raw_url":anek,"download_type":"TTF","preview_ready":True,"variable":True})
    for name,base,weights in [
        ("Hind Guntur","hindguntur/HindGuntur",[300,400,500,600,700]),
        ("Chathura","chathura/Chathura",[100,300,400,700,800])]:
        for w in weights:
            s=style_for(w)
            fonts.append({"id":f"{slug(name)}-{w}","name":name,"style":s,"weight":w,"width":100,"group":"multi",
              "author":"Google Fonts","license":"SIL OFL",
              "font_raw_url":f"https://raw.githubusercontent.com/google/fonts/main/ofl/{base}-{s}.ttf",
              "download_type":"TTF","preview_ready":True,"variable":False})
    noto="https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstelugu/NotoSansTelugu%5Bwdth%2Cwght%5D.ttf"
    for w in range(100,901,100):
        fonts.append({"id":f"noto-sans-telugu-{w}","name":"Noto Sans Telugu","style":style_for(w),"weight":w,"width":100,
          "group":"multi","author":"Google / Noto","license":"SIL OFL","font_raw_url":noto,
          "download_type":"TTF","preview_ready":True,"variable":True})
    baloo="https://raw.githubusercontent.com/google/fonts/main/ofl/balootammudu2/BalooTammudu2%5Bwght%5D.ttf"
    for w in [400,500,600,700,800]:
        fonts.append({"id":f"baloo-tammudu-2-{w}","name":"Baloo Tammudu 2","style":style_for(w),"weight":w,"width":100,
          "group":"multi","author":"Ek Type","license":"SIL OFL","font_raw_url":baloo,
          "download_type":"TTF","preview_ready":True,"variable":True})
    if len(fonts)!=84: raise RuntimeError(f"expected 84 seed fonts, got {len(fonts)}")
    return {"fonts":fonts}

def get_archive(src:dict[str,Any])->bytes:
    local=str(src.get("archive_path") or "").strip()
    if local:
        p=(ROOT/local).resolve()
        if ROOT.resolve() not in p.parents: raise ValueError("unsafe archive_path")
        return p.read_bytes()
    r=requests.get(src["zip_url"],timeout=(15,60),headers={"User-Agent":"AiTechApp-Font-Pipeline/2"},stream=True)
    r.raise_for_status(); out=bytearray()
    for chunk in r.iter_content(256*1024):
        if chunk:
            out.extend(chunk)
            if len(out)>MAX_ARCHIVE: raise ValueError("archive too large")
    return bytes(out)

def candidates(payload:bytes,src:dict[str,Any],log:list[dict[str,Any]])->list[dict[str,Any]]:
    sid=slug(src.get("id") or src.get("name") or "source"); result=[]
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        infos=z.infolist()
        if sum(max(0,i.file_size) for i in infos)>MAX_TOTAL: raise ValueError("uncompressed ZIP too large")
        for i in infos:
            if i.is_dir(): continue
            p=Path(i.filename.replace("\\","/"))
            if i.filename.startswith("/") or ".." in p.parts: continue
            ext=p.suffix.lower()
            if ext not in (".ttf",".otf") or i.file_size<=0 or i.file_size>MAX_MEMBER: continue
            data=z.read(i)
            try: meta=inspect_font(data,src.get("name",sid))
            except Exception as e:
                log.append({"source":sid,"file":i.filename,"status":"invalid-font","error":str(e)}); continue
            result.append({"source_id":sid,"member":i.filename,"ext":ext,"data":data,
              "sha256":hashlib.sha256(data).hexdigest(),**meta})
    return result

def choose_faces(items:list[dict[str,Any]],log:list[dict[str,Any]])->list[dict[str,Any]]:
    groups=defaultdict(list)
    for c in items: groups[face_key(c["family"],c["style"],c["weight"])].append(c)
    out=[]
    for key,g in groups.items():
        g.sort(key=lambda c:(0 if c["ext"]==".ttf" else 1,1 if c.get("variable") else 0,len(c["data"]),c["member"].casefold()))
        out.append(g[0])
        for x in g[1:]:
            log.append({"source":x["source_id"],"file":x["member"],"status":"duplicate-face","selected":g[0]["member"],"face":key})
    return out

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo",default=os.getenv("GITHUB_REPOSITORY","bammidieswararao/Aitechapp"))
    ap.add_argument("--branch",default=os.getenv("GITHUB_REF_NAME","main"))
    ap.add_argument("--confirm-all-rehosting",action="store_true")
    args=ap.parse_args()
    seed=json.loads(SEED.read_text(encoding="utf-8")) if SEED.exists() else builtin_seed()
    entries=[]
    for x in seed["fonts"]:
        e=dict(x); e.setdefault("source","seed"); validate_row(e); entries.append(e)
    if FONT_DIR.exists(): shutil.rmtree(FONT_DIR)
    FONT_DIR.mkdir(parents=True,exist_ok=True)
    log=[]; hashes={}
    srcdoc=json.loads(SOURCES.read_text(encoding="utf-8"))
    for src in srcdoc.get("sources",[]):
        sid=slug(src.get("id") or src.get("name") or "source")
        if not (src.get("redistribution_confirmed") or args.confirm_all_rehosting):
            log.append({"source":sid,"status":"skipped-rehosting-not-confirmed"}); continue
        try:
            for c in choose_faces(candidates(get_archive(src),src,log),log):
                if c["sha256"] in hashes:
                    log.append({"source":sid,"file":c["member"],"status":"duplicate-binary","same_as":hashes[c["sha256"]]}); continue
                d=FONT_DIR/sid; d.mkdir(parents=True,exist_ok=True)
                stem=re.sub(r"[^A-Za-z0-9._-]+","-",f"{c['family']}-{c['style']}-{c['weight']}").strip(".-")
                dest=d/(stem+c["ext"]); dest.write_bytes(c["data"])
                rel=dest.relative_to(ROOT); hashes[c["sha256"]]=rel.as_posix()
                e={"id":slug(f"{sid}-{c['family']}-{c['style']}-{c['weight']}"),"name":c["family"],"style":c["style"],
                   "weight":c["weight"],"width":100,"group":"third-party","author":src.get("name","Third-party font"),
                   "license":src.get("license","See original source terms"),"font_raw_url":raw_url(args.repo,args.branch,rel),
                   "download_type":c["ext"][1:].upper(),"preview_ready":True,"variable":c.get("variable",False),
                   "axes":c.get("axes",{}),"file_name":dest.name,"sha256":c["sha256"],"source":sid,
                   "source_zip_url":src.get("zip_url")}
                validate_row(e); entries.append(e)
                log.append({"source":sid,"file":c["member"],"status":"imported","entry":e["id"],"type":e["download_type"]})
        except Exception as ex: log.append({"source":sid,"status":"error","error":str(ex)})
    dedup={}
    for e in entries:
        k=face_key(e["name"],e.get("style","Regular"),e.get("weight",400))
        old=dedup.get(k)
        if old is None or (old.get("source")!="seed" and e.get("source")=="seed"):
            dedup[k]=e
        elif old.get("source")!="seed" and e.get("source")!="seed":
            if e["download_type"]=="TTF" and old["download_type"]=="OTF": dedup[k]=e
    order={"anek":0,"multi":1,"classic":2,"third-party":3}
    final=sorted(dedup.values(),key=lambda e:(order.get(e.get("group"),9),e["name"].casefold(),int(e.get("weight",400)),e.get("style","").casefold()))
    for n,e in enumerate(final,1): e["serial"]=n; validate_row(e)
    OUT.write_text(json.dumps({"fonts":final},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    REPORT.write_text(json.dumps({"records":log},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Wrote {len(final)} preview-ready direct font rows")
    return 0
if __name__=="__main__": raise SystemExit(main())
