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
import glob, html, os, re, json, shutil, math, urllib.parse
from collections import defaultdict
from itertools import groupby
from urllib.parse import quote
import gazetteer

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
DOCS = os.path.join(REPO, "docs")
IIIF_INFO = "https://digi.ub.uni-heidelberg.de/iiif/2/{slug}%3A{tok}.jpg/info.json"
IIIF_MAN  = "https://digi.ub.uni-heidelberg.de/diglit/iiif/{slug}/manifest"
LABELS = {1:"Bd. 1 (1892/93)",2:"Bd. 2 (1893/94)",3:"Bd. 3 (1894/95)",4:"Bd. 4 (1896)",
          5:"Bd. 5 (1897)",6:"Bd. 6 (1897/98)",7:"Bd. 7 (1898/1902)",8:"Bd. 8 (1903)"}

def unesc(s): return html.unescape(s)
def strip_tags(s): return re.sub(r"<[^>]+>", "", s)

# ---------- TEI → HTML (eigenes, schlankes Mapping für unser bekanntes Vokabular) ----------
def _cert_of(attrs):
    m = re.search(r'cert="([^"]+)"', attrs or "")
    return m.group(1) if m else "low"

TOK2BAND = {}   # IIIF-Token → Bandnr. (für bandübergreifende interne Selbstverweise)

def _refsub(m):
    """<ref> → HTML: bibliographisch (#bib_…, ggf. mit citedRange) oder intern (#pb_…)."""
    tgt = m.group(2); inner = m.group(3).replace("<citedRange>", "").replace("</citedRange>", "")
    if tgt.startswith("bib_"):
        return f'<a class="ent bibl" href="../register/bibliographie.html#{tgt}" title="Literatur">{inner}</a>'
    if tgt.startswith("pb_"):
        b = tgt[3:]; i = b.rfind("_")
        tk, anchor = (b[:i], f"{b[:i]}-{b[i+1:]}") if i > 0 else (b, b)
        band = TOK2BAND.get(tk)
        if band:
            return f'<a class="ent xref" href="bd{band}.html#pb-{anchor}" title="Limesblatt-Selbstverweis">{inner}</a>'
    return inner

def _entsub(inner):
    """Inline-Eigennamen-Tags → HTML-Register-Links, konfidenz-gestuft (c-high/medium/low).
    Vault-IDs (p_/pl_) → kuratierte Register; NER-only-IDs (psnN_/plcN_) → Volltext-Indizes."""
    def dare(m):
        return (f'<a class="ent placeName dare c-{_cert_of(m.group(2))}" '
                f'href="../register/places.html#dare_{m.group(1)}" title="weitere Limesstelle (DARE)">{m.group(3)}</a>')
    def ent(m):
        tag, xid, attrs, txt = m.group(1), m.group(2), m.group(3), m.group(4)
        cls = "persName" if tag == "persName" else "placeName"
        if xid.startswith("psnN_"):   href = f"../register/namen.html#{xid}"
        elif xid.startswith("plcN_"): href = f"../register/orte-index.html#{xid}"
        else:                         href = f'../register/{"persons" if tag=="persName" else "places"}.html#{xid}'
        return f'<a class="ent {cls} c-{_cert_of(attrs)}" href="{href}" title="{cls}">{txt}</a>'
    body = re.sub(r'<placeName ref="dare:([^"]+)"([^>]*)>(.*?)</placeName>', dare, inner, flags=re.S)
    body = re.sub(r'<(persName|placeName) ref="#([^"]+)"([^>]*)>(.*?)</\1>', ent, body, flags=re.S)
    body = re.sub(r'<ref type="([^"]+)" target="#([^"]+)">(.*?)</ref>', _refsub, body, flags=re.S)   # Literatur/intern
    return body.replace("<lb/>", "<br>")               # Zeilenumbrüche (Inschriften/Korrekturen)

def render_page(inner):
    """inner = <cb/> + <p>…</p>-Block einer Spalte; Inline-Tags → HTML-Spans/Links."""
    inner = re.sub(r'<cb\b[^>]*/>', '', inner)          # Spaltenmarke aus dem Lesetext entfernen
    if "<gap" in inner: return '<p class="gap">[leere bzw. nicht erfasste Seite]</p>'
    return _entsub(inner)  # bereits <p>…</p>

def render_head(inner):
    """Volle-Breite-Überschrift (<head>) einer Kachel → eigene HTML-Zeile."""
    return f'<p class="colhead">{_entsub(inner.strip())}</p>'

def render_span(inner):
    """Spaltenübergreifender Fließtext-Absatz (<p rend="span">) → normaler Absatz."""
    return f'<p class="spanpara">{_entsub(inner.strip())}</p>'

PB_RE = re.compile(r'<head>(.*?)</head>'
                   r'|<p rend="span">(.*?)</p>'
                   r'|<pb n="([^"]*)" facs="#f_([^"]+)" xml:id="pb_[^"]*?_([A-Za-z0-9]+)" type="([^"]*)"/>'
                   r'(.*?)(?=<pb |<head>|<p rend|</div>)', re.S)

def load_volume(path):
    """Spalten-treues Laden: ein „Seiten"-Objekt je <pb> = je Spalte = je Druckseite.
    `img_tok` = IIIF-Kachel (Bild), `printed` = Druckseite, `col` = Spalte, `anchor` = tok-col."""
    t = open(path, encoding="utf-8").read()
    nr = int(re.search(r'limesblatt-bd(\d+)-', path).group(1))
    slug = re.search(r'limesblatt-bd\d+-(.+)\.xml', os.path.basename(path)).group(1)
    body = (re.search(r'<body>(.*)</body>', t, re.S) or re.search(r'(.*)', t, re.S)).group(1)
    pages, pending_head = [], ""
    for m in PB_RE.finditer(body):
        if m.group(1) is not None:                      # <head>…</head> vor den Spalten sammeln
            pending_head += render_head(m.group(1))
            continue
        if m.group(2) is not None:                      # spaltenübergreifender Absatz vor den Spalten
            pending_head += render_span(m.group(2))
            continue
        printed, img_tok, col, typ, inner = m.group(3), m.group(4), m.group(5) or "a", m.group(6) or "", m.group(7).strip()
        anchor = f"{img_tok}-{col}"
        pages.append({"img_tok": img_tok, "printed": printed, "col": col, "anchor": anchor, "tok": anchor,
                      "type": typ, "head": pending_head, "html": render_page(inner),
                      "text": unesc(strip_tags(re.sub(r'<cb\b[^>]*/>|<lb/>|</?p\b[^>]*>', ' ', inner))).strip(),
                      "ents": re.findall(r'ref="#([^"]+)"', inner),
                      "dents": re.findall(r'ref="dare:([^"]+)"', inner),
                      "cites": re.findall(r'target="#(bib_[^"]+)"', inner)})
        pending_head = ""
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

# Geokodierter Trassenverlauf je Strecke-Nr. (Wegpunkte lat,lon) — erlaubt die DARE-Stellen-Zuordnung
# nach *geografischer Nähe zur Trasse* statt zum nächsten Kastell (sonst bleiben kastelllose Abschnitte leer).
STRECKE_PATH = {
    1:  [(50.502, 7.327), (50.339, 7.713)],                 # Rheinbrohl–Bad Ems
    2:  [(50.339, 7.713), (50.137, 8.067)],                 # Bad Ems–Adolfseck
    3:  [(50.137, 8.067), (50.276, 8.618)],                 # Adolfseck–Köpperner Tal
    4:  [(50.276, 8.618), (50.232, 8.951)],                 # Köpperner Tal–Marköbel
    5:  [(50.232, 8.951), (50.084, 8.990)],                 # Marköbel–Groß-Krotzenburg
    6:  [(50.045, 8.973), (49.704, 9.264)],                 # Main-Linie Seligenstadt–Miltenberg
    7:  [(49.704, 9.264), (49.555, 9.065)],                 # Miltenberg–Rehberg
    8:  [(49.555, 9.065), (49.296, 9.489)],                 # Rehberg–Jagsthausen (Odenwald)
    9:  [(49.296, 9.489), (48.876, 9.620)],                 # Jagsthausen–Haghof
    10: [(49.797, 9.154), (49.231, 9.160)],                 # Wörth–Bad Wimpfen (ältere Odenwaldlinie)
    11: [(49.231, 9.160), (48.681, 9.366)],                 # Bad Wimpfen–Köngen (Neckarlinie)
    12: [(48.876, 9.620), (48.798, 9.689), (48.838, 10.093), (49.020, 10.381)],  # Haghof–Lorch–Aalen–Mönchsroth
    13: [(49.020, 10.381), (49.116, 10.754)],               # Mönchsroth–Gunzenhausen
    14: [(49.116, 10.754), (48.948, 11.385)],               # Gunzenhausen–Kipfenberg
    15: [(48.948, 11.385), (48.852, 11.772)],               # Kipfenberg–Eining
}
_LON = 0.65   # cos(≈49,5°): Längengrad-Stauchung für planare Distanz

def _p2seg(p, a, b):
    px, py = p[0], p[1] * _LON; ax, ay = a[0], a[1] * _LON; bx, by = b[0], b[1] * _LON
    dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

def _p2path(p, path):
    return min(_p2seg(p, path[i], path[i + 1]) for i in range(len(path) - 1)) if len(path) > 1 else _p2seg(p, path[0], path[0])

# Kuratierte Strecken-Kommissare je ORL-Strecke — aus den dokumentierten Rollen/Regionen der Personen-
# notizen erschlossen (Bodewig: unterer Lahn; Jacobi: Taunus; Wolff: Wetterau; Kofler: Hessen; Conrady:
# Odenwald; Schumacher: Baden; Herzog: Württemberg; Steimle: Strecke 12; Eidam: Gunzenhausen–Weißenburg;
# Leonhard: Bayern/raetisch). Kommissare betreuten mehrere Strecken → bewusst mehrfach zugeordnet.
STRECKE_KOMMISSAR = {
    1:  ["Robert Bodewig", "Wilhelm Soldan"],
    2:  ["Robert Bodewig", "Emil Ritterling"],
    3:  ["Louis Jacobi", "Heinrich Jacobi", "Emil Ritterling"],
    4:  ["Georg Wolff", "Friedrich Kofler"],
    5:  ["Georg Wolff", "Friedrich Kofler", "Wilhelm Soldan"],
    6:  ["Friedrich Kofler", "Wilhelm Conrady"],
    7:  ["Wilhelm Conrady"],
    8:  ["Wilhelm Conrady", "Karl Schumacher"],
    9:  ["Ernst von Herzog"],
    10: ["Karl Schumacher", "Wilhelm Conrady"],
    11: ["Ernst von Herzog", "Karl Schumacher"],
    12: ["Heinrich Steimle", "Ernst von Herzog"],
    13: ["Heinrich Eidam", "Friedrich Leonhard"],
    14: ["Heinrich Eidam", "Friedrich Leonhard"],
    15: ["Friedrich Leonhard"],
}

# ---------- HTML-Shell ----------
def page(title, body, depth=0, head=""):
    up = "../" * depth
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Limesblatt-Edition</title>
<link rel="stylesheet" href="{up}assets/style.css">{head}</head><body>
<header><a class="home" href="{up}index.html">📕 Limesblatt-Edition</a>
<nav><a href="{up}index.html">Bände</a> · <a href="{up}register/persons.html">Personen</a> · <a href="{up}register/places.html">Orte</a> · <a href="{up}register/strecken.html">Strecken</a> · <a href="{up}register/fundindex.html">Funde</a> · <a href="{up}register/inschriften.html">Inschriften</a> · <a href="{up}register/namen.html">Namen</a> · <a href="{up}register/bibliographie.html">Bibliographie</a> · <a href="{up}register/rezeption.html">Rezeption</a> · <a href="{up}register/orl.html">ORL</a> · <a href="{up}register/wortschatz.html">Analyse</a> · <a href="{up}index.html#suche">Suche</a> · <a href="{up}dokumentation.html">Dokumentation</a> · <a href="{up}edit.html" title="TEI-Quelle bearbeiten (GitHub-Login)">✎&#8201;Bearbeiten</a></nav></header>
<div class="wip">🚧 Diese digitale Edition befindet sich im <b>Aufbau</b> — Inhalte, Auszeichnung und Analysen sind unvollständig und können sich noch ändern.</div>
<main>{body}</main>
<footer>Diplomatische OCR-Edition des <em>Limesblatt</em> (1892–1903) · Text &amp; Register
<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a> · Seitenbilder © UB Heidelberg
(<a href="http://rightsstatements.org/vocab/InC/1.0/">In Copyright</a>, via IIIF verlinkt) ·
<a href="https://github.com/pleuston/limesblatt-edition">Quellcode &amp; TEI</a></footer></body></html>"""

def vol_page(v, toc=None):
    slug = v["slug"]
    teiname = f"limesblatt-bd{v['nr']}-{slug}.xml"
    images = []                                          # eindeutige IIIF-Kacheln (1 Bild je Blatt-Token)
    for p in v["pages"]:
        if p["img_tok"] not in images: images.append(p["img_tok"])
    tiles = [IIIF_INFO.format(slug=slug, tok=t) for t in images]
    tokidx = {t: i for i, t in enumerate(images)}
    tmap = {}
    for t, num, title, br, cf in (toc or []): tmap.setdefault(t, {})[num] = (title, br)
    text = []
    for img_tok, grp in groupby(v["pages"], key=lambda p: p["img_tok"]):
        cols = list(grp); i = tokidx[img_tok]
        if cols[0].get("head"): text.append(cols[0]["head"])
        nums = tmap.get(img_tok, {}); done = set(); seg = []
        for p in cols:
            lbl = ("S. " + html.escape(p["printed"])) if p["type"] in ("head", "inferred") \
                  else ("Bl. " + html.escape(p["img_tok"]) + " " + p["col"])
            mut = "" if p["type"] == "head" else " inferred"
            seg.append(f'<div class="pb{mut}" id="pb-{html.escape(p["anchor"])}" data-page="{i}" data-col="{p["col"]}" '
                       f'data-pb="pb_{html.escape(img_tok)}_{html.escape(p["col"])}" '
                       f'onclick="viewer.goToPage({i})" title="Faksimile (Blatt {html.escape(img_tok)}) zeigen">— {lbl} —</div>')
            ph = p["html"]
            if nums:
                def wrap(m):                             # Überschrift inline an ihrer echten Stelle markieren
                    n = int(m.group(1))
                    if n in nums and n not in done and not TOC_NOISE.match(m.group(2).strip()):
                        done.add(n)
                        return f'</p>\n<p class="artp"><b class="arthead" id="art-{n}">{m.group(0).strip()}</b> '
                    return m.group(0)
                ph = TOC_PAT.sub(wrap, ph)
            seg.append(ph)
        for n in nums:                                   # Fallback: nicht im Fließtext gefunden → Anker voranstellen
            if f'id="art-{n}"' not in "".join(seg):
                seg.insert(0, f'<p class="artp"><b class="arthead" id="art-{n}">{n}. {html.escape(nums[n][0])}</b></p>')
        text.extend(seg)
    head = ('<script src="../assets/openseadragon.min.js"></script>'
            f'<script>window.TEIFILE="tei/{teiname}";</script>'
            '<script defer src="../assets/pageedit.js"></script>')
    inh = ""
    if toc:
        items = toc_li(toc, "", True)
        inh = f'<details class="inhalt" open><summary>Inhalt — {len(toc)} nummerierte Berichte</summary><ul class="toc">{items}</ul></details>'
    body = f"""<h1>Limesblatt · {html.escape(v['label'])}</h1>
<p class="meta">IIIF-Faksimile: <a href="{IIIF_MAN.format(slug=slug)}">Manifest</a> (UB Heidelberg) ·
TEI: <a href="../tei/{teiname}">XML</a></p>
<p class="meta legend">Eigennamen im Text öffnen das Register · Konfidenz:
<span class="ent persName c-high">kuratiert + Normdaten</span> ·
<span class="ent persName c-medium">NER + Normdaten</span> ·
<span class="ent persName c-low">nur Lesung</span> · <span class="pb inferred" style="cursor:default">Druckseite erschlossen</span></p>
{inh}
<div class="reader">
  <div class="facs"><div id="osd"></div>
    <div class="osdnav"><button onclick="viewer.goToPage(Math.max(0,viewer.currentPage()-1))">‹ vorige</button>
    <span class="toggles"><label class="synctoggle" title="Das Faksimile folgt automatisch der Druckseite im Lesetext"><input type="checkbox" id="syncscroll" checked> Faksimile folgt</label>
    <label class="synctoggle" title="Original-Zeilenumbrüche des Drucks zeigen (sonst fließend)"><input type="checkbox" id="linebreaks" checked> Originalzeilen</label></span>
    <span id="pgind"></span><button onclick="viewer.goToPage(Math.min({len(tiles)-1},viewer.currentPage()+1))">nächste ›</button></div></div>
  <div class="text">{''.join(text)}</div>
</div>
<script>
var tiles = {json.dumps(tiles)};
var viewer = OpenSeadragon({{id:"osd", prefixUrl:"", tileSources:tiles, sequenceMode:true,
  showNavigationControl:false, showSequenceControl:false, gestureSettingsMouse:{{clickToZoom:false}}}});
function upd(){{document.getElementById("pgind").textContent=(viewer.currentPage()+1)+" / "+tiles.length;}}
function syncOn(){{var b=document.getElementById("syncscroll");return !b||b.checked;}}
var _slock=false;
viewer.addHandler("open", upd);
viewer.addHandler("page", function(ev){{           // Faksimile bewegt → Lesetext nachziehen
  upd();
  if(!syncOn()||_slock) return;
  var pb=document.querySelector('.reader .text .pb[data-page="'+ev.page+'"]');
  if(pb){{_slock=true; pb.scrollIntoView({{behavior:"smooth",block:"start"}}); setTimeout(function(){{_slock=false;}},700);}}
}});
(function(){{                                       // Lesetext gescrollt → Faksimile folgt (IntersectionObserver)
  var pane=document.querySelector('.reader .text');
  if(!pane||!('IntersectionObserver' in window)) return;
  var io=new IntersectionObserver(function(es){{
    if(!syncOn()||_slock) return;
    es.forEach(function(e){{
      if(e.isIntersecting){{
        var p=parseInt(e.target.getAttribute('data-page'));
        if(p>=0 && p!==viewer.currentPage()){{_slock=true; viewer.goToPage(p); setTimeout(function(){{_slock=false;}},350);}}
      }}
    }});
  }},{{root:pane, rootMargin:"0px 0px -82% 0px", threshold:0}});
  pane.querySelectorAll('.pb[data-page]').forEach(function(pb){{io.observe(pb);}});
}})();
(function(){{                                       // „Originalzeilen" ein/aus → Druck-Zeilenumbrüche zeigen/fließend
  var lb=document.getElementById("linebreaks"), pane=document.querySelector('.reader .text');
  if(lb&&pane){{var f=function(){{pane.classList.toggle("flow", !lb.checked);}}; lb.addEventListener("change",f); f();}}
}})();
(function(){{          // Fund-/Register-Sprung: ?hl=Wort → exakte Fundstelle im Lesetext markieren + anspringen
  var hl=new URLSearchParams(location.search).get('hl'); if(!hl) return;
  var pane=document.querySelector('.reader .text'); if(!pane) return;
  var hlL=hl.toLowerCase();
  var start=location.hash?document.getElementById(decodeURIComponent(location.hash.slice(1))):null;
  var el=start?start.nextElementSibling:pane.firstElementChild, nodes=[];
  while(el){{ if(el.classList&&el.classList.contains('pb')) break; if(el.tagName==='P') nodes.push(el); el=el.nextElementSibling; }}
  if(!nodes.length) nodes=[].slice.call(pane.querySelectorAll('p'));
  for(var i=0;i<nodes.length;i++){{
    var tw=document.createTreeWalker(nodes[i],NodeFilter.SHOW_TEXT,null), tn;
    while((tn=tw.nextNode())){{
      var idx=tn.nodeValue.toLowerCase().indexOf(hlL);
      if(idx>=0){{
        try{{
          var r=document.createRange(); r.setStart(tn,idx); r.setEnd(tn,idx+hl.length);
          var mk=document.createElement('mark'); mk.className='findhl'; r.surroundContents(mk);
          setTimeout(function(){{mk.scrollIntoView({{behavior:'smooth',block:'center'}});}},60);
        }}catch(e){{}}
        return;
      }}
    }}
  }}
}})();
</script>"""
    return body, head

def beleg_html(eid, occ):
    """Rück-Links Register → Volltext-Fundstellen (Seite + Spalte), nach Band gruppiert."""
    items = occ.get(eid, [])
    if not items: return '<span class="meta">—</span>'
    out = []
    for vol, grp in groupby(items, key=lambda x: x[0]):
        seen, links = set(), []
        for _, anchor, printed in grp:
            if anchor in seen: continue
            seen.add(anchor)
            links.append(f'<a href="../volumes/bd{vol}.html#pb-{html.escape(anchor)}">{html.escape(printed)}</a>')
        out.append(f'Bd.&#160;{vol}: {", ".join(links)}')
    return " · ".join(out)

def links_line(parts):
    return ('<div class="links">' + " · ".join(p for p in parts if p) + '</div>') if any(parts) else ""

def persons_page(persons, occ, digs):
    rows = []
    for p in sorted(persons, key=lambda r: r["name"].split()[-1]):
        I = p["idno"]
        thumb = f'<img class="pthumb" src="{html.escape(I["portrait"])}" alt="" loading="lazy">' if I.get("portrait") else ""
        dts = f'<span class="dts">{html.escape(p["birth"])}–{html.escape(p["death"])}</span>' if (p["birth"] or p["death"]) else ""
        al  = f'<div class="alias">alias {html.escape(", ".join(p["alias"]))}</div>' if p.get("alias") else ""
        name = f'{thumb}<b>{html.escape(p["name"])}</b>{(" " + dts) if dts else ""}{al}'
        rolle = html.escape(p["occ"]) or '<span class="meta">—</span>'
        wirk = " · ".join(x for x in [
            html.escape(p["residence"]) if p.get("residence") else "",
            ("Strecke " + html.escape(p["strecke"])) if p.get("strecke") else "",
            ("🗄️ " + html.escape(p["nachlass"])) if p.get("nachlass") else ""] if x) or '<span class="meta">—</span>'
        kal = ""
        if I.get("Kalliope"):
            br = f' ({html.escape(p["briefe"])} Br.)' if p.get("briefe") else ""
            kal = f'<a href="https://kalliope-verbund.info/gnd/{html.escape(I["Kalliope"])}">Kalliope{br}</a>'
        norm = " · ".join(x for x in [
            f'<a href="https://d-nb.info/gnd/{html.escape(I["GND"])}">GND</a>' if I.get("GND") else "",
            f'<a href="https://www.wikidata.org/wiki/{html.escape(I["Wikidata"])}">Wikidata</a>' if I.get("Wikidata") else "",
            f'<a href="{html.escape(I["DeutscheBiographie"])}">Dt. Biogr.</a>' if I.get("DeutscheBiographie") else "",
            f'<a href="{html.escape(I["Propylaeum-VITAE"])}">VITAE</a>' if I.get("Propylaeum-VITAE") else "", kal] if x) or '<span class="meta">—</span>'
        bl = []
        forts = digs.get(p["id"], [])
        if forts:
            bl.append("⛏️ " + ", ".join(f'<a href="places.html#{f["id"]}">{html.escape(f["name"])}</a>' for f in forts))
        bel = beleg_html(p["id"], occ)
        if "—" not in bel: bl.append("📄 " + bel)
        belc = "<br>".join(bl) or '<span class="meta">—</span>'
        rows.append(f'<tr id="{p["id"]}"><td class="pn">{name}</td><td>{rolle}</td><td>{wirk}</td>'
                    f'<td class="nd">{norm}</td><td class="beleg">{belc}</td></tr>')
    return (f'<h1>Personenregister</h1><p class="meta">{len(persons)} kuratierte Personen der RLK-Forschungs'
            f'geschichte — mit Lebensdaten, Funktion, Normdaten, Korrespondenz/Nachlass, ausgegrabenen Kastellen '
            f'und Volltext-Fundstellen. Alle im Limesblatt namentlich genannten Personen (NER, mehrere hundert) '
            f'stehen im <a href="namen.html">Namenregister</a>.</p>'
            f'<table class="reg pers"><thead><tr><th>Person (Lebensdaten)</th><th>Rolle&#8201;/&#8201;Funktion</th>'
            f'<th>Wirkungsort&#8201;/&#8201;Nachlass</th><th>Normdaten</th><th>Belege</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')

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
                lk = ", ".join(f'<a href="../volumes/bd{v}.html#pb-{html.escape(a)}">{v}/{html.escape(pp)}</a>' for v, a, pp in hh[:3])
                vt = f' · 📄 {len(hh)}× ({lk}{" +"+str(len(hh)-3) if len(hh) > 3 else ""})'
            lis.append(f'<li id="dare_{did}">{html.escape(p.get("name", "?"))}{anc}{dare}{foc}{vt}</li>')
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

def strecken_page(strecken, str_forts, persons, pname, strecke_sites, orl_idx, volumes):
    byname = {p["name"]: p for p in persons}
    slug2nr = {v["slug"]: v["nr"] for v in volumes}
    def _core(k):
        k = re.sub(r"^(Kleinkastell|Kastelle von|Kastell|Kastelle)\s+", "", k or "").lower()
        return re.sub(r"[^a-zäöüß0-9]", "", k)
    kastB = {}
    for r in orl_idx.get("abteilung_B_kastelle", []):
        kastB.setdefault(_core(r["kastell"]), r)
    strA = {str(a.get("strecke")): a for a in orl_idx.get("abteilung_A_strecken", [])}
    cards = []
    for s in strecken:
        forts = str_forts.get(s["id"], [])
        fl = ", ".join(f'<a href="places.html#{f["id"]}">{html.escape(f["name"])}</a>' for f in forts) or '<span class="meta">—</span>'
        nr = int(s["nummer"]) if s.get("nummer", "").strip().isdigit() else 0
        komm = [byname[n] for n in STRECKE_KOMMISSAR.get(nr, []) if n in byname]
        dig_ids = []
        for f in forts:
            for d in f.get("diggers", []):
                if d in pname and d not in dig_ids: dig_ids.append(d)
        meta = " · ".join(x for x in [html.escape(s["verlauf"]), html.escape(s["region"]), html.escape(s["abschnitt"])] if x)
        extra = f'<div class="x">⛏️ Kastelle: {fl}</div>'
        # --- Limesblatt (Vorbericht) ↔ ORL (Endpublikation) nebeneinander ---
        orlB, seenB, vols = [], set(), {}
        for f in forts:
            r = kastB.get(_core(f["name"]))
            if r and r["nr"] not in seenB:
                seenB.add(r["nr"]); orlB.append(r)
                for vb in r.get("vorberichte", []):
                    n = slug2nr.get(vb.get("slug"))
                    if n: vols[n] = vols.get(n, 0) + 1
        if vols:
            bl = ", ".join(f'<a href="../volumes/bd{n}.html">Bd. {n}</a>' for n in sorted(vols))
            lb_html = f'{bl} <span class="meta">({sum(vols.values())} Berichte zu diesen Kastellen)</span>'
        else:
            lb_html = '<span class="meta">— kein zugeordneter Feldbericht</span>'
        sn = str(nr) if nr else ""
        op = []
        if strA.get(sn): op.append(f'<a href="orl.html#orl-a-{sn}">Strecken-Band (Abt.&#8201;A)</a>')
        if orlB: op.append("Lieferungen " + ", ".join(f'<a href="orl.html#orl-{r["nr"]}">ORL&#8201;{r["nr"]}</a>' for r in orlB))
        orl_html = " · ".join(op) or '<span class="meta">—</span>'
        extra += (f'<div class="x" style="display:grid;grid-template-columns:1fr 1fr;gap:.4em .9em;'
                  f'border-left:3px solid #cbb;padding-left:.7em;margin:.35em 0">'
                  f'<div>📄 <b>Limesblatt</b> · Vorbericht<br>{lb_html}</div>'
                  f'<div>📗 <b>ORL</b> · Endpublikation<br>{orl_html}</div></div>')
        ds, seen_n = [], set()
        for x in sorted(strecke_sites.get(s["id"], []), key=lambda x: x.get("name", "")):
            n = x.get("name", "?")
            if n not in seen_n: seen_n.add(n); ds.append(x)
        if ds:
            shown = ", ".join(f'<a href="places.html#dare_{html.escape(str(x.get("id","")))}">{html.escape(x.get("name","?"))}</a>' for x in ds[:24])
            extra += f'<div class="x">○ Türme/Stellen (DARE, {len(ds)}): {shown}{" +"+str(len(ds)-24) if len(ds) > 24 else ""}</div>'
        if forts: extra += f'<div class="x">🗺️ <a href="places.html?strecke={s["id"]}">Auf der Karte zeigen</a></div>'
        bet = []
        if komm: bet.append("Streckenkommissar: " + ", ".join(
            f'<a href="persons.html#{p["id"]}">{html.escape(p["name"])}</a>' for p in komm))
        if dig_ids: bet.append("Ausgräber: " + ", ".join(
            f'<a href="persons.html#{d}">{html.escape(pname[d])}</a>' for d in dig_ids))
        if bet: extra += '<div class="x">👤 Beteiligte — ' + " · ".join(bet) + '</div>'
        cards.append(f'<article class="card wide" id="{s["id"]}"><div class="cbody">'
                     f'<h3>{html.escape(s["name"])}</h3><div class="role">{meta}</div>{extra}</div></article>')
    return (f'<h1>Strecken</h1><p class="meta">{len(strecken)} Limes-Abschnitte, je mit den Kastellen, den '
            f'zugehörigen <b>Limesblatt-Bänden</b> (Vorbericht) und der <b>ORL</b>-Endpublikation nebeneinander, '
            f'den beteiligten Personen und den DARE-Stellen entlang der Linie. Die Turmstellen sind über den '
            f'geokodierten Trassenverlauf dem nächsten Abschnitt zugeordnet (≤ ~15&#8239;km); in Doppellinien-Zonen '
            f'näherungsweise.</p>'
            f'<div class="cards">{"".join(cards)}</div>')

def index_page(volumes, toc=None):
    toc = toc or {}
    bl = []
    for v in volumes:
        ents = toc.get(v["nr"], [])
        items = toc_li(ents, f'volumes/bd{v["nr"]}.html', False)
        sub = f'<ul class="toc idxtoc">{items}</ul>' if items else ""
        bl.append(f'<li><a href="volumes/bd{v["nr"]}.html"><b>{html.escape(v["label"])}</b></a> '
                  f'<span class="meta">— {len(v["pages"])} Seiten · {len(ents)} Berichte</span>{sub}</li>')
    lis = "".join(bl)
    head = '<script src="assets/minisearch.min.js"></script>'
    body = f"""<h1>Limesblatt — digitale Edition</h1>
<p class="lede">Die <em>Mitteilungen der Streckenkommissare bei der Reichs-Limeskommission</em>
(1892–1903): die laufenden Feldberichte der Limesforschung, als diplomatische OCR-Edition mit
IIIF-Faksimiles (UB Heidelberg) und mit GND-/Wikidata-/Geo-verknüpften Personen- und Ortsregistern.</p>
<section id="suche"><h2>Volltextsuche</h2>
<input id="q" type="search" placeholder="z. B. Saalburg, Entschädigung, Mommsen …" autocomplete="off">
<div id="res"></div></section>
<h2>Bände &amp; Inhaltsverzeichnisse</h2><ul class="bandlist">{lis}</ul>
<h2>Register</h2><ul><li><a href="register/persons.html">Personenregister</a> — mit Porträts, Normdaten, Korrespondenz, ausgegrabenen Kastellen</li>
<li><a href="register/places.html">Ortsregister</a> — mit Karte, Kastelltyp, Ausgräber, Inschriften</li>
<li><a href="register/strecken.html">Strecken</a> — die 15 Limes-Abschnitte mit Kastellen &amp; Kommissaren</li>
<li><a href="register/fundindex.html">Fundindex</a> — Fundgattungen, Münzkaiser, Sigillata-Formen &amp; Truppenstempel mit Seiten-/Spalten-Belegen</li>
<li><a href="register/inschriften.html">Inschriften (EDH)</a> — 759 katalogisierte Inschriften der Limes-Fundorte aus der Epigraphic Database Heidelberg</li>
<li><a href="register/bibliographie.html">Bibliographie &amp; Quellen</a> — die zitierten Werke, aufgelöst zu vollen Referenzen + Open-Access-Digitalisaten (UB Heidelberg u. a.)</li>
<li><a href="register/rezeption.html">Rezeption &amp; Wirkungsgeschichte</a> — wie das Limesblatt außerhalb seiner Bände rezipiert wurde (token-frei aus OpenAlex/Crossref/archive.org/DAI-Zenon)</li>
<li><a href="register/namen.html">Namen im Limesblatt</a> — vollständiges Namenregister aus dem Volltext (NER); jeder Name ist im Lesetext angeklickt verlinkt</li>
<li><a href="register/orte-index.html">Orte im Limesblatt</a> — vollständiges Ortsregister aus dem Volltext (NER), im Lesetext verlinkt</li>
<li><a href="register/wortschatz.html">Textanalyse</a> — diachroner Wortschatz, ORL-Gegenprobe, Münzkaiser-Chronologie, Truppen, Zitate, OCR-Qualität + KWIC-Konkordanz</li></ul>
<h2>ORL — die Endpublikation</h2>
<p class="meta">Das Standardwerk, in das das Limesblatt mündete, token-frei über HathiTrust erschlossen.</p>
<ul><li><a href="register/orl.html">ORL-Bandindex</a> — Abteilung A (Strecken) + B (Kastell-Lieferungen) mit Seitenzahl, Charakteristik, Sigillata-Score und Vorbericht-Verweisen</li>
<li><a href="register/orl-register.html">ORL-Gesamtapparat</a> — Personen- &amp; Ortsregister über alle Bände, Terra-Sigillata-Apparat, Vorbericht→ORL-Konkordanz</li>
<li><a href="register/hathitrust.html">HathiTrust — Werkzeuge &amp; Ertrag</a> — wie der ORL token-frei und nicht-konsumtiv erschlossen wurde (Workset · Extracted Features · NER · Data Capsule)</li></ul>
<p class="meta">→ <a href="dokumentation.html"><b>Dokumentation</b></a>: was auf dieser Website steht, wie wir an die Daten kamen und was sie sagen.</p>
<p class="meta">Abgeleitet aus dem (privaten) Forschungs-Vault zur <a href="https://github.com/pleuston/limes">Reichs-Limeskommission</a>.
Edition/Code: <a href="https://github.com/pleuston/limesblatt-edition">GitHub</a>.</p>
<script>
fetch("data/search.json").then(r=>r.json()).then(docs=>{{
 var ms=new MiniSearch({{fields:["text"],storeFields:["vol","anchor","pp","label"]}}); ms.addAll(docs);
 var q=document.getElementById("q"),res=document.getElementById("res");
 q.addEventListener("input",function(){{
  var v=q.value.trim(); if(v.length<3){{res.innerHTML="";return;}}
  var hits=ms.search(v,{{prefix:true,fuzzy:.1}}).slice(0,40);
  res.innerHTML=hits.length?hits.map(function(h){{
    var t=h.text||""; var i=t.toLowerCase().indexOf(v.toLowerCase());
    var sn=i<0?t.slice(0,140):t.slice(Math.max(0,i-50),i+90);
    return '<a class="hit" href="volumes/bd'+h.vol+'.html#pb-'+h.anchor+'">'+h.label+', S. '+h.pp+'</a> <span>…'+
      sn.replace(/</g,"&lt;")+'…</span>';}}).join(""):"<p class=meta>keine Treffer</p>";
 }});
}});
</script>"""
    return body, head

def page_links(pages, tok2anchor):
    """NER-Seitenrefs ("Bd.7 S.883") → Link auf die erste Spalte der Kachel (Token-Granularität)."""
    out = []
    for s in pages:
        m = re.match(r'Bd\.(\d+)\s+S\.(\S+)', s)
        if not m: continue
        vol, tok = int(m.group(1)), m.group(2)
        a = tok2anchor.get((vol, tok))
        if a:
            out.append(f'<a href="../volumes/bd{vol}.html#pb-{html.escape(a)}">{vol}/{html.escape(tok)}</a>')
    return ", ".join(out)

def ner_index_page(items, what, tok2anchor, recon):
    lab = "Namen" if what == "persons" else "Orte"
    rows = 0; matched = 0; lis = []
    for it in items:
        pl = page_links(it.get("pages", []), tok2anchor)
        if not pl: continue
        nm = it["name"]; r = recon.get(nm.lower()); disp = html.escape(nm); ref = ""
        if what == "persons":
            extra = " · ".join(it.get("roles", [])[:2])
            if r and r.get("src") == "reg":                    # kuratierte RLK-Figur → interner Eintrag
                disp = f'<a href="../register/persons.html#p_{r["slug"]}">{html.escape(nm)}</a>'
                if r.get("gnd"): ref = f' <a class="meta" href="https://d-nb.info/gnd/{r["gnd"]}">GND</a>'
                matched += 1
            elif r and r.get("src") == "gnd":                  # lobid-Vollnamen-Treffer
                t = html.escape(f'{r.get("gndName","")} {r.get("von","")}–{r.get("bis","")} · {r.get("prof","")}')
                ref = f' <a class="meta" href="https://d-nb.info/gnd/{r["gnd"]}" title="{t}">GND ✓</a>'
                matched += 1
            else:
                ref = f' <a class="meta" href="https://lobid.org/gnd/search?q={html.escape(nm)}&amp;format=html">GND?</a>'
        else:
            extra = it.get("kind", "")
            if r and r.get("gazId"):                           # iDAI-Gazetteer (Authority + Koordinaten)
                ref = f' <a class="meta" href="https://gazetteer.dainst.org/place/{r["gazId"]}">iDAI</a>'; matched += 1
            elif r and r.get("geo"):                           # nur Koordinaten (OSM)
                la, lo = r["geo"]
                ref = f' <a class="meta" href="https://www.openstreetmap.org/?mlat={la}&amp;mlon={lo}#map=13/{la}/{lo}">Karte</a>'; matched += 1
        em = f' <span class="meta">· {html.escape(extra)}</span>' if extra else ""
        lc = ' lc' if it.get("cert") != "high" else ""
        eid = ("psnN_" if what == "persons" else "plcN_") + gazetteer.slug(gazetteer._primary(nm)[0])
        lis.append(f'<li id="{eid}" class="ix{lc}"><b>{disp}</b>{em}{ref} — <span class="pgs">{pl}</span></li>')
        rows += 1
    rec = (f'<b>{matched}</b> mit GND bzw. dem Personenregister verknüpft' if what == "persons"
           else f'<b>{matched}</b> über iDAI-Gazetteer/Koordinaten verortet')
    head = ('<script>function nflt(q){q=q.toLowerCase();var n=0,L=document.querySelectorAll("#nerlist>li");'
            'L.forEach(function(li){var m=li.textContent.toLowerCase().indexOf(q)>=0;li.style.display=m?"":"none";if(m)n++;});'
            'document.getElementById("ncount").textContent=n;}</script>')
    body = (f'<h1>{lab} im Limesblatt</h1>'
            f'<p class="meta">{rows} {lab}, per <b>LLM-NER</b> aus dem gesamten Volltext extrahiert '
            f'(heuristisch, Fraktur-OCR; <span class="lc">grau = unsichere OCR-Lesung</span>) — token-frei {rec}. '
            f'Tippen filtert die Liste; die Zahlen springen ins Faksimile.</p>'
            f'<input type="search" placeholder="filtern… (z. B. Pfarrer, Förster, Mühle, Wald)" oninput="nflt(this.value)" '
            f'style="width:100%;padding:.5rem .7rem;border:1px solid var(--line);border-radius:4px;font:inherit">'
            f'<p class="meta"><span id="ncount">{rows}</span> angezeigt</p>'
            f'<ul id="nerlist" class="nerlist">{"".join(lis)}</ul>')
    return body, head

TM_GROUPS = {  # Anzeige -> (regex, Chart-Farbe|None)
    "Grabungsmethode": (r"(sondir\w*|sondier\w*|schnitt\w*|suchgraben\w*|durchschnitt\w*|profil\w*|anschnitt\w*|planum)", "#3060c0"),
    "Stratigraphie":   (r"(schicht\w*|brandschicht\w*|ablagerung\w*|aufschüttung\w*)", "#7a3fae"),
    "Holzbefund":      (r"(holz\w*|hölzern\w*|pfahl|pfähle|pfosten\w*|balken|fachwerk\w*)", "#1f7a4d"),
    "Steinbau":        (r"(mauer\w*|fundament\w*|mörtel\w*|estrich\w*)", "#b3331a"),
    "Funde – Sigillata":         (r"(sigillata|sigillaten)", None),
    "Funde – Münzen":            (r"(münze\w*|münz\w*)", None),
    "Funde – Inschrift/Stempel": (r"(inschrift\w*|ziegelstempel\w*|töpferstempel\w*|stempel\w*)", "#b07d20"),
    "Datierung":                 (r"(datir\w*|datier\w*|chronolog\w*|zeitstellung\w*)", None),
}
TM_KWIC = ["sigillata", "münze", "brandschicht", "ziegelstempel", "pfosten", "fibel", "mörtel"]
TM_YEARS = {1:"1892/93",2:"1893/94",3:"1894/95",4:"1896",5:"1897",6:"1897/98",7:"1898–1902",8:"1903"}

def tm_norm(t):
    t = t.replace("ſ","s"); t = re.sub(r"(\w)[-¬]\s*\n\s*(\w)", r"\1\2", t); return re.sub(r"\s+"," ",t)

def analysis_sections(volumes, orl_lex=None):
    """Befunde aus dem Volltext (+ ORL-Cache des Vaults) für die öffentliche Seite."""
    CACHE = os.path.join(REPO, "..", "limes", "tools", ".cache")
    BANDS = [("limesblatt1892_1893", "Bd. 1 (1892/93)"), ("limesblatt1893_1894", "Bd. 2 (1893/94)"),
             ("limesblatt1894_1895", "Bd. 3 (1894/95)"), ("limesblatt1896", "Bd. 4 (1896)"),
             ("limesblatt1897", "Bd. 5 (1897)"), ("limesblatt1897_1898", "Bd. 6 (1897/98)"),
             ("limesblatt1898_1902", "Bd. 7 (1898–1902)"), ("limesblatt1903", "Bd. 8 (1903)")]
    if not os.path.isdir(os.path.join(CACHE, BANDS[0][0])): return ""   # OCR-Cache (Vault) nicht vorhanden
    corp = {}
    for slug, label in BANDS:
        pg = []
        for fp in sorted(glob.glob(os.path.join(CACHE, slug, "*.txt"))):
            tok = os.path.splitext(os.path.basename(fp))[0]
            if not re.match(r'^\d+$', tok): continue
            pg.append((tok, tm_norm(open(fp, encoding="utf-8", errors="replace").read())))
        corp[label] = pg
    low = " ".join(t for _, lab in BANDS for _, t in corp[lab]).lower()
    def W(t): return max(1, len(re.findall(r"[a-zäöüß]+", t)))
    def rt(t, rx): return 1000.0 * len(re.findall(rx, t, re.I)) / W(t)
    out = []
    # ORL-Gegenprobe (ORL-Band aus dem Vault-Cache)
    orlp = os.path.join(CACHE, "orl", "derobergermanis00fabrgoog.txt")
    if os.path.exists(orlp):
        orl = tm_norm(open(orlp, encoding="utf-8", errors="replace").read()).lower()
        i = orl.find("osterburken"); orl = orl[i - 100:] if i > 0 else orl
        ost = " ".join(t.lower() for _, lab in BANDS for _, t in corp[lab] if "osterburken" in t.lower())
        cols = [("ORL·Osterburken", orl), ("LB·Osterburken", ost), ("LB·gesamt", low)]
        keep = ["Grabungsmethode", "Holzbefund", "Steinbau", "Funde – Münzen", "Funde – Inschrift/Stempel"]
        rows = "".join(f"<tr><td>{html.escape(g)}</td>" + "".join(f"<td>{rt(t, TM_GROUPS[g][0]):.1f}</td>" for _, t in cols) + "</tr>" for g in keep)
        out.append('<h2 id="orl">ORL-Gegenprobe (Osterburken)</h2>'
                   '<p class="meta">Derselbe Standort: der polierte ORL-Band (Schumacher 1895) gegen die Limesblatt-Osterburken-Seiten (Treffer je 1000 Wörter).</p>'
                   '<table class="reg tm"><tr><th>Term-Gruppe</th>' + "".join(f"<th>{html.escape(c[0])}</th>" for c in cols) + f'</tr>{rows}</table>'
                   '<p class="meta">Für dasselbe Kastell nennt das ORL <b>Holzbefunde ~4× seltener</b> als die Feldberichte — die Ausdünnung der Holz-Erde-Evidenz ist <b>editorial</b>, nicht feldbedingt.</p>')
    # Wortschatz-Gegenprobe über das ganze Werk (Keyness, aus orl_vs_limesblatt.json)
    if orl_lex:
        od = orl_lex.get("orl_distinctive", [])[:20]; ld = orl_lex.get("lb_distinctive", [])[:20]
        def _kr(items): return "".join(f'<tr><td>{html.escape(d["w"])}</td><td>{d.get("log2",0):+.1f}</td>'
                                       f'<td>{d.get("orl_per10k",0):.1f}</td><td>{d.get("lb_per10k",0):.1f}</td></tr>' for d in items)
        _kh = '<tr><th>Wort</th><th>Log2</th><th>ORL/10k</th><th>LB/10k</th></tr>'
        out.append(
            f'<h2 id="gegenprobe">Wortschatz-Gegenprobe: das ganze Werk (Limesblatt ↔ ORL)</h2>'
            f'<p class="meta">Nicht nur ein Band: der <b>gesamte</b> ORL-Korpus ({orl_lex.get("orl_words",0):,} Wörter) '
            f'gegen das ganze Limesblatt ({orl_lex.get("lb_words",0):,}). Gezeigt sind die Wörter, die jedes Werk am '
            f'stärksten kennzeichnen — als Log2 des Verhältnisses ihrer relativen Häufigkeiten (pro 10 000 Wörter). '
            f'Der Befund ist ein <b>Wechsel der Textsorte</b>, keine bloße Straffung: die Feldberichte spüren die '
            f'Grenzlinie auf und stecken sie ab — in der Ich-Form des Ausgräbers, voller Geländevokabular; die '
            f'Endpublikation katalogisiert die Funde, mit dem ganzen Apparat der Keramik-Typologie.</p>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1em">'
            f'<div><b>Distinktiv für den ORL</b> — Fund-Typologie<table class="reg tm">{_kh}{_kr(od)}</table></div>'
            f'<div><b>Distinktiv für das Limesblatt</b> — Trassierung, erste Person<table class="reg tm">{_kh}{_kr(ld)}</table></div>'
            f'</div>'
            f'<p class="meta">Caveat: die Formen stammen aus der maschinellen Umschrift der Frakturschrift; '
            f'Kürzungen wie „dragd" stehen für „Dragendorff". Die Richtung des Befunds ist davon unberührt.</p>')
    # Münzkaiser-Chronologie
    EMP = [("Vespasian", r"vespasian"), ("Domitian", r"domitian"), ("Trajan", r"tra[ij]an"), ("Hadrian", r"hadrian(?!swall)"),
           ("Ant. Pius", r"antoninus|antonin\b"), ("Marc Aurel", r"marc\W{0,2}aurel|marcus aurel"), ("Commodus", r"commodus"),
           ("Sept. Severus", r"septimius|sept\. sever"), ("Caracalla", r"caracalla"), ("Sev. Alexander", r"severus alexander"),
           ("Gordian", r"gordian"), ("Philippus", r"philippus\b"), ("Gallienus", r"gallienus"), ("Probus", r"\bprobus\b")]
    epages = [t for _, lab in BANDS for _, t in corp[lab]]
    ec = [(n, sum(1 for t in epages if re.search(rx, t, re.I))) for n, rx in EMP]; mx = max([c for _, c in ec] + [1])
    bars = "".join(f'<div class="attrow"><span class="attlabel">{html.escape(n)}</span><span class="attbar" style="width:{100*c/mx:.0f}%"></span><span class="attval">{c}</span></div>' for n, c in ec)
    out.append('<h2 id="muenzen">Münzkaiser-Chronologie</h2><p class="meta">Kaisernennungen (Münz-/Datierungsevidenz), chronologisch — bildet die Limes-Belegung ab: flavisch-trajanische Errichtung, severischer Sekundärpeak, Auslaufen vor 260.</p>'
               f'<div class="attwrap">{bars}</div>')
    # Truppen
    ROM = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
    def r2i(s):
        if not s or any(c not in ROM for c in s): return None
        t = pv = 0
        for c in reversed(s): v = ROM[c]; t += -v if v < pv else v; pv = max(pv, v)
        return t if 1 <= t <= 30 else None
    def i2r(n):
        o = ""
        for v, sy in [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]:
            while n >= v: o += sy; n -= v
        return o
    legs = defaultdict(int)
    for m in re.finditer(r"\bleg(?:\.|io\b|\.?\s)\s*([ivxlc]{1,6})\b", low):
        v = r2i(m.group(1))
        if v: legs[v] += 1
    legtxt = ", ".join(f"Legio {i2r(v)} ({c})" for v, c in sorted(legs.items(), key=lambda x: -x[1])[:5])
    out.append(f'<h2 id="truppen">Truppen</h2><p class="meta">Häufigste Legionsnennungen (Stempel/Text): <b>{legtxt}</b> — erwartungsgemäß dominiert <b>Legio XXII Primigenia</b> (Mainz).</p>')
    # Zitate
    jour = [("Westdeutsche Zeitschrift", r"westd\w*\.?\s*(?:zeitschr|ztschr|z\.)"), ("Korrespondenzblatt", r"korr\w*\.?\s*-?\s*bl"), ("Bonner Jahrbücher", r"bonn\w*\.?\s*jahrb")]
    jrows = "".join(f"<tr><td>{html.escape(n)}</td><td>{len(re.findall(rx, low, re.I))}</td></tr>" for n, rx in jour)
    dragn = len(re.findall(r"\bdrag(?:endorff)?\.?\s*\d", low, re.I)); dragd = len(set(re.findall(r"\bdrag(?:endorff)?\.?\s*(\d{1,3}[a-z]?)\b", low, re.I)))
    bram = set()
    for m in re.finditer(r"brambach\s+(?:nr\.?\s*)?(\d{2,4}(?:\s*[.,]\s*\d{2,4})*)", low, re.I): bram |= set(re.findall(r"\d{2,4}", m.group(1)))
    out.append('<h2 id="zitate">Zitate & Verweise</h2><p class="meta">Die Verweis-Apparatur ist <b>journal-</b>, nicht corpus-zentriert (die formale Inschriftenkonkordanz wandert erst ins ORL).</p>'
               f'<table class="reg tm"><tr><th>Quelle</th><th>Verweise</th></tr>{jrows}'
               f'<tr><td>Dragendorff-Sigillataformen</td><td>{dragn} ({dragd} versch.)</td></tr>'
               f'<tr><td>Brambach-Inschriften</td><td>{len(bram)} Nummern</td></tr></table>')
    # OCR-Qualität
    tpp = {}; gc = defaultdict(int)
    for slug, label in BANDS:
        for tok, t in corp[label]:
            ts = re.findall(r"[a-zäöüß]{3,}", t.lower()); tpp[(label, tok)] = ts
            for w in ts: gc[w] += 1
    good = {w for w, c in gc.items() if c >= 5}
    qrows = ""
    for slug, label in BANDS:
        qs = [sum(1 for w in tpp[(label, tok)] if w in good) / len(tpp[(label, tok)]) for tok, t in corp[label] if len(tpp[(label, tok)]) >= 25]
        if qs: qrows += f"<tr><td>{html.escape(label)}</td><td>{100*sum(qs)/len(qs):.1f} %</td></tr>"
    out.append('<h2 id="ocr">OCR-Qualität</h2><p class="meta">Proxy = Anteil im Korpus wiederkehrender Wörter (Garble ist meist Unikat) — ~85 % gleichmäßig über die Bände.</p>'
               f'<table class="reg tm"><tr><th>Band</th><th>Ø-Qualität</th></tr>{qrows}</table>')
    return "".join(out)

def wortschatz_page(volumes, attention=None, orl_lex=None):
    bands = sorted(volumes, key=lambda v: v["nr"]); nrs = [v["nr"] for v in bands]
    texts = {v["nr"]: tm_norm(" ".join(p["text"] for p in v["pages"] if p.get("text"))).lower() for v in bands}
    words = {nr: max(1, len(re.findall(r"[a-zäöüß]+", t))) for nr, t in texts.items()}
    rates = {g: {nr: 1000.0*len(re.findall(rx, texts[nr], re.I))/words[nr] for nr in nrs} for g,(rx,c) in TM_GROUPS.items()}
    # SVG-Liniendiagramm (nur eingefärbte Gruppen)
    plotted = [(g,c) for g,(rx,c) in TM_GROUPS.items() if c]
    W,H,PL,PR,PT,PB = 720,300,40,168,14,42
    mx = max([rates[g][nr] for g,_ in plotted for nr in nrs] + [1])
    X = lambda i: PL+(W-PL-PR)*i/(len(nrs)-1); Yv = lambda v: PT+(H-PT-PB)*(1-v/mx)
    svg = [f'<svg viewBox="0 0 {W} {H}" class="tmchart" role="img" aria-label="Term-Häufigkeit je Band">']
    step = max(1, round(mx/5))
    for k in range(0, int(mx)+1, step):
        y = Yv(k); svg.append(f'<line x1="{PL}" y1="{y:.0f}" x2="{W-PR}" y2="{y:.0f}" stroke="var(--line)"/>'
                              f'<text x="{PL-6}" y="{y+3:.0f}" text-anchor="end" font-size="10" fill="var(--muted)">{k}</text>')
    for i,nr in enumerate(nrs):
        svg.append(f'<text x="{X(i):.0f}" y="{H-PB+15}" text-anchor="middle" font-size="10" fill="var(--muted)">Bd.{nr}'
                   f'<tspan x="{X(i):.0f}" dy="11">{TM_YEARS.get(nr,"")[:4]}</tspan></text>')
    for j,(g,col) in enumerate(plotted):
        pts = " ".join(f"{X(i):.0f},{Yv(rates[g][nr]):.1f}" for i,nr in enumerate(nrs))
        ly = PT+18*j
        svg.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>'
                   f'<line x1="{W-PR+8}" y1="{ly}" x2="{W-PR+22}" y2="{ly}" stroke="{col}" stroke-width="2"/>'
                   f'<text x="{W-PR+26}" y="{ly+3}" font-size="11" fill="var(--ink)">{html.escape(g)}</text>')
    svg.append('</svg>'); chart = "".join(svg)
    th = "".join(f"<th>Bd.{nr}<br>{TM_YEARS.get(nr,'')}</th>" for nr in nrs)
    trs = "".join(f"<tr><td>{html.escape(g)}</td>" + "".join(f"<td>{rates[g][nr]:.1f}</td>" for nr in nrs) + "</tr>" for g in TM_GROUPS)
    table = f'<table class="reg tm"><tr><th>Term-Gruppe · je 1000 Wörter</th>{th}</tr>{trs}</table>'
    kw = ['<h2 id="kwic">Konkordanz (KWIC)</h2><p class="meta">Jeder Beleg springt ins Faksimile. Aufklappen je Begriff.</p>']
    for term in TM_KWIC:
        rx = re.compile(r"(.{0,46})\b(%s\w*)\b(.{0,46})" % re.escape(term), re.I); hits = []
        for v in bands:
            for p in v["pages"]:
                for m in rx.finditer(tm_norm(p.get("text") or "")):
                    hits.append((v["nr"], p["tok"], m.group(1).strip(), m.group(2), m.group(3).strip()))
        kw.append(f'<details><summary>{html.escape(term)} <span class="meta">({len(hits)})</span></summary><ul class="kwic">')
        for nr,tok,l,c,r in hits[:40]:
            kw.append(f'<li><a class="meta" href="../volumes/bd{nr}.html#pb-{html.escape(tok)}">{nr}/{html.escape(tok)}</a> '
                      f'…{html.escape(l)} <b>{html.escape(c)}</b> {html.escape(r)}…</li>')
        kw.append('</ul></details>')
    tot = sum(words.values())
    att = ""
    if attention:
        mxa = max((a[1] for a in attention), default=1) or 1
        bars = "".join(
            f'<div class="attrow"><span class="attlabel" title="{html.escape(str(nm))}">{html.escape(str(nm))}</span>'
            f'<span class="attbar" style="width:{100*tot_/mxa:.0f}%"></span>'
            f'<span class="attval">{tot_}<span class="meta"> · {npl} Orte</span></span></div>'
            for nm, tot_, npl in attention)
        att = ('<h2>Aufmerksamkeit je Streckenabschnitt</h2>'
               '<p class="meta">Summe der Volltext-Erwähnungen aller verorteten Orte, dem nächstgelegenen '
               'Kastell-Abschnitt zugeordnet (≤ ~22 km) — welche Limes-Abschnitte im Limesblatt am meisten '
               f'Aufmerksamkeit bekamen.</p><div class="attwrap">{bars}</div>')
    return (f'<h1>Textanalyse des Limesblatt</h1>'
            f'<p class="meta">Token-freie Auswertung des gesamten Fraktur-OCR-Volltexts (8 Bände, 1892–1903; {tot:,} Wörter). '
            f'Sprung zu: <a href="#gegenprobe">Wortschatz-Gegenprobe (ORL)</a> · <a href="#orl">Osterburken-Kontrast</a> · <a href="#muenzen">Münzkaiser</a> · <a href="#truppen">Truppen</a> · '
            f'<a href="#zitate">Zitate</a> · <a href="#ocr">OCR-Qualität</a> · <a href="#kwic">Konkordanz</a>.</p>'
            f'<div class="tmwrap">{chart}</div>'
            f'<h2>Term-Gruppen über die Zeit</h2>{table}'
            f'<p class="meta">Befund: Steinbau dominiert; Holzbefund-Vokabular ist präsent und steigt mittig (Bd. 4–6); '
            f'explizite Datierungssprache fehlt fast; „principia" kommt nicht vor (man schrieb „Prätorium").</p>'
            + att + analysis_sections(volumes, orl_lex) + "".join(kw))

TOC_PAT   = re.compile(r"(?<![A-Za-z0-9])(\d{1,3})[._]\s+([A-ZÄÖÜ][A-Za-zäöüß0-9 .„“”\-]{1,55}?)[.*)]+\s*(\[[^\]]{0,70}\])?")
TOC_NOISE = re.compile(r"^(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|De[cz]ember|"
                       r"Jan|Feb|Mär|Apr|Jun|Jul|Aug|Sept|Okt|Nov|Dez|Aufl|AuB|Auli|Aull|Jahr\w*|Ausgeg\w*|Druck|"
                       r"Verlag|Legion|Turm|Auf|Vgl|Nr|Forts|Seite|Band|Heft)\b", re.I)
TOC_TYP   = re.compile(r"^(Limes|Kastell|Station|Zwischenkastell|Strecke|Wachtturm|Mümling|Pfahl|Teilstrecke)", re.I)

def build_toc(PLA):
    """{nr: [(tok, Nr, Titel, Klammer)]} der nummerierten Berichte.

    Primär aus dem vollständigen, geerdeten `tools/toc.json` (token-freie Basis + kuratierte
    Auflage, erzeugt von `tools/toc_extract.py`). Rückfall auf den lokalen Anker+Lücken-Scan,
    falls toc.json fehlt.
    """
    tocf = next((p for p in (os.path.join(REPO, "data", "toc.json"),                 # committed (CI-Rebuild)
                              os.path.join(REPO, "..", "limes", "tools", "toc.json"))  # Vault (lokaler Build)
                 if os.path.exists(p)), None)
    if tocf:
        try:
            data = json.load(open(tocf, encoding="utf-8"))
            toc = {}
            for r in data.get("reports", []):
                br = f"[{r['theme']}]" if r.get("theme") else ""
                cf = r.get("conf") or "medium"     # toc_extract setzt conf bereits aus Ort-Erdung + Rand-Ziffer
                toc.setdefault(r["nr"], []).append((str(r.get("token") or ""), r["num"], r.get("place") or "", br, cf))
            if toc:
                return toc
        except Exception:
            pass
    CACHE = os.path.join(REPO, "..", "limes", "tools", ".cache")
    BANDS = [("limesblatt1892_1893", 1), ("limesblatt1893_1894", 2), ("limesblatt1894_1895", 3),
             ("limesblatt1896", 4), ("limesblatt1897", 5), ("limesblatt1897_1898", 6),
             ("limesblatt1898_1902", 7), ("limesblatt1903", 8)]
    if not os.path.isdir(os.path.join(CACHE, BANDS[0][0])): return {}
    cands = []
    for slug, nr in BANDS:
        for fp in sorted(glob.glob(os.path.join(CACHE, slug, "*.txt"))):
            tok = os.path.splitext(os.path.basename(fp))[0]
            if not re.match(r'^\d+$', tok): continue
            txt = tm_norm(open(fp, encoding="utf-8", errors="replace").read())
            for m in TOC_PAT.finditer(txt):
                num = int(m.group(1)); title = re.sub(r'\s+', ' ', m.group(2)).strip().rstrip(' .'); br = m.group(3) or ""
                if TOC_NOISE.match(title) or len(title) < 3 or re.search(r'\d\s*$', title): continue
                if sum(1 for w in title.split() if len(w) == 1) >= 3: continue
                fw = title.split()[0].lower().strip(".,")
                valid = bool(br) or bool(TOC_TYP.match(title)) or fw in PLA
                cands.append((nr, tok, num, title, br, valid))
    anchors = []; rm = 0
    for i, c in enumerate(cands):
        if c[5] and rm < c[2] <= rm + 55: anchors.append(i); rm = c[2]
    accept = {}
    for i in anchors: accept.setdefault(cands[i][2], i)
    for a, b in zip(anchors, anchors[1:]):
        na, nb = cands[a][2], cands[b][2]
        if nb - na > 20: continue                 # zu große Lücke → nicht füllen (sonst Fließtext-Rauschen)
        for j in range(a + 1, b):
            if na < cands[j][2] < nb: accept.setdefault(cands[j][2], j)
    toc = {}
    for num, j in sorted(accept.items(), key=lambda kv: kv[1]):
        nr, tok, num, title, br, _ = cands[j]; toc.setdefault(nr, []).append((tok, num, title, br, "medium"))
    return toc

def toc_li(entries, hrefpre, with_page):
    """TOC-Listeneinträge; leere Titel → Platzhalter, conf=low → gedämpft (ehrlich gekennzeichnet)."""
    out = []
    for t, num, title, br, cf in entries:
        disp = html.escape(title) if title else '<span class="muted">[ohne eigene Überschrift]</span>'
        cls = ' class="lowtoc"' if cf == "low" else ""
        meta = f' <span class="meta">S. {html.escape(t)}</span>' if with_page else ""
        out.append(f'<li{cls}><a href="{hrefpre}#art-{num}"><b>{num}.</b> {disp}</a>'
                   f'{(" " + html.escape(br)) if br else ""}{meta}</li>')
    return "".join(out)

# ---------- Fundindex & Bibliographie (token-frei, spalten-präzise) ----------
def scan_occ(volumes, patterns):
    """{key: [(vol, anchor, printed, term), …]} — je Spalte, entdoppelt; term = der konkret
    getroffene Wortlaut (für den Wort-genauen Sprung + Highlight im Lesetext)."""
    occ, seen = defaultdict(list), set()
    for v in volumes:
        for p in v["pages"]:
            txt = p.get("text") or ""
            if not txt: continue
            for key, rx in patterns:
                m = rx.search(txt)
                if m:
                    k = (key, v["nr"], p["anchor"])
                    if k not in seen:
                        seen.add(k); occ[key].append((v["nr"], p["anchor"], p["printed"], m.group(0)))
    return occ

def _belege(items, cap=60):
    out = []
    for vol, grp in groupby(items, key=lambda x: x[0]):
        seen, links = set(), []
        for it in grp:
            a, pp = it[1], it[2]; term = it[3] if len(it) > 3 else ""
            if a in seen: continue
            seen.add(a); links.append((a, pp, term))
        shown = ", ".join(
            f'<a href="../volumes/bd{vol}.html{("?hl=" + quote(term)) if term else ""}#pb-{html.escape(a)}">{html.escape(pp)}</a>'
            for a, pp, term in links[:cap])
        more = f' <span class="meta">+{len(links) - cap}</span>' if len(links) > cap else ""
        out.append(f'Bd.&#160;{vol}: {shown}{more}')
    return " · ".join(out) if out else '<span class="meta">—</span>'

FUND_CATS = [
    ("Münzen", r"münz\w+|denar\w*|sesterz\w*|aureus\w*|bronzemünz\w*|silbermünz\w*"),
    ("Terra Sigillata", r"sigillata\w*"),
    ("Stempel (Ziegel/Töpfer)", r"stempel\w*|töpfermarke\w*"),
    ("Inschriften & Weihesteine", r"inschrift\w*|weihestein\w*|weihinschrift\w*|meilenstein\w*|\bara\b|\baltar\w*|diplom\w*"),
    ("Fibeln", r"fibel\w*|fibul\w*"),
    ("Keramik & Gefäße", r"gefäss\w*|gefäß\w*|scherbe\w*|thongefäss\w*|amphor\w*|\bkrug\b|krüge\w*|schale\w*|becher\w*|\bnapf\w*|teller\w*|\btopf\b|töpfe\w*|urne\w*"),
    ("Glas", r"\bglas\b|gläs\w*|glasscherbe\w*|glasgefäss\w*"),
    ("Waffen & Geräte", r"lanzenspitze\w*|pfeilspitze\w*|wurfspiess\w*|schwert\w*|dolch\w*|\bbeil\b|\bmesser\b|\bnagel\b|nägel\b|schlüssel\w*|werkzeug\w*|\bgerät\w*"),
    ("Schmuck & Tracht", r"fingerring\w*|armband\w*|armring\w*|\bperle\w*|haarnadel\w*|gewandnadel\w*|gürtel\w*|schnalle\w*"),
    ("Bronze & Metall", r"\bbronze\w*|\beisen\w*|\bblei\b|silber\w*|\bgold\w*"),
    ("Architektur (Hypokaust/Bad)", r"hypokaust\w*|estrich\w*|\bsäule\w*|säulen|tubul\w*|heizung\w*|badegebäude\w*|\btherme\w*|\bbrunnen\w*"),
    ("Bestattung", r"brandgrab\w*|\bgräber\w*|\bgrab\b|bestattung\w*|aschenkiste\w*|leichenbrand\w*"),
    ("Knochen & Tierreste", r"\bknochen\w*|tierknochen\w*|geweih\w*"),
]
FUND_EMP = [("Vespasian", r"vespasian"), ("Domitian", r"domitian"), ("Trajan", r"tra[ij]an(?!\w)"),
            ("Hadrian", r"hadrian(?!swall)"), ("Antoninus Pius", r"antoninus|antonin\b"),
            ("Marc Aurel", r"marc\W{0,2}aurel|marcus aurel"), ("Commodus", r"commodus"),
            ("Septimius Severus", r"septimius|sept\. sever"), ("Caracalla", r"caracalla"),
            ("Severus Alexander", r"severus alexander"), ("Gordianus", r"gordian"),
            ("Philippus", r"philippus\b"), ("Gallienus", r"gallienus"), ("Probus", r"\bprobus\b")]

def thematic_table(occ, order, head):
    rows = "".join(f'<tr><td><b>{html.escape(lbl)}</b></td><td>{len(occ[k])}</td>'
                   f'<td class="beleg">{_belege(occ[k])}</td></tr>'
                   for k, lbl in order if occ.get(k))
    return f'<table class="reg fund"><tr><th>{head}</th><th>Seiten</th><th>Belege (Seite · Spalte)</th></tr>{rows}</table>'

def fundindex_page(volumes):
    occ = scan_occ(volumes, [(k, re.compile(rx, re.I)) for k, rx in FUND_CATS])
    emp = scan_occ(volumes, [(k, re.compile(rx, re.I)) for k, rx in FUND_EMP])
    drag = scan_occ(volumes, [(f"Drag. {n}", re.compile(rf"\bdrag(?:endorff)?\.?\s*{n}\b", re.I)) for n in
                              ["27", "29", "31", "32", "33", "35", "36", "37", "38", "45", "47", "49"]])
    leg, coh, legend = stamp_occ(volumes)
    order = lambda d: sorted(((k, k) for k in d), key=lambda x: -len(d[x[0]]))
    cat_t = thematic_table(occ, [(k, k) for k, _ in FUND_CATS], "Fundgattung")
    emp_t = thematic_table(emp, [(k, k) for k, _ in FUND_EMP], "Münzkaiser")
    drag_t = thematic_table(drag, [(f"Drag. {n}", f"Drag. {n}") for n in
             ["27", "29", "31", "32", "33", "35", "36", "37", "38", "45", "47", "49"]], "Sigillata-Form")
    leg_t = thematic_table(leg, order(leg), "Legionsstempel")
    coh_t = thematic_table(coh, order(coh), "Cohortenstempel")
    legend_top = sorted(legend, key=lambda w: -len(legend[w]))[:25]
    legend_t = thematic_table(legend, [(w, w) for w in legend_top], "Versal-Legende")
    body = (f'<h1>Fundindex</h1><p class="meta">Token-frei aus dem Volltext: Fundgattungen, Münzkaiser, '
            f'Sigillata-Formen und Truppenstempel mit <b>seiten- und spaltengenauen Belegen</b> ins Faksimile. '
            f'Heuristischer Wortabgleich auf Fraktur-OCR — Nennung ≠ stets Fund an dieser Stelle. '
            f'Die katalogisierte Epigraphik steht unter <a href="inschriften.html">Inschriften (EDH)</a>.</p>'
            f'<h2>Fundgattungen</h2>{cat_t}'
            f'<h2 id="muenzkaiser">Münzkaiser (Datierungsevidenz)</h2>'
            f'<p class="meta">Bildet die Limes-Belegung ab: flavisch-trajanische Errichtung, severischer Peak, Auslaufen vor 260.</p>{emp_t}'
            f'<h2 id="sigillata">Terra-Sigillata-Formen</h2>'
            f'<p class="meta">Die Dragendorff-Formtypen als laufendes Datierungsraster — vgl. <a href="bibliographie.html">Dragendorff 1895</a>.</p>{drag_t}'
            f'<h2 id="stempel">Truppenstempel</h2>'
            f'<p class="meta">Legio-/Cohors-Nennungen (Ziegelstempel &amp; Text) — erwartungsgemäß dominiert <b>Legio XXII Primigenia</b> (Mainz).</p>{leg_t}{coh_t}'
            f'<h2 id="legenden">Häufigste Versal-Legenden</h2>'
            f'<p class="meta">Großbuchstaben-Folgen aus dem Volltext — Töpfer-/Ziegelstempel-Legenden und Inschriftentext gemischt (heuristisch).</p>{legend_t}')
    return body

BIB_PERSON = {"bib_cohausen": ("p_karl_august_von_cohausen", "Karl August von Cohausen")}  # Autor-Werk → Vault-Person

def load_bibl(path):
    if not os.path.exists(path): return []
    t = open(path, encoding="utf-8").read(); out = []
    for m in re.finditer(r'<bibl xml:id="([^"]+)">(.*?)</bibl>', t, re.S):
        bid, blk = m.group(1), m.group(2)
        ti = re.search(r'<title>([^<]+)</title>', blk); no = re.search(r'<note>([^<]+)</note>', blk)
        oa = re.search(r'<ref type="oa" target="([^"]+)">([^<]+)</ref>', blk)
        iiif = re.search(r'<ref type="iiif-manifest" target="([^"]+)"', blk)
        propy = re.search(r'<ref type="propylaeum" target="([^"]+)"', blk)
        out.append({"id": bid, "title": unesc(ti.group(1)) if ti else bid,
                    "note": unesc(no.group(1)) if no else "",
                    "oa": oa.group(1) if oa else "", "oalabel": unesc(oa.group(2)) if oa else "",
                    "iiif": iiif.group(1) if iiif else "",
                    "propy": propy.group(1) if propy else ""})
    return out

def bibliography_page(bibls, occ):
    rows = []
    for b in bibls:
        items = occ.get(b["id"], [])
        link = f' · <a href="{b["oa"]}">{html.escape(b["oalabel"])}</a>' if b["oa"] else ""
        propy = (f' · <a class="propy" href="{b["propy"]}" target="_blank" rel="noopener"'
                 f' title="Im Fachinformationsdienst Altertumswissenschaften suchen">Propylaeum&#8201;SEARCH&#8201;↗</a>') if b.get("propy") else ""
        if items:                                          # im TEI als <ref> ausgezeichnet
            cnt, bel = str(len(items)), _belege(items, cap=40)
        elif b["id"] in BIB_PERSON:                        # Autor-Werk → Belege via Personenregister
            pid, pnm = BIB_PERSON[b["id"]]; pit = occ.get(pid, [])
            cnt = str(len(pit))
            bel = (_belege(pit, cap=40) + f' <span class="meta">(als Autor <a href="persons.html#{pid}">{html.escape(pnm)}</a>)</span>') if pit else '<span class="meta">—</span>'
        else:
            cnt, bel = '·', '<span class="meta">im Text als Autor → Personenregister</span>'
        iiifbtn = ""
        if b.get("iiif"):
            jl = b["title"].replace("'", "").replace('"', "")
            iiifbtn = (f' · <button class="iiifbtn" onclick="openIIIF(\'{b["iiif"]}\',\'{html.escape(jl)}\')">'
                       f'📖 Faksimile (IIIF)</button>')
        rows.append(f'<tr id="{b["id"]}"><td><b>{html.escape(b["title"])}</b>{link}{propy}{iiifbtn}'
                    f'<div class="meta">{html.escape(b["note"])}</div></td>'
                    f'<td>{cnt}</td><td class="beleg">{bel}</td></tr>')
    n_oa = sum(1 for b in bibls if b["oa"]); n_iiif = sum(1 for b in bibls if b.get("iiif"))
    return (f'<h1>Bibliographie &amp; Quellen</h1>'
            f'<p class="meta">Die im Limesblatt zitierte Apparatur — <b>im TEI-Fließtext als <code>&lt;ref&gt;</code> '
            f'ausgezeichnet</b> (Journale, Inschriftencorpora, Dragendorff-Formen) bzw. über das Personenregister '
            f'(Autor-Werke) — aufgelöst zu vollen Referenzen mit <b>{n_oa} Open-Access-Digitalisaten</b> '
            f'(v. a. UB Heidelberg) und seiten-/spaltengenauen Belegen. Bei <b>{n_iiif} Werken</b> lässt sich das '
            f'Faksimile per <b>IIIF</b> direkt hier im Fenster öffnen (UB Heidelberg / archive.org; Werk-/Beispielband-Ebene). '
            f'Jedes Werk ist zudem an den <b>Fachinformationsdienst Altertumswissenschaften (Propylaeum SEARCH</b>, '
            f'UB Heidelberg) angeschlossen — wie die Personen an <a href="namen.html">Propylaeum-VITAE</a> und die '
            f'Inschriften an die <a href="inschriften.html">EDH</a>. '
            f'Journal-zentriert: dominant die Westdeutsche Zeitschrift und ihr Korrespondenzblatt.</p>'
            f'<table class="reg fund"><tr><th>Werk / Reihe (Digitalisat)</th><th>Verweise</th><th>Belege (Seite · Spalte)</th></tr>'
            f'{"".join(rows)}</table>'
            f'<div id="iiifwin"><div class="iiifbar"><span id="iiiflabel"></span>'
            f'<button onclick="closeIIIF()">✕ schließen</button></div><div id="iiifosd"></div></div>'
            f'<script src="../assets/openseadragon.min.js"></script><script src="../assets/iiif.js"></script>')

# ---------- Truppen-/Töpferstempel & EDH-Inschriften ----------
_ROM = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
def _r2i(s):
    s = s.lower()
    if not s or any(c not in _ROM for c in s): return None
    t = pv = 0
    for c in reversed(s):
        v = _ROM[c]; t += -v if v < pv else v; pv = max(pv, v)
    return t if 1 <= t <= 30 else None
def _i2r(n):
    o = ""
    for v, sy in [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]:
        while n >= v: o += sy; n -= v
    return o

def stamp_occ(volumes):
    """Truppenstempel (Legio/Cohors + röm. Zahl) → Einheit; + häufigste Versal-Legenden."""
    leg, coh, legend = defaultdict(list), defaultdict(list), defaultdict(list)
    legrx = re.compile(r"\bleg(?:io|\.|\b)\.?\s*([ivxlc]{1,6})\b", re.I)
    cohrx = re.compile(r"\bcoh(?:ors|orte|\.|\b)\.?\s*([ivxlc]{1,6})\b", re.I)
    caprx = re.compile(r"\b([A-ZÄÖÜ]{4,10})\b")
    seen = set()
    for v in volumes:
        for p in v["pages"]:
            txt = p.get("text") or ""
            for rx, dst, pre in ((legrx, leg, "Legio"), (cohrx, coh, "Cohors")):
                for m in rx.finditer(txt):
                    n = _r2i(m.group(1))
                    if not n: continue
                    key = f"{pre} {_i2r(n)}"; k = (key, v["nr"], p["anchor"])
                    if k not in seen: seen.add(k); dst[key].append((v["nr"], p["anchor"], p["printed"], m.group(0)))
            for m in caprx.finditer(txt):                      # Versal-Legenden (Stempel/Inschrift)
                w = m.group(1)
                if _r2i(w) or not re.search(r"[AEIOUÄÖÜ]", w): continue
                k = (w, v["nr"], p["anchor"])
                if k not in seen: seen.add(k); legend[w].append((v["nr"], p["anchor"], p["printed"], w))
    return leg, coh, legend

def inscriptions_page(edh):
    secs = []
    for k in edh.get("kastelle", []):
        pid = "pl_" + gazetteer.slug(k["note"])
        gat = " · ".join(f"{a}: {n}" for a, n in k.get("gattungen", {}).items())
        span = f'{k["von"]}–{k["bis"]} n. Chr.' if k.get("von") else "—"
        rows = "".join(
            f'<li><a href="https://edh.ub.uni-heidelberg.de/edh/inschrift/{html.escape(i["hd"])}">{html.escape(i["hd"])}</a> '
            f'<span class="meta">{html.escape(i["art"])}{(" · " + html.escape(i["datierung"])) if i.get("datierung") else ""}</span> — '
            f'{html.escape(i["titel"])}</li>' for i in k["inschriften"][:60])
        more = f'<li class="lc">… +{k["n"] - 60} weitere bei EDH</li>' if k["n"] > 60 else ""
        secs.append(f'<details><summary><a href="places.html#{pid}">{html.escape(k["label"])}</a> '
                    f'<span class="meta">— {k["n"]} Inschriften · {span} · {html.escape(gat)}</span></summary>'
                    f'<ul class="nerlist">{rows}{more}</ul></details>')
    return (f'<h1>Inschriften (EDH)</h1>'
            f'<p class="meta">{edh.get("total", 0)} Inschriften der '
            f'<a href="https://edh.ub.uni-heidelberg.de/">Epigraphic Database Heidelberg</a> von den Limes-Fundorten — '
            f'aus den EpiDoc-GitHub-Dumps (CC BY-SA), nach Kastell gruppiert, mit Gattung, Datierung und Direktlink ins EDH. '
            f'Ergänzt den <a href="fundindex.html">Fundindex</a> um die katalogisierte Epigraphik; '
            f'verknüpft mit dem <a href="places.html">Ortsregister</a>.</p>'
            + "".join(secs))

def reception_page(rec):
    items = rec.get("items", []); summ = rec.get("summary", {}); nd = rec.get("normdata", {})
    sp = summ.get("span", [None, None])
    def row(it):
        au = (", ".join(it.get("authors", [])[:2]) + " · ") if it.get("authors") else ""
        return (f'<li><a href="{html.escape(it["url"])}">{html.escape(it.get("title") or "—")}</a> '
                f'<span class="meta">{html.escape(au)}{it.get("year") or "o. J."} — {html.escape(it["type"])} '
                f'<span class="lc">[{html.escape(" · ".join(it.get("srcs", [])))}]</span></span></li>')
    secs = ""
    for key, lbl in [("zeitgenössisch", "Zeitgenössische Rezeption (≤ 1912)"),
                     ("modern", "Moderne Zitations-Nachwirkung")]:
        lis = "".join(row(it) for it in items if it.get("era") == key)
        if lis: secs += f'<h2>{lbl}</h2><ul class="nerlist">{lis}</ul>'
    gap = ("<b>noch keinen eigenen Eintrag</b>" if not nd.get("wikidata_hits") and not nd.get("gnd_work_hits")
           else f'{nd.get("wikidata_hits", 0)} Wikidata- / {nd.get("gnd_work_hits", 0)} GND-Treffer')
    return (f'<h1>Rezeption &amp; Wirkungsgeschichte</h1>'
            f'<p class="meta">Wie das Limesblatt <b>außerhalb</b> seiner eigenen Bände rezipiert wurde — token-frei '
            f'aus Open-Access-Repositorien geharvestet (OpenAlex · Crossref · archive.org · DAI-Zenon). '
            f'{summ.get("total", 0)} Belege ({sp[0]}–{sp[1]}); heuristisch, metadaten-getrieben '
            f'(<code>tools/rezeption.py</code>).</p>'
            f'<h2>Vom Vorbericht zum Standardwerk (Limesblatt → ORL)</h2>'
            f'<p class="meta">Die laufenden Feldberichte des Limesblatt gingen in das definitive '
            f'<a href="bibliographie.html#bib_orl">ORL</a> ein; die <a href="wortschatz.html#orl">ORL-Gegenprobe</a> '
            f'zeigt, wie die ORL-Redaktion die Befunde (Holzbefunde ~4×) ausdünnte — Rezeption als editoriale Transformation.</p>'
            f'{secs}'
            f'<h2>Normdaten-Lücke</h2>'
            f'<p class="meta">Bemerkenswert für die digitale Erschließung: das Limesblatt als Periodikum hat in '
            f'<b>Wikidata</b> und der <b>GND</b> {gap} (Stand des Harvests) — ein offener Punkt seiner Rezeption.</p>')

def orl_page(idx, lex):
    a = idx.get("abteilung_A_strecken", []); b = idx.get("abteilung_B_kastelle", []); c = idx.get("counts", {})
    nef = sum(1 for r in b if r.get("pages")); nner = sum(1 for r in b if r.get("schicht_c", {}).get("ner_terms"))
    arows = "".join(f'<tr id="orl-a-{s.get("strecke","")}"><td>{s.get("strecke","")}</td><td>{html.escape(s.get("verlauf",""))}</td>'
                    f'<td>{html.escape(s.get("region",""))}</td></tr>' for s in a)
    def brow(r):
        sc = r.get("schicht_c", {})
        pages = (f'{r["schicht_b"]["pages"]} S.' if r.get("schicht_b")
                 else (f'{r["pages"]} S.' if r.get("pages") else '—'))
        dg = f' <a href="{html.escape(r["digitalisat"])}" title="Volltext (archive.org)">▣</a>' if r.get("digitalisat") else ''
        prof = ", ".join(sc.get("profile", [])[:4])
        bearb = ", ".join(html.escape(x) for x in r.get("bearbeiter", []))
        lk = []
        if r.get("wiki"):
            lk.append(f'<a href="https://de.wikipedia.org/wiki/{urllib.parse.quote(r["wiki"].replace(" ", "_"))}" title="Wikipedia-Artikel">W</a>')
        if r.get("htid"):
            lk.append(f'<a href="https://hdl.handle.net/2027/{html.escape(r["htid"])}" title="Scan bei HathiTrust">HT</a>')
        place = r.get("ort") or re.sub(r"^(Kastelle? von |Kleinkastell |Kastell )", "", r["kastell"])
        if place and not place.startswith("("):
            lk.append(f'<a href="https://de.wikisource.org/w/index.php?search={urllib.parse.quote(place)}&amp;fulltext=1" title="Realencyclopädie (RE) / Wikisource — offenes Altertums-Lexikon">RE</a>')
        links = f' <span class="lc">[{" · ".join(lk)}]</span>' if lk else ""
        return (f'<tr id="orl-{html.escape(r["nr"])}"><td>{html.escape(r["nr"])}</td><td>{html.escape(r["kastell"])}{links}</td>'
                f'<td class="meta">{html.escape(r.get("linie",""))}</td><td>{pages}{dg}</td>'
                f'<td>{sc.get("ner_gazetteer") or ""}</td><td>{r.get("sigillata",{}).get("score") or ""}</td>'
                f'<td class="meta">{html.escape(prof)}</td><td>{len(r.get("vorberichte",[])) or ""}</td>'
                f'<td class="meta">{bearb}</td></tr>')
    brows = "".join(brow(r) for r in b)
    keyn = ""
    if lex:
        od = ", ".join(html.escape(d["w"]) for d in lex.get("orl_distinctive", [])[:8])
        ld = ", ".join(html.escape(d["w"]) for d in lex.get("lb_distinctive", [])[:8])
        keyn = (f'<p class="meta" id="keyness">Ein Vergleich der Worthäufigkeiten beider Werke zeigt einen '
                f'<b>Wechsel der Textsorte</b>: distinktiv für die <b>ORL-Endpublikation</b> ist die Fund-Typologie '
                f'({od}…), für die <b>Limesblatt-Vorberichte</b> die Trassierung in erster Person ({ld}…). '
                f'Die vollständige <a href="wortschatz.html#gegenprobe">Wortschatz-Gegenprobe</a> mit beiden '
                f'Wortlisten steht in der <a href="wortschatz.html">Analyse</a>.</p>')
    return (f'<h1>ORL — Der obergermanisch-raetische Limes des Römerreiches</h1>'
            f'<p class="meta">Die <b>Endpublikation</b> der Reichs-Limeskommission (1894–1937): '
            f'{c.get("abt_A",len(a))} Strecken-Bände (Abt. A) + {c.get("abt_B",len(b))} Kastell-Lieferungen '
            f'(Abt. B) — das Standardwerk, in das die laufenden Feldberichte des '
            f'<a href="../index.html">Limesblatt</a> mündeten. Token-frei erschlossen über HathiTrust '
            f'(<a href="hathitrust.html">Werkzeuge &amp; Ertrag</a>): Seitenzahlen für {nef} Bände, '
            f'NER-Schicht-C für {nner}, ein konsolidierter <a href="orl-register.html">Gesamtapparat</a>, '
            f'den die in 14 Mappen erschienene Reihe nie besaß.</p>'
            f'{keyn}'
            f'<h2>Abteilung A — Strecken-Bände (Trassierung)</h2>'
            f'<table class="reg"><thead><tr><th>Str.</th><th>Verlauf</th><th>Region</th></tr></thead>'
            f'<tbody>{arows}</tbody></table>'
            f'<h2>Abteilung B — Kastell-Lieferungen</h2>'
            f'<p class="meta">Seiten: ▣ = archive.org-Volltext, sonst HathiTrust-Extracted-Features · '
            f'Cross-Work = mit dem Limesblatt gemeinsame Entitäten · Sig. = Terra-Sigillata-Score '
            f'(<a href="orl-register.html#sigillata">Apparat</a>) · Vorb. = Anzahl Limesblatt-Vorberichte '
            f'(<a href="orl-register.html#konkordanz">Konkordanz</a>). Verweise je Kastell: '
            f'<b>W</b> = Wikipedia · <b>HT</b> = Scan bei HathiTrust · <b>RE</b> = Realencyclopädie/Wikisource '
            f'(offenes Altertums-Lexikon).</p>'
            f'<table class="reg"><thead><tr><th>ORL</th><th>Kastell</th><th>Linie</th><th>Seiten</th>'
            f'<th>Cross-Work</th><th>Sig.</th><th>Charakteristik</th><th>Vorb.</th><th>Bearbeiter</th></tr></thead>'
            f'<tbody>{brows}</tbody></table>')

def orl_apparatus_page(reg, idx):
    persons = reg.get("persons", []); places = reg.get("places", [])
    def prow(r):
        bands = ", ".join(r["bands"][:12]) + ("…" if len(r["bands"]) > 12 else "")
        return (f'<tr><td>{html.escape(r["name"])}{" ✓" if r.get("gazetteer") else ""}</td>'
                f'<td>{r["nbands"]}</td><td>{r["count"]}</td><td class="meta">{html.escape(bands)}</td></tr>')
    pc = [r for r in persons if r.get("gazetteer") or r["nbands"] >= 3][:80]
    plc = [r for r in places if r.get("gazetteer") or r["nbands"] >= 3][:80]
    b = idx.get("abteilung_B_kastelle", [])
    sig = sorted([r for r in b if r.get("sigillata")], key=lambda r: -r["sigillata"]["score"])[:25]
    sigrows = "".join(f'<tr><td>{html.escape(r["nr"])}</td><td>{html.escape(r["kastell"])}</td>'
                      f'<td>{r["sigillata"]["score"]}</td><td class="meta">'
                      f'{html.escape(", ".join(f"{t} ({n})" for t,n in list(r["sigillata"]["terms"].items())[:6]))}</td></tr>'
                      for r in sig)
    con = [r for r in b if r.get("vorberichte")]
    conrows = "".join(f'<tr><td>{html.escape(r["nr"])}</td><td>{html.escape(r["kastell"])}</td>'
                      f'<td class="meta">{", ".join(html.escape(x) for x in r.get("bearbeiter", [])) or "—"}</td>'
                      f'<td class="meta">{", ".join("Nr. "+str(v["num"]) for v in r["vorberichte"][:10])}'
                      f'{"…" if len(r["vorberichte"])>10 else ""}</td></tr>' for r in con)
    return (f'<h1>ORL — Konsolidierter Gesamtapparat</h1>'
            f'<p class="meta">Register, Apparate und Konkordanzen über <b>alle</b> ORL-Bände — token-frei aus '
            f'HathiTrust-NER und Extracted Features aggregiert (<a href="hathitrust.html">Methode</a>); '
            f'das Generalwerkzeug, das die 14-Mappen-Reihe nie hatte. Zurück zum '
            f'<a href="orl.html">ORL-Bandindex</a>.</p>'
            f'<h2 id="personen">Personenregister ({len(pc)} bandübergreifend)</h2>'
            f'<p class="meta">✓ = im Limesblatt-Gazetteer der Edition belegt. Aus automatischer Eigennamenerkennung '
            f'über Fraktur-OCR; Schreibvarianten nicht zusammengeführt.</p>'
            f'<table class="reg"><thead><tr><th>Person</th><th>#Bd.</th><th>Nenn.</th><th>Bände (ORL-Nr.)</th></tr></thead>'
            f'<tbody>{"".join(prow(r) for r in pc)}</tbody></table>'
            f'<h2 id="orte">Ortsregister ({len(plc)} bandübergreifend)</h2>'
            f'<table class="reg"><thead><tr><th>Ort</th><th>#Bd.</th><th>Nenn.</th><th>Bände</th></tr></thead>'
            f'<tbody>{"".join(prow(r) for r in plc)}</tbody></table>'
            f'<h2 id="sigillata">Terra-Sigillata-Apparat</h2>'
            f'<p class="meta">Welche Lieferungen die großen Fund-Katalogbände sind (Dragendorff/Knorr/Ludowici/'
            f'Rheinzabern …), aus den EF-Token ausgezählt — die Verfeinerung, die die Keyness als ORL-typisch auswies.</p>'
            f'<table class="reg"><thead><tr><th>ORL</th><th>Kastell</th><th>Score</th><th>Begriffe</th></tr></thead>'
            f'<tbody>{sigrows}</tbody></table>'
            f'<h2 id="konkordanz">Vorbericht → ORL-Konkordanz</h2>'
            f'<p class="meta">Je Kastell der Limesblatt-Vorbericht (Bericht-Nr.) und der Bearbeiter — die Brücke '
            f'Vorbericht ↔ Endpublikation.</p>'
            f'<table class="reg"><thead><tr><th>ORL</th><th>Kastell</th><th>Bearbeiter</th><th>Limesblatt-Vorberichte</th></tr></thead>'
            f'<tbody>{conrows}</tbody></table>')

def hathitrust_page(idx, reg, lex):
    b = idx.get("abteilung_B_kastelle", [])
    nef = sum(1 for r in b if r.get("pages")); nner = sum(1 for r in b if r.get("schicht_c", {}).get("ner_terms"))
    np = reg.get("counts", {}).get("persons", 0); npl = reg.get("counts", {}).get("places", 0)
    ow = lex.get("orl_words", 0) if lex else 0
    return (f'<h1>HathiTrust — Werkzeuge &amp; Ertrag</h1>'
            f'<p class="meta">Wie der ORL token-frei und <b>nicht-konsumtiv</b> erschlossen wurde. Die 56 Bände sind '
            f'gemeinfrei, liegen bei HathiTrust aber nur als Seiten-Scans hinter einer Bot-Wall. Gearbeitet wurde '
            f'ausschließlich mit offenen, abgeleiteten Daten — kein Seitentext wird reproduziert; alles reproduzierbar '
            f'mit Python-Standardbibliothek, ohne API-Schlüssel.</p>'
            f'<h2>1 · Workset — die Bände identifizieren</h2>'
            f'<p>Aus vier HathiTrust-Katalog-Records (RIS-Exporte) die echten Volume-IDs (htids) geparst → ein sauberes '
            f'<b>56-Bände-Workset</b>, ein Exemplar je Lieferung. Decke: no.57–70 und die a/b-Unterhefte sind in '
            f'HathiTrust nicht digitalisiert.</p>'
            f'<h2>2 · Extracted Features — Vokabular &amp; Seiten</h2>'
            f'<p>Die HTRC <b>Extracted Features 2.5</b> (seitenweise Wortmengen, mitgliedsfrei) direkt per '
            f'<code>rsync</code> gezogen — am defekten <code>RSyncGenerator</code> vorbei (dessen HTTPS-Zertifikat '
            f'abgelaufen war), indem die Stubbytree-Pfade selbst aus den htids abgeleitet wurden. Ertrag: Seitenzahlen + '
            f'distinktive Vokabular-Profile (TF-IDF) für <b>{nef}</b> Bände.</p>'
            f'<h2>3 · HTRC Analytics — Entitäten &amp; Frequenzen</h2>'
            f'<p>Über das HTRC-Algorithmus-Portal auf dem Workset: <b>Named-Entity-Recognition</b> (≈130 000 '
            f'Entitäten → Schicht C + Cross-Work-Register für <b>{nner}</b> Bände) und <b>Token-Count</b> '
            f'(≈{ow:,}-Wörter-Korpusfrequenz → die <a href="orl.html#keyness">Wortschatz-Gegenprobe</a>).</p>'
            f'<h2>4 · Data Capsule — Volltext (in Arbeit)</h2>'
            f'<p>Für das, was nur fortlaufender Volltext liefert — Inschriften-Zitate (CIL/Brambach), Bearbeiter je '
            f'Lieferung, KWIC-Konkordanz — eine nicht-konsumtive <b>HTRC Data Capsule</b>: Volltext geladen, Analyse per '
            f'stdlib-Python, Export der aggregierten Ergebnisse über den HTRC-Review (laufend).</p>'
            f'<h2>Ertrag</h2>'
            f'<p>Aus diesen offenen Schichten entstand der konsolidierte <a href="orl-register.html">Gesamtapparat</a>: '
            f'ein <b>{np}-Personen-</b> und <b>{npl}-Orte-Generalregister</b>, die '
            f'<a href="orl-register.html#sigillata">Sigillata-Konkordanz</a>, die '
            f'<a href="orl-register.html#konkordanz">Vorbericht-Konkordanz</a> und die Wortschatz-Gegenprobe — '
            f'Apparate, die der in 14 Mappen über 40 Jahre erschienene ORL selbst nie besaß.</p>')

def documentation_page(s):
    # Datenherkunft — jede offene Quelle in klarer Sprache (kein Fachjargon)
    src = [
        ("Universitätsbibliothek Heidelberg", "Die eingescannten Originalseiten und ihre maschinelle Umschrift — Grundlage des Volltexts, der Suche, der Namens- und Ortslisten und der Analyse."),
        ("Normdaten der Bibliotheken (GND) &amp; Wikidata", "Zu den Personen: gesicherte Lebensdaten, Rollen und Verweise auf Standard-Nachschlagewerke."),
        ("Kalliope (Nachlass-Verbund)", "Wo die Nachlässe und Briefe der beteiligten Forscher heute aufbewahrt werden."),
        ("Epigraphische Datenbank Heidelberg", "Die von den Limes-Fundorten bekannten römischen Inschriften."),
        ("Antike-Ortsverzeichnisse &amp; OpenStreetMap", "Die Karte, der Verlauf der Grenzlinie und die Wachttürme und Kleinkastelle je Abschnitt."),
        ("archive.org", "Frei lesbare Digitalisate der zitierten Literatur und der eine offen zugängliche Band der Endpublikation."),
        ("Literaturdatenbanken (u. a. Zenon des DAI)", "Wo spätere Forschung das Limesblatt zitiert hat — für die Nachwirkung."),
        ("Digitale Bibliothek HathiTrust", "Die eingescannten Bände der Endpublikation (ORL), aus denen ihre Verzeichnisse gewonnen wurden."),
    ]
    srows = "".join(f'<tr><td>{a}</td><td class="meta">{b}</td></tr>' for a, b in src)
    return (
        f'<h1>Dokumentation</h1>'
        f'<p class="meta">Diese Website erschließt zwei Werke der frühen Limesforschung: die laufenden '
        f'<a href="index.html"><b>Feldberichte des Limesblatt</b></a> (1892–1903) und die große '
        f'<a href="register/orl.html"><b>Endpublikation ORL</b></a> (1894–1937) — und zeigt, wie das eine ins '
        f'andere überging. Alles, was sich verlässlich nachschlagen lässt (Lebensdaten, Orte, Nachweise), wurde '
        f'automatisch aus frei zugänglichen Quellen zusammengetragen; das Deuten, Prüfen und Schreiben blieb '
        f'Handarbeit. Diese Seite erklärt <b>was</b> hier zu finden ist, <b>woher</b> die Angaben stammen und '
        f'<b>was</b> sie erkennen lassen.</p>'

        f'<h2>1 · Was auf der Website steht — ein Wegweiser</h2>'
        f'<p class="meta">Die Seite hat drei Ebenen: den <i>lesbaren Text</i> der Bände, <i>Verzeichnisse</i>, die '
        f'diesen Text erschließen, und einige <i>Auswertungen</i>. Oben in der Leiste erreichbar, hier ausführlich:</p>'

        f'<h3>Der Text der Bände</h3>'
        f'<ul>'
        f'<li><a href="index.html"><b>Bände</b></a> — die {s["nvol"]} Hefte des Limesblatt vollständig lesbar, '
        f'Seite für Seite neben dem eingescannten Original; die zweispaltige Druckanordnung bleibt erhalten. '
        f'Personen, Orte und zitierte Werke sind im Text anklickbar und führen in die Verzeichnisse.</li>'
        f'<li><a href="index.html#suche"><b>Suche</b></a> — durchsucht den gesamten Text aller Bände.</li>'
        f'</ul>'

        f'<h3>Verzeichnisse — von Hand erstellt</h3>'
        f'<ul>'
        f'<li><a href="register/persons.html"><b>Personen</b></a> — die {s["npers"]} zentralen Beteiligten der '
        f'Reichs-Limeskommission: Lebensdaten, Funktion, wo ihre Nachlässe liegen, welche Kastelle sie ausgruben, '
        f'mit Verweisen auf die üblichen biografischen Nachschlagewerke.</li>'
        f'<li><a href="register/places.html"><b>Orte</b></a> — die {s["nplac"]} benannten Kastelle auf einer nach '
        f'Abschnitt filterbaren Karte, je mit heutigem Ortsnamen, Kastelltyp, Ausgräber und Inschriften.</li>'
        f'<li><a href="register/strecken.html"><b>Strecken</b></a> — die 15 Abschnitte, in die man die Grenze für '
        f'die Vermessung einteilte, je mit ihren Kastellen und dem zuständigen Streckenkommissar.</li>'
        f'</ul>'

        f'<h3>Verzeichnisse — automatisch aus dem Text gewonnen</h3>'
        f'<ul>'
        f'<li><a href="register/fundindex.html"><b>Fundindex</b></a> — was gefunden wurde: Münzen (nach Kaisern '
        f'geordnet), die gängigen Gefäßformen der Terra Sigillata, Ziegelstempel der Truppen und die Fundgattungen '
        f'— jeweils mit genauem Seiten- und Spaltennachweis.</li>'
        f'<li><a href="register/namen.html"><b>Namen im Text</b></a> &amp; '
        f'<a href="register/orte-index.html"><b>Orte im Text</b></a> — jeder Personen- bzw. Ortsname, den die '
        f'Auswertung im umgeschriebenen Text erkannt hat (rund {s["nner_p"]} Personen, etwa {s["nner_pl"]} Orte); '
        f'jeder ist mit den Fundstellen im Text und, wo möglich, mit den Standard-Verzeichnissen verknüpft. Weil '
        f'maschinell gelesen, sind unsichere Lesungen eigens gekennzeichnet.</li>'
        f'<li><a href="register/inschriften.html"><b>Inschriften</b></a> — die {s["nedh"]} römischen Inschriften '
        f'von den Limes-Fundorten, aus der Heidelberger Inschriften-Datenbank, nach Kastell geordnet und je direkt '
        f'zum Datensatz verlinkt.</li>'
        f'<li><a href="register/bibliographie.html"><b>Bibliographie</b></a> — die im Limesblatt zitierten Werke, '
        f'zu vollständigen Angaben aufgelöst und, wo frei verfügbar, mit Digitalisaten verlinkt.</li>'
        f'<li><a href="register/wortschatz.html"><b>Analyse</b></a> — statistische Blicke auf die Sprache: wie '
        f'sich der Wortschatz über die Jahre verschiebt, welche datierenden Münzen und Kaiser auftreten, eine '
        f'Übersicht zentraler Begriffe im Satzzusammenhang und ein grobes Maß für die Qualität der Umschrift.</li>'
        f'</ul>'

        f'<h3>Nachwirkung und Endpublikation</h3>'
        f'<ul>'
        f'<li><a href="register/rezeption.html"><b>Rezeption</b></a> — wo die spätere Forschung das Limesblatt '
        f'zitiert hat ({s["nrez"]} Belege, aus den großen Literaturdatenbanken); dazu der bemerkenswerte Befund, '
        f'dass die Zeitschrift selbst in den überregionalen Normdaten keinen eigenen Eintrag hat.</li>'
        f'<li><a href="register/orl.html"><b>ORL — die Endpublikation</b></a> — das mehrbändige Standardwerk, in '
        f'das die Feldberichte mündeten: der Bandindex (Abteilung A mit {s["norlA"]} Strecken-Bänden, Abteilung B '
        f'mit {s["norlB"]} Kastell-Lieferungen), je mit Seitenzahl, kurzer Inhaltskennzeichnung und den '
        f'vorangehenden Limesblatt-Berichten.</li>'
        f'<li><a href="register/orl-register.html"><b>ORL-Gesamtapparat</b></a> — ein zusammengeführtes Personen- '
        f'und Ortsverzeichnis über alle Bände ({s["norlpers"]} Personen, {s["norlplac"]} Orte); das '
        f'Gesamtregister, das dieses über 40 Jahre in Einzelheften erschienene Werk selbst nie besaß, dazu eine '
        f'Übersicht der großen Fund-Katalogbände und die Zuordnung Feldbericht → ORL-Band.</li>'
        f'<li><a href="register/hathitrust.html"><b>Wie die Endpublikation erschlossen wurde</b></a> — der Weg von '
        f'den eingescannten Bibliotheksseiten zu diesen Verzeichnissen.</li>'
        f'<li><a href="edit.html"><b>Bearbeiten</b></a> — ein eingebautes Werkzeug, um die Umschrift zu '
        f'korrigieren (für angemeldete Mitarbeiter).</li>'
        f'</ul>'

        f'<h2>2 · Woher die Angaben stammen</h2>'
        f'<p>Grundsatz: Was sich verlässlich <b>nachschlagen</b> lässt — Lebensdaten, Koordinaten, die Nachweise '
        f'in den Normdaten der Bibliotheken — wird <b>automatisch aus frei zugänglichen Quellen</b> geholt und in '
        f'die Daten geschrieben; das <b>Deuten und Schreiben</b> geschieht von Hand. Die historischen Seitenbilder '
        f'werden hier nicht kopiert — sie bleiben bei der Universitätsbibliothek Heidelberg und sind nur verlinkt. '
        f'Die Auswertung der Endpublikation fand in einer <b>geschützten Auswertungsumgebung</b> der digitalen '
        f'Bibliothek HathiTrust statt: dort darf man zählen und Listen erstellen, ohne den Text je erneut zu '
        f'veröffentlichen.</p>'
        f'<p class="meta">Welche offene Quelle welches Verzeichnis speist:</p>'
        f'<table class="reg"><thead><tr><th>Quelle</th><th>liefert</th></tr></thead><tbody>{srows}</tbody></table>'

        f'<h2>3 · Was die Daten erkennen lassen</h2>'
        f'<h3>Vom Feldbericht zum Standardwerk — ein Wechsel der Textsorte</h3>'
        f'<p class="meta">Ein Vergleich der Worthäufigkeiten beider Werke (die Endpublikation umfasst rund '
        f'{s["orl_words"]:,} Wörter, das Limesblatt rund {s["lb_words"]:,}) zeigt keine bloße Straffung, sondern '
        f'zwei Textsorten: das <b>Limesblatt</b> spürt die Grenzlinie auf und steckt sie ab — in der Ich-Form des '
        f'Ausgräbers, voller Geländevokabular (Pfahlreihe, Grenzgräbchen, Absteinung); die <b>Endpublikation</b> '
        f'katalogisiert die Funde, mit dem ganzen Apparat der Keramik-Typologie (Dragendorff, Knorr, Ludowici, '
        f'Rheinzabern).</p>'
        f'<h3>Was beide Werke teilen</h3>'
        f'<p class="meta">Das <a href="register/orl-register.html">Gesamtregister der Endpublikation</a> ist gegen '
        f'die Namen des Limesblatt abgeglichen — die gemeinsamen Personen (die Ausgräber Jacobi, Wolff, Schumacher, '
        f'Kofler …) verbinden Vorbericht und Standardwerk am Gegenstand.</p>'
        f'<h3>Eine Lücke in den Normdaten</h3>'
        f'<p class="meta">Bemerkenswert: das Limesblatt als Zeitschrift hat in den überregionalen Normdaten der '
        f'Bibliotheken bislang <b>keinen eigenen Eintrag</b> — ein offener Punkt seiner Erschließung, den die '
        f'<a href="register/rezeption.html">Rezeptionsseite</a> festhält.</p>'
        f'<h3>Alles hängt an der Umschrift</h3>'
        f'<p class="meta">Sämtliche Befunde aus dem Volltext ruhen auf der maschinellen Umschrift der alten '
        f'Frakturschrift: wo diese fehlerhaft ist, ist es der Befund auch. Die <a href="register/wortschatz.html">'
        f'Analyse</a> schätzt die Qualität ab, und die Verzeichnisse kennzeichnen unsichere Lesungen. Das ist die '
        f'methodische Grundbedingung — die Qualität der Erschließung bestimmt, was man findet.</p>'

        f'<h2>4 · Rechte &amp; Nachnutzung</h2>'
        f'<p class="meta">Editionstext, Verzeichnisse und Daten stehen unter '
        f'<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a> (© Manuel Sassmann) und dürfen mit '
        f'Namensnennung frei nachgenutzt werden. Die <b>Seitenbilder</b> © Universitätsbibliothek Heidelberg sind '
        f'urheberrechtlich geschützt und hier nur verlinkt, nicht erneut veröffentlicht. Quellcode und Textdateien '
        f'liegen offen bei <a href="https://github.com/pleuston/limesblatt-edition">GitHub</a>.</p>')

def main():
    os.makedirs(os.path.join(DOCS,"volumes"), exist_ok=True)
    os.makedirs(os.path.join(DOCS,"register"), exist_ok=True)
    for sub in ("tei","registers","data","assets"): os.makedirs(os.path.join(DOCS,sub), exist_ok=True)
    # TEI/Register zum Download/Reuse mitkopieren
    for f in glob.glob(os.path.join(REPO,"tei","*.xml")): shutil.copy(f, os.path.join(DOCS,"tei"))
    for f in glob.glob(os.path.join(REPO,"registers","*.xml")): shutil.copy(f, os.path.join(DOCS,"registers"))
    for f in glob.glob(os.path.join(REPO,"geo","*.geojson")): shutil.copy(f, os.path.join(DOCS,"data"))

    for f in glob.glob(os.path.join(REPO,"tei","*.xml")):   # Token→Band für interne Selbstverweise
        nr = int(re.search(r'limesblatt-bd(\d+)-', os.path.basename(f)).group(1))
        for tk in re.findall(r'<surface xml:id="f_([^"]+)"', open(f, encoding="utf-8").read()):
            TOK2BAND[tk] = nr
    volumes = sorted((load_volume(f) for f in glob.glob(os.path.join(REPO,"tei","*.xml"))), key=lambda v: v["nr"])
    if "--volumes-only" in __import__("sys").argv:   # CI-Rebuild nach TEI-Edit: nur Bandseiten aus dem (editierten) TEI
        _np = os.path.join(REPO, "data", "ner_places.json")
        PLA = {e["name"].split("(")[0].strip().lower() for e in (json.load(open(_np, encoding="utf-8")) if os.path.exists(_np) else []) if len(e["name"]) > 3}
        toc = build_toc(PLA)
        for v in volumes:
            b, h = vol_page(v, toc.get(v["nr"], []))
            open(os.path.join(DOCS,"volumes",f"bd{v['nr']}.html"),"w",encoding="utf-8").write(page(v["label"], b, 1, h))
        print(f"--volumes-only: {len(volumes)} Bandseiten + tei/ neu gebaut")
        return
    persons = load_register(os.path.join(REPO,"registers","persons.xml"), "person")
    places  = load_register(os.path.join(REPO,"registers","places.xml"), "place")
    strecken = load_strecken(os.path.join(REPO,"registers","strecken.xml"))
    str_by_id = {s["id"]: s for s in strecken}
    pname = {p["id"]: p["name"] for p in persons}
    sp = os.path.join(REPO,"geo","sites.geojson")
    sites = json.load(open(sp,encoding="utf-8")).get("features",[]) if os.path.exists(sp) else []
    # DARE-Stelle → geografisch nächste Strecke (Punkt-zu-Trassen-Distanz). Füllt auch kastelllose
    # Abschnitte und korrigiert Fehlzuordnungen, die das alte „nächstes Kastell"-Verfahren erzeugte.
    numid = {int(s["nummer"]): s["id"] for s in strecken if s.get("nummer", "").strip().isdigit()}
    paths = [(numid[n], p) for n, p in STRECKE_PATH.items() if n in numid]
    dare_strecke, strecke_sites = {}, defaultdict(list)
    for f in sites:
        g = f.get("geometry", {}); pr = f.get("properties", {})
        if g.get("type") != "Point" or not paths: continue
        lo, la = g["coordinates"][:2]
        best, bd = None, 1e9
        for sid, path in paths:
            d = _p2path((la, lo), path)
            if d < bd: bd, best = d, sid
        if best is not None and bd <= 0.135:            # ~15 km zur Trasse → Limes-Stelle dieses Abschnitts
            dare_strecke[pr.get("id")] = best; strecke_sites[best].append(pr)
    print(f"DARE-Stellen einer Strecke zugeordnet: {len(dare_strecke)}/{len(sites)} "
          f"({len(strecke_sites)}/{len(strecken)} Strecken belegt)")
    digs, str_forts = defaultdict(list), defaultdict(list)   # Person→Orte (Ausgräber), Strecke→Orte
    for pl in places:
        for d in pl.get("diggers", []):
            if d in pname: digs[d].append(pl)
        if pl.get("strecke_id"): str_forts[pl["strecke_id"]].append(pl)

    occ, seen = defaultdict(list), set()      # Entität → [(Band, Anker, Druckseite)], aus den TEI-Inline-Tags
    dare_hits, dseen = defaultdict(list), set()
    tok2anchor = {}                           # (Band, IIIF-Token) → erster Spaltenanker (für NER-Seitenrefs)
    for v in volumes:
        for p in v["pages"]:
            tok2anchor.setdefault((v["nr"], p["img_tok"]), p["anchor"])
            for eid in p["ents"]:
                key = (eid, v["nr"], p["anchor"])
                if key not in seen:
                    seen.add(key); occ[eid].append((v["nr"], p["anchor"], p["printed"]))
            for did in p["dents"]:
                key = (did, v["nr"], p["anchor"])
                if key not in dseen:
                    dseen.add(key); dare_hits[did].append((v["nr"], p["anchor"], p["printed"]))
            for cid in p.get("cites", []):                # Literaturverweise (TEI <ref target>)
                key = (cid, v["nr"], p["anchor"])
                if key not in seen:
                    seen.add(key); occ[cid].append((v["nr"], p["anchor"], p["printed"]))

    _np = os.path.join(REPO, "data", "ner_places.json")
    PLA = {e["name"].split("(")[0].strip().lower() for e in (json.load(open(_np, encoding="utf-8")) if os.path.exists(_np) else []) if len(e["name"]) > 3}
    toc = build_toc(PLA)
    corpus = []
    for v in volumes:
        b, h = vol_page(v, toc.get(v["nr"], []))
        open(os.path.join(DOCS,"volumes",f"bd{v['nr']}.html"),"w",encoding="utf-8").write(page(v["label"], b, 1, h))
        for p in v["pages"]:
            if p["text"]: corpus.append({"id":f"{v['nr']}-{p['anchor']}","vol":v["nr"],"anchor":p["anchor"],
                                         "pp":p["printed"],"label":v["label"],"text":p["text"]})
    json.dump(corpus, open(os.path.join(DOCS,"data","search.json"),"w",encoding="utf-8"), ensure_ascii=False)

    print(f"DARE-Inline-Tags im Lesetext: {len(dare_hits)}/{len(sites)} Stellen verlinkt")

    open(os.path.join(DOCS,"register","persons.html"),"w",encoding="utf-8").write(page("Personenregister", persons_page(persons, occ, digs), 1))
    plb, plh = places_page(places, occ, pname, str_by_id, sites, dare_hits)
    open(os.path.join(DOCS,"register","places.html"),"w",encoding="utf-8").write(page("Ortsregister", plb, 1, plh))
    def _orl_load(name):
        for base in (os.path.join(REPO, "data"), os.path.join(REPO, "..", "limes", "tools")):
            p = os.path.join(base, name)
            if os.path.exists(p): return json.load(open(p, encoding="utf-8"))
        return None
    orl_idx = _orl_load("orl_index.json") or {"abteilung_A_strecken": [], "abteilung_B_kastelle": []}
    orl_lex = _orl_load("orl_vs_limesblatt.json")
    open(os.path.join(DOCS,"register","strecken.html"),"w",encoding="utf-8").write(page("Strecken", strecken_page(strecken, str_forts, persons, pname, strecke_sites, orl_idx, volumes), 1))
    nerd = os.path.join(REPO, "data")
    def loadj(fn): return json.load(open(os.path.join(nerd,fn),encoding="utf-8")) if os.path.exists(os.path.join(nerd,fn)) else ([] if "ner_" in fn else {})
    ner_p, ner_pl = loadj("ner_persons.json"), loadj("ner_places.json")
    rec_p, rec_pl = loadj("recon_persons.json"), loadj("recon_places.json")
    nb, nh = ner_index_page(ner_p, "persons", tok2anchor, rec_p)
    open(os.path.join(DOCS,"register","namen.html"),"w",encoding="utf-8").write(page("Namen im Limesblatt", nb, 1, nh))
    ob, oh = ner_index_page(ner_pl, "places", tok2anchor, rec_pl)
    open(os.path.join(DOCS,"register","orte-index.html"),"w",encoding="utf-8").write(page("Orte im Limesblatt", ob, 1, oh))
    # GeoJSON der im Volltext genannten, verorteten Orte (Map-Layer)
    nsites = []; ner_attention = defaultdict(lambda: [0, 0])   # sid -> [Erwähnungen, Orte]
    for it in ner_pl:
        r = rec_pl.get(it["name"].lower())
        if not r or not r.get("geo"): continue
        la, lo = r["geo"]; m = len(it.get("pages", []))
        nsites.append({"type":"Feature","geometry":{"type":"Point","coordinates":[lo, la]},
            "properties":{"name":it["name"],"kind":it.get("kind",""),"n":m,
                          "gazId":r.get("gazId",""),"src":r.get("src","")}})
        if paths:                                              # → geografisch nächste Strecke (Trassen-Distanz)
            best, bd = None, 1e9
            for sid, path in paths:
                d = _p2path((la, lo), path)
                if d < bd: bd, best = d, sid
            if best is not None and bd <= 0.135:
                ner_attention[best][0] += m; ner_attention[best][1] += 1
    attention = sorted(((str_by_id.get(sid, {}).get("name") or sid, v[0], v[1]) for sid, v in ner_attention.items()),
                       key=lambda x: -x[1])
    json.dump({"type":"FeatureCollection","features":nsites},
              open(os.path.join(DOCS,"data","ner-sites.geojson"),"w",encoding="utf-8"), ensure_ascii=False)
    pm = sum(1 for v in rec_p.values() if v); om = sum(1 for v in rec_pl.values() if v and v.get("geo"))
    print(f"Volltext-Index (LLM-NER): {len(ner_p)} Namen ({pm} reconciled), {len(ner_pl)} Orte ({om} verortet → ner-sites.geojson)")
    open(os.path.join(DOCS,"register","wortschatz.html"),"w",encoding="utf-8").write(page("Wortschatz & Konkordanz", wortschatz_page(volumes, attention, orl_lex), 1))
    open(os.path.join(DOCS,"register","fundindex.html"),"w",encoding="utf-8").write(page("Fundindex", fundindex_page(volumes), 1))
    bibls = load_bibl(os.path.join(REPO, "registers", "bibliography.xml"))
    open(os.path.join(DOCS,"register","bibliographie.html"),"w",encoding="utf-8").write(page("Bibliographie", bibliography_page(bibls, occ), 1))
    _edhp = os.path.join(REPO, "..", "limes", "tools", "edh_limes.json")
    edh = json.load(open(_edhp, encoding="utf-8")) if os.path.exists(_edhp) else {"kastelle": [], "total": 0}
    open(os.path.join(DOCS,"register","inschriften.html"),"w",encoding="utf-8").write(page("Inschriften (EDH)", inscriptions_page(edh), 1))
    print(f"EDH-Inschriften: {edh.get('total',0)} von {len(edh.get('kastelle',[]))} Fundorten → register/inschriften.html")
    _recp = os.path.join(REPO, "..", "limes", "tools", "rezeption.json")
    rez = json.load(open(_recp, encoding="utf-8")) if os.path.exists(_recp) else {"items": [], "summary": {}, "normdata": {}}
    open(os.path.join(DOCS,"register","rezeption.html"),"w",encoding="utf-8").write(page("Rezeption", reception_page(rez), 1))
    print(f"Rezeption: {rez.get('summary',{}).get('total',0)} Belege → register/rezeption.html")
    # ORL-Register/Analyse-Seiten (orl_idx/orl_lex + _orl_load bereits vor den Strecken geladen)
    orl_reg = _orl_load("orl_register.json") or {"persons": [], "places": [], "counts": {}}
    if orl_idx.get("abteilung_B_kastelle"):
        open(os.path.join(DOCS,"register","orl.html"),"w",encoding="utf-8").write(page("ORL", orl_page(orl_idx, orl_lex), 1))
        open(os.path.join(DOCS,"register","orl-register.html"),"w",encoding="utf-8").write(page("ORL — Gesamtapparat", orl_apparatus_page(orl_reg, orl_idx), 1))
        open(os.path.join(DOCS,"register","hathitrust.html"),"w",encoding="utf-8").write(page("HathiTrust", hathitrust_page(orl_idx, orl_reg, orl_lex), 1))
        print(f"ORL: Abt. A {len(orl_idx.get('abteilung_A_strecken',[]))} + Abt. B {len(orl_idx.get('abteilung_B_kastelle',[]))} "
              f"→ register/orl.html · orl-register.html · hathitrust.html")
    stats = {"nvol": len(volumes), "npers": len(persons), "nplac": len(places),
             "nner_p": len(ner_p), "nner_pl": len(ner_pl),
             "nedh": edh.get("total", 0), "nrez": rez.get("summary", {}).get("total", 0),
             "norlA": (orl_idx or {}).get("counts", {}).get("abt_A", 0),
             "norlB": (orl_idx or {}).get("counts", {}).get("abt_B", 0),
             "norlpers": orl_reg.get("counts", {}).get("persons", 0),
             "norlplac": orl_reg.get("counts", {}).get("places", 0),
             "orl_words": (orl_lex or {}).get("orl_words", 0), "lb_words": (orl_lex or {}).get("lb_words", 0)}
    open(os.path.join(DOCS,"dokumentation.html"),"w",encoding="utf-8").write(page("Dokumentation", documentation_page(stats), 0))
    print(f"Dokumentation → dokumentation.html")
    ib, ih = index_page(volumes, toc)
    open(os.path.join(DOCS,"index.html"),"w",encoding="utf-8").write(page("Startseite", ib, 0, ih))
    print(f"docs/: index + {len(volumes)} Bände + 3 Register (Personen {len(persons)}, Orte {len(places)}, "
          f"Strecken {len(strecken)}) · Suchindex {len(corpus)} Seiten · Ausgräber-Links {sum(len(v) for v in digs.values())}")

if __name__ == "__main__":
    main()
