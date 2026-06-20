#!/usr/bin/env python3
"""
gazetteer.py — vereinte, token-freie Lexik für das Inline-Tagging der Edition.
================================================================================
Führt drei Quellen zu einem Entitäts-/Begriffsmodell zusammen:
  1. Vault-Frontmatter (Personen/Orte): kuratiert, mit Normdaten + `aliases`.
  2. vorab berechnete NER-Listen (`data/ner_*.json`): 364 Personen, 1022 Orte,
     je mit `pages`-Belegen (Token-Granularität).
  3. Rekonziliationen (`data/recon_*.json`): GND / iDAI-Gazetteer / Geo.

Strategie für hohe Präzision:
  • **Vault-Begriffe** (Nachnamen/Orts­namen/Aliase, eindeutig) werden **korpusweit**
    gematcht (hohe/mittlere Konfidenz).
  • **NER-Entitäten** werden nur auf **ihren eigenen Beleg-Seiten** gematcht
    (`token_terms`) — die NER hat die Seite bereits geprüft, das verankert den
    Treffer und liefert Spalte+Offset, statt blind das ganze Korpus zu fluten.
  • NER-Namen, die einer Vault-Entität entsprechen, verstärken diese (kein Dublett);
    nur „NER-only"-Namen erhalten geprägte IDs `psnN_…`/`plcN_…`.

Konfidenz: high = kuratiert + Normdaten · medium = kuratiert ohne Normdaten ODER
NER + rekonziliert · low = nur lexikalisch.  Reine stdlib, kein Netz, kein LLM.
"""
import re, unicodedata


def slug(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").replace("ſ", "s"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def _primary(name):
    """NER-Name → (Hauptform vor Klammer, [Klammer-Varianten])."""
    base = re.sub(r"\(.*?\)", "", name or "").strip(" .,")
    extra = [p.strip() for v in re.findall(r"\(([^)]+)\)", name or "")
             for p in re.split(r"[\/,;]", v) if p.strip() and not p.strip().isdigit()]
    return base, extra


def _pforms(base):
    """Personen-Oberflächenformen: „Cerialis, Spicius" → [Vollform, Nachname, „Spicius Cerialis"];
    „Adolf Rudorff" → [Vollform, Nachname]. Fängt OCR-typische „Nachname, Vorname"-Inversion."""
    forms = [base]
    if "," in base:
        sur, fore = (x.strip() for x in base.split(",", 1))
        forms += [sur, f"{fore} {sur}".strip()]
    else:
        parts = base.split()
        if len(parts) > 1:
            forms.append(parts[-1])
    seen, out = set(), []
    for f in forms:
        if f and f not in seen:
            seen.add(f); out.append(f)
    return out


def _pages(pp):
    out = []
    for s in pp or []:
        m = re.match(r"Bd\.(\d+)\s+S\.(\S+)", s)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out


def build(persons, places, ner_persons, ner_places, recon_p, recon_pl, STOP, GENERIC):
    entities = {}                  # id -> {id, kind, name, cert, source, gnd/wikidata/gazId/geo, …}
    psn_form, plc_form = {}, {}     # norm(Oberflächenform) -> {id}

    def addform(d, form, eid):
        n = _norm(form)
        if n:
            d.setdefault(n, set()).add(eid)

    for p in persons:
        cert = "high" if (p.get("gnd") or p.get("wikidata")) else "medium"
        entities[p["id"]] = {"id": p["id"], "kind": "person", "name": p["name"], "cert": cert,
                             "source": "vault", "gnd": p.get("gnd", ""), "wikidata": p.get("wikidata", "")}
        addform(psn_form, p["surname"], p["id"]); addform(psn_form, p["name"], p["id"])
        for a in p.get("aliases", []):
            addform(psn_form, a, p["id"])
    for pl in places:
        cert = "high" if (pl.get("wikidata") or pl.get("gazetteer") or pl.get("pleiades")) else "medium"
        entities[pl["id"]] = {"id": pl["id"], "kind": "place", "name": pl["name"], "cert": cert,
                              "source": "vault", "gazId": pl.get("gazetteer", ""),
                              "wikidata": pl.get("wikidata", ""), "geo": pl.get("geo", "")}
        addform(plc_form, pl["term"], pl["id"]); addform(plc_form, pl["name"], pl["id"])
        if pl.get("ort_modern"):
            addform(plc_form, pl["ort_modern"], pl["id"])

    # ---- korpusweite Vault-Begriffe (nur eindeutige Oberflächenformen) ----
    global_terms = {}               # term -> (kind, id, cert)

    def add_global(term, kind, eid):
        minlen = 5 if kind == "p" else 4
        if not term or len(term) < minlen or term in STOP or _norm(term) in GENERIC:
            return
        global_terms.setdefault(term, (kind, eid, entities[eid]["cert"]))

    pf, plf = {}, {}
    for p in persons:
        for f in [p["surname"], *[a for a in p.get("aliases", []) if a]]:
            pf.setdefault(f, set()).add(p["id"])
    for pl in places:
        for f in [pl["term"], *( [pl["ort_modern"]] if pl.get("ort_modern") else [])]:
            plf.setdefault(f, set()).add(pl["id"])
    for f, ids in pf.items():
        if len(ids) == 1:
            add_global(f, "p", next(iter(ids)))
    for f, ids in plf.items():
        if len(ids) == 1 and f not in global_terms:
            add_global(f, "pl", next(iter(ids)))

    # ---- NER-Entitäten: an Vault binden oder prägen; auf Beleg-Seiten verankern ----
    token_terms = {}                # (nr, tok) -> {term: (kind, id, cert)}
    ner_only = set()

    def scope(eid, kind, term, cert, pages):
        if not term or len(term) < (5 if kind == "p" else 3) or _norm(term) in GENERIC or term in STOP:
            return
        for nr, tok in _pages(pages):
            token_terms.setdefault((nr, tok), {}).setdefault(term, (kind, eid, cert))

    def vmatch(idx, *forms):
        for f in forms:
            ids = idx.get(_norm(f))
            if ids and len(ids) == 1:
                return next(iter(ids))
        return None

    for e in ner_persons:
        base, extra = _primary(e["name"])
        if not base:
            continue
        forms = _pforms(base) + extra
        vid = next((v for f in forms if (v := vmatch(psn_form, f))), None)
        if vid:
            for t in forms:
                scope(vid, "p", t, entities[vid]["cert"], e.get("pages", []))
            continue
        eid = "psnN_" + slug(base)
        if eid == "psnN_":
            continue
        r = recon_p.get((e["name"] or "").lower()) or {}
        cert = "medium" if r.get("gnd") else "low"
        entities.setdefault(eid, {"id": eid, "kind": "person", "name": base, "cert": cert,
                                  "source": "ner", "gnd": r.get("gnd", ""), "wikidata": "",
                                  "roles": e.get("roles", [])})
        ner_only.add(eid)
        for t in forms:
            scope(eid, "p", t, cert, e.get("pages", []))

    for e in ner_places:
        base, extra = _primary(e["name"])
        if not base or _norm(base) in GENERIC:
            continue
        vid = vmatch(plc_form, base, *extra)
        if vid:
            scope(vid, "pl", base, entities[vid]["cert"], e.get("pages", []))
            continue
        eid = "plcN_" + slug(base)
        if eid == "plcN_":
            continue
        r = recon_pl.get((e["name"] or "").lower()) or {}
        cert = "medium" if (r.get("gazId") or r.get("geo")) else "low"
        entities.setdefault(eid, {"id": eid, "kind": "place", "name": base, "cert": cert,
                                  "source": "ner", "gazId": r.get("gazId", ""),
                                  "geo": r.get("geo", []), "kinddetail": e.get("kind", "")})
        ner_only.add(eid)
        scope(eid, "pl", base, cert, e.get("pages", []))

    global_terms = dict(sorted(global_terms.items(), key=lambda kv: -len(kv[0])))
    return {"global_terms": global_terms, "token_terms": token_terms,
            "entities": entities, "ner_only": ner_only}
