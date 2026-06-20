# Markup-Modell der Limesblatt-Edition

Dieses Dokument beschreibt **vollständig**, wie der *Limesblatt*-Volltext ausgezeichnet ist:
das TEI-P5-Substrat, die Inline-Auszeichnung (Personen, Orte, Literatur- und interne Verweise),
die Register, der spalten-treue Seitenaufbau und die Qualitäts-/Vollständigkeitssicherung.
Alles wird **token-frei** (stdlib-Python, kein LLM zur Build-Zeit) aus dem privaten Forschungs-Vault
und vorab berechneten Daten erzeugt.

## 1. Pipeline (Überblick)

```
UB-Heidelberg-ALTO  ──>  tools/alto_layout.py  ──>  tools/limesblatt_ocr.py
   (get_ocr.cgi)          (Spalten-Geometrie)        <tok>.alto.xml + <tok>.alto.json + <tok>.txt   (Vault-Cache)
                                                                  │
   data/ner_*.json (vorab) + data/recon_*.json  ──┐               │
   Vault-Frontmatter (Personen/Orte/Strecken)  ───┼──> build/gazetteer.py ──┐
   tools/edh_limes.json (EDH-Inschriften)          │                         │
                                                   ▼                         ▼
                                            build/build_tei.py  ──>  tei/*.xml  +  registers/*.xml  +  data/occurrences.json
                                                                          │
                                                                          ▼
                                            build/build_site.py  ──>  docs/  (HTML, IIIF-Viewer, Karten, Suche)
                                            build/audit.py       ──>  Vollständigkeits-Audit (Konvergenz)
                                            .github/workflows/ci.yml ──> XML-Wohlgeformtheit + Referenz-Integrität
```

Neu bauen (Vault unter `../limes`, OCR-Cache vorhanden):
```bash
python3 build/build_tei.py --vault ../limes
python3 build/build_site.py
python3 build/audit.py          # Vollständigkeits-Audit
```

## 2. Spalten-treues Seitenmodell

Das Limesblatt ist **zweispaltig**; ein UB-HD-„Token" (eine IIIF-Kachel) trägt **zwei Druckseiten**
(linke Spalte = ungerade, rechte = gerade). `tools/alto_layout.py` rekonstruiert das aus der
ALTO-Geometrie: HPOS-Clustering der TextBlocks → Spalten, VPOS-Lesereihenfolge, spalteninterne
Dehyphenierung (`¬`/`-`), Druckseiten-Zahlen aus den Kolumnentiteln (`— 3 —`), Masthead/Heftkopf
oberhalb der Spalten abgetrennt, lange voll-breite Blöcke (Editorial) als spaltenübergreifender Absatz.

Im TEI:
- **`<surface xml:id="f_<tok>">`** je Kachel, mit `<graphic url="…IIIF…">` und je Spalte einer
  **`<zone rendition="column" ulx… lry…>`** (Pixelbox fürs spätere Faksimile-Highlight).
- Im Body je Spalte ein **`<pb n="<Druckseite>" facs="#f_<tok>" xml:id="pb_<tok>_<a|b>" type="head|inferred|token"/>`**
  (`@type` = Herkunft der Seitenzahl: Kolumnentitel / Odd-Even-Inferenz / Token-Fallback) plus ein
  **`<cb n="a|b" facs="#z_<tok>_<a|b>"/>`**.
- Voll-breite Überschriften → `<head>`; spaltenübergreifender Fließtext (Heft-Editorial) → `<p rend="span">`.
- Zeilenstruktur (Inschriften/Korrekturen): `\n` → **`<lb/>`**.

HTML-Anker: **`#pb-<tok>-<a|b>`**; eine Referenz löst damit auf **Seite + Spalte** auf
(z. B. eine Person auf S. 98 → `#pb-097-b`).

## 3. Lesetext

Diplomatisch, unkorrigiert (Fraktur-OCR), spaltentreu geordnet und dehypheniert. Gezielte
**Re-OCR-Korrekturen** für die wenigen unbrauchbaren Seiten (Inschriften/Tafeln) liegen versioniert
unter `../limes/tools/corrections/<slug>/<tok>[.<col>].txt` und werden beim Ableiten eingespielt
(`limesblatt_ocr.apply_corrections`); echte Fehler werden token-frei via `../limes/tools/garble.py`
gefunden (Editierdistanz 1 zu häufigem Korpuswort).

### Inhaltsverzeichnis (nummerierte Feldberichte)

Das Limesblatt ist *eine* fortlaufende Berichtsreihe; die Berichte tragen gedruckte Köpfe
„`<Nr>. <Ort>. [<Thema>]`", die **nur im OCR** stehen (das IIIF-Manifest kennt bloß die physischen
Lieferungen). `../limes/tools/toc_extract.py` erzeugt daraus `tools/toc.json` — **token-freie Basis**
(längste monotone Nummern-Kette) **plus kuratierte Auflage** `tools/toc_curated.json` (einmalig je Band
aus dem OCR erschlossen, wie `data/ner_*.json`), wobei jeder Eintrag **gegen das OCR seiner global
eindeutigen Druckseite geerdet** wird (markantes Ortswort muss vorkommen, sonst `conf=low`). Ergebnis:
**210 Berichte (Nr. 1–210), lückenlos** — 192 geerdet, 11 ohne eigene Überschrift („nur Zahlen"),
18 unsicher. `build_site.build_toc()` rendert daraus die klickbaren Verzeichnisse je Band **und** auf der
Startseite (`#art-<Nr>`, Köpfe inline markiert; unsichere/ohne-Titel gedämpft); `build_tei` zieht aus
`toc.json` den `reportmap` (Nr. → Start-`<pb>`) für die „Forts. zu Nr. NN"-Querverweise.

## 4. Inline-Auszeichnung: Personen & Orte

`build/gazetteer.py` vereint **drei** Quellen zu einem Begriff→Entität-Lexikon:
1. Vault-Frontmatter (kuratierte Personen/Orte) inkl. `aliases` und Normdaten (GND/Wikidata/iDAI/Pleiades),
2. vorab berechnete NER-Listen (`data/ner_*.json`, 364 Personen / 1022 Orte) mit Beleg-Seiten,
3. Rekonziliationen (`data/recon_*.json`).

Strategie: **Vault-Begriffe korpusweit**, **NER-Namen nur auf ihren NER-belegten Seiten** verankert
(`token_terms`) — die NER-Evidenz ist die Präzisionsgrenze. Personennamen werden in mehreren
Oberflächenformen gematcht (`_pforms`: „Cerialis, Spicius" → Nachname / „Spicius Cerialis"). Eine
**begrenzte Fuzzy-Stufe** (`build_tei`, Editierdistanz 1, nur seltene/eindeutige/großgeschriebene
Korpus-Tokens ≥ 7 Zeichen) fängt OCR-Garble bekannter Namen als Low-Cert-Treffer.

Markup: **`<persName ref="#<id>" cert="high|medium|low">`** / **`<placeName ref="#<id>" cert=…>`**
(DARE-Kleinstellen: `ref="dare:<id>"`). Konfidenz:
- **high** = kuratiert + Normdaten · **medium** = NER + rekonziliert (bzw. kuratiert ohne Normdaten) · **low** = nur lexikalisch/Fuzzy.

IDs: Vault-Entitäten `p_…`/`pl_…` (→ kuratierte Register), NER-only `psnN_…`/`plcN_…` (→ Volltext-Index).
Im HTML solid/gepunktet/gestrichelt unterstrichen; jeder Treffer mit Spalten-Offset im **Belegindex**
`data/occurrences.json` (`Entität → [Band, Token, Druckseite, Spalte, Offset]`).

## 5. Inline-Auszeichnung: Referenzen

Die zitierte Apparatur ist im Fließtext erschlossen und löst gegen `registers/bibliography.xml`
(`<listBibl>`) bzw. interne Anker auf:

| Markup | Zweck | Beispiel |
|---|---|---|
| `<ref type="bibl" target="#bib_…">` | Journale/Werke | Westd. Zeitschr., Korrespondenzblatt, Bonner Jb., ORL, Eph. Epigr., Steiner, Becker, Tischler, Haug |
| `<ref type="bibl"><citedRange>…</citedRange></ref>` | Inschriftennummer | `Brambach 1467, 1480` · `CIL/Corp. VIII p. 847` |
| `<ref type="internal" target="#pb_…">` | Selbstverweis Seite | „Limesblatt S. 80" → Druckseite (bandübergreifend) |
| `<ref type="internal" target="#pb_…">` | Bericht-Querverweis | „Forts. zu Nr. NN" → Berichts-Startseite (aus TOC + Spaltenanfang-/eindeutigem Lose-Scan) |

Autor-Monographien (z. B. Cohausen) werden **nicht** doppelt getaggt, sondern laufen über das
Personenregister; die Bibliographie-Seite zieht ihre Belege dafür aus dem Personen-Beleg. OA-Digitalisate
(großteils **UB Heidelberg**, sonst Internet Archive / CIL-BBAW) hängen je Werk am Register.

## 6. Register (`registers/*.xml`, TEI standOff)

- `persons.xml` `<listPerson>` — kuratierte Personen (Normdaten, Aliase, Porträt, Kalliope, Nachlass).
- `places.xml` `<listPlace>` — kuratierte Orte (Geo, Wikidata/iDAI/Pleiades/ORL, Ausgräber, EDH-Zahl).
- `strecken.xml` `<listPlace type="strecke">` — die 15 Limes-Abschnitte.
- `ner.xml` — leichtgewichtige Stubs der NER-only-Entitäten (jede Inline-`@ref` löst auf eine `xml:id`).
- `bibliography.xml` `<listBibl>` — die Werke + OA-Digitalisate (Ziel der `<ref target="#bib_…">`).

## 7. Abgeleitete Seiten (`docs/register/`)

- **fundindex.html** — Fundgattungen, Münzkaiser, Sigillata-Formen (Dragendorff) und **Truppenstempel**
  (Legio/Cohors + Versal-Legenden), je mit Seite+Spalte-Belegen.
- **inschriften.html** — 759 EDH-Inschriften der Limes-Fundorte (`tools/edh_limes.py`), je Kastell mit
  Gattung, Datierung und HD-Direktlink; mit Ortsregister verknüpft.
- **bibliographie.html** — die Apparatur aus `bibliography.xml` + Belege aus den `<ref>`-Tags. Bei
  **7 Werken** lässt sich das zitierte OA-Digitalisat per **IIIF direkt im Fenster** öffnen
  (`assets/iiif.js`: lädt das Manifest clientseitig, versteht IIIF Presentation v2 + v3, rendert mit
  OpenSeadragon im Sequenz-Modus). Manifeste hängen je `<bibl>` als `<ref type="iiif-manifest">`
  (Quellen: UB Heidelberg `diglit/iiif/<slug>/manifest`, archive.org `iiif.archive.org/iiif/<id>/manifest.json`).
  **Granularität: Werk-/Beispielband-Ebene** — die genaue zitierte Band-/Seitenstelle ist (noch) nicht
  erfasst, da die OCR Band/Seite der Zitate nicht zuverlässig hergibt.
- **rezeption.html** — die **Rezeptions-/Wirkungsgeschichte**: wie das Limesblatt *außerhalb* seiner
  Bände rezipiert wurde. Token-frei aus OA-Repositorien geharvestet (`../limes/tools/rezeption.py` →
  `rezeption.json`): **OpenAlex** + **Crossref** (Werk-Metadaten/DOIs — u. a. Hettners Revue-épigraphique-
  Résumés 1893/94), **archive.org** (Digitalisate/Faksimiles, verwandte Reihen), **DAI-Zenon**
  (fachbibliographisch, best-effort HTML/COinS). Klassifiziert nach **Ära** (zeitgenössisch ≤ 1912 /
  modern) × **Typ**; mit Block zur Limesblatt→ORL-Pipeline (Link auf die ORL-Gegenprobe) und zur
  **Normdaten-Lücke** (kein Wikidata-/GND-Eintrag). Gegenrichtung zur Bibliographie: dort die Werke, die
  *das Limesblatt zitiert* — hier die Belege, die *das Limesblatt zitieren*.
- **namen.html / orte-index.html** — vollständige Volltext-Register (NER), je Eintrag mit Anker + Belegen.
- **wortschatz.html** — diachrone Auswertung, ORL-Gegenprobe, KWIC.

## 8. Sicherung

- **CI-Gate** (`.github/workflows/ci.yml`): XML-Wohlgeformtheit aller `tei/*` + `registers/*`; jede
  `ref="#…"` löst auf eine Register-`xml:id`, jede `target="#…"` auf Register **oder** internen
  `<pb>`-Anker, jede `facs="#f_…"`/`#z_…` auf `<surface>`/`<zone>`; alle `xml:id` eindeutig.
- **Vollständigkeits-Audit** (`build/audit.py`): zählt referenz-artige Spans, die noch **nicht** in
  `<ref>/<persName>/<placeName>` stehen. Die Konvergenz-Schleife („linken, neu bauen, auditen")
  brachte den Bestand von **168 → 6**.

### Bekannte Grenzen (irreduzibler Rest = 6)
Rein OCR-bedingt, nicht durch fehlende Auszeichnung:
- **5** Bericht-Querverweise auf Berichte, deren Überschrift die OCR nicht eindeutig hergibt
  (mehrdeutige „N."-Datumszeilen bzw. zu stark gegarbelte Köpfe → keine sichere Seiten-Zuordnung);
- **1** Brambach-Nummer als OCR-Garble (`6Ü4`).

Entitäts-Abdeckung: ~74 % der NER-Personen, ~68 % der NER-Orte sind im Lesetext getaggt; der Rest sind
bewusst ausgelassene Gattungswörter (`Alteburg`), sehr kurze Namen oder Garble — höhere Recall-Stufen
würden Präzision kosten.
