/* Research, results, source viewer and library. */
"use strict";

function aiOffHint() {
  return State.user && State.user.is_platform_admin
    ? `<div><strong>To get written answers:</strong> open <a href="#/admin?tab=ai">Corpus admin → AI settings</a>, paste an API key (Google Gemini has a free tier), click Test, then Save.</div>`
    : `<div>Written AI answers are switched off on this server. Ask your LawPilot administrator to add an AI key.</div>`;
}

function corpusNotice(status) {
  if (!status) return "";
  const c = status.corpus;
  return `<div class="banner info"><strong>What LawPilot searches</strong>
    Its own indexed library: ${c.total} source${c.total === 1 ? "" : "s"}, of which ${c.primary} ${c.primary === 1 ? "is" : "are"} primary text
    (judgments, Acts) and the rest summaries or secondary material. It does not contain all Indian law. If nothing turns up,
    that tells you about the library, not that no authority exists.
    </div>${status.llm.configured ? "" : `<div class="banner warn"><strong>AI answers are off</strong>You will see ranked passages only.${aiOffHint()}</div>`}`;
}

Pages.research = async function (query) {
  const prefs = State.user.prefs || {};
  const matters = await loadMattersForSelect();
  if (!State.status) State.status = await api("GET", "/api/system/status");
  const preMatter = query.get("matter") || "";
  setMain(`<div class="page-head"><div><h1>Legal research</h1><p>Ask in plain language. Every answer is tied to passages you can open and check.</p></div>
      <a class="btn" href="#/library?tab=history">Past research</a></div>
    ${corpusNotice(State.status)}
    <div class="card ask"><form id="rf">
      <div class="field"><label for="q">Your question</label>
        <textarea id="q" name="query" required placeholder="e.g. Can anticipatory bail be granted for an offence under Section 498A IPC after the chargesheet is filed?">${esc(query.get("q") || "")}</textarea></div>
      <div class="row">
        <div class="mode-toggle" role="group" aria-label="Research mode">
          <button type="button" data-mode="quick" class="${prefs.research_mode !== "deep" ? "active" : ""}">Quick</button>
          <button type="button" data-mode="deep" class="${prefs.research_mode === "deep" ? "active" : ""}">Deep</button></div>
        <small class="muted" id="mode-help"></small>
        <span class="spacer"></span>
        ${can("research.run") ? `<button class="btn primary" id="go">Research</button>` : `<span class="muted">Your role cannot run research.</span>`}</div>
      <div class="grid grid-2" style="margin-top:12px">
        ${State.user.is_platform_admin ? `<div class="field"><label>Scope</label><div class="hint" style="margin-top:8px">Administrator test mode:
          searches the shared library only (administrators have no matters).</div></div>`
        : `<div class="field"><label>Matter (optional)</label><select name="matter_id">${options(matters.map(m => [m.id, m.title]), preMatter, { blank: "No matter: search the library only" })}</select>
          <div class="hint">Choosing a matter also searches that matter's own documents, and saves the research to it.</div></div>`}
        <div class="field"><label>Jurisdiction preference</label><input name="jurisdiction" value="${esc(prefs.default_jurisdiction || "")}" placeholder="e.g. Delhi (optional)">
          <div class="hint">Limits High Court results to this state; Supreme Court results always stay in.</div></div></div>
      <details><summary>Filters</summary><div class="grid grid-3" style="margin-top:10px">
        <div class="field"><label>Courts</label>${[["supreme", "Supreme Court"], ["high", "High Courts"], ["tribunal", "Tribunals"], ["district", "Lower courts"]]
          .map(([v, l]) => `<label class="check"><input type="checkbox" name="court_levels" value="${v}"> ${l}</label>`).join("")}</div>
        <div class="field"><label>Source types</label>${[["judgment", "Judgments"], ["statute", "Acts"], ["constitution", "Constitution"], ["rule", "Rules"],
          ["notification", "Notifications"], ["editorial_summary", "Case summaries"], ["commentary", "Commentary"]]
          .map(([v, l]) => `<label class="check"><input type="checkbox" name="kinds" value="${v}"> ${l}</label>`).join("")}</div>
        <div><div class="field"><label>Court name contains</label><input name="court" placeholder="e.g. Bombay"></div>
          <div class="row"><div class="field" style="flex:1"><label>From year</label><input type="number" name="year_from" min="1900" max="2100"></div>
          <div class="field" style="flex:1"><label>To year</label><input type="number" name="year_to" min="1900" max="2100"></div></div>
          <label class="check"><input type="checkbox" name="exclude_overruled" data-bool ${prefs.exclude_overruled ? "checked" : ""}> Hide overruled / repealed material</label></div>
      </div></details></form></div>
    <div id="result"></div>`);

  let mode = prefs.research_mode === "deep" ? "deep" : "quick";
  const help = { quick: "One focused search. Seconds.", deep: "Splits the question into sub-issues, searches each, and double-checks every claim. Slower." };
  const setMode = m => { mode = m; $$("[data-mode]").forEach(b => b.classList.toggle("active", b.dataset.mode === m)); $("#mode-help").textContent = help[m]; };
  setMode(mode);
  $$("[data-mode]").forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));

  $("#rf").addEventListener("submit", e => {
    e.preventDefault();
    const f = formObject(e.target);
    const filters = { court_levels: [].concat(f.court_levels || []), kinds: [].concat(f.kinds || []), court: f.court,
      jurisdiction: f.jurisdiction, year_from: f.year_from, year_to: f.year_to, exclude_overruled: f.exclude_overruled };
    $("#result").innerHTML = `<div class="card">${loading(mode === "deep" ? "Researching each issue and checking every claim. This can take a minute..." : "Searching the library...")}</div>`;
    withBusy($("#go"), async () => {
      const r = await api("POST", "/api/research", { query: f.query, mode, matter_id: f.matter_id || null, filters });
      history.replaceState(null, "", `#/research/${r.session.id}`);
      renderSession($("#result"), r.session);
    }).then(() => { if ($("#result").querySelector(".loading")) $("#result").innerHTML = ""; });
  });
};

Pages.researchSession = async function (id) {
  setMain(loading());
  const { session } = await api("GET", `/api/research/${id}`);
  setMain(`<div class="page-head"><div><div class="muted"><a href="#/research">Research</a> / <a href="#/library?tab=history">History</a></div>
    <h1>${esc(session.query.length > 140 ? session.query.slice(0, 140) + "..." : session.query)}</h1>
    <p>${fmtDateTime(session.created_at)} · ${session.mode} research${session.matter_id ? ` · <a href="#/matters/${session.matter_id}">matter</a>` : ""}</p></div>
    <a class="btn" href="#/research?q=${encodeURIComponent(session.query)}${session.matter_id ? "&matter=" + session.matter_id : ""}">Ask again / refine</a></div>
    <div id="result"></div>`);
  renderSession($("#result"), session);
};

const POINT_TYPE = { verified: ["Stated in source", "ok"], inference: ["Inference", "info"], assumption: ["Assumption", "warn"] };
const CHECK = { supported: ["Supported", "ok"], partial: ["Partly supported", "warn"], unsupported: ["Not supported", "bad"], assumption: ["Assumption", "warn"] };

function claimFor(v, section, index) {
  return v && v.claims ? v.claims.find(c => c.section === section && c.index === index) : null;
}
function checkBadge(claim) {
  if (!claim) return "";
  const [t, k] = CHECK[claim.status] || [claim.status, ""];
  return `<span class="badge ${k}" title="${esc(claim.reason || "")}">${t}</span>`;
}
function evRefs(ids) { return (ids || []).map(id => `<button class="cite" data-cite="${esc(id)}">${esc(id)}</button>`).join(""); }

function renderSession(el, s) {
  const a = s.answer || {};
  const v = s.verification || {};
  const an = s.analysis || {};
  const notes = an.notes || [];
  const outcome = LABELS.outcome[s.outcome] || [s.outcome, "info"];
  const bannerKind = { ok: "ok", warn: "warn", bad: "bad", info: "info" }[outcome[1]] || "info";
  const supporting = s.evidence.filter(e => e.role !== "contrary_candidate");
  const contrary = s.evidence.filter(e => e.role === "contrary_candidate");

  let verifyLine = "";
  if (v.counts) {
    const c = v.counts;
    verifyLine = v.all_supported
      ? `Every material claim was matched to the cited passages (${c.supported}).`
      : `${c.supported} claim(s) supported, ${c.partial} partly supported, ${c.unsupported} not supported by the cited passages. Check the flagged points before relying on them.`;
  }

  const section = (title, body) => body ? `<div class="answer-section"><h3>${title}</h3>${body}</div>` : "";
  const list = items => items && items.length ? `<ul>${items.map(i => `<li>${citeify(i)}</li>`).join("")}</ul>` : "";

  const shortClaims = (v.claims || []).filter(c => c.section === "short_answer");
  const shortFlag = shortClaims.some(c => c.status === "unsupported") ? badge("Contains unsupported statements", "bad")
    : shortClaims.some(c => c.status === "partial") ? badge("Partly supported", "warn") : shortClaims.length ? badge("Supported", "ok") : "";

  el.innerHTML = `
    <div class="banner ${bannerKind}"><strong>${esc(outcome[0])}</strong>
      ${notes.map(n => `<div>${esc(n)}</div>`).join("")}${verifyLine ? `<div>${esc(verifyLine)}</div>` : ""}
      ${s.outcome === "evidence_only" && State.status && !State.status.llm.configured ? aiOffHint() : ""}</div>

    ${s.outcome === "answered" || s.outcome === "insufficient_evidence" ? `<div class="card">
      ${section(`Answer ${shortFlag}`, a.short_answer ? `<div class="short-answer">${citeify(a.short_answer)}</div>` : "")}
      ${section("Issues", list(a.issues))}
      ${section("Applicable law", (a.applicable_law || []).map((p, i) => `<div class="point"><div class="tags">${checkBadge(claimFor(v, "applicable_law", i))}</div>
        <div>${citeify(p.text)} ${evRefs(p.evidence)}</div></div>`).join(""))}
      ${section("Authorities", (a.authorities || []).map((p, i) => {
        const ev = s.evidence.find(e => e.id === p.evidence);
        return `<div class="point"><div class="tags">${badge(p.treatment === "contrary" ? "Contrary" : p.treatment === "context" ? "Context" : "Supports",
          p.treatment === "contrary" ? "warn" : p.treatment === "context" ? "" : "ok")}${checkBadge(claimFor(v, "authorities", i))}</div>
          <div><strong>${ev ? esc(ev.title) : esc(p.evidence)}</strong> ${ev && ev.citation ? `<span class="muted">${esc(ev.citation)}</span>` : ""} ${evRefs([p.evidence])}
          ${ev ? " " + statusBadge(ev.legal_status) : ""}<div>${citeify(p.proposition)}</div></div></div>`;
      }).join(""))}
      ${section("Analysis", (a.analysis || []).map((p, i) => {
        const [t, k] = POINT_TYPE[p.type] || POINT_TYPE.inference;
        return `<div class="point"><div class="tags">${badge(t, k)}${p.type !== "assumption" ? checkBadge(claimFor(v, "analysis", i)) : ""}</div>
          <div>${citeify(p.text)} ${evRefs(p.evidence)}</div></div>`;
      }).join(""))}
      ${section("Contrary authorities", (a.contrary_authorities || []).map(p => `<div class="point"><div class="tags">${badge("Contrary", "warn")}</div>
        <div>${evRefs([p.evidence])} ${citeify(p.note)}</div></div>`).join("") || (a.short_answer ? `<p class="muted">None identified in the retrieved material.</p>` : ""))}
      ${section("Assumptions", list(a.assumptions))}
      ${section("Open questions / further research", list(a.unresolved_questions))}
      ${section("Limitations", list(a.limitations))}
      <div class="row end"><small class="muted">${esc(s.llm_model || "")} · ${s.duration_ms ? (s.duration_ms / 1000).toFixed(1) + "s" : ""}</small></div>
    </div>` : ""}

    <div class="card"><div class="card-head"><h2>Evidence (${s.evidence.length})</h2>
      <div class="row"><button class="btn small" id="save-r">Save research${s.matter_id ? "" : " to a matter"}</button>
      ${can("draft.create") ? `<a class="btn small" href="#/drafts/new?research=${s.id}${s.matter_id ? "&matter=" + s.matter_id : ""}">Use in a draft</a>` : ""}</div></div>
      ${supporting.length ? supporting.map(evidenceCard).join("") : `<div class="empty">No passages retrieved.</div>`}
      ${contrary.length ? `<h3 style="margin-top:16px">Possible contrary or related authority</h3>
        <p class="muted">Found because they overrule, are overruled by, or distinguish the results above, or use contrary-treatment language.</p>
        ${contrary.map(evidenceCard).join("")}` : ""}</div>

    <details class="card"><summary>How LawPilot read your question</summary><div style="margin-top:10px">${analysisTable(an, s)}</div></details>`;

  $$("[data-cite]", el).forEach(b => b.addEventListener("click", () => {
    const card = $(`#ev-${b.dataset.cite}`, el);
    if (card) { card.scrollIntoView({ behavior: "smooth", block: "center" }); card.classList.add("flash"); setTimeout(() => card.classList.remove("flash"), 1600); }
  }));
  $$("[data-full]", el).forEach(b => b.addEventListener("click", () => {
    const ev = s.evidence.find(e => e.id === b.dataset.full);
    const p = $(`#ev-${ev.id} .passage`, el);
    const full = p.dataset.full === "1";
    p.textContent = full ? ev.excerpt : ev.text;
    p.dataset.full = full ? "0" : "1";
    b.textContent = full ? "Show full passage" : "Show excerpt";
  }));
  $$("[data-save-src]", el).forEach(b => b.addEventListener("click", () => saveAuthorityModal(b.dataset.saveSrc, b.dataset.chunk, s.matter_id)));
  $("#save-r", el).addEventListener("click", () => saveResearchModal(s));
}

function evidenceCard(e) {
  const flags = [];
  if (e.from_matter) flags.push(badge("Matter document", "info"));
  if (e.kind && !e.from_matter) flags.push(badge(kindLabel(e.kind)));
  if (!e.from_matter) flags.push(statusBadge(e.legal_status));
  if (e.verification === "editorial_summary") flags.push(badge("Summary, not judgment text", "warn"));
  if ((e.security_flags || []).length) flags.push(badge("Instruction-like text ignored", "warn"));
  return `<div class="evidence ${e.role === "contrary_candidate" ? "contrary" : ""}" id="ev-${esc(e.id)}">
    <div class="row"><span class="eid">${esc(e.id)}</span><a href="#/sources/${e.source_id}?chunk=${encodeURIComponent(e.chunk_id)}&v=${e.version}"><strong>${esc(e.title)}</strong></a>
      <span class="spacer"></span>${flags.join(" ")}</div>
    <div class="meta">${esc([e.citation, e.court, e.date ? fmtDate(e.date) : "", e.pinpoint].filter(Boolean).join(" · "))}
      ${e.status_note ? `<div>${esc(e.status_note)}</div>` : ""}</div>
    <div class="passage" data-full="0">${esc(e.excerpt || e.text)}</div>
    <div class="row" style="margin-top:6px"><small class="muted" title="${esc((e.reasons || []).join(" · "))}">${esc((e.reasons || []).slice(0, 3).join(" · "))}</small>
      <span class="spacer"></span>${e.excerpt && e.excerpt !== e.text ? `<button class="btn small link" data-full="${esc(e.id)}">Show full passage</button>` : ""}
      ${e.source_url ? `<a class="btn small" href="${esc(e.source_url)}" target="_blank" rel="noopener noreferrer">Official / original source</a>` : ""}
      <a class="btn small" href="#/sources/${e.source_id}?chunk=${encodeURIComponent(e.chunk_id)}&v=${e.version}">Open</a>
      <button class="btn small" data-save-src="${e.source_id}" data-chunk="${esc(e.chunk_id)}">Save</button></div></div>`;
}

function analysisTable(an, s) {
  const rows = [
    ["Legal issue", an.legal_issue], ["Search type", an.search_type], ["Statutes", (an.statutes || []).join("; ")],
    ["Sections", (an.sections || []).join(", ")], ["Articles", (an.articles || []).join(", ")],
    ["Citations", (an.citations || []).join("; ")], ["Parties", (an.parties || []).join(" v. ")],
    ["Courts mentioned", (an.courts || []).join(", ")], ["Judges", (an.judges || []).join(", ")],
    ["Time period", an.year_from || an.year_to ? `${an.year_from || "..."} to ${an.year_to || "..."}` : ""],
    ["Legal domain", (an.domains || []).join(", ")],
    ["Old/new code", (an.code_correspondence || []).map(c => `${c.from} → ${c.to}`).join("; ")],
    ["Sub-questions", (an.sub_questions || []).join(" | ")],
    ["Search", an.retrieval ? `${an.retrieval.keyword_hits} keyword hits, ${an.retrieval.vector_hits} semantic hits` : ""],
    ["Embedding model", s.embedding_model], ["Corpus at the time", s.corpus_snapshot ? `${s.corpus_snapshot.total} sources (index v${s.corpus_snapshot.index_generation})` : ""],
  ].filter(([, v]) => v);
  return `<dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    ${(an.code_correspondence || []).length ? `<p class="muted" style="margin-top:8px">Old/new criminal code correspondence is a research aid. Confirm against the bare Act.</p>` : ""}`;
}

async function saveResearchModal(s) {
  const matters = await loadMattersForSelect();
  const md = modal(`<h2>Save research</h2><form id="sv">
    <div class="field"><label>Matter</label><select name="matter_id">${options(matters.filter(m => m.my_access !== "view").map(m => [m.id, m.title]), s.matter_id, { blank: "Just save to my library" })}</select></div>
    <div class="field"><label>Note</label><textarea name="note" placeholder="Why this matters (optional)"></textarea></div>
    <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`);
  $("#sv", md.el).addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#sv .primary", md.el), async () => { await api("POST", `/api/research/${s.id}/save`, formObject(e.target)); md.close(); toast("Saved."); });
  });
}

async function saveAuthorityModal(sourceId, chunkId, matterId) {
  const matters = await loadMattersForSelect();
  const md = modal(`<h2>Save authority</h2><form id="sa">
    <div class="field"><label>Matter</label><select name="matter_id">${options(matters.filter(m => m.my_access !== "view").map(m => [m.id, m.title]), matterId, { blank: "Just save to my library" })}</select></div>
    <div class="field"><label>Note</label><textarea name="note" placeholder="Proposition it supports, paragraph to quote... (optional)"></textarea></div>
    <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`);
  $("#sa", md.el).addEventListener("submit", e => {
    e.preventDefault();
    const data = { ...formObject(e.target), source_id: sourceId, chunk_id: chunkId || null };
    withBusy($("#sa .primary", md.el), async () => { await api("POST", "/api/saved", data); md.close(); toast("Authority saved."); });
  });
}

/* ── Source viewer ─────────────────────────────────────── */
Pages.source = async function (id, query) {
  setMain(loading());
  const v = query.get("v");
  const { source: s } = await api("GET", `/api/sources/${id}${v ? "?version=" + encodeURIComponent(v) : ""}`);
  const chunk = query.get("chunk");
  const isOld = s.version_info.version !== s.current_version;
  const meta = [["Type", kindLabel(s.kind)], ["Citation", s.citation], ["Neutral citation", s.neutral_citation],
    ["Other citations", (s.alt_citations || []).join("; ")], ["Court", s.court], ["Bench", s.bench], ["Judges", s.judges],
    ["Decided", s.decision_date ? fmtDate(s.decision_date) : ""], ["Act", s.act_name], ["Case number", s.case_number],
    ["Jurisdiction", s.jurisdiction], ["Source", s.origin === "seed" ? "LawPilot starter summaries" : s.origin.replace("_", " ")],
    ["Retrieved", s.retrieved_at ? fmtDateTime(s.retrieved_at) : ""], ["Verified against official source", s.verified_at ? fmtDateTime(s.verified_at) : "Not yet"],
    ["Version", `${s.version_info.version} of ${s.current_version} (hash ${s.version_info.sha256.slice(0, 12)})`],
    ["Source note", s.version_info.note]].filter(([, x]) => x);
  setMain(`<div class="page-head"><div><div class="muted"><button class="btn link" id="back">← Back</button></div>
      <h1>${esc(s.title)}</h1><div class="row">${s.matter_id ? badge("Matter document", "info") : statusBadge(s.legal_status) + " " + verificationBadge(s.verification)}</div></div>
      <div class="row">${s.source_url ? `<a class="btn" href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">Original source ↗</a>` : ""}
      ${s.has_file ? `<a class="btn" href="/api/sources/${s.id}/file?version=${s.version_info.version}">Download file</a>` : ""}
      <button class="btn primary" id="save">Save</button></div></div>
    ${isOld ? `<div class="banner warn"><strong>Older version</strong>You are viewing version ${s.version_info.version}, which was used by an earlier research session. <a href="#/sources/${s.id}">Open current version</a>.</div>` : ""}
    ${s.verification === "editorial_summary" ? `<div class="banner warn"><strong>Editorial summary</strong>This is a summary, not the text of the authority. Read the judgment or Act itself before citing it.</div>` : ""}
    ${s.status_note ? `<div class="banner ${s.legal_status === "overruled" ? "bad" : "info"}">${esc(s.status_note)}</div>` : ""}
    ${(s.security_flags || []).length ? `<div class="banner warn">This document contains text that looks like instructions to an AI (${esc(s.security_flags.join(", "))}). LawPilot treats it as plain data.</div>` : ""}
    <div class="grid grid-main-side">
      <div class="card"><div class="tabs"><button class="active" data-v="passages">Passages</button><button data-v="full">Full text</button></div>
        <div id="sv-body"></div></div>
      <div><div class="card"><h3>Details</h3><dl class="kv" style="grid-template-columns:1fr">${meta.map(([k, x]) => `<dt>${esc(k)}</dt><dd>${esc(x)}</dd>`).join("")}</dl></div>
        ${s.relations.length ? `<div class="card"><h3>Related authorities</h3>${s.relations.map(r => `<div class="list-item">
          <div class="muted">${esc(r.direction === "outgoing" ? `This ${r.relation.replace("_", " ")}` : `${r.relation.replace("_", " ")} this`)}</div>
          <a href="#/sources/${r.other_id}">${esc(r.other_title)}</a> <small class="muted">${esc(r.other_citation || "")}</small>
          ${r.note ? `<div style="font-size:.88rem">${esc(r.note)}</div>` : ""}</div>`).join("")}</div>` : ""}
        ${s.versions.length > 1 ? `<div class="card"><h3>Versions</h3>${s.versions.map(x => `<div class="list-item">
          <a href="#/sources/${s.id}?v=${x.version}">Version ${x.version}</a> <small class="muted">${fmtDateTime(x.created_at)}</small>
          ${x.note ? `<div style="font-size:.88rem">${esc(x.note)}</div>` : ""}</div>`).join("")}</div>` : ""}</div></div>`);
  const body = $("#sv-body");
  const showPassages = () => {
    body.innerHTML = s.passages.length ? s.passages.map(p => `<div class="passage-block ${p.id === chunk ? "hl" : ""}" id="p-${p.idx}">
      <div class="muted" style="font-size:.8rem">${esc([p.section_ref, p.page ? "p. " + p.page : "", p.para_ref, p.heading].filter(Boolean).join(" · ") || "Passage " + (p.idx + 1))}</div>
      <div style="font-family:var(--serif);white-space:pre-wrap">${esc(p.text)}</div></div>`).join("")
      : `<div class="doc-text">${esc(s.text)}</div>`;
    const hl = $(".passage-block.hl", body);
    if (hl) setTimeout(() => hl.scrollIntoView({ block: "center" }), 50);
  };
  $$("[data-v]").forEach(b => b.addEventListener("click", () => {
    $$("[data-v]").forEach(x => x.classList.toggle("active", x === b));
    if (b.dataset.v === "full") body.innerHTML = `<div class="doc-text">${esc(s.text)}</div>`; else showPassages();
  }));
  showPassages();
  $("#back").addEventListener("click", () => history.back());
  $("#save").addEventListener("click", () => saveAuthorityModal(s.id, chunk, s.matter_id));
};

/* ── Library: search the corpus, saved items, research history ─ */
Pages.library = async function (query) {
  const tab = query.get("tab") || "search";
  setMain(`<div class="page-head"><div><h1>Library</h1><p>Look up judgments and Acts directly, and revisit what you saved.</p></div></div>
    <div class="tabs">${[["search", "Search the library"], ["saved", "Saved"], ["history", "Research history"], ["coverage", "What's in the library"]]
      .map(([k, l]) => `<button data-tab="${k}" class="${tab === k ? "active" : ""}">${l}</button>`).join("")}</div><div id="tab"></div>`);
  $$("[data-tab]").forEach(b => b.addEventListener("click", () => navigate(`#/library?tab=${b.dataset.tab}`)));
  const el = $("#tab");
  if (tab === "search") return librarySearch(el, query);
  if (tab === "saved") return librarySaved(el);
  if (tab === "history") return libraryHistory(el, query);
  return libraryCoverage(el);
};

function librarySearch(el, query) {
  const g = k => esc(query.get(k) || "");
  el.innerHTML = `<div class="card"><form id="ls">
    <div class="field"><label>Words, phrase or citation</label><input type="search" name="q" value="${g("q")}" placeholder="e.g. default bail, (2017) 10 SCC 1, Section 138"></div>
    <div class="grid grid-4">
      <div class="field"><label>Case name / party</label><input name="title" value="${g("title")}" placeholder="e.g. Puttaswamy"></div>
      <div class="field"><label>Citation / case no.</label><input name="citation" value="${g("citation")}" placeholder="e.g. AIR 1978 SC 597"></div>
      <div class="field"><label>Judge</label><input name="judge" value="${g("judge")}"></div>
      <div class="field"><label>Act</label><input name="act" value="${g("act")}" placeholder="e.g. Arbitration"></div>
      <div class="field"><label>Court</label><input name="court" value="${g("court")}" placeholder="e.g. Madras"></div>
      <div class="field"><label>Court level</label><select name="court_level">${options({ supreme: "Supreme Court", high: "High Court", tribunal: "Tribunal", district: "Lower court" }, query.get("court_level"), { blank: "Any" })}</select></div>
      <div class="field"><label>From year</label><input type="number" name="year_from" value="${g("year_from")}"></div>
      <div class="field"><label>To year</label><input type="number" name="year_to" value="${g("year_to")}"></div>
    </div><div class="row"><select name="kind" style="width:auto">${options(LABELS.kind, query.get("kind"), { blank: "Any type" })}</select>
      <span class="spacer"></span><button class="btn primary">Search</button></div></form></div><div id="lr"></div>`;
  $("#ls").addEventListener("submit", e => {
    e.preventDefault();
    const p = new URLSearchParams();
    Object.entries(formObject(e.target)).forEach(([k, v]) => { if (v && k !== "user_document") p.set(k, v); });
    p.set("tab", "search");
    navigate(`#/library?${p.toString()}`);
  });
  const params = new URLSearchParams(query);
  params.delete("tab");
  if (![...params.values()].some(Boolean)) {
    $("#lr").innerHTML = `<div class="empty">Search by any combination of fields. For questions in plain language, use <a href="#/research">Research</a>.</div>`;
    return;
  }
  $("#lr").innerHTML = loading("Searching...");
  api("GET", `/api/corpus/search?${params.toString()}&limit=50`).then(r => {
    $("#lr").innerHTML = `<div class="card"><div class="muted" style="margin-bottom:6px">${r.total} result${r.total === 1 ? "" : "s"}${r.total > 50 ? " (showing 50)" : ""}</div>
      ${r.items.length ? r.items.map(x => `<div class="list-item"><a class="title" href="#/sources/${x.id}">${esc(x.title)}</a>
        <div class="row" style="margin-top:2px">${badge(kindLabel(x.kind))} ${statusBadge(x.legal_status)} ${x.verification === "editorial_summary" ? badge("Summary", "warn") : ""}
        <small class="muted">${esc([x.citation, x.court, x.decision_date ? fmtDate(x.decision_date) : "", x.act_name].filter(Boolean).join(" · "))}</small></div></div>`).join("")
        : `<div class="empty">No matching sources in the library.</div>`}</div>`;
  }).catch(fail);
}

async function librarySaved(el) {
  el.innerHTML = loading();
  const r = await api("GET", "/api/saved");
  el.innerHTML = `<div class="card">${r.items.length ? r.items.map(s => `<div class="list-item row"><div style="flex:1">
    ${s.kind === "authority" ? `<a class="title" href="#/sources/${s.source_id}">${esc(s.source_title)}</a> ${statusBadge(s.legal_status)}
      <div class="muted">${esc(s.citation || "")}</div>`
      : `<a class="title" href="#/research/${s.research_id}">${esc((s.research_query || "").slice(0, 140))}</a> ${outcomeBadge(s.research_outcome)}`}
    <div class="muted">${s.matter_title ? "Matter: " + esc(s.matter_title) + " · " : ""}saved ${ago(s.created_at)}</div>
    ${s.note ? `<div style="font-size:.9rem">${esc(s.note)}</div>` : ""}</div>
    <button class="btn small danger" data-del="${s.id}">Remove</button></div>`).join("") : `<div class="empty">Nothing saved yet. Use "Save" on research results or sources.</div>`}</div>`;
  $$("[data-del]", el).forEach(b => b.addEventListener("click", async () => {
    try { await api("DELETE", `/api/saved/${b.dataset.del}`); librarySaved(el); } catch (e) { fail(e); }
  }));
}

async function libraryHistory(el, query) {
  const q = query.get("q") || "";
  el.innerHTML = `<div class="card"><form id="hs" class="row"><input type="search" name="q" value="${esc(q)}" placeholder="Search past questions" style="flex:1">
    <button class="btn">Search</button></form></div><div id="hl">${loading()}</div>`;
  $("#hs").addEventListener("submit", e => { e.preventDefault(); navigate(`#/library?tab=history&q=${encodeURIComponent(e.target.q.value)}`); });
  const r = await api("GET", `/api/research?q=${encodeURIComponent(q)}&limit=100`);
  $("#hl").innerHTML = `<div class="card">${r.items.length ? r.items.map(x => `<div class="list-item row"><div style="flex:1">
    <a href="#/research/${x.id}">${esc(x.query.slice(0, 160))}</a>
    <div class="row">${outcomeBadge(x.outcome)}<small class="muted">${fmtDateTime(x.created_at)} · ${x.mode}</small></div></div>
    <button class="btn small danger" data-del="${x.id}">Delete</button></div>`).join("") : `<div class="empty">No research found.</div>`}</div>`;
  $$("[data-del]", el).forEach(b => b.addEventListener("click", async () => {
    if (await confirmDialog("Delete this research session?", { danger: true, okLabel: "Delete" })) {
      try { await api("DELETE", `/api/research/${b.dataset.del}`); libraryHistory(el, query); } catch (e) { fail(e); }
    }
  }));
}

async function libraryCoverage(el) {
  el.innerHTML = loading();
  const c = await api("GET", "/api/corpus/coverage");
  const tbl = (title, obj, labels) => `<div class="card"><h3>${title}</h3><table><tbody>${Object.entries(obj).map(([k, n]) =>
    `<tr><td>${esc((labels && (Array.isArray(labels[k]) ? labels[k][0] : labels[k])) || k)}</td><td style="text-align:right">${n}</td></tr>`).join("")}</tbody></table></div>`;
  el.innerHTML = `<div class="banner info"><strong>${c.total} sources in the library</strong>
      Decisions from ${esc(c.year_range[0] || "?")} to ${esc(c.year_range[1] || "?")}. Last updated ${c.last_updated ? fmtDateTime(c.last_updated) : "never"}.
      Coverage is partial: LawPilot only knows what has been added to it.</div>
    <div class="grid grid-2">${tbl("By type", c.by_kind, LABELS.kind)}${tbl("By court", c.by_court_level, LABELS.court_level)}
    ${tbl("By authority class", c.by_class, { primary: "Primary (judgments, Acts)", official: "Official explanatory", secondary: "Secondary / summaries", user_upload: "Uploaded" })}
    ${tbl("By verification", c.by_verification, LABELS.verification)}</div>`;
}
