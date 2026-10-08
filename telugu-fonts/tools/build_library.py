#!/usr/bin/env python3
"""Publish only fonts with documented public redistribution rights."""
import io,json,os,re,hashlib,zipfile,urllib.request
from pathlib import Path
from urllib.parse import quote
from fontTools.ttLib import TTFont
BASE=Path(__file__).resolve().parents[1]
PUB=BASE/"public"
FONTDIR=PUB/"fonts"
HOST="https://raw.githubusercontent.com/bammidieswararao/Aitechapp/main/telugu-fonts/public/fonts/"
MAX_FONT=25*1024*1024
MAX_ZIP=100*1024*1024
MAX_UNPACK=220*1024*1024

def safe_name(s):
    s=re.sub(r"[^A-Za-z0-9_.-]","_",s)
    if not s or s.startswith("."): raise ValueError("Bad font file name")
    return s[:140]

def valid_font(data):
    if len(data)<1024 or len(data)>MAX_FONT or data[:4] not in (b"\x00\x01\x00\x00",b"OTTO"):return False
    try:
        font=TTFont(io.BytesIO(data),lazy=True)
        good=any(0x0c00<=ch<=0x0c7f for tab in font["cmap"].tables for ch in tab.cmap)
        font.close()
        return good
    except Exception: return False

def fetch(url,maxbytes=MAX_FONT):
    if not url.startswith("https://raw.githubusercontent.com/google/fonts/main/ofl/"):
        raise ValueError("Remote source is not an approved OFL repository")
    with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"AiTechApp-FontLibrary/1.0"}),timeout=40) as r:
        b=r.read(maxbytes+1)
    if len(b)>maxbytes:raise ValueError("Input exceeds size limit")
    return b

def store(name,contents):
    if not valid_font(contents):raise ValueError("Font has no valid Telugu glyph coverage: "+name)
    fn=hashlib.sha256(contents).hexdigest()[:14]+"-"+safe_name(name)
    FONTDIR.mkdir(parents=True,exist_ok=True)
    (FONTDIR/fn).write_bytes(contents)
    return fn

def add(manifest,seen,name,style,filename,data,license_name,group,license_ref):
    digest=hashlib.sha256(data).hexdigest()
    if digest in seen:return
    seen.add(digest)
    stored=store(filename,data)
    manifest.append({"id":"mirror-"+digest[:20],"name":name,"style":style,
                     "file":filename,"url":HOST+quote(stored),
                     "license":license_name,"license_url":license_ref,
                     "group":group,"type":filename.rsplit(".",1)[-1].lower(),
                     "verified":True})

def build():
    FONTDIR.mkdir(parents=True,exist_ok=True)
    (PUB/"licenses").mkdir(parents=True,exist_ok=True)
    manifest=[];seen=set();errors=[]
    sources=json.loads((BASE/"sources.json").read_text("utf-8"))
    for entry in sources:
        try:
            src=entry["source"]; lic=entry["license_url"]
            licbytes=fetch(lic,250000)
            if b"SIL OPEN FONT LICENSE" not in licbytes.upper() and b"OPEN FONT LICENSE" not in licbytes.upper():
                raise ValueError("OFL license file missing")
            folder=lic.removesuffix("/OFL.txt").split("/")[-1]
            (PUB/"licenses"/(safe_name(folder)+".txt")).write_bytes(licbytes)
            data=fetch(src)
            add(manifest,seen,entry["name"],entry["style"],entry["file"],data,"OFL-1.1","hosted",lic)
        except Exception as exc:
            errors.append(entry.get("file","unknown")+": "+str(exc))
    permissions=json.loads((BASE/"incoming"/"permissions.json").read_text("utf-8"))
    for entry in permissions:
        if entry.get("license") not in ("OFL-1.1","redistribution-authorized"):
            raise ValueError("Imported archive must permit public redistribution")
        proof=entry.get("permission_evidence","")
        if not (proof.startswith("https://") and len(proof)>12):
            raise ValueError("Evidence URL required for each imported archive")
        path=(BASE/entry["archive"]).resolve()
        if not path.is_relative_to((BASE/"incoming").resolve()) or not path.is_file():
            raise ValueError("Invalid archive location")
        if path.stat().st_size>MAX_ZIP:raise ValueError("ZIP size exceeded")
        with zipfile.ZipFile(path) as z:
            infos=z.infolist();total=0
            if len(infos)>600:raise ValueError("Too many archive entries")
            for info in infos:
                if info.is_dir():continue
                total+=info.file_size
                if total>MAX_UNPACK:raise ValueError("Archive inflation limit")
                if info.flag_bits&1:raise ValueError("Encrypted ZIP unsupported")
                if (info.external_attr>>16)&0o170000==0o120000:
                    raise ValueError("ZIP symlink rejected")
                if not re.search(r"\.(ttf|otf)$",info.filename,re.I):continue
                if info.file_size>MAX_FONT:continue
                original=Path(info.filename).name
                blob=z.read(info)
                if not valid_font(blob):continue
                family=original.rsplit(".",1)[0].replace("_"," ").replace("-"," ")
                group="gist" if re.search(r"GIST|TLOT",original,re.I) else "hosted"
                add(manifest,seen,family,"Regular",original,blob,entry["license"],group,proof)
    if not manifest:raise SystemExit("No valid licensed Telugu fonts; refusing empty publication")
    expected={e["url"].rsplit("/",1)[-1] for e in manifest}
    for f in FONTDIR.iterdir():
        if f.is_file() and f.suffix.lower() in (".ttf",".otf") and f.name not in expected:
            f.unlink()
    (PUB/"fonts.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("PUBLISHED",len(manifest),"verified files; FAILED",len(errors))
    for e in errors:print("WARN",e)
    if len(manifest)<len(sources)//2:raise SystemExit("Too many failed OFL downloads; manifest incomplete")
if __name__=="__main__":build()
