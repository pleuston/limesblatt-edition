/* pageedit.js — Inline-Editor je Druckseite (Spalte) im Lesefenster.
   Bearbeitet GENAU den TEI-Abschnitt dieser <pb>/<cb>-Spalte (die <p>-Blöcke bis zur
   nächsten <pb>) und speichert in dieselbe tei/-Datei (window.TEIFILE) über die GitHub
   Contents API. Token wie im großen Editor aus localStorage (lb_pat). Nach dem Commit
   baut die rebuild-pages-Action die Bandseite neu. */
(function () {
  if (!window.TEIFILE) return;
  var OWNER = "pleuston", REPO = "limesblatt-edition", API = "https://api.github.com";
  var HINT = ' <span class="muted">Token prüfen: klassisch = Scope <code>repo</code>; fein-granular = dieses Repo + Contents: Read and write. Einrichtung unter „✎ Bearbeiten".</span>';
  function withHint(m) { return /403|not accessible|permission|forbidden|404/i.test(m) ? m + HINT : m; }
  var b64d = function (b) { return decodeURIComponent(escape(atob((b || "").replace(/\n/g, "")))); };
  var b64e = function (t) { return btoa(unescape(encodeURIComponent(t))); };
  function token() { return localStorage.getItem("lb_pat") || ""; }
  async function gh(path, opts) {
    opts = opts || {};
    var r = await fetch(API + path, Object.assign({}, opts, {
      headers: Object.assign({ Authorization: "Bearer " + token(), Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" }, opts.headers || {})
    }));
    if (!r.ok) { var m = r.status + ""; try { m = (await r.json()).message; } catch (e) {} throw new Error(m); }
    return r.status === 204 ? null : r.json();
  }
  function wellformed(x) { return !new DOMParser().parseFromString(x, "application/xml").querySelector("parsererror"); }

  var M;
  function modal() {
    if (M) return M;
    M = document.createElement("div"); M.id = "pbwin";
    M.innerHTML = '<div class="pbbox"><div class="pbhead"><b id="pbtitle"></b>' +
      '<span id="pbstat" class="pbstat"></span><span style="flex:1"></span>' +
      '<button id="pbcancel" class="ed" type="button">Abbrechen</button>' +
      '<button id="pbsave" class="ed primary" type="button">Speichern → GitHub</button></div>' +
      '<textarea id="pbtext" spellcheck="false"></textarea>' +
      '<input id="pbmsg" type="text" placeholder="Commit-Beschreibung (optional)">' +
      '<div id="pbresult" class="meta"></div></div>';
    document.body.appendChild(M);
    M.querySelector("#pbcancel").onclick = function () { M.style.display = "none"; };
    M.addEventListener("click", function (e) { if (e.target === M) M.style.display = "none"; });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && M.style.display === "flex") M.style.display = "none"; });
    return M;
  }

  async function openEdit(pbid, label) {
    if (!token()) {
      var t = prompt("GitHub Personal Access Token (Repo " + OWNER + "/" + REPO + ", Contents: write) — einmalig; Details in der Navigation unter Bearbeiten:");
      if (!t) return; localStorage.setItem("lb_pat", t.trim());
    }
    var m = modal(); m.style.display = "flex";
    var stat = m.querySelector("#pbstat"), ta = m.querySelector("#pbtext"), res = m.querySelector("#pbresult"), save = m.querySelector("#pbsave");
    m.querySelector("#pbtitle").textContent = "✎ " + label;
    stat.textContent = "lädt …"; ta.value = ""; ta.disabled = true; res.textContent = ""; save.disabled = true;
    var xml, sha, mm, idx;
    try {
      var d = await gh("/repos/" + OWNER + "/" + REPO + "/contents/" + window.TEIFILE + "?ref=main");
      xml = b64d(d.content); sha = d.sha;
      var re = new RegExp('(<pb\\b[^>]*xml:id="' + pbid + '"[^>]*/>\\s*<cb\\b[^>]*/>)([\\s\\S]*?)(?=<pb\\b|<head>|</div>)');
      mm = xml.match(re);
      if (!mm) { stat.textContent = "Abschnitt nicht gefunden (evtl. veraltete Seite?)"; return; }
      idx = mm.index;
      ta.value = mm[2].trim(); ta.disabled = false; stat.textContent = "TEI dieser Spalte — Text korrigieren, Tags belassen."; save.disabled = false; ta.focus();
    } catch (e) { stat.innerHTML = '<span class="err">Fehler: ' + withHint(e.message) + '</span>'; return; }

    save.onclick = async function () {
      var edited = "\n" + ta.value.trim() + "\n";
      var newxml = xml.slice(0, idx) + mm[1] + edited + xml.slice(idx + mm[0].length);
      if (!wellformed(newxml)) { res.innerHTML = '<span class="err">✗ XML nicht wohlgeformt — bitte die Tags prüfen.</span>'; return; }
      save.disabled = true; res.textContent = "speichert …";
      try {
        var msg = (m.querySelector("#pbmsg").value.trim() || ("Seite " + label + " bearbeitet")) + "\n\n(via Seiten-Editor)";
        var r = await gh("/repos/" + OWNER + "/" + REPO + "/contents/" + window.TEIFILE, {
          method: "PUT", body: JSON.stringify({ message: msg, content: b64e(newxml), sha: sha, branch: "main" })
        });
        res.innerHTML = '✓ gespeichert (<a href="' + r.commit.html_url + '" target="_blank" rel="noopener">' + r.commit.sha.slice(0, 7) + '</a>) — die Bandseite wird in ~1 Min. neu gebaut.';
        sha = r.content.sha; xml = newxml; mm = newxml.match(new RegExp('(<pb\\b[^>]*xml:id="' + pbid + '"[^>]*/>\\s*<cb\\b[^>]*/>)([\\s\\S]*?)(?=<pb\\b|<head>|</div>)')); idx = mm ? mm.index : idx;
      } catch (e) { res.innerHTML = '<span class="err">Fehler: ' + withHint(e.message) + (("" + e.message).indexOf("409") >= 0 ? " — Seite neu laden (zwischenzeitlich geändert)." : "") + '</span>'; }
      save.disabled = false;
    };
  }

  document.querySelectorAll('.text .pb[data-pb]').forEach(function (el) {
    var label = el.textContent.replace(/—/g, "").trim();
    var b = document.createElement("button");
    b.className = "pbedit"; b.type = "button"; b.textContent = "✎";
    b.title = "Diese Druckseite bearbeiten (GitHub)";
    b.onclick = function (ev) { ev.stopPropagation(); openEdit(el.getAttribute("data-pb"), label); };
    el.appendChild(b);
  });
})();
