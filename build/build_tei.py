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
from collections import defaultdict, Counter
from xml.sax.saxutils import escape, quoteattr
from urllib.parse import quote
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

# Bibliographie: (xml:id, Kurzname, Vollbeleg, OA-Digitalisat, OA-Label, Regex|None).
# Regex != None → die Referenz wird im TEI-Fließtext als <ref target="#id"> markiert
# (nur eindeutige NICHT-Personen-Referenzen, damit keine Kollision mit persName).
BIBLIO = [
    ("bib_westd", "Westdeutsche Zeitschrift", "Westdeutsche Zeitschrift für Geschichte und Kunst (Trier 1882–1907), hrsg. F. Hettner u. K. Lamprecht.",
     "https://digi.ub.uni-heidelberg.de/diglit/wzgk_kbl", "UB Heidelberg (OA)", r"Westd\w*\.?\s*(?:Zeitschr|Ztschr|Z\.)"),
    ("bib_korr", "Korrespondenzblatt der Westd. Zeitschrift", "Korrespondenzblatt der Westdeutschen Zeitschrift für Geschichte und Kunst (Trier 1882–1907).",
     "https://digi.ub.uni-heidelberg.de/diglit/wzgk_kbl", "UB Heidelberg (OA)", r"Korr\w*\.?\s*-?\s*[Bb]l"),
    ("bib_bonn", "Bonner Jahrbücher", "Bonner Jahrbücher (Jahrbücher des Vereins von Altertumsfreunden im Rheinlande), Bonn, seit 1842.",
     "https://journals.ub.uni-heidelberg.de/index.php/bjb", "UB Heidelberg Journals (OA)", r"Bonn\w*\.?\s*Jahrb"),
    ("bib_brambach", "W. Brambach, Corpus Inscriptionum Rhenanarum (1867)", "Wilhelm Brambach, Corpus Inscriptionum Rhenanarum. Elberfeld 1867.",
     "https://archive.org/details/bub_gb_pbJfXWjFgP4C", "Internet Archive (OA)", None),   # → CITE_RANGE
    ("bib_dragendorff", "H. Dragendorff, Terra sigillata (BJb 96/97, 1895)", "Hans Dragendorff, Terra sigillata. Ein Beitrag zur Geschichte der griechischen und römischen Keramik. Bonner Jahrbücher 96/97 (1895) 18–155.",
     "https://archive.org/details/terrasigillatae00draggoog", "Internet Archive (OA)", r"\bDrag(?:endorff)?\.?\s*\d"),
    ("bib_cil", "Corpus Inscriptionum Latinarum (CIL)", "Corpus Inscriptionum Latinarum. Berlin, seit 1863 (bes. Bd. XIII, Germania).",
     "https://cil.bbaw.de/", "CIL / BBAW (OA)", None),   # → CITE_RANGE (auch „Corp. III")
    ("bib_orl", "ORL — Der obergermanisch-raetische Limes des Römerreiches", "E. Fabricius, F. Hettner, O. v. Sarwey (Hrsg.), Der obergermanisch-raetische Limes des Römerreiches. 1894 ff.",
     "https://archive.org/details/derobergermanis00fabrgoog", "Internet Archive (OA)", r"\bO\.?\s?R\.?\s?L\b|obergerm\w*-?raet\w*\s+Limes\s+des"),
    ("bib_ephepigr", "Ephemeris Epigraphica", "Ephemeris Epigraphica. Corporis Inscriptionum Latinarum Supplementum. Rom/Berlin 1872 ff.",
     "https://archive.org/details/ephemerisepigrap04inst", "Internet Archive (OA)", r"\bEph(?:em)?\.?\s*[Ee]pigr"),
    ("bib_cohausen", "A. von Cohausen, Der römische Grenzwall in Deutschland (1884)", "August von Cohausen, Der römische Grenzwall in Deutschland. Wiesbaden: Kreidel, 1884.",
     "https://digi.ub.uni-heidelberg.de/diglit/cohausen1884ga", "UB Heidelberg (OA)", None),   # Autor → Personenregister
    ("bib_steiner", "P. Steiner, Römische Inschriften", "P. Steiner u. a., zu rheinischen/germanischen Inschriften.", "", "", r"\bSteiner\b"),
    ("bib_becker", "J. Becker, Inschriften-/Limesbeiträge", "J. Becker, Beiträge zur Limes- und Inschriftenforschung.", "", "", r"\bBecker\b"),
    ("bib_tischler", "O. Tischler, Fibel-Typologie", "Otto Tischler, zur Typologie der Fibeln (La-Tène/provinzialrömisch).", "", "", r"\bTischler\b"),
    ("bib_haug", "F. Haug, Inschriften Südwestdeutschlands", "Ferdinand Haug, zu den römischen Inschriften Südwestdeutschlands.", "", "", r"\bHaug\b"),
    # --- erweiterte Zitations-Abdeckung (token-frei im OCR belegt): Zeitschriften + antike Quellen ---
    ("bib_archanz", "Archäologischer Anzeiger", "Archäologischer Anzeiger (Beiblatt zum Jahrbuch des Deutschen Archäologischen Instituts), Berlin.",
     "https://www.digi.ub.uni-heidelberg.de/diglit/aa", "UB Heidelberg (OA)", r"Arch\w*\.?\s*Anz"),
    ("bib_nassau", "Annalen des Vereins für Nassauische Altertumskunde", "Annalen des Vereins für Nassauische Altertumskunde und Geschichtsforschung, Wiesbaden, seit 1830.",
     "", "", r"Nass\w*\.?\s*Ann|Annalen[^.]{0,14}[Nn]assau"),
    ("bib_wuertt", "Württembergische Vierteljahrshefte", "Württembergische Vierteljahrshefte für Landesgeschichte, Stuttgart, seit 1878.",
     "", "", r"Württ\w*\.?\s*Viert"),
    ("bib_hermes", "Hermes. Zeitschrift für classische Philologie", "Hermes. Zeitschrift für classische Philologie, Berlin, seit 1866.",
     "", "", r"\bHermes\s+[IVXLC0-9]"),
    ("bib_tacitus", "Tacitus (Germania / Annales / Historiae)", "P. Cornelius Tacitus, bes. Germania, Annales, Historiae (antike Quelle).",
     "", "", r"\bTacit(?:us|i)?\b|\bTac\.\s"),
    ("bib_ptolemaeus", "Klaudios Ptolemaios, Geographie", "Klaudios Ptolemaios, Geographike Hyphegesis (antike Quelle, bes. Buch II).",
     "", "", r"Ptolem\w*|\bPtol\."),
    ("bib_ammianus", "Ammianus Marcellinus", "Ammianus Marcellinus, Res gestae (antike Quelle).",
     "", "", r"Ammian\w*"),
    ("bib_notitia", "Notitia Dignitatum", "Notitia Dignitatum (spätantikes Staatshandbuch, bes. occ. — Limesgarnisonen).",
     "", "", r"Notitia\s+[Dd]ign|Not\.?\s*[Dd]ign\w*"),
    ("bib_peutinger", "Tabula Peutingeriana", "Tabula Peutingeriana (spätantike Straßenkarte).",
     "", "", r"(?:Tab\.?\s*)?Peuting\w*"),
    ("bib_itinant", "Itinerarium Antonini", "Itinerarium Antonini Augusti (spätantikes Straßenverzeichnis).",
     "", "", r"Itinerar\w*|Itin\.?\s*Anton\w*"),
]

# IIIF-Manifeste der OA-Digitalisate → im Leser einbettbares Faksimile (work-/Beispielband-Ebene).
BIB_IIIF = {
    "bib_westd":       "https://digi.ub.uni-heidelberg.de/diglit/iiif/wzgk_kbl1894/manifest",   # Korresp.-Bl. 13.1894
    "bib_korr":        "https://digi.ub.uni-heidelberg.de/diglit/iiif/wzgk_kbl1894/manifest",
    "bib_bonn":        "https://iiif.archive.org/iiif/bonnerjahrbcher00rheigoog/manifest.json",
    "bib_brambach":    "https://iiif.archive.org/iiif/bub_gb_pbJfXWjFgP4C/manifest.json",
    "bib_dragendorff": "https://iiif.archive.org/iiif/terrasigillatae00draggoog/manifest.json",
    "bib_cohausen":    "https://digi.ub.uni-heidelberg.de/diglit/iiif/cohausen1884bd1/manifest",
    "bib_orl":         "https://iiif.archive.org/iiif/derobergermanis00fabrgoog/manifest.json",
}

# Propylaeum SEARCH (FID Altertumswissenschaften, UB Heidelberg) — Discovery je zitiertem Werk:
# gezielte Suchanfrage (sonst aus dem Titel abgeleitet). Schließt die Edition an die
# Fachinformations-Infrastruktur an (vgl. Propylaeum-VITAE für Personen, EDH für Inschriften).
# Kurze, distinktive Suchanfragen — VuFind verknüpft Wörter mit UND, zu lange Queries → 0 Treffer.
PROPY = "https://www.propylaeumsearch.de/propylaeumsearch/Search/Results?type=AllFields&lookfor="
BIB_PROPY = {
    "bib_westd": "Westdeutsche Zeitschrift Geschichte",
    "bib_korr": "Korrespondenzblatt Westdeutsche Zeitschrift",
    "bib_bonn": "Bonner Jahrbücher",
    "bib_brambach": "Corpus Inscriptionum Rhenanarum",
    "bib_dragendorff": "Dragendorff Terra sigillata",
    "bib_cil": "Corpus Inscriptionum Latinarum",
    "bib_orl": "obergermanisch-raetische Limes Römerreiches",
    "bib_ephepigr": "Ephemeris Epigraphica",
    "bib_cohausen": "Cohausen römische Grenzwall",
    "bib_archanz": "Archäologischer Anzeiger",
    "bib_nassau": "Annalen Nassauische Altertumskunde",
    "bib_wuertt": "Württembergische Vierteljahrshefte",
    "bib_hermes": "Hermes Zeitschrift Philologie",
    "bib_tacitus": "Tacitus Germania",
    "bib_ptolemaeus": "Ptolemaeus Geographie",
    "bib_ammianus": "Ammianus Marcellinus",
    "bib_notitia": "Notitia Dignitatum",
    "bib_peutinger": "Tabula Peutingeriana",
    "bib_itinant": "Itinerarium Antonini",
    "bib_steiner": "Steiner römische Inschriften",
    "bib_becker": "Becker römische Inschriften",
    "bib_tischler": "Tischler Fibeln",
    "bib_haug": "Haug römische Inschriften",
}
def propy_url(bid, name):
    return PROPY + quote(BIB_PROPY.get(bid) or name)

# Inschriftennummern → <citedRange> (1 Capture-Gruppe = die Nummer/Sigle):
CITE_RANGE = [
    ("bib_cil",      re.compile(r"\b(?:C\.?\s?I\.?\s?L\.?|Corp\.)\s+([IVXLC]+(?:[\s,.]+(?:[Pp]\.\s*)?\d+)*)", re.I)),
    ("bib_brambach", re.compile(r"\bBramb(?:ach)?\.?\s+(?:Nr\.?\s*)?(\d{2,4}(?:\s*[.,]\s*\d{2,4})*)", re.I)),
    ("bib_brambach", re.compile(r"\bC\.?\s?I\.?\s?Rh\.?\s+(?:Nr\.?\s*)?(\d{2,4}(?:\s*[.,]\s*\d{2,4})*)", re.I)),
]

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

CITE_RX = [(bid, re.compile(rx, re.I)) for bid, _n, _f, _o, _l, rx in BIBLIO if rx]
SELF_RX = re.compile(r"Limesbl\w*\.?\s*S\.?\s*(\d{1,4})", re.I)   # interner Selbstverweis (Seite)
REGNUM_RX = re.compile(r"(?<![\d,.\-/])(\d{1,3})(?![\d]|\s*(?:cm|mm|m\b|km|kg))")  # Registerzahl = Spalte
REGISTER_START = "Verzeichnis der Mitarbeiter"   # Hintzelmanns Gesamtregister, Bd. 8: ab HIER sind
# bloße Zahlen Spaltenverweise. NICHT beim Titel »Register zu Nr. 1–35« beginnen — dessen 1 und 35
# sind HEFT-Nummern und würden sonst auf die Spalten 1 und 35 zeigen.
REPORT_RX = re.compile(r"(?:Forts\w*|Fortsetzung|[Vv]gl\.|siehe|s\.)\s+(?:zu\s+)?(Nr\.\s*(\d{1,3}))", re.I)  # Bericht-Querverweis

def tag_page(text, terms, cites=CITE_RX, ranges=CITE_RANGE, selfmap=None, reportmap=None, regnums=None):
    """terms: {term:(kind,id,cert)} → persName/placeName; cites → <ref target>;
    ranges (id,regex mit 1 Gruppe) → <ref><citedRange>; selfmap {Druckseite:pb-id}
    → interner <ref> auf „Limesblatt S. NNN". Non-overlapping, längste zuerst."""
    spans = []
    for term, (kind, xid, cert) in terms.items():
        for m in re.finditer(r"(?<![\wäöüÄÖÜß])" + re.escape(term) + r"(?![\wäöüÄÖÜß])", text):
            spans.append((m.start(), m.end(), kind, xid, cert))
    for bid, rx in cites:
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), "bibl", bid, ""))
    for bid, rx in ranges:                                 # Inschriftennummer → citedRange
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), "range", bid, (m.start(1), m.end(1))))
    if selfmap:
        for m in SELF_RX.finditer(text):
            tgt = selfmap.get(int(m.group(1)))
            if tgt:
                spans.append((m.start(), m.end(), "self", tgt, ""))
    if selfmap and regnums is not None:
        # NUR im Registerbereich (Hintzelmanns »Register zu Nr. 1–35«, Bd. 8): dort sind bloße
        # Zahlen Spaltenverweise — »Anthes 442 (Palissaden…), 443, 464«. Sein eigener Kopf sagt:
        # »Die Ziffern bezeichnen die Spalten.« Korpusweit wäre das fatal (Maße, Jahre, Funde),
        # deshalb ist es strikt begrenzt — und zwar ZEICHENGENAU: `regnums` ist der Offset, ab
        # dem das Register beginnt. Spalte 959a trägt beides, den Schluss eines Feldberichts und
        # den Registerkopf; ohne den Offset würde »Wachtposten 12« zu einem Verweis auf Spalte 12.
        for m in REGNUM_RX.finditer(text):
            if m.start(1) < regnums: continue
            tgt = selfmap.get(int(m.group(1)))
            if tgt:
                spans.append((m.start(1), m.end(1), "self", tgt, ""))
    if reportmap:
        for m in REPORT_RX.finditer(text):              # nur das „Nr. NN" markieren (Gruppe 1)
            tgt = reportmap.get(m.group(2))             # Gruppe 2 = die Bericht-Nummer
            if tgt:
                spans.append((m.start(1), m.end(1), "self", tgt, ""))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen, last = [], -1
    for s in spans:
        if s[0] >= last: chosen.append(s); last = s[1]
    res, pos, hits = [], 0, []
    for st, en, kind, xid, extra in chosen:
        res.append(escape(text[pos:st]))
        if kind == "bibl":
            res.append(f'<ref type="bibl" target="#{xid}">{escape(text[st:en])}</ref>')
        elif kind == "range":
            g0, g1 = extra
            res.append(f'<ref type="bibl" target="#{xid}">{escape(text[st:g0])}'
                       f'<citedRange>{escape(text[g0:g1])}</citedRange>{escape(text[g1:en])}</ref>')
        elif kind == "self":
            res.append(f'<ref type="internal" target="#{xid}">{escape(text[st:en])}</ref>')
        elif kind == "dare":
            res.append(f'<placeName ref="dare:{xid}" cert="{extra}">{escape(text[st:en])}</placeName>')
        else:
            tag = "persName" if kind == "p" else "placeName"
            res.append(f'<{tag} ref="#{xid}" cert="{extra}">{escape(text[st:en])}</{tag}>')
        pos = en; hits.append((xid, kind, st))
    res.append(escape(text[pos:]))
    return "".join(res), hits

def _structure(html, paras):
    """Die **ganze Spalte** wird in einem Stück getaggt (volle Trefferquote, saubere
    spalten-relative Offsets); danach Absatz-/Zeilenstruktur **nach Wort-Position** ins
    fertige HTML einsetzen, ohne in Tags zu schneiden: `</p><p>` an Absatzgrenzen, `<lb/>`
    an Original-Druckzeilen-Grenzen. `paras` = Absätze mit `\\n`-getrennten Druckzeilen."""
    para_ends, line_ends, acc = set(), set(), 0
    for pi, pr in enumerate(paras):
        lines = pr.split("\n")
        for li, ln in enumerate(lines):
            acc += len(ln.split())
            if li < len(lines) - 1:
                line_ends.add(acc)                 # Druckzeilen-Umbruch → <lb/>
        if pi < len(paras) - 1:
            para_ends.add(acc)                     # Absatz-Umbruch → </p><p>
    if not para_ends and not line_ends:
        return html
    out, depth, words, inword = [], 0, 0, False
    for ch in html:
        if ch == "<":
            depth += 1; out.append(ch)
        elif ch == ">":
            depth -= 1; out.append(ch)
        elif depth == 0 and ch.isspace():
            if inword:
                if words in para_ends:
                    out.append("</p><p>")
                elif words in line_ends:
                    out.append("<lb/>")
            inword = False; out.append(ch)
        elif depth == 0:
            if not inword:
                inword = True; words += 1
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)

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

def build_volume(slug_, label, nr, vault, global_terms, token_terms, occ, outdir, selfmap=None, reportmap=None):
    toks, cdir = tokens(vault, slug_)
    surfaces, body, npages, ntags, nempty = [], [], 0, 0, 0

    in_register = [False]   # ab Hintzelmanns »Register zu Nr. 1–35« bis Bandende (nur Bd. 8)

    def tag(text, printed, col, baseoff=0):
        """Spaltentext markieren: korpusweite Vault-Begriffe + auf dieses Token verankerte
        NER-Begriffe + Literatur-/Selbst-/Bericht-Verweise; Treffer mit (spalten-relativem)
        Offset im Belegindex sammeln. `baseoff` = Startoffset des Absatzes in der Spalte.
        Ab dem Registerbeginn zusätzlich: bloße Zahlen = Spaltenverweise (regnums)."""
        if REGISTER_START in text:            # Registerkopf in DIESER Spalte → ab hier
            regfrom = text.index(REGISTER_START) + len(REGISTER_START); in_register[0] = True
        else:
            regfrom = 0 if in_register[0] else None    # None = Register-Modus aus
        terms = dict(global_terms); terms.update(token_terms.get((nr, tok), {}))
        html_, hits = tag_page(text, terms, selfmap=selfmap, reportmap=reportmap,
                               regnums=regfrom)
        for eid, kind, off in hits:
            occ[eid].append([nr, tok, printed, col, baseoff + off])
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
                paras = c.get("paras") or ([c["text"]] if (c.get("text") or "").strip() else [])
                paras = [p for p in (x.strip() for x in paras) if p]
                if not paras:
                    body.append('<p><gap reason="ocr-empty"/></p>'); nempty += 1
                else:
                    flat = " ".join(p.replace("\n", " ") for p in paras)   # ganze Spalte zusammengezogen
                    tagged, n = tag(flat, printed, lbl); ntags += n        # einmal taggen → volle Trefferquote
                    body.append(f'<p>{_structure(tagged, paras)}</p>')     # Absätze (</p><p>) + Druckzeilen (<lb/>)
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

def write_bibl(path):
    """Bibliographie-Register <listBibl> — Ziel der <ref target="#bib_…"> im Fließtext."""
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="register-bibl"><teiHeader><fileDesc>',
         '<titleStmt><title>Bibliographie — Limesblatt-Edition</title></titleStmt>',
         '<publicationStmt><availability status="free"><licence target="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</licence></availability></publicationStmt>',
         '<sourceDesc><p>Die im Limesblatt zitierte Apparatur, aufgelöst zu Vollbelegen und Open-Access-Digitalisaten (UB Heidelberg u. a.).</p></sourceDesc>',
         '</fileDesc></teiHeader><standOff><listBibl>']
    for bid, name, full, oa, oal, rx in BIBLIO:
        L.append(f'<bibl xml:id="{bid}"><title>{escape(name)}</title><note>{escape(full)}</note>')
        if oa:
            L.append(f'<ref type="oa" target="{escape(oa)}">{escape(oal)}</ref>')
        if bid in BIB_IIIF:
            L.append(f'<ref type="iiif-manifest" target="{escape(BIB_IIIF[bid])}"/>')
        L.append(f'<ref type="propylaeum" target="{escape(propy_url(bid, name))}">Propylaeum SEARCH</ref>')
        L.append('</bibl>')
    L.append('</listBibl></standOff></TEI>')
    open(path, "w", encoding="utf-8").write("\n".join(L))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=os.environ.get("VAULT_ROOT", os.path.join(REPO, "..", "limes")))
    ap.add_argument("--dump-entities", action="store_true", help="Entitäts-Universum dumpen + raus")
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
    global_terms = dict({**g["global_terms"], **dterms})
    # Bounded Fuzzy: OCR-Garble-Varianten (Editierdistanz 1) bekannter Begriffe (≥7 Zeichen),
    # nur seltene (1–2×), eindeutige, großgeschriebene Korpus-Tokens → zusätzliche Low-Cert-Treffer.
    ctok = Counter(re.findall(r"[A-Za-zÄÖÜäöüß]{4,}",
                   " ".join(open(f, encoding="utf-8").read()
                            for f in glob.glob(os.path.join(vault, "tools", ".cache", "limesblatt*", "*.txt")))))
    low2forms = defaultdict(list)
    for w, n in ctok.items(): low2forms[w.lower()].append((w, n))
    known_low = {t.lower() for t in global_terms}
    AL = "abcdefghijklmnopqrstuvwxyzäöüß"
    def _edits1(w):
        sp = [(w[:i], w[i:]) for i in range(len(w) + 1)]
        return (set(a + b[1:] for a, b in sp if b) | set(a + b[1] + b[0] + b[2:] for a, b in sp if len(b) > 1)
                | set(a + c + b[1:] for a, b in sp if b for c in AL) | set(a + c + b for a, b in sp for c in AL))
    var2ent = {}
    for term, (kind, eid, cert) in list(global_terms.items()):
        if len(term) < 7 or kind == "dare": continue
        for v in _edits1(term.lower()):
            var2ent.setdefault(v, set()).add((kind, eid))
    fuzz = 0
    for v, ents in var2ent.items():
        if len(ents) != 1 or v in known_low: continue
        forms = [(w, n) for w, n in low2forms.get(v, []) if 1 <= n <= 2 and w[:1].isupper() and w not in global_terms]
        if len(forms) == 1:
            kind, eid = next(iter(ents)); global_terms[forms[0][0]] = (kind, eid, "low"); fuzz += 1
    # ---- kuratierte Korpus-weite Promotion (build/promote.json) ----
    # Recall-Stufe aus dem Entitäts-Recall-Audit (tools/entity_audit.py + Workflow): Formen, die
    # anderswo schon als Entität getaggt sind, aber unterhalb der konservativen Promotion-Schwelle
    # (≥7 Ort/≥6 Person) lagen (z. B. „Gmünd", „Main", „Pfünz", „Jacobi"). {form:[kind,id,cert]};
    # nur übernommen, wenn die Ziel-ID als Entität existiert. STOP-Homographe bleiben außen vor.
    pj = os.path.join(os.path.dirname(os.path.abspath(__file__)), "promote.json")
    prom = 0
    if os.path.exists(pj):
        for form, spec in json.load(open(pj, encoding="utf-8")).items():
            kind, eid, cert = spec[0], spec[1], spec[2]
            if form in STOP:
                continue
            if eid not in g["entities"]:
                # optionaler 4. Eintrag = Name → minimale Entität prägen (z. B. GENERIC-Landschaften
                # wie Taunus/Wetterau/Odenwald, die der Gazetteer bewusst nicht aus den NER-Listen prägt)
                if len(spec) >= 4 and spec[3]:
                    g["entities"][eid] = {"id": eid, "kind": "person" if kind == "p" else "place",
                                          "name": spec[3], "cert": cert, "source": "recall"}
                    g["ner_only"].add(eid)        # → Register-Stub (write_ner), damit @ref auflöst
                else:
                    continue
            global_terms[form] = (kind, eid, cert); prom += 1
    global_terms = dict(sorted(global_terms.items(), key=lambda kv: -len(kv[0])))
    print(f"Fuzzy-Garble-Varianten (Editierdistanz 1, Low-Cert): {fuzz} · kuratierte Promotion: {prom}")
    token_terms, entities, ner_only = g["token_terms"], g["entities"], g["ner_only"]
    if "--dump-entities" in sys.argv:
        forms = {}
        for t, (k, e, c) in global_terms.items(): forms.setdefault(e, []).append(t)
        json.dump({"entities": {i: {"kind": e["kind"], "name": e["name"]} for i, e in entities.items()},
                   "global_terms": list(global_terms), "entity_forms": forms},
                  open(os.path.join(vault, "tools", ".cache", "gazetteer_entities.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        print(f"--dump-entities: {len(entities)} Entitäten → tools/.cache/gazetteer_entities.json"); return
    write_ner(entities, ner_only, os.path.join(REPO, "registers", "ner.xml"))
    write_bibl(os.path.join(REPO, "registers", "bibliography.xml"))
    print(f"Register: {len(persons)} Personen, {len(places)} Orte, {len(strecken)} Strecken")
    print(f"Gazetteer: {len(global_terms)} korpusweite Begriffe (+{len(dterms)} DARE), "
          f"{sum(len(v) for v in token_terms.values())} seiten-verankerte NER-Begriffe, "
          f"{len(ner_only)} NER-only-Entitäten ({sum(1 for e in ner_only if entities[e]['kind']=='person')}P/"
          f"{sum(1 for e in ner_only if entities[e]['kind']=='place')}O)")
    print(f"Geo: {ngeo} DARE-Stellen (entdoppelt) + {nline} Verlaufslinie(n) → geo/")
    # Globale Druckseiten→<pb>-Karte für interne „Limesblatt S. NNN"-Selbstverweise
    # Spaltennummer → Anker. ACHTUNG: 32 von 931 Nummern haben MEHRERE Kandidaten, weil die
    # OCR Spaltenköpfe verliest („063" als 3, „633" als 33, „155" als 150) und weil Beilagen
    # (311a, 455a, 679a, 935a) Nummern der Hauptfolge dublieren. Ein blindes setdefault nimmt
    # den erstbesten — aufgefallen an Hintzelmanns Register, dessen Verweise dann auf
    # Beilagenseiten zeigten. Auflösung: das TOKEN ist die Wahrheit, nicht die Kopfzahl. Das
    # Limesblatt zählt Spalten, jede Druckseite trägt zwei; das Token ist die erste (ungerade).
    # Also gilt: Token T, Spalte a → Nummer T · Spalte b → T+1. Wer dazu passt, gewinnt.
    cands = {}
    for slug_, label, nr in WORKS:
        cdir = os.path.join(vault, "tools", ".cache", slug_)
        for jf in glob.glob(os.path.join(cdir, "*.alto.json")):
            try: cj = json.load(open(jf, encoding="utf-8"))
            except Exception: continue
            tk = os.path.basename(jf)[:-len(".alto.json")]
            for c in cj.get("columns", []):
                pn = str(c.get("printed_no", ""))
                if pn.isdigit() and c.get("printed_src") in ("head", "inferred"):
                    cands.setdefault(int(pn), []).append((tk, c["label"]))
    OFF = {"a": 0, "b": 1, "c": 2}
    def _konsistent(pn, tk, lab):
        """Deckt sich die Nummer mit dem Token? (Beilagen wie »679a« sind nie konsistent.)"""
        if not tk.isdigit(): return False
        return pn == int(tk) + OFF.get(lab, 99)
    selfmap, selfmap_ambig = {}, {}
    for pn, cs in cands.items():
        gut = [c for c in cs if _konsistent(pn, *c)]
        pick = gut[0] if gut else cs[0]
        if len(cs) > 1: selfmap_ambig[pn] = {"gewaehlt": pick, "kandidaten": cs, "token_konsistent": bool(gut)}
        selfmap[pn] = f"pb_{pick[0]}_{pick[1]}"
    # Die Kopfzahl ist unzuverlässig: Fraktur verliest 6→0 („155b" meldet 150 statt 156, „761a"
    # meldet 701 statt 761), und manche Spalte wiederholt schlicht die Zahl ihrer Nachbarin
    # (775b/823b/919b). Das TOKEN dagegen ist die Wahrheit — es kommt aus der UB-Paginierung.
    # Deshalb wird jede token-konsistente Nummer ZUSÄTZLICH eingetragen, auch wenn die OCR sie
    # nie gelesen hat. Aufgefallen an Hintzelmanns Register von 1903, dessen Verweise auf genau
    # diese Spalten ins Leere zeigten.
    n_erg = 0
    for tk, lab in {(tk, lab) for cs in cands.values() for tk, lab in cs}:
        if not tk.isdigit() or lab not in OFF: continue
        pn = int(tk) + OFF[lab]
        if pn not in selfmap:
            selfmap[pn] = f"pb_{tk}_{lab}"; n_erg += 1
    # TAFELSEITEN. Vier nummerierte Seiten tragen keinen Text: 211 (Bd. 2) sowie 467, 541, 559
    # (Bd. 4). Die UB liefert für sie ein ALTO mit HTTP 200, aber leer (745 statt ~76.000 Bytes) —
    # sie sind HANDSCHRIFTLICH IN KURRENT beschriftet, da ist nichts zu erkennen, auch nicht per
    # Re-OCR. Ohne Spalten entstehen sie hier gar nicht erst, und ihre Nummern fehlten der selfmap.
    # Sie sind aber zitierbar: Hintzelmann verweist 1903 auf Sp. 559 (»Gundelshalm«) — dort steht
    # Fig. 3 »… auf der Höhe bei Gundelshalm«. Der Faksimile-Anker pb_<tok>_a existiert längst
    # (type="token"); die Tafel trägt im Kopf BEIDE Spaltennummern (»— 559 — Limesblatt. — 560 —«),
    # deshalb zeigen beide auf dieselbe Seite.
    n_taf, tafeln = 0, []
    for slug_, label, nr in WORKS:
        cdir = os.path.join(vault, "tools", ".cache", slug_)
        for tf in glob.glob(os.path.join(cdir, "*.txt")):
            tk = os.path.basename(tf)[:-4]
            if not tk.isdigit(): continue
            if os.path.exists(os.path.join(cdir, f"{tk}.alto.json")): continue   # hat Spalten → kein Tafelfall
            for off in (0, 1):
                pn = int(tk) + off
                if pn not in selfmap:
                    selfmap[pn] = f"pb_{tk}_a"; n_taf += 1
            tafeln.append(f"{label}/{tk}")
    if tafeln:
        print(f"  Tafelseiten ohne Text (leeres ALTO, Kurrent-Beschriftung): {', '.join(sorted(tafeln))} "
              f"→ {n_taf} Spaltennummern auf das Faksimile gezeigt")
    if selfmap_ambig or n_erg:
        _kein = [n for n, v in selfmap_ambig.items() if not v["token_konsistent"]]
        print(f"  selfmap: {len(selfmap_ambig)} mehrdeutige Nummern über das Token aufgelöst · "
              f"{n_erg} aus dem Token ergänzt (Kopfzahl verlesen) · {len(selfmap)} Spalten gesamt"
              + (f" · {len(_kein)} ohne konsistenten Kandidaten: {sorted(_kein)}" if _kein else ""))
    # Bericht-Nr. → Startseiten-<pb> für „Forts. zu Nr. NN": erst der strenge TOC-Report
    # (hohe Präzision), dann ergänzend ein direkter Spaltenanfang-Scan (höhere Vollständigkeit).
    reportmap = {}
    tocj = os.path.join(vault, "tools", "toc.json")          # vollständig + token+Spalte genau
    if os.path.exists(tocj):
        try:
            for r in json.load(open(tocj, encoding="utf-8")).get("reports", []):
                if r.get("token"):
                    reportmap.setdefault(str(r["num"]), f"pb_{r['token']}_{r.get('col') or 'a'}")
        except Exception:
            pass
    tocf = os.path.join(vault, "tools", ".cache", "toc_report.md")
    if os.path.exists(tocf):
        for m in re.finditer(r'- S\. (\S+): \*\*(\d+)\.\*\*', open(tocf, encoding="utf-8").read()):
            reportmap.setdefault(m.group(2), f"pb_{m.group(1)}_a")
    head_rx = re.compile(r'^\s*(\d{1,3})[._]\s+[A-ZÄÖÜ]')                 # „24. Walldürn …" am Spaltenanfang
    loose_rx = re.compile(r'(?<![\d.,])(\d{1,3})[._]\s+[A-ZÄÖÜ][a-zäöü]')  # „N. Großwort" irgendwo
    loose = defaultdict(set)
    for slug_, label, nr in WORKS:
        cdir = os.path.join(vault, "tools", ".cache", slug_)
        for jf in sorted(glob.glob(os.path.join(cdir, "*.alto.json"))):
            try: cj = json.load(open(jf, encoding="utf-8"))
            except Exception: continue
            tk = os.path.basename(jf)[:-len(".alto.json")]
            for c in cj.get("columns", []):
                txt = c.get("text") or ""
                m = head_rx.match(txt[:40])
                if m:
                    reportmap.setdefault(m.group(1), f"pb_{tk}_{c['label']}")
                for mm in loose_rx.finditer(txt):
                    loose[mm.group(1)].add(f"pb_{tk}_{c['label']}")
    for num, pgs in loose.items():                          # nur EINDEUTIGE lose Überschriften ergänzen
        if num not in reportmap and len(pgs) == 1:
            reportmap[num] = next(iter(pgs))
    occ = defaultdict(list)
    tot = 0
    for slug_, label, nr in WORKS:
        if not os.path.isdir(os.path.join(vault, "tools", ".cache", slug_)):
            print(f"  ! {slug_}: kein Cache, übersprungen"); continue
        r = build_volume(slug_, label, nr, vault, global_terms, token_terms, occ, os.path.join(REPO, "tei"), selfmap, reportmap)
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
