/* Sign in, register, password reset, invitation acceptance. */
"use strict";

function authLayout(inner) {
  document.body.innerHTML = `<div class="auth-theme"><span class="ts-label"></span>${themeSwitchHtml()}</div>
    <div class="auth-wrap"><div class="auth-stack"><div class="card auth-card">
    <div class="brand"><div class="brand-mark">L</div><div><div class="brand-name">LawPilot</div>
    <div class="brand-sub">Legal research &amp; practice management</div></div></div>${inner}</div>
    <div class="auth-motto" lang="sa"><strong>सत्यमेव जयते</strong><span lang="en">Satyameva Jayate · Truth alone triumphs</span></div>
    </div></div>`;
  wireThemeSwitches();
}

Pages.login = function (query) {
  const next = query.get("next") || "#/home";
  authLayout(`<h2>Sign in</h2>
    <form id="f"><div class="field"><label for="email">Email</label><input id="email" name="email" type="email" required autocomplete="email"></div>
    <div class="field"><label for="password">Password</label><input id="password" name="password" type="password" required autocomplete="current-password"></div>
    <button class="btn primary" style="width:100%;justify-content:center">Sign in</button></form>
    <hr><div class="row"><a href="#/forgot">Forgot password?</a><span class="spacer"></span><a href="#/register${query.toString() ? "?" + esc(query.toString()) : ""}">Create an account</a></div>`);
  $("#f").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#f button"), async () => {
      const r = await api("POST", "/api/auth/login", formObject(e.target));
      State.user = r.user;
      navigate(r.user.must_change_password ? `#/set-password?next=${encodeURIComponent(next)}` : next);
    });
  });
};

Pages.register = function (query) {
  const next = query.get("next") || "#/home";
  authLayout(`<h2>Create your account</h2>
    <p class="muted">For individual advocates and law-firm members. We will email you a temporary password and a link to
      choose your own, which confirms the address is yours.</p>
    <form id="f">
      <div class="field"><label for="name">Full name</label><input id="name" name="name" required autocomplete="name"></div>
      <div class="field"><label for="email">Email</label><input id="email" name="email" type="email" required autocomplete="email"
        value="${esc(query.get("email") || "")}"></div>
      <div class="field"><label for="bar">Bar enrolment no. <small>(optional)</small></label><input id="bar" name="bar_enrolment"></div>
      <button class="btn primary" style="width:100%;justify-content:center">Create account</button></form>
    <hr><a href="#/login${query.toString() ? "?" + esc(query.toString()) : ""}">Already have an account? Sign in</a>`);
  $("#f").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#f button"), async () => {
      const r = await api("POST", "/api/auth/register", { ...formObject(e.target), next: next !== "#/home" ? next : "" });
      authLayout(`<div class="auth-sent">
        <div class="auth-sent-ico" aria-hidden="true">✉</div>
        <h2>Check your inbox</h2>
        <p>${esc(r.message)}</p>
        <ol class="muted auth-steps"><li>Open the email from LawPilot (check spam if it is not there in a minute).</li>
          <li>Click <strong>Set my password</strong>, or sign in with the temporary password.</li>
          <li>Choose your own password and you are in.</li></ol>
        ${r.dev_set_password_link ? `<div class="banner info"><strong>Development mode: no email server is configured</strong>
          Temporary password: <code>${esc(r.dev_temp_password)}</code><br><a href="${esc(r.dev_set_password_link)}">Open the set-password link</a></div>` : ""}
        <a class="btn primary" href="#/login${next !== "#/home" ? "?next=" + encodeURIComponent(next) : ""}" style="width:100%;justify-content:center">Go to sign in</a>
      </div>`);
    });
  });
};

Pages.forgot = function () {
  authLayout(`<h2>Reset password</h2><p class="muted">Enter your email and we will send a reset link.</p>
    <form id="f"><div class="field"><label for="email">Email</label><input id="email" name="email" type="email" required></div>
    <button class="btn primary" style="width:100%;justify-content:center">Send reset link</button></form>
    <div id="out"></div><hr><a href="#/login">Back to sign in</a>`);
  $("#f").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#f button"), async () => {
      const r = await api("POST", "/api/auth/forgot-password", formObject(e.target));
      let html = `<div class="banner ok" style="margin-top:12px">${esc(r.message)}</div>`;
      if (r.mail_configured === false) {
        html += `<div class="banner warn">This server has no email (SMTP) configured, so no email can actually be sent. Ask your administrator to reset access.</div>`;
      }
      if (r.dev_reset_link) html += `<div class="banner info"><strong>Development mode</strong><a href="${esc(r.dev_reset_link)}">Open reset link</a></div>`;
      $("#out").innerHTML = html;
    });
  });
};

function passwordFields() {
  return `<div class="field"><label for="password">New password</label><input id="password" name="password" type="password" required minlength="10" autocomplete="new-password">
      <div class="hint">At least 10 characters, mixing letters with numbers or symbols.</div></div>
    <div class="field"><label for="confirm">Confirm password</label><input id="confirm" name="confirm" type="password" required minlength="10" autocomplete="new-password"></div>`;
}

function checkPasswords(form) {
  if (form.password.value !== form.confirm.value) { toast("The two passwords do not match.", true); return false; }
  return true;
}

function safeNext(value) {
  return value && value.startsWith("#/") && !value.includes("//") ? value : "#/home";
}

Pages.resetPassword = function (query) {
  const token = query.get("token") || "";
  const welcome = query.get("welcome") === "1";
  const next = safeNext(query.get("next"));
  authLayout(`<h2>${welcome ? "Welcome to LawPilot" : "Choose a new password"}</h2>
    ${welcome ? `<p class="muted">Choose the password you will use to sign in. This replaces the temporary one we emailed you.</p>` : ""}
    <form id="f">${passwordFields()}
    <button class="btn primary" style="width:100%;justify-content:center">${welcome ? "Set password and continue" : "Set password"}</button></form>`);
  $("#f").addEventListener("submit", e => {
    e.preventDefault();
    if (!checkPasswords(e.target)) return;
    withBusy($("#f button"), async () => {
      const r = await api("POST", "/api/auth/reset-password", { token, password: e.target.password.value });
      if (r.user) {                       // welcome link: signed in straight away
        State.user = r.user;
        document.body.innerHTML = "";
        toast(r.user.is_platform_admin ? "Welcome! You are the administrator of this LawPilot server." : "Welcome to LawPilot.");
        navigate(next);
        return;
      }
      toast("Password updated. Please sign in.");
      navigate("#/login");
    });
  });
};

/* First sign-in with the emailed temporary password: choose a real one before anything else. */
Pages.setPassword = function (query) {
  if (!State.user) return navigate("#/login");
  const next = safeNext(query.get("next"));
  if (!State.user.must_change_password) return navigate(next);
  authLayout(`<h2>Choose your password</h2>
    <p class="muted">You signed in with the temporary password from your welcome email. Choose your own to continue.</p>
    <form id="f">${passwordFields()}
    <button class="btn primary" style="width:100%;justify-content:center">Save password and continue</button></form>
    <hr><button class="btn link" id="out">Sign out</button>`);
  $("#out").addEventListener("click", async () => {
    try { await api("POST", "/api/auth/logout"); } catch (e) { /* signing out anyway */ }
    State.user = null;
    navigate("#/login");
  });
  $("#f").addEventListener("submit", e => {
    e.preventDefault();
    if (!checkPasswords(e.target)) return;
    withBusy($("#f button"), async () => {
      const r = await api("POST", "/api/auth/set-password", { new_password: e.target.password.value });
      State.user = r.user;
      document.body.innerHTML = "";
      toast("Password saved. Welcome to LawPilot.");
      navigate(next);
    });
  });
};

Pages.invite = async function (query) {
  const token = query.get("token") || "";
  let inv;
  try { inv = await api("GET", `/api/invitations/${encodeURIComponent(token)}`); } catch (e) {
    authLayout(`<div class="banner bad">${esc(e.message)}</div><a href="#/home">Go to LawPilot</a>`);
    return;
  }
  const back = encodeURIComponent(`#/invite?token=${token}`);
  if (!State.user) {
    authLayout(`<h2>Join ${esc(inv.firm_name)}</h2>
      <p>You have been invited to join <strong>${esc(inv.firm_name)}</strong> as <strong>${esc(inv.role)}</strong>.</p>
      <p class="muted">Sign in or create an account with <strong>${esc(inv.email)}</strong> to accept.</p>
      <div class="row"><a class="btn primary" href="#/login?next=${back}">Sign in</a>
      <a class="btn" href="#/register?email=${encodeURIComponent(inv.email)}&next=${back}">Create account</a></div>`);
    return;
  }
  authLayout(`<h2>Join ${esc(inv.firm_name)}</h2>
    <p>Accept the invitation to join <strong>${esc(inv.firm_name)}</strong> as <strong>${esc(inv.role)}</strong>?</p>
    <div class="row"><button class="btn primary" id="acc">Accept invitation</button><a class="btn" href="#/home">Not now</a></div>`);
  $("#acc").addEventListener("click", () => withBusy($("#acc"), async () => {
    await api("POST", "/api/invitations/accept", { token });
    const me = await api("GET", "/api/auth/me");
    State.user = me.user;
    toast(`You joined ${inv.firm_name}.`);
    navigate("#/firm");
  }));
};
