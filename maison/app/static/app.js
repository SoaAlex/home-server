// UI sans framework : charge /api/listings une fois, filtre/tri côté client.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { all: [], crit: null, map: null, layer: null, markers: {}, chips: new Set(), type: "", dpe: "",
                cities: new Set(), photos: [], photoIdx: 0, lastRoute: "#/", openId: null };
const DPE = "ABCDEFG";
const DPE_COL = { A: "#009c6d", B: "#52b153", C: "#a5cc74", D: "#e0c90c", E: "#f0b50f", F: "#eb8235", G: "#d7221f" };
const TYPES = { detached: "Individuelle", semi: "Semi-individuelle", terraced: "Mitoyenne", unknown: "Type inconnu" };
const STYLES = { traditional: "traditionnelle", modern: "moderne", cubic: "cubique", mixed: "mixte" };
const SHAPES = { square: "carré", long: "en longueur", irregular: "irrégulier", unknown: "forme ?" };
const WORKS = { none: "aucun", light: "rafraîchissement", heavy: "travaux importants" };
const SCORE_MAX = { ville: 25, prix: 15, type: 12, jardin: 12, dpe: 8, travaux: 8, baignoire: 3, chambres: 7, trajet: 8, style: 4 };

const fmt = (n) => (n == null || n === "" || isNaN(n) ? "?" : Math.round(Number(n)).toLocaleString("fr-FR"));
const eff = (l, k) => (l.enrichment && l.enrichment[k] != null ? l.enrichment[k] : l[k]);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const scoreCol = (s) => (s >= 75 ? "var(--good)" : s >= 55 ? "var(--mid)" : "var(--low)");
const scoreHex = (s) => (s >= 75 ? "#1f9d6b" : s >= 55 ? "#e29a1b" : "#8a94a3");
const isNew = (l) => Date.now() - new Date(l.first_seen) < 48 * 3600e3;
const dests = () => (state.crit && state.crit.commute && state.crit.commute.destinations) || {};

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
const photos = (l) => (l.photos && l.photos.length ? l.photos.map((p) => "/photos/" + p) : l.photo_urls || []);
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
  return [["favorite", "★", "Favori"], ["to_visit", "🗓", "À visiter"], ["rejected", "✕", "Rejeter"]]
    .map(([v, t, title]) => `<button data-user="${v}" data-id="${l.id}" title="${title}" class="${l.user_status === v ? "on" : ""}">${t}</button>`).join("");
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
  const showRejected = $("#f-rejected").checked, showDups = $("#f-dups").checked;
  const ch = state.chips;
  let out = state.all.filter((l) => {
    if ((l.score || 0) < minScore && !l.excluded_reason) return false;
    if ((eff(l, "price") || 0) > maxPrice) return false;
    if (minLand && (+eff(l, "land_m2") || 0) < minLand) return false;
    const t = eff(l, "property_type");
    if (state.type && (state.type === "other" ? ["detached", "semi"].includes(t) : t !== state.type)) return false;
    if (state.dpe && (!eff(l, "dpe") || DPE.indexOf(eff(l, "dpe")) > DPE.indexOf(state.dpe))) return false;
    if (state.cities.size && !state.cities.has(lKey(l))) return false;
    if (ch.has("garage") && eff(l, "has_garage") !== true) return false;
    if (ch.has("garden") && eff(l, "has_garden") !== true) return false;
    if (ch.has("bath") && eff(l, "has_bathtub") !== true) return false;
    if (ch.has("noworks") && eff(l, "works") !== "none") return false;
    if (ch.has("fav") && !["favorite", "to_visit", "visited"].includes(l.user_status)) return false;
    if (ch.has("new") && !isNew(l)) return false;
    if (!showRejected && l.user_status === "rejected") return false;
    if (q && !norm([eff(l, "title"), eff(l, "city"), l.address, l.agency, l.source].join(" ")).includes(q)) return false;
    return true;
  });
  const key = {
    score: (l) => -(l.score || 0), price: (l) => eff(l, "price") || 9e9,
    ppm: (l) => (eff(l, "price") || 9e9) / (+eff(l, "surface_m2") || 1),
    new: (l) => -new Date(l.first_seen), commute: (l) => (l.commute && l.commute.total_min) || 999,
  }[$("#f-sort").value];
  out.sort((a, b) => key(a) - key(b));
  const groups = {};
  for (const l of state.all) if (l.fingerprint) (groups[l.fingerprint] ||= []).push(l);
  if (!showDups) {
    const seen = new Set();
    out = out.filter((l) => !l.fingerprint || (!seen.has(l.fingerprint) && seen.add(l.fingerprint)));
  }
  out.forEach((l) => (l._others = l.fingerprint ? groups[l.fingerprint].filter((o) => o.id !== l.id) : []));
  return out;
}

/* ---------- liste ---------- */
function card(l) {
  const p = photos(l)[0];
  const s = l.score ?? 0;
  const price = eff(l, "price"), surf = +eff(l, "surface_m2");
  const works = eff(l, "works"), e = l.enrichment || {};
  const tags = [
    isNew(l) ? `<span class="tag new">Nouveau</span>` : "",
    l.price_history && l.price_history.length ? `<span class="tag drop">↘ Baisse</span>` : "",
    l.excluded_reason ? `<span class="tag bad">${esc(l.excluded_reason)}</span>` : "",
    l.enrichment_status === "done" ? `<span class="tag" title="Analysée par l'agent">✦ analysée</span>` : "",
  ].join("");
  const street = l.address ? " · " + esc(l.address.replace(/,?\s*\d{5}.*$/, "")) : "";
  return `<article class="card ${l.user_status === "rejected" || l.excluded_reason ? "dim" : ""}" data-id="${l.id}">
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
      <div class="foot"><span>🚗 ${trips(l)}</span><span>${esc(l.source)}${l._others.length ? " +" + l._others.length : ""}</span></div>
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
async function openDetail(id) {
  if (state.openId === id && !$("#drawer").hidden) return;
  state.openId = id;
  const l = await fetch(`/api/listings/${id}`).then((r) => r.json());
  const e = l.enrichment || {}, c = l.commute || {};
  state.photos = photos(l); state.photoIdx = 0;
  const bars = Object.entries(l.score_details || {}).map(([k, v]) =>
    `<div class="bar"><span>${esc(k)}</span><div class="track"><div class="fill ${v < 0 ? "neg" : ""}" style="width:${Math.min(100, (Math.abs(v) / (SCORE_MAX[k] || 10)) * 100)}%"></div></div><b>${v > 0 ? "+" : ""}${v}</b></div>`).join("");
  const hist = (l.price_history || []).map((h) => `${fmt(h.price)} € <span class="muted">(${new Date(h.date).toLocaleDateString("fr-FR")})</span>`).join(" → ");
  const yn = (v) => (v === true ? "oui" : v === false ? "non" : "?");
  const price = eff(l, "price"), surf = +eff(l, "surface_m2");
  $("#detail").innerHTML = `
    <div class="d-head"><button class="icon-btn" data-close title="Fermer (Échap)">✕</button>
      <h2>${esc(eff(l, "title"))}</h2><div class="fav">${favButtons(l)}</div>
      <a class="btn" href="${esc(l.url)}" target="_blank" rel="noopener">Annonce ↗</a></div>
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
          ${hist ? `<div style="margin-top:6px">Historique : ${hist}</div>` : ""}
          ${l.excluded_reason ? `<div style="margin-top:8px"><span class="b no">Exclue : ${esc(l.excluded_reason)}</span></div>` : ""}
        </div>
        ${e.summary_fr ? `<div class="box"><h3>✦ Analyse de l'agent</h3><div>${esc(e.summary_fr)}</div>
          ${e.red_flags && e.red_flags.length ? `<ul class="flags">${e.red_flags.map((x) => `<li>⚠️ ${esc(x)}</li>`).join("")}</ul>` : ""}</div>` : ""}
        <div class="box"><h3>Caractéristiques</h3><dl class="kv">
          <dt>Type</dt><dd>${esc(TYPES[eff(l, "property_type")] || "?")}${e.style ? ` · style ${esc(STYLES[e.style] || e.style)}` : ""}</dd>
          <dt>Surface</dt><dd>${fmt(eff(l, "surface_m2"))} m² · ${l.rooms ?? "?"} pièces · ${eff(l, "bedrooms") ?? "?"} chambres${e.largest_bedroom_m2 ? ` (plus grande ${e.largest_bedroom_m2} m²)` : ""}</dd>
          <dt>Terrain</dt><dd>${fmt(eff(l, "land_m2"))} m²${e.garden_m2_estimate ? ` · jardin ≈ ${fmt(e.garden_m2_estimate)} m²` : ""}${e.garden_shape ? ` · ${esc(SHAPES[e.garden_shape] || e.garden_shape)}` : ""}</dd>
          <dt>Énergie</dt><dd>DPE ${dpePill(eff(l, "dpe"))} &nbsp; GES ${dpePill(l.ges)}</dd>
          <dt>Construction</dt><dd>${l.year_built ?? "?"}</dd>
          <dt>Garage / jardin</dt><dd>${yn(eff(l, "has_garage"))} / ${yn(eff(l, "has_garden"))}</dd>
          <dt>Baignoire</dt><dd>${yn(eff(l, "has_bathtub"))}</dd>
          <dt>Travaux</dt><dd>${esc(WORKS[eff(l, "works")] || "?")}</dd>
          <dt>Source</dt><dd>${esc(l.source)}${l.agency ? " · " + esc(l.agency) : ""} · vue le ${new Date(l.first_seen).toLocaleDateString("fr-FR")}</dd>
        </dl></div>
        <div class="box"><h3>Description</h3><div class="desc">${esc((l.description || "").replace(/<br\s*\/?>/gi, "\n"))}</div></div>
      </div>
      <div class="col">
        <div class="box"><h3>Localisation</h3>
          <div>${esc(l.address || "Adresse inconnue")}${e.address_confidence && e.address_confidence !== "none" ? ` <span class="b ${e.address_confidence === "exact" ? "yes" : "mid"}">${esc(e.address_confidence)}</span>` : ""}</div>
          ${l.lat != null ? `<div class="minimap" id="minimap"></div>` : ""}</div>
        <div class="box"><h3>Trajets voiture</h3>
          ${Object.entries(dests()).map(([k, d]) => `<div class="trip"><span>${esc(d.label)}</span><b>${c[k] ?? "?"} min</b></div>`).join("")}</div>
        <div class="box"><h3>Détail du score</h3>${bars || "<span class='muted'>—</span>"}</div>
      </div>
    </div>`;
  $("#drawer").hidden = false;
  $("#detail").scrollTop = 0;
  if (l.lat != null) {
    const mm = L.map("minimap", { zoomControl: false, attributionControl: false }).setView([l.lat, l.lon], l.geo_precision === "city" ? 13 : 15);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").addTo(mm);
    if (l.geo_precision === "exact") L.circleMarker([l.lat, l.lon], { radius: 9, color: "#fff", weight: 2, fillColor: scoreHex(l.score), fillOpacity: 1 }).addTo(mm);
    else L.circle([l.lat, l.lon], { radius: l.geo_precision === "city" ? 800 : 250, color: scoreHex(l.score) }).addTo(mm);
  }
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
document.addEventListener("keydown", (ev) => {
  if ($("#drawer").hidden) return;
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
