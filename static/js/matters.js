/* Matters list and matter workspace. */
"use strict";

const MATTER_FORM_FIELDS = [
  ["title", "Matter title", "e.g. State v. Sharma - bail"], ["client_name", "Client", ""],
  ["case_number", "Case number", "e.g. CRL.M.C. 1234/2026"], ["court", "Court / forum", "e.g. Delhi High Court"],
  ["case_type", "Type", "e.g. Criminal, Writ, Arbitration"], ["opposing_party", "Opposing party", ""],
  ["stage", "Stage", "e.g. Pleadings, Evidence, Arguments"], ["jurisdiction", "State / jurisdiction", "e.g. Delhi"],
];

function matterForm(m = {}) {
  return `<div class="grid grid-2">${MATTER_FORM_FIELDS.map(([k, l, ph]) => `<div class="field"><label>${l}${k === "title" ? " *" : ""}</label>
    <input name="${k}" value="${esc(m[k] || "")}" placeholder="${esc(ph)}" ${k === "title" ? "required" : ""}></div>`).join("")}</div>
    <div class="field"><label>Description / brief facts</label><textarea name="description">${esc(m.description || "")}</textarea></div>
    ${State.user.firm ? `<label class="check"><input type="checkbox" name="confidential" data-bool ${m.confidential ? "checked" : ""}>
      Confidential (ethical wall): only people added to this matter can see it, even partners</label>` : ""}`;
}

function newMatterModal() {
  const m = modal(`<h2>New matter</h2><form id="nm">${matterForm()}
    <div class="row end" style="margin-top:12px"><button type="button" class="btn" data-close>Cancel</button>
    <button class="btn primary">Create matter</button></div></form>`, { wide: true });
  $("#nm", m.el).addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#nm button.primary", m.el), async () => {
      const r = await api("POST", "/api/matters", formObject(e.target));
      m.close();
      toast("Matter created.");
      navigate(`#/matters/${r.id}`);
    });
  });
}

Pages.matters = async function (query) {
  const status = query.get("status") || "open";
  const q = query.get("q") || "";
  setMain(`<div class="page-head"><div><h1>Matters</h1><p>Your cases and client files${State.user.firm ? " in " + esc(State.user.firm.name) : ""}.</p></div>
    ${can("matter.create") ? `<button class="btn primary" id="new">+ New matter</button>` : ""}</div>
    <div class="card"><form class="row" id="filter">
      <input type="search" name="q" placeholder="Search by title, client, case number, court..." value="${esc(q)}" style="flex:1;min-width:200px">
      <select name="status" style="width:auto">${options({ open: "Open", closed: "Closed", archived: "Archived", all: "All" }, status)}</select>
      <button class="btn">Search</button></form></div>
    <div id="list">${loading()}</div>`);
  if ($("#new")) $("#new").addEventListener("click", newMatterModal);
  $("#filter").addEventListener("submit", e => {
    e.preventDefault();
    const f = formObject(e.target);
    navigate(`#/matters?status=${f.status}&q=${encodeURIComponent(f.q)}`);
  });
  if (query.get("new") && can("matter.create")) newMatterModal();
  const r = await api("GET", `/api/matters?status=${status}&q=${encodeURIComponent(q)}`);
  $("#list").innerHTML = r.matters.length ? `<div class="card table-wrap"><table><thead><tr>
      <th>Matter</th><th>Court</th><th>Next hearing</th><th>Open tasks</th><th>Docs</th></tr></thead><tbody>
      ${r.matters.map(m => `<tr><td><a href="#/matters/${m.id}"><strong>${esc(m.title)}</strong></a>
        ${m.confidential ? badge("Confidential", "warn") : ""}${m.status !== "open" ? badge(m.status) : ""}
        <div class="muted">${esc([m.client_name, m.case_number].filter(Boolean).join(" · "))}</div></td>
        <td>${esc(m.court || "")}</td><td>${m.next_hearing ? fmtDate(m.next_hearing) : "<span class='muted'>-</span>"}</td>
        <td>${m.open_tasks}</td><td>${m.document_count}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="card empty">No matters found.${can("matter.create") ? " Create one with <strong>+ New matter</strong>." : ""}</div>`;
};

Pages.matter = async function (id, query) {
  setMain(loading());
  const { matter: m } = await api("GET", `/api/matters/${id}`);
  const editable = m.my_access === "edit" || m.my_access === "manage";
  const tab = query.get("tab") || "overview";
  const tabs = [["overview", "Overview"], ["hearings", `Hearings (${m.hearings.length})`], ["tasks", `Tasks (${m.tasks.filter(t => t.status === "open").length})`],
    ["documents", `Documents (${m.documents.length})`], ["notes", `Notes (${m.notes.length})`],
    ["research", `Research (${m.research.length + m.saved.length})`], ["drafts", `Drafts (${m.drafts.length})`]];
  setMain(`<div class="page-head"><div><div class="muted"><a href="#/matters">Matters</a> /</div>
      <h1>${esc(m.title)} ${m.confidential ? badge("Confidential", "warn") : ""} ${m.status !== "open" ? badge(m.status) : ""}</h1>
      <p>${esc([m.client_name, m.case_number, m.court].filter(Boolean).join(" · "))}</p></div>
      <div class="row"><a class="btn" href="#/research?matter=${m.id}">Research for this matter</a>
      ${can("draft.create") ? `<a class="btn" href="#/drafts/new?matter=${m.id}">New draft</a>` : ""}</div></div>
    ${m.my_access === "view" ? `<div class="banner info">You have view-only access to this matter.</div>` : ""}
    <div class="tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="tab"></div>`);
  $$("[data-tab]").forEach(b => b.addEventListener("click", () => navigate(`#/matters/${id}?tab=${b.dataset.tab}`)));
  const reload = () => Pages.matter(id, new URLSearchParams(`tab=${tab}`));
  const el = $("#tab");
  ({ overview: matterOverview, hearings: matterHearings, tasks: matterTasks, documents: matterDocuments,
     notes: matterNotes, research: matterResearch, drafts: matterDrafts })[tab](el, m, editable, reload);
};

function matterOverview(el, m, editable, reload) {
  el.innerHTML = `<div class="grid grid-2"><div class="card"><div class="card-head"><h2>Details</h2>
      ${editable ? `<button class="btn small" id="edit">Edit</button>` : ""}</div>
      <dl class="kv">${MATTER_FORM_FIELDS.map(([k, l]) => `<dt>${l}</dt><dd>${esc(m[k] || "-")}</dd>`).join("")}
      <dt>Status</dt><dd>${esc(m.status)}</dd><dt>Next hearing</dt><dd>${m.next_hearing ? fmtDateTime(m.next_hearing) : "-"}</dd></dl>
      ${m.description ? `<hr><p style="white-space:pre-wrap">${esc(m.description)}</p>` : ""}</div>
    <div class="card"><div class="card-head"><h2>Team</h2>${m.my_access === "manage" && m.firm_id ? `<button class="btn small" id="addm">Add person</button>` : ""}</div>
      ${m.members.map(p => `<div class="list-item row"><div><strong>${esc(p.name)}</strong><div class="muted">${esc(p.email)}</div></div>
        <span class="spacer"></span>${badge({ lead: "Lead", member: "Can edit", viewer: "View only" }[p.access])}
        ${m.my_access === "manage" && p.id !== m.owner_id ? `<button class="btn small danger" data-rm="${p.id}">Remove</button>` : ""}</div>`).join("")}
      ${!m.firm_id ? `<p class="muted" style="margin-top:8px">This is a personal matter. Create or join a firm to share matters with colleagues.</p>` : ""}
      ${m.my_access === "manage" ? `<hr><div class="row"><select id="st" style="width:auto">${options({ open: "Open", closed: "Closed", archived: "Archived" }, m.status)}</select>
        <button class="btn small" id="setst">Update status</button><span class="spacer"></span>
        <button class="btn small danger" id="del">Delete matter</button></div>` : ""}</div></div>`;
  if ($("#edit")) $("#edit").addEventListener("click", () => {
    const md = modal(`<h2>Edit matter</h2><form id="em">${matterForm(m)}<div class="row end" style="margin-top:12px">
      <button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`, { wide: true });
    $("#em", md.el).addEventListener("submit", e => {
      e.preventDefault();
      const data = formObject(e.target);
      if (m.my_access !== "manage") delete data.confidential;
      withBusy($("#em .primary", md.el), async () => { await api("PATCH", `/api/matters/${m.id}`, data); md.close(); toast("Saved."); reload(); });
    });
  });
  if ($("#addm")) $("#addm").addEventListener("click", async () => {
    const firm = await api("GET", "/api/firm");
    const present = new Set(m.members.map(p => p.id));
    const choices = (firm.members || []).filter(p => !present.has(p.id));
    const md = modal(`<h2>Add a colleague</h2>${choices.length ? `<form id="am">
      <div class="field"><label>Colleague</label><select name="user_id">${options(choices.map(p => [p.id, `${p.name} (${p.role_label})`]))}</select></div>
      <div class="field"><label>Access</label><select name="access">${options({ member: "Can edit", viewer: "View only", lead: "Lead (can manage team)" })}</select></div>
      <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Add</button></div></form>`
      : `<p>Everyone in the firm is already on this matter.</p><div class="row end"><button class="btn" data-close>Close</button></div>`}`);
    if ($("#am", md.el)) $("#am", md.el).addEventListener("submit", e => {
      e.preventDefault();
      withBusy($("#am .primary", md.el), async () => { await api("POST", `/api/matters/${m.id}/members`, formObject(e.target)); md.close(); reload(); });
    });
  });
  $$("[data-rm]", el).forEach(b => b.addEventListener("click", async () => {
    if (await confirmDialog("Remove this person from the matter?", { danger: true, okLabel: "Remove" })) {
      try { await api("DELETE", `/api/matters/${m.id}/members/${b.dataset.rm}`); reload(); } catch (e) { fail(e); }
    }
  }));
  if ($("#setst")) $("#setst").addEventListener("click", () => withBusy($("#setst"), async () => {
    await api("PATCH", `/api/matters/${m.id}`, { status: $("#st").value }); toast("Status updated."); reload();
  }));
  if ($("#del")) $("#del").addEventListener("click", async () => {
    if (await confirmDialog(`Delete "${m.title}" with all its documents, hearings, tasks and notes? This cannot be undone.`, { danger: true, okLabel: "Delete" })) {
      try { await api("DELETE", `/api/matters/${m.id}`); toast("Matter deleted."); navigate("#/matters"); } catch (e) { fail(e); }
    }
  });
}

function matterHearings(el, m, editable, reload) {
  const today = new Date().toISOString().slice(0, 10);
  el.innerHTML = `${editable ? `<div class="card"><h3>Add a hearing / next date</h3><form id="hf" class="grid grid-4">
      <div><label>Date &amp; time *</label><input type="datetime-local" name="date" required></div>
      <div><label>Purpose</label><input name="purpose" placeholder="e.g. Arguments on bail"></div>
      <div><label>Court room / item no.</label><input name="court_room"></div>
      <div style="align-self:end"><button class="btn primary">Add</button></div></form></div>` : ""}
    <div class="card">${m.hearings.length ? `<table><thead><tr><th>Date</th><th>Purpose</th><th>Outcome / notes</th><th></th></tr></thead><tbody>
      ${m.hearings.map(h => `<tr><td><strong>${fmtDateTime(h.date)}</strong>${h.date.slice(0, 10) >= today ? " " + badge("Upcoming", "info") : ""}
        <div class="muted">${esc(h.court_room || "")}</div></td><td>${esc(h.purpose || "")}</td>
        <td>${esc(h.outcome || h.notes || "")}</td><td>${editable ? `<button class="btn small" data-out="${h.id}">Record outcome</button>
        <button class="btn small danger" data-del="${h.id}">Delete</button>` : ""}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">No hearings recorded.</div>`}</div>`;
  if ($("#hf")) $("#hf").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#hf button"), async () => { await api("POST", `/api/matters/${m.id}/hearings`, formObject(e.target)); toast("Hearing added."); reload(); });
  });
  $$("[data-out]", el).forEach(b => b.addEventListener("click", () => {
    const h = m.hearings.find(x => x.id === b.dataset.out);
    const md = modal(`<h2>Hearing of ${fmtDateTime(h.date)}</h2><form id="of">
      <div class="field"><label>Outcome / order passed</label><textarea name="outcome">${esc(h.outcome || "")}</textarea></div>
      <div class="field"><label>Notes</label><textarea name="notes">${esc(h.notes || "")}</textarea></div>
      <p class="muted">Add the next date as a new hearing.</p>
      <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`);
    $("#of", md.el).addEventListener("submit", e => {
      e.preventDefault();
      withBusy($("#of .primary", md.el), async () => { await api("PATCH", `/api/hearings/${h.id}`, formObject(e.target)); md.close(); reload(); });
    });
  }));
  $$("[data-del]", el).forEach(b => b.addEventListener("click", async () => {
    if (await confirmDialog("Delete this hearing?", { danger: true, okLabel: "Delete" })) {
      try { await api("DELETE", `/api/hearings/${b.dataset.del}`); reload(); } catch (e) { fail(e); }
    }
  }));
}

async function taskModal({ matter = null, onDone }) {
  let people = [{ id: State.user.id, name: "Me" }];
  if (State.user.firm && can("task.assign")) {
    const f = await api("GET", "/api/firm");
    people = people.concat(f.members.filter(p => p.id !== State.user.id).map(p => ({ id: p.id, name: `${p.name} (${p.role_label})` })));
  }
  const md = modal(`<h2>${matter ? "New task" : "New task or help request"}</h2>
    <p class="muted">Assign work to yourself, or ask a colleague for help${matter ? " on this matter" : ""}.</p>
    <form id="tf"><div class="field"><label>What needs doing? *</label><input name="title" required placeholder="e.g. Find Delhi HC rulings on default bail"></div>
    <div class="field"><label>Details</label><textarea name="description"></textarea></div>
    <div class="grid grid-3"><div class="field"><label>Assign to</label><select name="assigned_to">${options(people.map(p => [p.id, p.name]))}</select></div>
    <div class="field"><label>Due date</label><input type="date" name="due_date"></div>
    <div class="field"><label>Priority</label><select name="priority">${options({ normal: "Normal", high: "High", urgent: "Urgent", low: "Low" })}</select></div></div>
    <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Create</button></div></form>`);
  $("#tf", md.el).addEventListener("submit", e => {
    e.preventDefault();
    const data = formObject(e.target);
    if (matter) data.matter_id = matter.id;
    withBusy($("#tf .primary", md.el), async () => { await api("POST", "/api/tasks", data); md.close(); toast("Task created."); onDone(); });
  });
}

function taskRows(tasks, showMatter = false) {
  return tasks.length ? tasks.map(t => `<div class="list-item row">
    <input type="checkbox" data-done="${t.id}" ${t.status === "done" ? "checked" : ""} aria-label="Done" style="width:auto">
    <div style="flex:1"><span class="title" style="${t.status === "done" ? "text-decoration:line-through;opacity:.6" : ""}">${esc(t.title)}</span>
      ${priorityBadge(t.priority)}
      <div class="muted">${t.assignee_name ? "For " + esc(t.assignee_name) : "Unassigned"} · from ${esc(t.creator_name)}
      ${t.due_date ? " · due " + fmtDate(t.due_date) : ""}${showMatter && t.matter_title ? ` · <a href="#/matters/${t.matter_id}">${esc(t.matter_title)}</a>` : ""}</div>
      ${t.description ? `<div style="white-space:pre-wrap;font-size:.9rem">${esc(t.description)}</div>` : ""}</div>
    <button class="btn small danger" data-tdel="${t.id}">Delete</button></div>`).join("") : `<div class="empty">No tasks.</div>`;
}

function wireTasks(el, reload) {
  $$("[data-done]", el).forEach(cb => cb.addEventListener("change", async () => {
    try { await api("PATCH", `/api/tasks/${cb.dataset.done}`, { status: cb.checked ? "done" : "open" }); reload(); } catch (e) { fail(e); cb.checked = !cb.checked; }
  }));
  $$("[data-tdel]", el).forEach(b => b.addEventListener("click", async () => {
    if (await confirmDialog("Delete this task?", { danger: true, okLabel: "Delete" })) {
      try { await api("DELETE", `/api/tasks/${b.dataset.tdel}`); reload(); } catch (e) { fail(e); }
    }
  }));
}

function matterTasks(el, m, editable, reload) {
  el.innerHTML = `<div class="card"><div class="card-head"><h2>Tasks</h2>${editable ? `<button class="btn primary small" id="nt">+ Task / ask for help</button>` : ""}</div>
    ${taskRows(m.tasks)}</div>`;
  if ($("#nt")) $("#nt").addEventListener("click", () => taskModal({ matter: m, onDone: reload }).catch(fail));
  wireTasks(el, reload);
}

function matterDocuments(el, m, editable, reload) {
  const canUpload = editable && can("document.upload");
  el.innerHTML = `${canUpload ? `<div class="card"><h3>Upload documents</h3>
      <p class="muted">PDF, DOCX, TXT or HTML, up to ${25} MB. Text is extracted and indexed so research can search this matter's documents.
      Documents stay private to this matter. Scanned PDFs need OCR first.</p>
      <form id="up" class="row"><input type="file" name="file" required accept=".pdf,.docx,.txt,.md,.html,.htm" style="flex:1">
      <input name="title" placeholder="Title (optional)" style="max-width:260px"><button class="btn primary">Upload</button></form></div>` : ""}
    <div class="card">${m.documents.length ? `<table><thead><tr><th>Document</th><th>Pages</th><th>Uploaded</th><th></th></tr></thead><tbody>
      ${m.documents.map(d => `<tr><td><a href="#/sources/${d.id}"><strong>${esc(d.title)}</strong></a>
        ${(d.security_flags || []).length ? badge("Contains instruction-like text", "warn") : ""}
        <div class="muted">${esc(d.original_filename || "")}${d.current_version > 1 ? " · v" + d.current_version : ""}</div></td>
        <td>${d.page_count || "-"}</td><td>${ago(d.created_at)}</td>
        <td>${editable ? `<button class="btn small danger" data-ddel="${d.id}">Delete</button>` : ""}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">No documents uploaded.</div>`}</div>`;
  if ($("#up")) $("#up").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#up button"), async () => {
      const r = await api("POST", `/api/matters/${m.id}/documents`, new FormData(e.target));
      toast(r.status === "duplicate" ? r.message : `Uploaded. ${r.message}.`);
      reload();
    });
  });
  $$("[data-ddel]", el).forEach(b => b.addEventListener("click", async () => {
    if (await confirmDialog("Delete this document and its search index?", { danger: true, okLabel: "Delete" })) {
      try { await api("DELETE", `/api/matters/${m.id}/documents/${b.dataset.ddel}`); reload(); } catch (e) { fail(e); }
    }
  }));
}

function matterNotes(el, m, editable, reload) {
  el.innerHTML = `${editable ? `<div class="card"><form id="nf"><div class="field"><label>Add a note</label>
      <textarea name="body" required placeholder="Client call, strategy, instructions..."></textarea></div>
      <button class="btn primary">Add note</button></form></div>` : ""}
    <div class="card">${m.notes.length ? m.notes.map(n => `<div class="list-item"><div class="row"><strong>${esc(n.author_name)}</strong>
      <small>${fmtDateTime(n.created_at)}</small><span class="spacer"></span>
      ${n.author_id === State.user.id || m.my_access === "manage" ? `<button class="btn small danger" data-ndel="${n.id}">Delete</button>` : ""}</div>
      <div style="white-space:pre-wrap">${esc(n.body)}</div></div>`).join("") : `<div class="empty">No notes yet.</div>`}</div>`;
  if ($("#nf")) $("#nf").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#nf button"), async () => { await api("POST", `/api/matters/${m.id}/notes`, formObject(e.target)); reload(); });
  });
  $$("[data-ndel]", el).forEach(b => b.addEventListener("click", async () => {
    if (await confirmDialog("Delete this note?", { danger: true, okLabel: "Delete" })) {
      try { await api("DELETE", `/api/notes/${b.dataset.ndel}`); reload(); } catch (e) { fail(e); }
    }
  }));
}

function matterResearch(el, m, editable, reload) {
  el.innerHTML = `<div class="grid grid-2"><div class="card"><div class="card-head"><h2>Research sessions</h2>
      <a class="btn small primary" href="#/research?matter=${m.id}">New research</a></div>
      ${m.research.length ? m.research.map(r => `<div class="list-item"><a href="#/research/${r.id}">${esc(r.query.slice(0, 140))}</a>
        <div class="row">${outcomeBadge(r.outcome)}<small>${ago(r.created_at)}</small></div></div>`).join("") : `<div class="empty">No research run for this matter yet.</div>`}</div>
    <div class="card"><h2>Saved to this matter</h2>
      ${m.saved.length ? m.saved.map(s => `<div class="list-item row"><div style="flex:1">${s.kind === "authority"
        ? `<a href="#/sources/${s.source_id}">${esc(s.source_title)}</a><div class="muted">${esc(s.citation || "")}</div>`
        : `<a href="#/research/${s.research_id}">Research: ${esc((s.research_query || "").slice(0, 120))}</a>`}
        ${s.note ? `<div style="font-size:.9rem">${esc(s.note)}</div>` : ""}</div>
        ${editable ? `<button class="btn small danger" data-sdel="${s.id}">Remove</button>` : ""}</div>`).join("") : `<div class="empty">Nothing saved yet.</div>`}</div></div>`;
  $$("[data-sdel]", el).forEach(b => b.addEventListener("click", async () => {
    try { await api("DELETE", `/api/saved/${b.dataset.sdel}`); reload(); } catch (e) { fail(e); }
  }));
}

function matterDrafts(el, m) {
  el.innerHTML = `<div class="card"><div class="card-head"><h2>Drafts</h2>${can("draft.create") ? `<a class="btn small primary" href="#/drafts/new?matter=${m.id}">New draft</a>` : ""}</div>
    ${m.drafts.length ? m.drafts.map(d => `<div class="list-item"><a href="#/drafts/${d.id}">${esc(d.title)}</a>
      <div class="muted">v${d.version} · edited ${ago(d.updated_at)}</div></div>`).join("") : `<div class="empty">No drafts for this matter.</div>`}</div>`;
}
