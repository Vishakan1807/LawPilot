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

/* ── Themes ────────────────────────────────────────────── */
const THEMES = { brief: "Brief", blackcoat: "Black Coat", courtroom: "Courtroom" };
const THEME_ICONS = {
  brief: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M3.5 13h17"/><path d="M12 13l-2 4M12 13l2 4"/></svg>',
  blackcoat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4l8 4.5L20 4"/><rect x="7.5" y="9" width="4" height="11" rx="1"/><rect x="12.5" y="9" width="4" height="11" rx="1"/></svg>',
  courtroom: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.5 7.5l4-4 6 6-4 4z"/><path d="M13 5l6 6"/><path d="M12.5 11.5L4 20"/><path d="M13 21h8"/></svg>',
};
// Earlier names, kept so saved preferences carry over.
const LEGACY_THEMES = { light: "brief", parchment: "brief", dark: "blackcoat", midnight: "blackcoat", chambers: "courtroom" };

function normaliseTheme(t) {
  if (LEGACY_THEMES[t]) return LEGACY_THEMES[t];
  return THEMES[t] ? t : "system";
}
function storedTheme() { try { return localStorage.getItem("lp_theme"); } catch (e) { return null; } }
function rememberTheme(t) { try { localStorage.setItem("lp_theme", t); } catch (e) { /* private mode */ } }
function currentThemePref() {
  const fromUser = State.user && State.user.prefs && State.user.prefs.theme;
  return normaliseTheme(fromUser || storedTheme() || "system");
}
function effectiveTheme(pref) {
  if (THEMES[pref]) return pref;
  return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "blackcoat" : "brief";
}

/* Apply the saved theme (or one passed in). "system" follows the device's light/dark setting. */
function applyTheme(pref = currentThemePref(), animate = false) {
  const html = document.documentElement;
  if (animate) {
    html.classList.add("theme-anim");
    setTimeout(() => html.classList.remove("theme-anim"), 420);
  }
  if (THEMES[pref]) html.setAttribute("data-theme", pref); else html.removeAttribute("data-theme");
  rememberTheme(pref);
  syncThemeSwitches();
}

function themeSwitchHtml() {
  return `<div class="theme-switch" role="radiogroup" aria-label="Colour theme"><span class="ts-thumb" aria-hidden="true"></span>
    ${Object.entries(THEMES).map(([k, label]) => `<button type="button" role="radio" aria-checked="false" data-theme-opt="${k}"
      title="${label} theme" aria-label="${label} theme" tabindex="-1">${THEME_ICONS[k]}</button>`).join("")}</div>`;
}

function syncThemeSwitches() {
  const eff = effectiveTheme(currentThemePref());
  const keys = Object.keys(THEMES);
  $$(".theme-switch").forEach(sw => {
    sw.style.setProperty("--i", keys.indexOf(eff));
    $$("button", sw).forEach(b => {
      const on = b.dataset.themeOpt === eff;
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    });
  });
  $$(".ts-label").forEach(l => { l.textContent = THEMES[eff]; });
}

function setTheme(theme) {
  if (!THEMES[theme] || theme === currentThemePref()) return applyTheme(theme);
  if (State.user) {
    State.user.prefs = { ...(State.user.prefs || {}), theme };
    api("PATCH", "/api/account/profile", { prefs: { theme } }).catch(() => { /* kept locally anyway */ });
  }
  applyTheme(theme, true);
}

function wireThemeSwitches(root = document) {
  $$(".theme-switch", root).forEach(sw => {
    if (sw.dataset.wired) return;
    sw.dataset.wired = "1";
    $$("button", sw).forEach(b => b.addEventListener("click", () => setTheme(b.dataset.themeOpt)));
    sw.addEventListener("keydown", e => {        // arrow keys move between the three options
      const keys = Object.keys(THEMES);
      const i = keys.indexOf(effectiveTheme(currentThemePref()));
      let next = null;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") next = keys[(i + 1) % keys.length];
      if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = keys[(i + keys.length - 1) % keys.length];
      if (next) { e.preventDefault(); setTheme(next); $(`[data-theme-opt="${next}"]`, sw).focus(); }
    });
  });
  syncThemeSwitches();
}

if (window.matchMedia) {
  const mq = matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => { if (currentThemePref() === "system") syncThemeSwitches(); };
  if (mq.addEventListener) mq.addEventListener("change", onChange); else if (mq.addListener) mq.addListener(onChange);
}

/* ── Show / hide password ──────────────────────────────────
   Every password field gets an eye button, wherever it is rendered (sign-in, sign-up, reset,
   change password, API keys). A MutationObserver covers fields added later by any page. */
const EYE_OPEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_SHUT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.9 10.9 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.1M6.6 6.6C3.9 8.4 2 12 2 12s3.5 7 10 7a9.8 9.8 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

function enhancePasswordInput(input) {
  if (input.dataset.pwEnhanced) return;
  input.dataset.pwEnhanced = "1";
  const wrap = document.createElement("span");
  wrap.className = "pw-wrap";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pw-toggle";
  const sync = () => {
    const shown = input.type === "text";
    btn.innerHTML = shown ? EYE_SHUT : EYE_OPEN;
    btn.setAttribute("aria-pressed", String(shown));
    btn.setAttribute("aria-label", shown ? "Hide password" : "Show password");
    btn.title = shown ? "Hide password" : "Show password";
  };
  btn.addEventListener("click", () => {
    const pos = [input.selectionStart, input.selectionEnd];
    input.type = input.type === "password" ? "text" : "password";
    sync();
    input.focus();
    try { input.setSelectionRange(pos[0], pos[1]); } catch (e) { /* not supported for this input */ }
  });
  // Never leave a password visible after the form is submitted or the field loses its form.
  if (input.form) input.form.addEventListener("submit", () => { input.type = "password"; sync(); });
  sync();
  wrap.appendChild(btn);
}

function enhancePasswordInputs(root = document) {
  $$('input[type="password"]', root).forEach(enhancePasswordInput);
}

new MutationObserver(records => {
  for (const r of records) {
    r.addedNodes.forEach(n => {
      if (n.nodeType !== 1) return;
      if (n.matches && n.matches('input[type="password"]')) enhancePasswordInput(n);
      else if (n.querySelectorAll) enhancePasswordInputs(n);
    });
  }
}).observe(document.documentElement, { childList: true, subtree: true });
