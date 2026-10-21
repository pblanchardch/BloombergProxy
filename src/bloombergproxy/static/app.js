const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const money = (v, cur = "CHF") => v == null ? "–" : `${Number(v).toLocaleString("de-CH", { minimumFractionDigits: 2 })} ${cur}`;

function load(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, value); } catch { /* ohne Speicher weiter */ }
}

// Anmeldung: Entra ID (MSAL) im Betrieb, Benutzer/Scopes-Felder im Entwicklungsmodus.
const MSAL_URL = "https://cdn.jsdelivr.net/npm/@azure/msal-browser@3.28.1/lib/msal-browser.min.js";
const MSAL_SRI = "sha384-B5PXeUfgiegchZiX5iMRssMrHd9NfPhFnITob1HyW1CY76udptWpBsniaDsH/+BY";
let authConfig = { mode: "header" };
let msalApp = null;

function loadScript(src, integrity) {
  return new Promise((resolve, reject) => {
    const s = Object.assign(document.createElement("script"), { src, integrity, crossOrigin: "anonymous" });
    s.onload = resolve;
    s.onerror = () => reject(new Error("Anmeldebibliothek konnte nicht geladen werden"));
    document.head.append(s);
  });
}

async function initAuth() {
  authConfig = await (await fetch("/api/v1/auth/config")).json();
  if (authConfig.mode !== "entra") {
    $("user").value = load("bbg.user", "");
    $("scopes").value = load("bbg.scopes", "lizenz.read,lizenz.order");
    return Boolean($("user").value);
  }
  $("who").hidden = true;
  await loadScript(MSAL_URL, MSAL_SRI);
  msalApp = new msal.PublicClientApplication({
    auth: {
      clientId: authConfig.clientId,
      authority: `https://login.microsoftonline.com/${authConfig.tenantId}`,
      redirectUri: location.origin,
    },
    cache: { cacheLocation: "sessionStorage" },
  });
  await msalApp.initialize();
  const result = await msalApp.handleRedirectPromise();
  const account = result?.account || msalApp.getAllAccounts()[0];
  if (!account) {
    await msalApp.loginRedirect({ scopes: authConfig.scopes });
    return false;
  }
  msalApp.setActiveAccount(account);
  $("me").textContent = account.username;
  $("me").hidden = false;
  return true;
}

async function authHeaders() {
  if (authConfig.mode !== "entra") return { "X-User": $("user").value, "X-Scopes": $("scopes").value };
  const request = { scopes: authConfig.scopes, account: msalApp.getActiveAccount() };
  try {
    return { Authorization: `Bearer ${(await msalApp.acquireTokenSilent(request)).accessToken}` };
  } catch (e) {
    if (e instanceof msal.InteractionRequiredAuthError) await msalApp.acquireTokenRedirect(request);
    throw e;
  }
}

async function api(path, options = {}) {
  const res = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(await authHeaders()), ...(options.headers || {}) },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
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
    <td>${ids(i)}${i.name ? `<br><span class="hint">${esc(i.name)}</span>` : ""}</td><td>${esc(i.status)}</td><td>${esc(i.source)}</td><td>${esc(i.fieldSets.join(", "))}</td>
    <td>${esc(i.categories.join(", "))}</td><td>${esc(i.subscribedSince)}</td><td>${esc(i.unsubscribeEffective)}</td>
    <td><button data-ts="${esc(i.id)}" data-label="${esc(i.name || i.identifiers[0]?.value)}">Zeitreihe</button>
      ${i.status === "ACTIVE" ? `<button data-unsub='${esc(JSON.stringify(i.identifiers[0]))}'>Abbestellen</button>` : ""}</td>
  </tr>`).join("") || `<tr><td colspan="8" class="hint">Keine Instrumente</td></tr>`;
}
$("universe-filter").addEventListener("submit", (e) => { e.preventDefault(); guard(loadUniverse); });
$("u-rows").addEventListener("click", (e) => {
  if (e.target.dataset?.ts) return guard(() => openSeries(e.target.dataset.ts, e.target.dataset.label));
  const raw = e.target.dataset?.unsub;
  if (!raw) return;
  guard(async () => {
    const o = await api("/orders", { method: "POST", body: JSON.stringify({ type: "UNSUBSCRIBE", instruments: [JSON.parse(raw)] }) });
    toast(`Abbestellt per ${o.effectiveDate}`);
    await loadUniverse();
  });
});

// Zeitreihe (B-06, C1)
const num = (v) => v !== null && v !== "" && isFinite(v) ? Number(v).toLocaleString("de-CH", { maximumFractionDigits: 4 }) : v;
let seriesId = null;
async function openSeries(id, label) {
  seriesId = id;
  $("ts-title").textContent = label;
  const end = new Date(), start = new Date(Date.now() - 30 * 864e5);
  $("ts-from").value ||= start.toISOString().slice(0, 10);
  $("ts-to").value ||= end.toISOString().slice(0, 10);
  $("series").hidden = false;
  await loadSeries();
}
async function loadSeries() {
  const q = new URLSearchParams({ instrumentId: seriesId, field: $("ts-field").value, from: $("ts-from").value, to: $("ts-to").value });
  if ($("ts-asof").value) q.set("asOf", new Date($("ts-asof").value).toISOString().slice(0, 19));
  const ts = await api(`/timeseries?${q}`);
  $("ts-rows").innerHTML = ts.points.slice().reverse().map((p) => `<tr><td>${esc(p.date)}</td>
    <td class="num">${esc(num(p.value))}</td><td class="hint">${esc(new Date(p.recordedAt + "Z").toLocaleString("de-CH"))}</td></tr>`).join("")
    || `<tr><td colspan="3" class="hint">Keine Werte im Zeitraum</td></tr>`;
}
$("ts-form").addEventListener("submit", (e) => { e.preventDefault(); guard(loadSeries); });
$("ts-close").addEventListener("click", () => ($("series").hidden = true));

$("run-delivery").addEventListener("click", () => guard(async () => {
  const r = await api("/deliveries/run", { method: "POST" });
  const done = r.orders.filter((o) => o.status === "COMPLETED").length;
  toast(`Tageslauf: ${done} Bestellung(en) abgeschlossen, ${r.instruments} Instrumente geliefert, `
    + `${r.values ?? 0} neue Werte, ${r.corrections ?? 0} Korrekturen, ${r.removed.length} abbestellt`);
  await loadUniverse();
}));

// Xentis-Abgleich (B1)
$("x-import").addEventListener("click", () => guard(async () => {
  const file = $("x-file").files[0];
  if (!file) throw new Error("Bitte eine Positionsdatei wählen");
  const r = await api(`/positions/import?fileName=${encodeURIComponent(file.name)}`,
    { method: "POST", body: await file.arrayBuffer(), headers: { "Content-Type": "text/csv" } });
  $("x-result").innerHTML = `<p>${r.rows} Positionen per ${esc(r.asOfDates.join(", "))} importiert`
    + `${r.skipped.length ? `, <span class="warn">${r.skipped.length} Zeilen übersprungen</span>` : ""}.</p>`;
}));
async function reconcile(dryRun) {
  const r = await api("/universe/reconcile", { method: "POST", body: JSON.stringify({ dryRun }) });
  const rows = r.proposals.map((p) => `<tr><td>${esc(p.isin)}</td><td>${esc(p.action === "SUBSCRIBE" ? "Bestellen" : "Abbestellen")}</td><td>${esc(p.reason)}</td></tr>`).join("");
  const orders = Object.values(r.orders).map((o) => `${esc(o.status)}`).join(", ");
  $("x-result").innerHTML = `<p>${dryRun ? "Testlauf" : "Abgleich"} mit Positionen per ${esc(r.asOf)}: `
    + `${r.subscribe} bestellen, ${r.unsubscribe} abbestellen${orders ? ` · Bestellungen: ${orders}` : ""}.</p>`
    + r.notes.map((n) => `<p class="bad">${esc(n)}</p>`).join("")
    + (rows ? `<table><thead><tr><th>ISIN</th><th>Aktion</th><th>Grund</th></tr></thead><tbody>${rows}</tbody></table>` : "");
  if (!dryRun) await loadUniverse();
}
$("x-dry").addEventListener("click", () => guard(() => reconcile(true)));
$("x-run").addEventListener("click", () => guard(() => reconcile(false)));

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
    ...($("o-type").value === "BACKFILL" ? {
      fields: $("o-fields").value.split(",").map((f) => f.trim()).filter(Boolean),
      from: $("o-from").value || null,
      to: $("o-to").value || null,
    } : {}),
  };
}
$("o-type").addEventListener("change", () => {
  $("o-fieldsets").hidden = $("o-type").value !== "SUBSCRIBE";
  $("o-history").hidden = $("o-type").value !== "BACKFILL";
});

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

// Datenqualität (D1)
const DQ_LABEL = { MISSING_VALUE: "Wert fehlt", STALE_PRICE: "Preis unverändert", OUTLIER: "Ausreisser" };
let instrumentNames = {};
async function loadQuality() {
  const q = $("dq-status").value ? `?status=${$("dq-status").value}` : "";
  const [rows, page] = await Promise.all([api(`/data-quality/exceptions${q}`), api("/instruments?pageSize=500")]);
  instrumentNames = Object.fromEntries(page.items.map((i) => [i.id, i.name || i.identifiers[0]?.value]));
  $("dq-rows").innerHTML = rows.map((x) => `<tr>
    <td>${esc(x.date)}</td><td>${esc(DQ_LABEL[x.type] || x.type)}</td><td>${esc(instrumentNames[x.instrumentId] || x.instrumentId)}</td>
    <td>${esc(x.field)}</td><td>${esc(x.detail)}</td><td>${esc(x.status)}${x.handledBy ? `<br><span class="hint">${esc(x.handledBy)}</span>` : ""}</td>
    <td>${x.status === "OPEN" ? `<button data-dq="${esc(x.id)}" data-to="ACKNOWLEDGED">Übernehmen</button>` : ""}
        ${x.status !== "RESOLVED" ? `<button data-dq="${esc(x.id)}" data-to="RESOLVED">Erledigt</button>` : ""}</td>
  </tr>`).join("") || `<tr><td colspan="7" class="hint">Keine Ausnahmen</td></tr>`;
}
$("dq-form").addEventListener("submit", (e) => { e.preventDefault(); guard(loadQuality); });
$("dq-rows").addEventListener("click", (e) => {
  const { dq, to } = e.target.dataset || {};
  if (!dq) return;
  const comment = to === "RESOLVED" ? prompt("Kommentar (optional)") : null;
  guard(async () => {
    await api(`/data-quality/exceptions/${dq}/status`, { method: "POST", body: JSON.stringify({ status: to, comment }) });
    await loadQuality();
  });
});

// Meldungen (D4)
const SENT_LABEL = { SENT: "versandt", STORED: "nur gespeichert", FAILED: "Versand fehlgeschlagen", PENDING: "ausstehend" };
async function loadMessages() {
  const rows = await api("/notifications");
  $("m-rows").innerHTML = rows.map((n) => `<tr>
    <td>${esc(new Date(n.createdAt + "Z").toLocaleString("de-CH"))}</td>
    <td><strong>${esc(n.title)}</strong><br>${esc(n.text)}</td>
    <td class="${n.status === "FAILED" ? "bad" : "hint"}">${esc(SENT_LABEL[n.status] || n.status)}${n.error ? `<br>${esc(n.error)}` : ""}</td>
  </tr>`).join("") || `<tr><td colspan="3" class="hint">Keine Meldungen</td></tr>`;
}

const refresh = { universe: () => guard(loadUniverse), approvals: () => guard(loadApprovals), costs: () => guard(loadCosts),
  quality: () => guard(loadQuality), messages: () => guard(loadMessages) };
guard(async () => {
  if (await initAuth()) { refresh.universe(); await loadFieldSets(); }
});
