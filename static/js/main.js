/* App shell and router. */
"use strict";

// The advocate's collar bands: the mark of LawPilot AI.
const ADVOCATE_BANDS = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M2.8 4.2L12 8.9l9.2-4.7-1.5 3.9L12 12 4.3 8.1z"/><path d="M8.9 11.6h2.5v8.8c0 .5-.4.9-.9.9H8c-.6 0-1-.5-.9-1z"/><path d="M12.6 11.6h2.5l1.8 8.7c.1.5-.3 1-.9 1h-2.5c-.5 0-.9-.4-.9-.9z"/></svg>';

const NAV = [
  { href: "#/home", label: "Home", ico: "⌂" },
  { href: "#/matters", label: "Matters", ico: "▤" },
  { href: "#/research", label: "Research", ico: "⌕" },
  { href: "#/library", label: "Library", ico: "❏" },
  { href: "#/drafts", label: "Drafts", ico: "✎" },
  { href: "#/tasks", label: "Tasks & requests", ico: "✓" },
  { section: "Workspace" },
  { href: "#/firm", label: "Firm & team", ico: "☷" },
  { href: "#/activity", label: "Activity", ico: "↻" },
  { href: "#/settings", label: "Settings", ico: "⚙" },
];

// Platform administrators run the server; they get an operator menu and no practice features.
const ADMIN_NAV = [
  { href: "#/home", label: "Overview", ico: "⌂" },
  { section: "Legal library" },
  { href: "#/admin", label: "Manage library", ico: "★" },
  { href: "#/admin?tab=import", label: "Import judgments", ico: "⇩" },
  { href: "#/admin?tab=ai", label: "AI settings", ico: "✦" },
  { href: "#/research", label: "Test research", ico: "⌕" },
  { href: "#/library", label: "Search library", ico: "❏" },
  { section: "Server" },
  { href: "#/admin?tab=users", label: "Users", ico: "☺" },
  { href: "#/admin?tab=firms", label: "Firms", ico: "☷" },
  { href: "#/activity", label: "My activity", ico: "↻" },
  { href: "#/settings", label: "Settings", ico: "⚙" },
];
const SHORTCUT_LABEL = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? "⌘ K" : "Ctrl K";

/* Ctrl+K / ⌘K opens LawPilot AI from anywhere in the app. */
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k" && State.user) {
    e.preventDefault();
    if ($("#ai-root")) { const i = $("#ai-input"); if (i) i.focus(); } else navigate("#/ai");
  }
});

const PRACTICE_PAGES = new Set(["matters", "matter", "drafts", "draftNew", "draft", "tasks", "firm"]);

const ROUTES = [
  [/^\/home$/, "home"], [/^\/matters$/, "matters"], [/^\/matters\/(\w+)$/, "matter"],
  [/^\/research$/, "research"], [/^\/research\/(\w+)$/, "researchSession"], [/^\/library$/, "library"],
  [/^\/sources\/(\w+)$/, "source"], [/^\/drafts$/, "drafts"], [/^\/drafts\/new$/, "draftNew"],
  [/^\/drafts\/(\w+)$/, "draft"], [/^\/tasks$/, "tasks"], [/^\/firm$/, "firm"], [/^\/activity$/, "activity"],
  [/^\/settings$/, "settings"], [/^\/admin$/, "admin"], [/^\/ai$/, "ai"], [/^\/ai\/(\w+)$/, "aiChat"],
];
const PUBLIC = { "/login": "login", "/register": "register", "/forgot": "forgot", "/reset-password": "resetPassword",
  "/invite": "invite" };

function renderShell() {
  if ($("#main")) return;
  const u = State.user;
  document.body.innerHTML = `
    <div class="shell">
      <aside class="sidebar" id="sidebar">
        <div class="brand"><div class="brand-mark">L</div><div><div class="brand-name">LawPilot</div>
          <div class="brand-sub">Legal research &amp; practice</div></div></div>
        <a class="ai-launch" href="#/ai" data-nav="#/ai" title="Open LawPilot AI (${SHORTCUT_LABEL})">
          <span class="ai-launch-ico">${ADVOCATE_BANDS}</span>
          <span class="ai-launch-text"><strong>LawPilot AI</strong><small>Ask · Summarise · Cite</small></span>
          <span class="ai-launch-go" aria-hidden="true">→</span>
        </a>
        <nav class="nav">${(u.is_platform_admin ? ADMIN_NAV : NAV).map(n => n.section
          ? `<div class="nav-section">${esc(n.section)}</div>`
          : `<a href="${n.href}" data-nav="${n.href}"><span class="ico">${n.ico}</span>${esc(n.label)}</a>`).join("")}</nav>
        <div class="sidebar-foot"><div><strong>${esc(u.name)}</strong></div>
          <div class="muted">${u.is_platform_admin ? "Platform administrator" : u.firm ? esc(u.firm.name) + " · " + esc(u.firm.role_label) : "Individual practice"}</div>
          <button class="btn link" id="logout" style="margin-top:6px">Sign out</button></div>
      </aside>
      <div class="content">
        <header class="appbar">
          <button class="btn small menu-btn" id="menu" aria-label="Open menu">☰</button>
          <strong class="appbar-brand">LawPilot</strong>
          <span class="spacer"></span>
          <a class="appbar-ai" href="#/ai" title="Open LawPilot AI (${SHORTCUT_LABEL})">${ADVOCATE_BANDS}<span>Ask LawPilot AI</span>
            <kbd class="ai-kbd">${SHORTCUT_LABEL}</kbd></a>
          <span class="ts-label"></span>${themeSwitchHtml()}
        </header>
        <main class="main" id="main"></main>
      </div>
    </div>`;
  $("#logout").addEventListener("click", async () => {
    try { await api("POST", "/api/auth/logout"); } catch (e) { /* signing out anyway */ }
    State.user = null;
    location.hash = "#/login";
    location.reload();
  });
  $("#menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  wireThemeSwitches();
}

function markNav(path) {
  const hash = location.hash || "#/home";
  const tab = parseHash().query.get("tab");
  const tabbed = new Set($$("[data-nav]").map(a => a.getAttribute("data-nav")).filter(h => h.includes("?"))
    .map(h => new URLSearchParams(h.split("?")[1]).get("tab")));
  $$("[data-nav]").forEach(a => {
    const href = a.getAttribute("data-nav");
    let active;
    if (href.includes("?")) active = hash.startsWith(href);
    else if (href === "#/admin") active = path === "/admin" && !(tab && tabbed.has(tab));
    else { const target = href.slice(1); active = path === target || path.startsWith(target + "/"); }
    a.classList.toggle("active", active);
  });
  const sb = $("#sidebar");
  if (sb) sb.classList.remove("open");
}

async function route() {
  const { path, query } = parseHash();
  if (PUBLIC[path]) {
    if (State.user && path !== "/invite" && path !== "/reset-password") return navigate("#/home");
    return Pages[PUBLIC[path]](query);
  }
  if (!State.user) {
    const next = encodeURIComponent(location.hash || "#/home");
    return navigate(`#/login?next=${next}`);
  }
  renderShell();
  markNav(path);
  const aiPage = /^\/ai(\/|$)/.test(path);
  document.body.classList.toggle("ai-mode", aiPage);
  if (!aiPage && $("#ai-root")) $("#main").innerHTML = "";
  for (const [re, name] of ROUTES) {
    const m = path.match(re);
    if (m) {
      if (name === "admin" && !State.user.is_platform_admin) break;
      if (PRACTICE_PAGES.has(name) && State.user.is_platform_admin) {
        setMain(`<h1>Not available for administrator accounts</h1>
          <div class="banner info">Platform administrator accounts run the server: the legal library, AI settings and users.
          They cannot hold matters, drafts, tasks or firms, and they never see any firm's cases.
          To use LawPilot as a lawyer, sign out and create a separate regular account.</div><a class="btn" href="#/home">Back to overview</a>`);
        return;
      }
      try { await Pages[name](...m.slice(1), query); } catch (e) {
        setMain(`<div class="banner bad"><strong>Could not load this page</strong>${esc(e.message)}</div>`);
      }
      return;
    }
  }
  setMain(`<h1>Page not found</h1><p><a href="#/home">Go home</a></p>`);
}

async function boot() {
  try {
    const r = await api("GET", "/api/auth/me");
    State.user = r.user;
  } catch (e) {
    State.user = null;
  }
  applyTheme();
  if (State.user) {
    api("GET", "/api/system/status").then(s => { State.status = s; }).catch(() => {});
  }
  window.addEventListener("hashchange", route);
  route();
}

/* Re-fetch the signed-in user (after joining a firm, changing role, editing profile) and rebuild the shell. */
async function refreshUser() {
  const r = await api("GET", "/api/auth/me");
  State.user = r.user;
  applyTheme();
  document.body.innerHTML = "";
  route();
}

document.addEventListener("DOMContentLoaded", boot);
