/* AI mode: a chat assistant for legal work, with chat history, file uploads and cited answers. */
"use strict";

const AI = {
  chats: [], chat: null, messages: [], files: [], pending: [], busy: false, search: "",
  draft: { use_library: true, matter_id: "" }, matters: null,
};
const AI_ACCEPT = ".pdf,.docx,.txt,.md,.html,.htm";
const AI_ICONS = {
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>',
  clip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
  redo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  dots: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>',
  scale: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v18M7 21h10M5 7h14"/><path d="M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z"/></svg>',
  bands: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M2.8 4.2L12 8.9l9.2-4.7-1.5 3.9L12 12 4.3 8.1z"/><path d="M8.9 11.6h2.5v8.8c0 .5-.4.9-.9.9H8c-.6 0-1-.5-.9-1z"/><path d="M12.6 11.6h2.5l1.8 8.7c.1.5-.3 1-.9 1h-2.5c-.5 0-.9-.4-.9-.9z"/></svg>',  // the advocate's collar bands
  side: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};

const AI_SUGGESTIONS = [
  { title: "Summarise a judgment", sub: "Facts, issues, ratio and final order", attach: true,
    prompt: "Summarise this judgment: facts, issues, arguments, ratio decidendi and the final order." },
  { title: "Review a contract", sub: "Spot risky clauses for your client", attach: true,
    prompt: "Review this agreement for my client and list the risky or one-sided clauses with suggested changes." },
  { title: "Anticipatory bail", sub: "Attempt to murder — BNSS and CrPC", prompt:
    "Can anticipatory bail be granted for attempt to murder? Give the relevant BNSS and CrPC provisions and the BNS / IPC sections." },
  { title: "Old vs new criminal codes", sub: "IPC 420 → BNS", prompt:
    "What is the BNS equivalent of IPC Section 420 and what changed in the new provision?" },
  { title: "Cheque bounce notice", sub: "Section 138 NI Act essentials", prompt:
    "What must a legal notice under Section 138 of the Negotiable Instruments Act contain, and what are the timelines?" },
  { title: "Right to privacy", sub: "Article 21 and key holdings", prompt:
    "Explain the right to privacy under Article 21 of the Constitution and the leading Supreme Court holdings." },
];

/* ── Markdown (escape first, then a small safe subset) ───────────── */
function mdInline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\s][^_]*?)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/\[(S\d+)\]/g, (_, id) => `<button type="button" class="ai-cite" data-cite="${id}" title="Source ${id.slice(1)}">${id.slice(1)}</button>`);
}

const MD_LIST = /^(\s*)([-*+•]|\d+[.)])\s+(.*)$/;
const MD_BLOCK = /^(#{1,6}\s|```|\s*>|\s*([-*+•]|\d+[.)])\s+|\s*\|.*\|\s*$|\s*([-*_])\3{2,}\s*$)/;

function mdRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
}

function renderMarkdown(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let html = "";
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if (/^\s*```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      html += `<pre><code>${esc(buf.join("\n"))}</code></pre>`;
      continue;
    }
    if (!line.trim()) { i++; continue; }
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      const lvl = Math.min(5, m[1].length + 2);
      html += `<h${lvl}>${mdInline(esc(m[2].replace(/#+\s*$/, "")))}</h${lvl}>`;
      i++;
      continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { html += "<hr>"; i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = mdRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(mdRow(lines[i++]));
      html += `<div class="ai-table-wrap"><table><thead><tr>${head.map(c => `<th>${mdInline(esc(c))}</th>`).join("")}</tr></thead>
        <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${mdInline(esc(c))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      html += `<blockquote>${renderMarkdown(buf.join("\n"))}</blockquote>`;
      continue;
    }
    if (MD_LIST.test(line)) {
      const stack = [];
      while (i < lines.length) {
        const l = lines[i];
        if (!l.trim()) {
          if (i + 1 < lines.length && MD_LIST.test(lines[i + 1])) { i++; continue; }
          break;
        }
        const mm = l.match(MD_LIST);
        if (!mm) {
          if (/^\s{2,}\S/.test(l) && stack.length) { html += " " + mdInline(esc(l.trim())); i++; continue; }
          break;
        }
        const ind = mm[1].length;
        const tag = /\d/.test(mm[2]) ? "ol" : "ul";
        while (stack.length && ind < stack[stack.length - 1].indent) html += `</li></${stack.pop().tag}>`;
        const top = stack[stack.length - 1];
        if (!top || ind > top.indent) { html += `<${tag}>`; stack.push({ indent: ind, tag }); }
        else html += "</li>";
        html += `<li>${mdInline(esc(mm[3]))}`;
        i++;
      }
      while (stack.length) html += `</li></${stack.pop().tag}>`;
      continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !(buf.length && MD_BLOCK.test(lines[i]))) buf.push(lines[i++]);
    html += `<p>${buf.map(b => mdInline(esc(b))).join("<br>")}</p>`;
  }
  return html;
}

/* ── Page ─────────────────────────────────────────────────────────── */
Pages.ai = async function (chatId) {
  if (typeof chatId !== "string") chatId = null;
  const fresh = !$("#ai-root");
  if (fresh) {
    setMain(aiLayout());
    wireAi();
    if (AI.matters === null) {
      AI.matters = State.user.is_platform_admin ? [] : await loadMattersForSelect();
    }
  }
  await loadChatList();
  if (chatId) {
    if (fresh || !AI.chat || AI.chat.id !== chatId) await openChat(chatId);
  } else {
    AI.chat = null; AI.messages = []; AI.files = []; AI.pending = [];
    renderConversation();
  }
  renderChatList();
  renderComposerTools();
  closeAiDrawer();
  const input = $("#ai-input");
  input.focus();
  const handoff = State.aiPrefill;
  State.aiPrefill = null;
  if (handoff && !chatId) {
    input.value = handoff.text;
    input.dispatchEvent(new Event("input"));
    if (handoff.send) sendAiMessage();
    else if (handoff.attach) toast("Attach your file with the 📎 button, then press Enter.");
  }
};
Pages.aiChat = Pages.ai;

/* ── "Ask LawPilot AI" prompt on the home pages ───────────────────── */
const AI_HERO_CHIPS = [
  { label: "Anticipatory bail for attempt to murder", send: true,
    text: "Can anticipatory bail be granted for attempt to murder? Give the relevant BNSS and CrPC provisions and the BNS / IPC sections." },
  { label: "IPC 420 → BNS", send: true, text: "What is the BNS equivalent of IPC Section 420 and what changed in the new provision?" },
  { label: "Summarise a judgment", attach: true,
    text: "Summarise this judgment: facts, issues, arguments, ratio decidendi and the final order." },
  { label: "Review a contract", attach: true,
    text: "Review this agreement for my client and list the risky or one-sided clauses with suggested changes." },
];

function aiHeroHtml() {
  return `<form class="ai-hero" id="ai-hero" autocomplete="off">
    <div class="ai-hero-glow" aria-hidden="true"></div>
    <div class="ai-hero-mark">${AI_ICONS.bands}</div>
    <div class="ai-hero-body">
      <div class="ai-hero-title">Ask LawPilot AI <span class="ai-hero-tag">Cited answers</span></div>
      <div class="ai-hero-sub">Research Indian law, summarise judgments and review contracts. Every answer cites the statute,
        judgment or file it relies on.</div>
      <div class="ai-hero-input">
        <input id="ai-hero-q" type="text" maxlength="4000" aria-label="Ask LawPilot AI"
          placeholder="e.g. What must a Section 138 NI Act notice contain?">
        <button type="submit" class="ai-hero-go" aria-label="Ask">${AI_ICONS.send}</button>
      </div>
      <div class="ai-hero-chips">${AI_HERO_CHIPS.map((c, i) => `<button type="button" class="ai-hero-chip" data-chip="${i}">
        ${c.attach ? AI_ICONS.clip : ""}${esc(c.label)}</button>`).join("")}</div>
    </div></form>`;
}

function wireAiHero() {
  const form = $("#ai-hero");
  if (!form) return;
  form.addEventListener("submit", e => {
    e.preventDefault();
    const text = $("#ai-hero-q").value.trim();
    if (!text) { $("#ai-hero-q").focus(); return; }
    State.aiPrefill = { text, send: true };
    navigate("#/ai");
  });
  form.addEventListener("click", e => {
    const chip = e.target.closest("[data-chip]");
    if (!chip) return;
    const c = AI_HERO_CHIPS[+chip.dataset.chip];
    State.aiPrefill = { text: c.text, send: !!c.send, attach: !!c.attach };
    navigate("#/ai");
  });
}

function aiLayout() {
  return `<div class="ai-root" id="ai-root">
    <aside class="ai-history" id="ai-history">
      <div class="ai-history-top">
        <button class="ai-new" id="ai-new" type="button">${AI_ICONS.plus}<span>New chat</span></button>
        <button class="ai-icon-btn ai-drawer-close" id="ai-drawer-close" type="button" aria-label="Close chats">${AI_ICONS.x}</button>
      </div>
      <input class="ai-search" id="ai-search" type="search" placeholder="Search chats" aria-label="Search chats">
      <div class="ai-chat-list" id="ai-chat-list"></div>
    </aside>
    <div class="ai-scrim" id="ai-scrim"></div>
    <section class="ai-main" id="ai-drop">
      <div class="ai-head">
        <button class="ai-icon-btn ai-drawer-open" id="ai-drawer-open" type="button" aria-label="Show chats">${AI_ICONS.side}</button>
        <div class="ai-head-title" id="ai-head-title">LawPilot AI</div>
        <span class="spacer"></span>
        <button class="ai-chip-btn" id="ai-files-btn" type="button" hidden>${AI_ICONS.doc}<span id="ai-files-count"></span></button>
      </div>
      <div class="ai-scroll" id="ai-scroll"><div class="ai-thread" id="ai-thread"></div></div>
      <div class="ai-composer-wrap">
        <form class="ai-composer" id="ai-form">
          <div class="ai-pending" id="ai-pending"></div>
          <textarea id="ai-input" rows="1" placeholder="Ask a legal question, or attach a file and ask for a summary…"
            aria-label="Message LawPilot AI"></textarea>
          <div class="ai-tools">
            <button class="ai-icon-btn" id="ai-attach" type="button" title="Attach files (PDF, Word, text)" aria-label="Attach files">${AI_ICONS.clip}</button>
            <button class="ai-toggle" id="ai-lib" type="button" aria-pressed="true" title="Search LawPilot's library of Indian statutes and judgments">${AI_ICONS.scale}<span>Law library</span></button>
            <select class="ai-matter" id="ai-matter" aria-label="Link a matter" hidden></select>
            <span class="spacer"></span>
            <button class="ai-send" id="ai-send" type="submit" aria-label="Send" disabled>${AI_ICONS.send}</button>
          </div>
          <input type="file" id="ai-file" accept="${AI_ACCEPT}" multiple hidden>
        </form>
        <div class="ai-foot">LawPilot AI can make mistakes. Check the cited sources before relying on an answer.</div>
      </div>
      <div class="ai-dropzone" id="ai-dropzone"><div>${AI_ICONS.doc}<strong>Drop files to add them to this chat</strong>
        <span>PDF, Word, text or HTML</span></div></div>
    </section>
  </div>`;
}

function wireAi() {
  const input = $("#ai-input");
  const autosize = () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 240) + "px";
    $("#ai-send").disabled = AI.busy || !input.value.trim();
  };
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $("#ai-form").requestSubmit();
    }
  });
  $("#ai-form").addEventListener("submit", e => { e.preventDefault(); sendAiMessage(); });
  $("#ai-new").addEventListener("click", () => { AI.draft = { use_library: true, matter_id: "" }; navigate("#/ai"); });
  $("#ai-attach").addEventListener("click", () => $("#ai-file").click());
  $("#ai-file").addEventListener("change", e => { uploadAiFiles(Array.from(e.target.files)); e.target.value = ""; });
  $("#ai-lib").addEventListener("click", () => updateChatSettings({ use_library: !currentSettings().use_library }));
  $("#ai-matter").addEventListener("change", e => updateChatSettings({ matter_id: e.target.value }));
  $("#ai-files-btn").addEventListener("click", showAiFiles);
  $("#ai-drawer-open").addEventListener("click", () => $("#ai-root").classList.add("drawer"));
  $("#ai-drawer-close").addEventListener("click", closeAiDrawer);
  $("#ai-scrim").addEventListener("click", closeAiDrawer);
  let searchTimer;
  $("#ai-search").addEventListener("input", e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => { AI.search = e.target.value.trim(); await loadChatList(); renderChatList(); }, 250);
  });

  $("#ai-chat-list").addEventListener("click", e => {
    const menuBtn = e.target.closest("[data-chat-menu]");
    if (menuBtn) { e.preventDefault(); e.stopPropagation(); chatMenu(menuBtn); return; }
  });

  $("#ai-thread").addEventListener("click", e => {
    const cite = e.target.closest("[data-cite]");
    if (cite) {
      const msgEl = cite.closest("[data-msg]");
      const msg = AI.messages.find(m => m.id === (msgEl && msgEl.dataset.msg));
      const src = msg && (msg.citations || []).find(c => c.id === cite.dataset.cite);
      if (src) showAiSource(src);
      return;
    }
    const act = e.target.closest("[data-act]");
    if (!act) {
      const sug = e.target.closest("[data-suggest]");
      if (sug) useSuggestion(AI_SUGGESTIONS[+sug.dataset.suggest]);
      return;
    }
    const msg = AI.messages.find(m => m.id === act.closest("[data-msg]").dataset.msg);
    if (act.dataset.act === "copy") copyText(msg.content, act);
    if (act.dataset.act === "regen") regenerateAi();
    if (act.dataset.act === "sources") act.closest(".ai-msg").classList.toggle("show-sources");
    if (act.dataset.act === "verify") showVerification(msg);
  });

  $("#ai-pending").addEventListener("click", e => {
    const rm = e.target.closest("[data-unattach]");
    if (rm) { AI.pending = AI.pending.filter(p => p.key !== rm.dataset.unattach); renderPending(); }
  });

  // Drag and drop files anywhere on the conversation.
  const drop = $("#ai-drop");
  let depth = 0;
  drop.addEventListener("dragenter", e => { if (hasFiles(e)) { depth++; drop.classList.add("dragging"); } });
  drop.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) drop.classList.remove("dragging"); });
  drop.addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
  drop.addEventListener("drop", e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    drop.classList.remove("dragging");
    uploadAiFiles(Array.from(e.dataTransfer.files));
  });
}

function hasFiles(e) { return e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files"); }
function closeAiDrawer() { const r = $("#ai-root"); if (r) r.classList.remove("drawer"); }

/* ── Chat list ────────────────────────────────────────────────────── */
async function loadChatList() {
  try {
    const r = await api("GET", "/api/ai/chats" + (AI.search ? "?q=" + encodeURIComponent(AI.search) : ""));
    AI.chats = r.items;
  } catch (e) { AI.chats = []; fail(e); }
}

function chatGroup(c) {
  if (c.pinned) return "Pinned";
  const d = new Date(c.updated_at);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = (today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000;
  if (days <= 0) return "Today";
  if (days <= 1) return "Yesterday";
  if (days <= 7) return "Previous 7 days";
  if (days <= 30) return "Previous 30 days";
  return "Older";
}

function renderChatList() {
  const el = $("#ai-chat-list");
  if (!el) return;
  if (!AI.chats.length) {
    el.innerHTML = `<div class="ai-list-empty">${AI.search ? "No chats match your search." : "Your chats will appear here."}</div>`;
    return;
  }
  const groups = {};
  AI.chats.forEach(c => { (groups[chatGroup(c)] = groups[chatGroup(c)] || []).push(c); });
  el.innerHTML = ["Pinned", "Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"].filter(g => groups[g])
    .map(g => `<div class="ai-group">${esc(g)}</div>` + groups[g].map(c => `
      <a class="ai-chat-item ${AI.chat && AI.chat.id === c.id ? "active" : ""}" href="#/ai/${esc(c.id)}" title="${esc(c.title)}">
        <span class="ai-chat-name">${esc(c.title)}</span>
        <button class="ai-chat-menu" type="button" data-chat-menu="${esc(c.id)}" aria-label="Chat options">${AI_ICONS.dots}</button>
      </a>`).join("")).join("");
}

function chatMenu(btn) {
  document.querySelectorAll(".ai-menu").forEach(m => m.remove());
  const chat = AI.chats.find(c => c.id === btn.dataset.chatMenu);
  if (!chat) return;
  const menu = document.createElement("div");
  menu.className = "ai-menu";
  menu.innerHTML = `<button type="button" data-m="rename">Rename</button>
    <button type="button" data-m="pin">${chat.pinned ? "Unpin" : "Pin"}</button>
    <button type="button" data-m="delete" class="danger">Delete</button>`;
  document.body.appendChild(menu);
  const r = btn.getBoundingClientRect();
  menu.style.top = Math.min(window.innerHeight - 140, r.bottom + 4) + "px";
  menu.style.left = Math.max(8, r.right - 160) + "px";
  const close = ev => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener("mousedown", close); } };
  setTimeout(() => document.addEventListener("mousedown", close), 0);
  menu.addEventListener("click", async e => {
    const action = e.target.closest("[data-m]");
    if (!action) return;
    menu.remove();
    document.removeEventListener("mousedown", close);
    try {
      if (action.dataset.m === "rename") renameChat(chat);
      if (action.dataset.m === "pin") {
        await api("PATCH", `/api/ai/chats/${chat.id}`, { pinned: !chat.pinned });
        await loadChatList(); renderChatList();
      }
      if (action.dataset.m === "delete") {
        if (!(await confirmDialog(`Delete “${chat.title}”? Its messages and uploaded files are removed permanently.`,
          { okLabel: "Delete chat", danger: true }))) return;
        await api("DELETE", `/api/ai/chats/${chat.id}`);
        toast("Chat deleted.");
        if (AI.chat && AI.chat.id === chat.id) { AI.chat = null; navigate("#/ai"); }
        else { await loadChatList(); renderChatList(); }
      }
    } catch (err) { fail(err); }
  });
}

function renameChat(chat) {
  const m = modal(`<h2>Rename chat</h2><form id="rn"><div class="field"><input name="title" maxlength="120" required
    value="${esc(chat.title)}" aria-label="Chat title"></div>
    <div class="row"><span class="spacer"></span><button type="button" class="btn" data-close>Cancel</button>
    <button class="btn primary">Save</button></div></form>`);
  const input = $("input", m.el);
  setTimeout(() => input.select(), 40);
  $("#rn", m.el).addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("button.primary", m.el), async () => {
      const r = await api("PATCH", `/api/ai/chats/${chat.id}`, { title: input.value });
      if (AI.chat && AI.chat.id === chat.id) { AI.chat = r.chat; $("#ai-head-title").textContent = r.chat.title; }
      m.close();
      await loadChatList(); renderChatList();
    });
  });
}

/* ── Conversation ─────────────────────────────────────────────────── */
async function openChat(id) {
  try {
    const r = await api("GET", `/api/ai/chats/${id}`);
    AI.chat = r.chat; AI.messages = r.messages; AI.files = r.files; AI.pending = [];
  } catch (e) {
    fail(e);
    navigate("#/ai");
    return;
  }
  renderConversation();
}

function greeting() {
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return `${part}, ${esc((State.user.name || "").split(" ")[0] || "there")}`;
}

function renderConversation() {
  $("#ai-head-title").textContent = AI.chat ? AI.chat.title : "LawPilot AI";
  renderFilesButton();
  renderPending();
  const thread = $("#ai-thread");
  if (!AI.messages.length) {
    thread.innerHTML = `<div class="ai-welcome">
      <div class="ai-orb">${AI_ICONS.bands}</div>
      <h1>${greeting()}</h1>
      <p class="muted">Ask about Indian law, or attach judgments, contracts and pleadings to summarise, compare and question them.
        Answers cite your files and LawPilot's library.</p>
      <div class="ai-suggest">${AI_SUGGESTIONS.map((s, i) => `<button type="button" class="ai-suggest-card" data-suggest="${i}">
        <strong>${esc(s.title)}</strong><span>${esc(s.sub)}</span>${s.attach ? `<em>${AI_ICONS.clip} attach a file</em>` : ""}</button>`).join("")}</div>
    </div>`;
    return;
  }
  thread.innerHTML = AI.messages.map((m, i) => messageHtml(m, i === AI.messages.length - 1)).join("");
  scrollAiToEnd();
}

function messageHtml(m, isLast) {
  if (m.role === "user") {
    const att = (m.attachments || []).map(a => `<span class="ai-att">${AI_ICONS.doc}${esc(a.title)}</span>`).join("");
    return `<div class="ai-msg user" data-msg="${esc(m.id)}">${att ? `<div class="ai-atts">${att}</div>` : ""}
      <div class="ai-bubble">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`;
  }
  const extra = m.extra || {};
  const cites = m.citations || [];
  const cited = new Set(extra.cited || []);
  const shown = cites.filter(c => cited.has(c.id) || extra.mode === "summary" || !m.model);
  const others = cites.filter(c => !shown.includes(c));
  const v = extra.verification;
  let verify = "";
  if (v && v.checked) {
    const ok = v.supported, total = v.checked;
    const cls = v.unsupported || (v.invalid_citations || []).length ? "warn" : v.partial ? "mid" : "ok";
    verify = `<button type="button" class="ai-verify ${cls}" data-act="verify" title="How well each cited statement matches its source">
      ✓ ${ok}/${total} cited statements verified</button>`;
  }
  const notes = (extra.notes || []).map(n => `<div class="ai-note">${esc(n)}</div>`).join("");
  return `<div class="ai-msg assistant ${extra.error ? "error" : ""}" data-msg="${esc(m.id)}">
    <div class="ai-avatar">${AI_ICONS.bands}</div>
    <div class="ai-body">
      ${extra.mode === "summary" ? `<div class="ai-mode-tag">Document summary</div>` : ""}
      <div class="ai-md">${renderMarkdown(m.content)}</div>
      ${notes}
      ${shown.length ? `<div class="ai-sources">${shown.map(sourceCard).join("")}</div>` : ""}
      ${others.length ? `<div class="ai-more-sources">${others.map(sourceCard).join("")}</div>` : ""}
      <div class="ai-actions">
        <button type="button" class="ai-act" data-act="copy" title="Copy">${AI_ICONS.copy}</button>
        ${isLast ? `<button type="button" class="ai-act" data-act="regen" title="Regenerate">${AI_ICONS.redo}</button>` : ""}
        ${others.length ? `<button type="button" class="ai-act text" data-act="sources">${others.length} more searched passage${others.length > 1 ? "s" : ""}</button>` : ""}
        ${verify}
        ${m.model ? `<span class="ai-model">${esc(m.model.split(":").pop())}</span>` : ""}
      </div>
    </div></div>`;
}

const ORIGIN = { file: "Your file", matter: "Matter document", library: "Library" };

function sourceCard(c) {
  const sub = [c.citation, c.court, c.date ? fmtDate(c.date) : "", c.pinpoint].filter(Boolean).join(" · ");
  return `<button type="button" class="ai-src ${esc(c.origin)}" data-cite="${esc(c.id)}">
    <span class="ai-src-n">${esc(c.id.slice(1))}</span>
    <span class="ai-src-text"><strong>${esc(c.title || "Untitled")}</strong><small>${esc(ORIGIN[c.origin] || "")}${sub ? " · " + esc(sub) : ""}</small></span>
    ${c.role === "contrary" ? `<span class="badge warn">contrary</span>` : ""}</button>`;
}

function showAiSource(c) {
  const sub = [c.citation, c.court, c.date ? fmtDate(c.date) : "", c.pinpoint].filter(Boolean).join(" · ");
  modal(`<div class="row"><span class="badge">${esc(ORIGIN[c.origin] || "")}</span>
      ${c.legal_status && !["unknown", "in_force", "good_law"].includes(c.legal_status) ? statusBadge(c.legal_status) : ""}
      ${c.role === "contrary" ? badge("possible contrary authority", "warn") : ""}<span class="spacer"></span>
      <button class="btn small" data-close>Close</button></div>
    <h2 style="margin-top:10px">[${esc(c.id.slice(1))}] ${esc(c.title || "Untitled")}</h2>
    ${sub ? `<p class="muted">${esc(sub)}</p>` : ""}
    <blockquote class="ai-quote">${esc(c.excerpt || "")}</blockquote>
    ${c.flagged ? `<div class="banner warn">This text contained instruction-like content. It was treated as data only.</div>` : ""}
    <div class="row"><a class="btn primary" href="#/sources/${esc(c.source_id)}">Open full text</a>
    ${c.source_url ? `<a class="btn" href="${esc(c.source_url)}" target="_blank" rel="noopener noreferrer">Official source ↗</a>` : ""}</div>`,
  { wide: true });
}

function showVerification(m) {
  const v = (m.extra || {}).verification;
  if (!v) return;
  const label = { supported: ["Supported", "ok"], partial: ["Partly supported", "warn"], unsupported: ["Not supported", "bad"] };
  modal(`<div class="row"><h2 style="margin:0">Citation check</h2><span class="spacer"></span><button class="btn small" data-close>Close</button></div>
    <p class="muted">Each statement that cites a source was compared with the passage it cites (terms, section numbers,
      years and amounts). This is an automatic check, not a substitute for reading the source.</p>
    ${(v.invalid_citations || []).length ? `<div class="banner bad">Cites sources that do not exist: ${esc(v.invalid_citations.join(", "))}</div>` : ""}
    <div class="ai-claims">${(v.claims || []).map(c => `<div class="ai-claim">${badge(...label[c.status])}
      <span>${esc(c.text)}</span><small class="muted">${esc(c.cited.join(", "))}</small></div>`).join("")}</div>`,
  { wide: true });
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    btn.classList.add("done");
    setTimeout(() => btn.classList.remove("done"), 1200);
  } catch (e) { toast("Could not copy to the clipboard.", true); }
}

function scrollAiToEnd() {
  const s = $("#ai-scroll");
  if (!s) return;
  s.scrollTop = s.scrollHeight;
  requestAnimationFrame(() => { s.scrollTop = s.scrollHeight; });
}

function typingHtml(text) {
  return `<div class="ai-msg assistant typing" id="ai-typing"><div class="ai-avatar">${AI_ICONS.bands}</div>
    <div class="ai-body"><div class="ai-dots"><i></i><i></i><i></i></div><span class="ai-typing-text">${esc(text)}</span></div></div>`;
}

function startTyping(steps) {
  $("#ai-thread").insertAdjacentHTML("beforeend", typingHtml(steps[0]));
  scrollAiToEnd();
  let n = 0;
  const timer = setInterval(() => {
    n = Math.min(n + 1, steps.length - 1);
    const t = $("#ai-typing .ai-typing-text");
    if (t) t.textContent = steps[n];
  }, 2600);
  return () => { clearInterval(timer); const el = $("#ai-typing"); if (el) el.remove(); };
}

function typingSteps(text, attachments) {
  const summary = /summar|gist|key points|overview|synopsis|brief/i.test(text);
  if (summary && (attachments.length || AI.files.length)) return ["Reading your document…", "Pulling out the key points…", "Writing the summary…"];
  const where = [AI.files.length ? "your files" : "", currentSettings().use_library ? "the law library" : ""].filter(Boolean).join(" and ");
  return [where ? `Searching ${where}…` : "Thinking…", "Ranking the most relevant passages…", "Writing a cited answer…", "Checking citations…"];
}

/* ── Settings (library toggle, linked matter) ─────────────────────── */
function currentSettings() {
  if (AI.chat) return { use_library: AI.chat.use_library, matter_id: AI.chat.matter_id || "" };
  return AI.draft;
}

function renderComposerTools() {
  const s = currentSettings();
  const lib = $("#ai-lib");
  lib.setAttribute("aria-pressed", String(!!s.use_library));
  lib.classList.toggle("on", !!s.use_library);
  const sel = $("#ai-matter");
  const matters = AI.matters || [];
  sel.hidden = !matters.length;
  if (matters.length) {
    sel.innerHTML = `<option value="">No matter linked</option>` + matters.map(m =>
      `<option value="${esc(m.id)}" ${m.id === s.matter_id ? "selected" : ""}>${esc(m.title)}</option>`).join("");
  }
}

async function updateChatSettings(change) {
  if (!AI.chat) {
    Object.assign(AI.draft, change);
    renderComposerTools();
    return;
  }
  try {
    const r = await api("PATCH", `/api/ai/chats/${AI.chat.id}`, change);
    AI.chat = r.chat;
  } catch (e) { fail(e); }
  renderComposerTools();
  if ("use_library" in change) toast(change.use_library ? "Answers will also search the law library." : "Answers will use only your files" + (AI.chat && AI.chat.matter ? " and matter documents." : "."));
}

async function ensureChat() {
  if (AI.chat) return AI.chat;
  const r = await api("POST", "/api/ai/chats", { use_library: AI.draft.use_library, matter_id: AI.draft.matter_id || null });
  AI.chat = r.chat; AI.messages = []; AI.files = [];
  history.replaceState(null, "", `#/ai/${AI.chat.id}`);
  await loadChatList();
  renderChatList();
  return AI.chat;
}

/* ── Files ────────────────────────────────────────────────────────── */
function renderPending() {
  const el = $("#ai-pending");
  el.innerHTML = AI.pending.map(p => `<span class="ai-pchip ${p.state}">
    <span class="ai-pchip-ico">${p.state === "uploading" ? '<span class="spinner"></span>' : AI_ICONS.doc}</span>
    <span class="ai-pchip-name" title="${esc(p.name)}">${esc(p.name)}</span>
    ${p.state !== "uploading" ? `<button type="button" data-unattach="${esc(p.key)}" aria-label="Remove">${AI_ICONS.x}</button>` : ""}</span>`).join("");
  el.hidden = !AI.pending.length;
}

function renderFilesButton() {
  const btn = $("#ai-files-btn");
  btn.hidden = !AI.files.length;
  $("#ai-files-count").textContent = `${AI.files.length} file${AI.files.length === 1 ? "" : "s"}`;
}

async function uploadAiFiles(list) {
  const files = list.filter(f => f && f.name);
  if (!files.length) return;
  const limit = (State.status && State.status.serverless) ? 4 : 25;
  const ok = files.filter(f => {
    if (f.size > limit * 1024 * 1024) { toast(`${f.name} is larger than ${limit} MB.`, true); return false; }
    return true;
  });
  if (!ok.length) return;
  const entries = ok.map(f => ({ key: Math.random().toString(36).slice(2), name: f.name, state: "uploading", id: null }));
  AI.pending.push(...entries);
  renderPending();
  try {
    const chat = await ensureChat();
    // One request per file keeps each under the host's request-size limit.
    for (let i = 0; i < ok.length; i++) {
      const fd = new FormData();
      fd.append("file", ok[i]);
      try {
        const r = await api("POST", `/api/ai/chats/${chat.id}/files`, fd);
        const res = r.results[0];
        AI.files = r.files;
        if (res.status === "failed") throw new Error(res.message);
        entries[i].id = res.id;
        entries[i].state = "ready";
        if (res.status === "duplicate") toast(`${ok[i].name} is already in this chat.`);
      } catch (err) {
        AI.pending = AI.pending.filter(p => p !== entries[i]);
        toast(`${ok[i].name}: ${err.message}`, true);
      }
      renderPending();
      renderFilesButton();
    }
  } catch (e) {
    AI.pending = AI.pending.filter(p => !entries.includes(p));
    renderPending();
    fail(e);
  }
  $("#ai-input").focus();
}

function showAiFiles() {
  const m = modal(`<div class="row"><h2 style="margin:0">Files in this chat</h2><span class="spacer"></span>
      <button class="btn small" data-close>Close</button></div>
    <p class="muted">Answers in this chat search these files. They are private to you.</p>
    <div class="ai-file-list">${AI.files.map(f => `<div class="ai-file-row">${AI_ICONS.doc}
      <div class="ai-file-meta"><strong>${esc(f.title)}</strong><small class="muted">${esc(f.original_filename || "")}
        ${f.page_count ? " · " + f.page_count + " pages" : ""}</small></div>
      <a class="btn small" href="#/sources/${esc(f.id)}">Open</a>
      <button class="btn small" type="button" data-attach="${esc(f.id)}">Ask about it</button>
      <button class="btn small danger" type="button" data-rmfile="${esc(f.id)}">Remove</button></div>`).join("")}</div>`, { wide: true });
  m.el.addEventListener("click", async e => {
    const att = e.target.closest("[data-attach]");
    if (att) {
      const f = AI.files.find(x => x.id === att.dataset.attach);
      if (f && !AI.pending.some(p => p.id === f.id)) AI.pending.push({ key: f.id, name: f.title, state: "ready", id: f.id });
      renderPending();
      m.close();
      $("#ai-input").focus();
      return;
    }
    const rm = e.target.closest("[data-rmfile]");
    if (!rm) return;
    if (!(await confirmDialog("Remove this file from the chat? Earlier answers keep their citations.", { okLabel: "Remove", danger: true }))) return;
    try {
      const r = await api("DELETE", `/api/ai/chats/${AI.chat.id}/files/${rm.dataset.rmfile}`);
      AI.files = r.files;
      AI.pending = AI.pending.filter(p => p.id !== rm.dataset.rmfile);
      renderFilesButton();
      renderPending();
      m.close();
      if (AI.files.length) showAiFiles();
    } catch (err) { fail(err); }
  });
}

/* ── Sending ──────────────────────────────────────────────────────── */
function useSuggestion(s) {
  const input = $("#ai-input");
  input.value = s.prompt;
  input.dispatchEvent(new Event("input"));
  input.focus();
  if (s.attach) $("#ai-file").click();
}

function setBusy(on) {
  AI.busy = on;
  $("#ai-send").disabled = on || !$("#ai-input").value.trim();
  $("#ai-root").classList.toggle("busy", on);
}

async function sendAiMessage() {
  const input = $("#ai-input");
  const text = input.value.trim();
  if (!text || AI.busy) return;
  if (AI.pending.some(p => p.state === "uploading")) { toast("Wait for the files to finish uploading."); return; }
  const attachments = AI.pending.filter(p => p.id).map(p => ({ id: p.id, title: p.name }));
  setBusy(true);
  let stop = () => {};
  try {
    const chat = await ensureChat();
    input.value = "";
    input.dispatchEvent(new Event("input"));
    AI.pending = [];
    renderPending();
    const temp = { id: "pending-" + Date.now(), role: "user", content: text, attachments };
    if (!AI.messages.length) $("#ai-thread").innerHTML = "";
    AI.messages.push(temp);
    $("#ai-thread").insertAdjacentHTML("beforeend", messageHtml(temp, false));
    stop = startTyping(typingSteps(text, attachments));
    const r = await api("POST", `/api/ai/chats/${chat.id}/messages`, { content: text, attachments: attachments.map(a => a.id) });
    stop();
    AI.messages[AI.messages.indexOf(temp)] = r.user_message;
    AI.messages.push(r.message);
    AI.chat = r.chat;
    renderConversation();
    loadChatList().then(renderChatList);
  } catch (e) {
    stop();
    const t = AI.messages.findIndex(m => String(m.id).startsWith("pending-"));
    if (t >= 0) AI.messages.splice(t, 1);
    renderConversation();
    input.value = text;
    input.dispatchEvent(new Event("input"));
    fail(e);
  } finally {
    setBusy(false);
    input.focus();
  }
}

async function regenerateAi() {
  if (AI.busy || !AI.chat) return;
  setBusy(true);
  const last = AI.messages[AI.messages.length - 1];
  if (last && last.role === "assistant") AI.messages.pop();
  renderConversation();
  const q = AI.messages[AI.messages.length - 1];
  const stop = startTyping(typingSteps(q ? q.content : "", (q && q.attachments) || []));
  try {
    const r = await api("POST", `/api/ai/chats/${AI.chat.id}/regenerate`);
    stop();
    AI.messages.push(r.message);
  } catch (e) {
    stop();
    if (last) AI.messages.push(last);
    fail(e);
  } finally {
    renderConversation();
    setBusy(false);
  }
}
