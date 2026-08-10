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
/* Faksimile neben dem Registertext, wie in der Bandlesefassung. Die uebernommenen
 * Spaltenmarken tragen ihr data-page bereits, der Betrachter versteht sie unveraendert;
 * er startet nur nicht am Bandanfang, sondern auf der ersten Registerseite. */
(function () {
  if (typeof OpenSeadragon !== "function" || typeof tiles === "undefined") return;
  var osd = document.getElementById("osd");
  if (!osd) return;
  window.viewer = OpenSeadragon({
    id: "osd", prefixUrl: "", tileSources: tiles, sequenceMode: true,
    showNavigationControl: false, showSequenceControl: false,
    gestureSettingsMouse: { clickToZoom: false },
  });
  // `bereit` schaltet die Kopplung erst nach dem Startsprung frei. Ohne das zog der Betrachter
  // beim Laden den Lesetext mit, und die Seite stand sofort mitten im Register statt oben.
  var start = (typeof STARTSEITE === "number") ? STARTSEITE : 0,
      erst = true, sperre = false, bereit = false;
  function anzeige() {
    var e = document.getElementById("pgind");
    if (e) e.textContent = (viewer.currentPage() + 1) + " / " + tiles.length;
  }
  function folgt() {
    var b = document.getElementById("syncscroll");
    return bereit && (!b || b.checked);
  }
  viewer.addHandler("open", function () {
    // goHome nach dem Oeffnen: die Kachelgroesse steht erst mit der info.json fest, sonst
    // startet der Betrachter weit ausserhalb des Blattes und die Flaeche bleibt schwarz.
    if (erst) { erst = false; viewer.goToPage(start); }
    anzeige(); viewer.viewport.goHome(true);
    setTimeout(function () { bereit = true; }, 400);
  });
  viewer.addHandler("page", function (ev) {          // Faksimile bewegt -> Lesetext nachziehen
    anzeige();
    if (!folgt() || sperre) return;
    var pb = document.querySelector('.reader .text .pb[data-page="' + ev.page + '"]');
    if (pb) { sperre = true; pb.scrollIntoView({ block: "start" }); setTimeout(function () { sperre = false; }, 250); }
  });
  // Lesetext gescrollt -> Faksimile nachziehen: die oberste sichtbare Spaltenmarke gilt.
  var txt = document.querySelector(".reader .text");
  if (txt) txt.addEventListener("scroll", function () {
    if (!folgt() || sperre) return;
    var marken = txt.querySelectorAll(".pb[data-page]"), oben = null;
    for (var i = 0; i < marken.length; i++) {
      if (marken[i].getBoundingClientRect().top - txt.getBoundingClientRect().top <= 40) oben = marken[i];
    }
    if (!oben) return;
    var s = parseInt(oben.getAttribute("data-page"), 10);
    if (s !== viewer.currentPage()) { sperre = true; viewer.goToPage(s); setTimeout(function () { sperre = false; }, 250); }
  }, { passive: true });
})();

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
