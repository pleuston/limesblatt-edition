/* Facettierte Limes-Karte: benannte Kastelle (nach Abschnitt gefärbt/filterbar),
   Limesverlauf-Linie und die weiteren Limesstellen (DARE: Türme/Kleinkastelle/Lager
   zwischen den Kastellen) als zuschaltbare Ebenen. Fokus auf eine Strecke via ?strecke=<id>.
   Erwartet window.MAPDATA.feats; lädt ../data/limes-line.geojson und ../data/sites.geojson. */
(function () {
  var F = (window.MAPDATA && MAPDATA.feats) || [];
  var palette = ["#b3331a", "#1f7a4d", "#3060c0", "#b07d20", "#7a3fae"];
  var absList = [];
  F.forEach(function (f) { var a = f.abschnitt || "ohne Strecke"; if (absList.indexOf(a) < 0) absList.push(a); });
  var color = {}; absList.forEach(function (a, i) { color[a] = palette[i % palette.length]; });

  var map = L.map("map").setView([49.5, 9.4], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxZoom: 18, attribution: '© OpenStreetMap · Stellen © <a href="https://imperium.ahlfeldt.se/">DARE</a> (CC BY)' }).addTo(map);

  var fc = document.getElementById("facets");
  if (fc) fc.insertAdjacentHTML("beforeend", '<strong>Ebenen:</strong> ');
  function addToggle(label, dotColor, dotChar, layer, on) {
    if (on) layer.addTo(map);
    if (!fc) return;
    var lab = document.createElement("label");
    var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = on;
    cb.addEventListener("change", function () { if (cb.checked) layer.addTo(map); else map.removeLayer(layer); });
    lab.appendChild(cb);
    lab.insertAdjacentHTML("beforeend", ' <span class="dot" style="color:' + dotColor + '">' + dotChar + "</span> " + label);
    fc.appendChild(lab);
  }

  // 1) Benannte Kastelle, nach Limes-Abschnitt
  var groups = {}, markers = [];
  absList.forEach(function (a) { groups[a] = L.layerGroup(); });
  F.forEach(function (f) {
    var a = f.abschnitt || "ohne Strecke", c = color[a];
    var pop = "<b>" + f.name + "</b>" + (f.orl ? "<br>" + f.orl : "") +
      (f.strecke ? '<br><a href="strecken.html#' + f.strecke_id + '">' + f.strecke + "</a>" : "") +
      '<br><a href="#' + f.id + '">Details</a>';
    var m = L.circleMarker([f.lat, f.lng], { radius: 6, weight: 2, color: c, fillColor: c, fillOpacity: .85 }).bindPopup(pop);
    m._sid = f.strecke_id || ""; m._ll = [f.lat, f.lng];
    markers.push(m); groups[a].addLayer(m);
  });
  absList.forEach(function (a) { addToggle("Kastell · " + a, color[a], "●", groups[a], true); });

  // 2) Limesverlauf-Linie
  var lineLayer = L.layerGroup();
  fetch("../data/limes-line.geojson").then(function (r) { return r.json(); }).then(function (gj) {
    L.geoJSON(gj, { style: { color: "#6b4f2a", weight: 2.5, opacity: .75, dashArray: "5 4" } }).addTo(lineLayer);
    addToggle("Limesverlauf", "#6b4f2a", "▬", lineLayer, true);
  }).catch(function () {});

  // 3) Weitere Limesstellen (DARE): Türme/Kleinkastelle/Lager zwischen den Kastellen
  var dareColor = { camp: "#8a8a8a", fort: "#8a6d3b" };  // sonst (fortlet/tower):
  var siteLayer = L.layerGroup();
  fetch("../data/sites.geojson").then(function (r) { return r.json(); }).then(function (gj) {
    L.geoJSON(gj, {
      pointToLayer: function (feat, latlng) {
        var p = feat.properties || {}, col = dareColor[p.type] || "#3f6f7a";
        return L.circleMarker(latlng, { radius: 3, weight: 1, color: col, fillColor: col, fillOpacity: .65 })
          .bindPopup("<b>" + (p.name || "") + "</b>" + (p.ancient ? "<br><i>" + p.ancient + "</i>" : "") +
                     (p.type ? "<br>" + p.type : ""));
      }
    }).addTo(siteLayer);
    var n = (gj.features || []).length;
    addToggle("weitere Limesstellen · DARE (" + n + ")", "#3f6f7a", "○", siteLayer, true);
  }).catch(function () {});

  // ?strecke= Fokus (nur benannte Kastelle)
  var focus = new URLSearchParams(location.search).get("strecke");
  if (focus) {
    var pts = [], nm = "";
    markers.forEach(function (m) {
      if (m._sid !== focus) m.setStyle({ opacity: .15, fillOpacity: .08 });
      else { m.setStyle({ radius: 9, weight: 3 }); pts.push(m._ll); }
    });
    F.forEach(function (f) { if (f.strecke_id === focus) nm = f.strecke; });
    if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 11 });
    if (fc) fc.insertAdjacentHTML("afterbegin",
      '<div class="focusnote">Fokus: <b>' + (nm || focus) + '</b> · <a href="places.html">alle Orte zeigen</a></div>');
  }
  setTimeout(function () { map.invalidateSize(); }, 250);
})();
