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
def _entsub(inner):
    """Inline-Eigennamen-Tags → HTML-Register-Links (für Seiten und Überschriften)."""
    def ent(m):
        tag, xid, txt = m.group(1), m.group(2), m.group(3)
        cls, reg = ("persName","persons") if tag=="persName" else ("placeName","places")
        return f'<a class="ent {cls}" href="../register/{reg}.html#{xid}" title="{cls}">{txt}</a>'
    body = re.sub(r'<placeName ref="dare:([^"]+)"[^>]*>(.*?)</placeName>',
                  lambda m: f'<a class="ent placeName dare" href="../register/places.html#dare_{m.group(1)}" title="weitere Limesstelle (DARE)">{m.group(2)}</a>',
                  inner, flags=re.S)
    return re.sub(r'<(persName|placeName) ref="#([^"]+)"[^>]*>(.*?)</\1>', ent, body, flags=re.S)

def render_page(inner):
    """inner = <cb/> + <p>…</p>-Block einer Spalte; Inline-Tags → HTML-Spans/Links."""
    inner = re.sub(r'<cb\b[^>]*/>', '', inner)          # Spaltenmarke aus dem Lesetext entfernen
    if "<gap" in inner: return '<p class="gap">[leere bzw. nicht erfasste Seite]</p>'
    return _entsub(inner)  # bereits <p>…</p>

def render_head(inner):
    """Volle-Breite-Überschrift (<head>) einer Kachel → eigene HTML-Zeile."""
    return f'<p class="colhead">{_entsub(inner.strip())}</p>'

PB_RE = re.compile(r'<head>(.*?)</head>'
                   r'|<pb n="([^"]*)" facs="#f_([^"]+)" xml:id="pb_[^"]*?_([A-Za-z0-9]+)" type="([^"]*)"/>'
                   r'(.*?)(?=<pb |<head>|</div>)', re.S)

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
        printed, img_tok, col, typ, inner = m.group(2), m.group(3), m.group(4) or "a", m.group(5) or "", m.group(6).strip()
        anchor = f"{img_tok}-{col}"
        pages.append({"img_tok": img_tok, "printed": printed, "col": col, "anchor": anchor, "tok": anchor,
                      "type": typ, "head": pending_head, "html": render_page(inner),
                      "text": unesc(strip_tags(re.sub(r'<cb\b[^>]*/>', '', inner))).strip(),
                      "ents": re.findall(r'ref="#([^"]+)"', inner),
                      "dents": re.findall(r'ref="dare:([^"]+)"', inner)})
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

# ---------- HTML-Shell ----------
def page(title, body, depth=0, head=""):
    up = "../" * depth
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Limesblatt-Edition</title>
<link rel="stylesheet" href="{up}assets/style.css">{head}</head><body>
<header><a class="home" href="{up}index.html">📕 Limesblatt-Edition</a>
<nav><a href="{up}index.html">Bände</a> · <a href="{up}register/persons.html">Personen</a> · <a href="{up}register/places.html">Orte</a> · <a href="{up}register/strecken.html">Strecken</a> · <a href="{up}register/namen.html">Namen</a> · <a href="{up}register/wortschatz.html">Analyse</a> · <a href="{up}index.html#suche">Suche</a></nav></header>
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
    for t, num, title, br in (toc or []): tmap.setdefault(t, {})[num] = (title, br)
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
    head = ('<script src="../assets/openseadragon.min.js"></script>')
    inh = ""
    if toc:
        items = "".join(f'<li><a href="#art-{num}"><b>{num}.</b> {html.escape(title)}</a>{(" " + html.escape(br)) if br else ""} '
                        f'<span class="meta">S. {html.escape(t)}</span></li>' for t, num, title, br in toc)
        inh = f'<details class="inhalt" open><summary>Inhalt — {len(toc)} nummerierte Berichte</summary><ul class="toc">{items}</ul></details>'
    body = f"""<h1>Limesblatt · {html.escape(v['label'])}</h1>
<p class="meta">IIIF-Faksimile: <a href="{IIIF_MAN.format(slug=slug)}">Manifest</a> (UB Heidelberg) ·
TEI: <a href="../tei/{teiname}">XML</a></p>
{inh}
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
            f'<a href="{html.escape(I["DeutscheBiographie"])}">Dt. Biographie</a>' if I.get("DeutscheBiographie") else "",
            f'<a href="{html.escape(I["Propylaeum-VITAE"])}">Propylaeum-VITAE</a>' if I.get("Propylaeum-VITAE") else "", kal])
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

def strecken_page(strecken, str_forts, persons, pname, strecke_sites):
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
        ds, seen_n = [], set()
        for x in sorted(strecke_sites.get(s["id"], []), key=lambda x: x.get("name", "")):
            n = x.get("name", "?")
            if n not in seen_n: seen_n.add(n); ds.append(x)
        if ds:
            shown = ", ".join(f'<a href="places.html#dare_{html.escape(str(x.get("id","")))}">{html.escape(x.get("name","?"))}</a>' for x in ds[:24])
            extra += f'<div class="x">○ Türme/Stellen (DARE, {len(ds)}): {shown}{" +"+str(len(ds)-24) if len(ds) > 24 else ""}</div>'
        if forts: extra += f'<div class="x">🗺️ <a href="places.html?strecke={s["id"]}">Auf der Karte zeigen</a></div>'
        bet = []
        if komm: bet.append("Kommissar (Region): " + ", ".join(
            f'<a href="persons.html#{p["id"]}">{html.escape(p["name"])}</a>' for p in komm))
        if dig_ids: bet.append("Ausgräber: " + ", ".join(
            f'<a href="persons.html#{d}">{html.escape(pname[d])}</a>' for d in dig_ids))
        if bet: extra += '<div class="x">👤 Beteiligte — ' + " · ".join(bet) + '</div>'
        cards.append(f'<article class="card wide" id="{s["id"]}"><div class="cbody">'
                     f'<h3>{html.escape(s["name"])}</h3><div class="role">{meta}</div>{extra}</div></article>')
    return (f'<h1>Strecken</h1><p class="meta">{len(strecken)} Limes-Abschnitte mit Kastellen, beteiligten '
            f'Personen (Kommissare regional, Ausgräber aus den Kastellen) und den DARE-Stellen entlang der Linie. '
            f'Letztere sind dem <i>nächstgelegenen verankerten Kastell</i> (≤ ~22 km) zugeordnet — Abschnitte ohne '
            f'eigenes Kastell bleiben hier leer (ihre Stellen erscheinen weiter auf der Karte und im Ortsregister).</p>'
            f'<div class="cards">{"".join(cards)}</div>')

def index_page(volumes, toc=None):
    toc = toc or {}
    bl = []
    for v in volumes:
        ents = toc.get(v["nr"], [])
        items = "".join(f'<li><a href="volumes/bd{v["nr"]}.html#art-{num}"><b>{num}.</b> {html.escape(title)}</a>'
                        f'{(" " + html.escape(br)) if br else ""}</li>' for tok, num, title, br in ents)
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
<li><a href="register/namen.html">Namen im Limesblatt</a> — vollständiger Namenindex aus dem Volltext (LLM-NER)</li>
<li><a href="register/orte-index.html">Orte im Limesblatt</a> — vollständiger Ortsindex aus dem Volltext (LLM-NER)</li>
<li><a href="register/wortschatz.html">Textanalyse</a> — diachroner Wortschatz, ORL-Gegenprobe, Münzkaiser-Chronologie, Truppen, Zitate, OCR-Qualität + KWIC-Konkordanz</li></ul>
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
        lc = ' class="lc"' if it.get("cert") != "high" else ""
        lis.append(f'<li{lc}><b>{disp}</b>{em}{ref} — <span class="pgs">{pl}</span></li>')
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

def analysis_sections(volumes):
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

def wortschatz_page(volumes, attention=None):
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
            f'Sprung zu: <a href="#orl">ORL-Gegenprobe</a> · <a href="#muenzen">Münzkaiser</a> · <a href="#truppen">Truppen</a> · '
            f'<a href="#zitate">Zitate</a> · <a href="#ocr">OCR-Qualität</a> · <a href="#kwic">Konkordanz</a>.</p>'
            f'<div class="tmwrap">{chart}</div>'
            f'<h2>Term-Gruppen über die Zeit</h2>{table}'
            f'<p class="meta">Befund: Steinbau dominiert; Holzbefund-Vokabular ist präsent und steigt mittig (Bd. 4–6); '
            f'explizite Datierungssprache fehlt fast; „principia" kommt nicht vor (man schrieb „Prätorium").</p>'
            + att + analysis_sections(volumes) + "".join(kw))

TOC_PAT   = re.compile(r"(?<![A-Za-z0-9])(\d{1,3})[._]\s+([A-ZÄÖÜ][A-Za-zäöüß0-9 .„“”\-]{1,55}?)[.*)]+\s*(\[[^\]]{0,70}\])?")
TOC_NOISE = re.compile(r"^(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|De[cz]ember|"
                       r"Jan|Feb|Mär|Apr|Jun|Jul|Aug|Sept|Okt|Nov|Dez|Aufl|AuB|Auli|Aull|Jahr\w*|Ausgeg\w*|Druck|"
                       r"Verlag|Legion|Turm|Auf|Vgl|Nr|Forts|Seite|Band|Heft)\b", re.I)
TOC_TYP   = re.compile(r"^(Limes|Kastell|Station|Zwischenkastell|Strecke|Wachtturm|Mümling|Pfahl|Teilstrecke)", re.I)

def build_toc(PLA):
    """{nr: [(tok, Nr, Titel, Klammer)]} aus den nummerierten Bericht-Überschriften.

    Zwei-Pass: validierte Treffer (bekannter Ort/„Limes…"/[Klammer]) bilden als monotone
    Folge die Anker; danach werden die Lücken zwischen Ankern mit den fehlenden Nummern
    gefüllt (z. B. 77 zwischen 74 und 87). Daten/Unterpunkte/Register fallen heraus.
    """
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
        nr, tok, num, title, br, _ = cands[j]; toc.setdefault(nr, []).append((tok, num, title, br))
    return toc

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
    # DARE-Stelle → Strecke des nächstgelegenen (strecke-verankerten) Kastells (cos-gewichtet, Cap ~40 km)
    anchors = []
    for pl in places:
        if pl.get("strecke_id") and len(pl.get("geo") or []) == 2:
            try: la, lo = float(pl["geo"][0]), float(pl["geo"][1]); anchors.append((la, lo, pl["strecke_id"]))
            except ValueError: pass
    dare_strecke, strecke_sites = {}, defaultdict(list)
    for f in sites:
        g = f.get("geometry", {}); pr = f.get("properties", {})
        if g.get("type") != "Point" or not anchors: continue
        lo, la = g["coordinates"][:2]
        best, bd = None, 1e9
        for ala, alo, sid in anchors:
            d = (la - ala) ** 2 + (lo - alo) ** 2 * 0.42
            if d < bd: bd, best = d, sid
        if best is not None and bd <= 0.20 ** 2:        # ~22 km: nur konfident nahe Stellen
            dare_strecke[pr.get("id")] = best; strecke_sites[best].append(pr)
    print(f"DARE-Stellen einer Strecke zugeordnet: {len(dare_strecke)}/{len(sites)} ({len(strecke_sites)} Strecken)")
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
    open(os.path.join(DOCS,"register","strecken.html"),"w",encoding="utf-8").write(page("Strecken", strecken_page(strecken, str_forts, persons, pname, strecke_sites), 1))
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
        if anchors:                                            # → nächstes strecke-verankertes Kastell
            best, bd = None, 1e9
            for ala, alo, sid in anchors:
                d = (la - ala) ** 2 + (lo - alo) ** 2 * 0.42
                if d < bd: bd, best = d, sid
            if best is not None and bd <= 0.20 ** 2:
                ner_attention[best][0] += m; ner_attention[best][1] += 1
    attention = sorted(((str_by_id.get(sid, {}).get("name") or sid, v[0], v[1]) for sid, v in ner_attention.items()),
                       key=lambda x: -x[1])
    json.dump({"type":"FeatureCollection","features":nsites},
              open(os.path.join(DOCS,"data","ner-sites.geojson"),"w",encoding="utf-8"), ensure_ascii=False)
    pm = sum(1 for v in rec_p.values() if v); om = sum(1 for v in rec_pl.values() if v and v.get("geo"))
    print(f"Volltext-Index (LLM-NER): {len(ner_p)} Namen ({pm} reconciled), {len(ner_pl)} Orte ({om} verortet → ner-sites.geojson)")
    open(os.path.join(DOCS,"register","wortschatz.html"),"w",encoding="utf-8").write(page("Wortschatz & Konkordanz", wortschatz_page(volumes, attention), 1))
    ib, ih = index_page(volumes, toc)
    open(os.path.join(DOCS,"index.html"),"w",encoding="utf-8").write(page("Startseite", ib, 0, ih))
    print(f"docs/: index + {len(volumes)} Bände + 3 Register (Personen {len(persons)}, Orte {len(places)}, "
          f"Strecken {len(strecken)}) · Suchindex {len(corpus)} Seiten · Ausgräber-Links {sum(len(v) for v in digs.values())}")

if __name__ == "__main__":
    main()
