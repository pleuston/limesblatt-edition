/* Facettierte Limes-Karte: benannte Kastelle (nach Abschnitt gefärbt/filterbar),
   Limesverlauf-Linie und die weiteren Limesstellen (DARE: Türme/Kleinkastelle/Lager
   zwischen den Kastellen) als zuschaltbare Ebenen. Fokus auf eine Strecke via ?strecke=<id>.
   Erwartet window.MAPDATA.feats; lädt ../data/limes-line.geojson und ../data/sites.geojson.

   Mehrere historische/thematische Kartenebenen als LIVE Drittanbieter-Kacheldienste (kein
   Rehosting, nichts davon liegt in diesem Repo) — deshalb hier möglich, obwohl Breeze &
   Schaller 2011 (CC BY-NC, nur im privaten Vault) das nicht ist: HLGL-WMTS (Herzogtum
   Hessen-Nassau 1819/1848 + Großherzogtum Hessen 1823–1850), das Virtuelle Kartenforum
   SLUB Dresden (Karte des Deutschen Reiches 1:100.000, 1909, klassischer WMS über
   L.tileLayer.wms) und das Geländerelief (DGM) Hessen + Bayern + Baden-Württemberg
   (Terrainform statt Kartenbild — Bayern/BW klassischer WMS, Hessen über eine kleine
   L.TileLayer-Unterklasse, die die ArcGIS-`export`-Reprojektion pro Kachel aufruft, da der
   Dienst keinen {z}/{x}/{y}-Kachel-Endpunkt hat, s. Publikationen/Geländerelief (DGM) über
   dem Limes.md im Vault). */
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

  // 2b) Streckenabschnitte (echte Linie, nach Strecke eingefärbt)
  var strLayer = L.layerGroup();
  fetch("../data/strecken-line.geojson").then(function (r) { return r.json(); }).then(function (gj) {
    L.geoJSON(gj, {
      style: function (f) { return { color: f.properties.color, weight: 4, opacity: .85 }; },
      onEachFeature: function (f, l) {
        l.bindPopup('<a href="strecken.html#' + f.properties.id + '">' + f.properties.name + "</a>");
        l.bindTooltip(f.properties.name, { sticky: true });
      }
    }).addTo(strLayer);
    addToggle("Streckenabschnitte", "#888", "▬", strLayer, false);
  }).catch(function () {});

  // 3) Weitere Limesstellen (DARE): Türme/Kleinkastelle/Lager zwischen den Kastellen
  var dareColor = { camp: "#8a8a8a", fort: "#8a6d3b" };  // sonst (fortlet/tower):
  var siteLayer = L.layerGroup(), siteById = {};
  fetch("../data/sites.geojson").then(function (r) { return r.json(); }).then(function (gj) {
    L.geoJSON(gj, {
      pointToLayer: function (feat, latlng) {
        var p = feat.properties || {}, col = dareColor[p.type] || "#3f6f7a";
        var pop = "<b>" + (p.name || "") + "</b>" + (p.ancient ? "<br><i>" + p.ancient + "</i>" : "") +
          (p.type ? "<br>" + p.type : "") +
          (p.id ? '<br><a href="https://imperium.ahlfeldt.se/places/' + p.id + '">DARE</a>' : "");
        var mk = L.circleMarker(latlng, { radius: 3, weight: 1, color: col, fillColor: col, fillOpacity: .65 }).bindPopup(pop);
        if (p.id) siteById[p.id] = mk;
        return mk;
      }
    }).addTo(siteLayer);
    addToggle("weitere Limesstellen · DARE (" + (gj.features || []).length + ")", "#3f6f7a", "○", siteLayer, true);
  }).catch(function () {});

  // 4) Im Volltext genannte Orte (LLM-NER, verortet via iDAI-Gazetteer / OSM) – standardmäßig aus
  var nerLayer = L.layerGroup();
  fetch("../data/ner-sites.geojson").then(function (r) { return r.json(); }).then(function (gj) {
    L.geoJSON(gj, {
      pointToLayer: function (feat, latlng) {
        var p = feat.properties || {};
        var pop = "<b>" + (p.name || "") + "</b>" + (p.kind ? " · " + p.kind : "") +
          (p.n ? "<br>" + p.n + " Fundstelle(n) im Text" : "") +
          (p.gazId ? '<br><a href="https://gazetteer.dainst.org/place/' + p.gazId + '">iDAI-Gazetteer</a>' : "") +
          '<br><a href="orte-index.html">→ Volltext-Index</a>';
        var rad = Math.min(10, 2 + Math.sqrt(p.n || 1));   // Radius ∝ Erwähnungsdichte
        return L.circleMarker(latlng, { radius: rad, weight: 1, color: "#7a3fae", fillColor: "#b388e0", fillOpacity: .55 }).bindPopup(pop);
      }
    }).addTo(nerLayer);
    addToggle("im Volltext genannte Orte · NER (" + (gj.features || []).length + ")", "#7a3fae", "◆", nerLayer, false);
  }).catch(function () {});

  // 5) Historische Landesaufnahmen Hessen (HLGL-WMTS, live Kachel-Dienst, kein Rehosting):
  //    Herzogtum Hessen-Nassau 1819/1848 + Großherzogtum Hessen 1823–1850, gebührenfrei.
  var hlglHN = L.tileLayer(
    "https://wms.hlgl.uni-marburg.de/mapcache/landesaufnahme/wmts/1.0.0/hn/default/GoogleMapsCompatible/{z}/{y}/{x}.png",
    { maxZoom: 18, attribution: 'Historische Landesaufnahme © <a href="https://hil.hessen.de">HLGL</a>' });
  var hlglGHH = L.tileLayer(
    "https://wms.hlgl.uni-marburg.de/mapcache/landesaufnahme/wmts/1.0.0/ghh/default/GoogleMapsCompatible/{z}/{y}/{x}.png",
    { maxZoom: 18, attribution: 'Historische Landesaufnahme © <a href="https://hil.hessen.de">HLGL</a>' });
  addToggle("Herzogtum Hessen-Nassau, 1819/1848", "#9c6b30", "▦", hlglHN, false);
  addToggle("Großherzogtum Hessen, 1823–1850", "#9c6b30", "▦", hlglGHH, false);

  // 6) Karte des Deutschen Reiches 1:100.000, 1909 (Virtuelles Kartenforum SLUB Dresden,
  //    live WMS, kein Rehosting) — RLK-zeitgenössisch, deckt ganz Deutschland.
  var kdr100 = L.tileLayer.wms("https://wms.kartenforum.slub-dresden.de/map/deutsches_reich_tk100", {
    layers: "deutsches_reich_tk100", format: "image/png", transparent: true, maxZoom: 18,
    attribution: '„Karte des Deutschen Reiches" 1909 © <a href="https://kartenforum.slub-dresden.de/">Virtuelles Kartenforum, SLUB Dresden</a>'
  });
  addToggle("Karte des Deutschen Reiches, 1909", "#5a5a5a", "▦", kdr100, false);

  // 7) Geländerelief (DGM1) Hessen + Bayern — live WMS/ArcGIS-export, kein Rehosting.
  //    Zeigt Terrainform statt Kartenbild: Wall/Graben/Hohlwege werden als feine lineare
  //    Strukturen sichtbar, wo oberirdisch erhalten. Sucheinstieg, kein Nachweis.
  var reliefBayern = L.tileLayer.wms("https://geoservices.bayern.de/od/wms/dgm/v1/relief", {
    layers: "by_relief_kombiniert", format: "image/png", transparent: true, maxZoom: 18,
    attribution: 'Geländerelief © <a href="https://geoservices.bayern.de">Bayerische Vermessungsverwaltung</a>, CC BY 4.0'
  });
  addToggle("Geländerelief Bayern (DGM1)", "#6b6b6b", "▦", reliefBayern, false);

  var reliefBW = L.tileLayer.wms("https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DGM_025_Schummerung", {
    layers: "Schummerung_DGM_025_BW", format: "image/png", transparent: true, maxZoom: 18,
    attribution: 'Geländerelief © <a href="https://www.lgl-bw.de">LGL Baden-Württemberg</a>, DL-DE-BY-2.0'
  });
  addToggle("Geländerelief Baden-Württemberg (DGM 25cm)", "#6b6b6b", "▦", reliefBW, false);

  // Hessens DGM1-Dienst ist ein ArcGIS-ImageServer ohne {z}/{x}/{y}-Kachel-Endpunkt (natives
  // CRS EPSG:25832). Die `export`-Operation reprojiziert aber live über bboxSR/imageSR=3857 —
  // eine L.TileLayer-Unterklasse berechnet pro Kachel die EPSG:3857-BBOX und ruft sie als
  // 256×256-„Kachel" ab (derselbe Trick wie beim Vault-Bake in tools/relief_georef.py, nur
  // pro Kachel statt einmalig über die volle Ausdehnung).
  var HessenDGM1Layer = L.TileLayer.extend({
    getTileUrl: function (coords) {
      var R = 20037508.342789244, tiles = Math.pow(2, coords.z), res = (2 * R) / (tiles * 256);
      var x0 = coords.x * 256 * res - R, x1 = (coords.x + 1) * 256 * res - R;
      var y1 = R - coords.y * 256 * res, y0 = R - (coords.y + 1) * 256 * res;
      return "https://umweltdaten.hessen.de/arcgis/rest/services/geobasis/dgm1_schummerung/mapserver/export"
        + "?bbox=" + x0 + "," + y0 + "," + x1 + "," + y1 + "&bboxSR=3857&imageSR=3857"
        + "&size=256,256&format=png32&transparent=true&f=image";
    }
  });
  var reliefHessen = new HessenDGM1Layer("", {
    maxZoom: 18, minZoom: 9,
    attribution: 'Geländerelief © HVBG/HLNUG, <a href="https://opendata.hessen.de/dataset/atkis-dgm-1">DL-DE-Zero-2.0</a>'
  });
  addToggle("Geländerelief Hessen (DGM1)", "#6b6b6b", "▦", reliefHessen, false);

  window.focusSite = function (id) {
    var m = siteById[id]; if (!m) return false;
    if (!map.hasLayer(siteLayer)) siteLayer.addTo(map);
    map.setView(m.getLatLng(), 12); m.openPopup(); return false;
  };

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
  // Inline-Tag-Sprung in eine <details>-Liste: Sektion aufklappen + hinscrollen
  function openDetails() {
    if (!location.hash) return;
    var el = document.getElementById(location.hash.slice(1));
    var d = el && el.closest && el.closest("details");
    if (d) { d.open = true; el.scrollIntoView(); }
  }
  openDetails(); window.addEventListener("hashchange", openDetails);
  setTimeout(function () { map.invalidateSize(); }, 250);
})();
