#!/usr/bin/env python3
"""
build_site.py — statische GitHub-Pages-Edition aus tei/ + registers/ erzeugen.
================================================================================
Rendert die (selbst erzeugten) TEI-Bände build-zeitlich zu HTML (kein client-
seitiges TEI-Framework nötig), bindet je Band einen IIIF-Faksimile-Viewer
(OpenSeadragon, UB-Heidelberg-Tiles) ein, baut Personen-/Ortsregister (Leaflet-
Karte) und einen clientseitigen Volltextindex (MiniSearch). Ausgabe → docs/.

    python3 build/build_site.py
"""
import glob, html, os, re, json, shutil
from collections import defaultdict
from itertools import groupby

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
DOCS = os.path.join(REPO, "docs")
IIIF_INFO = "https://digi.ub.uni-heidelberg.de/iiif/2/{slug}%3A{tok}.jpg/info.json"
IIIF_MAN  = "https://digi.ub.uni-heidelberg.de/diglit/iiif/{slug}/manifest"
LABELS = {1:"Bd. 1 (1892/93)",2:"Bd. 2 (1893/94)",3:"Bd. 3 (1894/95)",4:"Bd. 4 (1896)",
          5:"Bd. 5 (1897)",6:"Bd. 6 (1897/98)",7:"Bd. 7 (1898/1902)",8:"Bd. 8 (1903)"}

def unesc(s): return html.unescape(s)
def strip_tags(s): return re.sub(r"<[^>]+>", "", s)

# ---------- TEI → HTML (eigenes, schlankes Mapping für unser bekanntes Vokabular) ----------
def render_page(inner):
    """inner = der <p>…</p>-Block einer Seite; Inline-Tags → HTML-Spans/Links."""
    if "<gap" in inner: return '<p class="gap">[leere bzw. nicht erfasste Seite]</p>'
    def ent(m):
        tag, xid, txt = m.group(1), m.group(2), m.group(3)
        cls, reg = ("persName","persons") if tag=="persName" else ("placeName","places")
        return f'<a class="ent {cls}" href="../register/{reg}.html#{xid}" title="{cls}">{txt}</a>'
    body = re.sub(r'<(persName|placeName) ref="#([^"]+)"[^>]*>(.*?)</\1>', ent, inner, flags=re.S)
    return body  # bereits <p>…</p>

def load_volume(path):
    t = open(path, encoding="utf-8").read()
    nr = int(re.search(r'limesblatt-bd(\d+)-', path).group(1))
    slug = re.search(r'limesblatt-bd\d+-(.+)\.xml', os.path.basename(path)).group(1)
    pages = []
    for m in re.finditer(r'<pb n="([^"]+)" facs="#f_[^"]+"/>(.*?)(?=<pb |</div>)', t, re.S):
        tok, inner = m.group(1), m.group(2).strip()
        pages.append({"tok": tok, "html": render_page(inner),
                      "text": unesc(strip_tags(inner)).strip(),
                      "ents": re.findall(r'ref="#([^"]+)"', inner)})
    return {"nr": nr, "slug": slug, "label": LABELS.get(nr, f"Bd. {nr}"), "pages": pages}

def load_register(path, tag):
    t = open(path, encoding="utf-8").read(); out = []
    for m in re.finditer(rf'<{tag} xml:id="([^"]+)">(.*?)</{tag}>', t, re.S):
        xid, blk = m.group(1), m.group(2)
        def g(p):
            mm = re.search(p, blk, re.S); return unesc(mm.group(1)) if mm else ""
        idnos = {k: unesc(v) for k, v in re.findall(r'<idno type="([^"]+)">([^<]+)</idno>', blk)}
        rec = {"id": xid, "name": g(r'<(?:persName|placeName)[^>]*>([^<]+)<'), "idno": idnos}
        if tag == "person":
            rec["alias"] = [unesc(a) for a in re.findall(r'<persName type="alias">([^<]+)<', blk)]
            rec["birth"] = g(r'<birth when="([^"]+)"'); rec["death"] = g(r'<death when="([^"]+)"')
            rec["occ"] = g(r'<occupation>([^<]+)<')
        else:
            geo = g(r'<geo>([^<]+)<'); rec["geo"] = geo.split() if geo else []
            rec["region"] = g(r'<region>([^<]+)<')
        out.append(rec)
    return out

# ---------- HTML-Shell ----------
def page(title, body, depth=0, head=""):
    up = "../" * depth
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Limesblatt-Edition</title>
<link rel="stylesheet" href="{up}assets/style.css">{head}</head><body>
<header><a class="home" href="{up}index.html">📕 Limesblatt-Edition</a>
<nav><a href="{up}index.html">Bände</a> · <a href="{up}register/persons.html">Personen</a> · <a href="{up}register/places.html">Orte</a> · <a href="{up}index.html#suche">Suche</a></nav></header>
<main>{body}</main>
<footer>Diplomatische OCR-Edition des <em>Limesblatt</em> (1892–1903) · Text &amp; Register
<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a> · Seitenbilder © UB Heidelberg
(<a href="http://rightsstatements.org/vocab/InC/1.0/">In Copyright</a>, via IIIF verlinkt) ·
<a href="https://github.com/pleuston/limesblatt-edition">Quellcode &amp; TEI</a></footer></body></html>"""

def vol_page(v):
    slug = v["slug"]
    teiname = f"limesblatt-bd{v['nr']}-{slug}.xml"
    tiles = [IIIF_INFO.format(slug=slug, tok=p["tok"]) for p in v["pages"]]
    idx = {p["tok"]: i for i, p in enumerate(v["pages"])}
    text = []
    for i, p in enumerate(v["pages"]):
        text.append(f'<div class="pb" id="pb-{p["tok"]}" data-page="{i}" '
                    f'onclick="viewer.goToPage({i})" title="Faksimile zu S. {html.escape(p["tok"])} zeigen">— {html.escape(p["tok"])} —</div>')
        text.append(p["html"])
    head = ('<script src="../assets/openseadragon.min.js"></script>')
    body = f"""<h1>Limesblatt · {html.escape(v['label'])}</h1>
<p class="meta">IIIF-Faksimile: <a href="{IIIF_MAN.format(slug=slug)}">Manifest</a> (UB Heidelberg) ·
TEI: <a href="../tei/{teiname}">XML</a></p>
<div class="reader">
  <div class="facs"><div id="osd"></div>
    <div class="osdnav"><button onclick="viewer.goToPage(Math.max(0,viewer.currentPage()-1))">‹ vorige</button>
    <span id="pgind"></span><button onclick="viewer.goToPage(Math.min({len(tiles)-1},viewer.currentPage()+1))">nächste ›</button></div></div>
  <div class="text">{''.join(text)}</div>
</div>
<script>
var tiles = {json.dumps(tiles)};
var viewer = OpenSeadragon({{id:"osd", prefixUrl:"", tileSources:tiles, sequenceMode:true,
  showNavigationControl:false, showSequenceControl:false, gestureSettingsMouse:{{clickToZoom:false}}}});
function upd(){{document.getElementById("pgind").textContent=(viewer.currentPage()+1)+" / "+tiles.length;}}
viewer.addHandler("page", upd); viewer.addHandler("open", upd);
</script>"""
    return body, head

def beleg_html(eid, occ):
    """Rück-Links Register → Volltext-Fundstellen, gruppiert nach Band (kein NER, nur die TEI-Tags)."""
    items = occ.get(eid, [])
    if not items: return '<span class="meta">—</span>'
    out = []
    for vol, grp in groupby(items, key=lambda x: x[0]):
        links = ", ".join(f'<a href="../volumes/bd{vol}.html#pb-{html.escape(t)}">{html.escape(t)}</a>' for _, t in grp)
        out.append(f'Bd.&#160;{vol}: {links}')
    return " · ".join(out)

def persons_page(persons, occ):
    rows = []
    for p in sorted(persons, key=lambda r: r["name"].split()[-1]):
        dts = f'{p["birth"]}–{p["death"]}' if p["birth"] or p["death"] else ""
        gnd = f'<a href="https://d-nb.info/gnd/{p["idno"]["GND"]}">GND</a>' if p["idno"].get("GND") else ""
        wd  = f'<a href="https://www.wikidata.org/wiki/{p["idno"]["Wikidata"]}">WD</a>' if p["idno"].get("Wikidata") else ""
        al  = f'<br><span class="alias">{html.escape(", ".join(p["alias"]))}</span>' if p.get("alias") else ""
        rows.append(f'<tr id="{p["id"]}"><td><strong>{html.escape(p["name"])}</strong>{al}</td>'
                    f'<td>{html.escape(dts)}</td><td>{html.escape(p["occ"])}</td><td>{gnd} {wd}</td>'
                    f'<td class="beleg">{beleg_html(p["id"], occ)}</td></tr>')
    return (f'<h1>Personenregister</h1><p class="meta">{len(persons)} Personen, mit GND/Wikidata verknüpft. '
            f'Spalte „Im Volltext" springt zu den Fundstellen im Limesblatt.</p>'
            f'<table class="reg"><thead><tr><th>Name</th><th>Lebensdaten</th><th>Rolle</th><th>Normdaten</th><th>Im Volltext</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')

def places_page(places, occ):
    feats, rows = [], []
    for pl in sorted(places, key=lambda r: r["name"]):
        wd = f'<a href="https://www.wikidata.org/wiki/{pl["idno"]["Wikidata"]}">WD</a>' if pl["idno"].get("Wikidata") else ""
        gz = f'<a href="https://gazetteer.dainst.org/place/{pl["idno"]["iDAI-Gazetteer"]}">iDAI</a>' if pl["idno"].get("iDAI-Gazetteer") else ""
        pleiades = f'<a href="https://pleiades.stoa.org/places/{pl["idno"]["Pleiades"]}">Pleiades</a>' if pl["idno"].get("Pleiades") else ""
        orl = html.escape(pl["idno"].get("ORL",""))
        rows.append(f'<tr id="{pl["id"]}"><td><strong>{html.escape(pl["name"])}</strong></td><td>{orl}</td>'
                    f'<td>{html.escape(pl["region"])}</td><td>{wd} {gz} {pleiades}</td>'
                    f'<td class="beleg">{beleg_html(pl["id"], occ)}</td></tr>')
        if pl["geo"]:
            feats.append({"name": pl["name"], "lat": float(pl["geo"][0]), "lng": float(pl["geo"][1]),
                          "orl": orl, "wd": pl["idno"].get("Wikidata","")})
    head = '<link rel="stylesheet" href="../assets/leaflet.css"><script src="../assets/leaflet.js"></script>'
    body = (f'<h1>Ortsregister</h1><p class="meta">{len(places)} verortete Orte (Kastelle/Hinterland), '
            f'Geo + Wikidata/iDAI-Gazetteer/Pleiades/ORL.</p><div id="map"></div>'
            f'<table class="reg"><thead><tr><th>Ort</th><th>ORL</th><th>Provinz</th><th>Normdaten</th><th>Im Volltext</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            f'<script>var F={json.dumps(feats)};'
            'var map=L.map("map").setView([49.5,9.4],7);'
            'L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:18,'
            'attribution:"© OpenStreetMap"}).addTo(map);'
            'F.forEach(function(f){L.circleMarker([f.lat,f.lng],{radius:6,color:"#7a1f1f",'
            'fillColor:"#b33",fillOpacity:.8}).addTo(map).bindPopup("<b>"+f.name+"</b>"+(f.orl?"<br>"+f.orl:""));});</script>')
    return body, head

def index_page(volumes):
    lis = "".join(f'<li><a href="volumes/bd{v["nr"]}.html">{html.escape(v["label"])}</a> '
                  f'<span class="meta">— {len(v["pages"])} Seiten</span></li>' for v in volumes)
    head = '<script src="assets/minisearch.min.js"></script>'
    body = f"""<h1>Limesblatt — digitale Edition</h1>
<p class="lede">Die <em>Mitteilungen der Streckenkommissare bei der Reichs-Limeskommission</em>
(1892–1903): die laufenden Feldberichte der Limesforschung, als diplomatische OCR-Edition mit
IIIF-Faksimiles (UB Heidelberg) und mit GND-/Wikidata-/Geo-verknüpften Personen- und Ortsregistern.</p>
<section id="suche"><h2>Volltextsuche</h2>
<input id="q" type="search" placeholder="z. B. Saalburg, Entschädigung, Mommsen …" autocomplete="off">
<div id="res"></div></section>
<h2>Bände</h2><ul class="vols">{lis}</ul>
<h2>Register</h2><ul><li><a href="register/persons.html">Personenregister</a></li>
<li><a href="register/places.html">Ortsregister (mit Karte)</a></li></ul>
<p class="meta">Abgeleitet aus dem (privaten) Forschungs-Vault zur <a href="https://github.com/pleuston/limes">Reichs-Limeskommission</a>.
Edition/Code: <a href="https://github.com/pleuston/limesblatt-edition">GitHub</a>.</p>
<script>
fetch("data/search.json").then(r=>r.json()).then(docs=>{{
 var ms=new MiniSearch({{fields:["text"],storeFields:["vol","tok","label"]}}); ms.addAll(docs);
 var q=document.getElementById("q"),res=document.getElementById("res");
 q.addEventListener("input",function(){{
  var v=q.value.trim(); if(v.length<3){{res.innerHTML="";return;}}
  var hits=ms.search(v,{{prefix:true,fuzzy:.1}}).slice(0,40);
  res.innerHTML=hits.length?hits.map(function(h){{
    var t=h.text||""; var i=t.toLowerCase().indexOf(v.toLowerCase());
    var sn=i<0?t.slice(0,140):t.slice(Math.max(0,i-50),i+90);
    return '<a class="hit" href="volumes/bd'+h.vol+'.html#pb-'+h.tok+'">'+h.label+', S. '+h.tok+'</a> <span>…'+
      sn.replace(/</g,"&lt;")+'…</span>';}}).join(""):"<p class=meta>keine Treffer</p>";
 }});
}});
</script>"""
    return body, head

def main():
    os.makedirs(os.path.join(DOCS,"volumes"), exist_ok=True)
    os.makedirs(os.path.join(DOCS,"register"), exist_ok=True)
    for sub in ("tei","registers","data","assets"): os.makedirs(os.path.join(DOCS,sub), exist_ok=True)
    # TEI/Register zum Download/Reuse mitkopieren
    for f in glob.glob(os.path.join(REPO,"tei","*.xml")): shutil.copy(f, os.path.join(DOCS,"tei"))
    for f in glob.glob(os.path.join(REPO,"registers","*.xml")): shutil.copy(f, os.path.join(DOCS,"registers"))

    volumes = sorted((load_volume(f) for f in glob.glob(os.path.join(REPO,"tei","*.xml"))), key=lambda v: v["nr"])
    persons = load_register(os.path.join(REPO,"registers","persons.xml"), "person")
    places  = load_register(os.path.join(REPO,"registers","places.xml"), "place")

    occ, seen = defaultdict(list), set()      # Entität → [(Band, Token)], aus den TEI-Inline-Tags
    for v in volumes:
        for p in v["pages"]:
            for eid in p["ents"]:
                key = (eid, v["nr"], p["tok"])
                if key not in seen:
                    seen.add(key); occ[eid].append((v["nr"], p["tok"]))

    corpus = []
    for v in volumes:
        b, h = vol_page(v)
        open(os.path.join(DOCS,"volumes",f"bd{v['nr']}.html"),"w",encoding="utf-8").write(page(v["label"], b, 1, h))
        for p in v["pages"]:
            if p["text"]: corpus.append({"id":f"{v['nr']}-{p['tok']}","vol":v["nr"],"tok":p["tok"],
                                         "label":v["label"],"text":p["text"]})
    json.dump(corpus, open(os.path.join(DOCS,"data","search.json"),"w",encoding="utf-8"), ensure_ascii=False)

    open(os.path.join(DOCS,"register","persons.html"),"w",encoding="utf-8").write(page("Personenregister", persons_page(persons, occ), 1))
    plb, plh = places_page(places, occ)
    open(os.path.join(DOCS,"register","places.html"),"w",encoding="utf-8").write(page("Ortsregister", plb, 1, plh))
    ib, ih = index_page(volumes)
    open(os.path.join(DOCS,"index.html"),"w",encoding="utf-8").write(page("Startseite", ib, 0, ih))
    print(f"docs/: index + {len(volumes)} Bände + 2 Register · Suchindex {len(corpus)} Seiten · "
          f"{len(persons)} Personen, {len(places)} Orte")

if __name__ == "__main__":
    main()
