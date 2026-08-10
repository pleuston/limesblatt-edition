/* tables.js — Registertabellen sortier- und filterbar machen.
   Kein Fremdcode, keine Abhängigkeit: die Seiten bleiben ohne JavaScript vollständig lesbar,
   die Sortierung ist eine Zugabe. Erfasst jede <table class="reg">, die nicht "nosort" trägt.

   Zahlen werden als Zahlen sortiert (deutsche Schreibweise: 1.234 und 12,5), Datums-/Jahres-
   spalten ebenfalls; alles andere alphabetisch nach deutscher Sortierregel. Leere Zellen und
   „—" landen immer am Ende, in beiden Richtungen — sonst füllt der Fehlbestand die Spitze. */
(function () {
  "use strict";
  var coll = new Intl.Collator("de", {sensitivity: "base", numeric: true});

  function zellwert(td) {
    if (!td) return "";
    return (td.textContent || "").replace(/\s+/g, " ").trim();
  }
  function zahl(s) {
    var m = s.replace(/ | /g, "").match(/^[^\d\-–]{0,3}(-?\d{1,3}(?:\.\d{3})+|-?\d+)(?:,(\d+))?/);
    if (!m) return null;
    var v = parseFloat(m[1].replace(/\./g, "") + (m[2] ? "." + m[2] : ""));
    return isNaN(v) ? null : v;
  }
  function spalte_numerisch(rows, i) {
    var n = 0, g = 0;
    for (var r = 0; r < rows.length; r++) {
      var s = zellwert(rows[r].cells[i]);
      if (!s || s === "—" || s === "·") continue;
      g++; if (zahl(s) !== null) n++;
    }
    return g > 0 && n / g >= 0.8;
  }

  function sortiere(tab, idx, richtung, num) {
    var tb = tab.tBodies[0] || tab, rows = [];
    for (var i = 0; i < tb.rows.length; i++) {
      var r = tb.rows[i];
      if (r.cells.length && r.cells[0].tagName === "TH" && !r.querySelector("td")) continue;  // Kopfzeile
      rows.push(r);
    }
    rows.forEach(function (r, i) { r._pos = i; });
    rows.sort(function (a, b) {
      var sa = zellwert(a.cells[idx]), sb = zellwert(b.cells[idx]);
      var la = (!sa || sa === "—" || sa === "·"), lb = (!sb || sb === "—" || sb === "·");
      if (la !== lb) return la ? 1 : -1;                       // Leerwerte immer nach hinten
      var d;
      if (num) { var na = zahl(sa), nb = zahl(sb); d = (na === null ? 0 : na) - (nb === null ? 0 : nb); }
      else d = coll.compare(sa, sb);
      if (!d) return a._pos - b._pos;                          // stabil
      return richtung === "auf" ? d : -d;
    });
    rows.forEach(function (r) { tb.appendChild(r); });
  }

  function filterbox(tab, kopfzeile) {
    var box = document.createElement("div");
    box.className = "tfilter";
    var inp = document.createElement("input");
    inp.type = "search"; inp.placeholder = "in der Tabelle suchen …";
    var cnt = document.createElement("span");
    var tb = tab.tBodies[0] || tab;
    function zeilen() {
      var out = [];
      for (var i = 0; i < tb.rows.length; i++) {
        var r = tb.rows[i];
        if (r === kopfzeile) continue;
        out.push(r);
      }
      return out;
    }
    var alle = zeilen();
    // Zusatzfilter fuer die Stichwoerter, die schon Hintzelmanns Register von 1903 fuehrt.
    // Er erscheint nur, wo die Tabelle solche Zeilen hat (data-hz), und nennt ihre Zahl:
    // "0 von 4068" waere eine Falschauskunft, ein fehlender Schalter ist keine.
    var mit1903 = alle.filter(function (r) { return r.hasAttribute("data-hz"); });
    var nur = null;
    if (mit1903.length) {
      var lab = document.createElement("label");
      lab.className = "hzfilter";
      lab.title = "Nur Eintraege zeigen, die Prof. Hintzelmann 1903 in sein Register aufgenommen hat";
      nur = document.createElement("input");
      nur.type = "checkbox";
      lab.appendChild(nur);
      lab.appendChild(document.createTextNode(" nur Hintzelmann 1903 (" + mit1903.length + ")"));
      box.appendChild(lab);
    }
    function zeige() {
      var q = inp.value.toLowerCase(), n = 0, h = nur && nur.checked;
      alle.forEach(function (r) {
        var m = (!q || (r.textContent || "").toLowerCase().indexOf(q) >= 0)
                && (!h || r.hasAttribute("data-hz"));
        r.style.display = m ? "" : "none"; if (m) n++;
      });
      cnt.textContent = n + " von " + alle.length + " Zeilen";
    }
    inp.addEventListener("input", zeige);
    if (nur) nur.addEventListener("change", zeige);
    box.appendChild(inp); box.appendChild(cnt);
    tab.parentNode.insertBefore(box, tab);
    zeige();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var tabs = document.querySelectorAll("table.reg:not(.nosort)");
    Array.prototype.forEach.call(tabs, function (tab) {
      var kopf = tab.tHead ? tab.tHead.rows[0] : null;
      if (!kopf) {                                   // Kopfzeile ohne <thead> (erste Zeile nur <th>)
        var r0 = tab.rows[0];
        if (r0 && r0.cells.length && !r0.querySelector("td")) kopf = r0;
      }
      if (!kopf) return;
      var datenzeilen = (tab.tBodies[0] || tab).rows.length - (kopf.parentNode === (tab.tBodies[0] || tab) ? 1 : 0);
      if (datenzeilen < 3) return;
      var richtung = {};
      Array.prototype.forEach.call(kopf.cells, function (th, i) {
        th.tabIndex = 0; th.classList.add("srt");
        function los() {
          var tb = tab.tBodies[0] || tab, rows = [];
          for (var k = 0; k < tb.rows.length; k++) if (tb.rows[k] !== kopf) rows.push(tb.rows[k]);
          var num = spalte_numerisch(rows, i);
          richtung[i] = richtung[i] === "auf" ? "ab" : "auf";
          Array.prototype.forEach.call(kopf.cells, function (o) { o.removeAttribute("data-srt"); });
          th.setAttribute("data-srt", richtung[i]);
          sortiere(tab, i, richtung[i], num);
        }
        th.addEventListener("click", los);
        th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); los(); } });
      });
      if (datenzeilen >= 15 && !tab.classList.contains("nofilter")) filterbox(tab, kopf);
    });
  });
})();
