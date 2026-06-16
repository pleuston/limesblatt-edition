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
from xml.sax.saxutils import escape, quoteattr

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
            "strecke": fm.get("strecke",""),
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
def build_terms(persons, places, dare, corpus_low=""):
    """term(exakte Schreibung) -> (kind, xmlid); nur eindeutige, distinkte Terme."""
    psn, plc = {}, {}
    for p in persons:
        s = p["surname"]
        if len(s) >= 5 and s not in STOP:
            psn.setdefault(s, []).append(p["id"])
    for pl in places:
        t = pl["term"]
        if len(t) >= 4:
            plc.setdefault(t, []).append(pl["id"])
    terms = {}
    for s, ids in psn.items():
        if len(ids) == 1: terms[s] = ("p", ids[0])     # mehrdeutige Nachnamen (z.B. Jacobi) auslassen
    for t, ids in plc.items():
        if len(ids) == 1 and t not in terms: terms[t] = ("pl", ids[0])
    # DARE-Kleinorte: spezifische, eindeutige Tokens, die nicht schon (Person/benannter Ort) belegt sind
    dterm = {}
    for f in dare:
        src = f.get("name", "") + " " + re.sub(r'^\*', '', f.get("ancient", ""))
        for tok in re.split(r"[\s/\-–,()]+", src):
            tok = tok.strip()
            if len(tok) >= 6 and tok[:1].isalpha() and tok.lower() not in GENERIC and tok not in terms:
                dterm.setdefault(tok, set()).add(f.get("id"))
    for tok, ids in dterm.items():
        if len(ids) == 1 and tok not in terms:
            if corpus_low and corpus_low.count(tok.lower()) > 40: continue   # zu häufig = Region/Gattungswort
            terms[tok] = ("dare", next(iter(ids)))
    # längere Terme zuerst matchen
    return dict(sorted(terms.items(), key=lambda kv: -len(kv[0])))

def tag_text(text, terms):
    spans = []
    for term, (kind, xid) in terms.items():
        for m in re.finditer(r"(?<![\wäöüÄÖÜß])" + re.escape(term) + r"(?![\wäöüÄÖÜß])", text):
            spans.append((m.start(), m.end(), kind, xid))
    spans.sort(key=lambda s: (s[0], -(s[1]-s[0])))
    chosen, last = [], -1
    for s in spans:
        if s[0] >= last: chosen.append(s); last = s[1]
    res, pos, n = [], 0, 0
    for st, en, kind, xid in chosen:
        res.append(escape(text[pos:st]))
        if kind == "dare":
            res.append(f'<placeName ref="dare:{xid}" cert="low">{escape(text[st:en])}</placeName>')
        else:
            tag = "persName" if kind == "p" else "placeName"
            res.append(f'<{tag} ref="#{xid}" cert="low">{escape(text[st:en])}</{tag}>')
        pos = en; n += 1
    res.append(escape(text[pos:]))
    return "".join(res), n

# ---------- Token-Reihenfolge ----------
def tokens(vault, slug_):
    d = os.path.join(vault, "tools", ".cache", slug_)
    toks = [os.path.basename(f)[:-4] for f in glob.glob(os.path.join(d, "*.txt"))]
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
<encodingDesc><editorialDecl><p>Diplomatische, unkorrigierte Wiedergabe der Fraktur-OCR (ALTO, UB Heidelberg). Ein <gi>pb</gi> je IIIF-Kachel (Doppelseite). Heuristisch erkannte Eigennamen tragen <att>cert</att>="low" und verweisen auf die Register.</p></editorialDecl></encodingDesc>
</teiHeader>"""

def build_volume(slug_, label, nr, vault, terms, outdir):
    toks, cdir = tokens(vault, slug_)
    surfaces, body, npages, ntags, nempty = [], [], 0, 0, 0
    for tok in toks:
        surfaces.append(f'<surface xml:id="f_{tok}" n="{escape(tok)}"><graphic url="{IIIF_IMG.format(slug=slug_, tok=tok)}"/></surface>')
        txt = open(os.path.join(cdir, f"{tok}.txt"), encoding="utf-8").read().strip()
        body.append(f'<pb n="{escape(tok)}" facs="#f_{tok}"/>')
        if not txt:
            body.append('<p><gap reason="ocr-empty"/></p>'); nempty += 1
        else:
            tagged, n = tag_text(txt, terms); ntags += n
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
    write_persons(persons, os.path.join(REPO, "registers", "persons.xml"))
    write_places(places,  os.path.join(REPO, "registers", "places.xml"))
    write_strecken(strecken, os.path.join(REPO, "registers", "strecken.xml"))
    ngeo, nline, dareprops = write_geo(vault, places, os.path.join(REPO, "geo"))
    corpus_low = " ".join(open(f, encoding="utf-8").read()
                          for f in glob.glob(os.path.join(vault, "tools", ".cache", "limesblatt*", "*.txt"))).lower()
    terms = build_terms(persons, places, dareprops, corpus_low)
    pids = {p["id"] for p in persons}
    dig = sum(1 for pl in places if pl["diggers"])
    dig_ok = sum(1 for pl in places for d in pl["diggers"] if d in pids)
    print(f"Register: {len(persons)} Personen, {len(places)} Orte, {len(strecken)} Strecken | Inline-Terme: {len(terms)}")
    print(f"Ausgräber: {dig} Kastelle verknüpft ({dig_ok} Personen-Refs aufgelöst) | "
          f"Porträts: {sum(1 for p in persons if p['portrait'])}P/{sum(1 for pl in places if pl['portrait'])}O | "
          f"EDH-Zahlen: {sum(1 for pl in places if pl['edh'])} | Strecke-Refs: {sum(1 for pl in places if pl['strecke_id'])}")
    print(f"Geo: {ngeo} DARE-Stellen (entdoppelt) + {nline} Verlaufslinie(n) → geo/")
    tot = 0
    for slug_, label, nr in WORKS:
        if not os.path.isdir(os.path.join(vault, "tools", ".cache", slug_)):
            print(f"  ! {slug_}: kein Cache, übersprungen"); continue
        r = build_volume(slug_, label, nr, vault, terms, os.path.join(REPO, "tei"))
        tot += r["tags"]
        print(f"  {r['file']:38} {r['pages']:>4} Seiten ({r['empty']} leer), {r['tags']:>4} Inline-Tags")
    print(f"Σ Inline-Tags: {tot}")

if __name__ == "__main__":
    main()
