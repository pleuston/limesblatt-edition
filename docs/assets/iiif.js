/* iiif.js — bettet ein zitiertes OA-Digitalisat über sein IIIF-Manifest in ein
   Fenster ein (OpenSeadragon, Sequenz-Modus). Versteht IIIF Presentation v2 + v3.
   Manifest + Bildkacheln werden clientseitig von UB Heidelberg / archive.org geladen. */
(function () {
  function svcInfo(id) { return id ? id.replace(/\/info\.json$/, "") + "/info.json" : null; }
  function infoUrls(m) {
    var out = [];
    if (m.sequences && m.sequences[0]) {                       // v2
      (m.sequences[0].canvases || []).forEach(function (c) {
        try {
          var r = c.images[0].resource, s = r.service;
          if (s && s.length) s = s[0];
          out.push(svcInfo((s && (s["@id"] || s.id)) || r["@id"] || r.id));
        } catch (e) {}
      });
    } else if (m.items) {                                      // v3
      m.items.forEach(function (c) {
        try {
          var b = c.items[0].items[0].body, s = b.service && b.service[0];
          out.push(svcInfo((s && (s.id || s["@id"])) || b.id));
        } catch (e) {}
      });
    }
    return out.filter(Boolean);
  }
  var viewer = null;
  function label(t) { document.getElementById("iiiflabel").textContent = t; }
  window.openIIIF = function (manifest, name) {
    var win = document.getElementById("iiifwin");
    win.style.display = "flex";
    label(name + " — Manifest lädt …");
    if (viewer) { viewer.destroy(); viewer = null; }
    fetch(manifest).then(function (r) { return r.json(); }).then(function (m) {
      var tiles = infoUrls(m);
      if (!tiles.length) { label(name + " — kein lesbares IIIF-Manifest"); return; }
      label(name + " · " + tiles.length + " Seiten (IIIF)");
      viewer = OpenSeadragon({ id: "iiifosd", prefixUrl: "", tileSources: tiles,
        sequenceMode: true, showReferenceStrip: false, showNavigationControl: true,
        showSequenceControl: true, navigatorPosition: "BOTTOM_RIGHT" });
    }).catch(function (e) {
      label(name + " — Faksimile nicht ladbar (" + e + "). Über den Link in neuem Tab öffnen.");
    });
  };
  window.closeIIIF = function () {
    document.getElementById("iiifwin").style.display = "none";
    if (viewer) { viewer.destroy(); viewer = null; }
  };
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") window.closeIIIF(); });
})();
