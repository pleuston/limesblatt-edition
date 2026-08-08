/* netz.js — Interaktion für die Netzansicht: Zoom, Verschieben, Typfilter, Suche, Nachbarschaft.
   Das Layout selbst kommt fertig aus dem Build (build/graph.py) — hier wird nur betrachtet. */
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var svg = document.getElementById("netz");
    if (!svg) return;
    var g = document.getElementById("netzg");
    var k = 1, tx = 0, ty = 0, ziehend = false, px = 0, py = 0;

    function anwenden() { g.setAttribute("transform", "translate(" + tx + "," + ty + ") scale(" + k + ")"); }
    svg.addEventListener("wheel", function (e) {
      e.preventDefault();
      var f = e.deltaY < 0 ? 1.12 : 1 / 1.12, neu = Math.min(6, Math.max(0.4, k * f));
      var r = svg.getBoundingClientRect();
      var mx = (e.clientX - r.left) / r.width * svg.viewBox.baseVal.width;
      var my = (e.clientY - r.top) / r.height * svg.viewBox.baseVal.height;
      tx = mx - (mx - tx) * (neu / k); ty = my - (my - ty) * (neu / k);
      k = neu; anwenden();
    }, {passive: false});
    svg.addEventListener("mousedown", function (e) { ziehend = true; px = e.clientX; py = e.clientY; });
    window.addEventListener("mouseup", function () { ziehend = false; });
    window.addEventListener("mousemove", function (e) {
      if (!ziehend) return;
      var r = svg.getBoundingClientRect(), s = svg.viewBox.baseVal.width / r.width;
      tx += (e.clientX - px) * s; ty += (e.clientY - py) * s;
      px = e.clientX; py = e.clientY; anwenden();
    });
    var zur = document.getElementById("netz-reset");
    if (zur) zur.addEventListener("click", function () { k = 1; tx = 0; ty = 0; anwenden(); });

    var knoten = Array.prototype.slice.call(document.querySelectorAll("#knoten .knoten"));
    var kanten = Array.prototype.slice.call(document.querySelectorAll("#kanten .kante"));
    var nachbarn = {};
    kanten.forEach(function (l) {
      var a = l.dataset.a, b = l.dataset.b;
      (nachbarn[a] = nachbarn[a] || {})[b] = 1;
      (nachbarn[b] = nachbarn[b] || {})[a] = 1;
    });

    function typenAktiv() {
      var an = {};
      Array.prototype.forEach.call(document.querySelectorAll(".netz-typ input"), function (c) {
        an[c.value] = c.checked;
      });
      return an;
    }
    function neuzeichnen() {
      var an = typenAktiv();
      var q = (document.getElementById("netz-suche") || {}).value || "";
      q = q.toLowerCase();
      var sichtbar = {};
      knoten.forEach(function (n) {
        var typ = (n.getAttribute("class").match(/t-(\w+)/) || [])[1];
        var ok = an[typ] !== false && (!q || n.dataset.label.indexOf(q) >= 0);
        n.style.display = ok ? "" : "none";
        n.classList.toggle("treffer", !!q && ok);
        if (ok) sichtbar[n.dataset.id] = 1;
      });
      kanten.forEach(function (l) {
        l.style.display = (sichtbar[l.dataset.a] && sichtbar[l.dataset.b]) ? "" : "none";
      });
      var z = document.getElementById("netz-zahl");
      if (z) z.textContent = Object.keys(sichtbar).length + " Knoten sichtbar";
    }
    Array.prototype.forEach.call(document.querySelectorAll(".netz-typ input"), function (c) {
      c.addEventListener("change", neuzeichnen);
    });
    var s = document.getElementById("netz-suche");
    if (s) s.addEventListener("input", neuzeichnen);

    knoten.forEach(function (n) {                       // Nachbarschaft beim Überfahren hervorheben
      n.addEventListener("mouseenter", function () {
        var id = n.dataset.id, nb = nachbarn[id] || {};
        knoten.forEach(function (m) { m.classList.toggle("blass", m.dataset.id !== id && !nb[m.dataset.id]); });
        kanten.forEach(function (l) { l.classList.toggle("aktiv", l.dataset.a === id || l.dataset.b === id); });
      });
      n.addEventListener("mouseleave", function () {
        knoten.forEach(function (m) { m.classList.remove("blass"); });
        kanten.forEach(function (l) { l.classList.remove("aktiv"); });
      });
    });
    neuzeichnen();
  });
})();
