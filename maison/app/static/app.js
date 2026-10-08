// UI sans framework : charge /api/listings une fois, filtre/tri côté client.
const $ = (s) => document.querySelector(s);
const state = { all: [], crit: null, map: null, layer: null };
const DPE = "ABCDEFG";
const TYPES = { detached: "Individuelle", semi: "Semi-individuelle", terraced: "Mitoyenne", unknown: "Type ?" };
const fmt = (n) => (n == null ? "?" : Number(n).toLocaleString("fr-FR"));
const eff = (l, k) => (l.enrichment && l.enrichment[k] != null ? l.enrichment[k] : l[k]);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function photo(l) {
  if (l.photos && l.photos.length) return "/photos/" + l.photos[0];
  return (l.photo_urls && l.photo_urls[0]) || "";
}
function scoreClass(s) { return s >= 75 ? "s-hi" : s >= 55 ? "s-mid" : "s-lo"; }
function tri(v, label) {
  if (v === true) return `<span class="yes">✔ ${label}</span>`;
  if (v === false) return `<span class="no">✖ ${label}</span>`;
  return `<span>? ${label}</span>`;
}

async function load() {
  const show = $("#f-excluded").checked ? 1 : 0;
  const [l, c] = await Promise.all([
    fetch(`/api/listings?show_excluded=${show}`).then((r) => r.json()),
    state.crit ? state.crit : fetch("/api/criteria").then((r) => r.json()),
  ]);
  state.all = l; state.crit = c;
  if (!$("#f-cities").options.length) {
    for (const city of [...c.preferred_cities, ...c.other_cities]) $("#f-cities").add(new Option(city, city));
  }
  render();
}

function norm(s) { return (s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace("œ", "oe").toLowerCase().replace(/[-' ]+/g, " ").replace(/\bst\b/, "saint").replace(/\bles\b/, "lez"); }

function filtered() {
  const f = {
    score: +$("#f-score").value, price: +$("#f-price").value || Infinity, land: +$("#f-land").value || 0,
    type: $("#f-type").value, dpe: $("#f-dpe").value,
    cities: [...$("#f-cities").selectedOptions].map((o) => norm(o.value)),
    garage: $("#f-garage").checked, garden: $("#f-garden").checked, bath: $("#f-bath").checked,
    noworks: $("#f-noworks").checked, fav: $("#f-fav").checked, rejected: $("#f-rejected").checked,
    dups: $("#f-dups").checked,
  };
  let out = state.all.filter((l) => {
    if ((l.score || 0) < f.score && !l.excluded_reason) return false;
    if ((eff(l, "price") || 0) > f.price) return false;
    if (f.land && (+eff(l, "land_m2") || 0) < f.land) return false;
    const t = eff(l, "property_type");
    if (f.type && (f.type === "other" ? ["detached", "semi"].includes(t) : t !== f.type)) return false;
    if (f.dpe && (!eff(l, "dpe") || DPE.indexOf(eff(l, "dpe")) > DPE.indexOf(f.dpe))) return false;
    if (f.cities.length && !f.cities.includes(norm(eff(l, "city")))) return false;
    if (f.garage && eff(l, "has_garage") !== true) return false;
    if (f.garden && eff(l, "has_garden") !== true) return false;
    if (f.bath && eff(l, "has_bathtub") !== true) return false;
    if (f.noworks && eff(l, "works") !== "none") return false;
    if (f.fav && !["favorite", "to_visit", "visited"].includes(l.user_status)) return false;
    if (!f.rejected && l.user_status === "rejected") return false;
    return true;
  });
  const key = {
    score: (l) => -(l.score || 0), price: (l) => eff(l, "price") || 9e9,
    ppm: (l) => (eff(l, "price") || 9e9) / (+eff(l, "surface_m2") || 1),
    new: (l) => -new Date(l.first_seen), commute: (l) => (l.commute && l.commute.total_min) || 999,
  }[$("#f-sort").value];
  out.sort((a, b) => key(a) - key(b));
  // Regrouper les doublons multi-sources (même empreinte) : garder le premier (mieux classé)
  const groups = {};
  for (const l of state.all) if (l.fingerprint) (groups[l.fingerprint] ||= []).push(l);
  if (!f.dups) {
    const seen = new Set();
    out = out.filter((l) => !l.fingerprint || (!seen.has(l.fingerprint) && seen.add(l.fingerprint)));
  }
  out.forEach((l) => (l._others = l.fingerprint ? groups[l.fingerprint].filter((o) => o.id !== l.id) : []));
  return out;
}

function card(l) {
  const c = l.commute || {};
  const drop = l.price_history && l.price_history.length ? `<span class="warn">↘ baisse de prix</span>` : "";
  const works = eff(l, "works");
  const enr = l.enrichment_status === "done" ? `<span class="yes">🔎 analysée</span>` : "";
  const excl = l.excluded_reason ? `<span class="no">${esc(l.excluded_reason)}</span>` : "";
  const others = l._others.length ? `<div class="meta">aussi sur : ${l._others.map((o) => esc(o.source)).join(", ")}</div>` : "";
  return `<article class="card ${l.user_status === "rejected" ? "rejected" : ""} ${l.excluded_reason ? "excluded" : ""}" data-id="${l.id}">
    <div class="img" style="background-image:url('${esc(photo(l))}')"><span class="pill ${scoreClass(l.score)}">${l.score ?? "?"}</span></div>
    <div class="body">
      <h3>${esc(eff(l, "title"))}</h3>
      <div class="price">${fmt(eff(l, "price"))} €</div>
      <div class="meta">${esc(eff(l, "city") || "ville ?")} · ${fmt(eff(l, "surface_m2"))} m² · terrain ${fmt(eff(l, "land_m2"))} m² · ${eff(l, "bedrooms") ?? "?"} ch. · DPE ${eff(l, "dpe") || "?"}</div>
      <div class="meta">🚗 Alex ${c.alexandre ?? "?"} min · Marie ${c.marie ?? "?"} min · ${esc(TYPES[eff(l, "property_type")] || "Type ?")}</div>
      <div class="badges">${tri(eff(l, "has_garage"), "garage")}${tri(eff(l, "has_garden"), "jardin")}${tri(eff(l, "has_bathtub"), "baignoire")}
        ${works === "none" ? '<span class="yes">sans travaux</span>' : works === "heavy" ? '<span class="no">travaux</span>' : works === "light" ? '<span class="warn">rafraîchir</span>' : ""}
        ${eff(l, "style") === "cubic" ? '<span class="warn">cubique</span>' : ""}${drop}${enr}${excl}</div>
      <div class="meta">${esc(l.source)} · ${esc(l.agency || "")} · vue le ${new Date(l.first_seen).toLocaleDateString("fr-FR")}</div>
      ${others}
      <div class="actions">${userButtons(l)}</div>
    </div></article>`;
}

function userButtons(l) {
  return [["favorite", "★ Favori"], ["to_visit", "🗓 À visiter"], ["rejected", "✖ Rejeter"]]
    .map(([v, t]) => `<button data-user="${v}" data-id="${l.id}" class="${l.user_status === v ? "on" : ""}">${t}</button>`).join("");
}

function render() {
  const items = filtered();
  $("#count").textContent = `${items.length} annonces`;
  const route = location.hash || "#/";
  document.querySelectorAll("header nav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === route));
  $("#list").hidden = route !== "#/"; $("#map").hidden = route !== "#/map"; $("#detail").hidden = !route.startsWith("#/l/");
  if (route === "#/") $("#list").innerHTML = items.map(card).join("") || "<p>Aucune annonce.</p>";
  if (route === "#/map") renderMap(items);
  if (route.startsWith("#/l/")) renderDetail(+route.slice(4));
}

function renderMap(items) {
  if (!state.map) {
    state.map = L.map("map").setView([50.66, 3.07], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap" }).addTo(state.map);
    for (const d of Object.values(state.crit.commute.destinations))
      L.marker([d.lat, d.lon], { title: d.label }).addTo(state.map).bindPopup(`💼 ${esc(d.label)}`);
  }
  setTimeout(() => state.map.invalidateSize(), 50);
  if (state.layer) state.layer.remove();
  state.layer = L.layerGroup().addTo(state.map);
  const col = (s) => (s >= 75 ? "#2e8b57" : s >= 55 ? "#e08e0b" : "#888");
  for (const l of items) {
    if (l.lat == null) continue;
    const popup = `<a href="#/l/${l.id}"><b>${l.score ?? "?"}/100</b> ${esc(eff(l, "title"))}</a><br>${fmt(eff(l, "price"))} € · ${esc(eff(l, "city"))}<br>${esc(l.address || "")}<br><img src="${esc(photo(l))}" width="200">`;
    const style = { color: col(l.score), fillColor: col(l.score), fillOpacity: 0.85 };
    if (l.geo_precision === "approx") L.circle([l.lat, l.lon], { radius: 250, color: col(l.score), weight: 1, fillOpacity: 0.08 }).addTo(state.layer);
    L.circleMarker([l.lat, l.lon], { radius: l.geo_precision === "exact" ? 9 : 6, ...style,
      dashArray: l.geo_precision === "city" ? "3" : null }).addTo(state.layer).bindPopup(popup);
  }
}

async function renderDetail(id) {
  const l = await fetch(`/api/listings/${id}`).then((r) => r.json());
  const e = l.enrichment || {}, c = l.commute || {};
  const imgs = (l.photos && l.photos.length ? l.photos.map((p) => "/photos/" + p) : l.photo_urls || [])
    .map((u) => `<a href="${esc(u)}" target="_blank"><img src="${esc(u)}" loading="lazy"></a>`).join("");
  const sd = Object.entries(l.score_details || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join("");
  const hist = (l.price_history || []).map((h) => `${fmt(h.price)} € (${new Date(h.date).toLocaleDateString("fr-FR")})`).join(" → ");
  $("#detail").innerHTML = `<p><a href="#/">← retour</a></p>
    <h2>${l.score ?? "?"}/100 — ${esc(eff(l, "title"))}</h2>
    <p class="price">${fmt(eff(l, "price"))} € ${hist ? `<small>(avant : ${hist})</small>` : ""}</p>
    <p>${esc(eff(l, "city"))} ${esc(l.postal_code || "")} · <b>Adresse :</b> ${esc(l.address || "inconnue")} ${e.address_confidence ? `(${esc(e.address_confidence)})` : ""}
      · 🚗 Alex ${c.alexandre ?? "?"} min · Marie ${c.marie ?? "?"} min</p>
    <p><a href="${esc(l.url)}" target="_blank">Voir l'annonce (${esc(l.source)})</a> · ${esc(l.agency || "")}</p>
    <div class="actions">${userButtons(l)}</div>
    <div class="gallery">${imgs}</div>
    ${e.summary_fr ? `<h3>Analyse de l'agent</h3><p>${esc(e.summary_fr)}</p>` : ""}
    ${e.red_flags && e.red_flags.length ? `<p>⚠️ ${e.red_flags.map(esc).join(" · ")}</p>` : ""}
    <h3>Caractéristiques</h3>
    <table>
      <tr><td>Type</td><td>${esc(TYPES[eff(l, "property_type")] || "?")}</td></tr>
      <tr><td>Surface / terrain</td><td>${fmt(eff(l, "surface_m2"))} m² / ${fmt(eff(l, "land_m2"))} m² ${e.garden_shape ? `(jardin : ${esc(e.garden_shape)})` : ""}</td></tr>
      <tr><td>Pièces / chambres</td><td>${l.rooms ?? "?"} / ${eff(l, "bedrooms") ?? "?"} ${e.largest_bedroom_m2 ? `(plus grande ${e.largest_bedroom_m2} m²)` : ""}</td></tr>
      <tr><td>DPE / GES</td><td>${eff(l, "dpe") || "?"} / ${l.ges || "?"}</td></tr>
      <tr><td>Construction</td><td>${l.year_built ?? "?"} ${e.style ? `· style ${esc(e.style)}` : ""}</td></tr>
      <tr><td>Garage / jardin / baignoire</td><td>${[eff(l, "has_garage"), eff(l, "has_garden"), eff(l, "has_bathtub")].map((v) => (v === true ? "oui" : v === false ? "non" : "?")).join(" / ")}</td></tr>
      <tr><td>Travaux</td><td>${esc(eff(l, "works") || "?")}</td></tr>
    </table>
    <h3>Détail du score</h3><table>${sd}</table>
    <h3>Description</h3><pre>${esc(l.description || "")}</pre>`;
}

document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-user]");
  if (b) {
    ev.stopPropagation();
    const l = state.all.find((x) => x.id === +b.dataset.id) || {};
    const v = l.user_status === b.dataset.user ? null : b.dataset.user;
    await fetch(`/api/listings/${b.dataset.id}/user`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_status: v }) });
    l.user_status = v; render(); return;
  }
  const c = ev.target.closest(".card");
  if (c) location.hash = `#/l/${c.dataset.id}`;
});
$("#filters").addEventListener("input", (ev) => {
  $("#o-score").textContent = $("#f-score").value;
  if (ev.target.id === "f-excluded") load(); else render();
});
$("#f-reset").addEventListener("click", () => { document.querySelectorAll("#filters input").forEach((i) => (i.type === "checkbox" ? (i.checked = false) : (i.value = i.type === "range" ? 0 : ""))); [...$("#f-cities").options].forEach((o) => (o.selected = false)); render(); });
window.addEventListener("hashchange", render);
load();
