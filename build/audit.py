#!/usr/bin/env python3
"""
Vollständigkeits-Audit: zählt referenz-/namens-artige Spans im TEI-Fließtext,
die NOCH NICHT in <ref>/<persName>/<placeName> ausgezeichnet sind. Treibt die
Konvergenz-Schleife: jede Runde werden die häufigsten ungelinkten Muster gelinkt,
bis nur noch Rauschen übrig bleibt.

  python3 build/audit.py
"""
import glob, os, re, collections, html

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
TAG = re.compile(r'<(persName|placeName|ref)\b[^>]*>.*?</\1>', re.S)   # bereits ausgezeichnet

# Referenz-Indikatoren (sollten gelinkt sein): (Label, Regex)
CITE_PAT = [
    ("Westd. Zeitschrift", r"Westd\w*\.?\s*(?:Zeitschr|Ztschr|Z\.)"),
    ("Korrespondenzblatt", r"Korr\w*\.?\s*-?\s*[Bb]l"),
    ("Bonner Jahrb.",      r"Bonn\w*\.?\s*Jahrb"),
    ("CIL",                r"\bC\.?\s?I\.?\s?L\.?\s+[IVXLC]"),
    ("Brambach",           r"\bBramb(?:ach)?\.?"),
    ("Dragendorff-Form",   r"\bDrag(?:endorff)?\.?\s*\d"),
    ("Cohausen",           r"\bCohausen\b"),
    ("Eph. Epigr.",        r"\bEph(?:em)?\.?\s*Epigr"),
    ("Corpus/Corp.",       r"\bCorp\.\s*(?:I|inscr)"),
    ("Mommsen",            r"\bMommsen\b"),
    ("Hübner",             r"\bHübner\b|\bHuebner\b"),
    ("Zangemeister",       r"\bZangemeister\b"),
    ("Steiner",            r"\bSteiner\b"),
    ("Becker",             r"\bBecker\b"),
    ("Riese",              r"\bRiese\b"),
    ("Haug",               r"\bHaug\b"),
    ("Selbstverweis S.NNN", r"Limesbl\w*\.?\s*S\.?\s*\d"),
    ("Bericht-Querverweis Nr.", r"(?:Forts\w*|Fortsetzung|[Vv]gl\.|siehe|s\.)\s+(?:zu\s+)?Nr\.\s*\d"),
    ("ORL",                r"obergerm\w*-?raet\w*\s+Limes\s+des"),
]


def main():
    files = sorted(glob.glob(os.path.join(REPO, "tei", "*.xml")))
    cnt = collections.Counter(); samp = collections.defaultdict(list)
    pats = [(lbl, re.compile(rx, re.I)) for lbl, rx in CITE_PAT]
    total_pages = 0
    for f in files:
        t = open(f, encoding="utf-8").read()
        body = (re.search(r"<body>(.*)</body>", t, re.S) or re.search(r"(.*)", t, re.S)).group(1)
        for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", body, re.S):
            total_pages += 1
            inner = m.group(1)
            untag = TAG.sub("  ", inner)                # ausgezeichnete Spans entfernen
            untag = re.sub(r"<[^>]+>", " ", untag)
            untag = html.unescape(untag)
            for lbl, rx in pats:
                for mm in rx.finditer(untag):
                    cnt[lbl] += 1
                    if len(samp[lbl]) < 3:
                        s = untag[max(0, mm.start() - 18):mm.end() + 18]
                        samp[lbl].append(re.sub(r"\s+", " ", s).strip())
    tot = sum(cnt.values())
    print(f"=== Vollständigkeits-Audit: {tot} ungelinkte Referenz-Indikatoren ({len(files)} Bände) ===")
    for lbl, n in cnt.most_common():
        print(f"  {lbl:24} {n:>4}   z. B. … {samp[lbl][0] if samp[lbl] else ''}")
    if not tot:
        print("  ✓ nichts mehr zu linken.")
    return tot


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
