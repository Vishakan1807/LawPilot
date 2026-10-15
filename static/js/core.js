/* LawPilot front-end core: API client, routing helpers, rendering utilities. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

const State = { user: null, status: null, coverage: null };
const Pages = {};

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(method, path, data) {
  const opts = { method, headers: { "X-Requested-With": "LawPilot" }, credentials: "same-origin" };
  if (data instanceof FormData) {
    opts.body = data;
  } else if (data !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(data);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError("Cannot reach the server. Check your connection.", 0);
  }
  const type = res.headers.get("Content-Type") || "";
  const payload = type.includes("application/json") ? await res.json() : null;
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      State.user = null;
      navigate("#/login");
    }
    throw new ApiError((payload && payload.error) || `Request failed (${res.status})`, res.status);
  }
  return payload;
}

/* ── Feedback ──────────────────────────────────────────── */
function toast(message, bad = false) {
  let wrap = $(".toast-wrap");
  if (!wrap) {
    wrap = document.createElement("div");
    wrap.className = "toast-wrap";
    document.body.appendChild(wrap);
  }
  const t = document.createElement("div");
  t.className = "toast" + (bad ? " bad" : "");
  t.textContent = message;
  wrap.appendChild(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3500);
}

function fail(err) {
  console.error(err);
  toast(err.message || String(err), true);
}

function modal(html, { wide = false } = {}) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div>`;
  document.body.appendChild(back);
  const close = () => back.remove();
  back.addEventListener("click", e => { if (e.target === back) close(); });
  $$("[data-close]", back).forEach(b => b.addEventListener("click", close));
  const onKey = e => { if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); } };
  document.addEventListener("keydown", onKey);
  const first = $("input, select, textarea", back);
  if (first) setTimeout(() => first.focus(), 30);
  return { el: back, close };
}

function confirmDialog(message, { okLabel = "Confirm", danger = false } = {}) {
  return new Promise(resolve => {
    const m = modal(`<p>${esc(message)}</p><div class="row end">
      <button class="btn" data-close>Cancel</button>
      <button class="btn ${danger ? "danger" : "primary"}" id="cd-ok">${esc(okLabel)}</button></div>`);
    $("#cd-ok", m.el).addEventListener("click", () => { m.close(); resolve(true); });
    $$("[data-close]", m.el).forEach(b => b.addEventListener("click", () => resolve(false)));
  });
}

async function withBusy(button, fn) {
  const label = button ? button.innerHTML : "";
  if (button) { button.disabled = true; button.innerHTML = `<span class="spinner"></span> ${label}`; }
  try { return await fn(); } catch (e) { fail(e); } finally { if (button) { button.disabled = false; button.innerHTML = label; } }
}

/* ── Formatting ────────────────────────────────────────── */
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  if (isNaN(d)) return esc(iso);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
function fmtDateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return esc(iso);
  const hasTime = iso.length > 10;
  return d.toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric",
    ...(hasTime ? { hour: "2-digit", minute: "2-digit" } : {}) });
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return fmtDate(iso);
}
function badge(text, kind = "") { return `<span class="badge ${kind}">${esc(text)}</span>`; }

const LABELS = {
  legal_status: { good_law: ["Good law", "ok"], overruled: ["Overruled", "bad"], partly_overruled: ["Partly overruled", "warn"],
    repealed: ["Repealed", "bad"], amended: ["Amended", "warn"], unknown: ["Status unchecked", ""] },
  verification: { verified_official: ["Verified (official)", "ok"], editorial_summary: ["Editorial summary", "warn"],
    unverified: ["Unverified text", ""] },
  kind: { judgment: "Judgment", statute: "Act", constitution: "Constitution", rule: "Rules", notification: "Notification",
    commentary: "Commentary", editorial_summary: "Case summary", user_document: "Matter document" },
  court_level: { supreme: "Supreme Court", high: "High Court", tribunal: "Tribunal", district: "Lower court",
    none: "No court (Acts, summaries, documents)" },
  outcome: {
    answered: ["Answer based on retrieved authorities", "ok"],
    insufficient_evidence: ["Not enough authority to answer", "warn"],
    no_relevant_authority: ["Nothing relevant in the corpus", "warn"],
    retrieval_failed: ["Search failed", "bad"],
    evidence_only: ["Relevant passages (no AI answer)", "info"],
  },
};
function statusBadge(s) { const [t, k] = LABELS.legal_status[s] || LABELS.legal_status.unknown; return badge(t, k); }
function verificationBadge(v) { const [t, k] = LABELS.verification[v] || LABELS.verification.unverified; return badge(t, k); }
function outcomeBadge(o) { const [t, k] = LABELS.outcome[o] || [o, ""]; return badge(t, k); }
function kindLabel(k) { return LABELS.kind[k] || k || ""; }

function can(perm) { return !!(State.user && State.user.permissions.includes(perm)); }

function formObject(form) {
  const out = {};
  new FormData(form).forEach((v, k) => {
    if (k in out) out[k] = [].concat(out[k], v); else out[k] = v;
  });
  // Checkboxes marked data-bool become true/false; other checkboxes are multi-select values.
  $$("input[type=checkbox][data-bool]", form).forEach(cb => { out[cb.name] = cb.checked; });
  return out;
}

/* Escape text, then turn [E3] markers into clickable evidence chips. */
function citeify(text) {
  return esc(text).replace(/\[(E\d+)\]/g, (_, id) => `<button class="cite" data-cite="${id}">${id}</button>`);
}

function options(map, selected, { blank } = {}) {
  const entries = Array.isArray(map) ? map.map(v => (Array.isArray(v) ? v : [v, v])) : Object.entries(map);
  return (blank !== undefined ? `<option value="">${esc(blank)}</option>` : "") +
    entries.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(selected ?? "") ? "selected" : ""}>${esc(l)}</option>`).join("");
}

/* ── Routing ───────────────────────────────────────────── */
function navigate(hash) { if (location.hash !== hash) location.hash = hash; else window.dispatchEvent(new HashChangeEvent("hashchange")); }

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/home";
  const [path, qs] = raw.split("?");
  return { path, query: new URLSearchParams(qs || "") };
}

function setMain(html) {
  const main = $("#main");
  main.innerHTML = html;
  main.scrollTop = 0;
  window.scrollTo(0, 0);
  return main;
}

function loading(text = "Loading...") { return `<div class="loading"><span class="spinner"></span> ${esc(text)}</div>`; }

async function loadMattersForSelect() {
  try {
    const r = await api("GET", "/api/matters?status=open");
    return r.matters.filter(m => m.my_access);
  } catch (e) { return []; }
}
