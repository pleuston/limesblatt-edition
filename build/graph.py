"""graph.py — deterministisches Kraftlayout + SVG-Ausgabe für die Netzansicht.

Warum vorberechnet und nicht im Browser: die Seite soll ohne Fremdbibliothek auskommen (die
Edition vendort nur, was sie wirklich braucht), das Ergebnis soll bei jedem Build identisch
sein (sonst rauscht jeder Commit) und auf schwacher Hardware sofort stehen. Die Startpositionen
liegen deshalb auf einem Ring in Knotenreihenfolge — kein Zufall, keine Seed-Frage.

Fruchterman-Reingold in seiner einfachsten Form: Abstoßung zwischen allen Knoten, Anziehung
entlang der Kanten, linear fallende Schrittweite. Für die Größenordnung hier (einige hundert
Knoten) ist das schnell genug und liefert eine lesbare Anordnung.
"""
import html
import math


def layout(nodes, edges, iters=300, breite=1900.0, hoehe=1150.0):
    """nodes: [{"id","label","typ","gewicht","href"}], edges: [(a, b, w)] → {id: (x, y)}

    **Knoten ohne Kante bleiben draussen.** Sie spüren nur Abstossung und Schwerkraft und
    wandern deshalb auf einen Kreis mit festem Radius: im Bild ein Ring aus Quadraten, der
    den verbundenen Kern in die Mitte quetscht und dort unlesbar macht. Sie bekommen statt
    dessen ein Raster am unteren Rand, wo sie zeigen, dass es sie gibt, ohne Platz zu nehmen,
    den die Beziehungen brauchen."""
    n_alle = len(nodes)
    if not n_alle:
        return {}
    verbunden = set()
    for a, b, _ in edges:
        verbunden.add(a); verbunden.add(b)
    kern = [d for d in nodes if d["id"] in verbunden]
    einzeln = [d for d in nodes if d["id"] not in verbunden]
    if not kern:
        kern, einzeln = nodes, []
    hoehe_kern = hoehe - (110.0 if einzeln else 0.0)
    pos_einzeln = _raster(einzeln, breite, hoehe, hoehe_kern)
    nodes, hoehe = kern, hoehe_kern
    n = len(nodes)
    idx = {d["id"]: i for i, d in enumerate(nodes)}
    r = min(breite, hoehe) * 0.45
    x = [breite / 2 + r * math.cos(2 * math.pi * i / n) for i in range(n)]
    y = [hoehe / 2 + r * math.sin(2 * math.pi * i / n) for i in range(n)]
    # Kantenliste auf Indizes, Gewichte gedämpft (ein 200-fach-Zitat darf den Rest nicht kollabieren)
    ee = [(idx[a], idx[b], 1.0 + math.log(1.0 + w)) for a, b, w in edges if a in idx and b in idx]
    k = math.sqrt(breite * hoehe / n) * 1.05
    temp = breite / 8.0
    for schritt in range(iters):
        dx = [0.0] * n; dy = [0.0] * n
        for i in range(n):                                   # Abstoßung
            xi, yi = x[i], y[i]
            for j in range(i + 1, n):
                ddx, ddy = xi - x[j], yi - y[j]
                d2 = ddx * ddx + ddy * ddy
                if d2 < 0.01: ddx, ddy, d2 = 0.1 * ((i % 7) - 3), 0.1 * ((j % 5) - 2), 0.02
                f = k * k / d2
                dx[i] += ddx * f; dy[i] += ddy * f
                dx[j] -= ddx * f; dy[j] -= ddy * f
        for a, b, w in ee:                                   # Anziehung: d²/k, nicht linear —
            ddx, ddy = x[a] - x[b], y[a] - y[b]              # sonst gewinnt die Abstoßung auf Distanz
            d = math.sqrt(ddx * ddx + ddy * ddy) or 0.01     # und alle Knoten wandern an den Rahmen
            f = d * d * w / k
            ux, uy = ddx / d * f, ddy / d * f
            dx[a] -= ux; dy[a] -= uy
            dx[b] += ux; dy[b] += uy
        for i in range(n):                                   # schwache Schwerkraft hält die
            gx, gy = breite / 2 - x[i], hoehe / 2 - y[i]     # Komponenten beieinander
            dx[i] += gx * 0.012; dy[i] += gy * 0.012
        for i in range(n):                                   # Schritt, begrenzt durch die Temperatur
            d = math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) or 1.0
            s = min(d, temp) / d
            x[i] += dx[i] * s; y[i] += dy[i] * s
            x[i] = min(breite - 20, max(20, x[i]))
            y[i] = min(hoehe - 20, max(20, y[i]))
        temp = max(0.5, temp * (1.0 - schritt / float(iters)) ** 0.5 * 0.985)
    aus = {d["id"]: (round(x[i], 1), round(y[i], 1)) for d, i in ((d, idx[d["id"]]) for d in nodes)}
    aus.update(pos_einzeln)
    return aus


def _raster(knoten, breite, hoehe, oberkante):
    """Verbindungslose Knoten in ein Raster am unteren Rand, gleichmässig verteilt."""
    if not knoten:
        return {}
    je_zeile = max(1, int(breite // 46))
    zeilen = max(1, (len(knoten) + je_zeile - 1) // je_zeile)
    schritt_y = min(34.0, (hoehe - oberkante - 18) / zeilen) if zeilen else 24.0
    aus = {}
    for i, d in enumerate(sorted(knoten, key=lambda z: z.get("label", ""))):
        sp, ze = i % je_zeile, i // je_zeile
        aus[d["id"]] = (round(26 + sp * (breite - 52) / max(1, je_zeile - 1), 1),
                        round(oberkante + 26 + ze * schritt_y, 1))
    return aus


TYP_FARBE = {"person": "#b5560f", "limesblatt": "#1f6f8b", "orl": "#4b6b2f",
             "jahresbericht": "#7a3b6a", "werk": "#8a6d1f", "organ": "#555f7a"}


def svg(nodes, edges, pos, breite=1900.0, hoehe=1150.0, labelgrenze=None):
    """Statisches SVG mit Klassen je Typ; die Interaktion (Zoom, Filter, Suche) macht netz.js."""
    idx = {d["id"]: d for d in nodes}
    ls = []
    for a, b, w in edges:
        if a not in pos or b not in pos: continue
        (x1, y1), (x2, y2) = pos[a], pos[b]
        ls.append(f'<line class="kante t-{idx[a]["typ"]} t-{idx[b]["typ"]}" data-a="{html.escape(a)}" '
                  f'data-b="{html.escape(b)}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                  f'stroke-width="{min(3.2, 0.4 + math.log(1 + w) * 0.5):.2f}"/>')
    # Beschriftungen nur für die tragenden Knoten: 155 Namen gleichzeitig sind keine Karte,
    # sondern eine Wand. Die übrigen zeigen ihren Namen beim Überfahren (title + CSS).
    if labelgrenze is None:
        gew = sorted((d.get("gewicht", 0) for d in nodes), reverse=True)
        labelgrenze = gew[min(len(gew) - 1, 59)] if gew else 0
    # Und selbst die tragenden überlagern sich, wo das Netz dicht ist: im Kern standen
    # »Karl Zangemeister« und »Bericht 1899« übereinander. Deshalb ein Belegungstest, der
    # von den wichtigsten Knoten abwärts vergibt: wo der Platz schon genommen ist, bleibt
    # die Beschriftung weg und der Name steht beim Überfahren.
    belegt = []

    def platz_frei(x, y, laenge):
        w, h = 7.4 * laenge, 15.0
        for bx, by, bw, bh in belegt:
            if abs(x - bx) < (w + bw) / 2 and abs(y - by) < (h + bh) / 2:
                return False
        belegt.append((x, y, w, h))
        return True

    ns = []
    for d in sorted(nodes, key=lambda z: -z.get("gewicht", 0)):
        if d["id"] not in pos: continue
        x, y = pos[d["id"]]
        r = 4 + min(14, math.sqrt(d.get("gewicht", 1)) * 1.6)
        farbe = TYP_FARBE.get(d["typ"], "#666")
        href = d.get("href") or ""
        titel = html.escape(d.get("titel") or d["label"])
        gross_genug = (d.get("gewicht", 0) >= labelgrenze
                       or d["typ"] in ("limesblatt", "jahresbericht"))
        r_vor = 4 + min(14, math.sqrt(d.get("gewicht", 1)) * 1.6)
        klein = "" if (gross_genug and platz_frei(x + r_vor + 3 + 3.7 * len(d["label"]),
                                                  y, len(d["label"]))) else " klein"
        g = [f'<g class="knoten{klein} t-{d["typ"]}" data-id="{html.escape(d["id"])}" '
             f'data-label="{html.escape(d["label"].lower())}" transform="translate({x},{y})">']
        if d["typ"] == "person":
            g.append(f'<circle r="{r:.1f}" fill="{farbe}"/>')
        else:
            g.append(f'<rect x="{-r:.1f}" y="{-r:.1f}" width="{2 * r:.1f}" height="{2 * r:.1f}" rx="2" fill="{farbe}"/>')
        g.append(f'<title>{titel}</title>')
        lab = f'<text class="nlabel" x="{r + 3:.1f}" y="4">{html.escape(d["label"])}</text>'
        g.append(f'<a href="{html.escape(href)}">{lab}</a>' if href else lab)
        g.append('</g>')
        ns.append("".join(g))
    return (f'<svg id="netz" viewBox="0 0 {int(breite)} {int(hoehe)}" preserveAspectRatio="xMidYMid meet">'
            f'<g id="netzg"><g id="kanten">{"".join(ls)}</g><g id="knoten">{"".join(ns)}</g></g></svg>')
