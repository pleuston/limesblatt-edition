/* Facettierte Limes-Karte: Marker nach Streckenabschnitt gefärbt/filterbar;
   optionaler Fokus auf eine Strecke via ?strecke=<id>. Erwartet window.MAPDATA.feats. */
(function () {
  var F = (window.MAPDATA && MAPDATA.feats) || [];
  var palette = ["#b3331a", "#1f7a4d", "#3060c0", "#b07d20", "#7a3fae"];
  var absList = [];
  F.forEach(function (f) { var a = f.abschnitt || "ohne Strecke"; if (absList.indexOf(a) < 0) absList.push(a); });
  var color = {}; absList.forEach(function (a, i) { color[a] = palette[i % palette.length]; });

  var map = L.map("map").setView([49.5, 9.4], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);

  var groups = {}; absList.forEach(function (a) { groups[a] = L.layerGroup().addTo(map); });
  var markers = [];
  F.forEach(function (f) {
    var a = f.abschnitt || "ohne Strecke", c = color[a];
    var pop = "<b>" + f.name + "</b>" + (f.orl ? "<br>" + f.orl : "") +
      (f.strecke ? '<br><a href="strecken.html#' + f.strecke_id + '">' + f.strecke + "</a>" : "") +
      '<br><a href="#' + f.id + '">Details</a>';
    var m = L.circleMarker([f.lat, f.lng], { radius: 6, weight: 2, color: c, fillColor: c, fillOpacity: .75 }).bindPopup(pop);
    m._abs = a; m._sid = f.strecke_id || ""; m._ll = [f.lat, f.lng];
    markers.push(m); groups[a].addLayer(m);
  });

  var fc = document.getElementById("facets");
  if (fc) {
    fc.innerHTML = '<strong>Limes-Abschnitt:</strong> ' + absList.map(function (a) {
      return '<label><input type="checkbox" data-abs="' + a + '" checked> <span class="dot" style="color:' +
        color[a] + '">●</span> ' + a + "</label>";
    }).join(" ");
    fc.addEventListener("change", function (e) {
      var a = e.target.getAttribute("data-abs"); if (a === null) return;
      if (e.target.checked) groups[a].addTo(map); else map.removeLayer(groups[a]);
    });
  }

  var focus = new URLSearchParams(location.search).get("strecke");
  if (focus) {
    var pts = [], nm = "";
    markers.forEach(function (m) {
      if (m._sid !== focus) { m.setStyle({ opacity: .15, fillOpacity: .08 }); }
      else { m.setStyle({ radius: 9, weight: 3 }); pts.push(m._ll); }
    });
    F.forEach(function (f) { if (f.strecke_id === focus) nm = f.strecke; });
    if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 11 });
    if (fc) fc.insertAdjacentHTML("afterbegin",
      '<div class="focusnote">Fokus: <b>' + (nm || focus) + '</b> · <a href="places.html">alle Orte zeigen</a></div>');
  }
  // Kachel-Vollabdeckung erzwingen (Container-Größe steht erst nach Layout fest)
  setTimeout(function () { map.invalidateSize(); }, 200);
})();
