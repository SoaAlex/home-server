// UI sans framework : charge /api/listings une fois, filtre/tri côté client.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { all: [], crit: null, map: null, layer: null, markers: {}, chips: new Set(), type: "", dpe: "",
                cities: new Set(), photos: [], photoIdx: 0, lastRoute: "#/", openId: null };
const DPE = "ABCDEFG";
const DPE_COL = { A: "#009c6d", B: "#52b153", C: "#a5cc74", D: "#e0c90c", E: "#f0b50f", F: "#eb8235", G: "#d7221f" };
const TYPES = { detached: "Individuelle", link_detached: "Accolée par le garage", semi: "Semi-individuelle",
                terraced: "Mitoyenne", unknown: "Type inconnu" };
const TYPE_SRC = { user: "utilisateur", agent: "agent", map: "carte", listing: "annonce" };
const TYPE_CONF = { high: "sûr", medium: "probable", low: "incertain" };
const STYLES = { traditional: "traditionnelle", modern: "moderne", cubic: "cubique", mixed: "mixte" };
const SHAPES = { square: "carré", long: "en longueur", irregular: "irrégulier", unknown: "forme ?" };
const WORKS = { none: "aucun", light: "rafraîchissement", heavy: "travaux importants" };
const SCORE_MAX = { ville: 25, prix: 15, type: 12, jardin: 12, dpe: 8, travaux: 8, baignoire: 3, chambres: 7, trajet: 8, style: 4 };

const fmt = (n) => (n == null || n === "" || isNaN(n) ? "?" : Math.round(Number(n)).toLocaleString("fr-FR"));
// Valeur affichée : prix retenu d'un bien multi-sources (offre active la moins chère), sinon enrichissement de
// l'agent puis colonnes (l'API a déjà complété les colonnes vides avec les autres sources du bien).
// Le type est arbitré côté serveur (type_info : utilisateur > agent avec preuves > carte > …), jamais recalculé ici.
const eff = (l, k) => (k === "price" && l.cluster && l.cluster.price ? l.cluster.price.value
  : k === "property_type" && l.type_info ? l.type_info.type
  : l.enrichment && l.enrichment[k] != null ? l.enrichment[k] : l[k]);
const SOURCES = { bienici: "Bien'ici", seloger: "SeLoger", leboncoin: "LeBonCoin", pap: "PAP", netty: "Netty",
                  maes: "Maes", notaires: "Notaires", agences: "Agence" };
const srcLabel = (s) => SOURCES[s] || s;
const OFFER_STATUS = { active: "", gone: "retirée", stale: "non confirmée" };
const offers = (l) => (l.cluster && l.cluster.offers) || [];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const scoreCol = (s) => (s >= 75 ? "var(--good)" : s >= 55 ? "var(--mid)" : "var(--low)");
const scoreHex = (s) => (s >= 75 ? "#1f9d6b" : s >= 55 ? "#e29a1b" : "#8a94a3");
const isNew = (l) => !l.seen_at && Date.now() - new Date(l.first_seen) < 48 * 3600e3;
const dests = () => (state.crit && state.crit.commute && state.crit.commute.destinations) || {};
const authors = () => (state.crit && state.crit.comment_authors) || [];
const when = (d) => new Date(d).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });

function norm(s) {
  return (s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/œ/gi, "oe").toLowerCase().trim();
}
function cityKey(city, cp) {   // même logique que textparse.city_key côté Python
  let k = norm(city);
  const inner = k.match(/\(([^)]+)\)/);
  if (inner) k = inner[1];
  k = k.replace(/[-']/g, " ").replace(/\bst\b/g, "saint").replace(/\b(les|lez)\b/g, "lez").replace(/\s+/g, " ").trim();
  if (k === "lille" && String(cp || "").startsWith("59160")) return "lomme";
  return k;
}
const lKey = (l) => cityKey(eff(l, "city"), l.postal_code);
const photos = (l) => (l.cluster && l.cluster.photos && l.cluster.photos.length ? l.cluster.photos
  : l.photos && l.photos.length ? l.photos.map((p) => "/photos/" + p) : l.photo_urls || []);
const dpePill = (d) => `<span class="dpe-pill" style="background:${DPE_COL[d] || "#8a94a3"}">${d || "?"}</span>`;
function trips(l) {
  const c = l.commute || {};
  return Object.entries(dests()).map(([k, d]) => `${esc(d.short || k)} ${c[k] ?? "?"} min`).join(" · ");
}
function tri(v, label, emoji = "") {
  const cls = v === true ? "yes" : v === false ? "no" : "";
  return `<span class="b ${cls}">${emoji} ${label} ${v === true ? "✓" : v === false ? "✕" : "?"}</span>`;
}
function favButtons(l) {
  const react = [["like", "👍", "J'aime"], ["dislike", "👎", "Je n'aime pas (masquée)"]]
    .map(([v, t, title]) => `<button data-reaction="${v}" data-id="${l.id}" title="${title}" class="${l.reaction === v ? "on" : ""}">${t}</button>`).join("");
  return react + `<button data-user="to_visit" data-id="${l.id}" title="À visiter" class="${l.user_status === "to_visit" ? "on" : ""}">🗓</button>`;
}

/* ---------- données ---------- */
async function load() {
  const show = $("#f-excluded").checked ? 1 : 0;
  const [l, c] = await Promise.all([
    fetch(`/api/listings?show_excluded=${show}`).then((r) => r.json()),
    state.crit || fetch("/api/criteria").then((r) => r.json()),
  ]);
  state.all = l; state.crit = c;
  buildCities();
  render();
}

function buildCities() {
  const counts = {};
  for (const l of state.all) counts[lKey(l)] = (counts[lKey(l)] || 0) + 1;
  const row = (c) => { const k = cityKey(c);
    return `<label><input type="checkbox" value="${esc(k)}" ${state.cities.has(k) ? "checked" : ""}> ${esc(c)}<span class="n">${counts[k] || 0}</span></label>`; };
  $("#f-cities").innerHTML = `<div class="sep">Préférées</div>${state.crit.preferred_cities.map(row).join("")}
    <div class="sep">Autres</div>${state.crit.other_cities.map(row).join("")}`;
}

function filtered() {
  const q = norm($("#f-q").value);
  const maxPrice = (+$("#f-price").value || 0) * 1000 || Infinity;
  const minScore = +$("#f-score").value, minLand = +$("#f-land").value || 0;
  const hideDisliked = $("#f-hide-disliked").checked;
  const ch = state.chips;
  let out = state.all.filter((l) => {
    if ((l.score || 0) < minScore && !l.excluded_reason) return false;
    if ((eff(l, "price") || 0) > maxPrice) return false;
    if (minLand && (+eff(l, "land_m2") || 0) < minLand) return false;
    const t = eff(l, "property_type");
    if (state.type && (state.type === "other" ? ["detached", "link_detached", "semi"].includes(t) : t !== state.type)) return false;
    if (state.dpe && (!eff(l, "dpe") || DPE.indexOf(eff(l, "dpe")) > DPE.indexOf(state.dpe))) return false;
    if (state.cities.size && !state.cities.has(lKey(l))) return false;
    if (ch.has("garage") && eff(l, "has_garage") !== true) return false;
    if (ch.has("garden") && eff(l, "has_garden") !== true) return false;
    if (ch.has("bath") && eff(l, "has_bathtub") !== true) return false;
    if (ch.has("noworks") && eff(l, "works") !== "none") return false;
    if (ch.has("fav") && l.reaction !== "like") return false;
    if (ch.has("visit") && l.user_status !== "to_visit") return false;
    if (ch.has("new") && !isNew(l)) return false;
    if (ch.has("dups") && !l.dup_pending) return false;
    if (ch.has("comments") && !l.comments_n) return false;
    if (hideDisliked && l.reaction === "dislike") return false;
    if (q && !norm([eff(l, "title"), eff(l, "city"), l.address, l.agency, l.source, l.comments_text].join(" ")).includes(q)) return false;
    return true;
  });
  const key = {
    score: (l) => -(l.score || 0), price: (l) => eff(l, "price") || 9e9,
    ppm: (l) => (eff(l, "price") || 9e9) / (+eff(l, "surface_m2") || 1),
    new: (l) => -new Date(l.first_seen), commute: (l) => (l.commute && l.commute.total_min) || 999,
  }[$("#f-sort").value];
  out.sort((a, b) => key(a) - key(b));
  return out;   // les doublons sont fusionnés côté serveur : une annonce = un bien
}

/* ---------- liste ---------- */
function card(l) {
  const p = photos(l)[0];
  const s = l.score ?? 0;
  const price = eff(l, "price"), surf = +eff(l, "surface_m2");
  const works = eff(l, "works"), e = l.enrichment || {};
  const tags = [
    isNew(l) ? `<span class="tag new">Nouveau</span>` : "",
    (l.cluster ? l.cluster.price_drop : l.price_history && l.price_history.length) ? `<span class="tag drop">↘ Baisse</span>` : "",
    offers(l).length > 1 ? `<span class="tag src" title="${esc(offers(l).map((o) => srcLabel(o.source)).join(", "))}">${offers(l).length} sources</span>` : "",
    l.dup_pending ? `<span class="tag dup" title="Doublon possible à valider">Doublon ?</span>` : "",
    l.comments_n ? `<span class="tag com" title="${esc((l.comments_text || "").slice(0, 160))}">💬 ${l.comments_n}</span>` : "",
    l.excluded_reason ? `<span class="tag bad">${esc(l.excluded_reason)}</span>` : "",
    l.enrichment_status === "done" ? `<span class="tag" title="Analysée par l'agent">✦ analysée</span>` : "",
  ].join("");
  const street = l.address ? " · " + esc(l.address.replace(/,?\s*\d{5}.*$/, "")) : "";
  return `<article class="card ${l.reaction === "dislike" || l.excluded_reason ? "dim" : ""} ${l.reaction === "like" ? "liked" : ""}" data-id="${l.id}">
    <div class="media" style="${p ? `background-image:url('${esc(p)}')` : ""}">
      ${p ? "" : `<div class="nophoto">⌂</div>`}
      <div class="score" style="--s:${s};--c:${scoreCol(s)}"><span>${l.score ?? "–"}</span></div>
      <div class="tags">${tags}</div>
      <div class="fav">${favButtons(l)}</div>
    </div>
    <div class="body">
      <div class="price-row"><span class="price">${fmt(price)} €</span>
        <span class="ppm">${price && surf ? fmt(price / surf) + " €/m²" : ""}</span></div>
      <div class="title">${esc(eff(l, "title"))}</div>
      <div class="loc">📍 ${esc(eff(l, "city") || "Ville à compléter")}${street}</div>
      <div class="specs"><span>📐 ${fmt(eff(l, "surface_m2"))} m²</span><span title="Terrain">🌱 ${fmt(eff(l, "land_m2"))} m²</span>
        <span>🛏 ${eff(l, "bedrooms") ?? "?"} ch.</span><span>${dpePill(eff(l, "dpe"))}</span></div>
      <div class="badges">${tri(eff(l, "has_garage"), "garage", "🚙")}${tri(eff(l, "has_garden"), "jardin", "🌳")}
        ${eff(l, "has_bathtub") ? `<span class="b yes">🛁 baignoire ✓</span>` : ""}
        <span class="b">🏠 ${esc(TYPES[eff(l, "property_type")] || "Type ?")}</span>
        ${works === "none" ? `<span class="b yes">✨ sans travaux</span>` : works === "heavy" ? `<span class="b no">🔨 travaux</span>` : works === "light" ? `<span class="b mid">🖌️ à rafraîchir</span>` : ""}
        ${e.style === "cubic" ? `<span class="b mid">🧊 cubique</span>` : ""}
        ${e.garden_shape === "long" ? `<span class="b mid">↔️ jardin en longueur</span>` : ""}</div>
      <div class="foot"><span>🚗 ${trips(l)}</span><span>${esc(l.cluster ? l.cluster.sources.map(srcLabel).join(" · ") : srcLabel(l.source))}</span></div>
    </div></article>`;
}

/* ---------- carte ---------- */
function ensureMap() {
  if (state.map) return;
  state.map = L.map("map").setView([50.66, 3.06], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap", maxZoom: 19 }).addTo(state.map);
  for (const d of Object.values(dests()))
    L.marker([d.lat, d.lon], { icon: L.divIcon({ className: "", html: `<div class="mk-work">💼</div>`, iconSize: [24, 24] }) })
      .addTo(state.map).bindTooltip(esc(d.label));
}
function renderMap(items) {
  ensureMap();
  setTimeout(() => state.map.invalidateSize(), 60);
  if (state.layer) state.layer.remove();
  state.layer = L.layerGroup().addTo(state.map);
  state.markers = {};
  for (const l of items) {
    if (l.lat == null || l.excluded_reason) continue;
    const col = scoreHex(l.score);
    const big = (l.score || 0) >= 55;
    if (big && l.geo_precision === "approx")
      L.circle([l.lat, l.lon], { radius: 250, color: col, weight: 1, fillOpacity: 0.05, interactive: false }).addTo(state.layer);
    const icon = big
      ? L.divIcon({ className: "", iconSize: [34, 24], iconAnchor: [17, 12],
          html: `<div class="mk ${l.geo_precision !== "exact" ? "approx" : ""}" style="background:${col}">${l.score ?? "–"}</div>` })
      : L.divIcon({ className: "", iconSize: [12, 12], iconAnchor: [6, 6], html: `<div class="dot" style="background:${col}"></div>` });
    const p = photos(l)[0];
    state.markers[l.id] = L.marker([l.lat, l.lon], { icon, riseOnHover: true, zIndexOffset: (l.score || 0) * 10, baseZ: (l.score || 0) * 10 }).addTo(state.layer)
      .bindPopup(`<div class="pop" data-open="${l.id}">${p ? `<img src="${esc(p)}">` : ""}<b>${fmt(eff(l, "price"))} €</b> · ${l.score ?? "–"}/100<br>
        ${esc(eff(l, "title"))}<br><span class="muted">${esc(l.address || eff(l, "city") || "")}</span></div>`);
  }
}
function highlight(id, on) {
  const m = state.markers[id];
  if (m && m._icon && m._icon.firstChild) { m._icon.firstChild.classList.toggle("hl", on); m.setZIndexOffset(on ? 5000 : (m.options.baseZ || 0)); }
}

/* ---------- fiche ---------- */
function offerLinks(l) {   // un lien par source ; menu déroulant si le bien est publié plusieurs fois
  const os = offers(l);
  if (os.length < 2) return `<a class="btn" href="${esc(l.url)}" target="_blank" rel="noopener">Annonce ↗</a>`;
  return `<details class="offers"><summary class="btn">Annonces (${os.length}) ↗</summary><div class="menu">${os.map((o) =>
    `<a href="${esc(o.url)}" target="_blank" rel="noopener" class="${esc(o.status)}"><span><b>${esc(srcLabel(o.source))}</b>${o.agency ? ` · ${esc(o.agency)}` : ""}</span>
      <span class="op">${fmt(o.price)} €${OFFER_STATUS[o.status] ? ` <span class="b mid">${OFFER_STATUS[o.status]}</span>` : ""}</span></a>`).join("")}</div></details>`;
}
function evidenceText(ev) {
  const pair = (v, unit) => (v && v.length === 2 ? `${fmt(v[0])} / ${fmt(v[1])} ${unit}` : "");
  return [ev.rule, ev.photos ? `${ev.photos} photo${ev.photos > 1 ? "s" : ""} commune${ev.photos > 1 ? "s" : ""}` : "aucune photo commune",
    ev.price ? `prix ${pair(ev.price, "€")}` : "", ev.surface ? `surface ${pair(ev.surface, "m²")}` : "",
    ev.distance_km != null ? `${ev.distance_km} km` : "", ev.text >= 0.2 ? `texte identique à ${Math.round(ev.text * 100)} %` : "",
    ev.veto ? `veto : ${ev.veto}` : ""]
    .filter(Boolean).map(esc).join(" · ");
}
function dupPane(x) {
  return `<div class="dup-pane"><div class="dup-pics">${(x.photos || []).slice(0, 3).map((u) => `<img src="${esc(u)}" loading="lazy" alt="">`).join("") || `<div class="nophoto">⌂</div>`}</div>
    <div><b>${fmt(x.price)} €</b> · ${esc((x.sources || [x.source]).map(srcLabel).join(", "))}</div>
    <div>${fmt(x.surface_m2)} m² · terrain ${fmt(x.land_m2)} m² · ${x.rooms ?? "?"} p. · ${x.bedrooms ?? "?"} ch.</div>
    <div class="muted">${esc(x.city || "")}${x.address ? " · " + esc(x.address) : ""}${x.excluded_reason ? ` · exclue : ${esc(x.excluded_reason)}` : ""}</div>
    ${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">annonce ↗</a>` : ""}</div>`;
}
function dupBoxes(l) {
  const me = { photos: photos(l), price: eff(l, "price"), surface_m2: eff(l, "surface_m2"), land_m2: eff(l, "land_m2"),
    rooms: l.rooms, bedrooms: eff(l, "bedrooms"), city: eff(l, "city"), address: l.address, url: l.url,
    sources: l.cluster ? l.cluster.sources : [l.source] };
  return (l.suggestions || []).map((sg) => `<div class="box dupbox"><h3>⚠️ Doublon possible</h3>
    <div class="dup-grid">${dupPane(me)}${dupPane(sg.other)}</div>
    <div class="muted small">${evidenceText(sg.evidence || {})}</div>
    <div class="addr-actions"><button class="btn sm" data-merge="${sg.other_id}" data-id="${l.id}">Fusionner</button>
      <button class="btn ghost sm" data-notdup="${sg.other_id}" data-id="${l.id}">Pas un doublon</button>
      <a class="btn ghost sm" href="#/l/${sg.other_id}">Ouvrir l'autre fiche</a></div></div>`).join("");
}
function sourcesBox(l) {
  const os = offers(l);
  if (os.length < 2) return "";
  return `<div class="box"><h3>Sources fusionnées (${os.length})</h3>${os.map((o) => `<div class="src-row ${esc(o.status)}">
    <a href="${esc(o.url)}" target="_blank" rel="noopener"><b>${esc(srcLabel(o.source))}</b>${o.agency ? ` · ${esc(o.agency)}` : ""}</a>
    <span>${fmt(o.price)} €</span><span class="muted">${OFFER_STATUS[o.status] || "active"} · vue le ${new Date(o.last_seen).toLocaleDateString("fr-FR")}</span>
    ${o.principal ? `<span class="muted small">principale</span>` : `<button class="btn ghost sm" data-split="${o.id}" data-id="${l.id}" title="Ce n'est pas le même bien">Séparer</button>`}
  </div>`).join("")}</div>`;
}
/* ---------- type retenu (arbitré par l'API) ---------- */
function typeBlock(l) {
  const ti = l.type_info || { type: eff(l, "property_type") || "unknown", verdicts: [] };
  const e = l.enrichment || {}, vs = ti.verdicts || [];
  const against = vs.filter((v) => v.disagrees);
  const src = ti.source ? `<span class="src-pill ${esc(ti.confidence)}" title="confiance : ${esc(TYPE_CONF[ti.confidence] || ti.confidence)}">${esc(TYPE_SRC[ti.source] || ti.source)}</span>` : "";
  const detail = (vs.find((v) => v.used) || {}).detail;
  const opts = [["", "Automatique"], ...Object.entries(TYPES).map(([k, v]) => [k, k === "unknown" ? "Inconnu" : v])];
  const userVal = e.property_type_user || "";
  return `<div class="type-line"><b>${esc(TYPES[ti.type] || "?")}</b>${src}${detail ? ` <span class="muted small-inline">${esc(detail)}</span>` : ""}
      ${against.length ? `<span class="b mid" title="${esc(against.map((v) => `${v.source_label} : ${v.label}`).join(" · "))}">⚠️ sources en désaccord : ${against.map((v) => `${esc(v.source_label)} → ${esc(v.label)}`).join(", ")}</span>` : ""}
      ${vs.length ? `<button class="link" data-type-why>Pourquoi ?</button>` : ""}</div>
    <ul class="type-why" hidden>${vs.map((v) => `<li class="${v.used ? "used" : ""}">${v.used ? "✔" : v.disagrees ? "⚠️" : "·"} <b>${esc(v.source_label)}</b> : ${esc(v.label)} <span class="muted">— ${esc(v.detail)}</span></li>`).join("")}</ul>
    <div class="type-actions"><label class="muted small-inline">Corriger :
        <select data-type-fix="${l.id}">${opts.map(([k, v]) => `<option value="${k}" ${k === userVal ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
      ${e.type_request ? `<span class="b mid" title="${esc(e.type_request.note || "")}">🔎 vérification demandée</span>`
        : `<button class="btn ghost sm" data-type-check="${l.id}" title="L'agent regarde les photos, la vue de rue et la vue aérienne, côté par côté">🔎 Faire vérifier</button>`}</div>`;
}

/* ---------- mini-carte : fonds IGN, cadastre, bâtiments du calcul cartographique ---------- */
const IGN = (layer, style, fmt) => `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=${layer}` +
  `&STYLE=${encodeURIComponent(style)}&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=${fmt}`;
const ADJ_STYLE = {
  parcel: { color: "#ffffff", weight: 2, dashArray: "4 3", fillOpacity: 0 },
  target: { color: "#1d4ed8", weight: 2, fillColor: "#3b82f6", fillOpacity: 0.45 },
  property: { color: "#0891b2", weight: 2, fillColor: "#22d3ee", fillOpacity: 0.35 },
  neighbour_main: { color: "#b91c1c", weight: 2, fillColor: "#ef4444", fillOpacity: 0.35 },
  neighbour_annex: { color: "#c2410c", weight: 2, fillColor: "#fb923c", fillOpacity: 0.35 },
  shared_wall: { color: "#d946ef", weight: 6, opacity: 0.95 },
};
const ADJ_LEGEND = [["target", "maison"], ["property", "propriété"], ["neighbour_main", "voisin habité"],
  ["neighbour_annex", "annexe voisine"], ["shared_wall", "mur commun"]];
const ADJ_WHY = { imprecise_position: "adresse imprécise", no_address: "pas d'adresse", low_confidence: "adresse trop incertaine" };
function adjNote(l) {   // pourquoi aucun bâtiment n'est tracé
  const a = l.adjacency, exact = l.geo_precision === "exact";
  if (!exact) return "⚠️ Maison non localisée : la carte est centrée sur une position approximative.";
  if (!a) return "Calcul de la mitoyenneté en attente (prochain cycle du collecteur).";
  if (a.status === "not_applicable") return `Pas de calcul de mitoyenneté : ${ADJ_WHY[a.reason] || a.reason || "adresse imprécise"}.`;
  if (a.status === "error") return "Calcul de la mitoyenneté en échec (nouvel essai dans 6 h).";
  if (a.status === "no_building") return "Aucun bâtiment trouvé à cette adresse dans la BD TOPO.";
  return "";
}
function miniMap(l) {
  let pref = {};
  try { pref = JSON.parse(localStorage["maison.minimap"] || "{}"); } catch (e) { pref = {}; }
  const plan = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 21, maxNativeZoom: 19, attribution: "© OpenStreetMap" });
  const ortho = L.tileLayer(IGN("ORTHOIMAGERY.ORTHOPHOTOS", "normal", "image/jpeg"), { maxZoom: 21, maxNativeZoom: 19, attribution: "© IGN" });
  const cadastre = L.tileLayer(IGN("CADASTRALPARCELS.PARCELLAIRE_EXPRESS", "PCI vecteur", "image/png"), { maxZoom: 21, maxNativeZoom: 19, attribution: "© IGN" });
  const exact = l.geo_precision === "exact";
  const mm = L.map("minimap", { zoomControl: true, attributionControl: true, maxZoom: 21, layers: [pref.base === "ortho" ? ortho : plan] })
    .setView([l.lat, l.lon], l.geo_precision === "city" ? 13 : exact ? 17 : 15);
  mm.attributionControl.setPrefix(false);
  if (pref.cadastre) cadastre.addTo(mm);
  L.control.layers({ Plan: plan, "Photo aérienne": ortho }, { Cadastre: cadastre }, { collapsed: false, position: "topright" }).addTo(mm);
  const save = () => { localStorage["maison.minimap"] = JSON.stringify({ base: mm.hasLayer(ortho) ? "ortho" : "plan", cadastre: mm.hasLayer(cadastre) }); };
  mm.on("baselayerchange overlayadd overlayremove", save);
  const geo = exact && l.adjacency && l.adjacency.geometry;
  if (geo && geo.features && geo.features.length) {
    const g = L.geoJSON(geo, { style: (f) => ADJ_STYLE[f.properties.role] || {}, interactive: false }).addTo(mm);
    g.eachLayer((x) => { if (x.feature.properties.role === "shared_wall") x.bringToFront(); });
    mm.fitBounds(g.getBounds(), { padding: [18, 18], maxZoom: 20 });
    const legend = L.control({ position: "bottomleft" });
    legend.onAdd = () => {
      const d = L.DomUtil.create("div", "adj-legend");
      d.innerHTML = ADJ_LEGEND.map(([r, t]) => `<span><i style="background:${ADJ_STYLE[r].fillColor || ADJ_STYLE[r].color}"></i>${t}</span>`).join("") + `<span class="muted">tracés ±3 m</span>`;
      return d;
    };
    legend.addTo(mm);
  } else if (exact) {
    L.circleMarker([l.lat, l.lon], { radius: 9, color: "#fff", weight: 2, fillColor: scoreHex(l.score), fillOpacity: 1 }).addTo(mm);
  } else {
    L.circle([l.lat, l.lon], { radius: l.geo_precision === "city" ? 800 : 250, color: scoreHex(l.score) }).addTo(mm);
  }
  return mm;
}
const gmaps = (l) => ({
  sat: `https://www.google.com/maps/@?api=1&map_action=map&center=${l.lat},${l.lon}&zoom=20&basemap=satellite`,
  pano: `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${l.lat},${l.lon}`,
});
async function loadStreet(l) {   // demandée à l'ouverture de la fiche seulement, puis servie par le cache serveur
  const box = $("#street-box");
  if (!box) return;
  let m;
  try { m = await fetch(`/api/listings/${l.id}/street`).then((r) => (r.ok ? r.json() : { available: false, reason: "service indisponible" })); }
  catch (e) { m = { available: false, reason: "service indisponible" }; }
  if (state.openId !== l.id || !$("#street-box")) return;
  $("#street-box").innerHTML = m.available
    ? `<a href="${esc(m.viewer_url)}" target="_blank" rel="noopener"><img src="${esc(m.image_url)}" alt="Vue de rue" loading="lazy"></a>
       <div class="muted small">Vue de rue du ${esc(m.date ? new Date(m.date).toLocaleDateString("fr-FR") : "?")} · à ${esc(m.distance_m)} m${m.kind === "plane" ? " · photo plane" : ""} ·
         © ${esc(m.author || "Panoramax")}${m.license ? ` (${esc(m.license)})` : ""} · <a href="${esc(m.viewer_url)}" target="_blank" rel="noopener">Panoramax ↗</a></div>`
    : `<div class="muted small">Aucune vue de rue${m.reason ? ` (${esc(m.reason)})` : ""}.</div>`;
}

/* ---------- commentaires (fil du bien, auteur mémorisé dans le navigateur) ---------- */
function myAuthor() {
  const a = localStorage["maison.author"];
  return authors().includes(a) ? a : null;    // un auteur retiré de la configuration est ignoré
}
function commentItem(c) {
  const editing = state.cEdit === c.id;
  return `<div class="c-item" data-cid="${c.id}">
    <div class="c-meta"><b>${esc(c.author || "—")}</b><span class="muted">${when(c.created_at)}${c.updated_at ? ` · modifié le ${when(c.updated_at)}` : ""}</span>
      ${editing ? "" : `<span class="c-tools"><button class="link" data-c-edit="${c.id}" title="Modifier">✏️</button><button class="link" data-c-del="${c.id}" title="Supprimer">🗑</button></span>`}</div>
    ${editing ? `<textarea class="c-text" data-c-edit-text maxlength="2000" rows="3">${esc(c.body)}</textarea>
      <div class="c-actions"><button class="btn ghost sm" data-c-cancel>Annuler</button><button class="btn sm" data-c-save="${c.id}">Enregistrer</button></div>`
      : `<div class="c-body">${esc(c.body)}</div>`}</div>`;
}
function commentsBox(l) {
  const cs = l.comments || [], me = myAuthor();
  return `<div class="box comments" id="comments-box"><h3>💬 Commentaires${cs.length ? ` (${cs.length})` : ""}</h3>
    <div class="c-list">${cs.map(commentItem).join("") || `<div class="muted small">Aucun commentaire pour l'instant.</div>`}</div>
    <form class="c-form" data-comment-form="${l.id}">
      <textarea class="c-text" name="body" maxlength="2000" rows="2" placeholder="Ajouter un commentaire…"></textarea>
      <div class="c-actions">
        ${authors().length ? `<div class="seg c-authors ${state.cNeedAuthor ? "need" : ""}"><span class="muted small">${state.cNeedAuthor ? "Qui écrit ?" : "Auteur :"}</span>${authors().map((a) =>
          `<button type="button" data-author="${esc(a)}" class="${a === me ? "on" : ""}">${esc(a)}</button>`).join("")}</div>` : ""}
        <span class="muted small c-hint">Ctrl+Entrée</span><button class="btn sm" type="submit">Publier</button></div>
    </form></div>`;
}
function refreshComments(focus) {   // remplace le seul bloc : carrousel, défilement et mini-carte restent en place
  const l = state.detail, box = $("#comments-box");
  if (!l || !box) return;
  const draft = ($(".c-form textarea", box) || {}).value || "";
  box.outerHTML = commentsBox(l);
  const ta = $("#comments-box .c-form textarea");
  ta.value = draft;
  if (focus) (state.cEdit ? $("#comments-box [data-c-edit-text]") : ta).focus();
  const entry = state.all.find((x) => x.id === l.id);   // badge, filtre et recherche de la liste
  if (entry) {
    entry.comments_n = l.comments.length;
    entry.comments_text = l.comments.map((c) => c.body).join(" ") || null;
    const el = $(`.card[data-id="${l.id}"]`);
    if (el) el.outerHTML = card(entry);
  }
}
async function commentApi(url, method, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { alert("Commentaire : " + (await r.text())); return null; }
  return r.status === 204 ? {} : r.json();
}
async function publishComment(form) {
  const ta = $("textarea", form), text = ta.value.trim();
  if (!text) return ta.focus();
  if (authors().length && !myAuthor()) { state.cNeedAuthor = true; return refreshComments(); }   // choix demandé d'abord
  const btn = $("button[type=submit]", form); btn.disabled = true;
  const c = await commentApi(`/api/listings/${form.dataset.commentForm}/comments`, "POST", { author: myAuthor(), body: text });
  btn.disabled = false;
  if (!c) return;
  state.detail.comments.push(c);
  ta.value = "";
  refreshComments(true);
}
async function openDetail(id) {
  if (state.openId === id && !$("#drawer").hidden) return;
  state.openId = id;
  markSeen(id);
  const l = await fetch(`/api/listings/${id}`).then((r) => r.json());
  if (l.merged_into) { state.openId = null; location.replace(`#/l/${l.merged_into}`); return; }   // source fusionnée
  const e = l.enrichment || {}, c = l.commute || {};
  state.photos = photos(l); state.photoIdx = 0;
  state.detail = l; l.comments = l.comments || []; state.cEdit = null; state.cNeedAuthor = false;
  const bars = Object.entries(l.score_details || {}).map(([k, v]) =>
    `<div class="bar"><span>${esc(k)}</span><div class="track"><div class="fill ${v < 0 ? "neg" : ""}" style="width:${Math.min(100, (Math.abs(v) / (SCORE_MAX[k] || 10)) * 100)}%"></div></div><b>${v > 0 ? "+" : ""}${v}</b></div>`).join("");
  const hist = l.cluster
    ? (l.cluster.price_history.length > 1 ? l.cluster.price_history.map((h) => `${fmt(h.price)} € <span class="muted">(${esc(srcLabel(h.source))}${h.date ? ", " + new Date(h.date).toLocaleDateString("fr-FR") : ""})</span>`).join(" → ") : "")
    : (l.price_history || []).map((h) => `${fmt(h.price)} € <span class="muted">(${new Date(h.date).toLocaleDateString("fr-FR")})</span>`).join(" → ");
  const best = l.cluster && l.cluster.price ? l.cluster.price.offer_id : null;
  const otherPrices = offers(l).filter((o) => o.id !== best && o.price && o.price !== eff(l, "price")).map((o) =>
    `${fmt(o.price)} € <span class="muted">(${esc(srcLabel(o.source))}${OFFER_STATUS[o.status] ? ", " + OFFER_STATUS[o.status] : ""})</span>`).join(" · ");
  const yn = (v) => (v === true ? "oui" : v === false ? "non" : "?");
  const price = eff(l, "price"), surf = +eff(l, "surface_m2");
  $("#detail").innerHTML = `
    <div class="d-head"><button class="icon-btn" data-close title="Fermer (Échap)">✕</button>
      <h2>${esc(eff(l, "title"))}</h2><div class="fav">${favButtons(l)}</div>
      ${offerLinks(l)}</div>
    ${state.photos.length ? `<div class="carousel"><img id="c-img" src="${esc(state.photos[0])}" alt="">
      ${state.photos.length > 1 ? `<button class="prev" data-nav="-1">‹</button><button class="next" data-nav="1">›</button>` : ""}
      <span class="idx" id="c-idx">1 / ${state.photos.length}</span></div>
      <div class="thumbs">${state.photos.map((u, i) => `<img src="${esc(u)}" data-i="${i}" loading="lazy" class="${i ? "" : "on"}" alt="">`).join("")}</div>` : ""}
    <div class="d-body">
      <div class="col">
        <div class="box">
          <div class="price-row"><span class="big-price">${fmt(price)} €</span>
            <span class="score" style="position:relative;top:0;left:0;--s:${l.score ?? 0};--c:${scoreCol(l.score ?? 0)}"><span>${l.score ?? "–"}</span></span></div>
          <div class="muted">${price && surf ? fmt(price / surf) + " €/m² · " : ""}${esc(eff(l, "city") || "")} ${esc(l.postal_code || "")}</div>
          ${otherPrices ? `<div style="margin-top:6px">Autres prix : ${otherPrices}</div>` : ""}
          ${hist ? `<div style="margin-top:6px">Historique : ${hist}</div>` : ""}
          ${l.excluded_reason ? `<div style="margin-top:8px"><span class="b no">Exclue : ${esc(l.excluded_reason)}</span></div>` : ""}
          <div class="addr-actions"><button class="btn ghost sm" data-merge-with="${l.id}" title="Doublon non détecté : réunir cette annonce et une autre">🔗 Fusionner avec…</button></div>
        </div>
        ${commentsBox(l)}${dupBoxes(l)}${sourcesBox(l)}
        ${e.summary_fr ? `<div class="box"><h3>✦ Analyse de l'agent</h3><div>${esc(e.summary_fr)}</div>
          ${e.red_flags && e.red_flags.length ? `<ul class="flags">${e.red_flags.map((x) => `<li>⚠️ ${esc(x)}</li>`).join("")}</ul>` : ""}</div>` : ""}
        <div class="box"><h3>Caractéristiques</h3><dl class="kv">
          <dt>Type</dt><dd>${typeBlock(l)}${e.style ? `<div class="muted small-inline">style ${esc(STYLES[e.style] || e.style)}</div>` : ""}</dd>
          <dt>Surface</dt><dd>${fmt(eff(l, "surface_m2"))} m² · ${l.rooms ?? "?"} pièces · ${eff(l, "bedrooms") ?? "?"} chambres${e.largest_bedroom_m2 ? ` (plus grande ${e.largest_bedroom_m2} m²)` : ""}</dd>
          <dt>Terrain</dt><dd>${fmt(eff(l, "land_m2"))} m²${e.garden_m2_estimate ? ` · jardin ≈ ${fmt(e.garden_m2_estimate)} m²` : ""}${e.garden_shape ? ` · ${esc(SHAPES[e.garden_shape] || e.garden_shape)}` : ""}</dd>
          <dt>Énergie</dt><dd>DPE ${dpePill(eff(l, "dpe"))} &nbsp; GES ${dpePill(l.ges)}</dd>
          <dt>Construction</dt><dd>${l.year_built ?? "?"}</dd>
          <dt>Garage / jardin</dt><dd>${yn(eff(l, "has_garage"))} / ${yn(eff(l, "has_garden"))}</dd>
          <dt>Baignoire</dt><dd>${yn(eff(l, "has_bathtub"))}</dd>
          <dt>Travaux</dt><dd>${esc(WORKS[eff(l, "works")] || "?")}</dd>
          <dt>Source</dt><dd>${esc(l.cluster ? l.cluster.sources.map(srcLabel).join(", ") : srcLabel(l.source))}${l.agency ? " · " + esc(l.agency) : ""} · vue le ${new Date(l.first_seen).toLocaleDateString("fr-FR")}</dd>
        </dl></div>
        <div class="box"><h3>Description</h3><div class="desc">${esc((l.description || "").replace(/<br\s*\/?>/gi, "\n"))}</div></div>
      </div>
      <div class="col">
        <div class="box"><h3>Localisation</h3>
          <div>${esc(l.address || "Adresse inconnue")}${e.address_confidence && e.address_confidence !== "none" ? ` <span class="b ${e.address_confidence === "exact" ? "yes" : "mid"}">${e.address_method === "user" ? "✔ validée" : esc(e.address_confidence)}</span>` : ""}</div>
          ${l.lat != null ? `<div class="minimap" id="minimap"></div>
            ${adjNote(l) ? `<div class="muted small">${esc(adjNote(l))}</div>` : ""}
            <div class="street" id="street-box"><div class="muted small">Recherche d'une vue de rue…</div></div>
            <div class="addr-actions"><a class="btn ghost sm" href="${esc(gmaps(l).sat)}" target="_blank" rel="noopener">🛰 Google satellite ↗</a>
              <a class="btn ghost sm" href="${esc(gmaps(l).pano)}" target="_blank" rel="noopener">🚶 Street View ↗</a></div>` : ""}
          <div class="addr-actions">
            ${e.address_request
              ? `<span class="b mid">🔍 Investigation demandée — traitée au prochain passage de l'agent (:20)</span>`
              : e.address_method === "user" ? "" : `<button class="btn ghost sm" data-investigate="${l.id}">🔍 Investiguer l'adresse</button>`}
            <button class="btn ghost sm" data-fix-address="${l.id}">✏️ Corriger</button>
          </div>
          ${(e.address_investigations || []).slice(-2).map((h) => `<div class="muted small">Investigation du ${new Date(h.done_at).toLocaleDateString("fr-FR")} : ${esc(h.result || "rien de concluant")} ${h.confidence ? `(${esc(h.confidence)})` : ""}${h.notes ? ` — ${esc(h.notes)}` : ""}</div>`).join("")}
        </div>
        <div class="box"><h3>Trajets voiture</h3>
          ${Object.entries(dests()).map(([k, d]) => `<div class="trip"><span>${esc(d.label)}</span><b>${c[k] ?? "?"} min</b></div>`).join("")}</div>
        <div class="box"><h3>Détail du score</h3>${bars || "<span class='muted'>—</span>"}</div>
      </div>
    </div>`;
  $("#drawer").hidden = false;
  $("#detail").scrollTop = 0;
  if (l.lat != null) { miniMap(l); loadStreet(l); }
}
function showPhoto(i) {
  const n = state.photos.length; if (!n || !$("#c-img")) return;
  state.photoIdx = (i + n) % n;
  $("#c-img").src = state.photos[state.photoIdx];
  $("#c-idx").textContent = `${state.photoIdx + 1} / ${n}`;
  $$(".thumbs img").forEach((t, j) => t.classList.toggle("on", j === state.photoIdx));
  const t = $$(".thumbs img")[state.photoIdx]; if (t) t.scrollIntoView({ block: "nearest", inline: "center" });
}
function closeDetail() { location.hash = state.lastRoute || "#/"; }
function reopen(id) { state.openId = null; openDetail(id); }
function markSeen(id) {
  const l = state.all.find((x) => x.id === id);
  if (l && l.seen_at) return;
  if (l) l.seen_at = new Date().toISOString();
  fetch(`/api/listings/${id}/seen`, { method: "POST" });
  const tag = $(`.card[data-id="${id}"] .tag.new`);
  if (tag) tag.remove();
}

/* ---------- rendu ---------- */
function render() {
  let route = location.hash || "#/";
  const detail = route.match(/^#\/l\/(\d+)/);
  if (detail) {   // la fiche s'ouvre par-dessus la vue courante, sans la recalculer
    openDetail(+detail[1]);
    if (state.rendered) return;
    route = state.lastRoute;
  } else {
    state.lastRoute = route; state.openId = null; $("#drawer").hidden = true;
  }
  state.rendered = true;
  const items = filtered();
  $("#count").textContent = `${items.length} annonce${items.length > 1 ? "s" : ""}`;
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.route === route));
  const main = $("#main");
  const wide = matchMedia("(min-width: 1280px)").matches;
  main.className = route === "#/map" ? "maponly" : wide ? "split" : "";
  if (route !== "#/map") $("#list").innerHTML = items.map(card).join("") || `<div class="empty">Aucune annonce ne correspond aux filtres.</div>`;
  if (main.className) renderMap(items);
}

/* ---------- événements ---------- */
document.addEventListener("click", async (ev) => {
  const au = ev.target.closest("button[data-author]");
  if (au) {
    localStorage["maison.author"] = au.dataset.author;
    const retry = state.cNeedAuthor;
    state.cNeedAuthor = false;
    refreshComments();
    if (retry) publishComment($("#comments-box .c-form"));      // la publication demandée reprend après le choix
    return;
  }
  if (ev.target.closest("[data-type-why]")) { const w = $("#detail .type-why"); if (w) w.hidden = !w.hidden; return; }
  const tc = ev.target.closest("[data-type-check]");
  if (tc) {
    const note = prompt("Faire vérifier le type par l'agent. Un indice ? (facultatif — ex. « le garage touche le voisin »)", "");
    if (note === null) return;
    const r = await fetch(`/api/listings/${tc.dataset.typeCheck}/type_request`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note }) });
    if (!r.ok) return alert("Demande impossible : " + (await r.text()));
    return reopen(+tc.dataset.typeCheck);
  }
  const ce = ev.target.closest("[data-c-edit]");
  if (ce) { state.cEdit = +ce.dataset.cEdit; return refreshComments(true); }
  if (ev.target.closest("[data-c-cancel]")) { state.cEdit = null; return refreshComments(); }
  const cs = ev.target.closest("[data-c-save]");
  if (cs) {
    const text = $("#comments-box [data-c-edit-text]").value.trim();
    if (!text) return;
    const upd = await commentApi(`/api/comments/${cs.dataset.cSave}`, "PATCH", { body: text });
    if (!upd) return;
    state.detail.comments = state.detail.comments.map((x) => (x.id === upd.id ? upd : x));
    state.cEdit = null;
    return refreshComments();
  }
  const cd = ev.target.closest("[data-c-del]");
  if (cd) {
    if (!confirm("Supprimer ce commentaire ? C'est définitif.")) return;
    if (!(await commentApi(`/api/comments/${cd.dataset.cDel}`, "DELETE"))) return;
    state.detail.comments = state.detail.comments.filter((x) => x.id !== +cd.dataset.cDel);
    return refreshComments();
  }
  const rb = ev.target.closest("button[data-reaction]");
  if (rb) {
    ev.stopPropagation();
    const id = +rb.dataset.id;
    const l = state.all.find((x) => x.id === id) || {};
    const v = l.reaction === rb.dataset.reaction ? null : rb.dataset.reaction;
    await fetch(`/api/listings/${id}/reaction`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reaction: v }) });
    l.reaction = v;
    $$(`button[data-reaction][data-id="${id}"]`).forEach((x) => x.classList.toggle("on", x.dataset.reaction === v));
    const card = $(`.card[data-id="${id}"]`);
    if (card) {
      card.classList.toggle("liked", v === "like");
      card.classList.toggle("dim", v === "dislike" || !!l.excluded_reason);
      if (v === "dislike" && $("#f-hide-disliked").checked) { card.classList.add("leaving"); setTimeout(rerender, 250); }
    }
    return;
  }
  const b = ev.target.closest("button[data-user]");
  if (b) {
    ev.stopPropagation();
    const l = state.all.find((x) => x.id === +b.dataset.id) || {};
    const v = l.user_status === b.dataset.user ? null : b.dataset.user;
    await fetch(`/api/listings/${b.dataset.id}/user`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_status: v }) });
    l.user_status = v;
    $$(`button[data-user][data-id="${b.dataset.id}"]`).forEach((x) => x.classList.toggle("on", x.dataset.user === v));
    return;
  }
  if (ev.target.closest("[data-close]")) return closeDetail();
  const mw = ev.target.closest("[data-merge-with]");
  if (mw) {   // doublon non détecté : l'utilisateur désigne l'autre annonce par son numéro (#/l/NUMÉRO)
    const v = prompt("Numéro de l'autre annonce (visible dans son adresse : …#/l/NUMÉRO) :", "");
    const other = parseInt((v || "").replace(/\D/g, ""), 10);
    if (!other) return;
    const r = await fetch(`/api/listings/${mw.dataset.mergeWith}/merge`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ other_id: other }) });
    if (!r.ok) return alert("Fusion impossible : " + (r.status === 404 ? "annonce introuvable" : await r.text()));
    const out = await r.json();
    await load();
    if (location.hash !== `#/l/${out.principal}`) location.hash = `#/l/${out.principal}`; else reopen(out.principal);
    return;
  }
  const grp = ev.target.closest("[data-merge],[data-notdup],[data-split]");
  if (grp) {   // actions sur les doublons : fusionner, refuser une suggestion, séparer une source
    const id = +grp.dataset.id;
    const [path, body] = grp.dataset.merge ? ["merge", { other_id: +grp.dataset.merge }]
      : grp.dataset.notdup ? ["not_duplicate", { other_id: +grp.dataset.notdup }] : ["split", { member_id: +grp.dataset.split }];
    if (path === "split" && !confirm("Séparer cette source ? Elle redeviendra une annonce à part et ne sera plus fusionnée automatiquement avec ce bien.")) return;
    grp.disabled = true;
    const r = await fetch(`/api/listings/${id}/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { grp.disabled = false; return alert("Action impossible : " + (await r.text())); }
    const out = await r.json();
    await load();
    const target = out.principal || id;
    if (location.hash !== `#/l/${target}`) location.hash = `#/l/${target}`; else reopen(target);
    return;
  }
  const inv = ev.target.closest("[data-investigate]");
  if (inv) {
    const note = prompt("Un indice pour l'agent ? (facultatif — ex. « pas la bonne maison, c'est près du parc »)", "");
    if (note === null) return;
    await fetch(`/api/listings/${inv.dataset.investigate}/address_request`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note }) });
    return reopen(+inv.dataset.investigate);
  }
  const fix = ev.target.closest("[data-fix-address]");
  if (fix) {
    const address = prompt("Adresse exacte (numéro, rue, ville) :", "");
    if (!address) return;
    const r = await fetch(`/api/listings/${fix.dataset.fixAddress}/address`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ address }) });
    if (!r.ok) return alert("Adresse introuvable : " + (await r.text()));
    const upd = await r.json();
    const l = state.all.find((x) => x.id === upd.id);
    if (l) Object.assign(l, { address: upd.address, lat: upd.lat, lon: upd.lon, commute: upd.commute, score: upd.score, geo_precision: "exact" });
    return reopen(upd.id);
  }
  const nav = ev.target.closest("[data-nav]"); if (nav) return showPhoto(state.photoIdx + +nav.dataset.nav);
  const th = ev.target.closest(".thumbs img"); if (th) return showPhoto(+th.dataset.i);
  const pop = ev.target.closest("[data-open]"); if (pop) { location.hash = `#/l/${pop.dataset.open}`; return; }
  const c = ev.target.closest(".card"); if (c) { location.hash = `#/l/${c.dataset.id}`; return; }
  const chip = ev.target.closest("[data-chip]");
  if (chip) { const k = chip.dataset.chip; state.chips.has(k) ? state.chips.delete(k) : state.chips.add(k); chip.classList.toggle("on"); return rerender(); }
  const seg = ev.target.closest(".seg button");
  if (seg) { $$("button", seg.parentElement).forEach((x) => x.classList.toggle("on", x === seg)); state[seg.parentElement.id === "f-type" ? "type" : "dpe"] = seg.dataset.v; return rerender(); }
});
function rerender() { state.rendered = false; render(); }
document.addEventListener("mouseover", (ev) => { const c = ev.target.closest(".card"); if (c) highlight(+c.dataset.id, true); });
document.addEventListener("mouseout", (ev) => { const c = ev.target.closest(".card"); if (c) highlight(+c.dataset.id, false); });
document.addEventListener("change", async (ev) => {
  const sel = ev.target.closest("select[data-type-fix]");
  if (!sel) return;
  const id = +sel.dataset.typeFix;
  sel.disabled = true;
  const r = await fetch(`/api/listings/${id}/property_type`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: sel.value || null }) });
  if (!r.ok) { sel.disabled = false; return alert("Correction impossible : " + (await r.text())); }
  const upd = await r.json();
  const entry = state.all.find((x) => x.id === upd.id);
  if (entry) Object.assign(entry, { score: upd.score, score_details: upd.score_details, type_info: upd.type_info });
  reopen(upd.id);
});
document.addEventListener("submit", (ev) => {
  const f = ev.target.closest("[data-comment-form]");
  if (f) { ev.preventDefault(); publishComment(f); }
});
document.addEventListener("keydown", (ev) => {
  if ($("#drawer").hidden) return;
  if (ev.target.matches("textarea, input")) {      // saisie : pas de navigation photo ni de fermeture de la fiche
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      const save = ev.target.matches("[data-c-edit-text]") && $("#comments-box [data-c-save]");
      if (save) save.click(); else { const f = ev.target.closest("[data-comment-form]"); if (f) publishComment(f); }
    }
    if (ev.key === "Escape") ev.target.blur();
    return;
  }
  if (ev.key === "Escape") closeDetail();
  if (ev.key === "ArrowRight") showPhoto(state.photoIdx + 1);
  if (ev.key === "ArrowLeft") showPhoto(state.photoIdx - 1);
});
$("#f-cities").addEventListener("change", (ev) => { ev.target.checked ? state.cities.add(ev.target.value) : state.cities.delete(ev.target.value); rerender(); });
$("#cities-clear").addEventListener("click", () => { state.cities.clear(); $$("#f-cities input").forEach((i) => (i.checked = false)); rerender(); });
$("#filters").addEventListener("input", (ev) => {
  $("#o-score").textContent = $("#f-score").value;
  if (ev.target.closest("#f-cities")) return;
  if (ev.target.id === "f-excluded") load(); else rerender();
});
let qTimer; $("#f-q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(rerender, 150); });
$("#f-sort").addEventListener("change", rerender);
$("#f-reset").addEventListener("click", () => {
  $$("#filters input").forEach((i) => (i.type === "checkbox" ? (i.checked = false) : (i.value = i.type === "range" ? 0 : "")));
  $("#f-hide-disliked").checked = true;
  $("#o-score").textContent = "0"; state.chips.clear(); state.cities.clear(); state.type = state.dpe = "";
  $$("#quick button").forEach((b) => b.classList.remove("on"));
  $$(".seg").forEach((s) => $$("button", s).forEach((b, i) => b.classList.toggle("on", i === 0)));
  state.rendered = false; load();
});
$("#btn-theme").addEventListener("click", () => {
  const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t; localStorage.theme = t;
});
$("#btn-filters").addEventListener("click", () => $("#filters").classList.toggle("open"));
window.addEventListener("hashchange", () => { if (!location.hash.startsWith("#/l/")) state.rendered = false; render(); });
window.addEventListener("resize", () => { clearTimeout(state.rz); state.rz = setTimeout(rerender, 250); });
load();
