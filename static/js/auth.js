/* Sign in, register, password reset, invitation acceptance. */
"use strict";

function authLayout(inner) {
  document.body.innerHTML = `<div class="auth-theme"><span class="ts-label"></span>${themeSwitchHtml()}</div>
    <div class="auth-wrap"><div class="card auth-card">
    <div class="brand"><div class="brand-mark">L</div><div><div class="brand-name">LawPilot</div>
    <div class="brand-sub">Legal research &amp; practice management</div></div></div>${inner}</div></div>`;
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
      navigate(next);
    });
  });
};

Pages.register = function (query) {
  const next = query.get("next") || "#/home";
  authLayout(`<h2>Create your account</h2>
    <p class="muted">For individual advocates and law-firm members. You can create or join a firm after signing up.</p>
    <form id="f">
      <div class="field"><label for="name">Full name</label><input id="name" name="name" required autocomplete="name"></div>
      <div class="field"><label for="email">Email</label><input id="email" name="email" type="email" required autocomplete="email"
        value="${esc(query.get("email") || "")}"></div>
      <div class="field"><label for="bar">Bar enrolment no. <small>(optional)</small></label><input id="bar" name="bar_enrolment"></div>
      <div class="field"><label for="password">Password</label><input id="password" name="password" type="password" required minlength="10" autocomplete="new-password">
        <div class="hint">At least 10 characters, mixing letters with numbers or symbols.</div></div>
      <button class="btn primary" style="width:100%;justify-content:center">Create account</button></form>
    <hr><a href="#/login">Already have an account? Sign in</a>`);
  $("#f").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#f button"), async () => {
      const r = await api("POST", "/api/auth/register", formObject(e.target));
      State.user = r.user;
      toast(r.user.is_platform_admin ? "Welcome! You are the administrator of this LawPilot server." : "Welcome to LawPilot.");
      navigate(next);
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

Pages.resetPassword = function (query) {
  const token = query.get("token") || "";
  authLayout(`<h2>Choose a new password</h2>
    <form id="f"><div class="field"><label for="password">New password</label><input id="password" name="password" type="password" required minlength="10" autocomplete="new-password"></div>
    <button class="btn primary" style="width:100%;justify-content:center">Set password</button></form>`);
  $("#f").addEventListener("submit", e => {
    e.preventDefault();
    withBusy($("#f button"), async () => {
      await api("POST", "/api/auth/reset-password", { token, password: e.target.password.value });
      toast("Password updated. Please sign in.");
      navigate("#/login");
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
