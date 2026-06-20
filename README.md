# Limesblatt — digitale Edition

Eine **statische digitale Edition** des *Limesblatt* (1892–1903) — der „Mitteilungen der Streckenkommissare bei der Reichs-Limeskommission", also der laufenden Feldberichte der frühen Limesforschung. Diplomatische OCR-Edition mit **IIIF-Faksimiles** (UB Heidelberg), **TEI-P5**-Volltext und **GND-/Wikidata-/Geo-verknüpften** Personen- und Ortsregistern.

**▶ Online lesen: https://pleuston.github.io/limesblatt-edition/**

> EN — A static digital edition of the *Limesblatt* (field reports of the Imperial Limes Commission, 1892–1903): diplomatic OCR transcription in TEI-P5, IIIF page facsimiles (Heidelberg University Library), and person/place registers linked to GND, Wikidata, iDAI-Gazetteer and Pleiades. Built token-free from a research vault; rendered to a static GitHub-Pages site.

## Was diese Edition enthält
- **8 Bände** (Bd. 1–8, 1892–1903), 488 Seiten als diplomatischer OCR-Volltext (Color-Check-/Beilage-Tafeln entfernt).
- **IIIF-Faksimile** je Seite über das Manifest der UB Heidelberg (Deep-Zoom via OpenSeadragon).
- **Personen-, Orts- und Streckenregister:** Personen (43) mit GND/Wikidata, **Porträts**, Korrespondenz (Kalliope, mit Briefzahlen) und Nachlass; Orte (17, mit **nach Limes-Abschnitt filterbarer Karte**) mit Kastelltyp, ORL-Nummer, **EDH-Inschriften** und **Ausgräber**; Strecken (15) mit ihren Kastellen, Kommissaren und „Auf der Karte zeigen".
- **Personen ↔ Orte verknüpft** über die Ausgräber-Relation (wer welches Kastell grub, in beide Richtungen). Register und Volltext sind **bidirektional verlinkt** (Eintrag → Fundstellen und zurück).
- **Karte mit Ebenen:** die benannten Kastelle (nach Limes-Abschnitt filterbar), der **Limesverlauf** und die **weiteren Limesstellen** — Türme, Kleinkastelle und Lager *zwischen* den Kastellen (aus DARE, 204; gegen die benannten Kastelle entdoppelt) sowie die **im Volltext genannten Orte** (LLM-NER, 702 via iDAI/OSM verortet) — als zuschaltbare Layer.
- **Inline-Auszeichnung** im Lesetext: Personen, benannte Kastelle *und* die kleinen DARE-Limesstellen (heuristisch, `@cert="low"`) — verlinkt ins Register bzw. in die Stellenliste (die beim Sprung automatisch aufklappt).
- **Volltext-Index (LLM-NER):** zwei vollständige, filterbare Verzeichnisse aller im Volltext genannten **Namen (~360)** und **Orte (~1000)** — per LLM-NER über alle Seiten extrahiert (heuristisch), je mit Seiten-Sprunglinks ins Faksimile. **Token-frei normdaten-angereichert** (`build/reconcile.py`): Personen gegen **GND**/das Personenregister (90 verknüpft, prominente RLK-Figuren intern verlinkt), Orte gegen den **iDAI-Gazetteer** mit Koordinaten (702 verortet, iDAI + OSM-Nominatim). Damit ist der gesamte Onomastik-/Toponym-Bestand erschlossen, nicht nur die ~60 kuratierten Register-Entitäten.
- **Textanalyse-Seite (token-frei):** Auswertung des gesamten OCR-Volltexts — diachroner Wortschatz (SVG-Diagramm) + KWIC-Konkordanz mit Faksimile-Sprung, **ORL-Gegenprobe** (für dasselbe Kastell Osterburken nennt das polierte ORL Holzbefunde ~4× seltener als die Feldberichte — Ausdünnung ist editorial), **Münzkaiser-Chronologie** (bildet die Limes-Belegung ab), **Truppen** (Legio XXII), **Zitate** (journal- statt corpus-zentriert) und **OCR-Qualität** je Band. Erwähnungsdichte je Streckenabschnitt zusätzlich als Balken.
- **Clientseitige Volltextsuche** über alle Seiten.

## Wie sie entstanden ist (token-frei)
Abgeleitet aus dem (privaten) Obsidian-Forschungs-Vault zur [Reichs-Limeskommission](https://github.com/pleuston/limes). Zwei Python-Skripte (nur Standardbibliothek, keine LLM-Tokens):

- `build/build_tei.py` — liest das Vault-Frontmatter (Personen/Orte) + den lokalen Limesblatt-OCR-Cache und erzeugt `tei/*.xml` (Faksimile/IIIF, **`<pb>` je Druckseite + `<cb>` je Spalte**, Inline-Tags für Personen/Orte/Literatur-/interne Verweise) sowie die Register `registers/persons.xml`, `places.xml`, `strecken.xml`, `ner.xml`, `bibliography.xml` und den Belegindex `data/occurrences.json`.
- `build/build_site.py` — rendert die TEI build-zeitlich zu `docs/` (HTML + OpenSeadragon-Faksimile + Leaflet-Ortskarte + MiniSearch-Index).
- `build/audit.py` — Vollständigkeits-Audit der Auszeichnung (Konvergenz-Schleife).

**▶ Das vollständige Auszeichnungs-Modell ist in [`MARKUP.md`](MARKUP.md) dokumentiert** (Spaltenmodell, Personen/Orte mit Konfidenz, Literatur-/Selbst-/Bericht-Verweise, `<citedRange>`, Register, Sicherung).

### Neu bauen
```bash
# Voraussetzung: der Vault liegt unter ../limes und sein OCR-Cache ist vorhanden
#   (im Vault einmalig: python3 tools/limesblatt_ocr.py)
python3 build/build_tei.py --vault ../limes
python3 build/build_site.py
```

## Struktur
`tei/` (8 TEI-P5-Bände, kanonische Editionsdaten) · `registers/` (TEI-Normdatenregister) · `build/` (Generatoren) · `docs/` (die statische GitHub-Pages-Site; enthält Kopien von `tei/`+`registers/` zum Download).

## Rechte
- **Editionstext, TEI, Register, Website:** [CC BY 4.0](LICENSE) (© Manuel Sassmann).
- **Seitenbilder:** © Universitätsbibliothek Heidelberg, [„In Copyright"](http://rightsstatements.org/vocab/InC/1.0/) (Nutzung zu Forschungszwecken) — in dieser Edition ausschließlich per **IIIF deep-verlinkt**, nicht nachgenutzt/re-hostet.
- Normdaten: GND (CC0), Wikidata (CC0), iDAI.gazetteer (DAI), Pleiades (CC BY).

## Caveats (ehrlich)
- **Fraktur-OCR**, unkorrigiert → diplomatische Wiedergabe mit Fehlern; brauchbar für Recherche/Suche, nicht als kritischer Text.
- **Eigennamen-Tags** sind heuristisch (Nachnamen-/Toponym-Match gegen die Register) und mit `@cert="low"` markiert; nicht alle Nennungen sind erfasst, einzelne können falsch sein.
- Eine Kachel = eine **Doppelseite**; `<pb n="…">` führt das gedruckte Seiten-/Kachel-Token (Faksimile-treu).

## Dank
Faksimiles: **UB Heidelberg** (IIIF). Normdaten: **GND/DNB**, **Wikidata**, **iDAI.gazetteer (DAI)**, **Pleiades**. Limesstellen & -verlauf: **DARE** (Digital Atlas of the Roman Empire, CC BY) und **OpenStreetMap** (ODbL). Viewer: OpenSeadragon, Leaflet, MiniSearch.
