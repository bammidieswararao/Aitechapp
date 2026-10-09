(() => {
  "use strict";
  const CATALOG_URL = "fonts-catalog.json";
  const DEFAULT_TEXT = "తెలుగు అక్షరాలు అందమైన ఫాంట్స్";
  const $ = (s, r=document) => r.querySelector(s);
  const list = $("#list");
  const previewInput = $("#previewInput");
  const count = $("#count");
  const dynamicStyle = $("#dynamicFontFaces") || (() => {
    const s=document.createElement("style"); s.id="dynamicFontFaces"; document.head.appendChild(s); return s;
  })();
  const loaded = new Set();
  const byId = new Map();

  function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
  function text(){return (previewInput?.value||"").trim()||DEFAULT_TEXT;}
  function family(f){return "AiTF_"+String(f.id).replace(/[^A-Za-z0-9_-]/g,"_");}
  function fmt(t){return String(t).toUpperCase()==="OTF"?"opentype":"truetype";}
  function style(f){return /italic|oblique/i.test(String(f.style||""))?"italic":"normal";}

  function valid(f){
    if(!f||!f.id||!f.name||!f.font_raw_url||f.preview_ready!==true)return false;
    const t=String(f.download_type||"").toUpperCase();
    if(t!=="TTF"&&t!=="OTF")return false;
    let u=String(f.font_raw_url);
    try{u=decodeURIComponent(u);}catch{}
    return !/\/blob\//i.test(u)&&!/\.zip(?:\?|$)/i.test(u)&&/\.(ttf|otf)(?:\?|$)/i.test(u);
  }

  async function register(f){
    if(loaded.has(f.id))return;
    const fam=family(f);
    const url=String(f.font_raw_url).replace(/\\/g,"\\\\").replace(/"/g,'\\"');
    const rule='@font-face{font-family:"'+fam+'";src:url("'+url+'") format("'+fmt(f.download_type)+'");font-style:'+style(f)+';font-weight:'+(Number(f.weight)||400)+';font-display:swap;}';
    dynamicStyle.sheet.insertRule(rule,dynamicStyle.sheet.cssRules.length);
    loaded.add(f.id);
    try{await document.fonts.load(style(f)+" "+(Number(f.weight)||400)+' 18px "'+fam+'"',text());}catch{}
  }

  function apply(f,el){
    el.style.fontFamily='"'+family(f)+'","Noto Sans Telugu",sans-serif';
    el.style.fontWeight=String(Number(f.weight)||400);
    el.style.fontStyle=style(f);
    if(f.variable){
      const axes=["'wght' "+(Number(f.weight)||400)];
      if(f.width)axes.push("'wdth' "+Number(f.width));
      el.style.fontVariationSettings=axes.join(",");
    }
  }

  async function verify(blob,type){
    const b=new Uint8Array(await blob.slice(0,4).arrayBuffer());
    const sig=Array.from(b).map(x=>x.toString(16).padStart(2,"0")).join("");
    const ttf=["00010000","74727565","74797031"].includes(sig);
    const otf=sig==="4f54544f";
    if(String(type).toUpperCase()==="OTF"?!otf:!(ttf||otf))throw new Error("not a font binary");
  }

  async function download(f,button){
    if(button.disabled)return;
    button.disabled=true; const old=button.textContent; button.textContent="Downloading…";
    try{
      const r=await fetch(f.font_raw_url,{mode:"cors",cache:"force-cache"});
      if(!r.ok)throw new Error("HTTP "+r.status);
      const blob=await r.blob(); await verify(blob,f.download_type);
      const url=URL.createObjectURL(blob);
      const a=document.createElement("a");
      a.href=url;
      a.download=(f.file_name||f.id+"."+String(f.download_type).toLowerCase()).replace(/[^A-Za-z0-9._-]+/g,"-");
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(()=>URL.revokeObjectURL(url),5000);
    } finally { button.disabled=false; button.textContent=old; }
  }

  function render(fonts){
    if(!list)return;
    list.innerHTML="";
    fonts.forEach((f,i)=>{
      const card=document.createElement("article");
      card.className="card"; card.dataset.id=f.id;
      card.innerHTML='<div class="line1"><span class="serial">#'+(f.serial||i+1)+'</span><strong>'+esc(f.name)+'</strong> <span>'+esc(f.style||"Regular")+'</span></div>'+
        '<div class="preview">'+esc(text())+'</div>'+
        '<button class="download" type="button">Download .'+esc(f.download_type)+'</button>';
      const p=$(".preview",card); apply(f,p);
      const b=$(".download",card); b.addEventListener("click",()=>download(f,b));
      list.appendChild(card); byId.set(f.id,f);
      register(f);
    });
    if(count)count.textContent=fonts.length;
  }

  previewInput?.addEventListener("input",()=>document.querySelectorAll("#list .preview").forEach(x=>x.textContent=text()));

  fetch(CATALOG_URL+"?v="+Date.now(),{cache:"no-store"})
    .then(r=>{if(!r.ok)throw new Error("HTTP "+r.status);return r.json();})
    .then(data=>{
      const rows=(Array.isArray(data)?data:data.fonts||[]).filter(valid);
      const seen=new Set(), clean=[];
      for(const f of rows){
        const k=[String(f.name).toLowerCase().replace(/[^a-z0-9]+/g,""),String(f.style||"Regular").toLowerCase().replace(/[^a-z0-9]+/g,""),Number(f.weight)||400,Number(f.width)||100].join("|");
        if(seen.has(k))continue; seen.add(k); clean.push(f);
      }
      render(clean);
    })
    .catch(err=>console.error("Telugu font catalogue load failed",err));
})();