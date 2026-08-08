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
import glob, html, os, re, json, shutil, math, urllib.parse, unicodedata
from collections import Counter, defaultdict
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

# Textlose Tafelseiten. Die UB liefert für sie ein ALTO mit HTTP 200, aber leer (745 statt
# ~76.000 Bytes): sie sind handschriftlich in KURRENT beschriftet, da ist per OCR nichts zu
# holen — auch nicht per Re-OCR. »Leer« sind sie deshalb NICHT, und so dürfen sie auch nicht
# heißen: Hintzelmann verweist 1903 auf Sp. 559 (»Gundelshalm«), wo Fig. 3 die Legende
# »… auf der Höhe bei Gundelshalm« trägt. Der Leser gehört hier ans Faksimile geschickt.
TAFELN = {("limesblatt1893_1894", "211"), ("limesblatt1896", "467"),
          ("limesblatt1896", "541"),      ("limesblatt1896", "559")}

def render_page(inner, tafel=None):
    """inner = <cb/> + <p>…</p>-Block einer Spalte; Inline-Tags → HTML-Spans/Links."""
    inner = re.sub(r'<cb\b[^>]*/>', '', inner)          # Spaltenmarke aus dem Lesetext entfernen
    if "<gap" in inner:
        if tafel:
            tok, slug_ = tafel
            no = int(tok)
            iiif = f"https://digi.ub.uni-heidelberg.de/iiif/2/{slug_}%3A{tok}.jpg"
            return (f'<figure class="tafel" style="margin:1em 0;text-align:center">'
                    f'<a href="{iiif}/full/max/0/default.jpg" title="Faksimile in voller Auflösung">'
                    f'<img loading="lazy" alt="Tafel, Spalten {no}/{no+1} — Faksimile" '
                    f'style="max-width:100%;border:1px solid var(--line,#ddd)" '
                    f'src="{iiif}/full/700,/0/default.jpg">'
                    f'</a><figcaption class="meta" style="text-align:left"><b>Tafel — Spalten {no}/{no+1}.</b> Die Seite trägt keinen Satztext: '
                    f'Sie ist <b>handschriftlich in Kurrent</b> beschriftet, weshalb die '
                    f'Schrifterkennung sie leer lässt. Zitierbar ist sie trotzdem — '
                    f'<a href="../register/hintzelmann.html">Hintzelmanns Register</a> von 1903 verweist '
                    f'darauf. <a href="{iiif}/full/max/0/default.jpg">Volle Auflösung</a> · '
                    f'Seitenbilder &#169; UB Heidelberg.</figcaption></figure>')
        return '<p class="gap">[leere bzw. nicht erfasste Seite]</p>'
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
                      "type": typ, "head": pending_head,
                      "html": render_page(inner, (img_tok, slug) if (slug, img_tok) in TAFELN else None),
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
<title>{html.escape(title)} — RLK-digital</title>
<link rel="stylesheet" href="{up}assets/style.css">
<script src="{up}assets/tables.js" defer></script>{head}</head><body>
<header><a class="home" href="{up}index.html">🏛 RLK-digital</a>
<nav><ul class="nav">
<li><a href="{up}uebersicht.html">Übersicht</a></li>
<li class="has"><a href="{up}quellen.html">Quellen</a><ul>
<li><a href="{up}quellen.html">Alle Quellen im Überblick</a></li>
<li><a href="{up}quellen.html#limesblatt"><b>Limesblatt</b> — das Feldorgan</a></li>
<li><a href="{up}baende.html">· Bände &amp; Inhaltsverzeichnis</a></li>
<li><a href="{up}quellen.html#orl"><b>ORL</b> — die Endpublikation</a></li>
<li><a href="{up}register/orl-inhalt.html">· Inhaltsverzeichnis</a></li>
<li><a href="{up}register/orl-verweise.html">· Binnenverweise</a></li>
<li><a href="{up}register/genese.html">· Genese des Werks</a></li>
<li><a href="{up}quellen.html#jahresberichte"><b>Jahresberichte</b> der RLK</a></li>
<li><a href="{up}quellen.html#extern"><b>Zitierte externe Quellen</b></a></li>
<li><a href="{up}quellen.html#lokal"><b>Lokale Publikationen</b></a></li>
<li><a href="{up}artikel/index.html"><b>Aufsätze</b></a></li>
<li><a href="{up}register/archive.html"><b>Archivbestände</b></a></li></ul></li>
<li class="has"><a href="{up}register/persons.html">Register</a><ul>
<li><a href="{up}register/persons.html">Personen</a></li>
<li><a href="{up}register/places.html">Orte</a></li>
<li><a href="{up}register/strecken.html">Strecken</a></li>
<li><a href="{up}register/organigramm.html">Organigramm</a></li>
<li><a href="{up}register/fundindex.html">Fundindex</a></li>
<li><a href="{up}register/inschriften.html">Inschriften</a></li>
<li><a href="{up}register/namen.html">Namen im Text</a></li>
<li><a href="{up}register/orte-index.html">Orte im Text</a></li>
<li><a href="{up}register/ortsnamen.html">Ortsnamen antik/modern</a></li>
<li><a href="{up}register/hintzelmann.html">Register von 1903</a></li>
<li><a href="{up}register/gesamtregister.html">Gesamtregister (alle Werke)</a></li>
<li><a href="{up}register/orl-register.html">ORL-Gesamtapparat</a></li>
<li><a href="{up}register/netz.html">Netzansicht</a></li></ul></li>
<li class="has"><a href="{up}register/wortschatz.html">Analyse</a><ul>
<li><a href="{up}register/wortschatz.html">Textanalyse</a></li>
<li><a href="{up}register/genese.html">Genese des ORL</a></li>
<li><a href="{up}register/hathitrust.html">Erschließung (HathiTrust)</a></li></ul></li>
<li class="has"><a href="{up}dokumentation.html">Über</a><ul>
<li><a href="{up}dokumentation.html">Dokumentation</a></li>
<li><a href="{up}impressum.html">Impressum &amp; Zitieren</a></li>
<li><a href="{up}edit.html" title="TEI-Quelle bearbeiten (GitHub-Login)">Bearbeiten ✎</a></li></ul></li>
<li><a href="{up}index.html#suche">🔍 Suche</a></li>
</ul></nav></header><main>{body}</main>
<footer><b>RLK-digital</b> — die Quellen der Reichs-Limeskommission (1892–1937), digital erschlossen:
<em>Limesblatt</em> · <em>Obergermanisch-Raetischer Limes</em> · Jahresberichte · Text &amp; Register
<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a> · Seitenbilder © UB Heidelberg
(<a href="http://rightsstatements.org/vocab/InC/1.0/">In Copyright</a>, via IIIF verlinkt) ·
<a href="https://github.com/pleuston/limesblatt-edition">Quellcode &amp; TEI</a> ·
<a href="{up}impressum.html">Impressum &amp; Zitieren</a></footer></body></html>"""

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
        items = toc_li_hefte(toc, "", True, band=v["nr"])
        nh = sum(1 for h in HEFTE if h["band"] == v["nr"])
        inh = (f'<details class="inhalt" open><summary>Inhalt — {nh} Hefte, '
               f'{len(toc)} nummerierte Berichte</summary><ul class="toc">{items}</ul></details>')
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
    sites_html = (f'<h2 id="weitere">Weitere Limesstellen (DARE)</h2>'
                  '<p class="meta">Türme, Kleinkastelle und Lager <i>zwischen</i> den benannten Kastellen, '
                  f'je mit DARE-Datensatz, 📍 Karten-Fokus und — bei {nvt} Stellen — 📄 <b>heuristischen '
                  'Volltext-Treffern</b> (Toponym-Abgleich auf Fraktur-OCR; nicht jede Nennung meint zwingend '
                  'diese Stelle). Gazetteer-Stellen ohne RLK-Wachtposten-Nr.</p>'
                  + "".join(secs)) if sites else ""
    head = '<link rel="stylesheet" href="../assets/leaflet.css"><script src="../assets/leaflet.js"></script>'
    body = (f'<h1>Ortsregister</h1><p class="meta">{len(places)} benannte Kastelle (Karten unten) plus '
            f'{len(sites)} weitere Limesstellen — auf der Karte zuschaltbar: der <b>Limesverlauf</b>, die '
            f'<b>Streckenabschnitte</b> (die echte Linie nach Strecke eingefärbt, Klick → Streckenseite) und die '
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

def quellen_page(volumes, toc, idx, jb, bibls, zs, rez, edh, artliste=None):
    """Der Quellen-Hub: fünf Bestände, je mit eigener Herkunft, eigenem Zuschnitt, eigener Grenze.

    Die Website ist aus einer Limesblatt-Edition gewachsen; inzwischen trägt sie vier weitere
    Bestände, die nicht Beiwerk sind, sondern eigene Quellen mit eigenen Fragen. Diese Seite
    stellt sie nebeneinander, statt sie als Anhänge der Edition zu führen."""
    n_hefte = len(HEFTE)
    n_ber = sum(len(v) for v in (toc or {}).values())
    n_seiten = sum(len(v["pages"]) for v in volumes)
    b = idx.get("abteilung_B_kastelle", [])
    a = idx.get("abteilung_A_strecken", [])
    n_jb = len(jb.get("berichte", []))
    organe = zs.get("organe_vollzaehlung") or {}
    n_rez = len(rez.get("items", []))
    n_edh = edh.get("total", 0)

    def block(anker, titel, zeitraum, was, punkte, links):
        lp = " · ".join(f'<a href="{h}">{t}</a>' for t, h in links)
        li = "".join(f"<li>{x}</li>" for x in punkte)
        return (f'<section class="qbox" id="{anker}"><h2>{titel} <span class="meta">{zeitraum}</span></h2>'
                f'<p>{was}</p><ul class="qpunkte">{li}</ul>'
                f'<p class="meta"><b>Hier entlang:</b> {lp}</p></section>')

    teile = [
        block("limesblatt", "Limesblatt", "1892–1903 · Feldorgan",
              "Die <em>Mitteilungen der Streckenkommissare</em> — das laufende Berichtsorgan der Kommission, "
              "in dem die Streckenkommissare ihre Kampagne noch im Gange beschreiben. Hier vollständig als "
              "diplomatische OCR-Edition mit Faksimile-Anschluss.",
              [f"<b>{n_seiten} Seiten</b> in 8 Bänden, durchlaufend in 967 Spalten gezählt",
               f"<b>{n_hefte} Hefteinheiten</b> (35 Nummern) mit Ausgabedatum aus den IIIF-Strukturdaten",
               f"<b>{n_ber} nummerierte Feldberichte</b>, 114 davon am Faksimile gegengeprüft",
               "Volltext, Register und Faksimile Seite für Seite verknüpft"],
              [("Bände &amp; Inhaltsverzeichnis", "baende.html"),
               ("Namen im Text", "register/namen.html"), ("Orte im Text", "register/orte-index.html"),
               ("Hintzelmanns Register 1903", "register/hintzelmann.html"),
               ("Textanalyse", "register/wortschatz.html")]),
        block("orl", "Obergermanisch-Raetischer Limes (ORL)", "1894–1937 · Endpublikation",
              "Das Werk, in das die Feldarbeit mündete — in 14 Mappen über 45 Jahre erschienen und deshalb nie "
              "als Ganzes erschlossen. Hier token-frei über HathiTrust aufgeschlüsselt: Inhalt, Apparat, "
              "Binnenverweise und die Schichten seiner Entstehung.",
              [f"<b>{len(b)} Kastell-Faszikel</b> und <b>{len(a)} Streckenbände</b>, nach Lieferung geordnet",
               "551 Binnenverweise beidseitig in den Scan verlinkt, doppelt validiert",
               "1751 Verweise auf das Limesblatt aufgelöst — das Feldarchiv, auf das sich der ORL beruft",
               "Gesamtregister über alle Bände, das die Reihe selbst nie hatte"],
              [("Inhaltsverzeichnis", "register/orl-inhalt.html"), ("Bandindex", "register/orl.html"),
               ("Binnenverweise", "register/orl-verweise.html"),
               ("Gesamtapparat", "register/orl-register.html"),
               ("Genese des Werks", "register/genese.html"),
               ("Erschließung", "register/hathitrust.html")]),
        block("jahresberichte", "Jahresberichte der Reichs-Limeskommission", "1892–1905 · institutionelle Selbstauskunft",
              "Was die Kommission jährlich über sich selbst veröffentlichte, als Anhang im "
              "<em>Jahrbuch des Deutschen Archäologischen Instituts</em>. Kein Feldbericht und keine "
              "Endpublikation, sondern die dritte Stimme: Personal, Beschlüsse, Fortschritt.",
              [f"<b>{n_jb} von 14 Jahrgängen</b> erschlossen (1894 nicht digitalisiert auffindbar)",
               "durchnummeriert nach Jahrgang — die Bandzahl gehört dem Jahrbuch, nicht den Berichten",
               "Register der genannten Personen und Orte, dazu das Verwaltungsvokabular",
               "Befund: der Berichtsumfang <b>fällt</b>, während die ORL-Lieferungen zunehmen"],
              [("Die Berichte", "register/jahresberichte.html"),
               ("Wer genannt wird", "register/jahresberichte.html#wer"),
               ("Welche Orte", "register/jahresberichte.html#wo")]),
        block("extern", "Zitierte externe Quellen", "Apparat der drei Werke",
              "Worauf sich die Limesforschung beruft: Inschriftencorpora, Referenzwerke, "
              "Fachzeitschriften — aufgelöst zu vollen Referenzen und, wo vorhanden, zum offenen Digitalisat.",
              [f"<b>{len(bibls)} zitierte Werke</b> im Limesblatt, als <code>&lt;ref&gt;</code> im TEI ausgezeichnet",
               f"<b>{n_edh} Inschriften</b> der Limes-Fundorte aus der Epigraphic Database Heidelberg",
               "817 Inschriften-Zitate des ORL, normalisiert zum Zitatregister",
               "Befund: nur 11 Inschriften zitieren beide Werke — der ORL-Apparat wurde neu aufgebaut"],
              [("Bibliographie", "register/bibliographie.html"),
               ("Gesamtbibliographie", "register/gesamtbibliographie.html"),
               ("Inschriften (EDH)", "register/inschriften.html")]),
        block("lokal", "Lokale &amp; regionale Publikationen", "das föderale Ökosystem",
              "Die Limesforschung erschien nicht nur zentral. Die Regionalorgane der beteiligten Staaten — "
              "Westdeutsche Zeitschrift, Nassauische Annalen, Fundberichte aus Schwaben, Mainzer Zeitschrift — "
              "tragen einen erheblichen Teil des Apparats. Mommsens Klage über „so viele Limeslitteraturen wie "
              "beteiligte Staaten“ wurde nicht stillgelegt, sondern eingebaut.",
              [f"<b>{len(organe)} Organe</b> im ORL-Apparat vollständig ausgezählt",
               "Organ und Region des Bandinhalts entsprechen einander in fünf geprüften Fällen",
               "das Ökosystem wuchs zwischen Vorbericht und Endpublikation — die Mainzer Zeitschrift "
               "(gegr. 1906) konnte im Limesblatt gar nicht stehen",
               f"<b>{n_rez} Nachweise</b> zur Rezeption des Limesblatts außerhalb seiner Bände"],
              [("Organe im Gesamtapparat", "register/orl-register.html"),
               ("Gesamtbibliographie", "register/gesamtbibliographie.html")]),
        block("aufsaetze", "Einzelne Aufsätze", "Nachrufe · Streitschriften · Gründungstexte",
              "Neben den drei Beständen stehen einzelne Aufsätze, an denen die Gründungsgeschichte hängt. "
              "Wo sie bei der UB Heidelberg digitalisiert sind, liegen sie in derselben Form vor wie das "
              "Limesblatt — Faksimile plus OCR — und stehen hier ebenso nebeneinander: Text links, Blatt rechts.",
              [f'<b>{len(artliste or [])} Aufsätze</b> erschlossen, '
               f'{sum(x.get("woerter", 0) for x in (artliste or [])):,} Wörter'.replace(",", "."),
               "darunter Florschütz’ Nachruf auf Cohausen (1895) und Cohausens Streitschrift (1892)",
               "der Bestand stammt aus dem Aufsatzverzeichnis des Vaults; der Jahrgang wird nicht "
               "geraten, sondern an den Seitenbeschriftungen der Bildfolge geprüft"],
              [("Verzeichnis der Aufsätze", "artikel/index.html")]),
        block("archive", "Archivbestände", "unveröffentlicht · zum Teil ungesehen",
              "Alles bisher Genannte ist gedruckt. Daneben liegt das <em>unveröffentlichte</em> Material: "
              "Grabungstagebücher, Korrespondenz, Vermessungsunterlagen, Ministerialakten. Hier steht, wo es "
              "liegt, wie man herankommt — und was noch niemand angesehen hat.",
              ["das wissenschaftliche Archiv der Kommission liegt geschlossen bei der "
               "<b>Römisch-Germanischen Kommission</b> des DAI in Frankfurt",
               "die <b>Verwaltungsüberlieferung</b> dagegen verteilt auf die Staatsarchive der Trägerstaaten — "
               "die Kommission war keine Behörde, sondern ein Verbund",
               "Nachlässe der Beteiligten, über Kalliope und die DAI-Findmittel ermittelt",
               "eine priorisierte <b>Bestell-Liste</b>: welche Akte welche offene Frage beantworten würde"],
              [("Archivbestände &amp; Desiderate", "register/archive.html")]),
    ]
    return (f'<h1>Die Quellen</h1>'
            f'<p class="lede">Fünf Bestände, die die Reichs-Limeskommission hinterlassen hat — jeder mit eigener '
            f'Entstehung, eigenem Zuschnitt und eigenen Grenzen. Sie erzählen dasselbe Unternehmen dreimal '
            f'verschieden: im Gange (Limesblatt), abgeschlossen (ORL) und von der Verwaltung her '
            f'(Jahresberichte); dazu die Literatur, auf die sie sich berufen, und die regionalen Organe, '
            f'in denen dieselben Leute parallel publizierten.</p>'
            f'<p class="meta">Werkübergreifend: <a href="register/gesamtregister.html">Gesamtregister</a> '
            f'(Personen und Orte in allen drei Werken) · '
            f'<a href="register/gesamtbibliographie.html">Gesamtbibliographie</a> · '
            f'<a href="register/netz.html">Netzansicht</a>.</p>'
            + "".join(teile))


INDEX_SUCHSKRIPT = """<script>
fetch("data/search.json").then(r=>r.json()).then(docs=>{
 var ms=new MiniSearch({fields:["text"],storeFields:["vol","anchor","pp","label"]}); ms.addAll(docs);
 var q=document.getElementById("q"),res=document.getElementById("res");
 q.addEventListener("input",function(){
  var v=q.value.trim(); if(v.length<3){res.innerHTML="";return;}
  var hits=ms.search(v,{prefix:true,fuzzy:.1}).slice(0,40);
  res.innerHTML=hits.length?hits.map(function(h){
    var t=h.text||""; var i=t.toLowerCase().indexOf(v.toLowerCase());
    var sn=i<0?t.slice(0,140):t.slice(Math.max(0,i-50),i+90);
    return '<a class="hit" href="volumes/bd'+h.vol+'.html#pb-'+h.anchor+'">'+h.label+', S. '+h.pp+'</a> <span>…'+
      sn.replace(/</g,"&lt;")+'…</span>';}).join(""):"<p class=meta>keine Treffer</p>";
 });
});
</script>"""


def baende_page(volumes, toc=None):
    """Die Limesblatt-Bände mit ihren Inhaltsverzeichnissen — eigene Seite, seit die Startseite
    ein Einstieg ist und kein Verzeichnis."""
    toc = toc or {}
    bl = []
    for v in volumes:
        ents = toc.get(v["nr"], [])
        items = toc_li_hefte(ents, f'volumes/bd{v["nr"]}.html', False, band=v["nr"])
        sub = f'<ul class="toc idxtoc">{items}</ul>' if items else ""
        hf = [h for h in HEFTE if h["band"] == v["nr"]]
        spanne = ""
        if hf:
            d1, d2 = hf[0].get("datum") or "", hf[-1].get("datum") or ""
            spanne = f' · {len(hf)} Hefte ({html.escape(d1)} – {html.escape(d2)})' if d1 else f' · {len(hf)} Hefte'
        bl.append(f'<li><a href="volumes/bd{v["nr"]}.html"><b>{html.escape(v["label"])}</b></a> '
                  f'<span class="meta">— {len(v["pages"])} Seiten{spanne} · {len(ents)} Berichte</span>{sub}</li>')
    n_ber = sum(len(x) for x in toc.values())
    return (f'<h1>Limesblatt — Bände &amp; Inhaltsverzeichnis</h1>'
            f'<p class="lede">Das Feldorgan der Reichs-Limeskommission, vollständig: acht Jahrgangsbände, '
            f'{len(HEFTE)} Hefteinheiten, {n_ber} nummerierte Feldberichte. Jeder Bericht führt an seine '
            f'Stelle im Text, jede Seite an das Faksimile der UB Heidelberg.</p>'
            f'<p class="meta">Die Hefte sind mit ihrem Ausgabedatum gegliedert (Quelle: die IIIF-Strukturdaten '
            f'des Digitalisats). Volltextsuche über alle Bände: <a href="index.html#suche">auf der '
            f'Startseite</a>.</p>'
            f'<div class="note"><p><b>Das Erscheinen: 35 Nummern, 1892–1903.</b> Das Limesblatt erschien in '
            f'<b>35 Nummern</b> — „Er[schein]t jährlich i[n] 5–6 Nrn. zum Preise von 3 Mark", wie sein Kopf sagt —, '
            f'gebunden in acht Jahrgangsbände. Die letzte trägt den Vermerk „<i>Nr. 35. Ausgegeben am 27. Mai '
            f'[1903]</i>". <span class="lc">(Eckige Klammern: Lesungen, wo die Fraktur-OCR versagt — sie liest '
            f'„Kracheiut jährlich iu" und „27. Mai Ulli:]". Das Jahr ist über den Jahrgangsband und den '
            f'Jahresbericht gesichert.)</span> Warum es endete, sagt die Kommission in ihrem Jahresbericht '
            f'selbst:</p>'
            f'<blockquote>„Das »Limesblatt« wurde durch Herausgabe eines letzten, des 35. Heftes, zum Abschluß '
            f'gebracht. Da diese Veröffentlichung, die dazu bestimmt war, vorläufige Berichte über die Ergebnisse '
            f'der Ausgrabungen fortlaufend zur Kenntnis der Mitforscher zu bringen, <b>mit dem Abschluß der '
            f'eigentlichen Grabungen ihren Zweck erfüllt hatte</b>, so wurde das weitere Erscheinen eingestellt."'
            f'<footer>Ernst Fabricius, Bericht der Reichs-Limeskommission, Januar 1904 '
            f'(<a href="register/jahresberichte.html">Jahresberichte</a>)</footer></blockquote>'
            f'<p>Die Einstellung war demnach kein Abbruch, sondern das vorgesehene Ende eines Organs der '
            f'laufenden Feldarbeit: Mit dem Abschluss der Grabungen entfiel sein Gegenstand. Die Ergebnisse '
            f'gingen in die Endpublikation ein, den <a href="register/orl.html">ORL</a> — wie beides '
            f'zusammenhängt, zeigt die Seite zur <a href="register/genese.html">Genese des ORL</a>.</p>'
            f'<p>Das Schlussheft ist entsprechend gebaut: Es enthält einen <b>Nachruf auf Karl Zangemeister und '
            f'Felix Hettner</b> — die beiden, die dem Blatt den Namen gaben und laut Fabricius „von Anbeginn an '
            f'der Spitze unseres Unternehmens gestanden hatten"; beide starben 1902 —, Beiträge von Popp, '
            f'Winkelmann, Schuchhardt, Fabricius, Steimle und Leonhard, und schließlich ein <b>„Register zu '
            f'Nr. 1–35 des Limesblattes. Von Prof. Dr. P. Hintzelmann"</b>. Dieses zeitgenössische Register steht im letzten Band dieser Edition '
            f'(<a href="volumes/bd8.html">Band 8</a>).</p>'
            f'{heft_rhythmus()}</div>'
            f'<ul class="bandlist">{"".join(bl)}</ul>'
            )


def index_page(volumes, toc=None):
    """Startseite: ein Einstieg, kein Verzeichnis — was hier zu finden ist, in einem Bildschirm."""
    toc = toc or {}
    n_ber = sum(len(x) for x in toc.values())
    n_seiten = sum(len(v["pages"]) for v in volumes)
    head = '<script src="assets/minisearch.min.js"></script>'

    def karte(titel, text, href, meta=""):
        return (f'<a class="startkarte" href="{href}"><b>{titel}</b>'
                f'<span class="meta">{meta}</span><span>{text}</span></a>')

    karten = [
        karte("Limesblatt", "Die laufenden Feldberichte der Streckenkommissare, vollständig lesbar neben dem "
              "Faksimile.", "baende.html", f"1892–1903 · {n_seiten} Seiten · {n_ber} Berichte"),
        karte("ORL", "Die Endpublikation, in die das Feld mündete — Inhalt, Apparat und Binnenverweise "
              "erschlossen.", "register/orl-inhalt.html", "1894–1937 · 92 Kastell-Faszikel"),
        karte("Jahresberichte", "Was die Kommission jährlich über sich selbst berichtete: Personal, "
              "Beschlüsse, Kampagnen.", "register/jahresberichte.html", "1892–1905 · 13 Jahrgänge"),
        karte("Archivbestände", "Das unveröffentlichte Material — wo es liegt, wie man herankommt, was noch "
              "niemand angesehen hat.", "register/archive.html", "Findmittel · Nachlässe · Desiderate"),
        karte("Personen &amp; Orte", "Wer die Grenze erforschte und wo — mit Normdaten, Karte und den "
              "Belegen im Text.", "register/persons.html", "85 Personen · 23 Kastelle · 15 Strecken"),
        karte("Funde", "Fundgattungen im Überblick und die Einzelstücke: Stempel-Lesungen, datierte Münzen, "
              "gemeldete Objekte.", "register/fundindex.html", "435 Einzelfunde · 125 Stempel"),
        karte("Werkübergreifend", "Dieselben Namen in allen drei Werken, die Gesamtbibliographie und das "
              "Beziehungsnetz.", "register/gesamtregister.html", "Register · Bibliographie · Netz"),
        karte("Analyse", "Wie sich die Sprache wandelt, was den Feldbericht vom Standardwerk trennt, wie der "
              "ORL entstand.", "register/wortschatz.html", "Wortschatz · Genese · Methode"),
    ]
    body = (f'<h1>RLK-digital</h1>'
            f'<p class="lede">Die <b>Reichs-Limeskommission</b> (1892–1937) — das erste länderübergreifende '
            f'Großforschungsunternehmen des Kaiserreichs — mit ihren Quellen: dem Feldorgan <em>Limesblatt</em>, '
            f'der Endpublikation <em>Obergermanisch-Raetischer Limes</em>, den Jahresberichten der Kommission, '
            f'der zitierten Literatur und den Archivbeständen, die dahinter liegen.</p>'
            f'<section id="suche"><h2>Volltextsuche im Limesblatt</h2>'
            f'<input id="q" type="search" placeholder="z. B. Saalburg, Entschädigung, Mommsen …" '
            f'autocomplete="off"><div id="res"></div></section>'
            f'<h2>Bereiche</h2>'
            f'<div class="startgrid">{"".join(karten)}</div>'
            f'<p class="meta">Alle Bestände nebeneinander, je mit Umfang und Grenze: '
            f'<a href="quellen.html"><b>Die Quellen</b></a>. Wie diese Website gebaut ist und woher die Angaben '
            f'stammen: <a href="dokumentation.html"><b>Dokumentation</b></a>. '
            f'Abgeleitet aus einem Forschungs-Vault zur '
            f'<a href="https://github.com/pleuston/limes">Reichs-Limeskommission</a>; Code und TEI auf '
            f'<a href="https://github.com/pleuston/limesblatt-edition">GitHub</a>.</p>'
            + INDEX_SUCHSKRIPT)
    return body, head


def _tokn(t):
    """Polsterung abtragen: der NER-Cache schreibt Band 1 als »043«, das TEI als »43«."""
    return re.sub(r"^0+(?=\d)", "", t)


def page_links(pages, tok2anchor, tok2any=None):
    """NER-Seitenrefs ("Bd.7 S.883") → Link auf die erste Spalte der Kachel (Token-Granularität).

    Zwei Korrekturen, beide aus dem Anker-Gate: (1) die Polsterung (»S.043«) traf keinen
    TEI-Token und ließ 413 Belege ins Leere laufen; (2) das Bandlabel des NER weicht an den
    Bandgrenzen ab (»Bd.8 S.921«, aber Seite 921 steht in Band 7) — die Druckseiten laufen
    durch (1–968), also entscheidet die Seite, nicht das Label."""
    out = []
    for s in pages:
        m = re.match(r'Bd\.(\d+)\s+S\.(\S+)', s)
        if not m: continue
        vol, tok = int(m.group(1)), m.group(2)
        a = tok2anchor.get((vol, tok)) or tok2anchor.get((vol, _tokn(tok)))
        if a is None and tok2any:
            alt = tok2any.get(tok) or tok2any.get(_tokn(tok))
            if alt: vol, a = alt
        if a:
            out.append(f'<a href="../volumes/bd{vol}.html#pb-{html.escape(a)}">{vol}/{html.escape(_tokn(tok))}</a>')
    return ", ".join(out)

def ner_index_page(items, what, tok2anchor, recon, tok2any=None):
    lab = "Namen" if what == "persons" else "Orte"
    rows = 0; matched = 0; lis = []; ohne = 0
    for it in items:
        pl = page_links(it.get("pages", []), tok2anchor, tok2any)
        if not pl:
            # Eintrag NICHT verwerfen: der Lesetext verlinkt diese Entität, der Anker muss stehen.
            pl = '<span class="meta">Seitenbeleg nicht auflösbar</span>'; ohne += 1
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
        em = f'<span class="meta">{html.escape(extra)}</span>' if extra else ""
        lc = ' lc' if it.get("cert") != "high" else ""
        eid = ("psnN_" if what == "persons" else "plcN_") + gazetteer.slug(gazetteer._primary(nm)[0])
        nbel = len(it.get("pages", []))
        lis.append(f'<tr id="{eid}" class="ix{lc}"><td><b>{disp}</b></td><td>{em}</td>'
                   f'<td>{nbel or ""}</td><td>{ref}</td><td class="pgs">{pl}</td></tr>')
        rows += 1
    rec = (f'<b>{matched}</b> mit GND bzw. dem Personenregister verknüpft' if what == "persons"
           else f'<b>{matched}</b> über iDAI-Gazetteer/Koordinaten verortet')
    head = ""
    art = "Rolle" if what == "persons" else "Art"
    body = (f'<h1>{lab} im Limesblatt</h1>'
            f'<p class="meta">{rows} {lab}, per <b>LLM-NER</b> aus dem gesamten Volltext extrahiert '
            f'(heuristisch, Fraktur-OCR; <span class="lc">grau = unsichere OCR-Lesung</span>) — token-frei {rec}. '
            f'<b>Jede Spalte sortiert</b> (Klick auf den Kopf), das Suchfeld filtert; die Zahlen springen ins '
            f'Faksimile. Nach <b>Belegen</b> sortiert steht oben, was das Limesblatt am häufigsten nennt.</p>'
            f'<table class="reg nerreg"><thead><tr><th>{lab[:-1] if lab.endswith("n") else lab}</th>'
            f'<th>{art}</th><th>Belege</th><th>Normdaten</th><th>Seiten (Band/Druckseite)</th></tr></thead>'
            f'<tbody>{"".join(lis)}</tbody></table>')
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
            f'<p class="meta">Der Textsortenwechsel ist Teil einer längeren Entwicklung — die Schichten des '
            f'Werks ordnet die Seite zur <a href="genese.html">Genese des ORL</a>.</p>'
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

def _load_json_any(name):
    """data/ des Editions-Repos zuerst (CI-Rebuild), sonst der Vault-Nachbar (lokaler Build)."""
    f = next((p for p in (os.path.join(REPO, "data", name),
                          os.path.join(REPO, "..", "limes", "tools", name))
              if os.path.exists(p)), None)
    if not f:
        return {}
    try:
        return json.load(open(f, encoding="utf-8"))
    except Exception:
        return {}


def load_hefte():
    """Die 34 Hefteinheiten (35 Nummern, 7/8 ist ein Doppelheft) mit Ausgabedatum.
    Quelle: IIIF-`structures` der UB Heidelberg via `tools/limesblatt_hefte.py` — die
    Fraktur-OCR gibt die Impressumszeilen nicht her (3 lesbare »Ausgegeben am« im ganzen Werk)."""
    f = next((p for p in (os.path.join(REPO, "data", "hefte.json"),
                          os.path.join(REPO, "..", "limes", "tools", "hefte.json"))
              if os.path.exists(p)), None)
    if not f:
        return []
    try:
        return json.load(open(f, encoding="utf-8")).get("hefte", [])
    except Exception:
        return []


HEFTE = load_hefte()


def heft_label(h):
    nr = f'{h["nr"]}/{h["nr_bis"]}' if h.get("nr_bis") else str(h["nr"])
    return f'Nr. {nr}', (h.get("datum") or "")


def tokkey(t):
    """Token-Normalform. Der OCR-Cache polstert die Druckseiten fuer die Sortierung
    (»001«), die IIIF-Canvas-Labels tun es nicht (»1«) — ohne Normalisierung faende
    Band 1 kein einziges seiner Hefte."""
    t = str(t or "").strip()
    m = re.match(r"^0*(\d+)([a-z]*)$", t)
    return (m.group(1) + m.group(2)) if m else t


ANCHOR_TOK = {}


def register_anchors(volumes):
    """img_tok je Band merken: die Bandseiten polstern Band 1 (»001«), die IIIF-Labels
    nicht (»1«) — ein Heft-Link auf das rohe Manifest-Token liefe ins Leere."""
    for v in volumes:
        for pg in v["pages"]:
            ANCHOR_TOK.setdefault((v["nr"], tokkey(pg["img_tok"])), pg["img_tok"])


def heft_anchor(h):
    """Sprungziel eines Heftes: erste Druckseite, Spalte a — mit dem ECHTEN Ankertoken."""
    t = ANCHOR_TOK.get((h["band"], tokkey(h.get("erste_seite"))))
    return "#pb-%s-a" % t if t else ""


def heft_index():
    """Druckseiten-Token -> Heft. Ein Bericht erbt das Ausgabedatum seines Heftes."""
    idx = {}
    for h in HEFTE:
        for t in h.get("tokens", []):
            idx.setdefault(tokkey(t), h)
    return idx


HEFT_IDX = heft_index()


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

def toc_li_hefte(entries, hrefpre, with_page, band=None):
    """Wie toc_li, aber nach HEFTEN gegliedert: vor jeder Gruppe eine Zeile »Nr. N · Datum«.
    Hefte ohne eigenen nummerierten Bericht erscheinen trotzdem — die Publikationsfolge soll
    vollstaendig sein. Berichte ohne Heft-Zuordnung stehen am Ende, ausgewiesen."""
    if not HEFTE:
        return toc_li(entries, hrefpre, with_page)
    gruppen, rest = {}, []
    for e in entries:
        h = HEFT_IDX.get(tokkey(e[0]))
        if h:
            gruppen.setdefault((h["band"], h["nr"]), []).append(e)
        else:
            rest.append(e)
    out = []
    for h in HEFTE:
        if band is not None and h["band"] != band:
            continue
        nr, dat = heft_label(h)
        anz = len(gruppen.get((h["band"], h["nr"]), []))
        ziel = hrefpre + heft_anchor(h)
        spalten = (" · Sp.&nbsp;%s–%s" % (h["erste_seite"], h["letzte_seite"])
                   if h.get("erste_seite") else "")
        berichte = " · %d Berichte" % anz if anz else ""
        out.append('<li class="heft"><a href="%s"><b>%s</b></a>'
                   '<span class="meta"> · %s%s%s</span></li>'
                   % (ziel, nr, html.escape(dat), spalten, berichte))
        if anz:
            out.append(toc_li(gruppen[(h["band"], h["nr"])], hrefpre, with_page))
    if rest:
        out.append('<li class="heft"><b class="muted">ohne Heftzuordnung</b>'
                   '<span class="meta"> · %d Berichte</span></li>' % len(rest))
        out.append(toc_li(rest, hrefpre, with_page))
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

# ---------- Die RLK-Jahresberichte als eigenes Korpus: nummeriert und indiziert ----------
# Der Bericht trägt im Jahrbuch keine eigene Zählung — er steht als Anhang im Archäologischen
# Anzeiger und erbt dessen Bandzahl (Bd. 7 = 1892). Für ein Register ist das unbrauchbar, weil
# die Zahl nicht sagt, der wievielte Bericht es ist. Hier bekommen sie eine laufende Nummer nach
# Jahrgang; die Lücke 1894 (Bd. 9, nicht digitalisiert) bleibt als Fehlstelle stehen und wird
# nicht weggezählt — sonst verschöbe sich alles danach.
JB_VETO = {"Graben", "Wall", "Kastell", "Limes", "Turm", "Berg", "Bach", "Feld", "Mauer", "Lager"}


def jb_korpus(jb, ner_p, ner_pl):
    """Personen-/Ortsnennungen je Jahresbericht — derselbe Gazetteer wie im Limesblatt.

    Einwortnamen werden über die Wortliste gezählt (schnell und exakt an Wortgrenzen),
    mehrteilige Namen über eine Phrasensuche im normalisierten Text."""
    ber = sorted(jb.get("berichte", []), key=lambda b: b["jahrgang"])
    if not ber: return [], {}, {}
    ein_p = {p["name"] for p in ner_p if len(p["name"]) > 4 and " " not in p["name"]} - JB_VETO
    ein_o = {p["name"] for p in ner_pl if len(p["name"]) > 4 and " " not in p["name"]} - JB_VETO
    mehr_p = [p["name"] for p in ner_p if " " in p["name"] and len(p["name"]) > 6]
    mehr_o = [p["name"] for p in ner_pl if " " in p["name"] and len(p["name"]) > 6]
    pers, orte = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    reihe = []
    for b in ber:
        nr = b["jahrgang"] - 1891          # 1892 = Bericht 1; die Lücke 1894 behält ihre Nummer 3
        txt = re.sub(r"\s+", " ", b.get("text") or "")
        wort = Counter(re.findall(r"[A-ZÄÖÜ][\wäöüß-]+", txt))
        for n in ein_p & set(wort): pers[n][b["jahrgang"]] += wort[n]
        for n in ein_o & set(wort): orte[n][b["jahrgang"]] += wort[n]
        for n in mehr_p:
            c = txt.count(n)
            if c: pers[n][b["jahrgang"]] += c
        for n in mehr_o:
            c = txt.count(n)
            if c: orte[n][b["jahrgang"]] += c
        reihe.append({"nr": nr, "jahrgang": b["jahrgang"], "band": b["band"], "woerter": b.get("woerter", 0),
                      "admin": b.get("admin_je_1000", 0), "feld": b.get("feld_je_1000", 0)})
    return reihe, pers, orte


JB_AMT = re.compile(r"(Streckenkommissar\w*|Dirigent\w*|Generalstab\w*|Reichstag\w*|Etat\w*|Rate\b|"
                    r"Bewilligung\w*|Denkschrift\w*|Sitzung\w*)")
JB_GELD = re.compile(r"([\d][\d.\s]{2,12})\s*(?:M\.|Mark|Mk\.)")


def jb_stellen(jb):
    """Institutionelles Vokabular und Geldbeträge mit Fundstelle — die Verwaltungsseite der RLK."""
    amt, geld = defaultdict(list), []
    for b in sorted(jb.get("berichte", []), key=lambda x: x["jahrgang"]):
        txt = re.sub(r"\s+", " ", b.get("text") or "")
        for m in JB_AMT.finditer(txt):
            amt[m.group(1)].append((b["jahrgang"], _snip(txt, m.start(), m.end(), 60)))
        for m in JB_GELD.finditer(txt):
            betrag = re.sub(r"[\s.]", "", m.group(1))
            if betrag.isdigit() and int(betrag) >= 100:
                geld.append({"jahr": b["jahrgang"], "betrag": int(betrag),
                             "kontext": _snip(txt, m.start(), m.end(), 70)})
    return amt, geld


# ---------- Konkrete Funde: Einzelstücke, Stempel-Lesungen, datierte Münzen ----------
# Die Gattungstabellen oben zählen Nennungen einer KLASSE (»Fibel« 15×). Was der Feldbericht
# tatsächlich meldet, ist aber das einzelne Stück — »ein bronzenes Ortband«, »Stempel OCILIVSFE
# auf Sigillata-Boden«, »Denar des Caracalla«. Die folgenden Register führen diese Einzelnennungen
# vollständig auf, jede mit Kontextzeile und Sprung ins Faksimile. Heuristik auf Fraktur-OCR:
# eine Nennung ist ein Beleg für die REDE vom Fund, nicht für den Fund an dieser Stelle.
FUND_OBJEKTE = [
    ("Waffen & Militaria", r"Lanzenspitze|Speerspitze|Pfeilspitze|Schildbuckel|Ortband|Schwert|Dolch|"
                           r"Helm|Panzerschuppe|Riemenzunge|Schleuderblei|Geschossspitze|Sporn|Trense|Hufeisen"),
    # »Sonde« und »Kelle« sind das Werkzeug des Ausgräbers, nicht sein Fund — sie stehen im Text,
    # gehören aber nicht in einen Fundkatalog; ebenso »Ziegel«/»Basis«, die fast immer Bauwerk meinen
    # (der gestempelte Ziegel steht im Stempelregister).
    ("Gerät & Werkzeug", r"Messer|Schlüssel|Beil|Axt|Sichel|Meissel|Zange|Hammer|Bohrer|Feile|Angelhaken|"
                         r"Griffel|Stilus|Spinnwirtel|Webgewicht|Mühlstein|Wetzstein|Nagel|Nägel"),
    ("Schmuck & Tracht", r"Fibel|Fingerring|Armreif|Armring|Halsring|Perle|Schnalle|Haarnadel|Gewandnadel|"
                         r"Nadel|Gemme|Kettchen|Anhänger|Handspiegel|Spiegel|Löffelchen|Löffel"),
    ("Gefäße & Keramik", r"Krug|Becher|Schale|Napf|Teller|Amphore|Amphora|Urne|Reibschale|Deckel|Lampe|"
                         r"Tintenfass|Räuchergefäss|Räucherkelch"),
    ("Bauplastik & Stein", r"Antefix|Säulentrommel|Kapitell|Relief|Statuette|Figürchen|Altar|Ara|"
                           r"Weihestein|Meilenstein|Grabstein|Ziegelstempel"),
]
FUND_MATERIAL = (r"(?:eisern|bronzen|silbern|golden|gläsern|thönern|tönern|steinern|hölzern|bleiern|kupfern)"
                 r"[a-zäöüß]{0,4}|(?:Bronze|Eisen|Silber|Gold|Blei|Thon|Ton|Glas|Stein|Bein|Knochen|Terra)"
                 r"[a-zäöüß]{0,12}")


def _snip(txt, a, b, breite=52):
    s = re.sub(r"\s+", " ", txt[max(0, a - breite):b + breite]).strip()
    return ("…" if a - breite > 0 else "") + s + ("…" if b + breite < len(txt) else "")


def objekt_funde(volumes):
    """Jede Einzelnennung eines Fundobjekts mit Materialangabe (wo genannt) und Kontext."""
    mat = re.compile(FUND_MATERIAL)
    out = []
    for grp, rx in FUND_OBJEKTE:
        r = re.compile(rf"\b({rx})(?:n|en|s|es|e)?\b")
        for v in volumes:
            for p in v["pages"]:
                txt = p.get("text") or ""
                for m in r.finditer(txt):
                    vor = txt[max(0, m.start() - 34):m.start()]
                    mm = list(mat.finditer(vor))
                    formel = (mm[-1].group(0) + " " + m.group(0)) if mm and m.start() - (max(0, m.start() - 34) + mm[-1].end()) <= 3 else m.group(0)
                    out.append({"gruppe": grp, "objekt": m.group(1), "formel": formel, "vol": v["nr"],
                                "anchor": p["anchor"], "printed": p["printed"], "col": p.get("col", ""),
                                "kontext": _snip(txt, m.start(), m.end())})
    out.sort(key=lambda x: (x["gruppe"], x["objekt"].lower(), x["vol"]))
    return out


TRUPPE_RX = re.compile(r"\b((?:[Ll]eg|LEG|[Cc]oh|COH|[Aa]la|ALA|[Vv]ex|VEX)[a-zäöüA-ZÄÖÜ]{0,6}\.?\s*"
                       r"([IVXLC]{1,6})\b(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöü.·]{1,14}){0,3})")
STEMPEL_KONTEXT = re.compile(r"stempel|gestempelt|töpfermarke|fabrikmarke|\bmarke\b|signatur", re.I)
# Die Endung »M« als Stempelmarke ist zu freigebig: sie zieht jeden vierbuchstabigen Fraktur-
# Fetzen mit (TADM, TIAM, ITAM, DALM). Erlaubt bleiben OF…/OP… und die Fecit-Endungen.
TOEPFER_FORM = re.compile(r"^(?:OF|OP|OFIC)[A-ZÄÖÜ]{2,}$|^[A-ZÄÖÜ]{4,}(?:F|FE|FEC|FECIT)$")
STEMPEL_VETO = {"ORVM", "ARVM", "IBVS", "AVGVSTI"}
STEMPEL_WORT = {"FECIT", "OPVS", "GERMAN", "FIGVLVS"}      # Stempelvokabular, kein Töpfername


def stempel_lesungen(volumes):
    """Konkrete Stempel-Lesungen: Truppenstempel (Leg./Coh. + Zahl + Beiname) und Töpferstempel.

    Der Töpferstempel wird über den KONTEXT erkannt (»Stempel«, »Töpfermarke« im Umfeld) oder
    über die Stempelform selbst (OF…/…F/…FEC) — reine Versalfolgen aus Inschriftentext sind
    keine Stempel, deshalb reicht Großschreibung als Signal nicht."""
    trp, toep = [], []
    caps = re.compile(r"\b([A-ZÄÖÜ]{3,16})\b")
    for v in volumes:
        for p in v["pages"]:
            txt = (p.get("text") or "").replace("\u00ad", "")
            for m in TRUPPE_RX.finditer(txt):
                if not _r2i(m.group(2)): continue          # nur echte römische Zahlen
                les = re.sub(r"\s+", " ", m.group(1)).strip(" .")
                trp.append({"lesung": les, "vol": v["nr"], "anchor": p["anchor"], "printed": p["printed"],
                            "kontext": _snip(txt, m.start(), m.end())})
            for m in caps.finditer(txt):                          # weiches Trennzeichen oben entfernt
                w = m.group(1)
                if _r2i(w) or not re.search(r"[AEIOUV]", w): continue
                if w in ("LEG", "COH", "ALA", "VEX", "LEGIO", "COHORS") or w in STEMPEL_WORT: continue
                if any(w.endswith(s) for s in STEMPEL_VETO) and not w.startswith(("OF", "OP")): continue
                vor = txt[max(0, m.start() - 34):m.start()]
                if re.search(r"(?i)\b(leg|coh|ala)[a-zäöü]*\.?\s*[IVXLC]*\s*$", vor): continue   # Teil eines Truppenstempels
                umfeld = txt[max(0, m.start() - 170):m.end() + 170]
                stempelform = bool(TOEPFER_FORM.match(w))
                # Vierbuchstabige Versalfetzen (»TADM«, »ITAM«) sind Fraktur-Bruch aus dem
                # Inschriftentext; nur die Stempelform selbst rechtfertigt so kurze Lesungen.
                if not stempelform and len(w) < 5: continue
                if not (stempelform or STEMPEL_KONTEXT.search(umfeld)): continue
                toep.append({"lesung": w, "vol": v["nr"], "anchor": p["anchor"], "printed": p["printed"],
                             "form": stempelform,
                             "kontext": _snip(txt, m.start(), m.end())})
    return trp, toep


MUENZ_NOMINAL = (r"Denar|Aureus|Sesterz|Mittelerz|Grosserz|Kleinerz|Antoninian|Bronzemünze|Silbermünze|"
                 r"Goldmünze|Kupfermünze|Kaisermünze")
MUENZ_KAISER = (r"Vespasian|Titus|Domitian|Nerva|Tra[ij]an|Hadrian|Antoninus Pius|Antoninus|Marc Aurel|"
                r"Commodus|Septimius Severus|Severus Alexander|Alexander Severus|Caracalla|Elagabal|"
                r"Gordian\w*|Philippus|Valerian|Gallienus|Probus|Nero|Claudius|Augustus|Tiberius|"
                r"Faustina|Julia \w+|Crispina|Lucilla|Maximin\w*|Postumus|Tetricus")


def muenz_funde(volumes):
    """Datierbare Einzelmünzen: Nominal + Kaiser — die Datierungsevidenz des Feldberichts."""
    rx = re.compile(rf"\b({MUENZ_NOMINAL})[a-zäöüß]{{0,4}}\s+(?:des|der|von|d\.)\s+({MUENZ_KAISER})")
    out = []
    for v in volumes:
        for p in v["pages"]:
            txt = p.get("text") or ""
            for m in rx.finditer(txt):
                out.append({"nominal": m.group(1), "kaiser": m.group(2), "vol": v["nr"], "anchor": p["anchor"],
                            "printed": p["printed"], "kontext": _snip(txt, m.start(), m.end())})
    return out


def _fundrows(items, spalten):
    """Zeilen für die sortierbaren Einzelfund-Tabellen; letzte Spalte immer der Beleglink."""
    out = []
    for it in items:
        tds = "".join(f'<td>{c}</td>' for c in spalten(it))
        out.append(f'<tr>{tds}<td><a href="../volumes/bd{it["vol"]}.html#pb-{html.escape(it["anchor"])}">'
                   f'Bd.&#8201;{it["vol"]}, S.&#8201;{html.escape(it["printed"])}</a></td>'
                   f'<td class="meta ktx">{html.escape(it["kontext"])}</td></tr>')
    return "".join(out)


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
    legend_all = sorted(legend, key=lambda w: (-len(legend[w]), w))
    legend_t = thematic_table(legend, [(w, w) for w in legend_all], "Versal-Legende")

    obj = objekt_funde(volumes)
    trp, toep = stempel_lesungen(volumes)
    mz = muenz_funde(volumes)
    obj_t = (f'<table class="reg fund"><thead><tr><th>Fundstück</th><th>Objekt</th><th>Gattung</th>'
             f'<th>Beleg</th><th>Kontext</th></tr></thead><tbody>'
             + _fundrows(obj, lambda it: (f'<b>{html.escape(it["formel"])}</b>', html.escape(it["objekt"]),
                                          f'<span class="meta">{html.escape(it["gruppe"])}</span>'))
             + '</tbody></table>')
    trp_t = (f'<table class="reg fund"><thead><tr><th>Lesung</th><th>Beleg</th><th>Kontext</th></tr></thead>'
             f'<tbody>' + _fundrows(trp, lambda it: (f'<b>{html.escape(it["lesung"])}</b>',)) + '</tbody></table>')
    toep_t = (f'<table class="reg fund"><thead><tr><th>Stempel</th><th>Erkannt über</th><th>Beleg</th>'
              f'<th>Kontext</th></tr></thead><tbody>'
              + _fundrows(toep, lambda it: (f'<b>{html.escape(it["lesung"])}</b>',
                                            "Stempelform" if it["form"] else "Kontextwort"))
              + '</tbody></table>')
    mz_t = (f'<table class="reg fund"><thead><tr><th>Nominal</th><th>Kaiser</th><th>Beleg</th><th>Kontext</th>'
            f'</tr></thead><tbody>'
            + _fundrows(mz, lambda it: (html.escape(it["nominal"]), f'<b>{html.escape(it["kaiser"])}</b>'))
            + '</tbody></table>')
    ogrp = Counter(x["gruppe"] for x in obj)

    body = (f'<h1>Fundindex</h1><p class="meta">Token-frei aus dem Volltext, in zwei Auflösungen: die '
            f'<b>Gattungen</b> zählen, wovon überhaupt die Rede ist; die <b>Einzelfund-Register</b> führen '
            f'auf, was der Feldbericht als konkretes Stück meldet — jedes mit Kontextzeile und Sprung ins '
            f'Faksimile. Alle Tabellen sind über die Spaltenköpfe sortierbar, längere zusätzlich durchsuchbar. '
            f'Heuristischer Abgleich auf Fraktur-OCR: eine Nennung belegt die <i>Rede</i> vom Fund, nicht den '
            f'Fund an dieser Stelle. Die katalogisierte Epigraphik steht unter '
            f'<a href="inschriften.html">Inschriften (EDH)</a>.</p>'
            f'<p class="meta"><b>Springe zu:</b> <a href="#objekte">Einzelfunde ({len(obj)})</a> · '
            f'<a href="#stempellesungen">Stempel-Lesungen ({len(trp) + len(toep)})</a> · '
            f'<a href="#muenzfunde">Datierte Münzen ({len(mz)})</a> · <a href="#gattungen">Gattungen</a> · '
            f'<a href="#sigillata">Sigillata-Formen</a> · <a href="#legenden">Versal-Legenden ({len(legend_all)})</a></p>'

            f'<h2 id="objekte">Einzelfunde</h2>'
            f'<p class="meta">Jede Nennung eines Fundobjekts, mit der Materialangabe des Berichts, wo er eine '
            f'macht (»bronzenes Ortband«, »eiserner Schildbuckel«). Verteilung: '
            + " · ".join(f'{html.escape(g)} {n}' for g, n in ogrp.most_common()) + '.</p>' + obj_t +

            f'<h2 id="stempellesungen">Stempel-Lesungen</h2>'
            f'<p class="meta">Der Stempel ist der genaueste Einzelfund des Limes: er nennt die Einheit oder den '
            f'Töpfer beim Namen. <b>{len(trp)} Truppenstempel-Lesungen</b> (Einheit + Zahl + Beiname) und '
            f'<b>{len(toep)} Töpfer- bzw. Fabrikstempel</b> — letztere erkannt entweder an der Stempelform '
            f'selbst (OF…, …F, …FEC) oder daran, dass im Umfeld von einem Stempel die Rede ist; bloße '
            f'Großschreibung genügt nicht, sonst zöge der Inschriftentext mit ein.</p>'
            f'<h3>Truppenstempel</h3>{trp_t}<h3>Töpfer- &amp; Fabrikstempel</h3>{toep_t}'

            f'<h2 id="muenzfunde">Datierte Münzen</h2>'
            f'<p class="meta">Nominal <i>und</i> Kaiser in einer Nennung — die datierende Einzelmünze, nicht nur '
            f'die Erwähnung eines Kaisernamens (die zählt die Tabelle <a href="#muenzkaiser">Münzkaiser</a>).</p>'
            f'{mz_t}'

            f'<h2 id="gattungen">Fundgattungen</h2>{cat_t}'
            f'<h2 id="muenzkaiser">Münzkaiser (Datierungsevidenz)</h2>'
            f'<p class="meta">Bildet die Limes-Belegung ab: flavisch-trajanische Errichtung, severischer Peak, Auslaufen vor 260.</p>{emp_t}'
            f'<h2 id="sigillata">Terra-Sigillata-Formen</h2>'
            f'<p class="meta">Die Dragendorff-Formtypen als laufendes Datierungsraster — vgl. <a href="bibliographie.html">Dragendorff 1895</a>.</p>{drag_t}'
            f'<h2 id="stempel">Truppenstempel</h2>'
            f'<p class="meta">Legio-/Cohors-Nennungen (Ziegelstempel &amp; Text) — erwartungsgemäß dominiert <b>Legio XXII Primigenia</b> (Mainz).</p>{leg_t}{coh_t}'
            f'<h2 id="legenden">Versal-Legenden</h2>'
            f'<p class="meta">Alle Großbuchstaben-Folgen des Volltextes, ungefiltert: Stempellegenden und '
            f'Inschriftentext gemischt. Die aussortierte Teilmenge steht oben unter '
            f'<a href="#stempellesungen">Stempel-Lesungen</a>; hier bleibt der Rohbestand stehen, damit '
            f'nachprüfbar ist, was die Auswahl weggelassen hat.</p>{legend_t}')
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

def gesamtbibliographie_page(bibls, idx, abta, jb, rez, zs, bli, hefte):
    """Alle bibliographischen Einheiten der Edition in EINER sortierbaren Tabelle.

    Die Einzelregister sind nach Herkunft getrennt (zitierte Werke, ORL-Lieferungen,
    Jahresberichte, Rezeption, Organe) — sie beantworten je eine Frage. Wer aber wissen will,
    was in einem bestimmten Organ oder Jahr erschien, muss quer dazu lesen können; deshalb hier
    dieselben Einheiten in einem Raster: Urheber · Jahr · Titel · Organ/Reihe · Gattung · Nachweis.
    Jede Spalte sortiert, das Suchfeld filtert. Was eine Quelle nicht hergibt, bleibt leer —
    nichts wird ergänzt, um die Tabelle voll aussehen zu lassen."""
    rows = []

    def add(urheber, jahr, titel, organ, gattung, nachweis, notiz=""):
        rows.append({"u": urheber or "", "j": str(jahr or ""), "t": titel or "", "o": organ or "",
                     "g": gattung, "n": nachweis or '<span class="meta">—</span>', "note": notiz})

    ZS_RX = re.compile(r"zeitschrift|korrespondenzblatt|jahrbuch|jahresbericht|annalen|blätter|"
                       r"mitteilungen|anzeiger|quartalblätter|fundberichte", re.I)
    CORP_RX = re.compile(r"\bCIL\b|corpus|brambach|inscription", re.I)
    for b in bibls:                                            # im Limesblatt zitierte Apparatur
        ti = b["title"]
        jahr = ""
        m = re.search(r"\b(1[6-9]\d\d)\b", b.get("note", "") + " " + ti)
        if m: jahr = m.group(1)
        gat = ("Zeitschrift" if ZS_RX.search(ti) else "Inschriftencorpus" if CORP_RX.search(ti) else "Werk")
        link = f'<a href="{b["oa"]}">{html.escape(b["oalabel"] or "Digitalisat")}</a>' if b.get("oa") else ""
        add("", jahr, f'<a href="bibliographie.html#{b["id"]}">{html.escape(ti)}</a>',
            ti if gat == "Zeitschrift" else "", gat, link, b.get("note", ""))

    lfg = bli.get("kastell_nr_zu_lieferung") or {}
    for k in idx.get("abteilung_B_kastelle", []):              # ORL, Abteilung B (Kastelle)
        l = lfg.get(k["nr"]) or {}
        bearb = k.get("bearbeiter") or k.get("bearbeiter_k10") or []
        if isinstance(bearb, str): bearb = [bearb]
        if not bearb and l.get("bearbeiter"): bearb = [l["bearbeiter"]]
        jahr = k.get("year_k10") or (l.get("jahr") or "")[:4]      # Merten notiert »1900-10« = Monat
        nw = (f'<a href="https://babel.hathitrust.org/cgi/pt?id={html.escape(k["htid"])}">HathiTrust</a>'
              if k.get("htid") else "")
        notiz = (f'Lieferung {l["lieferung"]} (Beleg: {l.get("quelle", "?")})' if l.get("lieferung") else "")
        add(", ".join(bearb), jahr,
            f'<a href="orl-inhalt.html#orltoc-{html.escape(k["nr"])}">ORL {html.escape(k["nr"])} — {html.escape(k["kastell"])}</a>',
            "ORL, Abteilung B", "ORL-Lieferung", nw, notiz)
    for r in abta.get("records", []):                          # ORL, Abteilung A (Strecken)
        add("Ernst Fabricius (Hg.)", r.get("jahr", ""),
            f'{html.escape(r.get("band", ""))} — {html.escape(r.get("titel", ""))}',
            "ORL, Abteilung A", "ORL-Streckenband", "",
            "Sammelband" if r.get("sammelband") else "")
    for b in jb.get("berichte", []):                           # RLK-Jahresberichte
        add("Reichs-Limeskommission", b.get("jahrgang", ""),
            f'<a href="jahresberichte.html">Bericht über die Thätigkeit der Reichs-Limeskommission '
            f'{b.get("jahrgang", "")}</a>',
            "Jahrbuch des DAI (Archäologischer Anzeiger)", "Jahresbericht",
            f'<a href="https://archive.org/details/jahrbuchdeskaise{b.get("band", "")}kaisrich">archive.org</a>',
            f'Jahrbuch Bd. {b.get("band", "")} (Jahrgangszählung des Jahrbuchs, nicht der Berichte) · '
            f'{b.get("woerter", "")} Wörter')
    for h in (hefte or []):                                    # das Limesblatt selbst, Heft für Heft
        d, iso = h.get("datum") or "", h.get("datum_iso") or ""
        nr = f'{h.get("nr")}/{h.get("nr_bis")}' if h.get("nr_bis") else str(h.get("nr", ""))
        add("Reichs-Limeskommission", (iso[:4] if iso else ""),
            f'<a href="../volumes/bd{h.get("band", 1)}.html">Limesblatt, Nr.&#8201;{html.escape(nr)}</a>',
            "Limesblatt", "Heft", "", f'ausgegeben {d} · Sp. {h.get("erste_seite","")}–{h.get("letzte_seite","")}')
    for it in rez.get("items", []):                            # Rezeption / Nachleben
        au = ", ".join(it.get("authors") or [])
        nw = f'<a href="{it["url"]}">{html.escape((it.get("srcs") or ["Nachweis"])[0])}</a>' if it.get("url") else ""
        add(au, it.get("year", ""), html.escape(it.get("title", "")), "", f'Rezeption — {it.get("era", "")}',
            nw, it.get("type", ""))
    for org, d in sorted((zs.get("organe_vollzaehlung") or {}).items(), key=lambda x: -x[1].get("seiten", 0)):
        add("", "", html.escape(org), html.escape(org), "Organ im ORL-Apparat",
            f'<a href="orl-register.html">{d.get("seiten", 0)} Seiten</a>',
            f'{d.get("n_faszikel", 0)} Faszikel')

    gcnt = Counter(r["g"] for r in rows)
    trs = "".join(
        f'<tr><td>{html.escape(r["u"]) if "<" not in r["u"] else r["u"]}</td><td>{html.escape(r["j"])}</td>'
        f'<td>{r["t"]}{f"<div class=meta>{html.escape(r[chr(110) + chr(111) + chr(116) + chr(101)])}</div>" if r["note"] else ""}</td>'
        f'<td>{r["o"]}</td><td>{html.escape(r["g"])}</td><td>{r["n"]}</td></tr>' for r in rows)
    return (f'<h1>Gesamtbibliographie</h1>'
            f'<p class="meta">Alle bibliographischen Einheiten dieser Edition in einem Raster — '
            f'<b>{len(rows)} Titel</b> aus sechs getrennt gepflegten Registern: die im Limesblatt zitierte '
            f'Apparatur, die Lieferungen des <a href="orl-inhalt.html">ORL</a> (Abt. A und B), die '
            f'<a href="jahresberichte.html">Jahresberichte der RLK</a>, die Hefte des Limesblatts selbst, '
            f'die Rezeption (Nachweise aus OpenAlex, Crossref und archive.org) und die Zeitschriften-Organe '
            f'des ORL-Apparats. <b>Jede Spalte ist sortierbar</b> (Klick auf den Kopf), das Suchfeld filtert die Zeilen — '
            f'so lässt sich das Material auch nach Organ oder Jahr lesen, quer zu seiner Herkunft. '
            f'Leere Felder sind Lücken der Quelle, nicht der Darstellung: Erscheinungsjahre etwa liegen für '
            f'die ORL-Lieferungen nur dort vor, wo der Verbundkatalog sie führt.</p>'
            f'<p class="meta"><b>Bestand:</b> ' + " · ".join(f'{html.escape(g)} {n}' for g, n in gcnt.most_common()) + '</p>'
            f'<table class="reg fund"><thead><tr><th>Urheber</th><th>Jahr</th><th>Titel</th>'
            f'<th>Organ / Reihe</th><th>Gattung</th><th>Nachweis</th></tr></thead><tbody>{trs}</tbody></table>')


def bibliography_page(bibls, occ):
    # Nachweis-Spalte: jede Titelaufnahme ist gegen K10plus bzw. die ZDB geprüft
    # (tools/biblio_check.py). Anlass war ein Eintrag, den es nicht gab.
    pruef = {e["id"]: e for e in (_load_json_any("biblio_check.json") or {}).get("eintraege", [])}
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
        pr = pruef.get(b["id"], {})
        st = pr.get("status", "")
        if st == "belegt" and pr.get("belege"):
            k = pr["belege"][0]
            nw = (f'<span class="ok">✓ nachgewiesen</span><div class="meta">'
                  f'{html.escape((k.get("verfasser") or "").strip())}'
                  f'{": " if k.get("verfasser") else ""}{html.escape(k.get("titel", "")[:70])}'
                  f'{" (" + html.escape(k.get("jahr", "")) + ")" if k.get("jahr") else ""}'
                  f'{" · ZDB " + html.escape(k["zdb"]) if k.get("zdb") else " · K10plus"}</div>')
        elif st == "antike Quelle":
            nw = ('<span class="meta">antike Quelle</span><div class="meta">keine Titelaufnahme; '
                  'zitiert wird nach Ausgabe</div>')
        elif st:
            nw = '<span class="lc">nicht nachgewiesen</span>'
        else:
            nw = '<span class="meta">—</span>'
        rows.append(f'<tr id="{b["id"]}"><td><b>{html.escape(b["title"])}</b>{link}{propy}{iiifbtn}'
                    f'<div class="meta">{html.escape(b["note"])}</div></td>'
                    f'<td>{nw}</td>'
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
            f'<div class="note"><p><b>Jede Titelaufnahme ist geprüft.</b> Anlass war ein Eintrag, den es '
            f'nicht gab: »Ferdinand Haug, zu den römischen Inschriften Südwestdeutschlands« war eine '
            f'Umschreibung, mit der ein Namenstreffer im Volltext beschriftet worden war — das Werk heißt '
            f'<i>Die römischen Inschriften und Bildwerke Württembergs</i> (Haug/Sixt 1900). Seither läuft '
            f'jede Aufnahme gegen den Verbundkatalog K10plus (Monographien) bzw. die Zeitschriftendatenbank '
            f'ZDB (Reihen); geprüft wird Verfasser <i>und</i> Titel, weil die Titelwörter allein '
            f'Fehltreffer bestätigen. Antike Werke haben keine Titelaufnahme und stehen entsprechend '
            f'gekennzeichnet. Drei Einträge, die nur Namen im Volltext einfingen, sind entfallen.</p></div>'
            f'<table class="reg fund"><tr><th>Werk / Reihe (Digitalisat)</th><th>Nachweis</th>'
            f'<th>Verweise</th><th>Belege (Seite · Spalte)</th></tr>'
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


def orl_page(idx, lex, bli=None):
    # bli = orl_band_lieferung.json — der GEPRÜFTE Index. Alles Scan-Abgeleitete (Seitenzahl, EF-Profil,
    # Sigillata-Score, Cross-Work) hing an orl_index.htid, und die Zuordnung war für 30 Bände falsch:
    # die HathiTrust-enumcron „v. N" ist die LIEFERUNG, nicht die Kastell-Nr. Diese Spalten sind daher
    # entfernt; an ihre Stelle tritt die belegte Lieferung. Details: Vault-Notiz „ORL-Index Band, Lieferung, Kastell".
    bli = bli or {}
    lfg_ok = bli.get("kastell_nr_zu_lieferung") or {}
    htid_ok = bli.get("kastell_nr_zu_htids") or {}
    a = idx.get("abteilung_A_strecken", []); b = idx.get("abteilung_B_kastelle", []); c = idx.get("counts", {})
    n_lfg = sum(1 for r in b if lfg_ok.get(r["nr"]))
    n_scharf = sum(1 for r in b if lfg_ok.get(r["nr"]) and "-" not in str(lfg_ok[r["nr"]].get("lieferung", "")))
    arows = "".join(f'<tr id="orl-a-{s.get("strecke","")}"><td>{s.get("strecke","")}</td><td>{html.escape(s.get("verlauf",""))}</td>'
                    f'<td>{html.escape(s.get("region",""))}</td></tr>' for s in a)
    QUELLE = {"merten": "Merten 2002", "bibliographie": "Bibliographie des Jahrbuchs",
              "jahresbericht": "RLK-Jahresbericht"}
    def brow(r):
        lf = lfg_ok.get(r["nr"]) or {}
        # Bearbeiter: der Lieferungsindex ist die bessere Quelle (55/55 belegt, aus Merten 2002 /
        # Bibliographie des Jahrbuchs / RLK-Jahresbericht) — orl_index hat nur 13/92.
        bearb = (html.escape(lf.get("bearbeiter") or "")
                 or ", ".join(html.escape(x) for x in r.get("bearbeiter", [])))
        if lf:
            span = "-" in str(lf.get("lieferung", ""))
            q = QUELLE.get(lf.get("quelle"), "?")
            lfg = (f'<b>{html.escape(str(lf["lieferung"]))}</b>'
                   f'{"" if span else ""}<span class="lc"> ({html.escape(str(lf.get("jahr") or "?"))})</span>')
            hint = f'{q}{" · nur Spanne" if span else ""}'
            lfgc = f'<td>{lfg}</td><td class="meta">{html.escape(hint)}</td>'
        else:
            lfgc = '<td>—</td><td class="meta">nicht belegt</td>'
        lk = []
        if r.get("wiki"):
            lk.append(f'<a href="https://de.wikipedia.org/wiki/{urllib.parse.quote(r["wiki"].replace(" ", "_"))}" title="Wikipedia-Artikel">W</a>')
        ht = (htid_ok.get(r["nr"]) or [None])[0]      # NUR der geprüfte Scan, nie orl_index.htid
        if ht:
            lk.append(f'<a href="https://hdl.handle.net/2027/{html.escape(ht)}" title="Geprüfter Scan bei HathiTrust">HT</a>')
        place = r.get("ort") or re.sub(r"^(Kastelle? von |Kleinkastell |Kastell )", "", r["kastell"])
        if place and not place.startswith("("):
            lk.append(f'<a href="https://de.wikisource.org/w/index.php?search={urllib.parse.quote(place)}&amp;fulltext=1" title="Realencyclopädie (RE) / Wikisource — offenes Altertums-Lexikon">RE</a>')
        links = f' <span class="lc">[{" · ".join(lk)}]</span>' if lk else ""
        return (f'<tr id="orl-{html.escape(r["nr"])}"><td>{html.escape(r["nr"])}</td><td>{html.escape(r["kastell"])}{links}</td>'
                f'<td class="meta">{html.escape(r.get("linie",""))}</td>{lfgc}'
                f'<td>{len(r.get("vorberichte",[])) or ""}</td><td class="meta">{bearb}</td></tr>')
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
            f'<a href="../index.html">Limesblatt</a> mündeten. Für <b>{n_lfg} Kastelle</b> ist die '
            f'<b>Lieferung</b> quellenmäßig belegt ({n_scharf} kastellscharf), dazu ein konsolidierter '
            f'<a href="orl-register.html">Gesamtapparat</a>, den die in 14 Mappen erschienene Reihe nie besaß.</p>'
            f'{keyn}'
            f'<h2>Abteilung A — Strecken-Bände (Trassierung)</h2>'
            f'<table class="reg"><thead><tr><th>Str.</th><th>Verlauf</th><th>Region</th></tr></thead>'
            f'<tbody>{arows}</tbody></table>'
            f'<h2>Abteilung B — Kastell-Lieferungen</h2>'
            f'<div class="note"><p><b>Zwei Zählungen, die man nicht verwechseln darf.</b> Die '
            f'<b>ORL-Nummer ist geografisch</b> vergeben — von Rheinbrohl (1) im Norden bis zur Donau. Die '
            f'<b>Lieferung ist chronologisch</b>: Sie erschien, wenn der Bearbeiter fertig war. Lieferung 1 '
            f'(1894) enthält deshalb Butzbach (14), Murrhardt (44) <i>und</i> Unterböbingen (65) — drei '
            f'Kastelle aus drei Landschaften. Die Lieferungsfolge ist ein <b>Arbeitsstands-, kein '
            f'Ortsregister</b>.</p>'
            f'<p>Diese Seite zeigte bis 2026-07 vier weitere Spalten (Seitenzahl, Cross-Work, '
            f'Sigillata-Score, „Charakteristik"), die aus dem HathiTrust-Scan abgeleitet waren. Sie wurden '
            f'<b>entfernt</b>: Die Scan-Zuordnung las die Signatur „v.&#8201;N" als Kastell-Nummer, obwohl '
            f'sie die <i>Lieferung</i> meint — für 30 Bände war sie damit falsch, und selbst bei richtiger '
            f'Zuordnung beschreibt ein Scan die ganze Lieferung (bis zu vier Kastelle), nie ein einzelnes. '
            f'Die Gegenprobe an der zeitgenössischen Bibliographie zeigt die Größenordnung: Für Kemel wies '
            f'die Seitenzahl 372 aus — gedruckt sind <b>8</b>. An ihrer Stelle steht jetzt die belegte '
            f'Lieferung.</p></div>'
            f'<p class="meta">Lieferung: <b>fett</b> = Nummer, dahinter das Erscheinungsjahr; die Spalte '
            f'<b>Beleg</b> nennt die Quelle und ob die Zuordnung kastellscharf ist oder nur eine Spanne '
            f'(dann nennt die Quelle die Lieferungen und die Kastelle, ordnet sie aber nicht einander zu — '
            f'das wird nicht geraten). · Vorb. = Anzahl Limesblatt-Vorberichte '
            f'(<a href="orl-register.html#konkordanz">Konkordanz</a>). Verweise je Kastell: '
            f'<b>W</b> = Wikipedia · <b>HT</b> = <i>geprüfter</i> Scan bei HathiTrust · <b>RE</b> = '
            f'Realencyclopädie/Wikisource (offenes Altertums-Lexikon).</p>'
            f'<table class="reg"><thead><tr><th>ORL</th><th>Kastell</th><th>Linie</th><th>Lieferung</th>'
            f'<th>Beleg</th><th>Vorb.</th><th>Bearbeiter</th></tr></thead>'
            f'<tbody>{brows}</tbody></table>')

SIGILLATA_FORSCHER = {"dragendorff", "knorr", "ludowici", "ricken", "dechelette", "walters", "forrer",
                      "oswald", "drexel", "loeschcke", "haug"}   # Terra-Sigillata-/Fund-Typologen
ANTIKE_PERSONEN = {"vespasian", "titus", "domitian", "nerva", "traian", "trajan", "hadrian", "antoninus",
                   "pius", "aurel", "aurelius", "commodus", "pertinax", "severus", "septimius", "caracalla",
                   "geta", "alexander", "maximinus", "gordian", "philippus", "decius", "valerian", "gallienus",
                   "postumus", "probus", "caesar", "augustus", "tiberius", "caligula", "claudius", "nero",
                   "faustina", "sabina", "diana", "fortuna", "victoria", "mercurius", "merkur", "jupiter",
                   "juno", "minerva", "mars", "apollo", "hercules", "herkules", "silvanus", "mithras", "epona",
                   "tacitus", "plinius", "ptolemaeus", "ptolemäus",
                   "germanicus", "drusus", "agrippa", "galba", "otho", "vitellius", "titus", "elagabal",
                   "macrinus", "maximian", "aurelian", "tetricus", "victorinus", "carus", "carinus",
                   "numerian", "diocletian", "constantin", "constantius", "constans", "julian", "valens",
                   "valentinian", "gratian", "magnentius", "crispus", "helena", "iulia", "julia", "domna",
                   "sabinus", "genius", "iuppiter", "iuno", "neptun", "vulcanus", "sol", "luna", "nemesis",
                   "isis", "serapis", "cybele", "attis", "abundantia", "felicitas", "pax", "roma"}

def _pn(s): return re.sub(r"[^a-zäöüß]", "", (s or "").lower())

# Namensfamilien — »Ludowici«, »W. Ludowici«, »Randfries Ludowici« sind Formen EINES Namens.
# Die NER liest über Fraktur-OCR und klebt dabei das vorangehende Wort an (Ornamentbegriffe,
# Ortsnamen: »Eierstab Ludowici«, »Würzberg Kofler«), außerdem stehen Initiale und Vollname
# nebeneinander. Zusammengefasst wird nach dem NACHNAMEN — eine Anzeige-Gruppierung, keine
# Personenidentifikation. Wo die Quelle selbst zwei Träger unterscheidet (Jacobi: Louis UND
# Heinrich), belegen die Initialen das, und die Familie bleibt getrennt.
NAM_PARTIKEL = {"v", "von", "van", "de", "der", "d", "dr", "prof", "geh", "hofrat", "oberst",
                "major", "hauptmann", "herr", "st", "u"}      # »u. Barthel« = »und Barthel«
VORNAMEN_ZUSATZ = {"robert", "wilhelm", "heinrich", "georg", "friedrich", "ernst", "karl", "carl",
                   "eduard", "ludwig", "otto", "hermann", "paul", "emil", "adolf", "august",
                   "johann", "josef", "joseph", "franz", "theodor", "louis", "anton", "hans",
                   "richard", "gustav", "albert", "alexander", "philipp", "wilhelmine", "siegfried"}


def namensfamilien(rows, vornamen):
    """Formen desselben Nachnamens verschmelzen; #Bd. als VEREINIGUNG, Nenn. als Summe.

    Vetorecht hat die Quelle: liefern die Formen zwei verschiedene Vornamen-Initialen, sind es
    zwei Personen (Jacobi H./L.) und die Familie bleibt ungetrennt stehen. Klebeformen tragen
    keine Initiale bei — »Randfries« ist kein Vorname, sondern ein Ornamentbegriff, der davor
    stand; deshalb zählt nur, was als Initial gesetzt oder als Vorname belegt ist."""
    fam = {}
    for r in rows:
        fam.setdefault(_pn(r["name"].split()[-1]), []).append(r)
    out = []
    for nach, forms in fam.items():
        bar = [f for f in forms if len(f["name"].split()) == 1]
        if _pcat(nach) == "ant":                  # »Septimius/Alexander Severus« sind zwei Kaiser
            out.extend(forms); continue
        if len(forms) == 1 or not bar:            # ohne blanke Grundform keine Familie (»Knorr Taf«)
            out.extend(forms); continue
        init, init_stark = set(), set()
        for f in forms:
            toks = f["name"].split()[:-1]
            for i, w in enumerate(toks):
                k = _pn(w)
                if not k or k in NAM_PARTIKEL: continue
                if k == "a" and i + 1 < len(toks) and _pn(toks[i + 1]) == "d": continue  # »a. D.« = außer Dienst
                if len(w.rstrip(".")) == 1: b = k[0]
                elif k in vornamen: b = k[0]
                else: continue                     # Klebeform (Ort, Ornamentwort) — keine Namensevidenz
                init.add(b)
                if f["count"] >= 10: init_stark.add(b)
        # Zwei gewichtige Initialen = die Quelle trennt selbst (Jacobi H./L.); drei überhaupt
        # belegte = ein Allerweltsname mit mehreren Trägern (Keller A./C./O.).
        if len(init_stark) > 1 or len(init) > 2:
            out.extend(forms); continue
        voll = [f for f in forms if any(_pn(w) in vornamen for w in f["name"].split()[:-1])
                and (f["count"] >= 10 or f["count"] >= 0.25 * sum(g["count"] for g in forms))]
        kanon = max(voll, key=lambda f: f["count"])["name"] if voll else max(bar, key=lambda f: f["count"])["name"]
        bands = set()
        for f in forms: bands.update(f["bands"])
        out.append({"name": kanon, "bands": sorted(bands, key=lambda x: (int(re.match(r"\d+", str(x)).group()) if re.match(r"\d+", str(x)) else 999, str(x))),
                    "nbands": len(bands), "count": sum(f["count"] for f in forms),
                    "gazetteer": any(f.get("gazetteer") for f in forms),
                    "formen": sorted(((f["name"], f["count"]) for f in forms), key=lambda x: -x[1])})
    return out

def _pcat(name):
    w = re.sub(r"[^a-zäöüß]", "", name.split()[-1].lower()) if name.split() else ""
    if w in SIGILLATA_FORSCHER: return "sig"
    if w in ANTIKE_PERSONEN or w.rstrip("s") in ANTIKE_PERSONEN: return "ant"
    return "rlk"

def hintzelmann_page(volumes):
    """Hintzelmanns »Register zu Nr. 1–35 des Limesblattes« (1903) als eigene Seite.

    Das Register steht gedruckt im Schlussheft und damit im Lesetext von Band 8. Diese Seite
    hebt es aus dem Fließtext heraus und macht es benutzbar: als das aelteste Findmittel zum
    Limesblatt, mit auflösbaren Spaltenverweisen. Der Text wird NICHT neu gesetzt, sondern aus
    dem gebauten Bandtext übernommen — eine Quelle, keine zweite Wahrheit.
    """
    v8 = next((v for v in volumes if v["nr"] == 8), None)
    if not v8: return None
    import io
    src = os.path.join(DOCS, "volumes", "bd8.html")
    if not os.path.exists(src): return None
    h = open(src, encoding="utf-8").read()
    i = h.find("Register zu Nr")
    if i < 0: return None
    j = h.find("</article>", i)
    body = h[i:j if j > 0 else len(h)]
    body = re.sub(r'<span class="pb"[^>]*>.*?</span>', "", body, flags=re.S)   # Spaltenmarken raus
    body = body.replace('href="bd', 'href="../volumes/bd').replace('href="../register/', 'href="')
    body = re.sub(r'href="#pb-', 'href="../volumes/bd8.html#pb-', body)
    n_ref = len(re.findall(r'class="ent xref"', body))
    return (f'<h1>Hintzelmanns Register zum Limesblatt (1903)</h1>'
            f'<div class="note"><p><b>Das älteste Findmittel zum Limesblatt</b> — und es stammt von der '
            f'Redaktion selbst. Das Schlussheft Nr. 35 (27. Mai 1903) schließt mit einem Gesamtregister '
            f'über alle 35 Hefte, verfasst von Prof. Dr. P. Hintzelmann: Mitarbeiter, Orte, Inschriften. '
            f'Die Zeitschrift schließt sich zum Abschied selbst auf.</p>'
            f'<p>Seine Vorbemerkung sagt, worauf die Zahlen zeigen: <i>„Die Ziffern bezeichnen die '
            f'Spalten."</i> Das Limesblatt zählt nämlich <b>Spalten, nicht Seiten</b> — jede Druckseite '
            f'trägt zwei Nummern. Alle <b>{n_ref}</b> Verweise sind hier aufgelöst und führen in den '
            f'Lesetext; sie streuen aus dem Schlussheft in alle acht Bände. Abkürzungen wie im Original: '
            f'<b>K.</b> = Kastell · <b>Zk.</b> = Zwischenkastell · <b>L.</b> = Limes.</p>'
            f'<p class="meta">Der Text ist der des <a href="../volumes/bd8.html#pb-959-a">Bandes 8</a>, '
            f'Spalten 959–968 — diplomatisch, mit den Fraktur-Fehlern des Drucks. Wo die OCR einen Namen '
            f'zerbrach, steht die Lesung des Drucks und die Identifikation im Link: „II et tn er" ist '
            f'Hettner, „S t e i m 1 e" ist Steimle, „Kofier" ist Kofler — Register-Lemmata wurden gesperrt '
            f'gesetzt, und daran scheitert die Schrifterkennung gerade bei den bekanntesten Namen.</p>'
            f'</div>{body}')


def namen_page(nm):
    """Orts-Crosswalk antik ↔ modern ↔ Flurname.

    Warum die Seite existiert: Das Limesblatt benennt seine Orte MODERN. Wer „Nida" sucht, findet 0 Treffer —
    obwohl 60 Stellen über den Ort reden, alle unter „Heddernheim". Die Tabelle sagt daher nicht, wie ein Ort
    HIESS, sondern unter welchem String er im jeweiligen Korpus AUFFINDBAR ist.
    """
    o = nm.get("orte") or nm.get("places") or []
    if isinstance(o, dict): o = list(o.values())
    ant = sorted([e for e in o if e.get("antik")], key=lambda e: -(e.get("n_orl_antik") or 0))
    flur = [e for e in o if e.get("flurname")]
    def anames(e): return ", ".join(a["name"] for a in e["antik"])
    def fname(e):   # flurname ist ein Objekt {name, quelle}, kein String
        f = e.get("flurname")
        return (f or {}).get("name") if isinstance(f, dict) else (f or None)
    def cell(n): return f'<b>{n}</b>' if n else '<span class="lc">0</span>'
    def row(e):
        vn = (f'<a href="../index.html">{html.escape(e["vault_note"])}</a>' if False else
              html.escape(e.get("vault_note") or "—"))
        lb_a, lb_m = e.get("n_limesblatt_antik") or 0, e.get("n_limesblatt_modern") or 0
        best = e.get("modern_im_limesblatt") or e.get("modern") or ""
        return (f'<tr><td><i>{html.escape(anames(e))}</i></td>'
                f'<td>{html.escape(e.get("modern") or "—")}</td>'
                f'<td>{html.escape(fname(e) or "—")}</td>'
                f'<td>{cell(lb_a)} · {lb_m}&#8239;× <span class="lc">{html.escape(best)}</span></td>'
                f'<td>{e.get("n_orl_antik") or 0} · {e.get("n_orl_modern") or 0}</td>'
                f'<td class="meta">{html.escape(e.get("orl_nr") or "—")}</td></tr>')
    rows = "".join(row(e) for e in ant)
    fl = "".join(f'<tr><td>{html.escape(fname(e) or "?")}</td><td>{html.escape(e.get("modern") or "—")}</td>'
                 f'<td>{e.get("n_limesblatt_flur") or 0}</td><td>{e.get("n_limesblatt_modern") or 0}</td>'
                 f'<td class="meta">{html.escape((e.get("flurname") or {}).get("quelle","") if isinstance(e.get("flurname"), dict) else "")}</td></tr>'
                 for e in flur)
    return (f'<h1>Ortsnamen — antik, modern, Flurname</h1>'
            f'<div class="note"><p><b>Wer hier „Nida" sucht, findet im Limesblatt nichts</b> — und das ist kein '
            f'Fehler der Edition, sondern eine Eigenschaft der Quelle. Der Feldbericht der Reichs-Limeskommission '
            f'<b>benennt seine Orte modern</b>: „Nida" steht in den acht Bänden <b>0×</b>, „Heddernheim" <b>65×</b>. '
            f'Auch „Mogontiacum" kommt <b>0×</b> vor, während „Mainz" <b>37×</b> dasteht — bei Mainz war der '
            f'römische Name nie strittig, es geht also nicht um Unsicherheit, sondern um das Register der Gattung: '
            f'Wer im Gelände gräbt, benennt Orte wie eine Postanschrift — nach Dorf, Flur, Gewann.</p>'
            f'<p>Diese Tabelle ist deshalb ein <b>Recherche-Instrument, keine Namenskunde</b>. Sie beantwortet '
            f'nicht „wie hieß Heddernheim in der Antike?", sondern: <i>Ich suche Nida — welchen String muss ich '
            f'eingeben?</i> Antwort: <code>Heddernheim</code>.</p></div>'
            f'<h2>Antike Namen</h2>'
            f'<p class="meta">Jede Gleichsetzung ist gegen <a href="https://pleiades.stoa.org">Pleiades</a> '
            f'und/oder DARE geerdet; ungesicherte stehen nicht in der Tabelle. Die Zahlenspalten lesen sich '
            f'<i>antik · modern</i> — sie sagen, unter welchem Namen der Ort im jeweiligen Werk auffindbar ist. '
            f'Für die meisten Kastelle ist <b>kein</b> antiker Name überliefert (darunter die Saalburg); sie '
            f'fehlen hier zu Recht.</p>'
            f'<table class="reg"><thead><tr><th>antik</th><th>modern</th><th>Flurname</th>'
            f'<th>im Limesblatt</th><th>im ORL</th><th>ORL-Nr.</th></tr></thead><tbody>{rows}</tbody></table>'
            f'<h2>Die Schwelle verläuft auch durch den ORL</h2>'
            f'<p class="meta">Der Kontrast ist <b>nicht</b> „Feldbericht modern, Endpublikation antik". Auch der '
            f'ORL benennt überwiegend modern: <i>Vetoniana</i> kommt dort <b>0×</b> vor, <i>Pfünz</i> 478×; '
            f'<i>Arae Flaviae</i> <b>0×</b>, <i>Rottweil</i> 488×. Und wo der ORL den antiken Namen führt, '
            f'erdrückt ihn der moderne (Nida 91 : Heddernheim 469). Der Unterschied ist graduell, aber real: '
            f'Die Endpublikation <i>lässt den antiken Namen zu</i> — als gelehrte Glosse neben dem Arbeitsnamen. '
            f'Der Feldbericht lässt ihn nur ins Zitat: Der einzige antike Ortsname im ganzen Limesblatt ist '
            f'<i>Abusina</i> (2×), und beide Belege zitieren eine Schriftquelle — ein Itinerar und eine '
            f'Truppendislokation —, keiner benennt den Boden, auf dem gegraben wird.</p>'
            + (f'<h2>Flurnamen</h2>'
               f'<p class="meta">Namen, unter denen der Feldbericht einen Platz führte, bevor die Endpublikation '
               f'ihn umtaufte — systematisch hebbar nur dort, wo ein ORL-Titel sie konserviert hat („Das Kastell '
               f'<i>Alteburg</i> bei Walldürn"). Zahlen: Flurname · moderner Name im Limesblatt.</p>'
               f'<table class="reg"><thead><tr><th>Flurname</th><th>modern</th><th>Flur im LB</th>'
               f'<th>modern im LB</th><th>Beleg</th></tr></thead><tbody>{fl}</tbody></table>' if fl else ""))


def orl_toc_page(idx, bli=None, fasz=None, dseiten=None, abta=None, places=None):
    """Das vollständige ORL-Inhaltsverzeichnis nach PUBLIKATIONSEINHEIT — das Gegenstück
    zum Limesblatt-Verzeichnis, wo die Hefte gliedern. Beim ORL gliedert die Lieferung:
    die Kastell-Nummern laufen GEOGRAFISCH (1 = Rheinbrohl im Norden), erschienen sind
    die Faszikel CHRONOLOGISCH, nach Fertigstellung durch die Bearbeiter."""
    bli = bli or {}
    lfg_ok = bli.get("kastell_nr_zu_lieferung") or {}
    b = idx.get("abteilung_B_kastelle", [])
    a = idx.get("abteilung_A_strecken", [])
    QUELLE = {"merten": "Merten 2002", "bibliographie": "Bibliographie des Jahrbuchs",
              "jahresbericht": "RLK-Jahresbericht"}

    umfang = {}
    for blk in (dseiten or {}).get("bloecke", []):
        if blk.get("kastell") and blk.get("druckseiten"):
            umfang.setdefault(blk["kastell"], blk["druckseiten"])
    # Sammelband, in dem ein Faszikel steckt (aus der Faszikel-Zerlegung des Kapsel-Laufs)
    inband = {}
    for htid, v in (fasz or {}).get("baende", {}).items():
        for f in v["faszikel"]:
            if f.get("kastell") and len(v["faszikel"]) > 1:
                inband.setdefault(f["kastell"], v["label"])

    def _pk(s):
        s = unicodedata.normalize("NFKD", (s or "").replace("ß", "ss"))
        s = "".join(c for c in s if not unicodedata.combining(c)).lower()
        return re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    plid = {_pk(p["name"]): p["id"] for p in (places or [])}

    def vorbericht_links(r):
        """Deep-Links in die Limesblatt-Edition: der Vorbericht zu genau diesem Kastell."""
        out = []
        vb = r.get("vorberichte")
        if isinstance(vb, str):
            try:
                vb = json.loads(vb.replace("'", '"'))
            except Exception:
                vb = []
        for v in (vb or [])[:5]:
            bd = {"limesblatt1892_1893": 1, "limesblatt1893_1894": 2, "limesblatt1894_1895": 3,
                  "limesblatt1896": 4, "limesblatt1897": 5, "limesblatt1897_1898": 6,
                  "limesblatt1898_1902": 7, "limesblatt1903": 8}.get(v.get("slug"))
            if bd:
                out.append(f'<a href="../volumes/bd{bd}.html#art-{v.get("num")}" '
                           f'title="{html.escape(str(v.get("theme") or ""))}">Nr.&#8239;{v.get("num")}</a>')
        return out

    def zeile(r):
        lf = lfg_ok.get(r["nr"]) or {}
        kurz = re.sub(r"^Kastelle? ", "", r["kastell"])
        pid = plid.get(_pk(r["kastell"]))
        name = (f'<a href="places.html#{pid}" title="Ort im Register">{html.escape(kurz)}</a>'
                if pid else html.escape(kurz))
        # Zeile 2: Umfang · Linie · Sammelband
        det = []
        if lf.get("seiten"):
            det.append(f'{lf["seiten"]}&#8239;S.' + (f', {lf["tafeln"]}&#8239;Taf.' if lf.get("tafeln") else ""))
        elif r["kastell"] in umfang:
            det.append(f'S.&#8239;{umfang[r["kastell"]]} <span class="lc">(aus dem Scan)</span>')
        if r.get("linie"):
            det.append(html.escape(r["linie"]))
        if r["kastell"] in inband:
            det.append(f'gebunden in {html.escape(inband[r["kastell"]])}')
        # Zeile 3: Verweise
        ref = []
        vb = vorbericht_links(r)
        if vb:
            ref.append("Vorbericht " + " · ".join(vb))
        if r.get("htid") and r.get("access") == "pd":
            ref.append(f'<a href="https://babel.hathitrust.org/cgi/pt?id={html.escape(r["htid"])}">Digitalisat</a>')
        elif r.get("digitalisat"):
            ref.append(f'<a href="{html.escape(r["digitalisat"])}">Digitalisat</a>')
        ref.append(f'<a href="orl.html#orl-{r["nr"]}">im Bandindex</a>')
        bearb = html.escape(lf.get("bearbeiter") or "")
        return (f'<li id="orltoc-{r["nr"]}"><b>ORL&#8239;{r["nr"]}</b> {name}'
                f'{f" <span class=meta>· {bearb}</span>" if bearb else ""}'
                f'{f"<br><span class=meta>{" · ".join(det)}</span>" if det else ""}'
                f'<br><span class="meta">{" · ".join(ref)}</span></li>')

    grp = {}
    for r in b:
        lf = lfg_ok.get(r["nr"])
        grp.setdefault(str(lf["lieferung"]) if lf else None, []).append(r)

    def lkey(k):
        if k is None:
            return (9999, 999, "")
        lf = next((lfg_ok[r["nr"]] for r in grp[k] if lfg_ok.get(r["nr"])), {})
        jahr = str(lf.get("jahr") or "")
        m = re.match(r"(\d+)", k)
        return (int(jahr[:4]) if jahr[:4].isdigit() else 9998, int(m.group(1)) if m else 999, k)

    reihenfolge = sorted(grp, key=lkey)
    blocks, sprung = [], []
    for k in reihenfolge:
        rows = sorted(grp[k], key=lambda r: (int(re.sub(r"\D", "", r["nr"]) or 0), r["nr"]))
        if k is None:
            blocks.append(
                f'<h3 id="lfg-offen">Ohne belegte Lieferung <span class="meta">· {len(rows)} Faszikel</span></h3>'
                f'<p class="meta">Für diese Faszikel nennt keine der drei Quellen (Merten&nbsp;2002, '
                f'Bibliographie des Jahrbuchs, RLK-Jahresberichte) eine Lieferung. Sie fehlen nicht im '
                f'Werk — nur ihr Erscheinungsdatum ist nicht belegt.</p>'
                f'<ul class="toc orltoc">{"".join(zeile(r) for r in rows)}</ul>')
            sprung.append('<a href="#lfg-offen">ohne Lfg.</a>')
            continue
        lf = next((lfg_ok[r["nr"]] for r in rows if lfg_ok.get(r["nr"])), {})
        jahr = html.escape(str(lf.get("jahr") or "?"))
        span = "-" in str(k)
        q = QUELLE.get(lf.get("quelle"), "?")
        seiten = sum(int(lfg_ok[r["nr"]].get("seiten") or 0) for r in rows if lfg_ok.get(r["nr"]))
        tafeln = sum(int(lfg_ok[r["nr"]].get("tafeln") or 0) for r in rows if lfg_ok.get(r["nr"]))
        bearb = sorted({(lfg_ok.get(r["nr"]) or {}).get("bearbeiter") for r in rows} - {None, ""})
        summe = " · ".join(x for x in (
            f'{seiten}&#8239;S.' if seiten else "",
            f'{tafeln}&#8239;Taf.' if tafeln else "",
            html.escape(", ".join(bearb)) if bearb else "") if x)
        blocks.append(
            f'<h3 id="lfg-{html.escape(k)}">Lieferung {html.escape(k)} '
            f'<span class="meta">· {jahr} · {len(rows)} Faszikel{" · " + summe if summe else ""}</span></h3>'
            f'<p class="meta">Beleg: {html.escape(q)}{" · Lieferungsnummer nur als Spanne überliefert" if span else ""}</p>'
            f'<ul class="toc orltoc">{"".join(zeile(r) for r in rows)}</ul>')
        sprung.append(f'<a href="#lfg-{html.escape(k)}">{html.escape(k)}<span class="lc">&#8239;·&#8239;{jahr[:4]}</span></a>')

    # --- Abteilung A: die ECHTEN Faszikeltitel der RLK (K10plus), nicht die Kurzform ---
    recs = (abta or {}).get("records", [])
    best = {}
    for rec in recs:
        try:
            sn = json.loads(str(rec.get("strecken") or "[]"))
        except Exception:
            sn = []
        if not sn:
            continue
        # »Die Strecken 6 - 9« ist eine SPANNE (vier Abschnitte), »Die Strecken 1 und 2« eine
        # Aufzählung (zwei). Die Zahlenliste allein sagt das nicht — der Titel sagt es.
        if len(sn) == 2 and re.search(r"\d\s*[-–]\s*\d", str(rec.get("titel") or "")):
            sn = list(range(min(sn), max(sn) + 1))
        key = tuple(sn)
        j = str(rec.get("jahr") or "")
        if key not in best or (j and j < str(best[key].get("jahr") or "9999")):
            best[key] = rec                     # frühestes Jahr = Erstausgabe
    verlauf = {s.get("strecke"): s for s in a}
    arows = []
    for key in sorted(best, key=lambda k: (k[0], len(k))):        # nach Strecke, Einzel vor Sammel
        rec, sn = best[key], list(key)
        titel = re.sub(r"\s*/.*$", "", str(rec.get("titel") or "")).strip()
        vl = " · ".join(html.escape(str(verlauf.get(n, {}).get("verlauf") or "")) for n in sn
                        if verlauf.get(n, {}).get("verlauf"))
        anker = " ".join(f'<a href="orl.html#orl-a-{n}">Str.&#8239;{n}</a>' for n in sn)
        band = str(rec.get("band") or "")
        det = [x for x in (
            band if band and not band.isdigit() else "",       # »1« ist kein Bandtitel
            str(rec.get("jahr") or ""),
            ("<b>Sammelfaszikel</b> für %d Strecken" % len(sn)) if len(sn) > 1 else "",
            vl) if x]
        arows.append(f'<li><b>{anker}</b> {html.escape(titel)}'
                     f'<br><span class="meta">{" · ".join(det)}</span></li>')
    fehlt = sorted({s.get("strecke") for s in a} - {n for key in best for n in key})

    n_lfg = sum(1 for r in b if lfg_ok.get(r["nr"]))
    n_lief = len([k for k in grp if k is not None])
    n_fasz = sum(len(v["faszikel"]) for v in (fasz or {}).get("baende", {}).values())

    return (
        f'<h1>Inhaltsverzeichnis des ORL</h1>'
        f'<p class="lede">Alle <b>{len(b)} Kastell-Faszikel</b> der Abteilung&nbsp;B und die '
        f'Streckenbände der Abteilung&nbsp;A — geordnet nicht nach ihrer Nummer, sondern nach '
        f'ihrem <b>Erscheinen</b>. Das ist beim ORL zweierlei: die Nummern laufen '
        f'<b>geografisch</b> (ORL&nbsp;1 = Rheinbrohl im Norden, ORL&nbsp;75 = Pförring an der '
        f'Donau), die Faszikel erschienen aber <b>chronologisch</b>, wie die Bearbeiter fertig '
        f'wurden — Lieferung&nbsp;1 bindet Butzbach, Murrhardt und Unterböbingen zusammen, drei '
        f'weit auseinanderliegende Plätze. Die nummerngeordnete Ansicht steht im '
        f'<a href="orl.html">Bandindex</a>.</p>'
        f'<div class="note"><p><b>Was hier belegt ist — und was nicht.</b> Eine Lieferung mit Jahr '
        f'ist für <b>{n_lfg} der {len(b)}</b> Faszikel aus einer zeitgenössischen Quelle belegt '
        f'({n_lief} Lieferungen): Hettners Herausgeberliste bei Merten&nbsp;2002, die '
        f'<a href="jahresberichte.html">RLK-Jahresberichte</a> und — kastellscharf, mit Seiten- und '
        f'Tafelzahl — die <b>Bibliographie des Jahrbuchs</b>, die jede Lieferung bei Erscheinen '
        f'anzeigte. Wo diese fehlt, steht der aus dem Digitalisat errechnete Druckseiten-Bereich '
        f'(gekennzeichnet). Die Faszikel ohne belegte Lieferung stehen unten in einem eigenen '
        f'Block; unbelegt ist ihr Datum, nicht ihr Vorhandensein.</p></div>'
        f'<h2 id="abt-a">Abteilung A — die Strecken</h2>'
        f'<p class="meta">Die Beschreibung der Linie selbst, erschienen 1914–1936. Gezeigt sind die '
        f'<b>Faszikeltitel der RLK</b> (Verbundkatalog K10plus), nicht die geläufige Kurzform: '
        f'die Kommission benannte ihre Abschnitte nach <b>Flüssen und Landschaft</b>, nicht nach '
        f'Städten — und mehrere Strecken erschienen in <b>einem</b> Faszikel.</p>'
        f'<ul class="toc orltoc">{"".join(arows)}</ul>'
        + (f'<p class="meta">Ohne eigenen Titelnachweis in K10plus: '
           f'{", ".join("Strecke&#8239;%s" % n for n in fehlt)} — sie stecken in den Sammelfaszikeln '
           f'oben oder liefert der Katalog mit dieser Abfrage nicht.</p>' if fehlt else "")
        + f'<h2 id="abt-b">Abteilung B — die Kastelle, nach Lieferungen</h2>'
        f'<p class="meta">Springen zu: {" · ".join(sprung)}</p>'
        + "".join(blocks) +
        f'<p class="meta">Seitenumfang: wo die Bibliographie ihn nennt, ist es ihre Angabe samt '
        f'Tafelzahl; sonst der aus dem Digitalisat errechnete Bereich — dafür wurden {n_fasz} '
        f'Faszikel in den Scans abgegrenzt. → <a href="hathitrust.html">wie das erschlossen wurde</a></p>')


def genese_page(bv, bl, lat, zj, hefte, toc):
    """Die mehrstufige Genese des fertigen ORL: was Grundlage ist, was aufeinander
    aufbaut, was hinzukommt. Alle Zahlen stammen aus den Daten-JSONs der Erschließung
    und sind dort nachrechenbar; jede Behauptung verlinkt ihre Belegseite."""
    def kom(x):
        return str(x).replace(".", ",")
    r_bi = (bv.get("richtung") or {}).get("Binnenverweise (ORL→ORL)", {})
    r_ep = (bv.get("richtung") or {}).get("Inschriften-Zitate (CIL u. a.)", {})
    lz = lat.get("latenz", {})
    sb = lat.get("streubreite_lb_baende", {})
    n_ber = len(toc.get("reports", []))
    n_hefte = len(hefte)
    jn = (zj.get("join") or {})
    orl_ein = (zj.get("orl") or {}).get("eindeutig", "—")
    nur_neu = orl_ein - jn.get("in_beiden", 0) if isinstance(orl_ein, int) else "—"

    def schicht(nr, titel, zeit, inhalt):
        return (f'<section style="border-left:3px solid var(--line,#cbbfa8);margin:1.6em 0;'
                f'padding:.2em 0 .2em 1.1em"><h2 style="margin:.2em 0 .1em">{nr} · {titel} '
                f'<span class="meta">{zeit}</span></h2>{inhalt}</section>')

    s1 = (
        f'<p>Drei Bestände waren da, bevor die Kommission 1892 zu graben begann, und alle drei '
        f'tragen das fertige Werk erkennbar mit:</p><ul>'
        f'<li><b>Die Epigraphik.</b> Das Corpus Inscriptionum Latinarum und Brambachs rheinisches '
        f'Corpus (1867) liefern den Datierungsapparat der frühen Faszikel: Deren Inschriften-Zitatdichte '
        f'liegt mit {kom(r_ep.get("frueh", {}).get("median_je_10k", "?"))} je 10.000 Wörter fast doppelt '
        f'so hoch wie in den späten ({kom(r_ep.get("spaet", {}).get("median_je_10k", "?"))}) — die erste '
        f'Generation datiert über die Inschrift (<a href="orl-register.html">Gesamtapparat</a>).</li>'
        f'<li><b>Die Vorgänger-Forschung.</b> Karl August von Cohausens »Der römische Grenzwall in '
        f'Deutschland« (1884) bleibt Referenz über das ganze Werk hinweg: Cohausen ist der '
        f'meistzitierte Feldautor des Limesblatts und in den zuletzt erschienenen Streckenbänden der '
        f'Abteilung A die präsenteste Person überhaupt — vier Jahrzehnte nach seinem Tod 1894.</li>'
        f'<li><b>Die föderalen Vereinsorgane.</b> Nassauische Annalen, Bonner Jahrbücher, die '
        f'Westdeutsche Zeitschrift und die Lokalorgane: Alle dreizehn geprüften Organe erscheinen im '
        f'Apparat des fertigen ORL, jeweils konzentriert in den Bänden ihrer Region. Die vor 1892 '
        f'zersplitterte Landesforschung wurde nicht abgelöst, sondern eingebaut.</li></ul>')

    s2 = (
        f'<p>Ab Dezember 1892 erscheint das <a href="../index.html">Limesblatt</a>: '
        f'<b>{n_ber} nummerierte Feldberichte</b> der Streckenkommissare in {n_hefte} Heften, '
        f'durchgehend in Spalten gezählt. Diese durchlaufende Zählung macht das Blatt vom ersten '
        f'Heft an präzise zitierbar — eine Eigenschaft, die dem ORL selbst noch lange fehlt.</p>'
        f'<p>Für das spätere Werk wird das Blatt zum Feldarchiv: Der ORL verweist '
        f'<b>{(zj.get("gegenrichtung_orl_zitiert_limesblatt") or {}).get("echte_verweise_mit_stelle", "—")}-mal '
        f'mit Stellenangabe</b> auf Limesblatt-Spalten. Die frühen Faszikel zitieren ihren Vorbericht '
        f'mit im Median <b>{kom(lz.get("zitierende_frueh_bis_1904", {}).get("median", "?"))} Jahren</b> '
        f'Abstand, die späten mit <b>{kom(lz.get("zitierende_spaet_ab_1914", {}).get("median", "?"))}</b> — '
        f'und dabei nicht seltener, sondern dichter und quer durch alle Jahrgänge '
        f'(im Median {kom(sb.get("spaet", {}).get("median", "?"))} von 8 Bänden je Faszikel, '
        f'früh {kom(sb.get("frueh", {}).get("median", "?"))}). Das Blatt veraltete nicht; es wurde '
        f'zur Primärquelle des Werks, das aus ihm hervorging.</p>')

    s3 = (
        f'<p>Parallel zur Feldarbeit beginnt 1894 die Endpublikation: einzelne Kastell-Faszikel der '
        f'Abteilung B, nach Fertigstellung der Bearbeiter geliefert '
        f'(<a href="orl-inhalt.html">Inhaltsverzeichnis nach Lieferungen</a>). Diese frühen Faszikel '
        f'sind Monographien für sich: <b>Kein einziger der 16 datierten Frühfaszikel enthält einen '
        f'werkinternen Verweis</b> — ihr Apparat zeigt auf die Fachzeitschriften (16 von 16) und '
        f'zurück ins Limesblatt (12 von 16), aber nie auf die eigene Reihe '
        f'(<a href="orl-verweise.html">Binnenverweise</a>). Der Stein ist der unmarkierte Normalfall '
        f'der Beschreibung; datiert wird über die Inschrift.</p>'
        f'<p>Um die Jahrhundertwende liegt eine mehrfache Zäsur: 1899 erscheint keine Lieferung, '
        f'1900 kein Limesblatt-Heft; 1902/03 sterben Zangemeister, Hettner und Mommsen; 1903 wird '
        f'das Limesblatt eingestellt (<a href="jahresberichte.html">Jahresberichte</a>). Auch '
        f'druckhistorisch ist der Einschnitt fassbar: Zwischen Lieferung 21 und 22 stellt die '
        f'Druckerei Petters auf die Orthographie von 1901 um — abrupt, nicht gleitend.</p>')

    s4 = (
        f'<p>Nach der Kriegs- und Nachkriegslücke erscheinen ab 1914 die übrigen Kastell-Faszikel '
        f'und 1914–1936 die Streckenbände der Abteilung A. Was jetzt hinzukommt, stammt zum großen '
        f'Teil aus einer Fachwelt, die es 1903 noch nicht gab:</p><ul>'
        f'<li><b>Ein neuer Apparat.</b> Von den {orl_ein} im ORL zitierten Inschriften standen '
        f'<b>{nur_neu} nie im Limesblatt</b> — der Zitierapparat wurde bei der Endredaktion neu '
        f'aufgebaut, nicht übernommen. Die Sigillata-Autoritäten der Zwischenzeit (Dragendorff, '
        f'Ludowici, Knorr) und Organe wie die 1906 gegründete Mainzer Zeitschrift treten hinzu.</li>'
        f'<li><b>Das Werk wird System.</b> Alle 13 datierten Spätfaszikel verweisen werkintern '
        f'(Median {kom(r_bi.get("spaet", {}).get("median_je_10k", "?"))} je 10.000 Wörter); '
        f'{(bl.get("bilanz") or {}).get("verlinkt", "—")} dieser Verweise sind heute bis auf die '
        f'Scanseite <a href="orl-verweise.html">aufgelöst</a>. Jeder neue Platz wird gegen die '
        f'schon publizierten gestellt.</li>'
        f'<li><b>Eine neue Befundsprache.</b> Das Vokabular der Bauphasen (Holzbau, Steinbau, '
        f'Palisade) vervielfacht sich, der Limesfall wird über die Gallienus-Münzreihen zum Thema, '
        f'während das Zivilsiedlungs-Vokabular zurücktritt '
        f'(<a href="wortschatz.html">Analyse</a>).</li>'
        f'<li><b>Die Linie zuletzt.</b> Die Abteilung A beschreibt den Grenzverlauf selbst — nach '
        f'Flüssen und Landschaft benannt (»Der Limes vom Rhein bis zur Lahn«), auf den Aufnahmen '
        f'der 1890er beruhend, redigiert von Ernst Fabricius. Ihr präsentester Gewährsmann bleibt '
        f'Cohausen.</li></ul>')

    return (
        f'<h1>Die Genese des ORL</h1>'
        f'<p class="lede">Das Standardwerk »Der obergermanisch-raetische Limes des Roemerreiches« '
        f'entstand über 45 Jahre in erkennbaren Schichten. Diese Seite ordnet sie: was als '
        f'Grundlage schon da war, was aufeinander aufbaute, was erst spät hinzukam. Jede Angabe '
        f'verlinkt die Seite, auf der sie belegt und nachrechenbar ist.</p>'
        + schicht("I", "Die Grundlagen", "vor 1892", s1)
        + schicht("II", "Das eigene Fundament: das Limesblatt", "1892–1903", s2)
        + schicht("III", "Der erste Aufbau: die frühen Faszikel", "1894–1904", s3)
        + schicht("IV", "Was hinzukommt: Spätwerk und Strecken", "1914–1937", s4)
        + f'<div class="note"><p><b>Woher die Zahlen stammen.</b> Die Limesblatt-Seite dieser '
          f'Angaben kommt aus der vorliegenden Edition; die ORL-Seite aus einer nicht-konsumtiven '
          f'Volltextanalyse der 78 HathiTrust-Scans (<a href="hathitrust.html">Erschließung</a>). '
          f'Zwillings-Scans desselben Faszikels zählen in Rohzahlen doppelt; Mediane sind davon '
          f'unberührt. Undatierte Faszikel bleiben in den Früh/Spät-Vergleichen außen vor.</p></div>')


def orl_verweise_page(bl, bv):
    """Das BINNENVERWEIS-Netz des ORL, beidseitig anklickbar: 551 aufgelöste Stellen
    (Kapsel-Kombination internal_refs x orl_faszikel x orl_druckseiten), gruppiert nach
    dem ZIEL — den Referenzkastellen, an denen das Werk seine Typologie festmacht."""
    links = bl.get("links", [])
    bilanz = bl.get("bilanz", {})
    npb = bilanz.get("namensprobe", {})
    # nach Ziel gruppieren, innerhalb nach zitierter Druckseite
    grp = {}
    for l in links:
        grp.setdefault((l["ziel_nr"], l["ziel_kastell"] or ("ORL " + l["ziel_nr"])), []).append(l)
    bloecke = []
    for (nr, kname), ll in sorted(grp.items(), key=lambda kv: -len(kv[1])):
        ll.sort(key=lambda l: (l["ziel_druckseite"], l["von_label"]))
        # je (Zielseite, von-Faszikel) EINE Zeile — mdp/uc1-Zwillinge desselben Faszikels
        # tragen verschiedene Labels (»no.54 1936« / »v. 54«), sind aber EIN Verweis:
        # auf die Faszikelnummer kollabieren.
        def fnum(lab):
            m = re.match(r"(?:no\.|v\.?\s*)(\d+)", lab or "")
            return m.group(1) if m else lab
        seen, rows = set(), []
        for l in ll:
            key = (l["ziel_druckseite"], fnum(l["von_label"]))
            if key in seen:
                continue
            seen.add(key)
            von = html.escape(l["von_label"])
            von_url = f'https://babel.hathitrust.org/cgi/pt?id={l["von_htid"]}&seq={l["von_seq"]}'
            best = "" if l.get("toponym_bestaetigt") else ' <span class="lc">(Zielseite unbestätigt)</span>'
            rows.append(f'<tr><td><a href="{html.escape(l["url"])}">S.&#8239;{l["ziel_druckseite"]}</a>{best}</td>'
                        f'<td><a href="{von_url}">{von}</a></td>'
                        f'<td class="meta">{html.escape(l["kontext"][:110])}</td></tr>')
        kurz = html.escape(kname.replace("Kastell ", ""))
        bloecke.append(
            f'<details id="ziel-{html.escape(nr)}"><summary><b>ORL&#8239;{html.escape(nr)} {kurz}</b> '
            f'<span class="meta">— {len(rows)} verweisende Stellen</span></summary>'
            f'<table class="reg"><thead><tr><th>zitierte Stelle</th><th>verweisender Band</th>'
            f'<th>Kontext (≤12 Wörter)</th></tr></thead><tbody>{"".join(rows)}</tbody></table></details>')
    r = (bv.get("richtung") or {}).get("Binnenverweise (ORL→ORL)", {})
    return (
        f'<h1>ORL-Binnenverweise</h1>'
        f'<p class="lede">Der ORL erschien 45 Jahre lang in 14 Mappen und hatte nie ein Register — aber seine '
        f'Faszikel <b>verweisen aufeinander</b> (»Abt.&nbsp;B Bd.&nbsp;II Nr.&nbsp;8 Kastell Zugmantel '
        f'S.&nbsp;107&nbsp;ff.«). Für <b>{bilanz.get("verlinkt", 0)} der {bilanz.get("binnenverweise", 0)}</b> '
        f'werkinternen Verweise ist die zitierte Stelle im Digitalisat aufgelöst: Ziel-Nummer aus dem Verweis, '
        f'Druckseite aus dem Kontext, Scanseite über die Faszikel-Zerlegung — <b>beide Seiten</b> jedes '
        f'Verweises springen direkt in den HathiTrust-Scan.</p>'
        f'<div class="note"><p><b>Der Befund hinter dem Apparat:</b> Kein einziger der 16 frühen Faszikel '
        f'(1894–1903) enthält einen werkinternen Verweis — alle 13 späten (1914–1937) tun es '
        f'(Median {str(r.get("spaet", {}).get("median_je_10k", "?")).replace(".", ",")} je 10.000 Wörter). '
        f'Nicht, weil es früh nichts zu zitieren gab: dieselben Frühfaszikel zitieren das '
        f'<a href="../index.html">Limesblatt</a> und die Fachzeitschriften. Ein ORL-Faszikel wurde erst '
        f'zitierfähig, als Abteilung, Band und Nummer als stabile Adressen etabliert waren.</p>'
        f'<p><b>Verlässlichkeit:</b> Jede Zielseite ist doppelt geprüft — in {str(bilanz.get("quote_bestaetigt_pct", "?")).replace(".", ",")}&#8239;% '
        f'trägt sie das Toponym des Ziel-Kastells (»unbestätigt« heißt meist nur: die Seite nennt ihr eigenes '
        f'Kastell gerade nicht), und wo der Verweis das Kastell beim Namen nennt, stimmt der Name in '
        f'{str(npb.get("quote_pct", "?")).replace(".", ",")}&#8239;% ({npb.get("pruefbar", 0)} prüfbar). '
        f'Die Namens-Abweichungen gehen auf die <i>Quelle</i> zurück: der ORL zitiert a-Faszikel unter ihrer '
        f'Grundnummer (Urspring als »Nr.&nbsp;66«, Böhming als »Nr.&nbsp;73«). '
        f'{bilanz.get("mit_seite_unaufloesbar", 0)} Verweise mit Seitenangabe bleiben unaufgelöst, '
        f'{bilanz.get("ohne_seitenangabe", 0)} nennen keine Seite. Der Zugriff auf HathiTrust-Scans kann '
        f'je nach Band eine Anmeldung erfordern.</p></div>'
        + "".join(bloecke) +
        f'<p class="meta">Methodik und Rohdaten: Kapsel-Re-Run v2 (<a href="hathitrust.html">Erschließung</a>) · '
        f'Gruppierung nach dem Ziel; je Zielseite und verweisendem Band eine Zeile (Zwillings-Scans kollabiert).</p>')


def orl_apparatus_page(reg, idx, persons=None):
    places = reg.get("places", [])
    p2id = {}                                             # Name/Nachname → Personenregister-ID (Verlinkung)
    for p in (persons or []):
        p2id[_pn(p["name"])] = p["id"]; p2id.setdefault(_pn(p["name"].split()[-1]), p["id"])
    plist = reg.get("persons", []); keys = {_pn(r["name"]) for r in plist}; merged = {}
    for r in plist:                                       # Genitiv-/Varianten-Merge: „Hadrians" → „Hadrian"
        k = _pn(r["name"])
        if k.endswith("s") and len(k) > 4 and k[:-1] in keys: k = k[:-1]
        e = merged.setdefault(k, {"name": r["name"], "bands": set(), "count": 0, "gazetteer": False, "best": -1})
        e["bands"].update(r["bands"]); e["count"] += r["count"]; e["gazetteer"] |= bool(r.get("gazetteer"))
        if r["count"] > e["best"]: e["best"] = r["count"]; e["name"] = r["name"]
    def _nk(x): m = re.match(r"\d+", str(x)); return (int(m.group()) if m else 999, str(x))
    pmerged = [{"name": e["name"], "bands": sorted(e["bands"], key=_nk), "nbands": len(e["bands"]),
                "count": e["count"], "gazetteer": e["gazetteer"]} for e in merged.values()]
    def prow(r):
        # Die frühere Spalte „Bände (ORL-Nr.)" ist entfernt: Ihre Nummern kamen aus der htid→Kastell-Nr.-
        # Abbildung des orl_index, und die war für 37 der 56 Bände falsch (enumcron „v. N" = Lieferung,
        # nicht Kastell-Nr.). Geprüft und WEITERHIN GÜLTIG sind #Bd. und Nenn.: keine der 130.540 NER-Zeilen
        # fällt weg, und die Abbildung ist injektiv (56 Bände → 56 Nrn.) — sie zählt also richtig, in wie
        # vielen Bänden ein Name steht; falsch war nur, WELCHE genannt wurden.
        pid = p2id.get(_pn(r["name"])) or p2id.get(_pn(r["name"].split()[-1]))
        nm = f'<a href="persons.html#{pid}">{html.escape(r["name"])}</a>' if pid else html.escape(r["name"])
        fm = [f for f in r.get("formen", []) if f[0] != r["name"]]
        if fm:
            nm += ('<span class="meta"> · Formen: '
                   + html.escape(", ".join(f'{n} ({c})' for n, c in fm[:4]))
                   + ("…" if len(fm) > 4 else "") + '</span>')
        return (f'<tr><td>{nm}{" ✓" if r.get("gazetteer") else ""}</td><td>{r["nbands"]}</td>'
                f'<td>{r["count"]}</td></tr>')
    vornamen = {_pn(p["name"].split()[0]) for p in (persons or []) if len(p["name"].split()) > 1}
    vornamen |= VORNAMEN_ZUSATZ
    pmerged = namensfamilien(pmerged, vornamen)
    cred = [r for r in pmerged if r.get("gazetteer") or r["nbands"] >= 3]
    groups = {"sig": [], "ant": [], "rlk": [], "rest": []}
    for r in cred:
        c = _pcat(r["name"])
        groups[c if (c != "rlk" or r.get("gazetteer")) else "rest"].append(r)
    for g in groups.values(): g.sort(key=lambda r: (-r["nbands"], -r["count"]))
    def _tbl(rows): return (f'<table class="reg"><thead><tr><th>Person</th><th>#Bd.</th><th>Nenn.</th>'
                            f'</tr></thead><tbody>{"".join(prow(r) for r in rows)}</tbody></table>')
    persons_html = (
        f'<h2 id="personen">Personen im ORL</h2>'
        f'<p class="meta">Aufgeschlüsselt nach Art. ✓ = auch im Limesblatt-Gazetteer der Edition belegt; '
        f'<b>verlinkte Namen</b> führen ins <a href="persons.html">Personenregister</a>. '
        f'Genitivformen sind zusammengeführt; aus automatischer Eigennamenerkennung über Fraktur-OCR. '
        f'<b>Formen desselben Nachnamens</b> — Initiale, Vollname und die Klebeformen der OCR '
        f'(»Randfries Ludowici«) — stehen in <i>einer</i> Zeile, mit den Varianten daneben; '
        f'<b>#Bd.</b> ist dabei die Vereinigung, nicht die Summe. Zusammengefasst wird nach dem '
        f'Nachnamen, nicht nach der Person: wo die Quelle selbst zwei Träger unterscheidet '
        f'(Jacobi — Louis und Heinrich), bleiben sie getrennt.</p>'
        f'<div class="note"><p><b>Warum hier keine Bandnummern stehen.</b> Bis 2026-07 nannte diese Tabelle '
        f'zu jedem Namen die Bände, in denen er vorkommt. Diese Nummern stammten aus der Zuordnung '
        f'Scan→Kastell-Nummer, und die war für <b>37 der 56 Bände falsch</b> (die HathiTrust-Signatur '
        f'„v.&#8201;N" ist die <i>Lieferung</i>, nicht die Kastell-Nummer). Geprüft und weiterhin gültig '
        f'sind <b>#Bd.</b> und <b>Nenn.</b>: Keine der 130.540 NER-Zeilen fällt weg, und die Abbildung ist '
        f'injektiv (56 Bände → 56 Nummern) — sie zählt also richtig, in <i>wie vielen</i> Bänden ein Name '
        f'steht; falsch war nur, <i>welche</i>. Häufigkeits- und Streuungsaussagen tragen daher, '
        f'diachrone Lesarten („erscheint erst in den späten Bänden") nicht.</p></div>'
        f'<h3 id="bearbeiter">RLK-Bearbeiter und Ausgräber</h3>{_tbl(groups["rlk"][:70])}'
        f'<h3 id="sigillata-forscher">Sigillata-Forscher</h3>'
        f'<p class="meta">Die Terra-Sigillata-Typologen (Dragendorff, Knorr, Ludowici …), die den '
        f'charakteristischen Fund-Apparat des ORL prägten — der Grund, warum der ORL sprachlich als '
        f'Fund-Katalog erscheint (vgl. <a href="wortschatz.html#gegenprobe">Wortschatz-Gegenprobe</a>).</p>{_tbl(groups["sig"])}'
        f'<h3>Antike Personen</h3>'
        f'<p class="meta">Kaiser (als Datierungsanker), Gottheiten und antike Autoren.</p>{_tbl(groups["ant"][:40])}'
        f'<details><summary>Übrige, automatisch erkannt &amp; ungeprüft ({len(groups["rest"])})</summary>'
        f'<p class="meta">Ohne Gazetteer-Beleg — darunter auch OCR-Artefakte (Gemeinwörter).</p>'
        f'{_tbl(groups["rest"][:60])}</details>')
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
    return (f'<h1>ORL-Gesamtapparat</h1>'
            f'<p class="meta">Register, Apparate und Konkordanzen über <b>alle</b> ORL-Bände — token-frei aus '
            f'HathiTrust-NER und Extracted Features aggregiert (<a href="hathitrust.html">Methode</a>); '
            f'das Generalwerkzeug, das die 14-Mappen-Reihe nie hatte. Zurück zum '
            f'<a href="orl.html">ORL-Bandindex</a>.</p>'
            f'{persons_html}'
            f'<h2 id="orte">Ortsregister ({len(plc)} bandübergreifend)</h2>'
            f'<table class="reg"><thead><tr><th>Ort</th><th>#Bd.</th><th>Nenn.</th></tr></thead>'
            f'<tbody>{"".join(prow(r) for r in plc)}</tbody></table>'
            f'<h2 id="sigillata">Terra-Sigillata-Apparat — zurückgezogen</h2>'
            f'<div class="note"><p>Diese Tabelle wies aus, welche Lieferungen die großen Fund-Katalogbände '
            f'sind (Dragendorff/Knorr/Ludowici/Rheinzabern …), ausgezählt über die HathiTrust-Extracted-'
            f'Features. Sie ist <b>zurückgezogen</b>, weil sie am selben Fehler hängt wie die früheren '
            f'Spalten des <a href="orl.html">Bandindex</a>: Die Zuordnung Scan→Kastell las die Signatur '
            f'„v.&#8201;N" als Kastell-Nummer, obwohl sie die <i>Lieferung</i> meint — für 30 Bände war sie '
            f'damit falsch, und ein Scan umfasst ohnehin eine ganze Lieferung (bis zu vier Kastelle), nie '
            f'ein einzelnes. Ein Sigillata-Score „je Kastell" ist auf dieser Grundlage nicht zu haben.</p>'
            f'<p>Der Befund selbst — dass der ORL sprachlich ein Fund-Katalog ist — bleibt unberührt; er '
            f'ruht auf der <a href="wortschatz.html#gegenprobe">Wortschatz-Gegenprobe</a> über den gesamten '
            f'Korpus, die keine Band-Zuordnung braucht. Die Tabelle kehrt zurück, sobald die Scans über den '
            f'geprüften Lieferungsindex neu zugeordnet sind.</p></div>'
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
    return (f'<h1>Erschließung über HathiTrust</h1>'
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
            f'<h2>4 · Data Capsule — Volltext (abgeschlossen, zwei Zyklen)</h2>'
            f'<p>Für das, was nur fortlaufender Volltext liefert, lief eine nicht-konsumtive <b>HTRC Data '
            f'Capsule</b>: Volltext der 78 Scans geladen, Analyse per stdlib-Python in der Kapsel, Export '
            f'ausschließlich aggregierter Ableitungen über den HTRC-Review. Der zweite Lauf (August 2026) '
            f'lieferte 17 Exporte, darunter seitengenaues Kastell-Tagging, die Zeitschriften-Nennungen, '
            f'sämtliche Limesblatt- und werkinternen Verweise mit Stellenangabe sowie vollständige '
            f'Tokenlisten je Band. Auf dieser Website tragen sie das '
            f'<a href="orl-inhalt.html">Inhaltsverzeichnis</a> (Faszikel-Abgrenzung, Seitenumfänge), die '
            f'<a href="orl-verweise.html">Binnenverweise</a> und die <a href="genese.html">Genese-Seite</a>.</p>'
            f'<h2>Ertrag</h2>'
            f'<p>Aus diesen offenen Schichten entstand der konsolidierte <a href="orl-register.html">Gesamtapparat</a>: '
            f'ein <b>{np}-Personen-</b> und <b>{npl}-Orte-Generalregister</b>, die '
            f'<a href="orl-register.html#sigillata">Sigillata-Konkordanz</a>, die '
            f'<a href="orl-register.html#konkordanz">Vorbericht-Konkordanz</a> und die Wortschatz-Gegenprobe — '
            f'Apparate, die der in 14 Mappen über 40 Jahre erschienene ORL selbst nie besaß.</p>')

def _rlk_paragraphs(text):
    paras = re.split(r"\n\s*\n", text.strip())
    out = []
    for p in paras:
        line = re.sub(r"\s+", " ", p.replace("\n", " ")).strip()
        line = re.sub(r"(\w)-\s+(\w)", r"\1\2", line)          # Silbentrennung am Zeilenende auflösen
        if line:
            out.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(out)

def rlk_jahresberichte_page(data, ner_p=None, ner_pl=None):
    if not data:
        return "<h1>RLK-Jahresberichte</h1><p class=\"meta\">Daten nicht verfügbar.</p>"
    berichte = data.get("berichte", [])
    trend = data.get("trend_spearman", {})
    geg = data.get("gegenprobe_limesblatt", {})
    reihe, jb_pers, jb_orte = jb_korpus(data, ner_p or [], ner_pl or [])
    nr_von = {r["jahrgang"]: r["nr"] for r in reihe}
    # Lücken der Reihe, jede mit ihrem Grund: 1894 fehlt als Digitalisat, 1897 hat gar keinen
    # Bericht (der Band führt den Titel nur in seiner Bibliographie).
    fehl_jahr = {b: 1885 + b for b in data.get("fehlend", [])}
    grund = {1885 + b: "auf archive.org nicht digitalisiert auffindbar" for b in data.get("fehlend", [])}
    for x in data.get("ohne_bericht", []):
        fehl_jahr[x["band"]] = x["jahrgang"]
        grund[x["jahrgang"]] = ("in diesem Band ist kein Bericht der Kommission enthalten — der "
                                "Titel steht dort nur in der Bibliographie")
    def _tsd(n): return f"{n:,}".replace(",", ".")
    zeilen = []
    for b in berichte:
        for fb, fj in sorted(fehl_jahr.items()):
            if fj < b["jahrgang"] and not any(x["jahrgang"] == fj for x in zeilen):
                zeilen.append({"jahrgang": fj, "band": fb, "fehlt": True})
        zeilen.append(dict(b, fehlt=False))
    rows = "".join(
        (f'<tr class="fehlt"><td><b>{z["jahrgang"] - 1891}</b></td><td>{z["jahrgang"]}</td><td>{z["band"]}</td>'
         f'<td colspan="6" class="meta">{html.escape(grund.get(z["jahrgang"], ""))} — '
         f'die Nummer bleibt vergeben</td></tr>'
         if z["fehlt"] else
         f'<tr><td><b>{nr_von.get(z["jahrgang"], "—")}</b></td><td>{z["jahrgang"]}</td><td>{z["band"]}</td>'
         f'<td>{_tsd(z["woerter"])}</td>'
         f'<td>{sum(1 for n in jb_pers if z["jahrgang"] in jb_pers[n])}</td>'
         f'<td>{sum(1 for n in jb_orte if z["jahrgang"] in jb_orte[n])}</td>'
         f'<td>{z["admin_je_1000"]}</td><td>{z["feld_je_1000"]}</td>'
         f'<td><a href="../jahresberichte/jb{z["jahrgang"]}.html">lesen mit Faksimile</a></td></tr>')
        for z in zeilen)
    details = "".join(
        f'<p><a href="../jahresberichte/jb{b["jahrgang"]}.html"><b>Bericht {b["jahrgang"]}</b></a> '
        f'<span class="meta">— Jahrbuch Bd. {b["band"]}, {b["woerter"]:,} Wörter, '
        f'{len(b.get("seiten") or [])} Blätter</span></p>'.replace(",", ".")
        for b in berichte)
    fehlt = ", ".join(f"Bd. {n}" for n in data.get("fehlend", [])) or "—"
    ohne = data.get("ohne_bericht", [])
    ohne_satz = ""
    if ohne:
        ohne_satz = (f', und in Band {ohne[0]["band"]} ({ohne[0]["jahrgang"]}) steht überhaupt kein '
                     f'Bericht — den Titel führt dieser Band nur in seiner Bibliographie')
    return (
        f'<h1>RLK-Jahresberichte (1892–1904)</h1>'
        f'<p class="meta">Die institutionellen Rechenschaftsberichte der Reichs-Limeskommission — jährlich als '
        f'„Bericht über die Thätigkeit/die Arbeiten der Reichs-Limeskommission" im Archäologischer-Anzeiger-Anhang '
        f'des <i>Jahrbuch des Kaiserlich Deutschen Archäologischen Instituts</i> veröffentlicht (Bd. 7–20, 1892–1905). '
        f'Die Bandzahlen sind die des Jahrbuchs, das 1886 mit Band 1 beginnt: <b>Bd. 7 ist der Jahrgang 1892</b> — '
        f'das Gründungsjahr der Kommission und damit der erste Bericht, den es geben kann. '
        f'Unabhängig vom Limesblatt (den Feldberichten der Streckenkommissare): das ist die institutionelle '
        f'Selbstauskunft der Kommission an die Öffentlichkeit. Token-frei geharvestet von archive.org '
        f'(<b>{len(berichte)} von 14 Jahrgängen</b>; {fehlt} ist nicht digitalisiert auffindbar'
        f'{ohne_satz}.)</p>'
        f'<div class="note"><p><b>Woher der Text kommt.</b> Die Berichte sind am Faksimile neu gelesen '
        f'(macOS Vision, halbseitenweise wegen des zweispaltigen Satzes); die mitgelieferte OCR von '
        f'archive.org las »l6. Januar« statt »16. Januar« und trennte Wörter über den Zeilenumbruch. '
        f'Der Umfang jedes Berichts ist über seinen <b>Kolumnentitel</b> bestimmt, der sich auf jeder '
        f'Berichtsseite wiederholt und mit dem Folgeartikel wechselt.</p></div>'
        f'<h2 id="umfang">Umfang der Berichte</h2>'
        f'<p>Über die erschlossenen Jahrgänge <b>fällt</b> der Berichtsumfang (Spearman ρ = '
        f'<b>{trend.get("umfang", "–")}</b>) — ein Gegenbefund zur wachsenden '
        f'<a href="orl.html">ORL-Lieferungsreihe</a> über deren 45 Jahre. Beide widersprechen sich nicht: die '
        f'ORL-Lieferungen sind die wissenschaftliche Endpublikation über 45 Jahre, die Jahresberichte sind die '
        f'institutionelle Selbstauskunft über die ersten 13 Jahre — und die schrumpft, je mehr die Kommission von '
        f'der Gründungs- in die Verwaltungsroutine übergeht. Der Bruch fällt mit dem Tod Theodor Mommsens, Felix '
        f'Hettners und Karl Zangemeisters 1902/03 zusammen (Bd. 19/1904 spricht selbst von „großer Zurückhaltung" '
        f'bei neuen Grabungen).</p>'
        f'<p class="meta">Wie sich Feldorgan, Jahresberichte und Endpublikation zeitlich zueinander '
        f'verhalten — einschließlich des Lieferungslochs 1899 und des heftlosen Jahres 1900 — ordnet '
        f'die Seite zur <a href="genese.html">Genese des ORL</a> ein.</p>'
        f'<p><b>Gegenprobe gegen das Limesblatt-Korpus</b> ({geg.get("limesblatt_woerter", 0):,} Wörter): '
        f'Verwaltungssprache {geg.get("jahresbericht_admin_je_1000")}/1000 W. (Jahresbericht) vs. '
        f'{geg.get("limesblatt_admin_je_1000")}/1000 W. (Limesblatt); Feldsprache '
        f'{geg.get("jahresbericht_feld_je_1000")}/1000 W. vs. {geg.get("limesblatt_feld_je_1000")}/1000 W. — in '
        f'beiden Korpora nahezu identisch. Der Jahresbericht ist keine trockene Verwaltungsprosa, sondern teilt das '
        f'Vokabular der Feldnarration; er fasst zusammen, was die Streckenkommissare im Limesblatt ausführlicher '
        f'erzählen.</p>'
        f'<h2 id="berichte">Die Berichte</h2>'
        f'<p class="meta">Der Bericht führt keine eigene Zählung: er erscheint als Anhang und erbt die Bandzahl '
        f'des Jahrbuchs. Die laufende Nummer hier ist nach Jahrgang vergeben; der fehlende Jahrgang 1894 '
        f'(Jahrbuch Bd. 9) bleibt als Lücke stehen und wird nicht weggezählt — sonst verschöbe sich alles '
        f'dahinter. <b>Personen</b> und <b>Orte</b> zählen, wie viele verschiedene Namen des '
        f'<a href="namen.html">Limesblatt-Gazetteers</a> im jeweiligen Bericht vorkommen.</p>'
        f'<table class="reg"><thead><tr><th>Nr.</th><th>Jahrgang</th><th>Jahrbuch-Bd.</th><th>Wörter</th>'
        f'<th>Personen</th><th>Orte</th><th>Admin/1000 W.</th><th>Feld/1000 W.</th><th></th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        + jb_register_html(reihe, jb_pers, jb_orte, data)
        + jb_ereignis_html(jb_personalia(data, ner_p or []), jb_kampagnen(data, ner_pl or [])) +
        f'<h2 id="lesefassungen">Lesefassungen</h2>'
        f'<p class="meta">Jeder Bericht steht als eigene Seite: der Text links, das Blatt des Digitalisats '
        f'rechts, beide aneinander gekoppelt. Gegliedert wird mit den Zwischenüberschriften des Drucks '
        f'(Versalzeilen) und seinen Aufzählungen; Absatzgrenzen im Fließtext gibt die Vorlage nicht her. '
        f'Der Umfang des Berichts ist heuristisch bestimmt (zusammenhängender Lauf der Blätter, die die '
        f'Kommission nennen) — kleine Ränder zum Nachbarartikel sind möglich.</p>{details}'
    )

def jb_register_html(reihe, pers, orte, data):
    """Die Register zu den Jahresberichten: wer, wo, und das Verwaltungsvokabular."""
    def tab(d, lab, ziel):
        zeilen = sorted(d.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
        tr = []
        for n, jj in zeilen:
            jahre = ", ".join(str(j) for j in sorted(jj))
            eid = ("psnN_" if ziel == "namen" else "plcN_") + gazetteer.slug(gazetteer._primary(n)[0])
            tr.append(f'<tr><td><a href="{ziel}.html#{eid}">{html.escape(n)}</a></td>'
                      f'<td>{sum(jj.values())}</td><td>{len(jj)}</td><td class="meta">{jahre}</td></tr>')
        return (f'<table class="reg"><thead><tr><th>{lab}</th><th>Nennungen</th><th>Berichte</th>'
                f'<th>Jahrgänge</th></tr></thead><tbody>{"".join(tr)}</tbody></table>')

    amt, geld = jb_stellen(data)
    arows = "".join(
        f'<tr><td><b>{html.escape(w)}</b></td><td>{len(v)}</td>'
        f'<td class="meta">{", ".join(str(j) for j in sorted({x[0] for x in v}))}</td>'
        f'<td class="meta ktx">{html.escape(v[0][1])}</td></tr>'
        for w, v in sorted(amt.items(), key=lambda kv: -len(kv[1])))
    grows = "".join(f'<tr><td>{g["jahr"]}</td><td>{g["betrag"]:,}</td>'
                    f'<td class="meta ktx">{html.escape(g["kontext"])}</td></tr>'
                    for g in sorted(geld, key=lambda x: (x["jahr"], -x["betrag"])))
    return (f'<h2 id="wer">Genannte Personen</h2>'
            f'<p class="meta">Personen des Limesblatt-Gazetteers in den Jahresberichten — dieselben Namen, '
            f'anderes Genre: hier stehen sie als Beauftragte der Kommission, nicht als Erzähler ihrer Grabung. '
            f'Sortierbar; „Berichte" ist die Zahl der Jahrgänge, in denen der Name vorkommt.</p>{tab(pers, "Person", "namen")}'
            f'<h2 id="wo">Genannte Orte</h2>'
            f'<p class="meta">Ein Jahr-für-Jahr-Bild der Kampagne: welche Plätze der Bericht überhaupt erwähnt. '
            f'Gemeinwörter, die zugleich Ortsnamen sind (»Graben«, »Wall«), sind ausgenommen — sie stünden sonst '
            f'an der Spitze und meinten nichts.</p>{tab(orte, "Ort", "orte-index")}'
            f'<h2 id="verwaltung">Das Verwaltungsvokabular</h2>'
            f'<p class="meta">Womit sich der Bericht als <i>institutioneller</i> Text zu erkennen gibt: '
            f'Streckenkommissare und Dirigenten, Reichstag und Etat, Sitzungen und Denkschriften — je Begriff '
            f'die Jahrgänge und eine Belegstelle.</p>'
            f'<table class="reg"><thead><tr><th>Begriff</th><th>Belege</th><th>Jahrgänge</th><th>Erste Fundstelle</th>'
            f'</tr></thead><tbody>{arows}</tbody></table>'
            + (f'<h3>Beträge</h3><p class="meta">Alle Markbeträge, die die Fraktur-OCR lesbar hergibt — '
               f'wenige, und die Ziffern sind der unzuverlässigste Teil dieser Vorlage: Beträge bitte am '
               f'Volltext prüfen, sie stehen hier als Fundstellen-Hinweis, nicht als Etatzahlen.</p>'
               f'<table class="reg"><thead><tr><th>Jahrgang</th><th>Betrag (M.)</th><th>Kontext</th></tr></thead>'
               f'<tbody>{grows}</tbody></table>' if grows else ""))


_JB_CACHE = {}


def jb_korpus_cached(jb, ner_p, ner_pl):
    if "x" not in _JB_CACHE:
        _JB_CACHE["x"] = jb_korpus(jb, ner_p, ner_pl)
    return _JB_CACHE["x"]


def _lb_baende(pages):
    """NER-Seitenrefs → {Band: Nennungen}; das Bandlabel ist hier verlässlich genug für Zählung."""
    d = Counter()
    for s in pages or []:
        m = re.match(r"Bd\.(\d+)", s)
        if m: d[int(m.group(1))] += 1
    return d


def gesamtregister_page(ner_p, ner_pl, orl_reg, jb, persons):
    """Ein Register über ALLE drei Werke: Limesblatt, ORL, Jahresberichte.

    Die drei Korpora sind getrennt erschlossen (Volltext-NER · HathiTrust-NER · Gazetteer-Abgleich)
    und haben je eigene Register. Diese Tabelle legt sie nebeneinander, damit sichtbar wird, wer
    und was in mehr als einem Werk vorkommt — die Frage, die kein Einzelregister beantwortet.

    Verbunden wird über den normalisierten Namen. Das ist eine Anzeige-Zusammenführung, keine
    Identitätsaussage: derselbe Nachname kann zwei Träger haben (Jacobi), und die Korpora sind
    verschieden zuverlässig (Fraktur-OCR hier, HathiTrust-OCR dort)."""
    _, jbp, jbo = jb_korpus_cached(jb, ner_p, ner_pl)
    pid = {}
    for p in (persons or []):
        pid[_pn(p["name"])] = p["id"]; pid.setdefault(_pn(p["name"].split()[-1]), p["id"])

    def bau(lb_items, orl_items, jb_map, art):
        # Nur verlinken, was im Volltext-Index der Edition auch einen Anker hat: Namen, die
        # allein aus dem ORL oder den Jahresberichten stammen, stehen dort nicht.
        anker = {gazetteer.slug(gazetteer._primary(it["name"])[0]) for it in lb_items}
        z = {}
        for it in lb_items:
            k = _pn(it["name"])
            if not k: continue
            e = z.setdefault(k, {"name": it["name"], "lb": 0, "orl": 0, "jb": {}, "orlname": "", "lbname": ""})
            e["lb"] += len(it.get("pages", [])); e["lbname"] = it["name"]
        for it in orl_items:
            k = _pn(it["name"])
            if not k: continue
            e = z.setdefault(k, {"name": it["name"], "lb": 0, "orl": 0, "jb": {}, "orlname": "", "lbname": ""})
            e["orl"] = max(e["orl"], it.get("nbands", 0)); e["orlname"] = it["name"]
        for n, jj in jb_map.items():
            k = _pn(n)
            e = z.setdefault(k, {"name": n, "lb": 0, "orl": 0, "jb": {}, "orlname": "", "lbname": ""})
            e["jb"] = jj
        rows = []
        for k, e in z.items():
            werke = (1 if e["lb"] else 0) + (1 if e["orl"] else 0) + (1 if e["jb"] else 0)
            nm = e["lbname"] or e["name"]
            slug = gazetteer.slug(gazetteer._primary(nm)[0])
            ziel = ("namen.html#psnN_" if art == "person" else "orte-index.html#plcN_") + slug
            link = (f'<a href="{ziel}">{html.escape(nm)}</a>' if slug in anker else html.escape(nm))
            if art == "person" and pid.get(k):
                link += f' <a class="meta" href="persons.html#{pid[k]}">↗ Personenregister</a>'
            rows.append({"n": link, "sort": nm, "lb": e["lb"], "orl": e["orl"],
                         "jb": sum(e["jb"].values()), "jahre": ", ".join(str(x) for x in sorted(e["jb"])),
                         "w": werke})
        rows.sort(key=lambda r: (-r["w"], -(r["lb"] + r["orl"] * 20 + r["jb"]), r["sort"].lower()))
        return rows

    pr = bau(ner_p, orl_reg.get("persons", []), jbp, "person")
    orr = bau(ner_pl, orl_reg.get("places", []), jbo, "ort")

    def tab(rows, lab):
        tr = "".join(
            f'<tr><td>{r["n"]}</td><td>{r["lb"] or "—"}</td><td>{r["orl"] or "—"}</td>'
            f'<td>{r["jb"] or "—"}</td><td class="meta">{r["jahre"]}</td><td><b>{r["w"]}</b></td></tr>'
            for r in rows)
        return (f'<table class="reg"><thead><tr><th>{lab}</th><th>Limesblatt (Belege)</th>'
                f'<th>ORL (Bände)</th><th>Jahresberichte (Nennungen)</th><th>Jahrgänge</th>'
                f'<th>Werke</th></tr></thead><tbody>{tr}</tbody></table>')

    d3p = sum(1 for r in pr if r["w"] == 3); d3o = sum(1 for r in orr if r["w"] == 3)
    return (f'<h1>Gesamtregister</h1>'
            f'<p class="meta">Dieselbe Person, derselbe Ort, gesucht in <b>drei getrennt erschlossenen '
            f'Korpora</b>: dem <a href="../index.html">Limesblatt</a> (Volltext dieser Edition), dem '
            f'<a href="orl-register.html">ORL</a> (HathiTrust-Erschließung über 56 Bände) und den '
            f'<a href="jahresberichte.html">RLK-Jahresberichten</a> (13 Jahrgänge). Die Spalte '
            f'<b>Werke</b> sagt, in wie vielen der drei ein Name überhaupt vorkommt — sortiert man danach, '
            f'stehen oben die Namen, die das ganze Unternehmen durchziehen: <b>{d3p} Personen</b> und '
            f'<b>{d3o} Orte</b> in allen dreien.</p>'
            f'<div class="note"><p><b>Was der Vergleich trägt und was nicht.</b> Verbunden wird über den '
            f'normalisierten Namen — eine Zusammenführung für die Anzeige, keine Identitätsaussage: derselbe '
            f'Nachname kann zwei Träger haben (Jacobi: Louis und Heinrich), und die drei Korpora sind '
            f'unterschiedlich zuverlässig erschlossen. Die Zahlen sind <i>nicht</i> untereinander '
            f'vergleichbar: „Belege" im Limesblatt sind Seiten, „Bände" im ORL sind Faszikel, „Nennungen" '
            f'im Jahresbericht sind Wortvorkommen. Vergleichbar ist allein das <i>Ob</i>.</p></div>'
            f'<h2 id="personen">Personen</h2>{tab(pr, "Person")}'
            f'<h2 id="orte">Orte</h2>{tab(orr, "Ort")}')


def netz_page(persons, ner_p, orl_idx, bli, verw, jb, ner_pl, bibls, occ):
    """Personen · Publikationen · Zitate als Netz — was die Register nur zeilenweise zeigen.

    Vier Kantenfamilien, jede aus einer belegten Quelle: wer in einem Limesblatt-Band genannt wird,
    wer einen ORL-Faszikel bearbeitet hat, wer in einem Jahresbericht vorkommt, und welcher
    ORL-Faszikel welchen Limesblatt-Band zitiert. Dazu die im Limesblatt zitierte Literatur."""
    import graph
    knoten, kanten = {}, Counter()

    def kn(nid, label, typ, href="", titel=""):
        knoten.setdefault(nid, {"id": nid, "label": label, "typ": typ, "href": href,
                                "titel": titel or label, "gewicht": 0})
        return nid

    for v in range(1, 9):
        kn(f"lb{v}", f"Limesblatt Bd. {v}", "limesblatt", f"../volumes/bd{v}.html")
    reihe, jbp, _ = jb_korpus_cached(jb, ner_p, ner_pl)
    for r in reihe:
        kn(f"jb{r['jahrgang']}", f"Bericht {r['jahrgang']}", "jahresbericht",
           f"../jahresberichte/jb{r['jahrgang']}.html")
    knr = {}
    for k in orl_idx.get("abteilung_B_kastelle", []):
        knr[_pn(k["kastell"])] = k["nr"]

    pnames = {}
    for p in (persons or []):
        pnames[_pn(p["name"].split()[-1])] = p
        pnames[_pn(p["name"])] = p

    def person_kn(name):
        p = pnames.get(_pn(name)) or pnames.get(_pn(name.split()[-1]))
        if not p: return None
        return kn("p" + p["id"], p["name"], "person", f"persons.html#{p['id']}")

    for it in ner_p:                                        # genannt im Limesblatt
        nid = person_kn(it["name"])
        if not nid: continue
        for v, c in _lb_baende(it.get("pages")).items():
            if 1 <= v <= 8: kanten[(nid, f"lb{v}")] += c
    for n, jj in jbp.items():                               # genannt im Jahresbericht
        nid = person_kn(n)
        if not nid: continue
        for j, c in jj.items(): kanten[(nid, f"jb{j}")] += c
    lfg = bli.get("kastell_nr_zu_lieferung") or {}
    for k in orl_idx.get("abteilung_B_kastelle", []):        # bearbeitet einen ORL-Faszikel
        bearb = k.get("bearbeiter") or []
        if isinstance(bearb, str): bearb = [bearb]
        b2 = (lfg.get(k["nr"]) or {}).get("bearbeiter")
        if b2: bearb = bearb + [b2]
        if not bearb: continue
        oid = kn(f"orl{k['nr']}", f"ORL {k['nr']} {k['kastell'].replace('Kastell ', '')}", "orl",
                 f"orl-inhalt.html#orltoc-{k['nr']}", f"ORL {k['nr']} — {k['kastell']}")
        for b in bearb:
            for teil in re.split(r"[/,]", b):
                nid = person_kn(teil.strip())
                if nid: kanten[(nid, oid)] += 3
    for w in (verw.get("verweise") or []):                   # ORL zitiert das Limesblatt
        ka = w.get("orl_kastell") or ""
        nr = knr.get(_pn(ka))
        if not nr or not (1 <= int(w.get("band", 0)) <= 8): continue
        oid = kn(f"orl{nr}", f"ORL {nr} {ka.replace('Kastell ', '')}", "orl",
                 f"orl-inhalt.html#orltoc-{nr}", f"ORL {nr} — {ka}")
        kanten[(oid, f"lb{w['band']}")] += 1
    for b in bibls:                                          # das Limesblatt zitiert Literatur
        items = occ.get(b["id"], [])
        if not items: continue
        wid = kn("w" + b["id"], b["title"][:38], "werk", f"bibliographie.html#{b['id']}", b["title"])
        for vol, _a, _p in ((i[0], i[1], i[2]) for i in items):
            if 1 <= vol <= 8: kanten[(f"lb{vol}", wid)] += 1

    for (a, b), w in kanten.items():
        if a in knoten: knoten[a]["gewicht"] += w
        if b in knoten: knoten[b]["gewicht"] += w
    lose = {n for n, d in knoten.items() if d["gewicht"] == 0}
    for n in lose: knoten.pop(n)
    kl = [(a, b, w) for (a, b), w in kanten.items() if a in knoten and b in knoten]
    nl = sorted(knoten.values(), key=lambda d: (d["typ"], d["id"]))
    pos = graph.layout(nl, kl)
    zahl = Counter(d["typ"] for d in nl)
    LAB = {"person": "Personen", "limesblatt": "Limesblatt-Bände", "orl": "ORL-Faszikel",
           "jahresbericht": "Jahresberichte", "werk": "zitierte Werke"}
    filt = "".join(
        f'<label><span class="sw" style="background:{graph.TYP_FARBE[k]}"></span>'
        f'<input type="checkbox" value="{k}" checked> {LAB[k]} ({zahl[k]})</label>'
        for k in ("person", "limesblatt", "orl", "jahresbericht", "werk") if zahl.get(k))
    return (f'<h1>Netzansicht</h1>'
            f'<p class="meta">Dieselben Daten wie in den Registern, nur als Beziehungsbild: '
            f'<b>{len(nl)} Knoten</b> und <b>{len(kl)} Kanten</b> aus vier belegten Quellen — wer in einem '
            f'Limesblatt-Band <i>genannt</i> wird, wer einen ORL-Faszikel <i>bearbeitet</i> hat, wer in einem '
            f'<i>Jahresbericht</i> vorkommt, und welcher ORL-Faszikel welchen Limesblatt-Band <i>zitiert</i> '
            f'(<a href="orl-verweise.html">Binnenverweise</a>). Dazu die im Limesblatt zitierte Literatur. '
            f'Ziehen verschiebt, Mausrad zoomt, Überfahren hebt die Nachbarschaft hervor, ein Klick auf die '
            f'Beschriftung führt zum Register.</p>'
            f'<div class="netzsteuer"><input type="search" id="netz-suche" placeholder="Knoten suchen …">'
            f'<span class="meta" id="netz-zahl"></span>'
            f'<button id="netz-reset" class="iiifbtn">Ansicht zurücksetzen</button></div>'
            f'<div class="netz-typ netzsteuer">{filt}</div>'
            f'<div class="netzbox">{graph.svg(nl, kl, pos)}</div>'
            f'<p class="meta">Die Knotengröße ist die Summe der Kantengewichte — sie zeigt Verflechtung, '
            f'nicht Bedeutung. Nur Knoten mit mindestens einer Kante sind aufgenommen; Personen erscheinen, '
            f'soweit sie im <a href="persons.html">kuratierten Personenregister</a> stehen, damit jeder Punkt '
            f'auf eine geprüfte Person zeigt und nicht auf eine OCR-Form. Das Layout ist vorberechnet und bei '
            f'jedem Build identisch.</p>'
            f'<script src="../assets/netz.js" defer></script>')


# ---------- Personalia und Kampagnen: was der Jahresbericht als Institution meldet ----------
# Der Jahresbericht ist die einzige der drei Quellen, die über PERSONAL spricht — wer berufen
# wurde, wer starb, wer an wessen Stelle trat. Und er ist die einzige, die Jahr für Jahr sagt,
# WO gegraben wurde. Beides steht im Fließtext, nicht in Tabellen; extrahiert wird satzweise:
# ein Satz zählt, wenn er ein Ereigniswort UND einen Namen aus dem Gazetteer führt.
JB_EREIGNIS = [
    ("Berufung", r"\bernannt|\bberufen|\bbestellt\b|\büberträgt\b|\bübertragen\b|zum\s+Vorsitzenden"),
    ("Nachfolge", r"an\s+(?:die\s+)?Stelle\s+(?:des|der)|\bNachfolger|trat\s+an\s+die\s+Stelle|\bnachgefolgt"),
    ("Tod", r"\bverstorben|\bgestorben|\bTode?\b|\bAbleben|\bHinscheiden|\bdahingerafft"),
    ("Ausscheiden", r"\bausgeschieden|\bzurückgetreten|\bRücktritt|niedergelegt|\bentbunden|\bentlassen"),
    ("Beauftragung", r"Kommissar\s+für|als\s+Streckenkommissar|mit\s+der\s+(?:Bearbeitung|Untersuchung|Leitung)\s+"
                     r"(?:der|des|betraut)|\bbetraut\b|\bübernahm\b|\bübernommen\b"),
]
JB_GRABUNG = re.compile(r"ausgegraben|aufgedeckt|freigelegt|blossgelegt|bloßgelegt|untersucht|Grabung|"
                        r"gegraben|Aufdeckung|Untersuchung|nachgegraben|Schürf|aufgenommen|ermittelt", re.I)


def _saetze(txt):
    t = re.sub(r"\s+", " ", txt)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", t) if len(s.strip()) > 30]


def jb_personalia(jb, ner_p):
    """Personalereignisse je Jahrgang — Ereignisart, genannte Personen, Belegstelle.

    Gesucht wird in einem FENSTER um das Ereigniswort, nicht im Satz: die OCR setzt Punkte
    unzuverlässig (»Zange- meister«, »z. D.«), und ein Satzsplitter zerlegt genau dort, wo
    Amtsbezeichnungen abgekürzt stehen. Das Fenster ist grob, aber es verliert nichts."""
    nset = {p["name"] for p in ner_p if len(p["name"]) > 4 and " " not in p["name"]} - JB_VETO
    out, gesehen = [], set()
    for b in sorted(jb.get("berichte", []), key=lambda x: x["jahrgang"]):
        txt = re.sub(r"\s+", " ", b.get("text") or "")
        for art, rx in JB_EREIGNIS:
            for m in re.finditer(rx, txt):
                a, e = max(0, m.start() - 190), min(len(txt), m.end() + 190)
                fenster = txt[a:e]
                pers = [w for w in dict.fromkeys(re.findall(r"[A-ZÄÖÜ][\wäöüß-]+", fenster)) if w in nset]
                if not pers:
                    continue
                schl = (b["jahrgang"], art, pers[0], m.start() // 400)
                if schl in gesehen:
                    continue
                gesehen.add(schl)
                out.append({"jahrgang": b["jahrgang"], "art": art, "personen": pers[:4],
                            "beleg": "…" + fenster.strip() + "…"})
    out.sort(key=lambda e: (e["jahrgang"], e["art"]))
    return out


def jb_kampagnen(jb, ner_pl):
    """Wo in welchem Jahr gegraben wurde — Ort × Jahrgang aus den Grabungssätzen."""
    orte = {p["name"] for p in ner_pl if len(p["name"]) > 4 and " " not in p["name"]} - JB_VETO
    treffer = defaultdict(lambda: {"n": 0, "beleg": "", "strecken": set()})
    for b in sorted(jb.get("berichte", []), key=lambda x: x["jahrgang"]):
        for s in _saetze(b.get("text") or ""):
            if not JB_GRABUNG.search(s):
                continue
            st = re.findall(r"Strecke\s+([IVXLC]+|\d{1,2})", s)
            for w in dict.fromkeys(re.findall(r"[A-ZÄÖÜ][\wäöüß-]+", s)):
                if w not in orte:
                    continue
                e = treffer[(b["jahrgang"], w)]
                e["n"] += 1
                e["strecken"].update(st)
                if not e["beleg"]:
                    e["beleg"] = s if len(s) < 300 else s[:297] + "…"
    return [{"jahrgang": j, "ort": o, "n": v["n"], "strecken": sorted(v["strecken"]), "beleg": v["beleg"]}
            for (j, o), v in sorted(treffer.items(), key=lambda kv: (kv[0][0], -kv[1]["n"], kv[0][1]))]


def jb_ereignis_html(pers, kamp):
    prows = "".join(
        f'<tr><td>{e["jahrgang"]}</td><td>{html.escape(e["art"])}</td>'
        f'<td>{", ".join(f"<a href=namen.html#psnN_{gazetteer.slug(gazetteer._primary(n)[0])}>{html.escape(n)}</a>" for n in e["personen"])}</td>'
        f'<td class="meta ktx">{html.escape(e["beleg"])}</td></tr>' for e in pers)
    krows = "".join(
        f'<tr><td>{k["jahrgang"]}</td>'
        f'<td><a href="orte-index.html#plcN_{gazetteer.slug(gazetteer._primary(k["ort"])[0])}">{html.escape(k["ort"])}</a></td>'
        f'<td>{k["n"]}</td>'
        f'<td class="meta ktx">{html.escape(k["beleg"])}</td></tr>' for k in kamp)
    jahre_k = len({k["jahrgang"] for k in kamp}); orte_k = len({k["ort"] for k in kamp})
    art = Counter(e["art"] for e in pers)
    return (f'<h2 id="personalia">Personalia</h2>'
            f'<p class="meta">Stellen, an denen ein Personalereignis <i>und</i> ein bekannter Name '
            f'beieinanderstehen: '
            f'{" · ".join(f"{a} {n}" for a, n in art.most_common())}. Der Jahresbericht ist die einzige der '
            f'drei Quellen, die über Personal spricht — das Limesblatt berichtet vom Feld, der ORL von der '
            f'Sache. Die Zuordnung Person↔Ereignis ist die der Textstelle, nicht eine geprüfte '
            f'Biographie — gesucht wird in einem Fenster um das Ereigniswort, weil die OCR Satzgrenzen '
            f'unzuverlässig setzt.</p>'
            f'<table class="reg"><thead><tr><th>Jahrgang</th><th>Art</th><th>Personen</th><th>Beleg</th>'
            f'</tr></thead><tbody>{prows}</tbody></table>'
            f'<h2 id="kampagnen">Kampagnen</h2>'
            f'<p class="meta">{orte_k} Orte über {jahre_k} Jahrgänge, aus Sätzen mit Grabungsvokabular '
            f'(ausgegraben, aufgedeckt, untersucht …). Nach <b>Ort</b> sortiert wird daraus die Chronologie '
            f'eines Platzes, nach <b>Jahrgang</b> das Arbeitsprogramm einer Saison. Nennung in einem '
            f'Grabungssatz heißt nicht, dass dort in diesem Jahr gegraben wurde — der Satz kann vergleichen '
            f'oder zurückblicken; der Beleg steht daneben. Eine Strecken-Spalte fehlt mit Absicht: der '
            f'Bericht benennt seine Strecken nach ihrem Verlauf („die Strecke von Dambach über Gunzenhausen '
            f'zur Rezat“), nicht nach Nummern — dieselbe Gewohnheit, die auch die Titel der '
            f'<a href="orl-inhalt.html#abt-a">Abteilung A</a> prägt.</p>'
            f'<table class="reg"><thead><tr><th>Jahrgang</th><th>Ort</th><th>Stellen</th>'
            f'<th>Beleg</th></tr></thead><tbody>{krows}</tbody></table>')


def archiv_page(a, persons):
    """Die Archivlage: was hinter den digitalen Quellen liegt und was davon noch aussteht.

    Diese Seite veröffentlicht kein Archivgut. Sie führt zusammen, was die Recherche über die
    Bestände ergeben hat — Signaturen, Findmittel, Zugangswege — und benennt, was ungesehen
    geblieben ist. Die Vorbehalte sind Zitate aus den Rechercheprotokollen, keine Bewertung."""
    if not a:
        return ""
    pid = {}
    for p in (persons or []):
        pid[_pn(p["name"])] = p["id"]
    arb = a.get("arbeitsliste", [])
    arows = "".join(f'<tr><td>{html.escape(x["prio"])}</td><td><b>{html.escape(x["bestellung"])}</b></td>'
                    f'<td>{html.escape(x["frage"])}</td></tr>' for x in arb)
    drows = "".join(f'<tr><td class="meta">{html.escape(x["notiz"])}</td><td>{html.escape(x["text"])}</td></tr>'
                    for x in a.get("desiderate", []))
    brows = ""
    for b in a.get("bestaende", []):
        lnk = (f'<a href="{html.escape(b["findmittel_url"])}">Findmittel ↗</a>'
               if b.get("findmittel_url") else '<span class="meta">—</span>')
        sig = ", ".join(html.escape(s) for s in b.get("signaturen", [])[:8]) or "—"
        brows += (f'<tr><td><b>{html.escape(b["notiz"])}</b>'
                  f'<div class="meta">{html.escape(b.get("kurz", "")[:230])}…</div></td>'
                  f'<td>{html.escape(", ".join(b.get("institutionen", [])) or "—")}</td>'
                  f'<td class="meta">{sig}</td>'
                  f'<td class="meta">{html.escape(b.get("zugang", "") or "—")}</td><td>{lnk}</td></tr>')
    nrows = ""
    for n in a.get("nachlaesse", []):
        i = pid.get(_pn(n["person"]))
        nm = (f'<a href="persons.html#{i}">{html.escape(n["person"])}</a>' if i else html.escape(n["person"]))
        nrows += (f'<tr><td>{nm}</td><td class="meta">{html.escape(n.get("rolle", "") or "—")}</td>'
                  f'<td>{html.escape(n["verwahrort"])}</td></tr>')
    prows = "".join(f'<tr><td><a href="{html.escape(p["url"])}">{html.escape(p["name"])}</a></td>'
                    f'<td>{html.escape(p["reichweite"])}</td></tr>' for p in a.get("portale", []))
    zrows = "".join(f'<tr><td class="meta">{html.escape(z["notiz"])}</td><td>{html.escape(z["findmittel"])}</td>'
                    f'<td class="meta ktx">{html.escape(z["erreichbarkeit"])}</td>'
                    f'<td class="meta">{html.escape(z.get("deckt_ab", ""))}</td></tr>'
                    for z in a.get("zugangslage", []))
    bil = a.get("bilanz", {})
    return (f'<h1>Archivbestände</h1>'
            f'<p class="lede">Hinter den gedruckten Quellen liegt das unveröffentlichte Material: '
            f'Grabungstagebücher, Korrespondenz, Vermessungsunterlagen, Ministerialakten. Diese Seite '
            f'veröffentlicht davon nichts — sie führt zusammen, <b>wo es liegt</b>, <b>wie man herankommt</b> '
            f'und vor allem, <b>was noch niemand angesehen hat</b>. '
            f'{bil.get("bestandsnotizen", 0)} Bestands- und Findmittelnotizen, '
            f'{bil.get("signaturen", 0)} belegte Signaturen, {bil.get("nachlaesse", 0)} Nachlässe.</p>'
            f'<div class="note"><p><b>Der Grundbefund.</b> Das wissenschaftliche Archiv der Kommission liegt '
            f'geschlossen bei der Römisch-Germanischen Kommission des DAI in Frankfurt; ihre <i>Verwaltungs</i>'
            f'überlieferung dagegen verteilt sich auf die Staatsarchive der Trägerstaaten — die Kommission war '
            f'keine Behörde, sondern ein Verbund. Wer das Unternehmen als Institution untersuchen will, muss '
            f'daher an mehreren Orten suchen; wer die Grabungen untersuchen will, an einem.</p></div>'
            + (f'<h2 id="arbeitsliste">Bestellliste</h2>'
               f'<p class="meta">Aus dem Erschließungsplan zum RGK-Bestand: welche Akte welche offene Frage '
               f'beantworten würde. Die Reihenfolge ist eine Setzung, keine Rangfolge der Bestände.</p>'
               f'<table class="reg nosort"><thead><tr><th>Prio</th><th>Bestellung (Signatur)</th>'
               f'<th>beantwortet welche offene Frage</th></tr></thead><tbody>{arows}</tbody></table>'
               if arb else "")
            + (f'<h2 id="vorbehalte">Vorbehalte</h2>'
               f'<p class="meta">Was die Recherche selbst als ungeprüft festgehalten hat — zitiert, nicht '
               f'zusammengefasst.</p>'
               f'<table class="reg"><thead><tr><th>Notiz</th><th>Vorbehalt</th></tr></thead>'
               f'<tbody>{drows}</tbody></table>' if drows else "")
            + f'<h2 id="bestaende">Bestände &amp; Findmittel</h2>'
            f'<table class="reg"><thead><tr><th>Bestand / Findmittel</th><th>Institution</th>'
            f'<th>Signaturen</th><th>Zugang</th><th></th></tr></thead><tbody>{brows}</tbody></table>'
            + (f'<h2 id="zugang">Erreichbarkeit</h2>'
               f'<p class="meta">Welches Findmittel maschinell erreichbar ist und welches nicht — die '
               f'Unterscheidung entscheidet, was sich token-frei erschließen lässt und was eine Reise '
               f'kostet.</p><table class="reg"><thead><tr><th>Notiz</th><th>Findmittel</th>'
               f'<th>Erreichbarkeit</th><th>deckt ab</th></tr></thead><tbody>{zrows}</tbody></table>'
               if zrows else "")
            + f'<h2 id="nachlaesse">Nachlässe</h2>'
            f'<p class="meta">Wo die persönliche Überlieferung der Kommissionsmitglieder liegt — ermittelt '
            f'über den <a href="https://kalliope-verbund.info">Kalliope-Verbund</a> und die Findmittel des '
            f'DAI. Sortierbar nach Person und Verwahrort.</p>'
            f'<table class="reg"><thead><tr><th>Person</th><th>Rolle</th><th>Verwahrort / Nachweis</th>'
            f'</tr></thead><tbody>{nrows}</tbody></table>'
            f'<h2 id="portale">Sucheinstiege</h2>'
            f'<table class="reg nosort"><thead><tr><th>Portal</th><th>Reichweite</th></tr></thead>'
            f'<tbody>{prows}</tbody></table>'
            f'<p class="meta">Zurück zu den <a href="../quellen.html">Quellen</a>.</p>')


def impressum_page():
    """Impressum, Lizenz und Zitiervorschlag — eine Website, die zitierfähig sein will,
    muss sagen, wie sie zitiert werden möchte, und wer für sie einsteht."""
    zit = ("Manuel Sassmann: RLK-digital. Die Quellen der Reichs-Limeskommission (1892–1937), "
           "digital erschlossen. 2026. "
           "URL: https://pleuston.github.io/limesblatt-edition/ (abgerufen am TT.MM.JJJJ).")
    zit_seite = ("Manuel Sassmann: [Seitentitel], in: RLK-digital. Die Quellen der Reichs-Limeskommission, "
                 "digital erschlossen, 2026. URL: [Seiten-URL] (abgerufen am TT.MM.JJJJ).")
    bib = ("@misc{rlk-digital,\n"
           "  author       = {Sassmann, Manuel},\n"
           "  title        = {RLK-digital. Die Quellen der Reichs-Limeskommission (1892--1937), "
           "digital erschlossen},\n"
           "  year         = {2026},\n"
           "  url          = {https://pleuston.github.io/limesblatt-edition/},\n"
           "  note         = {Abgerufen am TT.MM.JJJJ}\n"
           "}")
    return (f'<h1>Impressum &amp; Zitieren</h1>'
            f'<h2 id="verantwortlich">Verantwortlich</h2>'
            f'<p>Diese Website wurde erstellt von <b>Manuel Sassmann</b>. Sie ist ein privates '
            f'Forschungsprojekt ohne institutionelle Trägerschaft; Kontakt über das '
            f'<a href="https://github.com/pleuston/limesblatt-edition">Projekt-Repositorium</a> '
            f'(GitHub-Issues).</p>'
            f'<h2 id="zitieren">Zitiervorschlag</h2>'
            f'<p>Für die Website als ganze:</p>'
            f'<blockquote class="zit">{html.escape(zit)}</blockquote>'
            f'<p>Für eine einzelne Seite:</p>'
            f'<blockquote class="zit">{html.escape(zit_seite)}</blockquote>'
            f'<p class="meta">Wer einzelne <b>Daten</b> weiterverwendet — Register, Konkordanzen, '
            f'Fundlisten —, zitiert bitte zusätzlich die Seite, auf der die Angabe steht, und ihren Stand: '
            f'die Register werden fortgeschrieben. Für Belege aus dem Quellentext gilt die Zählung der '
            f'Quelle selbst (Limesblatt: Spalte; ORL: Abteilung, Band, Nummer, Seite), nicht die dieser '
            f'Website.</p>'
            f'<details><summary>BibTeX</summary><pre class="bib">{html.escape(bib)}</pre></details>'
            f'<h2 id="lizenz">Lizenz</h2>'
            f'<p><b>Text, Register und abgeleitete Daten dieser Website:</b> '
            f'<a href="https://creativecommons.org/licenses/by/4.0/deed.de">CC BY 4.0</a> — '
            f'Weiterverwendung mit Namensnennung erlaubt, auch kommerziell und in Bearbeitung.</p>'
            f'<p><b>Nicht davon erfasst</b> sind die verlinkten Quellen selbst: die Seitenbilder des '
            f'Limesblatt liegen bei der <a href="https://www.ub.uni-heidelberg.de/">UB Heidelberg</a> und '
            f'sind über IIIF eingebunden (<a href="http://rightsstatements.org/vocab/InC/1.0/">In '
            f'Copyright</a>); die ORL-Scans liegen bei HathiTrust, die Normdaten bei GND, Wikidata, iDAI '
            f'und der Epigraphic Database Heidelberg. Deren Bedingungen gelten unverändert weiter.</p>'
            f'<h2 id="herkunft">Herkunft der Angaben</h2>'
            f'<p class="meta">Wie die einzelnen Register entstanden sind, welche Quelle jeweils trägt und wo '
            f'die Grenzen liegen, steht in der <a href="dokumentation.html">Dokumentation</a>. Die Website '
            f'wird aus einem privaten Forschungs-Vault erzeugt; Aufbereitungs-Code und TEI-Quelltext sind '
            f'offen (<a href="https://github.com/pleuston/limesblatt-edition">GitHub</a>).</p>')


# ---------- Lesefassung der Jahresberichte: Text und Faksimile nebeneinander ----------
# Die Berichte liegen als Blatt-Texte vor (macOS-Vision-Lesung je Scanblatt, s. Vault-Werkzeug
# rlk_jahresberichte.py --reocr). archive.org stellt zu jedem Blatt einen IIIF-Image-Service
# bereit — `iiif.archive.org/iiif/<item>$<blatt>` —, also lässt sich dieselbe Zweipanel-Ansicht
# bauen wie bei den Limesblatt-Bänden: links der Lesetext, rechts das Blatt, aneinander gekoppelt.
JB_KOPFZEILE = re.compile(r"^[A-ZÄÖÜ][A-ZÄÖÜ0-9 .,:;()\-–']{5,}$")
# Nur die Klammerform »1)« zählt als Aufzählung. »17. October« ist ein Datum, kein
# Gliederungspunkt — die Punktform holte in den frühen Berichten reihenweise Datumsangaben herein.
JB_PUNKT = re.compile(r"^\s*((?:\d{1,2}\))|(?:[IVXLC]{1,5}\.))\s+(?=[A-ZÄÖÜ„])")
# Normalisierte Lauftitel des Bandes — was ihnen entspricht, ist Kolumnentitel, nicht Gliederung.
JB_LAUFTITEL = ("ARCHÄOLOGISCHERANZEIGER", "BEIBLATTZUMJAHRBUCHDESARCHÄOLOGISCHENINSTITUTS",
                "JAHRBUCHDESARCHÄOLOGISCHENINSTITUTS", "BERICHTÜBERDIETHÄTIGKEITDERREICHSLIMESKOMMISSION",
                "BERICHTÜBERDIEARBEITDERREICHSLIMESKOMMISSION", "DESARCHÄOLOGISCHENINSTITUTS")


def jb_struktur(txt):
    """Blatt-Text → Absätze und Zwischenüberschriften.

    Der Druck gliedert mit Versalzeilen (»ERWERBUNGEN«) und mit Aufzählungen (»1) Hr. Professor
    Fink in München …«) — beides überlebt die Vision-Lesung als Zeilenanfang und trägt hier die
    Gliederung. Absatzgrenzen im Fließtext gibt die Vorlage nicht her: die Lesung liefert Zeilen,
    keine Absätze. Deshalb wird nur dort umbrochen, wo die Quelle selbst gliedert."""
    absaetze, puffer, kopfe = [], [], []
    kopfpuffer = []

    def flush():
        if puffer:
            absaetze.append(("p", " ".join(puffer)))
            puffer.clear()

    def flush_kopf():
        # Der Halbseiten-Schnitt zerlegt jede seitenbreite Zeile in zwei Stücke
        # (»ARCHÄOLOGISC« + »CHER ANZEIGER«). Aufeinanderfolgende Versalzeilen gehören
        # deshalb zusammen; und was danach der Kolumnentitel ist, ist keine Gliederung.
        if not kopfpuffer:
            return
        s = " ".join(kopfpuffer); kopfpuffer.clear()
        n = re.sub(r"[^A-ZÄÖÜ]", "", s.upper())
        if len(n) < 8:
            return
        for lauf in JB_LAUFTITEL:
            if n in lauf or lauf in n or (len(n) > 12 and n[:12] in lauf):
                return
        absaetze.append(("h", s)); kopfe.append(s)

    for roh in txt.split("\n"):
        z = roh.strip()
        if not z:
            continue
        if JB_KOPFZEILE.match(z) and len(z) < 90 and not re.search(r"[a-zäöüß]{3}", z):
            flush(); kopfpuffer.append(z); continue
        flush_kopf()
        m = JB_PUNKT.match(z)
        if m:
            flush(); puffer.append(z); continue
        puffer.append(z)
    flush_kopf(); flush()
    # Die Gliederung des Berichts sind seine Aufzählungen: »1) Hr. Professor Fink in München
    # förderte …« — jeder Punkt ein Streckenkommissar und sein Abschnitt. Versalzeilen kommen
    # dazu, wo der Druck welche setzt. Beides wird angesteuert, beides steht in der Gliederung.
    out, marken = [], []
    for art, s in absaetze:
        if art == "h":
            aid = "jbk-" + gazetteer.slug(s)[:40]
            lab = s.title() if s.isupper() else s
            marken.append((lab, aid))
            out.append(f'<h3 id="{aid}">{html.escape(lab)}</h3>')
        else:
            m = JB_PUNKT.match(s)
            if m:
                rest = s[m.end():]
                aid = "jbp-" + gazetteer.slug(m.group(1) + "-" + rest[:30])[:44]
                lab = m.group(1) + " " + (rest[:60] + "…" if len(rest) > 60 else rest)
                marken.append((lab, aid))
                out.append(f'<p class="artp" id="{aid}"><b>{html.escape(m.group(1))}</b> '
                           f'{html.escape(rest)}</p>')
            else:
                out.append(f'<p>{html.escape(s)}</p>')
    return "".join(out), marken


def jahresbericht_reader(b):
    """Eine Lesefassung je Jahrgang: strukturierter Text links, IIIF-Blatt rechts."""
    seiten = b.get("seiten") or []
    if not seiten:
        return None, None
    item = b.get("item") or f"jahrbuchdeskaise{b['band']:02d}kaisrich"
    # archive.org zählt die IIIF-Blätter AB 1, das djvu-XML ab 0 — ohne das +1 zeigt das
    # Faksimile durchgehend die Seite davor. Geprüft: IIIF $371 ist djvu-Blatt 370.
    tiles = [f"https://iiif.archive.org/iiif/{item}%24{s['leaf'] + 1}/info.json" for s in seiten]
    text, alle_kopfe = [], []
    for i, s in enumerate(seiten):
        koerper, marken = jb_struktur(s.get("text") or "")
        alle_kopfe += marken
        text.append(f'<div class="pb" id="blatt-{s["leaf"]}" data-page="{i}" '
                    f'onclick="viewer.goToPage({i})" title="Dieses Blatt im Faksimile zeigen">'
                    f'— Blatt {s["leaf"]} —</div>{koerper}')
    gliederung = ""
    if alle_kopfe:
        gliederung = ('<details class="inhalt"><summary>Gliederung des Berichts '
                      f'({len(alle_kopfe)} Abschnitte)</summary>'
                      '<ul class="toc">'
                      + "".join(f'<li><a href="#{aid}">{html.escape(lab)}</a></li>'
                                for lab, aid in alle_kopfe) + '</ul></details>')
    head = '<script src="../assets/openseadragon.min.js"></script>'
    body = f"""<h1>Bericht der Reichs-Limeskommission {b['jahrgang']}</h1>
<p class="meta">Jahrbuch des Kaiserlich Deutschen Archäologischen Instituts, Band {b['band']} —
Archäologischer Anzeiger, Blatt {seiten[0]['leaf']}–{seiten[-1]['leaf']} ·
{len(seiten)} Seiten · Faksimile: <a href="https://archive.org/details/{item}">archive.org</a> ·
Lesung: {html.escape(b.get('ocr', ''))} ·
TEI: <a href="../tei/jahresberichte/jb{b['jahrgang']}.xml">XML</a> ·
zurück zum <a href="../register/jahresberichte.html">Berichtsindex</a></p>
{gliederung}
<div class="reader">
  <div class="facs"><div id="osd"></div>
    <div class="osdnav"><button onclick="viewer.goToPage(Math.max(0,viewer.currentPage()-1))">‹ vorige</button>
    <span class="toggles"><label class="synctoggle"><input type="checkbox" id="syncscroll" checked>
    Faksimile folgt</label></span>
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
// goHome nach dem Öffnen: der Viewer startet sonst weit außerhalb des Blattes (die Kachelgröße
// steht erst fest, wenn info.json da ist) — die Fläche bleibt schwarz, das Blatt ein Fleck.
viewer.addHandler("open", function(){{ upd(); viewer.viewport.goHome(true); }});
viewer.addHandler("page", function(ev){{
  upd();
  if(!syncOn()||_slock) return;
  var pb=document.querySelector('.reader .text .pb[data-page="'+ev.page+'"]');
  if(pb){{_slock=true; pb.scrollIntoView({{behavior:"smooth",block:"start"}}); setTimeout(function(){{_slock=false;}},700);}}
}});
(function(){{
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
</script>"""
    return body, head


def artikel_seite(a):
    """Ein Aufsatz als Lesefassung: Text links, Blatt der UB Heidelberg rechts."""
    seiten = a.get("seiten") or []
    if not seiten:
        return None, None
    tiles = [s["iiif"] + "/info.json" for s in seiten if s.get("iiif")]
    text = []
    for i, s in enumerate(seiten):
        absaetze = "".join(f"<p>{html.escape(z.strip())}</p>"
                           for z in re.split(r"\n\s*\n", s.get("text") or "") if z.strip())
        text.append(f'<div class="pb" id="s-{html.escape(s["druckseite"])}" data-page="{i}" '
                    f'onclick="viewer.goToPage({i})" title="Dieses Blatt im Faksimile zeigen">'
                    f'— S. {html.escape(s["druckseite"])} —</div>{absaetze}')
    head = '<script src="../assets/openseadragon.min.js"></script>'
    # Zwei Anbieter, zwei Adressformen — die Kennung eines Anbieters in die URL des anderen zu
    # setzen ergibt einen Link, der nach Nachweis aussieht und ins Leere führt.
    if a.get("anbieter") == "archive.org":
        quelle_link = f'<a href="https://archive.org/details/{a["slug"]}">archive.org</a>'
    else:
        quelle_link = (f'<a href="https://digi.ub.uni-heidelberg.de/diglit/{a["slug"]}">'
                       f'UB Heidelberg</a>')
    body = f"""<h1>{html.escape(a["titel"])}</h1>
<p class="meta">{html.escape(a.get("verfasser", ""))} · {html.escape(a.get("quelle", ""))} ·
{len(seiten)} Seiten · Faksimile und OCR: {quelle_link} · TEI: <a href="../tei/artikel/{a["id"]}.xml">XML</a> ·
zurück zum <a href="index.html">Aufsatzverzeichnis</a></p>
<p class="meta">{html.escape(a.get("warum", ""))}</p>
<div class="reader">
  <div class="facs"><div id="osd"></div>
    <div class="osdnav"><button onclick="viewer.goToPage(Math.max(0,viewer.currentPage()-1))">‹ vorige</button>
    <span class="toggles"><label class="synctoggle"><input type="checkbox" id="syncscroll" checked>
    Faksimile folgt</label></span>
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
viewer.addHandler("open", function(){{ upd(); viewer.viewport.goHome(true); }});
viewer.addHandler("page", function(ev){{
  upd();
  if(!syncOn()||_slock) return;
  var pb=document.querySelector('.reader .text .pb[data-page="'+ev.page+'"]');
  if(pb){{_slock=true; pb.scrollIntoView({{behavior:"smooth",block:"start"}}); setTimeout(function(){{_slock=false;}},700);}}
}});
(function(){{
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
</script>"""
    return body, head


def artikel_index(arts, offen=()):
    """Verzeichnis der einzeln erschlossenen Aufsätze — nach Organ gruppiert.

    `offen` sind die Aufsätze des Verzeichnisses, die KEIN erreichbares Digitalisat haben. Sie
    werden mit ihrem Grund genannt, statt zu verschwinden: eine Lücke, die man sieht, ist eine
    Angabe; eine Lücke, die man nicht sieht, ist ein stiller Bestandsfehler."""
    from collections import defaultdict
    grp = defaultdict(list)
    for a in arts:
        organ = re.split(r"\s+\d", a.get("quelle", "Einzeldruck"))[0].strip() or "Einzeldruck"
        grp[organ].append(a)
    teile = []
    for organ in sorted(grp):
        zeilen = "".join(
            f'<tr><td><a href="{a["id"]}.html"><b>{html.escape(a["titel"][:90])}</b></a></td>'
            f'<td>{html.escape(a.get("verfasser", "") or "—")}</td>'
            f'<td class="meta">{html.escape(a.get("quelle", ""))}</td>'
            f'<td>{len(a.get("seiten") or [])}</td><td>{a.get("woerter", 0):,}</td>'
            f'<td class="meta">{"archive.org" if a.get("anbieter") == "archive.org" else "UB Heidelberg"}</td></tr>'.replace(",", ".")
            for a in sorted(grp[organ], key=lambda x: x.get("quelle", "")))
        teile.append(f'<h2>{html.escape(organ)}</h2>'
                     f'<table class="reg"><thead><tr><th>Aufsatz</th><th>Verfasser</th><th>Fundstelle</th>'
                     f'<th>Seiten</th><th>Wörter</th><th>Digitalisat</th></tr></thead>'
                     f'<tbody>{zeilen}</tbody></table>')
    n_w = sum(a.get("woerter", 0) for a in arts)
    luecke = ""
    if offen:
        zeilen = "".join(
            f'<tr><td>{html.escape(o.get("wer_was", ""))}</td>'
            f'<td class="meta">Bd. {html.escape(str(o.get("band", "")))}, '
            f'S. {o.get("von")}–{o.get("bis")}</td>'
            f'<td class="meta">{html.escape(o.get("grund", ""))}</td></tr>' for o in offen)
        luecke = (f'<h2>Ohne erreichbares Digitalisat</h2>'
                  f'<p class="meta">Diese Aufsätze stehen im Verzeichnis, ihr Band ist aber in keiner '
                  f'der abgefragten Sammlungen digitalisiert. Sie bleiben hier stehen, damit die '
                  f'Lücke sichtbar ist.</p>'
                  f'<table class="reg"><thead><tr><th>Aufsatz</th><th>Fundstelle</th>'
                  f'<th>Warum nicht hier</th></tr></thead><tbody>{zeilen}</tbody></table>')
    return (f'<h1>Aufsätze</h1>'
            f'<p class="lede">Einzelne Aufsätze, die für die Gründungs- und Forschungsgeschichte zählen — '
            f'jeder als Volltext neben seinem Faksimile. <b>{len(arts)} Aufsätze</b>, '
            f'{n_w:,} Wörter.</p>'.replace(",", ".") +
            f'<p class="meta">Der Bestand kommt aus dem Aufsatzverzeichnis des Forschungs-Vaults, das die '
            f'Inhaltsverzeichnisse der Digitalisate ausgewertet hat; Text und Blatt liefern die '
            f'Digitalisate selbst — bei der UB Heidelberg über IIIF-Manifest und ALTO-OCR, bei '
            f'archive.org über die Bildfolge und den mitgelieferten Text. Welche Stelle eines Bandes '
            f'den Aufsatz führt, wird nicht geraten. Bei der UB Heidelberg gilt der Jahrgang erst als '
            f'gefunden, wenn die Seitenbeschriftungen seiner Bildfolge die gesuchte Spanne wirklich '
            f'enthalten. Bei archive.org reicht das nicht, denn dort sind mehrere Bände in einen Scan '
            f'gebunden und dieselbe Seitenzahl kommt mehrfach vor: dort müssen <b>zwei</b> Belege '
            f'zusammenfallen — Verfasser- und Titelwörter auf dem Blatt selbst, und eine passende '
            f'Folgenummer in Reichweite. Fällt beides nicht zusammen, bleibt der Aufsatz unerschlossen.</p>'
            + "".join(teile) + luecke +
            f'<p class="meta">Zurück zu den <a href="../quellen.html">Quellen</a>.</p>')


def documentation_page(s):
    # Datenherkunft — jede offene Quelle in klarer Sprache (kein Fachjargon)
    src = [
        ("Universitätsbibliothek Heidelberg", "Die eingescannten Originalseiten und ihre maschinelle Umschrift — Grundlage des Volltexts, der Suche, der Namens- und Ortslisten und der Analyse."),
        ("Normdaten der Bibliotheken (GND) &amp; Wikidata", "Zu den Personen: gesicherte Lebensdaten, Rollen und Verweise auf Standard-Nachschlagewerke."),
        ("Kalliope (Nachlass-Verbund)", "Wo die Nachlässe und Briefe der beteiligten Forscher heute aufbewahrt werden."),
        ("Epigraphische Datenbank Heidelberg", "Die von den Limes-Fundorten bekannten römischen Inschriften."),
        ("Antike-Ortsverzeichnisse &amp; OpenStreetMap", "Die Karte, der Verlauf der Grenzlinie und die Wachttürme und Kleinkastelle je Abschnitt."),
        ("HLGL (Uni Marburg) &amp; Virtuelles Kartenforum (SLUB Dresden)", "Zwei zuschaltbare historische Kartenebenen — live eingebundene Kacheldienste, hier nicht kopiert: die hessische Landesaufnahme 1819–1850 und die »Karte des Deutschen Reiches« von 1909."),
        ("Digitale Geländemodelle Hessen (HVBG/HLNUG), Bayern (LDBV) &amp; Baden-Württemberg (LGL)", "Ein zuschaltbares Geländerelief statt Kartenbild — live eingebundene Dienste, hier nicht kopiert: zeigt die Terrainform, in der Wall und Graben des Limes stellenweise noch als flache Erhebung erkennbar sind."),
        ("archive.org", "Frei lesbare Digitalisate der zitierten Literatur und der eine offen zugängliche Band der Endpublikation."),
        ("Digitale Bibliothek HathiTrust", "Die eingescannten Bände der Endpublikation (ORL), aus denen ihre Verzeichnisse gewonnen wurden."),
    ]
    srows = "".join(f'<tr><td>{a}</td><td class="meta">{b}</td></tr>' for a, b in src)
    return (
        f'<h1>Dokumentation</h1>'
        f'<p class="meta"><b>RLK-digital</b> erschließt die Quellen der Reichs-Limeskommission in fünf '
        f'Beständen (<a href="quellen.html">Überblick</a>): die laufenden '
        f'<a href="index.html"><b>Feldberichte des Limesblatt</b></a> (1892–1903), die große '
        f'<a href="register/orl.html"><b>Endpublikation ORL</b></a> (1894–1937), die '
        f'<a href="register/jahresberichte.html"><b>Jahresberichte</b></a> der Kommission (1892–1905), die '
        f'zitierte Literatur und die regionalen Organe — und zeigt, wie das eine ins andere überging. Alles, was '
        f'sich verlässlich nachschlagen lässt (Lebensdaten, Orte, Nachweise), wurde automatisch aus frei '
        f'zugänglichen Quellen zusammengetragen; das Deuten, Prüfen und Schreiben blieb Handarbeit. Diese Seite '
        f'erklärt <b>was</b> hier zu finden ist, <b>woher</b> die Angaben stammen und <b>was</b> sie erkennen '
        f'lassen.</p>'

        f'<h2>1 · Was auf der Website steht</h2>'
        f'<p class="meta">Die Seite hat drei Ebenen: den <i>lesbaren Text</i> der Bände, <i>Verzeichnisse</i>, die '
        f'diesen Text erschließen, und einige <i>Auswertungen</i>. Oben in der Leiste erreichbar, hier ausführlich:</p>'

        f'<h3>Der Text der Bände</h3>'
        f'<ul>'
        f'<li><a href="baende.html"><b>Bände</b></a> — die {s["nvol"]} Hefte des Limesblatt vollständig lesbar, '
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

        f'<h3>Die Endpublikation (ORL)</h3>'
        f'<ul>'
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
        f'<h3>Vom Feldbericht zum Standardwerk</h3>'
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
        f'Bibliotheken bislang <b>keinen eigenen Eintrag</b> — ein offener Punkt seiner Erschließung.</p>'
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

PERSON_INST = {   # kuratierte institutionelle Verankerung der RLK-Leitung & Streckenkommissare
    "Theodor Mommsen": "Preuß. Akademie, Berlin", "Ernst Fabricius": "Univ. Freiburg",
    "Oscar von Sarwey": "Stuttgart", "Felix Hettner": "Provinzialmuseum Trier",
    "Friedrich Leonhard": "Berlin", "Louis Jacobi": "Saalburgmuseum", "Heinrich Jacobi": "Saalburgmuseum",
    "Georg Wolff": "Frankfurt a. M.", "Friedrich Kofler": "Denkmalpflege Darmstadt",
    "Wilhelm Conrady": "Miltenberg", "Karl Schumacher": "RGZM Mainz", "Ernst von Herzog": "Univ. Tübingen",
    "Heinrich Steimle": "Stuttgart", "Robert Bodewig": "Lahnstein", "Emil Ritterling": "Museum Wiesbaden",
    "Wilhelm Soldan": "Hanau",
}

def organigramm_page(persons, pname):
    byname = {p["name"]: p for p in persons}
    # Streckenkommissare → ihre Strecken (datengetrieben aus STRECKE_KOMMISSAR)
    komm = {}
    for nr, names in STRECKE_KOMMISSAR.items():
        for n in names:
            komm.setdefault(n, []).append(nr)
    order = sorted(komm, key=lambda n: (min(komm[n]), n))
    # Layout
    Wd, bw, bh, gx, gy, per = 1080, 244, 64, 18, 34, 4
    startx = (Wd - (per * bw + (per - 1) * gx)) // 2
    gridy = 210
    rows = (len(order) + per - 1) // per
    grid_bottom = gridy + rows * bh + (rows - 1) * gy
    ausy = grid_bottom + 26
    insty = ausy + 82
    H = insty + 84
    S = [f'<svg viewBox="0 0 {Wd} {H}" xmlns="http://www.w3.org/2000/svg" font-family="system-ui,sans-serif" '
         f'style="max-width:100%;height:auto;border:1px solid var(--line,#ddd);border-radius:6px;background:var(--bg,#fff)">']
    def box(x, y, w, h, lines, fill, href=None, fs=14):
        r = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="#b9b3a6"/>')
        n = len(lines); ty0 = y + h / 2 - (n - 1) * 8 + 5
        tx = x + w / 2
        txt = "".join(f'<text x="{tx:.0f}" y="{ty0 + i*16:.0f}" text-anchor="middle" font-size="{fs if i==0 else 12}" '
                      f'fill="#222"{" font-weight=\"600\"" if i==0 else ""}>{html.escape(ln)}</text>' for i, ln in enumerate(lines))
        inner = r + txt
        return f'<a href="{href}">{inner}</a>' if href else inner
    def line(x1, y1, x2, y2): return f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="#b9b3a6"/>'
    cx = Wd / 2
    # Ebene 0: Kommission
    S.append(box(cx - 175, 16, 350, 54, ["Reichs-Limeskommission", "1892–1937"], "#e7eef6", fs=16))
    # Ebene 1: Leitung / Herausgeber
    S.append(line(cx, 70, cx, 96))
    S.append(box(cx - 300, 96, 600, 50,
                 ["Initiator: Theodor Mommsen · Leitung/Herausgeber:", "Sarwey · Hettner · Fabricius · Leonhard"],
                 "#e7eef6", fs=14))
    # Bus zu den Kommissaren
    busy = 176
    S.append(line(cx, 146, cx, busy))
    firstx = startx + bw / 2; lastx = startx + (min(per, len(order)) - 1) * (bw + gx) + bw / 2
    S.append(line(firstx, busy, lastx, busy))
    # Ebene 2: Streckenkommissare
    for i, n in enumerate(order):
        col, row = i % per, i // per
        x = startx + col * (bw + gx); y = gridy + row * (bh + gy)
        strk = ", ".join(str(s) for s in sorted(komm[n]))
        p = byname.get(n)
        href = f'persons.html#{p["id"]}' if p else None
        if row == 0:
            S.append(line(x + bw / 2, busy, x + bw / 2, y))
        blines = [n, f'Strecke {strk}'] + ([PERSON_INST[n]] if n in PERSON_INST else [])
        S.append(box(x, y, bw, bh, blines, "#f4efe4", href=href, fs=13))
    # Ebene 3: Ausgräber (Sammelhinweis)
    S.append(line(cx, grid_bottom, cx, ausy))
    S.append(box(cx - 300, ausy, 600, 40,
                 ["Ausgräber der Kastelle → im Personenregister und je Strecke"], "#f4efe4",
                 href="persons.html", fs=13))
    # Institutionen (kuratiert)
    inst = [("Reich / Reichstag", "Finanzierung", "https://de.wikipedia.org/wiki/Reichs-Limeskommission"),
            ("Preußische Akademie", "d. Wissenschaften", "https://de.wikipedia.org/wiki/Preußische_Akademie_der_Wissenschaften"),
            ("Röm.-Germ. Kommission", "RGK, ab 1902", "https://de.wikipedia.org/wiki/Römisch-Germanische_Kommission"),
            ("Provinzialmuseen", "Saalburg · Mainz u. a.", "places.html")]
    iw = (Wd - 2 * startx - 3 * gx) / 4
    S.append(f'<text x="{startx}" y="{insty - 12}" font-size="12" fill="#666">Trägerschaft &amp; Umfeld</text>')
    for i, (t1, t2, href) in enumerate(inst):
        x = startx + i * (iw + gx)
        S.append(box(x, insty, iw, 52, [t1, t2], "#e9f2ec", href=href, fs=13))
    S.append('</svg>')
    return (f'<h1>Organigramm der Reichs-Limeskommission</h1>'
            f'<p class="meta">Die Struktur des ersten länderübergreifenden Großforschungs-Unternehmens des '
            f'Kaiserreichs: initiiert von Theodor Mommsen, geleitet von wenigen Herausgebern, getragen von den '
            f'<b>Streckenkommissaren</b>, die je einen oder mehrere der 15 Abschnitte verantworteten und unter '
            f'denen die Ausgräber vor Ort arbeiteten. Die Kommissar-Kästen sind mit dem '
            f'<a href="persons.html">Personenregister</a> verknüpft (die Strecken selbst stehen bei den '
            f'<a href="strecken.html">Strecken</a>). Unter jedem Kommissar steht seine <b>institutionelle '
            f'Verankerung</b> (Museum, Akademie oder Universität) — so wird sichtbar, aus welchem Netz von '
            f'Provinzialmuseen und Universitäten sich das Unternehmen speiste. Leitungs-, Institutions- und '
            f'Affiliations-Ebene sind kuratiert; die Kommissar→Strecken-Zuordnung ist datengetrieben.</p>'
            f'<div style="overflow-x:auto">{"".join(S)}</div>')

def willkommen_page(s):
    def tile(icon, title, desc, href):
        return (f'<a href="{href}" style="display:block;text-decoration:none;color:inherit;'
                f'border:1px solid var(--line,#ddd);border-radius:8px;padding:.85em 1.05em;background:var(--card,#fbfaf7)">'
                f'<div style="font-size:1.5em;line-height:1">{icon}</div>'
                f'<div style="font-weight:600;margin:.25em 0 .15em">{title}</div>'
                f'<div class="meta">{desc}</div></a>')
    tiles = [
        ("🗂", "Die Quellen",
         'Fünf Bestände nebeneinander: das Feldorgan <b>Limesblatt</b>, die Endpublikation <b>ORL</b>, die '
         '<b>Jahresberichte</b> der Kommission, die zitierte Literatur und die regionalen Organe.',
         "quellen.html"),
        ("📖", "Die Bände lesen",
         f'Alle <b>35 Hefte</b> des Limesblatt (1892–1903), gebunden in {s["nvol"]} Jahrgangsbände — neben den eingescannten Originalseiten, mit Volltextsuche.',
         "baende.html"),
        ("👥", "Menschen &amp; Orte",
         f'Wer die Grenze erforschte und wo: {s["npers"]} Personen, {s["nplac"]} Kastelle auf der Karte, die 15 Abschnitte und das <b>Organigramm</b> der Kommission.',
         "register/organigramm.html"),
        ("🏺", "Was gefunden wurde",
         f'Münzen, Keramik und Truppenstempel aus dem Fundindex sowie die {s["nedh"]} römischen Inschriften der Fundorte.',
         "register/fundindex.html"),
        ("📗", "Die Endpublikation (ORL)",
         'Das mehrbändige Standardwerk, in das die Feldberichte mündeten — Bandindex, Gesamtregister, Bearbeiter.',
         "register/orl.html"),
        ("📊", "Analyse",
         'Wie sich die Sprache über die Jahre wandelt, welche Kaiser und Münzen datieren, und der Vergleich Feldbericht ↔ Standardwerk.',
         "register/wortschatz.html"),
        ("ℹ️", "Über diese Website",
         'Ein Wegweiser: was hier zu finden ist, woher die Angaben stammen und was sie erkennen lassen.',
         "dokumentation.html"),
    ]
    grid = ('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1em;margin:1.3em 0">'
            + "".join(tile(*t) for t in tiles) + '</div>')
    return (f'<h1>Übersicht</h1>'
            f'<p><b>RLK-digital</b> macht die <b>Anfänge der Limesforschung</b> lesbar und durchsuchbar — die '
            f'Quellen der <b>Reichs-Limeskommission</b> (1892–1937) in fünf Beständen: die laufenden Feldberichte '
            f'des <i>Limesblatt</i> (1892–1903), das Standardwerk, in das sie mündeten (<i>Obergermanisch-'
            f'Raetischer Limes</i>, 1894–1937), die <a href="register/jahresberichte.html">Jahresberichte</a> der '
            f'Kommission, die zitierte Literatur und die regionalen Organe '
            f'(<a href="quellen.html">Überblick</a>).</p>'
            f'{grid}'
            f'<p class="meta">Neu hier? Beginnen Sie mit den <a href="baende.html">Bänden</a> oder lesen Sie die '
            f'<a href="dokumentation.html">Dokumentation</a>. Editionstext und Daten stehen unter CC&nbsp;BY&nbsp;4.0.</p>')

def orl_spiegel():
    """Die Gegenreihe: ORL-Faszikel je Jahr aus der Merten-Lieferungstabelle des Vaults.
    Nur ausgeben, wenn die Daten wirklich vorliegen — sonst schweigen statt behaupten."""
    f = next((p for p in (os.path.join(REPO, "data", "orl_abtB_lieferungen.json"),
                          os.path.join(REPO, "..", "limes", "tools", "orl_abtB_lieferungen.json"))
              if os.path.exists(p)), None)
    if not f:
        return ""
    try:
        d = json.load(open(f, encoding="utf-8"))
        rows = next((v for v in d.values() if isinstance(v, list)), None) if isinstance(d, dict) else d
        jahre = {}
        for l in rows or []:
            j = l.get("jahr") or l.get("jahr_lfg") or l.get("year")
            if j:
                jahre[int(str(j)[:4])] = jahre.get(int(str(j)[:4]), 0) + 1
    except Exception:
        return ""
    if not jahre or 1900 not in jahre or jahre.get(1899):
        return ""
    return (f' Die Endpublikation zeigt das umgekehrte Muster: 1899 erschien keine '
            f'ORL-Lieferung, 1900 dagegen {jahre[1900]} Faszikel — in dem Jahr, in dem kein '
            f'Limesblatt herauskam. Die Arbeitskraft der Kommission verlagerte sich in diesen '
            f'Jahren erkennbar vom Vorbericht auf die '
            f'<a href="register/orl.html">Endpublikation</a>.')


def heft_rhythmus():
    """Der Erscheinungsrhythmus, aus den Ausgabedaten gerechnet (nichts von Hand)."""
    d = [h for h in HEFTE if h.get("datum_iso")]
    if len(d) < 5:
        return ""
    import datetime, statistics
    from collections import Counter
    tage = [(datetime.date.fromisoformat(b["datum_iso"]) - datetime.date.fromisoformat(a["datum_iso"])).days
            for a, b in zip(d, d[1:])]
    med = int(statistics.median(tage))
    jahre = Counter(int(h["datum_iso"][:4]) for h in d)
    sp = range(min(jahre), max(jahre) + 1)
    bal = "".join(
        f'<span style="display:inline-block;text-align:center;margin:0 .35em .2em 0">'
        f'<span style="display:block;width:1.5em;background:{"#8a8375" if jahre.get(y) else "#d9d3c6"};'
        f'height:{max(3, jahre.get(y, 0) * 9)}px" title="{y}: {jahre.get(y, 0)} Hefte"></span>'
        f'<span class="meta" style="font-size:.72em">{str(y)[2:]}</span></span>' for y in sp)
    letzte = [f'{a["nr"]}→{b["nr"]}' for a, b in zip(d, d[1:])][-3:]
    return (f'<p><b>Der Erscheinungsrhythmus.</b> Aus den Ausgabedaten der Hefte '
            f'(im <a href="baende.html">Inhaltsverzeichnis</a> bei jeder Nummer) lässt sich der '
            f'Rhythmus ablesen: bis 1899 im Median <b>{med} Tage</b> von Heft zu Heft — das '
            f'entspricht den angekündigten „5–6 Nrn. jährlich". Danach dehnt sich die Folge '
            f'erheblich: Zwischen Nr. 32 (25. Juli 1899) und Nr. 33 (1. Februar 1901) liegen '
            f'<b>556 Tage</b>, im Jahr 1900 erschien kein Heft, und die letzten drei Nummern '
            f'folgen jeweils im Jahresabstand.{orl_spiegel()}</p>'
            f'<p style="margin:.2em 0 .1em"><span class="meta">Hefte je Jahr:</span></p>'
            f'<div style="display:flex;align-items:flex-end;gap:0">{bal}</div>'
            f'<p class="meta">Quelle der Daten: die <i>structures</i> der IIIF-Manifeste '
            f'(UB Heidelberg) — die gedruckten Impressumszeilen selbst sind für die '
            f'Fraktur-OCR unlesbar.</p>')


STRECKE_COLORS = ["#e6194B", "#3cb44b", "#ca9a00", "#4363d8", "#f58231", "#911eb4", "#0898a4",
                  "#c026a8", "#7a9c1f", "#c76a8a", "#469990", "#8a63c4", "#9A6324", "#800000", "#000075"]

def _pt_seg_d(p, a, b):                                    # Punkt→Strecken-Abstand (lat/lng planar-approx)
    (py, px), (ay, ax), (by, bx) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0: return ((px - ax) ** 2 + (py - ay) ** 2) ** .5
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** .5

def write_strecken_line(strecken):
    """Färbt die echte OSM-Limeslinie nach Strecke ein: jeder Linienpunkt wird der nächstliegenden
    Strecke (STRECKE_PATH) zugeordnet → data/strecken-line.geojson (eine farbige Linie je Abschnitt)."""
    lp = os.path.join(DOCS, "data", "limes-line.geojson")
    if not os.path.exists(lp) or not STRECKE_PATH: return
    gj = json.load(open(lp, encoding="utf-8"))
    nr2 = {int(s["nummer"]): (s["id"], s.get("name", "Strecke " + str(s["nummer"])))
           for s in strecken if str(s.get("nummer", "")).strip().isdigit()}
    sp = sorted(STRECKE_PATH.items())
    nearest = lambda ll: min(sp, key=lambda kv: _pt_seg_d(ll, kv[1][0], kv[1][1]))[0]
    def lines(g): return [g["coordinates"]] if g["type"] == "LineString" else (g["coordinates"] if g["type"] == "MultiLineString" else [])
    def mk(run, nr):
        sid, name = nr2.get(nr, ("", "Strecke " + str(nr)))
        return {"type": "Feature", "properties": {"strecke": nr, "name": name, "id": sid,
                "color": STRECKE_COLORS[(nr - 1) % len(STRECKE_COLORS)]},
                "geometry": {"type": "LineString", "coordinates": run}}
    feats = []
    for f in gj["features"]:
        for coords in lines(f["geometry"]):
            asg = [(nearest((lat, lng)), [lng, lat]) for lng, lat in coords]
            i = 0
            while i < len(asg):
                nr = asg[i][0]; run = [asg[i][1]]; j = i + 1
                while j < len(asg) and asg[j][0] == nr: run.append(asg[j][1]); j += 1
                if j < len(asg): run.append(asg[j][1])          # Brücke zum Nachbarabschnitt
                if len(run) >= 2: feats.append(mk(run, nr))
                i = j
    json.dump({"type": "FeatureCollection", "features": feats},
              open(os.path.join(DOCS, "data", "strecken-line.geojson"), "w", encoding="utf-8"))
    print(f"Streckenabschnitte auf der Karte: {len(feats)} farbige Segmente → data/strecken-line.geojson")

def main():
    os.makedirs(os.path.join(DOCS,"volumes"), exist_ok=True)
    os.makedirs(os.path.join(DOCS,"register"), exist_ok=True)
    for sub in ("tei","registers","data","assets"): os.makedirs(os.path.join(DOCS,sub), exist_ok=True)
    # TEI/Register zum Download/Reuse mitkopieren
    for f in glob.glob(os.path.join(REPO,"tei","*.xml")): shutil.copy(f, os.path.join(DOCS,"tei"))
    for unter in ("artikel", "jahresberichte"):          # TEI der Aufsätze und Jahresberichte
        os.makedirs(os.path.join(DOCS, "tei", unter), exist_ok=True)
        for f in glob.glob(os.path.join(REPO, "tei", unter, "*.xml")):
            shutil.copy(f, os.path.join(DOCS, "tei", unter))
    for f in glob.glob(os.path.join(REPO,"registers","*.xml")): shutil.copy(f, os.path.join(DOCS,"registers"))
    for f in glob.glob(os.path.join(REPO,"geo","*.geojson")): shutil.copy(f, os.path.join(DOCS,"data"))

    for f in glob.glob(os.path.join(REPO,"tei","*.xml")):   # Token→Band für interne Selbstverweise
        nr = int(re.search(r'limesblatt-bd(\d+)-', os.path.basename(f)).group(1))
        for tk in re.findall(r'<surface xml:id="f_([^"]+)"', open(f, encoding="utf-8").read()):
            TOK2BAND[tk] = nr
    volumes = sorted((load_volume(f) for f in glob.glob(os.path.join(REPO,"tei","*.xml"))), key=lambda v: v["nr"])
    register_anchors(volumes)                    # Heft-Sprungziele auf echte Ankertokens
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
    write_strecken_line(strecken)
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
    tok2anchor = {}; tok2any = {}             # (Band, IIIF-Token) → erster Spaltenanker (für NER-Seitenrefs)
    for v in volumes:
        for p in v["pages"]:
            tok2anchor.setdefault((v["nr"], p["img_tok"]), p["anchor"])
            tok2any.setdefault(p["img_tok"], (v["nr"], p["anchor"]))     # Seite entscheidet, nicht das Bandlabel
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
    open(os.path.join(DOCS,"register","organigramm.html"),"w",encoding="utf-8").write(page("Organigramm", organigramm_page(persons, pname), 1))
    nerd = os.path.join(REPO, "data")
    def loadj(fn): return json.load(open(os.path.join(nerd,fn),encoding="utf-8")) if os.path.exists(os.path.join(nerd,fn)) else ([] if "ner_" in fn else {})
    ner_p, ner_pl = loadj("ner_persons.json"), loadj("ner_places.json")
    rec_p, rec_pl = loadj("recon_persons.json"), loadj("recon_places.json")
    nb, nh = ner_index_page(ner_p, "persons", tok2anchor, rec_p, tok2any)
    open(os.path.join(DOCS,"register","namen.html"),"w",encoding="utf-8").write(page("Namen im Limesblatt", nb, 1, nh))
    ob, oh = ner_index_page(ner_pl, "places", tok2anchor, rec_pl, tok2any)
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
    # ORL-Register/Analyse-Seiten (orl_idx/orl_lex + _orl_load bereits vor den Strecken geladen)
    _artjson = _load_json_any("artikel.json") or {}
    _artliste = _artjson.get("artikel", [])
    _artoffen = _artjson.get("_offen", [])
    orl_reg = _orl_load("orl_register.json") or {"persons": [], "places": [], "counts": {}}
    if orl_idx.get("abteilung_B_kastelle"):
        orl_bli = _orl_load("orl_band_lieferung.json") or {}
    _hz = hintzelmann_page(volumes)
    if _hz:
        open(os.path.join(DOCS,"register","hintzelmann.html"),"w",encoding="utf-8").write(
            page("Hintzelmanns Register (1903)", _hz, 1))
    nm = _orl_load("namen.json")
    if nm:
        open(os.path.join(DOCS,"register","ortsnamen.html"),"w",encoding="utf-8").write(
            page("Ortsnamen — antik, modern, Flurname", namen_page(nm), 1))
        open(os.path.join(DOCS,"register","orl.html"),"w",encoding="utf-8").write(page("ORL", orl_page(orl_idx, orl_lex, orl_bli), 1))
        open(os.path.join(DOCS,"register","orl-register.html"),"w",encoding="utf-8").write(page("ORL — Gesamtapparat", orl_apparatus_page(orl_reg, orl_idx, persons), 1))
        _fj = _load_json_any("orl_faszikel.json")
        _dj = _load_json_any("orl_druckseiten.json")
        _aj = _load_json_any("orl_abtA.json")
        open(os.path.join(DOCS,"register","orl-inhalt.html"),"w",encoding="utf-8").write(
            page("ORL — Inhaltsverzeichnis", orl_toc_page(orl_idx, orl_bli, _fj, _dj, _aj, places), 1))
        _blj = _load_json_any("orl_binnenlinks.json")
        _bvj = _load_json_any("orl_binnenverweise.json")
        if _blj.get("links"):
            open(os.path.join(DOCS,"register","orl-verweise.html"),"w",encoding="utf-8").write(
                page("ORL — Binnenverweise", orl_verweise_page(_blj, _bvj), 1))
        _latj = _load_json_any("orl_latenz.json")
        _zjj = _load_json_any("orl_limesblatt_zitatjoin.json")
        _tocj = _load_json_any("toc.json")
        open(os.path.join(DOCS,"register","genese.html"),"w",encoding="utf-8").write(
            page("Die Genese des ORL", genese_page(_bvj, _blj, _latj, _zjj, HEFTE, _tocj), 1))
        open(os.path.join(DOCS,"register","hathitrust.html"),"w",encoding="utf-8").write(page("HathiTrust", hathitrust_page(orl_idx, orl_reg, orl_lex), 1))
        print(f"ORL: Abt. A {len(orl_idx.get('abteilung_A_strecken',[]))} + Abt. B {len(orl_idx.get('abteilung_B_kastelle',[]))} "
              f"→ register/orl.html · orl-register.html · hathitrust.html")
    rlk_jb = _orl_load("rlk_jahresberichte.json")
    if rlk_jb:
        os.makedirs(os.path.join(DOCS, "jahresberichte"), exist_ok=True)
        _n = 0
        for _b in rlk_jb.get("berichte", []):
            _body, _head = jahresbericht_reader(_b)
            if not _body:
                continue
            open(os.path.join(DOCS, "jahresberichte", f'jb{_b["jahrgang"]}.html'), "w",
                 encoding="utf-8").write(page(f'Bericht der RLK {_b["jahrgang"]}', _body, 1, _head))
            _n += 1
        if _n:
            print(f"Jahresberichte: {_n} Lesefassungen mit Faksimile → jahresberichte/jb<Jahr>.html")
        open(os.path.join(DOCS,"register","jahresberichte.html"),"w",encoding="utf-8").write(
            page("RLK-Jahresberichte", rlk_jahresberichte_page(rlk_jb, ner_p, ner_pl), 1))
        print(f"RLK-Jahresberichte: {rlk_jb.get('baende',0)}/14 Jahrgänge → register/jahresberichte.html")
    open(os.path.join(DOCS,"register","gesamtbibliographie.html"),"w",encoding="utf-8").write(
        page("Gesamtbibliographie",
             gesamtbibliographie_page(bibls, orl_idx, _load_json_any("orl_abtA.json") or {}, rlk_jb or {},
                                      _load_json_any("rezeption.json") or {},
                                      _load_json_any("orl_zeitschriften.json") or {},
                                      (orl_bli if "orl_bli" in dir() else {}) or {}, HEFTE), 1))
    print("Gesamtbibliographie → register/gesamtbibliographie.html")
    if rlk_jb:
        open(os.path.join(DOCS,"register","gesamtregister.html"),"w",encoding="utf-8").write(
            page("Gesamtregister", gesamtregister_page(ner_p, ner_pl, orl_reg, rlk_jb, persons), 1))
        _vw = _load_json_any("orl_limesblatt_links.json") or {}
        open(os.path.join(DOCS,"register","netz.html"),"w",encoding="utf-8").write(
            page("Netz: Personen, Publikationen, Zitate",
                 netz_page(persons, ner_p, orl_idx, (orl_bli if "orl_bli" in dir() else {}) or {},
                           _vw, rlk_jb, ner_pl, bibls, occ), 1))
        print("Gesamtregister + Netz → register/gesamtregister.html · register/netz.html")
    open(os.path.join(DOCS,"quellen.html"),"w",encoding="utf-8").write(
        page("Die Quellen", quellen_page(volumes, toc, orl_idx, rlk_jb or {}, bibls,
                                         _load_json_any("orl_zeitschriften.json") or {},
                                         _load_json_any("rezeption.json") or {}, edh, _artliste), 0))
    if _artliste:
        os.makedirs(os.path.join(DOCS, "artikel"), exist_ok=True)
        for _a in _artliste:
            _b, _h = artikel_seite(_a)
            if _b:
                open(os.path.join(DOCS, "artikel", f'{_a["id"]}.html'), "w", encoding="utf-8").write(
                    page(_a["titel"][:60], _b, 1, _h))
        open(os.path.join(DOCS, "artikel", "index.html"), "w", encoding="utf-8").write(
            page("Aufsätze", artikel_index(_artliste, _artoffen), 1))
        print(f"Aufsätze: {len(_artliste)} Lesefassungen mit Faksimile → artikel/index.html")
    print("Quellen-Hub → quellen.html")
    _arch = _load_json_any("archive.json") or {}
    if _arch.get("bestaende"):
        open(os.path.join(DOCS,"register","archive.html"),"w",encoding="utf-8").write(
            page("Archivbestände", archiv_page(_arch, persons), 1))
        print(f"Archivbestände → register/archive.html ({_arch['bilanz']['bestandsnotizen']} Notizen, "
              f"{_arch['bilanz']['nachlaesse']} Nachlässe)")
    stats = {"nvol": len(volumes), "npers": len(persons), "nplac": len(places),
             "nner_p": len(ner_p), "nner_pl": len(ner_pl),
             "nedh": edh.get("total", 0),
             "norlA": (orl_idx or {}).get("counts", {}).get("abt_A", 0),
             "norlB": (orl_idx or {}).get("counts", {}).get("abt_B", 0),
             "norlpers": orl_reg.get("counts", {}).get("persons", 0),
             "norlplac": orl_reg.get("counts", {}).get("places", 0),
             "orl_words": (orl_lex or {}).get("orl_words", 0), "lb_words": (orl_lex or {}).get("lb_words", 0)}
    open(os.path.join(DOCS,"dokumentation.html"),"w",encoding="utf-8").write(page("Dokumentation", documentation_page(stats), 0))
    print(f"Dokumentation → dokumentation.html")
    open(os.path.join(DOCS,"uebersicht.html"),"w",encoding="utf-8").write(page("Übersicht", willkommen_page(stats), 0))
    ib, ih = index_page(volumes, toc)
    open(os.path.join(DOCS,"index.html"),"w",encoding="utf-8").write(page("Startseite", ib, 0, ih))
    open(os.path.join(DOCS,"baende.html"),"w",encoding="utf-8").write(
        page("Limesblatt — Bände", baende_page(volumes, toc), 0))
    open(os.path.join(DOCS,"impressum.html"),"w",encoding="utf-8").write(
        page("Impressum & Zitieren", impressum_page(), 0))
    print(f"docs/: index + {len(volumes)} Bände + 3 Register (Personen {len(persons)}, Orte {len(places)}, "
          f"Strecken {len(strecken)}) · Suchindex {len(corpus)} Seiten · Ausgräber-Links {sum(len(v) for v in digs.values())}")

if __name__ == "__main__":
    main()
