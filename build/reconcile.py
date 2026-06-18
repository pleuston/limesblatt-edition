#!/usr/bin/env python3
"""Token-frei: reichert die LLM-NER-Indizes mit Normdaten + Koordinaten an.

  Personen  → GND (lobid-Suche, konservativ: Vornamen-Initiale + RLK-Zeitfenster + Fach/Amt)
  Orte      → iDAI.gazetteer (gazId + Koordinaten); OSM-Nominatim als Koordinaten-Fallback

Liest data/ner_persons.json + ner_places.json, schreibt data/recon_persons.json +
data/recon_places.json (keyed by lowercased name → Treffer-Dict oder null). md5-gecacht
unter build/.cache/reconcile/, gedrosselt, idempotent (Eintrag-Ebene resümierbar). stdlib only.
"""
import json, os, re, time, hashlib, urllib.request, urllib.parse, urllib.error

REPO  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA  = os.path.join(REPO, "data")
CACHE = os.path.join(REPO, "build", ".cache", "reconcile"); os.makedirs(CACHE, exist_ok=True)
UA = "limes-vault-reconcile/1.0 (research; mailto:manuel.sassmann@gmail.com)"

def fetch(url, throttle):
    k = os.path.join(CACHE, hashlib.md5(url.encode()).hexdigest() + ".txt")
    if os.path.exists(k):
        return open(k, encoding="utf-8").read()
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode("utf-8", "replace")
            open(k, "w", encoding="utf-8").write(txt); time.sleep(throttle); return txt
        except urllib.error.HTTPError as e:
            if e.code == 404:
                open(k, "w", encoding="utf-8").write("{}"); return "{}"
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return "{}"

def fetch_j(url, throttle):
    try: return json.loads(fetch(url, throttle))
    except Exception: return {}

# ---------- Personen → GND ----------
PROF = re.compile(r'Arch[äaie]olog|Prähist|Vorgesch|Altertum|Antike|Provinzial|Epigraph|Numismat|'
                  r'Phil(olog|osoph)|Histor|Konservator|Offizier|General|Major|Oberst|Hauptmann|'
                  r'Beamt|Architekt|Ingenieur|Pfarr|Theolog|Geistlich|Politiker|Lehrer|Professor|'
                  r'Geograph|Bibliothekar|Museum|Forscher|Gelehrt|Kartograph|Geodät|Vermess', re.I)

def split_name(nm):
    nm = re.sub(r'\([^)]*\)', '', nm).strip()
    if ',' in nm:
        a, b = nm.split(',', 1); return a.strip(), b.strip()        # "Mommsen, Theodor"
    t = nm.split()
    if not t: return None, None
    if len(t) == 1: return t[0], None                               # bloßer Nachname (→ nur Register)
    return t[-1], t[0]                                               # "Theodor Mommsen"

def load_register():
    """{surname → [(name, {slug, gnd}), …]} aus dem kuratierten Personenregister der Edition."""
    by_sur = {}
    try: x = open(os.path.join(REPO, "registers", "persons.xml"), encoding="utf-8").read()
    except Exception: return by_sur
    for slug, body in re.findall(r'<person xml:id="p_([^"]+)">(.*?)</person>', x, re.S):
        names = [n.strip() for n in re.findall(r'<persName[^>]*>([^<]+)</persName>', body)]
        if not names: continue
        gm = re.search(r'type="GND">([^<]+)<', body)
        ent = {"slug": slug, "name": names[0], "gnd": gm.group(1) if gm else ""}
        for nm in names:
            by_sur.setdefault(nm.split()[-1].lower(), []).append((nm, ent))
    return by_sur

def recon_person(nm, reg):
    if re.search(r'\bder\s+(Große|Großen|Jüngere|Ältere|Fromme|Heilige|Erste|Zweite|Dritte)\b', nm, re.I):
        return None                                                 # historische Epitheta („Karl der Große")
    sur, fore = split_name(nm)
    if not sur or len(sur) < 3: return None
    fi = fore.rstrip('.')[:1].lower() if fore else ""
    # 1) kuratiertes Register der Edition (die RLK-Figuren mit geprüfter GND)
    cands = reg.get(sur.lower(), [])
    if cands:
        e = cands[0][1]
        if fi and len(cands) > 1:
            pick = [c for c in cands if c[0].split()[0][:1].lower() == fi]
            if pick: e = pick[0][1]
        return {"slug": e["slug"], "gnd": e["gnd"], "regName": e["name"], "src": "reg"}
    # 2) lobid GND – Vollnamen-Suche (Vorname Pflicht; Nachname-allein zu mehrdeutig)
    if not fore: return None
    d = fetch_j(f"https://lobid.org/gnd/search?q={urllib.parse.quote(fore + ' ' + sur)}"
                f"&filter=type:DifferentiatedPerson&size=10&format=json", 0.34)
    for m in d.get("member", []):
        pn = m.get("preferredName", "")
        if pn.split(",", 1)[0].strip().lower() != sur.lower(): continue   # Nachname wortgenau (kein Teilstring)
        fn = pn.split(",", 1)[1].strip() if "," in pn else ""
        if fi and fn[:1].lower() != fi: continue                    # Vornamen-Initiale Pflicht
        try: yob = int((m.get("dateOfBirth") or ["0"])[0][:4])
        except Exception: yob = 0
        if not (1800 <= yob <= 1882): continue                      # Autor in 1892–1903 ⇒ vor ~1882 geboren
        profs = " ".join(o.get("label", "") for o in m.get("professionOrOccupation", []))
        if not PROF.search(profs): continue                         # Fach/Amt-Filter gegen Fehltreffer
        yod = (m.get("dateOfDeath") or [""])[0][:4]
        return {"gnd": m.get("gndIdentifier"), "gndName": pn,
                "prof": profs[:70], "von": yob, "bis": yod, "src": "gnd"}
    return None

# ---------- Orte → iDAI / Nominatim ----------
GENERIC = {'afrika','alexandria','rom','italien','gallien','germanien','britannien','asien','europa',
           'rätien','raetien','obergermanien','niedergermanien','gallia','germania','reich','spanien',
           'frankreich','england','dakien','pannonien','noricum','illyrien','ägypten','syrien'}
NO_NOMI = {'flur', 'wald', 'gewann'}     # zu lokale Namen → kein Nominatim-Fallback

def nrm(s): return re.sub(r'[^a-zäöüß]', '', s.lower())

def recon_place(nm, kind):
    q = re.sub(r'\([^)]*\)', '', nm).strip()
    key = nrm(q)
    if len(key) < 3 or key in GENERIC: return None
    qtok = {nrm(t) for t in re.split(r'[\s\-]+', q) if len(nrm(t)) > 2}
    # 1) iDAI.gazetteer (Authority + Koordinaten)
    d = fetch_j(f"https://gazetteer.dainst.org/search.json?q={urllib.parse.quote(q)}&limit=6", 0.34)
    for r in d.get("result", []):
        names = [n.get("title", "") for n in r.get("names", [])]
        names.append(r.get("prefName", {}).get("title", ""))
        ntok = set()
        for x in names:
            ntok |= {nrm(t) for t in re.split(r'[\s\-]+', x) if len(nrm(t)) > 2}
        if not (key in ntok or (qtok and qtok <= ntok)): continue    # Wort-genauer Abgleich
        loc = r.get("prefLocation", {}).get("coordinates")
        if loc and len(loc) == 2:
            return {"gazId": r.get("gazId"), "gazName": r.get("prefName", {}).get("title"),
                    "geo": [round(loc[1], 5), round(loc[0], 5)], "src": "iDAI"}
    # 2) Nominatim (nur Koordinaten, Limes-Region) – nicht für Flur/Wald
    if (kind or "").lower() not in NO_NOMI:
        d = fetch_j(f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(q)}"
                    f"&format=json&limit=1&countrycodes=de,at&viewbox=7,51,12,47.5&bounded=1", 1.1)
        if isinstance(d, list) and d:
            return {"geo": [round(float(d[0]['lat']), 5), round(float(d[0]['lon']), 5)],
                    "nomName": d[0]['display_name'][:60], "src": "OSM"}
    return None

def load(p): return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
def save(p, o): json.dump(o, open(p, "w", encoding="utf-8"), ensure_ascii=False)

def main():
    persons = load(os.path.join(DATA, "ner_persons.json"))
    places  = load(os.path.join(DATA, "ner_places.json"))
    P_OUT = os.path.join(DATA, "recon_persons.json"); rp = load(P_OUT)
    L_OUT = os.path.join(DATA, "recon_places.json");  rl = load(L_OUT)

    reg = load_register()
    print(f"Personen reconcilen ({len(persons)}); Register: {sum(len(v) for v in reg.values())} Namen…")
    for i, it in enumerate(persons):
        k = it["name"].lower()
        if k in rp: continue
        rp[k] = recon_person(it["name"], reg)
        if (i + 1) % 40 == 0:
            save(P_OUT, rp); print(f"  {i+1}/{len(persons)} · GND-Treffer {sum(1 for v in rp.values() if v)}")
    save(P_OUT, rp)
    hit = sum(1 for v in rp.values() if v)
    print(f"✓ Personen: {hit}/{len(persons)} mit GND")

    print(f"Orte reconcilen ({len(places)})…")
    for i, it in enumerate(places):
        k = it["name"].lower()
        if k in rl: continue
        rl[k] = recon_place(it["name"], it.get("kind", ""))
        if (i + 1) % 50 == 0:
            save(L_OUT, rl)
            idai = sum(1 for v in rl.values() if v and v.get("src") == "iDAI")
            osm  = sum(1 for v in rl.values() if v and v.get("src") == "OSM")
            print(f"  {i+1}/{len(places)} · iDAI {idai} · OSM {osm}")
    save(L_OUT, rl)
    idai = sum(1 for v in rl.values() if v and v.get("src") == "iDAI")
    osm  = sum(1 for v in rl.values() if v and v.get("src") == "OSM")
    geo  = sum(1 for v in rl.values() if v and v.get("geo"))
    print(f"✓ Orte: {geo}/{len(places)} mit Koordinaten (iDAI {idai}, OSM {osm}); "
          f"iDAI-gazId {sum(1 for v in rl.values() if v and v.get('gazId'))}")

if __name__ == "__main__":
    main()
