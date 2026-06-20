#!/usr/bin/env python3
"""
build_tei.py — TEI-P5-Edition des Limesblatt + Normdaten-Register, token-frei.
================================================================================
Liest das RLK-Vault-Frontmatter (Personen/, Orte/) und den lokalen Limesblatt-
OCR-Cache (tools/.cache/limesblatt<slug>/<token>.txt) und erzeugt:
  tei/limesblatt-bdN-<slug>.xml   (8 Bände: facsimile/IIIF + pb je Token + Inline-Tags)
  registers/persons.xml           (TEI <listPerson> aus Personen/-Frontmatter)
  registers/places.xml            (TEI <listPlace> aus Orte/-Frontmatter, mit <geo>)

Diplomatische, unkorrigierte Wiedergabe der Fraktur-OCR; Inline-Eigennamen tragen
@cert="low". Bildrechte: UB Heidelberg „In Copyright" – nur per IIIF deep-gelinkt.

    python3 build/build_tei.py [--vault /pfad/zum/limes]
"""
import argparse, glob, json, os, re, sys, unicodedata
from collections import defaultdict
from xml.sax.saxutils import escape, quoteattr
import gazetteer

HERE  = os.path.dirname(os.path.abspath(__file__))
REPO  = os.path.dirname(HERE)
UA    = "limesblatt-edition/1.0 (research)"

WORKS = [  # (slug, Band-Label, Bandnr)
    ("limesblatt1892_1893", "Bd. 1 (1892/93)", 1), ("limesblatt1893_1894", "Bd. 2 (1893/94)", 2),
    ("limesblatt1894_1895", "Bd. 3 (1894/95)", 3), ("limesblatt1896",      "Bd. 4 (1896)",    4),
    ("limesblatt1897",      "Bd. 5 (1897)",    5), ("limesblatt1897_1898", "Bd. 6 (1897/98)", 6),
    ("limesblatt1898_1902", "Bd. 7 (1898/1902)", 7), ("limesblatt1903",    "Bd. 8 (1903)",    8),
]
IIIF_IMG = "https://digi.ub.uni-heidelberg.de/iiif/2/{slug}%3A{tok}.jpg/full/max/0/default.jpg"
IIIF_MAN = "https://digi.ub.uni-heidelberg.de/diglit/iiif/{slug}/manifest"
DIGLIT   = "https://digi.ub.uni-heidelberg.de/diglit/{slug}"

# Häufige Wörter / Mehrdeutiges, die NICHT als Personennamen getaggt werden
STOP = {"Mauer","Stein","Limes","Kastell","Strecke","Graben","Turm","Bericht","Provinz",
        "Anlage","Fundament","Abschnitt","Wall","Kommission","Funde"}
GENERIC = {"alteburg","altenburg","altes","oberburg","schanz","kapelle","kirche","mauer","graben",
           "heide","feld","muehle","mühle","strasse","straße","wiese","brücke","bruecke","steinbruch",
           "taunus","odenwald","wetterau","wetteraukreis","spessart","hunsrück","hunsrueck","neckar",
           "schwäbisch","fränkisch","oberer","unterer","grosser","großer","kleiner","römer","römisch"}

def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode()
    return re.sub(r"[^a-z0-9]+","_", s.lower()).strip("_")

def wikilinks(s):
    return [m.split("|")[0].strip() for m in re.findall(r"\[\[([^\]]+?)\]\]", s or "") if m.strip()]

# ---------- Frontmatter ----------
def frontmatter(path):
    t = open(path, encoding="utf-8").read()
    if not t.startswith("---"): return {}
    end = t.find("\n---", 3)
    body = t[3:end] if end >= 0 else ""
    fm = {}
    for line in body.splitlines():
        m = re.match(r"^([A-Za-z_][\w]*):\s*(.*)$", line)
        if not m: continue
        k, v = m.group(1), m.group(2).strip()
        if v.startswith("[") and v.endswith("]"):
            fm[k] = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
        else:
            fm[k] = v.strip('"').strip("'")
    return fm

def geo_of(fm):
    loc = fm.get("location")
    if isinstance(loc, list) and len(loc) == 2:
        try: return f"{float(loc[0])} {float(loc[1])}"
        except ValueError: pass
    ko = fm.get("koordinaten", "")
    m = re.match(r"\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)", ko or "")
    return f"{m.group(1)} {m.group(2)}" if m else ""

# ---------- Register laden ----------
def load_persons(vault):
    out = []
    for p in sorted(glob.glob(os.path.join(vault, "Personen", "*.md"))):
        name = os.path.basename(p)[:-3]
        fm = frontmatter(p)
        surname = name.split()[-1] if name.split() else name
        out.append({"id": "p_" + slug(name), "name": name, "surname": surname,
            "aliases": fm.get("aliases", []) if isinstance(fm.get("aliases"), list) else [],
            "gnd": fm.get("gnd",""), "wikidata": fm.get("wikidata",""),
            "birth": fm.get("geboren",""), "death": fm.get("gestorben",""),
            "role": fm.get("rolle","") or (fm.get("funktion",[""])[0] if isinstance(fm.get("funktion"),list) and fm.get("funktion") else ""),
            "residence": fm.get("wirkungsort",""), "portrait": fm.get("bild",""),
            "biografie": fm.get("biografie",""), "nachlass": fm.get("nachlass",""),
            "vitae": fm.get("vitae",""), "strecke": fm.get("strecke",""),
            "briefe_von": fm.get("briefe_von",""), "briefe_an": fm.get("briefe_an","")})
    return out

def load_places(vault):
    out = []
    for p in sorted(glob.glob(os.path.join(vault, "Orte", "**", "*.md"), recursive=True)):
        txt = open(p, encoding="utf-8").read()
        fm = frontmatter(p)
        geo = geo_of(fm)
        if not geo: continue                      # Strecken/Monument ohne Punkt überspringen
        name = os.path.basename(p)[:-3]
        term = re.sub(r"^(Klein)?[Kk]astell\s+", "", name)
        term = re.sub(r"\s*\([^)]*\)", "", term).strip()
        diggers = ["p_" + slug(t) for t in wikilinks(fm.get("ausgraeber",""))]
        sname = (wikilinks(fm.get("strecke","")) or [""])[0]
        edh = re.search(r"(\d+)\s+Inschriften", txt)
        out.append({"id": "pl_" + slug(name), "name": name, "term": term, "geo": geo,
            "wikidata": fm.get("wikidata",""), "gazetteer": fm.get("gazetteer",""),
            "pleiades": fm.get("pleiades",""), "orl": fm.get("orl_nr",""),
            "region": fm.get("provinz",""), "typ": fm.get("typ",""),
            "ort_modern": fm.get("ort_modern",""), "portrait": fm.get("bild",""),
            "diggers": diggers, "strecke_name": sname,
            "strecke_id": ("str_" + slug(sname)) if sname else "",
            "edh": edh.group(1) if edh else ""})
    return out

# ---------- Inline-Tag-Terme ----------
def dare_terms(dare, taken, corpus_low=""):
    """DARE-Kleinorte (Türme/Kleinkastelle): eindeutige, spezifische Tokens → ('dare', id, 'low')."""
    dterm = {}
    for f in dare:
        src = f.get("name", "") + " " + re.sub(r'^\*', '', f.get("ancient", ""))
        for tok in re.split(r"[\s/\-–,()]+", src):
            tok = tok.strip()
            if len(tok) >= 6 and tok[:1].isalpha() and tok.lower() not in GENERIC and tok not in taken:
                dterm.setdefault(tok, set()).add(f.get("id"))
    out = {}
    for tok, ids in dterm.items():
        if len(ids) == 1 and tok not in taken:
            if corpus_low and corpus_low.count(tok.lower()) > 40: continue   # zu häufig = Region/Gattungswort
            out[tok] = ("dare", next(iter(ids)), "low")
    return out

def tag_page(text, terms):
    """terms: {term: (kind, id, cert)}. Markiert je Vorkommen, non-overlapping, längste zuerst.
    Liefert (HTML, [(eid, kind, offset)]) — Offsets für den Belegindex."""
    spans = []
    for term, (kind, xid, cert) in terms.items():
        for m in re.finditer(r"(?<![\wäöüÄÖÜß])" + re.escape(term) + r"(?![\wäöüÄÖÜß])", text):
            spans.append((m.start(), m.end(), kind, xid, cert))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen, last = [], -1
    for s in spans:
        if s[0] >= last: chosen.append(s); last = s[1]
    res, pos, hits = [], 0, []
    for st, en, kind, xid, cert in chosen:
        res.append(escape(text[pos:st]))
        if kind == "dare":
            res.append(f'<placeName ref="dare:{xid}" cert="{cert}">{escape(text[st:en])}</placeName>')
        else:
            tag = "persName" if kind == "p" else "placeName"
            res.append(f'<{tag} ref="#{xid}" cert="{cert}">{escape(text[st:en])}</{tag}>')
        pos = en; hits.append((xid, kind, st))
    res.append(escape(text[pos:]))
    return "".join(res), hits

# ---------- Token-Reihenfolge ----------
def tokens(vault, slug_):
    d = os.path.join(vault, "tools", ".cache", slug_)
    toks = [os.path.basename(f)[:-4] for f in glob.glob(os.path.join(d, "*.txt"))]
    # Color-Check-/Beilage-Tafel je Band raus (Token = Zahl+Buchstabe, z.B. 311a);
    # Front-Matter (0000a–d, mit 0 beginnend) bleibt erhalten.
    toks = [t for t in toks if not re.match(r'^[1-9]\d*[a-z]+$', t)]
    nondig = sorted(t for t in toks if not t.isdigit())
    dig    = sorted((t for t in toks if t.isdigit()), key=int)
    return nondig + dig, d

# ---------- Register-XML ----------
def write_persons(persons, path):
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="register-persons"><teiHeader><fileDesc>',
         '<titleStmt><title>Personenregister — Limesblatt-Edition</title></titleStmt>',
         '<publicationStmt><availability status="free"><licence target="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</licence></availability></publicationStmt>',
         '<sourceDesc><p>Generiert aus dem RLK-Vault-Frontmatter (Personen/); Normdaten GND/Wikidata.</p></sourceDesc>',
         '</fileDesc></teiHeader><standOff><listPerson>']
    for p in persons:
        L.append(f'<person xml:id="{p["id"]}">')
        L.append(f'<persName>{escape(p["name"])}</persName>')
        for a in p["aliases"]:
            if a and a != p["name"]: L.append(f'<persName type="alias">{escape(a)}</persName>')
        if p["birth"]: L.append(f'<birth when={quoteattr(str(p["birth"]))}/>')
        if p["death"]: L.append(f'<death when={quoteattr(str(p["death"]))}/>')
        if p["role"]:  L.append(f'<occupation>{escape(p["role"])}</occupation>')
        if p["gnd"]:      L.append(f'<idno type="GND">{escape(str(p["gnd"]))}</idno>')
        if p["wikidata"]: L.append(f'<idno type="Wikidata">{escape(str(p["wikidata"]))}</idno>')
        if p["residence"]: L.append(f'<residence>{escape(p["residence"])}</residence>')
        if p["strecke"]:   L.append(f'<state type="strecke"><label>{escape(p["strecke"])}</label></state>')
        if p["portrait"]:  L.append(f'<idno type="portrait">{escape(p["portrait"])}</idno>')
        if p["biografie"]: L.append(f'<idno type="DeutscheBiographie">{escape(p["biografie"])}</idno>')
        if p.get("vitae"): L.append(f'<idno type="Propylaeum-VITAE">{escape(p["vitae"])}</idno>')
        if (p["briefe_von"] or p["briefe_an"]) and p["gnd"]:
            L.append(f'<idno type="Kalliope">{escape(str(p["gnd"]))}</idno>')
            L.append(f'<note type="briefe" n="{escape(str(p["briefe_von"] or 0))}/{escape(str(p["briefe_an"] or 0))}"/>')
        if p["nachlass"]:  L.append(f'<note type="nachlass">{escape(p["nachlass"])}</note>')
        L.append('</person>')
    L.append('</listPerson></standOff></TEI>')
    open(path, "w", encoding="utf-8").write("\n".join(L))

def write_places(places, path):
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="register-places"><teiHeader><fileDesc>',
         '<titleStmt><title>Ortsregister — Limesblatt-Edition</title></titleStmt>',
         '<publicationStmt><availability status="free"><licence target="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</licence></availability></publicationStmt>',
         '<sourceDesc><p>Generiert aus dem RLK-Vault-Frontmatter (Orte/); Normdaten Wikidata/iDAI-Gazetteer/Pleiades/ORL, Geo aus location.</p></sourceDesc>',
         '</fileDesc></teiHeader><standOff><listPlace>']
    for pl in places:
        L.append(f'<place xml:id="{pl["id"]}">')
        L.append(f'<placeName>{escape(pl["name"])}</placeName>')
        if pl["ort_modern"]: L.append(f'<placeName type="modern">{escape(pl["ort_modern"])}</placeName>')
        if pl["geo"]: L.append(f'<location><geo>{escape(pl["geo"])}</geo></location>')
        if pl["region"]:    L.append(f'<region>{escape(pl["region"])}</region>')
        if pl["wikidata"]:  L.append(f'<idno type="Wikidata">{escape(str(pl["wikidata"]))}</idno>')
        if pl["gazetteer"]: L.append(f'<idno type="iDAI-Gazetteer">{escape(str(pl["gazetteer"]))}</idno>')
        if pl["pleiades"]:  L.append(f'<idno type="Pleiades">{escape(str(pl["pleiades"]))}</idno>')
        if pl["orl"]:       L.append(f'<idno type="ORL">{escape(str(pl["orl"]))}</idno>')
        if pl["typ"]:       L.append(f'<trait type="kastelltyp"><desc>{escape(pl["typ"])}</desc></trait>')
        if pl["portrait"]:  L.append(f'<idno type="portrait">{escape(pl["portrait"])}</idno>')
        if pl["diggers"]:   L.append(f'<relation name="excavatedBy" passive="{escape(" ".join("#"+d for d in pl["diggers"]))}"/>')
        if pl["strecke_id"]: L.append(f'<note type="strecke" corresp="#{pl["strecke_id"]}">{escape(pl["strecke_name"])}</note>')
        if pl["edh"]:       L.append(f'<note type="edh" n="{escape(pl["edh"])}"/>')
        L.append('</place>')
    L.append('</listPlace></standOff></TEI>')
    open(path, "w", encoding="utf-8").write("\n".join(L))

def load_strecken(vault):
    out = []
    for p in sorted(glob.glob(os.path.join(vault, "Orte", "Strecken", "*.md"))):
        fm = frontmatter(p); name = os.path.basename(p)[:-3]
        out.append({"id": "str_" + slug(name), "name": name, "nummer": fm.get("nummer",""),
            "verlauf": fm.get("verlauf",""), "region": fm.get("region",""), "abschnitt": fm.get("abschnitt","")})
    try: out.sort(key=lambda s: int(s["nummer"]))
    except (ValueError, TypeError): pass
    return out

def write_strecken(strecken, path):
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="register-strecken"><teiHeader><fileDesc>',
         '<titleStmt><title>Streckenregister — Limesblatt-Edition</title></titleStmt>',
         '<publicationStmt><availability status="free"><licence target="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</licence></availability></publicationStmt>',
         '<sourceDesc><p>Generiert aus dem RLK-Vault-Frontmatter (Orte/Strecken/).</p></sourceDesc>',
         '</fileDesc></teiHeader><standOff><listPlace>']
    for s in strecken:
        L.append(f'<place type="strecke" xml:id="{s["id"]}">')
        L.append(f'<placeName>{escape(s["name"])}</placeName>')
        if s["verlauf"]:   L.append(f'<desc type="verlauf">{escape(s["verlauf"])}</desc>')
        if s["region"]:    L.append(f'<region>{escape(s["region"])}</region>')
        if s["abschnitt"]: L.append(f'<desc type="abschnitt">{escape(s["abschnitt"])}</desc>')
        if s["nummer"]:    L.append(f'<idno type="nummer">{escape(str(s["nummer"]))}</idno>')
        L.append('</place>')
    L.append('</listPlace></standOff></TEI>')
    open(path, "w", encoding="utf-8").write("\n".join(L))

def fix_moji(s):
    if s and ("Ã" in s or "Â" in s):
        for enc in ("cp1252", "latin-1"):
            try: return s.encode(enc).decode("utf-8")
            except Exception: pass
    return s

def write_geo(vault, places, outdir):
    """Aus dem Vault: DARE-Limesstellen (Türme/Kleinkastelle/Lager zwischen den Kastellen,
    entdoppelt gegen die benannten Kastelle) + Limesverlauf-Linie → geo/*.geojson."""
    os.makedirs(outdir, exist_ok=True)
    named = []
    for pl in places:
        try: lat, lng = pl["geo"].split(); named.append((float(lat), float(lng)))
        except Exception: pass
    near = lambda la, lo: any(abs(la-a) < 0.006 and abs(lo-b) < 0.006 for a, b in named)
    def load(fn):
        try: return json.load(open(os.path.join(vault, "tools", fn), encoding="utf-8")).get("features", [])
        except Exception: return []
    feats = []
    for f in load("limes_dare.geojson"):
        g = f.get("geometry", {})
        if g.get("type") != "Point": continue
        lng, lat = g["coordinates"][:2]
        if near(lat, lng): continue
        p = f.get("properties", {})
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"id": p.get("id", ""), "name": fix_moji(p.get("name", "")), "ancient": fix_moji(p.get("ancient", "")), "type": p.get("type", "")}})
    json.dump({"type": "FeatureCollection", "features": feats},
              open(os.path.join(outdir, "sites.geojson"), "w", encoding="utf-8"), ensure_ascii=False)
    lines = [f for f in load("limes.geojson") if f.get("geometry", {}).get("type") in ("LineString", "MultiLineString")]
    json.dump({"type": "FeatureCollection", "features": lines},
              open(os.path.join(outdir, "limes-line.geojson"), "w", encoding="utf-8"), ensure_ascii=False)
    return len(feats), len(lines), [ft["properties"] for ft in feats]

# ---------- Band-XML ----------
def header(slug_, label):
    return f"""<teiHeader><fileDesc>
<titleStmt><title>Limesblatt — Mitteilungen der Streckenkommissare bei der Reichs-Limeskommission. {escape(label)}</title>
<respStmt><resp>Diplomatische OCR-Edition</resp><name>Manuel Sassmann</name></respStmt></titleStmt>
<publicationStmt><publisher>limesblatt-edition</publisher>
<availability status="restricted"><licence target="https://creativecommons.org/licenses/by/4.0/">Editionstext &amp; Register: CC BY 4.0.</licence>
<p>Seitenbilder (IIIF) &#169; Universit&#228;tsbibliothek Heidelberg, <ref target="http://rightsstatements.org/vocab/InC/1.0/">In Copyright</ref> &#8212; nur verlinkt, nicht nachgenutzt.</p></availability></publicationStmt>
<sourceDesc><bibl>Reichs-Limeskommission (Hrsg.), <title>Limesblatt</title>, {escape(label)}. Trier: Lintz.
<ref type="digitisate" target="{DIGLIT.format(slug=slug_)}">UB Heidelberg</ref>;
<ref type="iiif-manifest" target="{IIIF_MAN.format(slug=slug_)}">IIIF-Manifest</ref></bibl></sourceDesc>
</fileDesc>
<encodingDesc><editorialDecl><p>Diplomatische, unkorrigierte Wiedergabe der Fraktur-OCR (ALTO, UB Heidelberg), spaltentreu aus der ALTO-Geometrie rekonstruiert. Eine <gi>surface</gi> je IIIF-Kachel (Blatt) mit <gi>zone</gi> je Spalte; im Text ein <gi>pb</gi> je <emph>Druckseite</emph> (linke Spalte = ungerade, rechte = gerade) und ein <gi>cb</gi> je Spalte. Die Druckseitenzahl (<att>n</att>) stammt aus dem Kolumnentitel (<att>type</att>="head"), sonst aus Odd/Even-Inferenz (<att>type</att>="inferred") bzw. dem Blatt-Token (<att>type</att>="token"). Heuristisch erkannte Eigennamen tragen <att>cert</att>="low" und verweisen auf die Register.</p></editorialDecl></encodingDesc>
</teiHeader>"""

def coljson(cdir, tok):
    """Spalten-Geometrie eines Tokens (von tools/alto_layout via limesblatt_ocr.py)."""
    p = os.path.join(cdir, f"{tok}.alto.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None

def build_volume(slug_, label, nr, vault, global_terms, token_terms, occ, outdir):
    toks, cdir = tokens(vault, slug_)
    surfaces, body, npages, ntags, nempty = [], [], 0, 0, 0

    def tag(text, printed, col):
        """Spaltentext markieren: korpusweite Vault-Begriffe + auf dieses Token verankerte
        NER-Begriffe; Treffer mit Offset im Belegindex sammeln."""
        terms = dict(global_terms); terms.update(token_terms.get((nr, tok), {}))
        html_, hits = tag_page(text, terms)
        for eid, kind, off in hits:
            occ[eid].append([nr, tok, printed, col, off])
        return html_.replace("\n", "<lb/>"), len(hits)   # Zeilenumbrüche (Korrekturen) → <lb/>

    for tok in toks:
        img = IIIF_IMG.format(slug=slug_, tok=tok)
        cj = coljson(cdir, tok)
        if cj and cj.get("columns"):
            # Eine <surface> je IIIF-Kachel + eine <zone> je Spalte (Pixelbox fürs Faksimile).
            pw, ph = ((cj.get("page_px") or [0, 0]) + [0, 0])[:2]
            zones = "".join(
                f'<zone xml:id="z_{tok}_{c["label"]}" ulx="{c["box"][0]}" uly="{c["box"][1]}" '
                f'lrx="{c["box"][2]}" lry="{c["box"][3]}" rendition="column"/>'
                for c in cj["columns"] if c.get("box"))
            dim = f' lrx="{pw}" lry="{ph}"' if pw and ph else ""
            surfaces.append(f'<surface xml:id="f_{tok}" n="{escape(tok)}"{dim}><graphic url="{img}"/>{zones}</surface>')
            p0 = str(cj["columns"][0].get("printed_no", tok)) if cj["columns"] else tok
            for h in cj.get("heads", []):                      # volle-Breite-Titelzeilen einmal vor den Spalten
                ht = (h.get("text") or "").strip()
                if ht:
                    tagged, n = tag(ht, p0, "a"); ntags += n
                    body.append(f'<head>{tagged}</head>')
            for pr in cj.get("paras", []):                     # spaltenübergreifender Fließtext (Editorial/Vorwort)
                pt = (pr.get("text") or "").strip()
                if pt:
                    tagged, n = tag(pt, p0, "a"); ntags += n
                    body.append(f'<p rend="span">{tagged}</p>')
            for c in cj["columns"]:                            # je Spalte = eine Druckseite
                lbl = c["label"]; printed = str(c.get("printed_no", tok))
                body.append(f'<pb n="{escape(printed)}" facs="#f_{tok}" '
                            f'xml:id="pb_{tok}_{lbl}" type="{escape(str(c.get("printed_src", "token")))}"/>')
                body.append(f'<cb n="{lbl}" facs="#z_{tok}_{lbl}"/>')
                ctxt = (c.get("text") or "").strip()
                if not ctxt:
                    body.append('<p><gap reason="ocr-empty"/></p>'); nempty += 1
                else:
                    tagged, n = tag(ctxt, printed, lbl); ntags += n
                    body.append(f'<p>{tagged}</p>')
                npages += 1
            continue
        # Fallback: kein Spalten-JSON (kaputtes ALTO) → flacher Einzeltext, eine Spalte „a".
        surfaces.append(f'<surface xml:id="f_{tok}" n="{escape(tok)}"><graphic url="{img}"/></surface>')
        txt = open(os.path.join(cdir, f"{tok}.txt"), encoding="utf-8").read().strip()
        body.append(f'<pb n="{escape(tok)}" facs="#f_{tok}" xml:id="pb_{tok}_a" type="token"/>')
        if not txt:
            body.append('<p><gap reason="ocr-empty"/></p>'); nempty += 1
        else:
            tagged, n = tag(txt, tok, "a"); ntags += n
            body.append(f'<p>{tagged}</p>')
        npages += 1
    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<?xml-model href="https://www.tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng" type="application/xml" schematypens="http://relaxng.org/ns/structure/1.0"?>\n'
           f'<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:lang="de" xml:id="limesblatt-bd{nr}">\n'
           + header(slug_, label) + "\n<facsimile>\n" + "\n".join(surfaces) + "\n</facsimile>\n"
           + '<text><body><div type="volume">\n' + "\n".join(body) + "\n</div></body></text>\n</TEI>\n")
    path = os.path.join(outdir, f"limesblatt-bd{nr}-{slug_}.xml")
    open(path, "w", encoding="utf-8").write(doc)
    return {"file": os.path.basename(path), "pages": npages, "empty": nempty, "tags": ntags}

def write_ner(entities, ner_only, path):
    """Leichtgewichtige Register-Stubs für NER-only-Entitäten (psnN_/plcN_) — damit jede
    Inline-@ref auf ein xml:id auflöst (CI) und die NER-Listen wiederverwendbar bleiben."""
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="register-ner"><teiHeader><fileDesc>',
         '<titleStmt><title>NER-Register (Volltext-Namen/Orte) — Limesblatt-Edition</title></titleStmt>',
         '<publicationStmt><availability status="free"><licence target="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</licence></availability></publicationStmt>',
         '<sourceDesc><p>Token-frei aus dem Volltext extrahierte Eigennamen (NER), die keiner kuratierten Vault-Entität entsprechen; Normdaten via lobid-GND/iDAI-Gazetteer reconciled.</p></sourceDesc>',
         '</fileDesc></teiHeader><standOff><listPerson>']
    for eid in sorted(e for e in ner_only if entities[e]["kind"] == "person"):
        e = entities[eid]
        L.append(f'<person xml:id="{eid}" type="ner" cert="{e["cert"]}"><persName>{escape(e["name"])}</persName>')
        if e.get("roles"): L.append(f'<occupation>{escape(" · ".join(e["roles"][:2]))}</occupation>')
        if e.get("gnd"):   L.append(f'<idno type="GND">{escape(str(e["gnd"]))}</idno>')
        L.append('</person>')
    L.append('</listPerson><listPlace>')
    for eid in sorted(e for e in ner_only if entities[e]["kind"] == "place"):
        e = entities[eid]
        L.append(f'<place xml:id="{eid}" type="ner" cert="{e["cert"]}"><placeName>{escape(e["name"])}</placeName>')
        if e.get("kinddetail"): L.append(f'<trait type="art"><desc>{escape(e["kinddetail"])}</desc></trait>')
        if e.get("gazId"): L.append(f'<idno type="iDAI-Gazetteer">{escape(str(e["gazId"]))}</idno>')
        if isinstance(e.get("geo"), list) and len(e["geo"]) == 2:
            L.append(f'<location><geo>{e["geo"][0]} {e["geo"][1]}</geo></location>')
        L.append('</place>')
    L.append('</listPlace></standOff></TEI>')
    open(path, "w", encoding="utf-8").write("\n".join(L))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=os.environ.get("VAULT_ROOT", os.path.join(REPO, "..", "limes")))
    a = ap.parse_args()
    vault = os.path.abspath(a.vault)
    if not os.path.isdir(os.path.join(vault, "tools", ".cache")):
        sys.exit(f"OCR-Cache fehlt unter {vault}/tools/.cache — erst `python3 tools/limesblatt_ocr.py` im Vault laufen lassen.")
    persons, places = load_persons(vault), load_places(vault)
    strecken = load_strecken(vault)
    os.makedirs(os.path.join(REPO, "registers"), exist_ok=True)
    os.makedirs(os.path.join(REPO, "tei"), exist_ok=True)
    os.makedirs(os.path.join(REPO, "data"), exist_ok=True)
    write_persons(persons, os.path.join(REPO, "registers", "persons.xml"))
    write_places(places,  os.path.join(REPO, "registers", "places.xml"))
    write_strecken(strecken, os.path.join(REPO, "registers", "strecken.xml"))
    ngeo, nline, dareprops = write_geo(vault, places, os.path.join(REPO, "geo"))
    corpus_low = " ".join(open(f, encoding="utf-8").read()
                          for f in glob.glob(os.path.join(vault, "tools", ".cache", "limesblatt*", "*.txt"))).lower()
    # vorab berechnete NER + Reconciliation (statische Daten, kein LLM zur Build-Zeit)
    def loadj(fn, dflt):
        p = os.path.join(REPO, "data", fn)
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else dflt
    ner_p, ner_pl = loadj("ner_persons.json", []), loadj("ner_places.json", [])
    rec_p, rec_pl = loadj("recon_persons.json", {}), loadj("recon_places.json", {})
    g = gazetteer.build(persons, places, ner_p, ner_pl, rec_p, rec_pl, STOP, GENERIC)
    dterms = dare_terms(dareprops, set(g["global_terms"]), corpus_low)
    global_terms = dict(sorted({**g["global_terms"], **dterms}.items(), key=lambda kv: -len(kv[0])))
    token_terms, entities, ner_only = g["token_terms"], g["entities"], g["ner_only"]
    write_ner(entities, ner_only, os.path.join(REPO, "registers", "ner.xml"))
    print(f"Register: {len(persons)} Personen, {len(places)} Orte, {len(strecken)} Strecken")
    print(f"Gazetteer: {len(global_terms)} korpusweite Begriffe (+{len(dterms)} DARE), "
          f"{sum(len(v) for v in token_terms.values())} seiten-verankerte NER-Begriffe, "
          f"{len(ner_only)} NER-only-Entitäten ({sum(1 for e in ner_only if entities[e]['kind']=='person')}P/"
          f"{sum(1 for e in ner_only if entities[e]['kind']=='place')}O)")
    print(f"Geo: {ngeo} DARE-Stellen (entdoppelt) + {nline} Verlaufslinie(n) → geo/")
    occ = defaultdict(list)
    tot = 0
    for slug_, label, nr in WORKS:
        if not os.path.isdir(os.path.join(vault, "tools", ".cache", slug_)):
            print(f"  ! {slug_}: kein Cache, übersprungen"); continue
        r = build_volume(slug_, label, nr, vault, global_terms, token_terms, occ, os.path.join(REPO, "tei"))
        tot += r["tags"]
        print(f"  {r['file']:38} {r['pages']:>4} Seiten ({r['empty']} leer), {r['tags']:>4} Inline-Tags")
    # persistierter Belegindex: Entität → Vorkommen [Band, Token, Druckseite, Spalte, Offset]
    meta = {eid: {"name": e["name"], "kind": e["kind"], "cert": e["cert"], "source": e["source"]}
            for eid, e in entities.items() if eid in occ}
    json.dump({"entities": meta, "occ": {k: occ[k] for k in occ}},
              open(os.path.join(REPO, "data", "occurrences.json"), "w", encoding="utf-8"), ensure_ascii=False)
    nent = len(occ); ncov = sum(1 for e in entities if e in occ)
    print(f"Σ Inline-Tags: {tot} · Belegindex: {nent} Entitäten, {sum(len(v) for v in occ.values())} Vorkommen "
          f"→ data/occurrences.json")

if __name__ == "__main__":
    main()
