const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const money = (v, cur = "CHF") => v == null ? "–" : `${Number(v).toLocaleString("de-CH", { minimumFractionDigits: 2 })} ${cur}`;

function load(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, value); } catch { /* ohne Speicher weiter */ }
}

$("user").value = load("bbg.user", "");
$("scopes").value = load("bbg.scopes", "lizenz.read,lizenz.order");

async function api(path, options = {}) {
  const res = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-User": $("user").value,
      "X-Scopes": $("scopes").value,
      ...(options.headers || {}),
    },
  });
  const body = res.status === 204 ? null : await res.json();
  if (!res.ok) throw new Error(body?.message || body?.detail || res.statusText);
  return body;
}

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  setTimeout(() => (t.hidden = true), 4000);
}

async function guard(fn) {
  try { await fn(); } catch (e) { toast(e.message); }
}

// Navigation
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll("main section").forEach((s) => (s.hidden = s.id !== b.dataset.tab));
  refresh[b.dataset.tab]?.();
}));

$("who").addEventListener("submit", (e) => {
  e.preventDefault();
  save("bbg.user", $("user").value);
  save("bbg.scopes", $("scopes").value);
  refresh.universe();
  loadFieldSets();
});

// Universum
const ids = (i) => i.identifiers.map((x) => `${x.type} ${x.value}`).join("<br>");
async function loadUniverse() {
  const q = new URLSearchParams({ pageSize: 200 });
  if ($("u-search").value) q.set("search", $("u-search").value);
  if ($("u-status").value) q.set("status", $("u-status").value);
  const page = await api(`/instruments?${q}`);
  $("u-rows").innerHTML = page.items.map((i) => `<tr>
    <td>${ids(i)}</td><td>${esc(i.status)}</td><td>${esc(i.fieldSets.join(", "))}</td>
    <td>${esc(i.categories.join(", "))}</td><td>${esc(i.subscribedSince)}</td><td>${esc(i.unsubscribeEffective)}</td>
    <td>${i.status === "ACTIVE" ? `<button data-unsub='${esc(JSON.stringify(i.identifiers[0]))}'>Abbestellen</button>` : ""}</td>
  </tr>`).join("") || `<tr><td colspan="7" class="hint">Keine Instrumente</td></tr>`;
}
$("universe-filter").addEventListener("submit", (e) => { e.preventDefault(); guard(loadUniverse); });
$("u-rows").addEventListener("click", (e) => {
  const raw = e.target.dataset?.unsub;
  if (!raw) return;
  guard(async () => {
    const o = await api("/orders", { method: "POST", body: JSON.stringify({ type: "UNSUBSCRIBE", instruments: [JSON.parse(raw)] }) });
    toast(`Abbestellt per ${o.effectiveDate}`);
    await loadUniverse();
  });
});

// Bestellen
async function loadFieldSets() {
  const sets = await api("/field-sets");
  $("o-fieldsets").innerHTML = `<legend>Feldlisten</legend>` + (sets.map((fs) => `<label>
    <input type="checkbox" value="${esc(fs.id)}"> ${esc(fs.name)}
    <span class="hint">&nbsp;· ${esc(fs.categories.join(", "))} · ${fs.fields.length} Felder</span></label>`).join("")
    || `<p class="hint">Noch keine Feldlisten angelegt.</p>`);
}

function orderBody() {
  return {
    type: $("o-type").value,
    instruments: $("o-ids").value.split(/\n+/).map((v) => v.trim()).filter(Boolean).map((value) => ({ type: $("o-idtype").value, value })),
    fieldSets: $("o-type").value === "SUBSCRIBE"
      ? [...document.querySelectorAll("#o-fieldsets input:checked")].map((c) => c.value) : [],
    reason: $("o-reason").value || null,
  };
}

function renderEstimate(est, order) {
  const rows = est.byCategory.map((c) => `<tr><td>${esc(c.category)}</td><td class="num">${c.newUniqueInstruments}</td><td class="num">${money(c.cost, est.currency)}</td></tr>`).join("");
  $("o-result").innerHTML = `<div class="card">
    ${order ? `<p><strong>Bestellung ${esc(order.status)}</strong>${order.requiresApproval ? ' <span class="warn">– wartet auf Freigabe</span>' : ""}</p>` : ""}
    <p>Mehrkosten diesen Monat: <strong>${money(est.additionalMonthlyCost, est.currency)}</strong></p>
    ${est.alreadyBilledInstruments ? `<p class="hint">${est.alreadyBilledInstruments} Instrument(e) diesen Monat schon abgerechnet – ohne Mehrkosten.</p>` : ""}
    ${est.duplicates.length ? `<p class="warn">Doppelt oder bereits im Universum, wird übersprungen: ${esc(est.duplicates.map((d) => d.value).join(", "))}</p>` : ""}
    ${est.unpricedCategories.length ? `<p class="warn">Ohne hinterlegten Preis: ${esc(est.unpricedCategories.join(", "))} – Freigabe nötig.</p>` : ""}
    ${rows ? `<table><thead><tr><th>Kategorie</th><th>Neue Instrumente</th><th>Kosten</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
  </div>`;
}

$("o-estimate").addEventListener("click", () => guard(async () => {
  renderEstimate(await api("/orders/estimate", { method: "POST", body: JSON.stringify(orderBody()) }));
}));
$("order-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guard(async () => {
    const order = await api("/orders", { method: "POST", body: JSON.stringify(orderBody()), headers: { "Idempotency-Key": crypto.randomUUID() } });
    renderEstimate(order.estimate, order);
  });
});

// Freigaben
async function loadApprovals() {
  const page = await api("/orders?status=PENDING_APPROVAL");
  $("a-rows").innerHTML = page.items.map((o) => `<tr>
    <td>${esc(new Date(o.requestedAt + "Z").toLocaleString("de-CH"))}</td><td>${esc(o.requestedBy)}</td><td>${esc(o.type)}</td>
    <td>${esc(o.instruments.map((i) => i.value).join(", "))}</td>
    <td class="num">${money(o.estimate?.additionalMonthlyCost, o.estimate?.currency)}${o.estimate?.unpricedCategories?.length ? '<br><span class="warn">Preis fehlt</span>' : ""}</td>
    <td><button data-approve="${esc(o.id)}">Freigeben</button> <button data-reject="${esc(o.id)}">Ablehnen</button></td>
  </tr>`).join("") || `<tr><td colspan="6" class="hint">Keine offenen Freigaben</td></tr>`;
}
$("a-rows").addEventListener("click", (e) => {
  const { approve, reject } = e.target.dataset || {};
  if (approve) guard(async () => { await api(`/orders/${approve}/approve`, { method: "POST", body: "{}" }); toast("Freigegeben"); loadApprovals(); });
  if (reject) {
    const comment = prompt("Begründung der Ablehnung");
    if (comment) guard(async () => { await api(`/orders/${reject}/reject`, { method: "POST", body: JSON.stringify({ comment }) }); toast("Abgelehnt"); loadApprovals(); });
  }
});

// Kosten
$("c-month").value = new Date().toISOString().slice(0, 7);
async function loadCosts() {
  const rows = await api(`/usage?month=${$("c-month").value}`);
  $("c-rows").innerHTML = rows.map((r) => `<tr><td>${esc(r.category)}</td><td class="num">${r.uniqueInstruments}</td>
    <td class="num">${r.cost == null ? '<span class="warn">Preis fehlt</span>' : money(r.cost, r.currency)}</td></tr>`).join("")
    || `<tr><td colspan="3" class="hint">Keine Nutzung in diesem Monat</td></tr>`;
}
$("cost-form").addEventListener("submit", (e) => { e.preventDefault(); guard(loadCosts); });

const refresh = { universe: () => guard(loadUniverse), approvals: () => guard(loadApprovals), costs: () => guard(loadCosts) };
if ($("user").value) { refresh.universe(); guard(loadFieldSets); }
