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
- **Absätze je Spalte → ein `<p>` pro Absatz** + **`<lb/>` je Original-Druckzeile** (token-frei aus der
  Zeilengeometrie, `alto_layout._paragraphs`): ein Absatz beginnt, wo die erste Zeile **eingerückt** ist
  oder eine **Rand-Ziffer** links der Satzkante hängt (Bericht-Köpfe wie „3. Die Wiederaufnahme …"); die
  `\n` zwischen den Zeilen werden zu `<lb/>`. So wird jeder Bericht-/Absatzanfang scannbar **und** der
  Lesetext spiegelt die Druckzeilen (Inschriften/Korrekturen inklusive).
- Damit die **Entity-Erkennung nicht an Zeilen-/Absatzgrenzen scheitert**, wird die **ganze Spalte am Stück
  getaggt** (volle Trefferquote, saubere spalten-relative Offsets) und Absatz-/Zeilenstruktur danach **nach
  Wort-Position** ins fertige HTML eingesetzt (`build_tei._structure`: `</p><p>` bzw. `<lb/>`, ohne in Tags
  zu schneiden — ein Umbruch darf auch *innerhalb* einer mehrwortigen Entität liegen).

HTML-Anker: **`#pb-<tok>-<a|b>`**; eine Referenz löst damit auf **Seite + Spalte** auf
(z. B. eine Person auf S. 98 → `#pb-097-b`).

**Synchronisiertes Scrollen:** Im Lesefenster folgt das IIIF-Faksimile automatisch der Druckseite im
Text (IntersectionObserver auf die `<pb>`-Marken → `viewer.goToPage`) und umgekehrt (Faksimile-Navigation
zieht den Text nach); abschaltbar per „Faksimile folgt dem Text". Ein zweiter Schalter „Originalzeilen"
blendet die `<lb/>`-Zeilenumbrüche aus (fließender, justierter Lesesatz statt druckzeilen-treu).

## 3. Lesetext

Diplomatisch, unkorrigiert (Fraktur-OCR), spaltentreu geordnet und dehypheniert. Echte Fehler-Seiten
findet token-frei `../limes/tools/garble.py` (wort-förmig + selten + Editierdistanz 1 zu häufigem
Korpuswort); die **schlechtesten Seiten werden am IIIF-Faksimile neu transkribiert** (LLM liest je
Spalte das Digitalisat — wie bei den TOC-Köpfen) und liegen versioniert unter
`../limes/tools/corrections/<slug>/<tok>[.<col>].txt`. Beim Ableiten spielt `limesblatt_ocr.apply_corrections`
sie ein und behandelt sie **wie die Geometrie-Seiten** (`_corr_paras`: Absätze, Druckzeilen `<lb/>`,
Silbentrennung aufgelöst). Rund **270 Blatt-Token** (gut die Hälfte des Korpus) in mehreren Faksimile-Wellen
re-OCR't — die fehlerträchtigen Seiten ∪ **alle Bericht-Kopf-Seiten**, je Spalte diplomatisch.
Beim Voll-Scan meldeten die Agenten zugleich jeden Bericht-Kopf (Nr./Ort/Spalte); der Abgleich gegen
`toc.json` **bestätigte 151 von 167 Kopf-Positionen** und korrigierte echte Fehlstellen (z. B. Nr. 51
„Mümlinglinie" statt „Robern", richtige Seite). Die wenigen erhöhten garble-Werte danach sind kein
Rückschritt, sondern **korrekt transkribierte Latein-Inschriften** (z. B. die Militärdiplom-Seiten), die
der für deutschen Prosatext gebaute garble-Proxy als „selten" flaggt. *Token-freie freq-basierte Autokorrektur* wurde verworfen
(sie verschlimmbessert legitim seltene Wörter).

### Inhaltsverzeichnis (nummerierte Feldberichte)

Das Limesblatt ist *eine* fortlaufende Berichtsreihe; die Berichte tragen gedruckte Köpfe
„`<Nr>. <Ort>. [<Thema>]`", die **nur im OCR** stehen (das IIIF-Manifest kennt bloß die physischen
Lieferungen). `../limes/tools/toc_extract.py` erzeugt daraus `tools/toc.json` — **token-freie Basis**
(längste monotone Nummern-Kette) **plus kuratierte Auflage** `tools/toc_curated.json` (einmalig je Band
aus dem OCR erschlossen, wie `data/ner_*.json`), wobei jeder Eintrag **gegen das OCR seiner global
eindeutigen Druckseite geerdet** wird (markantes Ortswort, fuzzy; sonst `conf=low`). **Dritte Ebene:**
viele Bericht-Nummern sind **als Rand-Ziffern in die Seitenpaneele** gedruckt (vom Titel gelöst,
z. B. „…bei der Er- 5. forschung"); `margin_numerals()` belegt sie token-frei aus der ALTO-Geometrie
(numerische Strings am Spaltenrand) und bestätigt so die Nummer auch bei verrauschtem Titel. Ergebnis:
**210 Berichte (Nr. 1–210), lückenlos** — 207 ort-geerdet, **209/210 lokalisiert** (Ort, Rand-Ziffer
oder Faksimile-Nachlese). Vierte Stufe: wo der Kopf am Spaltenfuß steht und das OCR ihn verlor, wird die
Seite über die **IIIF Image API direkt am Digitalisat gelesen** (so u. a. Nr. 2 Kleiner Feldberg, 73
Verbesserungen, 186 Strassenforschung 1897, 208 Konstruktion u. Zweck des Limeswalles). Nur Nr. 146 bleibt
an der Bd.4/5-Grenze unbelegt. `build_site.build_toc()` rendert daraus die klickbaren Verzeichnisse je Band **und** auf der
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
  `<ref>/<persName>/<placeName>` stehen. Die Konvergenz-Schleife brachte den Bestand von **168 → 6 → 0**:
  das vollständige Inhaltsverzeichnis (`toc.json`) löste die 5 verbliebenen Bericht-Querverweise auf, die
  **Faksimile-Re-OCR** der Inschriften-Seiten den letzten Brambach-Garble (`6Ü4`). **Aktuell 0 ungelinkte
  Referenz-Indikatoren.**

Entitäts-Abdeckung: **5126 Inline-Tags / 1167 Entitäten** (vorher 3791 / 1062). Der große Sprung kommt
aus der **korpusweiten Promotion eindeutiger, distinktiver NER-Namen** (`gazetteer.build`, Recall-Stufe):
ein einzelnes, langes Großwort, das auf **genau eine** Entität zeigt (Abusina, Heidenheim,
Grosskrotzenburg, „Mommsen"), wird korpusweit gematcht statt nur auf seinen NER-Beleg-Seiten;
**mehrdeutige** Formen (z. B. „Alteburg" → mehrere Kastelle) bleiben seiten-verankert. Präzision-Stichprobe
über alle Bände: 1043 Orts-Oberflächen, **1 Fehltreffer** („Alexandri"). Der ungetaggte Rest sind bewusst
ausgelassene Gattungswörter, sehr kurze Namen oder OCR-Garble. *Token-freie freq-basierte
OCR-Autokorrektur wurde verworfen* (korrigiert legitim seltene Wörter falsch: „hören→höhen"); echte
OCR-Fehler werden stattdessen seiten-weise per Faksimile-Re-OCR (`corrections/`) behoben.

**Entitäts-Recall-Audit (gesäuberte OCR) → 6100 Inline-Tags / 1382 Entitäten.** Nach dem Voll-Scan-Re-OCR
prüfte ein Audit (`../limes/tools/entity_audit.py` + Multi-Agent-Workflow), ob auf den gesäuberten Seiten
**alle** Eigennamen markiert sind. Weil Deutsch jedes Substantiv großschreibt, ist reine Großschreibung als
Entitäts-Signal wertlos (12 000 Kandidaten, fast alle Gemeinwörter); der Audit trennt daher **KNOWN-MISSED**
(Form anderswo schon getaggt, hier nicht — Recall-Loch der konservativen ≥7-Promotion-Schwelle) deterministisch
von der **LLM-Prüfung** des Fließtexts (134 Leser → adversarische 2-Pass-Verifikation promote/page_anchor/reject
→ Vollständigkeits-Kritik). Ergebnis: **+839 Tags**; 100 bestätigte Formen an bestehende Entitäten gebunden
(`build/promote.json`, korpusweite Allowlist), **219 neue Entitäten** entdeckt und an `data/ner_*.json` angehängt —
darunter zuvor **gar nicht erfasste** Landschaften wie **Taunus** (38×, im Gazetteer als GENERIC nie geprägt),
Wetterau, Obergermanien, dutzende Flur-/Gewann- und römische Kaiser-/Inschriften-Namen. Gemeinwort-Homographe
(„Henkel", „Knapp", „Huth") wurden per Verifikation auf seiten-verankertes Taggen zurückgestuft, statt korpusweit.

## 9. Bearbeiten im Browser (`docs/edit.html`)

Ein eingebauter **TEI-Editor** erlaubt es, die Quelle direkt auf der Seite zu bearbeiten und mit dem
eigenen GitHub-Konto zu speichern — ohne Server, ohne lokalen Build:

- **Anmeldung**: ein fein-granularer **Personal Access Token** (Repo `pleuston/limesblatt-edition`,
  *Contents: read/write*), der nur im Browser-`localStorage` bleibt. GitHub Pages ist statisch — ein
  echter OAuth-Login bräuchte einen Proxy; der PAT ist die infrastrukturfreie Variante.
- **Laden/Speichern** über die **GitHub Contents API** (`GET`/`PUT …/contents/<path>` mit `sha`),
  Dateien aus `tei/` und `registers/`. Vor dem Commit prüft der Editor **Wohlgeformtheit** (`DOMParser`)
  und **doppelte `xml:id`** — kaputtes XML lässt sich nicht committen.
- **Live-Update**: der Pfad-gefilterte Workflow `.github/workflows/rebuild.yml` baut nach einem
  `tei/**`-Push **nur die Bandseiten** neu — `build_site.py --volumes-only` braucht keinen privaten
  Vault, nur das committete `tei/` + `data/toc.json` + `data/ner_places.json` — und committet das
  regenerierte `docs/` zurück (Pfadfilter ⇒ keine Schleife). Die Änderung ist nach ~1 Min. live.

**Seiten-Inline-Editor** (`docs/assets/pageedit.js`): jede Druckseiten-Marke im Lesefenster trägt ein
**✎**; ein Klick öffnet genau den TEI-Abschnitt **dieser Spalte** (die `<p>`-Blöcke zwischen dieser
`<pb>`/`<cb>` und der nächsten `<pb>`, per Regex aus der `tei/`-Datei extrahiert), lässt ihn bearbeiten,
prüft Wohlgeformtheit und schreibt den Abschnitt zurück in dieselbe Datei (Token aus demselben
`localStorage`). Ideal für schnelle OCR-Korrekturen direkt an der Seite — der Rest der Datei bleibt
unberührt, der Auto-Rebuild macht die Korrektur live.

Erreichbar über „✎ Bearbeiten" in der Navigation (ganze Datei) bzw. das ✎ je Druckseite (eine Spalte).

**Rückspielung in den Vault** (`../limes/tools/sync_edits.py`): Editor-Edits landen in `tei/`, das
`build_tei.py` aber aus dem Vault (OCR-Cache + `corrections/`) neu erzeugt — ein lokaler Voll-Rebuild
würde sie sonst überschreiben. Vor einem solchen Rebuild daher `git pull` in der Edition, dann
`python3 tools/sync_edits.py --apply`: es vergleicht je Spalte den entity-freien Lesetext der Edition-`tei/`
mit der Vault-Ableitung und legt **nur abweichende** (= hand-editierte) Spalten als
`corrections/<slug>/<tok>.<col>.txt` ab. So werden die Edits dauerhaft und überleben jeden Rebuild.
