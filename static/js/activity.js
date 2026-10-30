/* Activity logs: your own account, your firm (firm admins / partners), and platform administrators.
   Firm and platform views only ever show the kind of action, never its content. */
"use strict";

const ROLE_NAMES = { firm_admin: "Firm Admin", partner: "Partner", associate: "Associate", junior_associate: "Junior Associate",
  researcher: "Researcher", clerk: "Clerk", read_only: "Read-only", individual: "Independent", platform_admin: "Platform admin" };
const CAT_GLYPH = { account: "◉", matters: "▤", documents: "❏", research: "⌕", drafting: "✎", calendar: "◷", firm: "☷",
  library: "★", admin: "⚙" };
const PRIVACY_NOTE = `For confidentiality, this view shows who did what kind of action and when. It never shows the
  content: no questions asked, document or matter names, or IP addresses.`;

Pages.activity = async function (query) {
  if (State.user.is_platform_admin) {
    if (query.get("firm")) return activityAdminFirm(query.get("firm"), query);
    return query.get("tab") === "mine" ? activityShell("mine", query, activityMine) : activityShell("firms", query, activityAdminFirms);
  }
  const canFirm = can("firm.view_activity") && State.user.firm;
  const tab = query.get("tab") === "firm" && canFirm ? "firm" : "mine";
  return activityShell(tab, query, tab === "firm" ? activityFirm : activityMine);
};

function activityShell(tab, query, render) {
  const admin = State.user.is_platform_admin;
  const tabs = admin ? [["firms", "Firms"], ["mine", "My activity"]]
    : [["mine", "My activity"], ...(can("firm.view_activity") && State.user.firm ? [["firm", "Firm activity"]] : [])];
  setMain(`<div class="page-head"><div><h1>Activity</h1>
      <p>${admin ? "Account and firm activity across this server." : "A record of actions on your account" + (tabs.length > 1 ? " and your firm." : ".")}</p></div></div>
    ${tabs.length > 1 ? `<div class="tabs">${tabs.map(([k, l]) => `<button data-atab="${k}" class="${tab === k ? "active" : ""}">${l}</button>`).join("")}</div>` : ""}
    <div id="act"></div>`);
  $$("[data-atab]").forEach(b => b.addEventListener("click", () => navigate(`#/activity?tab=${b.dataset.atab}`)));
  return render($("#act"), query);
}

/* ── Shared pieces ───────────────────────────────────────── */
function activityParams(query, keys) {
  const p = new URLSearchParams();
  keys.forEach(k => { if (query.get(k)) p.set(k, query.get(k)); });
  return p;
}

function dayLabel(iso) {
  const d = new Date(iso);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = Math.round((today - day) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

function activityRows(items, { who, own }) {
  let html = "", last = "";
  for (const a of items) {
    const day = dayLabel(a.ts);
    if (day !== last) { html += `<div class="act-day">${esc(day)}</div>`; last = day; }
    const time = new Date(a.ts).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
    html += `<div class="act-row">
      <span class="act-ico act-${esc(a.category)}" aria-hidden="true">${CAT_GLYPH[a.category] || "•"}</span>
      <div class="act-main">
        <div class="act-line">${who ? `<strong>${esc(a.user_name)}</strong> ` : ""}<span>${esc(who ? a.label.charAt(0).toLowerCase() + a.label.slice(1) : a.label)}</span></div>
        ${own && a.summary && a.summary !== a.label ? `<div class="act-detail">${esc(a.summary)}${a.matter_id ? ` · <a href="#/matters/${esc(a.matter_id)}">open matter</a>` : ""}</div>` : ""}
      </div>
      <div class="act-meta"><span>${esc(time)}</span>${own && a.ip ? `<small class="muted">${esc(a.ip)}</small>` : ""}</div>
    </div>`;
  }
  return html || `<div class="empty">No activity matches these filters.</div>`;
}

function activityFilters(meta, query, { members, search }) {
  return `<form class="act-filters" id="actf">
    ${search ? `<input type="search" name="q" value="${esc(query.get("q") || "")}" placeholder="Search your activity" aria-label="Search">` : ""}
    <select name="category" aria-label="Category"><option value="">All categories</option>
      ${meta.categories.map(c => `<option value="${c.key}" ${query.get("category") === c.key ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select>
    ${members ? `<select name="user_id" aria-label="Member"><option value="">All members</option>
      ${members.map(m => `<option value="${esc(m.id)}" ${query.get("user_id") === m.id ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>` : ""}
    <label class="act-date">From <input type="date" name="from" value="${esc(query.get("from") || "")}"></label>
    <label class="act-date">To <input type="date" name="to" value="${esc(query.get("to") || "")}"></label>
    <button class="btn">Apply</button>
    ${["q", "category", "user_id", "from", "to"].some(k => query.get(k)) ? `<button type="button" class="btn link" id="actclear">Clear</button>` : ""}
  </form>`;
}

function wireActivityFilters(base, query) {
  const form = $("#actf");
  form.addEventListener("submit", e => {
    e.preventDefault();
    const p = new URLSearchParams(base);
    Object.entries(formObject(form)).forEach(([k, v]) => { if (v) p.set(k, v); });
    navigate(`#/activity?${p}`);
  });
  if ($("#actclear")) $("#actclear").addEventListener("click", () => navigate(`#/activity?${new URLSearchParams(base)}`));
}

/* Paged feed with "Load more". */
async function activityFeed(el, url, opts) {
  let offset = 0;
  const box = $(".act-feed", el);
  const more = $(".act-more", el);
  const load = async () => {
    const r = await api("GET", `${url}${url.includes("?") ? "&" : "?"}limit=100&offset=${offset}`);
    offset += r.items.length;
    box.insertAdjacentHTML("beforeend", activityRows(r.items, opts));
    more.hidden = !r.has_more;
    return r;
  };
  more.addEventListener("click", () => withBusy(more, load));
  return load();
}

function membersPanel(members, query, base) {
  return `<div class="card act-members"><div class="card-head"><h3>Members</h3><small class="muted">last 30 days</small></div>
    ${members.length ? members.map(m => {
      const p = new URLSearchParams(base); p.set("user_id", m.id);
      return `<a class="act-member ${query.get("user_id") === m.id ? "active" : ""}" href="#/activity?${p}">
        <span class="act-avatar">${esc((m.name || "?").trim().charAt(0).toUpperCase())}</span>
        <span class="act-member-text"><strong>${esc(m.name)}</strong><small>${esc(ROLE_NAMES[m.role] || m.role || "")}
          · ${m.last_active ? "active " + ago(m.last_active) : "no activity yet"}</small></span>
        <span class="act-count" title="Actions in the last 30 days">${m.events_30d}</span></a>`;
    }).join("") : `<div class="empty">No members.</div>`}</div>`;
}

/* ── 1. My activity ─────────────────────────────────────── */
async function activityMine(el, query) {
  const base = State.user.is_platform_admin ? { tab: "mine" } : {};
  const params = activityParams(query, ["q", "category", "from", "to"]);
  el.innerHTML = loading();
  const first = await api("GET", `/api/account/activity?${params}&limit=1`);
  el.innerHTML = `<div class="card">${activityFilters(first, query, { search: true })}</div>
    <div class="card"><div class="act-feed"></div><button class="btn act-more" hidden>Load more</button></div>
    <p class="muted act-foot">Includes sign-ins, failed sign-in attempts and the device address they came from, so you can spot
      anything you do not recognise. Only you can see the details on this page.</p>`;
  wireActivityFilters(base, query);
  await activityFeed(el, `/api/account/activity?${params}`, { own: true });
}

/* ── 2. Firm activity (firm admins / partners) ──────────── */
async function activityFirm(el, query) {
  const base = { tab: "firm" };
  const params = activityParams(query, ["category", "user_id", "from", "to"]);
  el.innerHTML = loading();
  const first = await api("GET", `/api/firm/activity?${params}&limit=1`);
  el.innerHTML = `<div class="banner info act-privacy"><strong>${esc(first.firm)}</strong>${PRIVACY_NOTE}</div>
    <div class="act-layout">${membersPanel(first.members, query, base)}
      <div class="act-col"><div class="card">${activityFilters(first, query, { members: first.members })}
        <div class="row act-tools"><span class="spacer"></span><a class="btn small" href="/api/firm/activity?${params}&format=csv">Export CSV</a></div></div>
        <div class="card"><div class="act-feed"></div><button class="btn act-more" hidden>Load more</button></div></div></div>`;
  wireActivityFilters(base, query);
  await activityFeed(el, `/api/firm/activity?${params}`, { who: true });
}

/* ── 3. Platform administrators: firms first ────────────── */
async function activityAdminFirms(el) {
  el.innerHTML = loading();
  const r = await api("GET", "/api/admin/activity/firms");
  const card = (id, name, info, special) => `<a class="card act-firm ${special ? "special" : ""}" href="#/activity?firm=${encodeURIComponent(id)}">
      <div class="act-firm-name">${esc(name)}</div>
      <div class="act-firm-stats"><span><strong>${info.members}</strong> member${info.members === 1 ? "" : "s"}</span>
        <span><strong>${info.events_7d}</strong> actions this week</span></div>
      <small class="muted">${info.last_activity ? "Last activity " + ago(info.last_activity) : "No activity yet"}</small>
      <span class="act-firm-go" aria-hidden="true">→</span></a>`;
  el.innerHTML = `<div class="banner info act-privacy"><strong>Privacy by design</strong>${PRIVACY_NOTE}</div>
    <div class="row act-search"><input type="search" id="actfs" placeholder="Search firms" aria-label="Search firms" style="max-width:360px"></div>
    <h2 class="act-h">Law firms <small class="muted">(${r.firms.length})</small></h2>
    <div class="act-firms" id="actfirms">${r.firms.length ? r.firms.map(f => card(f.id, f.name, f)).join("") : `<div class="empty">No firms have been created yet.</div>`}</div>
    <h2 class="act-h">Other accounts</h2>
    <div class="act-firms">${card("independent", "Independent advocates", r.independent, true)}${card("platform", "Platform administrators", r.platform, true)}</div>`;
  $("#actfs").addEventListener("input", e => {
    const q = e.target.value.trim().toLowerCase();
    $$("#actfirms .act-firm").forEach(c => { c.hidden = q && !c.textContent.toLowerCase().includes(q); });
  });
}

async function activityAdminFirm(firmId, query) {
  const base = { firm: firmId };
  const params = activityParams(query, ["category", "user_id", "from", "to"]);
  setMain(loading());
  const first = await api("GET", `/api/admin/activity/firms/${encodeURIComponent(firmId)}?${params}&limit=1`);
  setMain(`<div class="page-head"><div><a href="#/activity" class="act-back">← All firms</a><h1>${esc(first.title)}</h1>
      <p>Member activity · ${first.members.length} member${first.members.length === 1 ? "" : "s"}</p></div>
      <a class="btn" href="/api/admin/activity/firms/${encodeURIComponent(firmId)}?${params}&format=csv">Export CSV</a></div>
    <div class="banner info act-privacy">${PRIVACY_NOTE}</div>
    <div class="act-layout">${membersPanel(first.members, query, base)}
      <div class="act-col"><div class="card">${activityFilters(first, query, { members: first.members })}</div>
        <div class="card"><div class="act-feed"></div><button class="btn act-more" hidden>Load more</button></div></div></div>`);
  wireActivityFilters(base, query);
  await activityFeed($("#main"), `/api/admin/activity/firms/${encodeURIComponent(firmId)}?${params}`, { who: true });
}
