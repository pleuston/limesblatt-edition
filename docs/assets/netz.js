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
    if (zur) zur.addEventListener("click", function () {
      var g_ = document.getElementById("netz-grad");
      if (g_) { g_.value = 0; }
      var s_ = document.getElementById("netz-suche");
      if (s_) { s_.value = ""; }
      k = 1; tx = 0; ty = 0; anwenden();
      if (typeof neuzeichnen === "function") neuzeichnen();
    });

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
    // Grad = Zahl der Verbindungen. Der Gradfilter ist der wirksamste Hebel dieser Ansicht:
    // die Hälfte der Knoten hängt an genau einer Kante und trägt zum Bild nichts bei; wer sie
    // ausblendet, sieht das Gerüst. Er sitzt deshalb neben der Suche, nicht in einem Menü.
    var grad = {};
    knoten.forEach(function (n) { grad[n.dataset.id] = Object.keys(nachbarn[n.dataset.id] || {}).length; });
    var maxGrad = Math.max.apply(null, [1].concat(Object.keys(grad).map(function (i) { return grad[i]; })));

    function mindestgrad() {
      var s = document.getElementById("netz-grad");
      return s ? parseInt(s.value, 10) || 0 : 0;
    }
    function neuzeichnen() {
      var an = typenAktiv();
      var q = (document.getElementById("netz-suche") || {}).value || "";
      q = q.toLowerCase();
      var mg = mindestgrad();
      var sichtbar = {};
      knoten.forEach(function (n) {
        var typ = (n.getAttribute("class").match(/t-(\w+)/) || [])[1];
        var ok = an[typ] !== false && (!q || n.dataset.label.indexOf(q) >= 0)
                 && (grad[n.dataset.id] || 0) >= mg;
        n.style.display = ok ? "" : "none";
        n.classList.toggle("treffer", !!q && ok);
        if (ok) sichtbar[n.dataset.id] = 1;
      });
      kanten.forEach(function (l) {
        l.style.display = (sichtbar[l.dataset.a] && sichtbar[l.dataset.b]) ? "" : "none";
      });
      var z = document.getElementById("netz-zahl");
      if (z) {
        var n_s = Object.keys(sichtbar).length;
        z.textContent = n_s + " von " + knoten.length + " Knoten"
          + (mg > 0 ? " (ab " + mg + " Verbindungen)" : "");
      }
      var ga = document.getElementById("netz-gradwert");
      if (ga) ga.textContent = mg === 0 ? "alle" : "ab " + mg;
      einpassen();
    }

    // Nach dem Filtern auf die übrigen Knoten zoomen. Das Layout ist statisch (es kommt fertig
    // aus dem Build), also bleibt der Kern sonst klein in der Mitte stehen und die gewonnene
    // Fläche bleibt leer: der Filter hätte aufgeräumt, ohne dass man mehr sieht.
    function einpassen() {
      var x1 = 1e9, y1 = 1e9, x2 = -1e9, y2 = -1e9, n = 0;
      knoten.forEach(function (m) {
        if (m.style.display === "none") return;
        var tr = (m.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
        if (!tr) return;
        var x = parseFloat(tr[1]), y = parseFloat(tr[2]);
        if (x < x1) x1 = x; if (y < y1) y1 = y;
        if (x > x2) x2 = x; if (y > y2) y2 = y;
        n++;
      });
      if (n < 2) return;
      var vb = svg.viewBox.baseVal, rand = 90;
      var bw = Math.max(1, x2 - x1 + 2 * rand), bh = Math.max(1, y2 - y1 + 2 * rand);
      k = Math.min(6, Math.max(0.4, Math.min(vb.width / bw, vb.height / bh)));
      tx = vb.width / 2 - ((x1 + x2) / 2) * k;
      ty = vb.height / 2 - ((y1 + y2) / 2) * k;
      anwenden();
    }
    Array.prototype.forEach.call(document.querySelectorAll(".netz-typ input"), function (c) {
      c.addEventListener("change", neuzeichnen);
    });
    var s = document.getElementById("netz-suche");
    if (s) s.addEventListener("input", neuzeichnen);
    var gs = document.getElementById("netz-grad");
    if (gs) { gs.max = Math.min(12, maxGrad); gs.addEventListener("input", neuzeichnen); }
    neuzeichnen();

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
