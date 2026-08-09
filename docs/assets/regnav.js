/* Buchstabensprung in Hintzelmanns Register (1903).
 *
 * Zwei Eigenheiten der Vorlage bestimmen den Bau:
 *
 * 1. Das Register ist gedruckter Fliesstext, kein Datensatz. Die Zeilenumbrueche des Drucks
 *    laufen mitten durch die Eintraege, ein Schnitt an <br> zerschnitte die Lemmata. Als
 *    Lemmakopf gilt deshalb ein Namens- oder Ortslink DIREKT nach einem Zeilenumbruch.
 *
 * 2. Die Ueberschrift "II. Ortsverzeichnis" steht im Textstrom an der falschen Stelle: die
 *    Layout-Erkennung hat ueber dem Bundsteg von Spalte 963 eine Schein-Spalte gefunden und
 *    den Kopf dorthin gezogen, vor die letzten Eintraege von Teil I. Wer die Abschnitts-
 *    grenze aus der Ueberschrift nimmt, bekommt die Ortsnennungen der Mitarbeiter-Eintraege
 *    dazu und sieht ein unalphabetisches Register. Gesucht wird deshalb nicht die
 *    Ueberschrift, sondern die LAENGSTE AUFSTEIGENDE KETTE von Lemmakoepfen: dieselbe
 *    Methode, mit der die Feldbericht-Nummern aus dem OCR gehoben werden.
 */
(function () {
  var t1 = document.getElementById("teil-1"), bar = document.getElementById("azbar");
  if (!t1 || !bar) return;

  var falte = { "Ä": "A", "Ö": "O", "Ü": "U" };
  function anfang(el) {
    var c = (el.textContent || "").trim().charAt(0).toUpperCase();
    return falte[c] || c;
  }

  // Lemmakoepfe des ganzen Registers, in Dokumentreihenfolge.
  var koepfe = [];
  document.querySelectorAll("a.ent.persName, a.ent.placeName").forEach(function (a) {
    if (!(t1.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING)) return;
    var p = a.previousSibling;
    while (p && p.nodeType === 3 && !p.textContent.trim()) p = p.previousSibling;
    if (p && p.nodeName === "BR") koepfe.push(a);
  });
  if (koepfe.length < 10) return;

  // Laengste aufsteigende TEILFOLGE (nicht: laengster ununterbrochener Lauf). Der
  // Unterschied ist nicht kosmetisch: eine Fortsetzungszeile beginnt gelegentlich mit einem
  // Ortslink, der alphabetisch zurueckfaellt ("Butzbach … <Umbruch> Bulau"). Ein Lauf
  // zerbricht daran und liess das Ortsverzeichnis erst bei B beginnen, obwohl es bei Aalen
  // anfaengt; eine Teilfolge ueberspringt den Ausreisser.
  var n = koepfe.length, laenge = new Array(n), vorg = new Array(n), best = 0;
  for (var i = 0; i < n; i++) {
    laenge[i] = 1; vorg[i] = -1;
    for (var j = 0; j < i; j++) {
      if (anfang(koepfe[j]) <= anfang(koepfe[i]) && laenge[j] + 1 > laenge[i]) {
        laenge[i] = laenge[j] + 1; vorg[i] = j;
      }
    }
    if (laenge[i] > laenge[best]) best = i;
  }
  var kette = [];
  for (var k = best; k >= 0; k = vorg[k]) kette.unshift(k);

  // Vorlauf abschneiden. Die Teilfolge greift auch ein paar vereinzelte Ortsnennungen AUS
  // den Mitarbeiter-Eintraegen ab (dort steht "Arnsburg" in Anthes' Zeile), und der
  // Buchstabe A zeigte dann in Teil I statt auf Aalen. Im Ortsverzeichnis selbst folgen die
  // Stichwoerter lueckenlos aufeinander (Abstand 1), die Ausreisser liegen 12 und 26
  // Stichwoerter vom Rest entfernt: geschnitten wird am ersten Abstand <= 3.
  while (kette.length > 25 && kette[1] - kette[0] > 3) kette.shift();
  var beste = kette.map(function (i) { return koepfe[i]; });
  if (beste.length < 20) return;

  var erste = {};
  beste.forEach(function (a) {
    var c = anfang(a);
    if (c >= "A" && c <= "Z" && !erste[c]) erste[c] = a;
  });

  bar.hidden = false;
  Array.prototype.forEach.call(bar.querySelectorAll("button"), function (b) {
    var ziel = erste[b.getAttribute("data-b")];
    if (!ziel) { b.disabled = true; b.title = "kein Eintrag unter diesem Buchstaben"; return; }
    b.title = ziel.textContent.trim();
    b.addEventListener("click", function () {
      // Ohne Animation: die Spruenge gehen ueber bis zu 10.000 px, und eine weiche
      // Bewegung ueber diese Strecke wurde im Test nach 17 px abgebrochen. Ein Sprung,
      // der manchmal nicht stattfindet, ist schlechter als einer ohne Animation.
      ziel.scrollIntoView({ behavior: "auto", block: "center" });
      ziel.classList.add("treffer");
      setTimeout(function () { ziel.classList.remove("treffer"); }, 1600);
    });
  });
})();
