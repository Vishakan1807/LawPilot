/* Settings → AI models: connect your own provider keys and choose the model that answers. */
"use strict";

const AIM_INITIALS = { anthropic: "A", gemini: "G", openai: "O", mistral: "M", deepseek: "D", groq: "Q", openrouter: "R" };

async function loadAiModels() {
  return api("GET", "/api/account/ai");
}

function aimActiveLine(v) {
  const a = v.active;
  if (a.source === "own") {
    const p = v.providers.find(x => x.key === a.provider);
    return `<strong>${esc(p ? p.name : a.provider)}</strong> · <code>${esc(a.model)}</code> <span class="badge ok">your key</span>`;
  }
  if (a.source === "server") return `<strong>The server's model</strong> · <code>${esc(a.model || "")}</code> <span class="badge info">shared</span>`;
  return `<strong>No model connected.</strong> <span class="muted">${esc(a.reason || "")}</span>`;
}

async function renderAiModels(el) {
  el.innerHTML = loading("Loading your AI models…");
  let v;
  try { v = await loadAiModels(); } catch (e) { el.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; return; }
  const keys = Object.fromEntries(v.keys.map(k => [k.provider, k]));
  el.innerHTML = `
    <div class="card aim-hero">
      <div class="aim-hero-ico">${ADVOCATE_BANDS}</div>
      <div class="aim-hero-body">
        <div class="muted aim-eyebrow">Answering your questions now</div>
        <div class="aim-active">${aimActiveLine(v)}</div>
        <p class="muted">Bring your own API key from any provider below and switch between them at any time. Your key is
          encrypted on the server, used only for your requests, and never shown again. Search, ranking, citations and
          claim-checking work the same whichever model you choose. Only the passages relevant to each question are sent
          to the provider.</p>
        ${v.server.available && v.active.source === "own" ? `<button class="btn small" id="aim-server">Use the server's model instead</button>` : ""}
      </div>
    </div>
    <div class="aim-grid">${v.providers.map(p => aimCard(p, keys[p.key], v.active)).join("")}</div>`;

  const refresh = () => renderAiModels(el);
  if ($("#aim-server")) $("#aim-server").addEventListener("click", () => withBusy($("#aim-server"), async () => {
    await api("POST", "/api/account/ai/use-server"); toast("Using the server's model."); State.status = null; refresh();
  }));
  el.querySelectorAll("[data-aim]").forEach(btn => btn.addEventListener("click", () => {
    const provider = v.providers.find(p => p.key === btn.dataset.provider);
    const key = keys[btn.dataset.provider];
    const act = btn.dataset.aim;
    if (act === "connect" || act === "replace") return aimConnectDialog(provider, key, refresh);
    if (act === "model") return aimModelDialog(provider, key, refresh);
    withBusy(btn, async () => {
      if (act === "use") { await api("POST", `/api/account/ai/${provider.key}/activate`); toast(`Now answering with ${provider.name}.`); }
      if (act === "test") {
        try {
          const r = await api("POST", `/api/account/ai/${provider.key}/test`);
          toast(`${provider.name} works · ${r.check.model}`);
        } catch (e) { toast(e.message, true); }
      }
      if (act === "remove") {
        if (!(await confirmDialog(`Disconnect ${provider.name}? The saved key is deleted from LawPilot.`, { okLabel: "Disconnect", danger: true }))) return;
        await api("DELETE", `/api/account/ai/${provider.key}`); toast(`${provider.name} disconnected.`);
      }
      State.status = null;
      refresh();
    });
  }));
}

function aimCard(p, k, active) {
  const isActive = k && k.is_active && active.source === "own";
  const status = !k ? `<span class="badge">Not connected</span>`
    : k.last_error ? `<span class="badge bad">Needs attention</span>`
    : isActive ? `<span class="badge ok">Active</span>` : `<span class="badge info">Connected</span>`;
  return `<div class="card aim-card ${isActive ? "active" : ""} ${k ? "connected" : ""}">
    <div class="aim-card-head">
      <span class="aim-logo aim-${esc(p.key)}">${esc(AIM_INITIALS[p.key] || p.name[0])}</span>
      <div class="aim-card-title"><strong>${esc(p.name)}</strong><small class="muted">${esc(p.note || "")}</small></div>
      ${status}
    </div>
    ${k ? `<dl class="aim-meta"><dt>Model</dt><dd><code>${esc(k.model)}</code></dd>
        <dt>Key</dt><dd>${esc(k.key_hint || "saved")}</dd>
        <dt>Last used</dt><dd>${k.last_used_at ? ago(k.last_used_at) : "never"}</dd></dl>
      ${k.last_error ? `<div class="banner bad aim-err">${esc(k.last_error)}</div>` : ""}
      <div class="row aim-actions">
        ${isActive ? "" : `<button class="btn small primary" data-aim="use" data-provider="${p.key}">Use this model</button>`}
        <button class="btn small" data-aim="model" data-provider="${p.key}">Change model</button>
        <button class="btn small" data-aim="test" data-provider="${p.key}">Test</button>
        <button class="btn small" data-aim="replace" data-provider="${p.key}">Replace key</button>
        <button class="btn small danger" data-aim="remove" data-provider="${p.key}">Remove</button>
      </div>`
    : `<p class="muted aim-blurb">Default model <code>${esc(p.default_model)}</code>. You can pick another after connecting.</p>
      <div class="row aim-actions"><button class="btn small primary" data-aim="connect" data-provider="${p.key}">Connect ${esc(p.name)}</button>
        <a class="btn small" href="${esc(p.key_url)}" target="_blank" rel="noopener noreferrer">Get a key ↗</a></div>`}
  </div>`;
}

function aimConnectDialog(p, existing, done) {
  const m = modal(`<h2>${existing ? "Replace" : "Connect"} ${esc(p.name)}</h2>
    <ol class="aim-steps">
      <li>Open <a href="${esc(p.key_url)}" target="_blank" rel="noopener noreferrer">${esc(p.name)} → API keys ↗</a> and create a key.</li>
      <li>Paste it below${p.key_prefix ? ` (it usually starts with <code>${esc(p.key_prefix)}</code>)` : ""}.</li>
      <li>LawPilot sends one tiny test request, then saves the key encrypted.</li>
    </ol>
    <form id="aim-form">
      <div class="field"><label for="aim-key">API key</label>
        <input id="aim-key" name="api_key" type="password" autocomplete="off" spellcheck="false" required placeholder="Paste your ${esc(p.name)} API key"></div>
      <div class="field"><label for="aim-model">Model</label>
        <div class="row"><input id="aim-model" name="model" list="aim-models" value="${esc(existing ? existing.model : p.default_model)}" style="flex:1">
          <button type="button" class="btn small" id="aim-load">Load my models</button></div>
        <datalist id="aim-models"></datalist>
        <div class="hint">Leave the default, or load the models your key can use and pick one.</div></div>
      <div id="aim-out"></div>
      <div class="row end"><button type="button" class="btn" data-close>Cancel</button>
        <button class="btn primary" id="aim-save">Test &amp; save</button></div>
    </form>`);
  const el = m.el;
  $("#aim-load", el).addEventListener("click", () => withBusy($("#aim-load", el), async () => {
    const key = $("#aim-key", el).value.trim();
    if (!key) { toast("Paste your API key first.", true); return; }
    const r = await api("POST", `/api/account/ai/${p.key}/models`, { api_key: key });
    $("#aim-models", el).innerHTML = r.models.map(x => `<option value="${esc(x)}">`).join("");
    toast(`${r.models.length} models available. Click the Model box to choose.`);
  }));
  $("#aim-form", el).addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#aim-save", el), async () => {
      $("#aim-out", el).innerHTML = "";
      try {
        const r = await api("PUT", `/api/account/ai/${p.key}`, { api_key: $("#aim-key", el).value, model: $("#aim-model", el).value, activate: true });
        m.close();
        toast(`${p.name} connected · ${r.check.model}. It now answers your questions.`);
        State.status = null;
        done();
      } catch (err) {
        $("#aim-out", el).innerHTML = `<div class="banner bad"><strong>That did not work</strong>${esc(err.message)}</div>`;
      }
    });
  });
}

async function aimModelDialog(p, k, done) {
  const m = modal(`<h2>${esc(p.name)} model</h2><div id="aim-ml">${loading("Fetching the models your key can use…")}</div>`);
  const el = m.el;
  let models = [];
  try { models = (await api("GET", `/api/account/ai/${p.key}/models`)).models; } catch (e) { /* free text below */ }
  $("#aim-ml", el).innerHTML = `<form id="aim-mf">
      <div class="field"><label for="aim-mi">Model</label>
        <input id="aim-mi" list="aim-mlist" value="${esc(k.model)}" required>
        <datalist id="aim-mlist">${models.map(x => `<option value="${esc(x)}">`).join("")}</datalist>
        <div class="hint">${models.length ? `${models.length} models available to your key.` : "Type the model id from your provider's documentation."}</div></div>
      <div id="aim-mo"></div>
      <div class="row end"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary" id="aim-ms">Test &amp; save</button></div>
    </form>`;
  $$("[data-close]", el).forEach(b => b.addEventListener("click", m.close));
  $("#aim-mf", el).addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#aim-ms", el), async () => {
      try {
        const r = await api("PUT", `/api/account/ai/${p.key}`, { model: $("#aim-mi", el).value });
        m.close(); toast(`${p.name} now uses ${r.check.model}.`); State.status = null; done();
      } catch (err) { $("#aim-mo", el).innerHTML = `<div class="banner bad">${esc(err.message)}</div>`; }
    });
  });
}

/* Compact switcher used inside AI mode: pick which connected model answers. */
function aiModelSwitcherHtml(v) {
  const opts = v.keys.map(k => `<option value="${esc(k.provider)}" ${k.is_active && v.active.source === "own" ? "selected" : ""}>${esc(k.provider_name)} · ${esc(k.model)}</option>`);
  if (v.server.available) opts.push(`<option value="__server" ${v.active.source === "server" ? "selected" : ""}>Server model</option>`);
  if (!opts.length) return `<a class="ai-toggle ai-connect" href="#/settings?tab=ai">Connect an AI model</a>`;
  return `<select class="ai-matter ai-model-select" id="ai-model" aria-label="AI model">${opts.join("")}</select>`;
}
