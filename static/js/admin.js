/* Platform administration: corpus coverage, ingestion, sources, users. */
"use strict";

let ADMIN_OPTS = null;

Pages.admin = async function (query) {
  const tab = query.get("tab") || "overview";
  if (!ADMIN_OPTS) ADMIN_OPTS = await api("GET", "/api/admin/corpus/options");
  setMain(`<div class="page-head"><div><h1>Corpus admin</h1><p>Manage the legal library that research searches, and the users of this server.</p></div></div>
    <div class="tabs">${[["overview", "Coverage & index"], ["ai", "AI settings"], ["import", "Import judgments"], ["add", "Add sources"],
      ["sources", "Sources"], ["jobs", "Ingestion log"], ["users", "Users"], ["firms", "Firms"], ["deletions", "Deletion requests"]].map(([k, l]) => `<button data-tab="${k}" class="${tab === k ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="tab">${loading()}</div>`);
  $$("[data-tab]").forEach(b => b.addEventListener("click", () => navigate(`#/admin?tab=${b.dataset.tab}`)));
  const el = $("#tab");
  return ({ overview: adminOverview, ai: adminAI, import: adminImport, add: adminAdd, sources: adminSources, jobs: adminJobs,
    users: adminUsers, firms: adminFirms, deletions: adminDeletions })[tab](el, query);
};

function countTable(title, obj, labels = {}) {
  const rows = Object.entries(obj || {});
  return `<div class="card"><h3>${title}</h3>${rows.length ? `<table><tbody>${rows.map(([k, n]) => `<tr><td>${esc(Array.isArray(labels[k]) ? labels[k][0] : labels[k] || k)}</td>
    <td style="text-align:right">${n}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">None</div>`}</div>`;
}

async function adminOverview(el) {
  const s = await api("GET", "/api/admin/corpus/stats");
  const primary = (s.by_class || {}).primary || 0;
  el.innerHTML = `
    ${primary === 0 ? `<div class="banner warn"><strong>No primary sources yet</strong>The library has only the starter editorial summaries.
      Add bare Acts (e.g. PDFs from indiacode.nic.in) and judgments (from sci.gov.in, High Court sites, or eCourts) under <a href="#/admin?tab=add">Add sources</a>.</div>` : ""}
    <div class="grid grid-4">
      <div class="card"><div class="muted">Library sources</div><div class="stat">${s.total_sources}</div><small>${s.inactive_sources} inactive</small></div>
      <div class="card"><div class="muted">Indexed passages</div><div class="stat">${s.total_passages}</div><small>${s.fts_rows} in keyword index</small></div>
      <div class="card"><div class="muted">Matter documents</div><div class="stat">${s.matter_documents}</div><small>private to their matters</small></div>
      <div class="card"><div class="muted">Failed ingestions</div><div class="stat">${(s.jobs || {}).failed || 0}</div><small>${(s.jobs || {}).duplicate || 0} duplicates skipped</small></div></div>
    <div class="card"><div class="card-head"><h2>Vector index</h2>${s.embedding.stale ? `<button class="btn primary small" id="reembed">Re-embed ${s.embedding.stale} passages</button>` : ""}</div>
      <p>Active embedding model: <strong>${esc(s.embedding.active_model)}</strong>.
      ${s.embedding.stale ? `<span class="badge warn">${s.embedding.stale} passages were embedded with a different model and are invisible to semantic search until re-embedded.</span>` : badge("All passages up to date", "ok")}</p>
      <small class="muted">${Object.entries(s.embedding.by_model).map(([k, v]) => `${esc(k)}: ${v}`).join(" · ")} · index generation ${esc(s.index_generation)}</small></div>
    <div class="grid grid-3">${countTable("By type", s.by_kind, LABELS.kind)}${countTable("By authority class", s.by_class)}
      ${countTable("By court", s.by_court_level, LABELS.court_level)}${countTable("Verification", s.by_verification, LABELS.verification)}
      ${countTable("Legal status", s.by_status, LABELS.legal_status)}${countTable("Origin", s.by_origin)}</div>
    <div class="card"><div class="card-head"><h2>Starter summaries</h2><div class="row">
      <button class="btn small" id="starter-load">Load 66 summaries</button><button class="btn small danger" id="starter-remove">Remove them</button></div></div>
      <p class="muted">Hand-written summaries of landmark cases, labelled as secondary sources. Useful for a demo; remove them once real judgments are imported.
      Currently loaded: ${(s.by_origin || {}).seed || 0}.</p></div>
    <div class="card"><h2>Recent failures</h2>${s.recent_failures.length ? s.recent_failures.map(f => `<div class="list-item"><strong>${esc(f.input_ref)}</strong>
      <small class="muted">${fmtDateTime(f.started_at)}</small><div>${esc(f.message)}</div></div>`).join("") : `<div class="empty">No failed ingestions.</div>`}</div>`;
  ["load", "remove"].forEach(action => $(`#starter-${action}`).addEventListener("click", async () => {
    if (action === "remove" && !(await confirmDialog("Remove all starter summaries from the library?", { danger: true, okLabel: "Remove" }))) return;
    withBusy($(`#starter-${action}`), async () => {
      const r = await api("POST", "/api/admin/corpus/starter", { action });
      toast(action === "load" ? `Loaded ${r.count} summaries.` : `Removed ${r.count} summaries.`);
      State.status = null;
      adminOverview(el);
    });
  }));
  if ($("#reembed")) $("#reembed").addEventListener("click", () => withBusy($("#reembed"), async () => {
    const r = await api("POST", "/api/admin/corpus/reembed");
    toast(`Re-embedded ${r.reembedded} passages.`);
    adminOverview(el);
  }));
}

function metaFields(src = {}) {
  const o = ADMIN_OPTS;
  const f = (name, label, ph = "", type = "text") => `<div class="field"><label>${label}</label><input type="${type}" name="${name}" value="${esc(src[name] || "")}" placeholder="${esc(ph)}"></div>`;
  return `<div class="grid grid-3">
    <div class="field"><label>Type</label><select name="kind">${options(o.kinds, src.kind || "judgment")}</select></div>
    <div class="field"><label>Authority class</label><select name="authority_class">${options(o.authority_classes, src.authority_class || "primary")}</select></div>
    <div class="field"><label>Verification</label><select name="verification">${options({ unverified: "Unverified", verified_official: "Verified against official source", editorial_summary: "Editorial summary" }, src.verification || "unverified")}</select></div></div>
    ${f("title", "Title *", "e.g. Arnesh Kumar v. State of Bihar / The Limitation Act, 1963")}
    <div class="grid grid-3">${f("citation", "Citation", "(2014) 8 SCC 273")}${f("neutral_citation", "Neutral citation", "2023 INSC 123")}
    ${f("alt_citations", "Other citations (separate with ;)", "AIR 2014 SC 2756")}${f("court", "Court", "Supreme Court of India")}
    ${f("bench", "Bench", "2-Judge Bench")}${f("judges", "Judges")}${f("decision_date", "Decision date", "", "date")}
    ${f("case_number", "Case number")}${f("parties", "Parties")}${f("act_name", "Act (for statutes)")}${f("jurisdiction", "Jurisdiction / state", "India")}
    ${f("legal_domain", "Legal domain", "criminal")}</div>
    <div class="grid grid-2"><div class="field"><label>Legal status</label><select name="legal_status">${options(Object.fromEntries(o.legal_statuses.map(x => [x, (LABELS.legal_status[x] || [x])[0]])), src.legal_status || "unknown")}</select></div>
    ${f("status_note", "Status note", "e.g. Overruled by ... in ...")}</div>${f("source_url", "Source URL", "https://...")}`;
}

function adminAdd(el) {
  el.innerHTML = `<div class="banner info"><strong>Prefer official sources</strong>India Code (indiacode.nic.in) for Acts and rules; the Supreme Court
    (sci.gov.in) and High Court websites or eCourts for judgments. Mark a source "Verified against official source" only after checking it there.</div>
    <div class="tabs" id="addtabs">${[["file", "Upload file"], ["url", "Import from URL"], ["text", "Paste text"], ["kanoon", "Indian Kanoon API"]]
      .map(([k, l], i) => `<button data-add="${k}" class="${i === 0 ? "active" : ""}">${l}</button>`).join("")}</div><div id="addbody"></div>`;
  const body = $("#addbody");
  const show = k => {
    $$("[data-add]").forEach(b => b.classList.toggle("active", b.dataset.add === k));
    if (k === "file") {
      body.innerHTML = `<div class="card"><form id="af"><div class="field"><label>File (PDF, DOCX, TXT, HTML) *</label><input type="file" name="file" required accept=".pdf,.docx,.txt,.md,.html,.htm"></div>
        ${metaFields()}<div class="field"><label>Version note</label><input name="note"></div><button class="btn primary">Upload &amp; index</button></form></div>`;
      $("#af").addEventListener("submit", e => { e.preventDefault(); withBusy($("#af .primary"), async () => done(await api("POST", "/api/admin/corpus/upload", new FormData(e.target)))); });
    } else if (k === "url") {
      body.innerHTML = `<div class="card"><form id="uf"><div class="field"><label>URL of a PDF or web page *</label>
        <div class="row"><input type="url" name="url" required placeholder="https://..." style="flex:1">
        <button type="button" class="btn" id="fetchmeta">Fetch details</button></div>
        <div class="hint">India Code links (the <code>indiacode.gov.in/pdf-viewer/...</code> address of an Act, or its <code>/handle/...</code> page)
          are recognised: click <strong>Fetch details</strong> and the official title, Act number, year and jurisdiction are filled in for you.</div>
        <div id="fetchout"></div></div>
        ${metaFields()}<button class="btn primary">Import</button></form></div>`;
      const uf = $("#uf");
      $("#fetchmeta").addEventListener("click", () => withBusy($("#fetchmeta"), async () => {
        const r = await api("GET", `/api/admin/corpus/url-preview?url=${encodeURIComponent(uf.url.value.trim())}`);
        if (!r.recognised) {
          $("#fetchout").innerHTML = `<div class="banner info" style="margin-top:8px">Not an India Code link, so fill in the details below yourself.</div>`;
          return;
        }
        Object.entries(r.meta).forEach(([k, v]) => { if (v && uf.elements[k]) uf.elements[k].value = v; });
        $("#fetchout").innerHTML = `<div class="banner ok" style="margin-top:8px"><strong>Filled from ${esc(r.source)}</strong>${esc(r.long_title || "")}
          <div>Check <em>Legal status</em>: India Code does not say whether the Act was later amended or repealed.</div></div>`;
      }));
      uf.addEventListener("submit", e => { e.preventDefault(); withBusy($("#uf .primary"), async () => done(await api("POST", "/api/admin/corpus/import-url", formObject(e.target)))); });
    } else if (k === "text") {
      body.innerHTML = `<div class="card"><form id="tf">${metaFields()}<div class="field"><label>Text *</label><textarea name="text" rows="14" required></textarea></div>
        <button class="btn primary">Index text</button></form></div>`;
      $("#tf").addEventListener("submit", e => { e.preventDefault(); withBusy($("#tf .primary"), async () => done(await api("POST", "/api/admin/corpus/text", formObject(e.target)))); });
    } else {
      body.innerHTML = `<div class="card"><p class="muted">Uses the official Indian Kanoon API (paid token from api.indiankanoon.org, set as KANOON_API_TOKEN).
        LawPilot does not scrape websites.</p><form id="ks" class="row"><input type="search" name="q" placeholder="Search Indian Kanoon" style="flex:1" required>
        <button class="btn">Search</button></form><div id="kr"></div></div>`;
      $("#ks").addEventListener("submit", e => {
        e.preventDefault();
        withBusy($("#ks button"), async () => {
          const r = await api("GET", `/api/admin/corpus/kanoon/search?q=${encodeURIComponent(e.target.q.value)}`);
          $("#kr").innerHTML = r.items.map(d => `<div class="list-item row"><div style="flex:1"><strong>${esc(d.title)}</strong>
            <div class="muted">${esc([d.court, d.date].filter(Boolean).join(" · "))}</div><div style="font-size:.88rem">${esc(d.snippet)}</div></div>
            <button class="btn small primary" data-kimp="${esc(d.doc_id)}" data-court="${esc(d.court || "")}">Import</button></div>`).join("") || `<div class="empty">No results.</div>`;
          $$("[data-kimp]").forEach(b => b.addEventListener("click", () => withBusy(b, async () => {
            done(await api("POST", "/api/admin/corpus/kanoon/import", { doc_id: b.dataset.kimp, kind: "judgment", authority_class: "primary" }));
          })));
        });
      });
    }
  };
  const done = r => {
    if (r.status === "duplicate") toast(r.message);
    else { toast(r.message); }
    if (r.id) navigate(`#/sources/${r.id}`);
  };
  $$("[data-add]").forEach(b => b.addEventListener("click", () => show(b.dataset.add)));
  show("file");
}

async function adminSources(el, query) {
  const q = query.get("q") || "";
  const kind = query.get("kind") || "";
  const ver = query.get("verification") || "";
  el.innerHTML = `<div class="card"><form id="sf" class="row"><input type="search" name="q" value="${esc(q)}" placeholder="Title, citation, court, Act" style="flex:1">
    <select name="kind" style="width:auto">${options(ADMIN_OPTS.kinds, kind, { blank: "Any type" })}</select>
    <select name="verification" style="width:auto">${options(LABELS.verification ? Object.fromEntries(Object.entries(LABELS.verification).map(([k, v]) => [k, v[0]])) : {}, ver, { blank: "Any verification" })}</select>
    <button class="btn">Filter</button></form></div><div class="card table-wrap" id="sl">${loading()}</div>`;
  $("#sf").addEventListener("submit", e => {
    e.preventDefault();
    const f = formObject(e.target);
    navigate(`#/admin?tab=sources&q=${encodeURIComponent(f.q)}&kind=${f.kind}&verification=${f.verification}`);
  });
  const r = await api("GET", `/api/admin/corpus/sources?q=${encodeURIComponent(q)}&kind=${kind}&verification=${ver}&limit=200`);
  $("#sl").innerHTML = `<div class="muted" style="margin-bottom:6px">${r.total} source(s)</div><table><thead><tr><th>Source</th><th>Status</th><th>Passages</th><th>Updated</th><th></th></tr></thead><tbody>
    ${r.items.map(s => `<tr><td><a href="#/sources/${s.id}"><strong>${esc(s.title)}</strong></a>${s.is_active ? "" : " " + badge("Inactive", "bad")}
      <div class="muted">${esc([kindLabel(s.kind), s.citation, s.court, s.decision_date].filter(Boolean).join(" · "))}</div></td>
      <td>${statusBadge(s.legal_status)}<br>${verificationBadge(s.verification)}</td><td>${s.passages}<div class="muted">v${s.current_version}</div></td>
      <td>${ago(s.updated_at)}</td><td class="row"><button class="btn small" data-edit="${s.id}">Edit</button></td></tr>`).join("")}</tbody></table>`;
  $$("[data-edit]").forEach(b => b.addEventListener("click", () => editSourceModal(b.dataset.edit, () => adminSources(el, query))));
}

async function editSourceModal(id, reload) {
  const { source: s } = await api("GET", `/api/sources/${id}`);
  const md = modal(`<h2>Edit source</h2><form id="es">${metaFields({ ...s, alt_citations: (s.alt_citations || []).join("; ") })}
    <label class="check"><input type="checkbox" name="is_active" data-bool ${s.is_active ? "checked" : ""}> Active (included in search)</label>
    <div class="row" style="margin-top:12px"><button type="button" class="btn danger" id="del">Delete</button><button type="button" class="btn" id="ver">Upload new version</button>
    <button type="button" class="btn" id="rel">Add relationship</button><span class="spacer"></span>
    <button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`, { wide: true });
  $("#es", md.el).addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#es .primary", md.el), async () => { await api("PATCH", `/api/admin/corpus/sources/${id}`, formObject(e.target)); md.close(); toast("Saved."); reload(); });
  });
  $("#del", md.el).addEventListener("click", async () => {
    if (await confirmDialog(`Permanently delete "${s.title}" and its index? Research sessions keep their stored passages.`, { danger: true, okLabel: "Delete" })) {
      try { await api("DELETE", `/api/admin/corpus/sources/${id}`); md.close(); toast("Deleted."); reload(); } catch (err) { fail(err); }
    }
  });
  $("#ver", md.el).addEventListener("click", () => {
    md.close();
    const vm = modal(`<h2>New version of ${esc(s.title)}</h2><p class="muted">Earlier versions are kept so past research stays traceable.</p>
      <form id="nv"><div class="field"><label>File</label><input type="file" name="file" required accept=".pdf,.docx,.txt,.md,.html,.htm"></div>
      <div class="field"><label>What changed?</label><input name="note" placeholder="e.g. Amended by Act 12 of 2025"></div>
      <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Upload</button></div></form>`);
    $("#nv", vm.el).addEventListener("submit", e => {
      e.preventDefault();
      withBusy($("#nv .primary", vm.el), async () => { const r = await api("POST", `/api/admin/corpus/sources/${id}/version`, new FormData(e.target)); vm.close(); toast(r.message); reload(); });
    });
  });
  $("#rel", md.el).addEventListener("click", () => {
    md.close();
    const rm = modal(`<h2>Relationship for ${esc(s.title)}</h2><form id="rf">
      <div class="field"><label>This source...</label><select name="relation">${options({ overrules: "overrules", reverses: "reverses", distinguishes: "distinguishes", follows: "follows", affirms: "affirms" })}</select></div>
      <div class="field"><label>...the source titled</label><input name="target_q" placeholder="Search by title or citation" required><div id="tq"></div></div>
      <div class="field"><label>Note</label><input name="note"></div>
      <label class="check"><input type="checkbox" name="update_status" data-bool checked> Mark the other source as overruled (for overrules / reverses)</label>
      <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`);
    let target = null;
    $("input[name=target_q]", rm.el).addEventListener("input", async e => {
      const v = e.target.value.trim();
      if (v.length < 3) return;
      const r = await api("GET", `/api/admin/corpus/sources?q=${encodeURIComponent(v)}&limit=8`);
      $("#tq", rm.el).innerHTML = r.items.filter(x => x.id !== id).map(x => `<button type="button" class="btn small" data-t="${x.id}" style="margin:4px 4px 0 0">${esc(x.title.slice(0, 60))}</button>`).join("");
      $$("[data-t]", rm.el).forEach(b => b.addEventListener("click", () => { target = b.dataset.t; $$("[data-t]", rm.el).forEach(x => x.classList.toggle("primary", x === b)); }));
    });
    $("#rf", rm.el).addEventListener("submit", e => {
      e.preventDefault();
      if (!target) return toast("Pick the other source from the search results.", true);
      const f = formObject(e.target);
      withBusy($("#rf .primary", rm.el), async () => {
        await api("POST", "/api/admin/corpus/relations", { from_source_id: id, to_source_id: target, relation: f.relation, note: f.note, update_status: f.update_status });
        rm.close(); toast("Relationship saved."); reload();
      });
    });
  });
}

async function adminJobs(el, query) {
  const status = query.get("status") || "";
  el.innerHTML = `<div class="row" style="margin-bottom:12px"><select id="js" style="width:auto">${options({ succeeded: "Succeeded", failed: "Failed", duplicate: "Duplicate", running: "Running" }, status, { blank: "All jobs" })}</select></div>
    <div class="card table-wrap" id="jl">${loading()}</div>`;
  $("#js").addEventListener("change", e => navigate(`#/admin?tab=jobs&status=${e.target.value}`));
  const r = await api("GET", `/api/admin/corpus/jobs?status=${status}&limit=200`);
  const kind = { succeeded: "ok", failed: "bad", duplicate: "warn", running: "info" };
  $("#jl").innerHTML = r.items.length ? `<table><thead><tr><th>When</th><th>Input</th><th>Result</th><th>By</th></tr></thead><tbody>
    ${r.items.map(j => `<tr><td style="white-space:nowrap">${fmtDateTime(j.started_at)}</td>
      <td>${j.source_id && j.status !== "failed" ? `<a href="#/sources/${j.source_id}">${esc(j.input_ref)}</a>` : esc(j.input_ref)}</td>
      <td>${badge(j.status, kind[j.status])}<div style="font-size:.85rem">${esc(j.message || "")}</div></td><td>${esc(j.created_by_name || "system")}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">No ingestion jobs.</div>`;
}

async function adminUsers(el, query) {
  const q = query.get("q") || "";
  el.innerHTML = `<div class="card"><form id="uf" class="row"><input type="search" name="q" value="${esc(q)}" placeholder="Name or email" style="flex:1"><button class="btn">Search</button></form></div>
    <div class="card table-wrap" id="ul">${loading()}</div>`;
  $("#uf").addEventListener("submit", e => { e.preventDefault(); navigate(`#/admin?tab=users&q=${encodeURIComponent(e.target.q.value)}`); });
  const r = await api("GET", `/api/admin/users?q=${encodeURIComponent(q)}`);
  $("#ul").innerHTML = `<table><thead><tr><th>User</th><th>Firm</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead><tbody>
    ${r.items.map(u => `<tr><td><strong>${esc(u.name)}</strong>${u.is_platform_admin ? " " + badge("Admin", "info") : ""}<div class="muted">${esc(u.email)}</div></td>
      <td>${esc(u.firm_name || "-")}${u.role ? `<div class="muted">${esc(u.role)}</div>` : ""}</td>
      <td>${badge(u.status, u.status === "active" ? "ok" : "warn")}</td><td>${u.last_login_at ? ago(u.last_login_at) : "never"}</td>
      <td class="row">${u.id === State.user.id ? "" : `<button class="btn small" data-toggle="${u.id}" data-status="${u.status === "disabled" ? "active" : "disabled"}">${u.status === "disabled" ? "Enable" : "Disable"}</button>
        <button class="btn small" data-admin="${u.id}" data-val="${u.is_platform_admin ? "0" : "1"}">${u.is_platform_admin ? "Remove admin" : "Make admin"}</button>`}</td></tr>`).join("")}</tbody></table>`;
  $$("[data-toggle]").forEach(b => b.addEventListener("click", async () => {
    try { await api("PATCH", `/api/admin/users/${b.dataset.toggle}`, { status: b.dataset.status }); adminUsers(el, query); } catch (e) { fail(e); }
  }));
  $$("[data-admin]").forEach(b => b.addEventListener("click", async () => {
    if (b.dataset.val === "1" && !(await confirmDialog("Make this person a platform administrator? Administrator accounts manage the "
      + "library, AI settings and users, and cannot use matters, drafts, tasks or firms.", { okLabel: "Make administrator" }))) return;
    try { await api("PATCH", `/api/admin/users/${b.dataset.admin}`, { is_platform_admin: b.dataset.val === "1" }); adminUsers(el, query); } catch (e) { fail(e); }
  }));
}

async function adminDeletions(el) {
  const r = await api("GET", "/api/admin/deletion-requests");
  el.innerHTML = `<div class="card">${r.items.length ? r.items.map(d => `<div class="list-item row"><div style="flex:1"><strong>${esc(d.name)}</strong> <span class="muted">${esc(d.email)}</span>
    <div class="muted">Requested ${fmtDateTime(d.requested_at)}</div>${d.reason ? `<div>${esc(d.reason)}</div>` : ""}</div>
    <button class="btn small danger" data-proc="${d.id}">Process deletion</button></div>`).join("") : `<div class="empty">No pending deletion requests.</div>`}
    <p class="muted" style="margin-top:10px">Processing deletes the user's personal matters, research, drafts and saved items, anonymises the account,
    and transfers firm matters they owned to another firm member.</p></div>`;
  $$("[data-proc]").forEach(b => b.addEventListener("click", async () => {
    if (await confirmDialog("Permanently delete this user's personal data? This cannot be undone.", { danger: true, okLabel: "Delete data" })) {
      try { await api("POST", `/api/admin/deletion-requests/${b.dataset.proc}/process`); toast("Processed."); adminDeletions(el); } catch (e) { fail(e); }
    }
  }));
}


/* ── AI settings ───────────────────────────────────────── */
const KEY_FIELD = { anthropic: "anthropic_api_key", gemini: "gemini_api_key", openai: "openai_api_key",
  openrouter: "openrouter_api_key", groq: "groq_api_key" };
const KEY_HELP = {
  anthropic: "Create a key at console.anthropic.com (paid, pay-as-you-go).",
  gemini: "Create a free key at aistudio.google.com. On Google's free tier, prompts may be used to improve Google's products, so avoid sending confidential client documents through it.",
  openai: "Create a key at platform.openai.com.",
  openrouter: "Create a key at openrouter.ai (gives access to many models).",
  groq: "Create a key at console.groq.com (fast, has a free tier).",
};

async function adminAI(el) {
  const v = await api("GET", "/api/admin/ai");
  const current = v.llm_provider && v.llm_provider !== "none" ? v.llm_provider : (v.active ? v.active.provider : "anthropic");
  el.innerHTML = `
    <div class="banner ${v.active ? "ok" : "warn"}"><strong>${v.active ? "AI answers are on" : "AI answers are off"}</strong>
      ${v.active ? `Using ${esc(v.active.provider)} / ${esc(v.active.model)}.` : "Research shows ranked passages only until you add an API key below, test it, and save."}</div>
    <div class="grid grid-2"><div class="card"><h2>Answer model</h2><form id="aif">
      <div class="field"><label>Provider</label><select name="llm_provider" id="prov">${options(Object.fromEntries(v.providers.map(p => [p.key, p.label])), current)}</select></div>
      <div class="field"><label>API key</label><input type="password" id="apikey" autocomplete="off" placeholder="Paste a new key to set or replace it">
        <div class="hint" id="keystate"></div><div class="hint" id="keyhelp"></div></div>
      <div class="field"><label>Model</label><div class="row"><input id="model" list="models" value="${esc(v.llm_model || "")}" style="flex:1">
        <button type="button" class="btn small" id="loadmodels">Load available models</button></div><datalist id="models"></datalist>
        <div class="hint" id="modelhint"></div></div>
      <div class="row"><button type="button" class="btn" id="test">Test connection</button><span class="spacer"></span>
        <button type="button" class="btn danger small" id="clearkey">Remove key</button><button class="btn primary">Save</button></div>
      <div id="testout" style="margin-top:10px"></div></form></div>
    <div class="card"><h2>Search embeddings</h2><form id="ef">
      <div class="field"><label>Embedding model</label><select name="embedding_provider">${options({ local: "Built-in (free, lexical-semantic)",
        voyage: "Voyage voyage-law-2 (legal-domain, recommended)", openai: "OpenAI text-embedding-3-small" }, v.embedding_provider || "local")}</select></div>
      <div class="field"><label>Voyage API key</label><input type="password" name="voyage_api_key" autocomplete="off"
        placeholder="${v.keys.voyage_api_key.set ? "Set " + esc(v.keys.voyage_api_key.hint) + " (paste to replace)" : "Paste key from dash.voyageai.com"}"></div>
      <p class="muted">Active: <strong>${esc(v.embedding_model)}</strong>. After changing the embedding model, run <em>Re-embed</em> on the Coverage tab
        so existing passages become searchable by meaning again. The OpenAI option uses the OpenAI key from the answer-model settings.</p>
      <button class="btn primary">Save embeddings</button></form></div></div>`;

  const prov = $("#prov");
  const refresh = () => {
    const p = prov.value;
    const k = v.keys[KEY_FIELD[p]];
    $("#keystate").innerHTML = k.set ? `Key set ${esc(k.hint)} (from ${esc(k.source)}).` : "<strong>No key set for this provider.</strong>";
    $("#keyhelp").textContent = KEY_HELP[p] || "";
    const def = v.providers.find(x => x.key === p).default_model;
    $("#model").placeholder = `default: ${def}`;
    $("#modelhint").textContent = `Leave empty to use ${def}, or click "Load available models" to pick one your key can use.`;
  };
  prov.addEventListener("change", () => { $("#model").value = ""; $("#models").innerHTML = ""; refresh(); });
  refresh();

  const body = () => ({ provider: prov.value, api_key: $("#apikey").value.trim(), model: $("#model").value.trim() });
  $("#loadmodels").addEventListener("click", () => withBusy($("#loadmodels"), async () => {
    const r = await api("POST", "/api/admin/ai/models", body());
    $("#models").innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join("");
    toast(`${r.models.length} models available. Click the Model box to choose one.`);
  }));
  $("#test").addEventListener("click", () => withBusy($("#test"), async () => {
    const r = await api("POST", "/api/admin/ai/test", body());
    $("#testout").innerHTML = r.ok
      ? `<div class="banner ok"><strong>Connected</strong>${esc(r.model)} replied "${esc(r.reply)}" in ${r.seconds}s. Click Save to use it.</div>`
      : `<div class="banner bad"><strong>Connection failed</strong>${esc(r.error)}</div>`;
  }));
  $("#aif").addEventListener("submit", e => {
    e.preventDefault();
    const b = body();
    const payload = { llm_provider: b.provider, llm_model: b.model, keys: {} };
    if (b.api_key) payload.keys[KEY_FIELD[b.provider]] = b.api_key;
    withBusy($("#aif .primary"), async () => {
      const r = await api("POST", "/api/admin/ai", payload);
      State.status = null;
      toast(r.active ? `Saved. AI answers now use ${r.active.provider} / ${r.active.model}.` : "Saved, but no usable key is set for this provider.", !r.active);
      adminAI(el);
    });
  });
  $("#clearkey").addEventListener("click", async () => {
    if (!(await confirmDialog(`Remove the stored ${prov.value} key?`, { danger: true, okLabel: "Remove" }))) return;
    try { await api("POST", "/api/admin/ai", { keys: { [KEY_FIELD[prov.value]]: "" } }); State.status = null; adminAI(el); } catch (e) { fail(e); }
  });
  $("#ef").addEventListener("submit", e => {
    e.preventDefault();
    const f = formObject(e.target);
    const payload = { embedding_provider: f.embedding_provider, keys: {} };
    if (f.voyage_api_key) payload.keys.voyage_api_key = f.voyage_api_key;
    withBusy($("#ef .primary"), async () => { await api("POST", "/api/admin/ai", payload); toast("Saved. Now run Re-embed on the Coverage tab."); adminAI(el); });
  });
}

/* ── Import real judgments ─────────────────────────────── */
let importTimer = null;

async function adminImport(el) {
  clearTimeout(importTimer);
  const year = new Date().getFullYear();
  const status = await api("GET", "/api/system/status");
  el.innerHTML = `<div class="card"><h2>Supreme Court of India judgments</h2>
      <p>Imports real judgments, full text plus metadata (parties, SCR and neutral citations, coram, decision date, case number, bench),
      from the public <em>Indian Supreme Court Judgments</em> open dataset: about 35,000 judgments from 1950 onwards, CC-BY-4.0,
      sourced from the Supreme Court's judgment portal. Judgments already in the library are skipped, so re-running picks up only new ones.</p>
      <div class="banner info">Each judgment takes roughly 0.5–1 MB once indexed. A free 0.5 GB cloud database holds a few hundred;
      the full collection needs self-hosted PostgreSQL with tens of GB of disk. Start with recent years or a per-year limit.
      Old scanned judgments without a text layer are skipped and reported.</div>
      ${status.serverless ? `<div class="banner warn"><strong>Run imports from your computer</strong>This site runs on Vercel, which stops
        background work after a few minutes, so imports can't run from this page. On your computer, in the LawPilot folder (with the same
        <code>.env</code>), run <code>venv\\Scripts\\python.exe -m lawpilot.importers --years ${year - 1}</code>. It writes to the same database,
        so the judgments appear here straight away, and progress shows in the table below.</div>` : ""}
      <form id="imf" class="grid grid-4" ${status.serverless ? 'style="display:none"' : ""}>
        <div><label>From year</label><input type="number" name="year_from" min="1950" max="${year}" value="${year - 1}" required></div>
        <div><label>To year</label><input type="number" name="year_to" min="1950" max="${year}" value="${year - 1}"></div>
        <div><label>Max per year</label><input type="number" name="limit_per_year" min="1" placeholder="no limit" value="50"></div>
        <div style="align-self:end"><button class="btn primary">Start import</button></div></form>
      <small class="muted">Runs in the background on the server; you can leave this page. For very large imports use the command line:
      <code>python -m lawpilot.importers --years 2015-2024</code></small></div>
    <div class="card"><h2>Import runs</h2><div id="runs">${loading()}</div></div>`;
  const loadRuns = async () => {
    if (!document.body.contains($("#runs"))) return;
    const r = await api("GET", "/api/admin/imports");
    const kind = { running: "info", cancelling: "warn", completed: "ok", failed: "bad", cancelled: "warn" };
    $("#runs").innerHTML = r.items.length ? `<div class="table-wrap"><table><thead><tr><th>Started</th><th>Scope</th><th>Status</th><th>Progress</th><th></th></tr></thead><tbody>
      ${r.items.map(x => {
        const p = typeof x.params === "string" ? JSON.parse(x.params || "{}") : (x.params || {});
        const yrs = p.years || [];
        return `<tr><td>${fmtDateTime(x.started_at)}<div class="muted">${esc(x.created_by_name || "command line")}</div></td>
          <td>${yrs.length ? `${yrs[0]}${yrs.length > 1 ? "–" + yrs[yrs.length - 1] : ""}` : ""}${p.limit_per_year ? `<div class="muted">max ${p.limit_per_year}/year</div>` : ""}</td>
          <td>${badge(x.status, kind[x.status])}</td>
          <td>${x.done}/${x.total} processed · <strong>${x.imported}</strong> imported · ${x.skipped} skipped · ${x.failed} failed
            ${x.message ? `<div class="muted" style="font-size:.85rem">${esc(x.message)}</div>` : ""}</td>
          <td>${x.status === "running" ? `<button class="btn small danger" data-cancel="${x.id}">Cancel</button>` : ""}</td></tr>`;
      }).join("")}</tbody></table></div>` : `<div class="empty">No imports yet.</div>`;
    $$("[data-cancel]").forEach(b => b.addEventListener("click", async () => { await api("POST", `/api/admin/imports/${b.dataset.cancel}/cancel`); loadRuns(); }));
    if (r.items.some(x => x.status === "running" || x.status === "cancelling")) importTimer = setTimeout(loadRuns, 3000);
  };
  $("#imf").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#imf button"), async () => { await api("POST", "/api/admin/imports/sci", formObject(e.target)); toast("Import started."); loadRuns(); });
  });
  loadRuns().catch(fail);
}


/* ── Firms using this server (no access to their matters) ── */
async function adminFirms(el) {
  const r = await api("GET", "/api/admin/firms");
  el.innerHTML = `<div class="banner info">Firms create themselves: any lawyer who signs up can create a firm and becomes its Firm Admin,
      then invites colleagues. You see who they are and how much they use LawPilot, never their matters or documents.</div>
    <div class="card table-wrap">${r.items.length ? `<table><thead><tr><th>Firm</th><th>Firm Admin(s)</th><th>Members</th><th>Matters</th><th>Last active</th></tr></thead><tbody>
      ${r.items.map(f => `<tr><td><strong>${esc(f.name)}</strong><div class="muted">${esc(f.city || "")}${f.city ? " · " : ""}since ${fmtDate(f.created_at)}</div></td>
        <td>${f.admins.map(a => `${esc(a.name)}<div class="muted">${esc(a.email)}</div>`).join("") || "-"}</td>
        <td>${f.members}</td><td>${f.matters}</td><td>${f.last_active ? ago(f.last_active) : "never"}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">No firms yet.</div>`}</div>`;
}
