/* Tasks, firm & team, activity history, settings. */
"use strict";

Pages.tasks = async function (query) {
  const scope = query.get("scope") || "mine";
  const status = query.get("status") || "open";
  setMain(`<div class="page-head"><div><h1>Tasks &amp; help requests</h1><p>Work assigned to you, and help you asked colleagues for.</p></div>
    <button class="btn primary" id="nt">+ New task / ask for help</button></div>
    <div class="row" style="margin-bottom:12px"><div class="mode-toggle">
      <button data-scope="mine" class="${scope === "mine" ? "active" : ""}">Assigned to me</button>
      <button data-scope="assigned_by_me" class="${scope === "assigned_by_me" ? "active" : ""}">Created by me</button></div>
      <select id="st" style="width:auto">${options({ open: "Open", done: "Done", all: "All" }, status)}</select></div>
    <div class="card" id="tl">${loading()}</div>`);
  const go = (s, st) => navigate(`#/tasks?scope=${s}&status=${st}`);
  $$("[data-scope]").forEach(b => b.addEventListener("click", () => go(b.dataset.scope, status)));
  $("#st").addEventListener("change", e => go(scope, e.target.value));
  const reload = () => Pages.tasks(query);
  $("#nt").addEventListener("click", () => taskModal({ onDone: reload }).catch(fail));
  const r = await api("GET", `/api/tasks?scope=${scope}&status=${status}`);
  $("#tl").innerHTML = taskRows(r.tasks, true);
  wireTasks($("#tl"), reload);
};

/* ── Firm ──────────────────────────────────────────────── */
Pages.firm = async function () {
  setMain(loading());
  const [f, roles] = await Promise.all([api("GET", "/api/firm"), api("GET", "/api/firm/roles")]);
  if (!f.firm) return firmSetup(roles);
  const manage = can("firm.manage_members");
  const roleOpts = Object.fromEntries(roles.roles.map(r => [r.key, r.label]));
  setMain(`<div class="page-head"><div><h1>${esc(f.firm.name)}</h1><p>${esc(f.firm.city || "")} · ${f.members.length} member${f.members.length === 1 ? "" : "s"} · your role: ${esc(roleOpts[f.my_role])}</p></div>
      <div class="row">${manage ? `<button class="btn" id="edit-firm">Edit firm</button><button class="btn primary" id="invite">+ Invite member</button>` : ""}
      <button class="btn danger" id="leave">Leave firm</button></div></div>
    <div class="card table-wrap"><h2>Members</h2><table><thead><tr><th>Name</th><th>Role</th><th>Permissions</th>${manage ? "<th></th>" : ""}</tr></thead><tbody>
      ${f.members.map(m => {
        const custom = Object.keys(m.permission_overrides || {}).length;
        return `<tr><td><strong>${esc(m.name)}</strong>${m.id === State.user.id ? " " + badge("You", "info") : ""}<div class="muted">${esc(m.email)}${m.bar_enrolment ? " · " + esc(m.bar_enrolment) : ""}</div></td>
        <td>${manage ? `<select data-role="${m.id}" style="width:auto">${options(roleOpts, m.role)}</select>` : esc(m.role_label)}</td>
        <td><small>${m.effective_permissions.length} permission${m.effective_permissions.length === 1 ? "" : "s"}</small>${custom ? " " + badge("Customised", "warn") : ""}</td>
        ${manage ? `<td class="row"><button class="btn small" data-perms="${m.id}">Permissions</button>
          ${m.id !== State.user.id ? `<button class="btn small danger" data-remove="${m.id}">Remove</button>` : ""}</td>` : ""}</tr>`;
      }).join("")}</tbody></table></div>
    ${manage ? `<div class="card"><h2>Pending invitations</h2>${f.invitations.length ? f.invitations.map(i => `<div class="list-item row">
      <div><strong>${esc(i.email)}</strong> as ${esc(roleOpts[i.role])}<div class="muted">sent ${ago(i.created_at)}, expires ${fmtDate(i.expires_at)}</div></div>
      <span class="spacer"></span><button class="btn small danger" data-revoke="${i.id}">Revoke</button></div>`).join("") : `<div class="empty">No pending invitations.</div>`}</div>` : ""}
    <details class="card"><summary>What each role can do</summary><div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Permission</th>
      ${roles.roles.map(r => `<th>${esc(r.label)}</th>`).join("")}</tr></thead><tbody>${roles.permissions.map(p => `<tr><td>${esc(p.label)}</td>
      ${roles.roles.map(r => `<td>${r.permissions.includes(p.key) ? "✓" : ""}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`);
  const reload = () => Pages.firm();

  $$("[data-role]").forEach(sel => sel.addEventListener("change", async () => {
    try { await api("PATCH", `/api/firm/members/${sel.dataset.role}`, { role: sel.value }); toast("Role updated."); if (sel.dataset.role === State.user.id) refreshUser(); else reload(); }
    catch (e) { fail(e); reload(); }
  }));
  $$("[data-remove]").forEach(b => b.addEventListener("click", async () => {
    const m = f.members.find(x => x.id === b.dataset.remove);
    if (await confirmDialog(`Remove ${m.name} from the firm? They lose access to firm matters, and matters they own are transferred to you.`, { danger: true, okLabel: "Remove" })) {
      try { await api("DELETE", `/api/firm/members/${m.id}`); reload(); } catch (e) { fail(e); }
    }
  }));
  $$("[data-revoke]").forEach(b => b.addEventListener("click", async () => {
    try { await api("DELETE", `/api/firm/invitations/${b.dataset.revoke}`); reload(); } catch (e) { fail(e); }
  }));
  $$("[data-perms]").forEach(b => b.addEventListener("click", () => permissionsModal(f.members.find(x => x.id === b.dataset.perms), roles, reload)));
  $("#leave").addEventListener("click", async () => {
    if (await confirmDialog("Leave this firm? You will lose access to its matters.", { danger: true, okLabel: "Leave" })) {
      try { await api("POST", "/api/firm/leave"); toast("You left the firm."); refreshUser(); } catch (e) { fail(e); }
    }
  });
  if ($("#invite")) $("#invite").addEventListener("click", () => {
    const md = modal(`<h2>Invite a member</h2><form id="inv">
      <div class="field"><label>Email</label><input type="email" name="email" required></div>
      <div class="field"><label>Role</label><select name="role">${options(roleOpts, "associate")}</select></div>
      <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Create invitation</button></div></form><div id="inv-out"></div>`);
    $("#inv", md.el).addEventListener("submit", e => {
      e.preventDefault();
      withBusy($("#inv .primary", md.el), async () => {
        const r = await api("POST", "/api/firm/invitations", formObject(e.target));
        $("#inv", md.el).remove();
        $("#inv-out", md.el).innerHTML = `${r.email_sent ? `<div class="banner ok">Invitation emailed.</div>` : `<div class="banner warn">Email is not configured on this server, so share this link with the person yourself:</div>`}
          <div class="field"><input id="link" readonly value="${esc(r.invite_link)}"></div><div class="row end"><button class="btn" id="copy">Copy link</button><button class="btn primary" data-close>Done</button></div>`;
        $("#copy", md.el).addEventListener("click", () => navigator.clipboard.writeText(r.invite_link).then(() => toast("Copied.")));
        $$("[data-close]", md.el).forEach(x => x.addEventListener("click", () => { md.close(); reload(); }));
      });
    });
  });
  if ($("#edit-firm")) $("#edit-firm").addEventListener("click", () => {
    const md = modal(`<h2>Edit firm</h2><form id="ef"><div class="field"><label>Name</label><input name="name" value="${esc(f.firm.name)}" required></div>
      <div class="field"><label>City</label><input name="city" value="${esc(f.firm.city || "")}"></div>
      <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`);
    $("#ef", md.el).addEventListener("submit", e => {
      e.preventDefault();
      withBusy($("#ef .primary", md.el), async () => { await api("PATCH", "/api/firm", formObject(e.target)); md.close(); refreshUser(); });
    });
  });
};

function permissionsModal(member, roles, reload) {
  const roleDefaults = new Set((roles.roles.find(r => r.key === member.role) || {}).permissions || []);
  const ov = member.permission_overrides || {};
  const md = modal(`<h2>Permissions: ${esc(member.name)}</h2>
    <p class="muted">Defaults come from the role (${esc(member.role_label)}). Grant or revoke individual permissions here.</p>
    <form id="pf"><table><thead><tr><th>Permission</th><th>Role default</th><th>Setting</th></tr></thead><tbody>
    ${roles.permissions.map(p => `<tr><td>${esc(p.label)}</td><td>${roleDefaults.has(p.key) ? "Allowed" : "Not allowed"}</td>
      <td><select name="${p.key}" style="width:auto">${options({ "": "Role default", grant: "Grant", revoke: "Revoke" },
        p.key in ov ? (ov[p.key] ? "grant" : "revoke") : "")}</select></td></tr>`).join("")}</tbody></table>
    <div class="row end" style="margin-top:12px"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`, { wide: true });
  $("#pf", md.el).addEventListener("submit", e => {
    e.preventDefault();
    const overrides = {};
    Object.entries(formObject(e.target)).forEach(([k, v]) => { if (v === "grant") overrides[k] = true; else if (v === "revoke") overrides[k] = false; });
    withBusy($("#pf .primary", md.el), async () => {
      await api("PATCH", `/api/firm/members/${member.id}`, { permission_overrides: overrides });
      md.close();
      toast("Permissions updated.");
      if (member.id === State.user.id) refreshUser(); else reload();
    });
  });
}

function firmSetup(roles) {
  setMain(`<div class="page-head"><div><h1>Firm &amp; team</h1><p>You are practising individually.</p></div></div>
    <div class="grid grid-2"><div class="card"><h2>Create a firm</h2>
      <p>Set up your chambers or law firm, then invite colleagues with roles such as Partner, Associate, Researcher or Clerk.</p>
      <form id="cf"><div class="field"><label>Firm name</label><input name="name" required></div>
      <div class="field"><label>City</label><input name="city"></div>
      <label class="check"><input type="checkbox" name="move_my_matters" data-bool checked> Move my existing matters into the firm</label>
      <button class="btn primary" style="margin-top:10px">Create firm</button></form></div>
    <div class="card"><h2>Join a firm</h2><p>Ask your firm administrator to invite your email address (<strong>${esc(State.user.email)}</strong>).
      Open the invitation link they send you to join.</p></div></div>`);
  $("#cf").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#cf button"), async () => { await api("POST", "/api/firm", formObject(e.target)); toast("Firm created. You are its Firm Admin."); refreshUser(); });
  });
}

/* ── Activity ──────────────────────────────────────────── */
Pages.activity = async function (query) {
  const scope = query.get("scope") || "mine";
  const q = query.get("q") || "";
  const canFirm = can("firm.view_activity");
  setMain(`<div class="page-head"><div><h1>Activity</h1><p>A record of important actions.</p></div></div>
    <div class="card"><form id="af" class="row"><input type="search" name="q" value="${esc(q)}" placeholder="Search activity" style="flex:1">
      ${canFirm ? `<select name="scope" style="width:auto">${options({ mine: "My activity", firm: "Whole firm" }, scope)}</select>` : ""}
      <button class="btn">Search</button></form></div><div class="card" id="al">${loading()}</div>`);
  $("#af").addEventListener("submit", e => {
    e.preventDefault();
    const f = formObject(e.target);
    navigate(`#/activity?q=${encodeURIComponent(f.q)}&scope=${f.scope || "mine"}`);
  });
  const r = await api("GET", `/api/account/activity?scope=${scope}&q=${encodeURIComponent(q)}&limit=200`);
  $("#al").innerHTML = r.items.length ? `<table><thead><tr><th>When</th>${scope === "firm" ? "<th>Who</th>" : ""}<th>What</th></tr></thead><tbody>
    ${r.items.map(a => `<tr><td style="white-space:nowrap">${fmtDateTime(a.ts)}</td>${scope === "firm" ? `<td>${esc(a.user_name || "")}</td>` : ""}
      <td>${esc(a.summary || a.action)}${a.matter_id ? ` · <a href="#/matters/${a.matter_id}">matter</a>` : ""}<div class="muted mono" style="font-size:.75rem">${esc(a.action)}</div></td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">No activity found.</div>`;
};

/* ── Settings ──────────────────────────────────────────── */
Pages.settings = async function (query) {
  const tab = query.get("tab") || "profile";
  setMain(`<div class="page-head"><div><h1>Settings</h1></div></div>
    <div class="tabs">${[["profile", "Profile"], ["preferences", "Preferences"], ["security", "Security"], ["data", "Your data"], ["system", "About this server"]]
      .map(([k, l]) => `<button data-tab="${k}" class="${tab === k ? "active" : ""}">${l}</button>`).join("")}</div><div id="tab"></div>`);
  $$("[data-tab]").forEach(b => b.addEventListener("click", () => navigate(`#/settings?tab=${b.dataset.tab}`)));
  const el = $("#tab");
  const u = State.user;
  if (tab === "profile") {
    el.innerHTML = `<div class="card" style="max-width:640px"><form id="pf">
      <div class="field"><label>Full name</label><input name="name" value="${esc(u.name)}" required></div>
      <div class="field"><label>Email</label><input value="${esc(u.email)}" disabled><div class="hint">Contact your administrator to change the sign-in email.</div></div>
      <div class="field"><label>Phone</label><input name="phone" value="${esc(u.phone || "")}"></div>
      <div class="field"><label>Bar enrolment number</label><input name="bar_enrolment" value="${esc(u.bar_enrolment || "")}"></div>
      <button class="btn primary">Save profile</button></form></div>`;
    $("#pf").addEventListener("submit", e => {
      e.preventDefault();
      withBusy($("#pf button"), async () => { await api("PATCH", "/api/account/profile", formObject(e.target)); toast("Profile saved."); refreshUser(); });
    });
  } else if (tab === "preferences") {
    const p = u.prefs;
    el.innerHTML = `<div class="card" style="max-width:640px"><form id="prf">
      <div class="field"><label>Answer language</label><select name="language">${options({ en: "English", hi: "Hindi", ta: "Tamil", te: "Telugu", kn: "Kannada",
        ml: "Malayalam", mr: "Marathi", bn: "Bengali", gu: "Gujarati" }, p.language)}</select>
        <div class="hint">AI research answers are written in this language; citations and quotations stay in the original. The interface is in English.</div></div>
      <div class="field"><label>Default jurisdiction (state)</label><input name="default_jurisdiction" value="${esc(p.default_jurisdiction || "")}" placeholder="e.g. Karnataka"></div>
      <div class="field"><label>Default court</label><input name="default_court" value="${esc(p.default_court || "")}" placeholder="e.g. Karnataka High Court"></div>
      <div class="field"><label>Default research mode</label><select name="research_mode">${options({ quick: "Quick", deep: "Deep" }, p.research_mode)}</select></div>
      <label class="check"><input type="checkbox" name="exclude_overruled" data-bool ${p.exclude_overruled ? "checked" : ""}> Hide overruled / repealed material by default</label>
      <label class="check"><input type="checkbox" name="notify_hearings" data-bool ${p.notify_hearings ? "checked" : ""}> Email me hearing reminders the day before</label>
      <label class="check"><input type="checkbox" name="notify_tasks" data-bool ${p.notify_tasks ? "checked" : ""}> Email me when a task is assigned to me</label>
      <div class="field" style="margin-top:10px"><label>Theme</label><select name="theme">${options({ system: "Follow my device (Brief or Black Coat)", brief: "Brief: ivory brief paper, ink and red tape",
        blackcoat: "Black Coat: the advocate's black coat and white bands",
        courtroom: "Courtroom: mahogany bench, oxblood leather and brass" }, normaliseTheme(p.theme))}</select>
        <div class="hint">You can also switch instantly with the three-way switch at the top of the page.</div></div>
      ${State.status && !State.status.email_configured ? `<div class="banner warn">Email is not configured on this server, so notifications cannot be sent yet.</div>` : ""}
      <button class="btn primary">Save preferences</button></form></div>`;
    $("#prf").addEventListener("submit", e => {
      e.preventDefault();
      withBusy($("#prf button"), async () => { await api("PATCH", "/api/account/profile", { prefs: formObject(e.target) }); toast("Preferences saved."); refreshUser(); });
    });
  } else if (tab === "security") {
    el.innerHTML = `<div class="grid grid-2"><div class="card"><h2>Change password</h2><form id="cp">
      <div class="field"><label>Current password</label><input type="password" name="current_password" required autocomplete="current-password"></div>
      <div class="field"><label>New password</label><input type="password" name="new_password" required minlength="10" autocomplete="new-password"></div>
      <button class="btn primary">Change password</button><div class="hint">Other devices will be signed out.</div></form></div>
      <div class="card"><h2>Signed-in devices</h2><div id="sess">${loading()}</div></div></div>`;
    $("#cp").addEventListener("submit", e => {
      e.preventDefault();
      withBusy($("#cp button"), async () => { await api("POST", "/api/auth/change-password", formObject(e.target)); e.target.reset(); toast("Password changed."); });
    });
    const loadSessions = async () => {
      const r = await api("GET", "/api/auth/sessions");
      $("#sess").innerHTML = r.sessions.map(s => `<div class="list-item row"><div><strong>${esc((s.user_agent || "Unknown device").slice(0, 60))}</strong>
        ${s.current ? badge("This device", "info") : ""}<div class="muted">${esc(s.ip || "")} · last active ${ago(s.last_seen_at)}</div></div>
        <span class="spacer"></span>${s.current ? "" : `<button class="btn small danger" data-rev="${s.id}">Sign out</button>`}</div>`).join("");
      $$("[data-rev]").forEach(b => b.addEventListener("click", async () => { await api("DELETE", `/api/auth/sessions/${b.dataset.rev}`); loadSessions(); }));
    };
    loadSessions().catch(fail);
  } else if (tab === "data") {
    el.innerHTML = `<div class="grid grid-2"><div class="card"><h2>Export your data</h2>
      <p>Download everything you created (profile, matters you own, uploaded documents' text, research, drafts, tasks, notes, activity) as a JSON file.</p>
      <a class="btn primary" href="/api/account/export">Download export</a></div>
      <div class="card"><h2>Delete account</h2>${u.status === "deletion_requested"
        ? `<div class="banner warn">Deletion requested. An administrator will process it.</div><button class="btn" id="cancel-del">Cancel request</button>`
        : `<p>Your personal research history, drafts and personal matters will be deleted. Matters and documents that belong to your firm are kept by the firm, as professional records must be retained.</p>
          <form id="dr"><div class="field"><label>Reason (optional)</label><textarea name="reason"></textarea></div><button class="btn danger">Request deletion</button></form>`}</div></div>`;
    if ($("#dr")) $("#dr").addEventListener("submit", async e => {
      e.preventDefault();
      if (!(await confirmDialog("Request deletion of your account?", { danger: true, okLabel: "Request deletion" }))) return;
      withBusy($("#dr button"), async () => { const r = await api("POST", "/api/account/deletion-request", formObject(e.target)); toast(r.message); refreshUser(); });
    });
    if ($("#cancel-del")) $("#cancel-del").addEventListener("click", () => withBusy($("#cancel-del"), async () => {
      await api("DELETE", "/api/account/deletion-request"); toast("Deletion request cancelled."); refreshUser();
    }));
  } else {
    const s = State.status = await api("GET", "/api/system/status");
    el.innerHTML = `<div class="card" style="max-width:720px"><dl class="kv">
      <dt>LawPilot version</dt><dd>${esc(s.version)}</dd>
      <dt>Database</dt><dd>${esc(s.database.engine)}${s.database.engine === "PostgreSQL" ? (s.database.pgvector ? " with pgvector" : " (pgvector not installed: in-memory vector search)") : " (single file; use PostgreSQL in production)"}</dd>
      <dt>AI model</dt><dd>${s.llm.configured ? esc(`${s.llm.provider} / ${s.llm.model}`) : "Not configured (research shows passages only)"}</dd>
      <dt>Search embeddings</dt><dd>${esc(s.embeddings.model)}${s.embeddings.semantic ? "" : " (built-in lexical-semantic; set up Voyage or OpenAI embeddings for true semantic search)"}</dd>
      <dt>Library</dt><dd>${s.corpus.total} sources, ${s.corpus.primary} primary</dd>
      <dt>Email</dt><dd>${s.email_configured ? "Configured" : "Not configured"}</dd>
      <dt>Indian Kanoon API</dt><dd>${s.kanoon_api ? "Connected" : "Not configured"}</dd></dl>
      <hr><p class="muted">LawPilot is a research aid. It does not replace reading the authorities or professional judgement.</p></div>`;
  }
};
