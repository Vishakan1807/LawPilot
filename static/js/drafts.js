/* Drafts: template gallery, editor, pre-filing checks, AI assist. */
"use strict";

Pages.drafts = async function () {
  setMain(`<div class="page-head"><div><h1>Drafts</h1><p>Notices, petitions, plaints, applications and opinions.</p></div>
    ${can("draft.create") ? `<a class="btn primary" href="#/drafts/new">+ New draft</a>` : ""}</div><div id="dl">${loading()}</div>`);
  const r = await api("GET", "/api/drafts");
  $("#dl").innerHTML = `<div class="card">${r.drafts.length ? `<table><thead><tr><th>Draft</th><th>Matter</th><th>Version</th><th>Edited</th></tr></thead><tbody>
    ${r.drafts.map(d => `<tr><td><a href="#/drafts/${d.id}"><strong>${esc(d.title)}</strong></a></td>
      <td>${d.matter_id ? `<a href="#/matters/${d.matter_id}">${esc(d.matter_title || "")}</a>` : "<span class='muted'>-</span>"}</td>
      <td>v${d.version}</td><td>${ago(d.updated_at)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No drafts yet.</div>`}</div>`;
};

Pages.draftNew = async function (query) {
  const [{ templates }, matters] = await Promise.all([api("GET", "/api/drafts/templates"), loadMattersForSelect()]);
  const matterId = query.get("matter") || "";
  const research = query.get("research") || "";
  const groups = {};
  templates.forEach(t => { (groups[t.category] = groups[t.category] || []).push(t); });
  setMain(`<div class="page-head"><div><div class="muted"><a href="#/drafts">Drafts</a> /</div><h1>New draft</h1>
      <p>Pick a template. Fields you leave empty stay as visible [[placeholders]]; nothing is made up.</p></div></div>
    <div class="card"><div class="grid grid-2"><div class="field"><label>Matter</label>
      <select id="dm">${options(matters.filter(m => m.my_access !== "view").map(m => [m.id, m.title]), matterId, { blank: "Not linked to a matter" })}</select></div></div></div>
    ${Object.entries(groups).map(([cat, list]) => `<h2>${esc(cat)}</h2><div class="grid grid-3">${list.map(t => `<div class="card">
      <h3>${esc(t.title)}</h3><p class="muted" style="font-size:.9rem">${esc(t.description)}</p>
      <button class="btn primary small" data-tpl="${t.key}">Use this template</button></div>`).join("")}</div>`).join("")}
    <h2>Other</h2><div class="card"><p>Start from a blank page${research ? " (the research you came from will be available to the AI assistant)" : ""}.</p>
      <button class="btn" data-tpl="">Blank draft</button></div>`);
  $$("[data-tpl]").forEach(b => b.addEventListener("click", () => withBusy(b, async () => {
    const body = { template_key: b.dataset.tpl || null, matter_id: $("#dm").value || null };
    if (!body.template_key) { body.content = ""; body.title = "Untitled draft"; }
    const r = await api("POST", "/api/drafts", body);
    navigate(`#/drafts/${r.id}${research ? "?research=" + research : ""}`);
  })));
};

function issuesHtml(check) {
  if (!check || !check.issues) return `<div class="empty">Run the check to see issues.</div>`;
  const head = check.ready ? `<div class="banner ok"><strong>No blocking issues</strong>Still read it through before filing or sending.</div>`
    : `<div class="banner bad"><strong>${check.blockers} blocking issue${check.blockers === 1 ? "" : "s"}</strong>Fix these before filing or sending.</div>`;
  return head + check.issues.map(i => `<div class="issue ${i.severity}"><strong>${esc(i.message)}</strong>${i.fix ? `<div>${esc(i.fix)}</div>` : ""}</div>`).join("")
    + (check.citations && check.citations.length ? `<h3 style="margin-top:12px">Citations found</h3>${check.citations.map(c => `<div class="list-item">
      <span class="mono">${esc(c.citation)}</span> ${c.found ? `→ ${esc(c.title)} ${statusBadge(c.legal_status)}` : badge("Not in library")}</div>`).join("")}` : "");
}

Pages.draft = async function (id, query) {
  setMain(loading());
  const [{ draft: d }, { templates }] = await Promise.all([api("GET", `/api/drafts/${id}`), api("GET", "/api/drafts/templates")]);
  const tpl = templates.find(t => t.key === d.template_key);
  const research = query.get("research") || "";
  let dirty = false;

  const fieldInput = f => {
    const v = d.fields[f.key];
    const val = Array.isArray(v) ? v.join("\n") : (v || "");
    const input = f.kind === "textarea" || f.kind === "list"
      ? `<textarea name="${f.key}" rows="${f.kind === "list" ? 4 : 3}" placeholder="${f.kind === "list" ? "One point per line" : ""}">${esc(val)}</textarea>`
      : `<input type="${f.kind === "date" ? "date" : "text"}" name="${f.key}" value="${esc(val)}">`;
    return `<div class="field"><label>${esc(f.label)}${f.required ? "" : " <small>(optional)</small>"}</label>${input}${f.help ? `<div class="hint">${esc(f.help)}</div>` : ""}</div>`;
  };

  setMain(`<div class="page-head"><div><div class="muted"><a href="#/drafts">Drafts</a>${d.matter_id ? ` / <a href="#/matters/${d.matter_id}">Matter</a>` : ""} /</div>
      <input id="title" value="${esc(d.title)}" style="font:600 1.4rem var(--serif);border:0;background:transparent;padding:0;width:100%">
      <p>${tpl ? esc(tpl.title) : "Free-form draft"} · version <span id="ver">${d.version}</span> · <span id="saved-at">saved ${ago(d.updated_at)}</span></p></div>
      <div class="row"><select id="versions" style="width:auto">${options([[d.version, `Current (v${d.version})`]].concat(d.versions.map(v => [v.version, `v${v.version} · ${fmtDate(v.created_at)}`])), d.version)}</select>
      <a class="btn" href="/api/drafts/${d.id}/download?format=docx">Word</a><a class="btn" href="/api/drafts/${d.id}/download">Text</a>
      <button class="btn danger" id="del">Delete</button><button class="btn primary" id="save">Save</button></div></div>
    <div class="grid grid-editor">
      <div><div class="tabs">${tpl ? `<button data-p="fields" class="active">Fields</button>` : ""}<button data-p="check" class="${tpl ? "" : "active"}">Pre-filing check</button>
        <button data-p="ai">AI assist</button></div>
        <div class="card" data-panel="fields" ${tpl ? "" : "hidden"}>${tpl ? `<form id="ff">${tpl.fields.map(fieldInput).join("")}
          <button class="btn primary" type="submit">Apply fields to the draft</button>
          <div class="hint">Rebuilds the text from the template. Your previous text is kept as an earlier version.</div></form>` : ""}</div>
        <div class="card" data-panel="check" ${tpl ? "hidden" : ""}><div class="row" style="margin-bottom:10px"><button class="btn small" id="runcheck">Run check</button>
          <small class="muted">Checks empty fields, cited cases against the library (overruled?), old vs new criminal codes, verification and other formalities.</small></div>
          <div id="issues">${issuesHtml(d.check_report)}</div></div>
        <div class="card" data-panel="ai" hidden><div id="ai"></div></div></div>
      <div class="card"><textarea id="content" class="editor" spellcheck="true">${esc(d.content)}</textarea></div></div>`);

  const content = $("#content");
  content.addEventListener("input", () => { dirty = true; });
  $("#title").addEventListener("input", () => { dirty = true; });
  window.onbeforeunload = () => (dirty ? true : undefined);
  window.addEventListener("hashchange", () => { window.onbeforeunload = null; }, { once: true });

  $$("[data-p]").forEach(b => b.addEventListener("click", () => {
    $$("[data-p]").forEach(x => x.classList.toggle("active", x === b));
    $$("[data-panel]").forEach(p => { p.hidden = p.dataset.panel !== b.dataset.p; });
  }));

  const save = async () => {
    const r = await api("PATCH", `/api/drafts/${d.id}`, { title: $("#title").value, content: content.value });
    dirty = false;
    $("#ver").textContent = r.version;
    $("#saved-at").textContent = "saved just now";
    return r;
  };
  $("#save").addEventListener("click", () => withBusy($("#save"), async () => { await save(); toast("Draft saved."); }));
  document.addEventListener("keydown", function onKey(e) {
    if (!document.body.contains(content)) { document.removeEventListener("keydown", onKey); return; }
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); $("#save").click(); }
  });

  if ($("#ff")) $("#ff").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#ff button"), async () => {
      if (dirty && !(await confirmDialog("Rebuilding from the template replaces the current text (it is kept as a version). Continue?"))) return;
      const fields = formObject(e.target);
      await api("PATCH", `/api/drafts/${d.id}`, { title: $("#title").value, fields, regenerate: true });
      dirty = false;
      Pages.draft(id, query);
    });
  });

  $("#runcheck").addEventListener("click", () => withBusy($("#runcheck"), async () => {
    const r = await api("POST", "/api/drafts/check", { content: content.value, template_key: d.template_key });
    $("#issues").innerHTML = issuesHtml(r.check);
  }));

  $("#versions").addEventListener("change", async e => {
    const v = Number(e.target.value);
    if (v === d.version) { content.value = d.content; content.readOnly = false; return; }
    const r = await api("GET", `/api/drafts/${d.id}/versions/${v}`);
    content.value = r.content;
    content.readOnly = true;
    toast(`Showing version ${v} (read-only). Copy what you need, or pick "Current" to return.`);
  });

  $("#del").addEventListener("click", async () => {
    if (await confirmDialog("Delete this draft and all its versions?", { danger: true, okLabel: "Delete" })) {
      try { await api("DELETE", `/api/drafts/${d.id}`); dirty = false; navigate("#/drafts"); } catch (e) { fail(e); }
    }
  });

  const aiEl = $("#ai");
  if (!State.status) State.status = await api("GET", "/api/system/status");
  if (!State.status.llm.configured) {
    aiEl.innerHTML = `<div class="banner info">AI assistance needs an LLM API key configured on the server.</div>`;
    return;
  }
  const sessions = (await api("GET", `/api/research?limit=50${d.matter_id ? "&matter_id=" + d.matter_id : ""}`)).items;
  aiEl.innerHTML = `<p class="muted">Ask for a specific section. The assistant only cites authorities from the research session you pick, and leaves [[placeholders]] for facts it does not have.</p>
    <form id="af"><div class="field"><label>What should it write?</label><textarea name="instruction" required
      placeholder="e.g. Draft grounds challenging the award for patent illegality, based on the research"></textarea></div>
    <div class="field"><label>Use authorities from research</label><select name="research_id">${options(sessions.map(s => [s.id, s.query.slice(0, 90)]), research, { blank: "None (no case law will be cited)" })}</select></div>
    <label class="check"><input type="checkbox" name="with_text" data-bool checked> Let it read the current draft for context</label>
    <button class="btn primary" style="margin-top:10px">Generate</button></form><div id="ai-out" style="margin-top:12px"></div>`;
  $("#af").addEventListener("submit", e => {
    e.preventDefault();
    const f = formObject(e.target);
    withBusy($("#af button"), async () => {
      const r = await api("POST", "/api/drafts/assist", { instruction: f.instruction, research_id: f.research_id || null,
        matter_id: d.matter_id, current_text: f.with_text ? content.value : null });
      $("#ai-out").innerHTML = `<div class="doc-text" style="max-height:340px">${esc(r.text)}</div>
        ${r.check.issues.length ? `<div style="margin-top:8px">${r.check.issues.map(i => `<div class="issue ${i.severity}">${esc(i.message)}</div>`).join("")}</div>` : ""}
        <div class="row" style="margin-top:8px"><button class="btn small primary" id="ins">Insert at cursor</button>
        <button class="btn small" id="cp">Copy</button><small class="muted">${esc(r.model)}</small></div>`;
      $("#ins").addEventListener("click", () => {
        const pos = content.selectionStart || content.value.length;
        content.value = content.value.slice(0, pos) + "\n" + r.text + "\n" + content.value.slice(pos);
        dirty = true;
        toast("Inserted. Review it, then save.");
      });
      $("#cp").addEventListener("click", () => navigator.clipboard.writeText(r.text).then(() => toast("Copied.")));
    });
  });
};
