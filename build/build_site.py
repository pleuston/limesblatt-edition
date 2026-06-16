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
            rec["residence"] = g(r'<residence>([^<]+)<')
            rec["strecke"] = g(r'<state type="strecke"><label>([^<]+)<')
            rec["briefe"] = g(r'<note type="briefe" n="([^"]+)"')
            rec["nachlass"] = g(r'<note type="nachlass">([^<]+)<')
        else:
            geo = g(r'<geo>([^<]+)<'); rec["geo"] = geo.split() if geo else []
            rec["region"] = g(r'<region>([^<]+)<')
            rec["modern"] = g(r'<placeName type="modern">([^<]+)<')
            rec["typ"] = g(r'<trait type="kastelltyp"><desc>([^<]+)<')
            rec["edh"] = g(r'<note type="edh" n="([^"]+)"')
            rec["strecke_id"] = g(r'<note type="strecke" corresp="#([^"]+)"')
            rec["strecke_name"] = g(r'<note type="strecke"[^>]*>([^<]+)<')
            dg = g(r'excavatedBy" passive="([^"]+)"')
            rec["diggers"] = [d[1:] for d in dg.split()] if dg else []
        out.append(rec)
    return out

def load_strecken(path):
    t = open(path, encoding="utf-8").read(); out = []
    for m in re.finditer(r'<place type="strecke" xml:id="([^"]+)">(.*?)</place>', t, re.S):
        xid, blk = m.group(1), m.group(2)
        def g(p):
            mm = re.search(p, blk, re.S); return unesc(mm.group(1)) if mm else ""
        out.append({"id": xid, "name": g(r'<placeName>([^<]+)<'), "nummer": g(r'<idno type="nummer">([^<]+)<'),
            "verlauf": g(r'<desc type="verlauf">([^<]+)<'), "region": g(r'<region>([^<]+)<'),
            "abschnitt": g(r'<desc type="abschnitt">([^<]+)<')})
    return out

# ---------- HTML-Shell ----------
def page(title, body, depth=0, head=""):
    up = "../" * depth
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Limesblatt-Edition</title>
<link rel="stylesheet" href="{up}assets/style.css">{head}</head><body>
<header><a class="home" href="{up}index.html">📕 Limesblatt-Edition</a>
<nav><a href="{up}index.html">Bände</a> · <a href="{up}register/persons.html">Personen</a> · <a href="{up}register/places.html">Orte</a> · <a href="{up}register/strecken.html">Strecken</a> · <a href="{up}index.html#suche">Suche</a></nav></header>
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

def links_line(parts):
    return ('<div class="links">' + " · ".join(p for p in parts if p) + '</div>') if any(parts) else ""

def persons_page(persons, occ, digs):
    cards = []
    for p in sorted(persons, key=lambda r: r["name"].split()[-1]):
        I = p["idno"]
        dts = f' <span class="dts">({html.escape(p["birth"])}–{html.escape(p["death"])})</span>' if p["birth"] or p["death"] else ""
        al  = f'<div class="alias">alias {html.escape(", ".join(p["alias"]))}</div>' if p.get("alias") else ""
        meta = " · ".join(x for x in [html.escape(p["occ"]),
                 ("Wirkungsort: " + html.escape(p["residence"])) if p.get("residence") else "",
                 ("Streckenkommissar: " + html.escape(p["strecke"])) if p.get("strecke") else ""] if x)
        kal = ""
        if I.get("Kalliope"):
            br = f' ({html.escape(p["briefe"])} Briefe)' if p.get("briefe") else ""
            kal = f'<a href="https://kalliope-verbund.info/gnd/{html.escape(I["Kalliope"])}">Kalliope{br}</a>'
        links = links_line([
            f'<a href="https://d-nb.info/gnd/{html.escape(I["GND"])}">GND</a>' if I.get("GND") else "",
            f'<a href="https://www.wikidata.org/wiki/{html.escape(I["Wikidata"])}">Wikidata</a>' if I.get("Wikidata") else "",
            f'<a href="{html.escape(I["DeutscheBiographie"])}">Dt. Biographie</a>' if I.get("DeutscheBiographie") else "", kal])
        extra = []
        if p.get("nachlass"): extra.append(f'<div class="x">🗄️ Nachlass: {html.escape(p["nachlass"])}</div>')
        forts = digs.get(p["id"], [])
        if forts:
            extra.append('<div class="x">⛏️ Ausgegraben: ' + ", ".join(
                f'<a href="places.html#{f["id"]}">{html.escape(f["name"])}</a>' for f in forts) + '</div>')
        bel = beleg_html(p["id"], occ)
        if "—" not in bel: extra.append(f'<div class="x">📄 Im Volltext: {bel}</div>')
        img = f'<img class="portrait" src="{html.escape(I["portrait"])}" alt="" loading="lazy">' if I.get("portrait") else ""
        cards.append(f'<article class="card" id="{p["id"]}">{img}<div class="cbody">'
                     f'<h3>{html.escape(p["name"])}{dts}</h3>{al}<div class="role">{meta}</div>{links}{"".join(extra)}</div></article>')
    return (f'<h1>Personenregister</h1><p class="meta">{len(persons)} Personen — Normdaten, Porträts, '
            f'Korrespondenz, Nachlass, ausgegrabene Kastelle und Volltext-Fundstellen.</p>'
            f'<div class="cards">{"".join(cards)}</div>')

def places_page(places, occ, pname, str_by_id, sites, site_hits):
    feats, cards = [], []
    for pl in sorted(places, key=lambda r: r["name"]):
        I = pl["idno"]
        meta = " · ".join(x for x in [
            ("heute " + html.escape(pl["modern"])) if pl.get("modern") else "",
            html.escape(pl.get("typ","")), html.escape(I.get("ORL","")), html.escape(pl.get("region",""))] if x)
        links = links_line([
            f'<a href="https://www.wikidata.org/wiki/{html.escape(I["Wikidata"])}">Wikidata</a>' if I.get("Wikidata") else "",
            f'<a href="https://gazetteer.dainst.org/place/{html.escape(I["iDAI-Gazetteer"])}">iDAI-Gazetteer</a>' if I.get("iDAI-Gazetteer") else "",
            f'<a href="https://pleiades.stoa.org/places/{html.escape(I["Pleiades"])}">Pleiades</a>' if I.get("Pleiades") else ""])
        extra = []
        dg = [d for d in pl.get("diggers", []) if d in pname]
        if dg:
            extra.append('<div class="x">⛏️ Ausgräber: ' + ", ".join(
                f'<a href="persons.html#{d}">{html.escape(pname[d])}</a>' for d in dg) + '</div>')
        if pl.get("strecke_id"):
            extra.append(f'<div class="x">🛤️ <a href="strecken.html#{pl["strecke_id"]}">{html.escape(pl.get("strecke_name",""))}</a></div>')
        if pl.get("edh"):
            extra.append(f'<div class="x">🪦 {html.escape(pl["edh"])} Inschriften (<a href="https://edh.ub.uni-heidelberg.de/">EDH</a>)</div>')
        bel = beleg_html(pl["id"], occ)
        if "—" not in bel: extra.append(f'<div class="x">📄 Im Volltext: {bel}</div>')
        img = f'<img class="portrait" src="{html.escape(I["portrait"])}" alt="" loading="lazy">' if I.get("portrait") else ""
        cards.append(f'<article class="card" id="{pl["id"]}">{img}<div class="cbody">'
                     f'<h3>{html.escape(pl["name"])}</h3><div class="role">{meta}</div>{links}{"".join(extra)}</div></article>')
        if pl["geo"]:
            sid = pl.get("strecke_id", "")
            ab = str_by_id.get(sid, {}).get("abschnitt", "") if sid else ""
            feats.append({"name": pl["name"], "lat": float(pl["geo"][0]), "lng": float(pl["geo"][1]),
                          "orl": html.escape(I.get("ORL","")), "id": pl["id"],
                          "strecke": pl.get("strecke_name",""), "strecke_id": sid, "abschnitt": ab})
    # Liste der weiteren Limesstellen (DARE), gruppiert nach Typ
    by_type = {}
    for s in sites:
        p = s.get("properties", {}); by_type.setdefault(p.get("type", "?"), []).append(p)
    tlabel = {"fortlet/tower": "Türme &amp; Kleinkastelle", "fort": "Forts / Kastelle", "camp": "Lager"}
    secs = []
    for t in ["fortlet/tower", "fort", "camp"]:
        items = sorted(by_type.get(t, []), key=lambda p: p.get("name", ""))
        if not items: continue
        lis = []
        for p in items:
            anc = f' <i>{html.escape(p["ancient"])}</i>' if p.get("ancient") else ""
            did = html.escape(str(p.get("id", "")))
            dare = f' · <a href="https://imperium.ahlfeldt.se/places/{did}">DARE</a>' if did else ""
            foc = f' <a href="#map" onclick="focusSite(\'{did}\')" title="auf der Karte zeigen">📍</a>' if did else ""
            hh = site_hits.get(p.get("id"), [])
            vt = ""
            if hh:
                lk = ", ".join(f'<a href="../volumes/bd{v}.html#pb-{html.escape(t)}">{v}/{html.escape(t)}</a>' for v, t in hh[:3])
                vt = f' · 📄 {len(hh)}× ({lk}{" +"+str(len(hh)-3) if len(hh) > 3 else ""})'
            lis.append(f'<li>{html.escape(p.get("name", "?"))}{anc}{dare}{foc}{vt}</li>')
        secs.append(f'<details><summary>{tlabel.get(t, t)} ({len(items)})</summary><ul class="sites">{"".join(lis)}</ul></details>')
    nvt = sum(1 for s in sites if site_hits.get(s.get("properties",{}).get("id")))
    sites_html = (f'<h2 id="weitere">Weitere Limesstellen — {len(sites)} (DARE)</h2>'
                  '<p class="meta">Türme, Kleinkastelle und Lager <i>zwischen</i> den benannten Kastellen, '
                  f'je mit DARE-Datensatz, 📍 Karten-Fokus und — bei {nvt} Stellen — 📄 <b>heuristischen '
                  'Volltext-Treffern</b> (Toponym-Abgleich auf Fraktur-OCR; nicht jede Nennung meint zwingend '
                  'diese Stelle). Gazetteer-Stellen ohne RLK-Wachtposten-Nr.</p>'
                  + "".join(secs)) if sites else ""
    head = '<link rel="stylesheet" href="../assets/leaflet.css"><script src="../assets/leaflet.js"></script>'
    body = (f'<h1>Ortsregister</h1><p class="meta">{len(places)} benannte Kastelle (Karten unten) plus '
            f'{len(sites)} weitere Limesstellen — auf der Karte zuschaltbar: der <b>Limesverlauf</b> und die '
            f'<b>weiteren Limesstellen</b> (Türme / Kleinkastelle / Lager, DARE). Filter nach Limes-Abschnitt.</p>'
            f'<div id="facets"></div><div id="map"></div>'
            f'<div class="cards">{"".join(cards)}</div>'
            f'{sites_html}'
            f'<script>var MAPDATA={{"feats":{json.dumps(feats)}}};</script>'
            f'<script src="../assets/map.js"></script>')
    return body, head

def strecken_page(strecken, str_forts, persons, pname):
    cards = []
    for s in strecken:
        forts = str_forts.get(s["id"], [])
        fl = ", ".join(f'<a href="places.html#{f["id"]}">{html.escape(f["name"])}</a>' for f in forts) or '<span class="meta">—</span>'
        hay = (s["name"] + s["region"] + s["verlauf"] + s["abschnitt"]).lower()
        komm = [p for p in persons if p.get("strecke") and p["strecke"].lower() in hay]
        dig_ids = []
        for f in forts:
            for d in f.get("diggers", []):
                if d in pname and d not in dig_ids: dig_ids.append(d)
        meta = " · ".join(x for x in [html.escape(s["verlauf"]), html.escape(s["region"]), html.escape(s["abschnitt"])] if x)
        extra = f'<div class="x">⛏️ Kastelle: {fl}</div>'
        if forts: extra += f'<div class="x">🗺️ <a href="places.html?strecke={s["id"]}">Auf der Karte zeigen</a></div>'
        bet = []
        if komm: bet.append("Kommissar (Region): " + ", ".join(
            f'<a href="persons.html#{p["id"]}">{html.escape(p["name"])}</a>' for p in komm))
        if dig_ids: bet.append("Ausgräber: " + ", ".join(
            f'<a href="persons.html#{d}">{html.escape(pname[d])}</a>' for d in dig_ids))
        if bet: extra += '<div class="x">👤 Beteiligte — ' + " · ".join(bet) + '</div>'
        cards.append(f'<article class="card wide" id="{s["id"]}"><div class="cbody">'
                     f'<h3>{html.escape(s["name"])}</h3><div class="role">{meta}</div>{extra}</div></article>')
    return (f'<h1>Strecken</h1><p class="meta">{len(strecken)} Limes-Abschnitte mit Kastellen sowie den '
            f'beteiligten Personen (Streckenkommissare regional zugeordnet, Ausgräber aus den Kastellen).</p>'
            f'<div class="cards">{"".join(cards)}</div>')

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
<h2>Register</h2><ul><li><a href="register/persons.html">Personenregister</a> — mit Porträts, Normdaten, Korrespondenz, ausgegrabenen Kastellen</li>
<li><a href="register/places.html">Ortsregister</a> — mit Karte, Kastelltyp, Ausgräber, Inschriften</li>
<li><a href="register/strecken.html">Strecken</a> — die 15 Limes-Abschnitte mit Kastellen &amp; Kommissaren</li></ul>
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
    for f in glob.glob(os.path.join(REPO,"geo","*.geojson")): shutil.copy(f, os.path.join(DOCS,"data"))

    volumes = sorted((load_volume(f) for f in glob.glob(os.path.join(REPO,"tei","*.xml"))), key=lambda v: v["nr"])
    persons = load_register(os.path.join(REPO,"registers","persons.xml"), "person")
    places  = load_register(os.path.join(REPO,"registers","places.xml"), "place")
    strecken = load_strecken(os.path.join(REPO,"registers","strecken.xml"))
    str_by_id = {s["id"]: s for s in strecken}
    pname = {p["id"]: p["name"] for p in persons}
    sp = os.path.join(REPO,"geo","sites.geojson")
    sites = json.load(open(sp,encoding="utf-8")).get("features",[]) if os.path.exists(sp) else []
    digs, str_forts = defaultdict(list), defaultdict(list)   # Person→Orte (Ausgräber), Strecke→Orte
    for pl in places:
        for d in pl.get("diggers", []):
            if d in pname: digs[d].append(pl)
        if pl.get("strecke_id"): str_forts[pl["strecke_id"]].append(pl)

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

    # DARE-Kleinorte heuristisch an den Volltext binden (Toponym-Match, token-frei)
    GENERIC = {"alteburg","altenburg","altes","oberburg","schanz","kapelle","kirche","mauer","graben",
               "heide","feld","muehle","mühle","strasse","straße","wiese","brücke","bruecke","steinbruch"}
    def site_terms(p):
        out = set()
        for src in (p.get("name",""), re.sub(r'^\*','', p.get("ancient",""))):
            for tok in re.split(r"[\s/\-–,()]+", src or ""):
                tok = tok.strip()
                if len(tok) >= 6 and tok[:1].isalpha() and tok.lower() not in GENERIC: out.add(tok.lower())
        return out
    pages_low = [(c["vol"], c["tok"], c["text"].lower()) for c in corpus]
    site_hits = {}
    for f in sites:
        p = f.get("properties", {}); ts = list(site_terms(p))
        if not ts: continue
        best = None
        for x in ts:                          # spezifischsten Term wählen (= wenigste Treffer)
            hh = [(v, t) for v, t, low in pages_low if x in low]
            if hh and (best is None or len(hh) < len(best)): best = hh
        if best: site_hits[p.get("id")] = best
    print(f"DARE-Kleinorte mit Volltext-Treffern: {len(site_hits)}/{len(sites)}")

    open(os.path.join(DOCS,"register","persons.html"),"w",encoding="utf-8").write(page("Personenregister", persons_page(persons, occ, digs), 1))
    plb, plh = places_page(places, occ, pname, str_by_id, sites, site_hits)
    open(os.path.join(DOCS,"register","places.html"),"w",encoding="utf-8").write(page("Ortsregister", plb, 1, plh))
    open(os.path.join(DOCS,"register","strecken.html"),"w",encoding="utf-8").write(page("Strecken", strecken_page(strecken, str_forts, persons, pname), 1))
    ib, ih = index_page(volumes)
    open(os.path.join(DOCS,"index.html"),"w",encoding="utf-8").write(page("Startseite", ib, 0, ih))
    print(f"docs/: index + {len(volumes)} Bände + 3 Register (Personen {len(persons)}, Orte {len(places)}, "
          f"Strecken {len(strecken)}) · Suchindex {len(corpus)} Seiten · Ausgräber-Links {sum(len(v) for v in digs.values())}")

if __name__ == "__main__":
    main()
