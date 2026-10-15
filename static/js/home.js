/* Home dashboard. */
"use strict";

Pages.home = async function () {
  if (State.user.is_platform_admin) return adminHome();
  setMain(loading());
  const [d, status] = await Promise.all([api("GET", "/api/dashboard"), api("GET", "/api/system/status")]);
  State.status = status;
  const u = State.user;
  const greet = new Date().getHours() < 12 ? "Good morning" : new Date().getHours() < 17 ? "Good afternoon" : "Good evening";

  const setup = [];
  if (!status.llm.configured) setup.push(u.is_platform_admin
    ? "AI answers are off. Add an API key in Corpus admin → AI settings (Google Gemini has a free tier)."
    : "AI answers are off on this server. Ask your administrator to add an AI key.");
  if (status.corpus.primary === 0) setup.push(status.corpus.total
    ? "The library only has starter case summaries. Import real Supreme Court judgments and add bare Acts (Corpus admin) before relying on research."
    : "The library is empty. Import real Supreme Court judgments and add bare Acts in Corpus admin.");
  if (!status.email_configured) setup.push("Email is not configured, so invitations and password resets must be shared by link.");

  setMain(`
    <div class="page-head"><div><h1>${greet}, ${esc(u.name.split(" ")[0])}</h1>
      <p>${new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</p></div>
      <div class="row">${can("matter.create") ? `<a class="btn" href="#/matters?new=1">+ New matter</a>` : ""}
      <a class="btn primary" href="#/research">Ask a legal question</a></div></div>

    ${setup.length ? `<div class="banner info"><strong>Setup notes</strong>${setup.map(s => `<div>• ${esc(s)}</div>`).join("")}
      ${u.is_platform_admin ? `<div class="row" style="margin-top:6px"><a href="#/admin?tab=ai">AI settings</a><a href="#/admin?tab=import">Import judgments</a></div>` : ""}</div>` : ""}

    <div class="grid grid-3">
      <div class="card"><div class="muted">Open matters</div><div class="stat">${d.counts.open_matters}</div></div>
      <div class="card"><div class="muted">Hearings in next 14 days</div><div class="stat">${d.counts.hearings_14d}</div></div>
      <div class="card"><div class="muted">My open tasks</div><div class="stat">${d.counts.open_tasks}</div></div>
    </div>

    <div class="grid grid-2">
      <div class="card"><div class="card-head"><h2>Upcoming hearings</h2><a href="#/matters">All matters</a></div>
        ${d.upcoming_hearings.length ? d.upcoming_hearings.map(h => `<div class="list-item">
          <div class="row"><strong>${fmtDateTime(h.date)}</strong><span class="spacer"></span>${h.court_room ? badge(h.court_room) : ""}</div>
          <a href="#/matters/${h.matter_id}">${esc(h.matter_title)}</a>
          <div class="muted">${esc(h.purpose || "")}${h.court ? " · " + esc(h.court) : ""}</div></div>`).join("")
          : `<div class="empty">No hearings in the next two weeks.</div>`}</div>

      <div class="card"><div class="card-head"><h2>My tasks</h2><a href="#/tasks">All tasks</a></div>
        ${d.my_tasks.length ? d.my_tasks.map(t => `<div class="list-item"><div class="row">
          <span class="title">${esc(t.title)}</span><span class="spacer"></span>${priorityBadge(t.priority)}</div>
          <div class="muted">${t.due_date ? "Due " + fmtDate(t.due_date) : "No due date"}${t.matter_title ? ` · <a href="#/matters/${t.matter_id}">${esc(t.matter_title)}</a>` : ""}
          ${t.creator_name !== u.name ? " · from " + esc(t.creator_name) : ""}</div></div>`).join("")
          : `<div class="empty">Nothing assigned to you.</div>`}</div>

      <div class="card"><div class="card-head"><h2>Recent matters</h2></div>
        ${d.recent_matters.length ? d.recent_matters.map(m => `<div class="list-item">
          <a class="title" href="#/matters/${m.id}">${esc(m.title)}</a>
          <div class="muted">${esc([m.client_name, m.court].filter(Boolean).join(" · "))}${m.next_hearing ? " · next hearing " + fmtDate(m.next_hearing) : ""}</div></div>`).join("")
          : `<div class="empty">No matters yet.${can("matter.create") ? ` <a href="#/matters?new=1">Create your first matter</a>.` : ""}</div>`}</div>

      <div class="card"><div class="card-head"><h2>Recent research</h2><a href="#/research">New research</a></div>
        ${d.recent_research.length ? d.recent_research.map(r => `<div class="list-item">
          <a href="#/research/${r.id}">${esc(r.query.slice(0, 120))}</a>
          <div class="row" style="margin-top:2px">${outcomeBadge(r.outcome)}<small>${ago(r.created_at)} · ${r.mode}</small></div></div>`).join("")
          : `<div class="empty">No research yet.</div>`}</div>

      <div class="card"><div class="card-head"><h2>Recent drafts</h2><a href="#/drafts">All drafts</a></div>
        ${d.recent_drafts.length ? d.recent_drafts.map(x => `<div class="list-item"><a href="#/drafts/${x.id}">${esc(x.title)}</a>
          <div class="muted">Edited ${ago(x.updated_at)}</div></div>`).join("") : `<div class="empty">No drafts yet.</div>`}</div>

      <div class="card"><div class="card-head"><h2>Saved authorities</h2><a href="#/library?tab=saved">All saved</a></div>
        ${d.saved_authorities.length ? d.saved_authorities.map(s => `<div class="list-item">
          <a href="#/sources/${s.source_id}">${esc(s.title)}</a> ${statusBadge(s.legal_status)}
          <div class="muted">${esc(s.citation || "")}</div></div>`).join("") : `<div class="empty">Save authorities from research results to see them here.</div>`}</div>
    </div>`);
};

function priorityBadge(p) {
  return { urgent: badge("Urgent", "bad"), high: badge("High", "warn"), low: badge("Low") }[p] || "";
}


/* Overview for platform administrators: server and library health. Counts only, never client content. */
async function adminHome() {
  setMain(loading());
  const o = await api("GET", "/api/admin/overview");
  const greet = new Date().getHours() < 12 ? "Good morning" : new Date().getHours() < 17 ? "Good afternoon" : "Good evening";
  const todo = [];
  if (!o.ai.configured) todo.push(["Turn on AI answers: add an API key and save it.", "#/admin?tab=ai", "AI settings"]);
  if (o.library.judgments === 0) todo.push(["Import Supreme Court judgments into the library.", "#/admin?tab=import", "Import judgments"]);
  if (o.library.acts === 0) todo.push(["Add the bare Acts lawyers use (India Code links).", "#/admin?tab=add", "Add sources"]);
  if (o.library.stale_embeddings) todo.push([`${o.library.stale_embeddings} passages are not yet searchable by meaning with ${o.embedding_model}.`, "#/admin", "Re-embed"]);
  if (!o.email_configured) todo.push(["Email is not configured: invitations and password resets must be shared as links.", null, null]);
  if (o.users.deletion_pending) todo.push([`${o.users.deletion_pending} account deletion request(s) waiting.`, "#/admin?tab=deletions", "Review"]);
  const stat = (label, value, sub = "") => `<div class="card"><div class="muted">${label}</div><div class="stat">${value}</div>${sub ? `<small class="muted">${sub}</small>` : ""}</div>`;
  setMain(`
    <div class="page-head"><div><h1>${greet}, ${esc(State.user.name.split(" ")[0])}</h1>
      <p>Platform overview · ${esc(o.database)} · registration ${o.registration_open ? "open to anyone" : "closed"}</p></div>
      <div class="row"><a class="btn" href="#/admin?tab=import">Import judgments</a><a class="btn primary" href="#/research">Test research</a></div></div>
    ${todo.length ? `<div class="banner ${todo.length > 2 ? "warn" : "info"}"><strong>Needs attention</strong>${todo.map(([t, href, l]) =>
      `<div>• ${esc(t)}${href ? ` <a href="${href}">${esc(l)}</a>` : ""}</div>`).join("")}</div>`
      : `<div class="banner ok"><strong>All set</strong>AI answers, library and search index are in order.</div>`}
    <h2>Legal library</h2>
    <div class="grid grid-4">
      ${stat("Judgments", o.library.judgments)}${stat("Acts & rules", o.library.acts)}
      ${stat("Searchable passages", o.library.passages, o.library.stale_embeddings ? `${o.library.stale_embeddings} awaiting re-embed` : "all up to date")}
      ${stat("AI answers", o.ai.configured ? "On" : "Off", o.ai.configured ? esc(`${o.ai.provider} / ${o.ai.model}`) : "no API key saved")}
    </div>
    ${o.last_import ? `<div class="card"><div class="card-head"><h3>Latest import</h3><a href="#/admin?tab=import">All imports</a></div>
      ${badge(o.last_import.status, o.last_import.status === "completed" ? "ok" : o.last_import.status === "failed" ? "bad" : "info")}
      <small class="muted">${fmtDateTime(o.last_import.started_at)}</small><div>${esc(o.last_import.message || "")}</div></div>` : ""}
    <h2>Usage</h2>
    <div class="grid grid-4">
      ${stat("Lawyers", o.users.total - o.users.admins, `${o.users.active_7d} active this week`)}
      ${stat("Firms", o.firms, `${o.individuals} individual practitioners`)}
      ${stat("Matters", o.matters, `${o.matter_documents} private documents`)}
      ${stat("Research this week", o.research_7d)}
    </div>
    <div class="card"><div class="card-head"><h3>Newest accounts</h3><a href="#/admin?tab=users">All users</a></div>
      ${o.recent_users.length ? o.recent_users.map(u => `<div class="list-item row"><div><strong>${esc(u.name)}</strong>
        <div class="muted">${esc(u.email)}${u.firm_name ? " · " + esc(u.firm_name) : " · individual"}</div></div>
        <span class="spacer"></span><small class="muted">joined ${ago(u.created_at)}</small></div>`).join("")
        : `<div class="empty">No lawyers have signed up yet. Share the address of this server; the first lawyer to create a firm becomes its Firm Admin.</div>`}</div>
    <p class="muted">As platform administrator you see counts only. Matters, client documents and drafts stay private to each firm.</p>`);
}
