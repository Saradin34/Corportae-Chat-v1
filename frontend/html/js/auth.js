/* ============================================================
   Auth views: Login & Register.
   Renders into #app. Clean event handling — no reliance on
   event.target internals (fixes the old "closest is not a function").
   ============================================================ */
(function () {
  "use strict";

  const app = () => document.getElementById("app");

  // Cached auth config (which sign-in methods are enabled).
  let authConfig = { local_auth: true, ldap_enabled: false, ldap_domain: "", sso_enabled: false, sso_negotiate: false, sso_allow_proxy: false };

  async function loadAuthConfig() {
    try { authConfig = await API.authConfig(); } catch (e) { /* keep defaults */ }
    return authConfig;
  }

  function showError(msg) {
    const box = document.getElementById("form-error");
    if (!box) return;
    box.textContent = msg;
    box.classList.add("show");
  }
  function clearError() {
    const box = document.getElementById("form-error");
    if (box) box.classList.remove("show");
  }

  function doSSO() {
    // Native browser navigation so the browser handles SPNEGO/NTLM
    // negotiation automatically (works in Chrome, Edge, Firefox, IE).
    window.location.href = "/api/auth/sso";
  }

  async function trySSO() {
    // Used for proxy-SSO (auto-try) when a reverse proxy injects
    // X-Remote-User. For Negotiate mode we use doSSO() instead.
    try {
      const data = await API.sso();
      API.Store.setToken(data.access_token);
      API.Store.setUser(data.user);
      window.toast("Добро пожаловать, " + (data.user.full_name || data.user.username) + "!", "success");
      Router.navigate("/chats");
      return true;
    } catch (e) {
      if (e && e.status === 401) {
        console.log("SSO: no ticket yet (401) — waiting for user action");
      } else {
        console.log("SSO unavailable", e);
      }
    }
    return false;
  }

  async function autoTrySSO() {
    const status = document.getElementById("sso-status");
    if (status) status.style.display = "block";
    const ok = await trySSO();
    if (!ok && status) {
      status.style.display = "none";
      // if auto-try failed, show the button so user can click explicitly
      const btn = document.getElementById("sso-btn");
      if (btn) btn.style.display = "";
    }
  }

  async function renderLogin() {
    await loadAuthConfig();
    const adOnly = authConfig.ldap_enabled && !authConfig.local_auth;
    const adHybrid = authConfig.ldap_enabled && authConfig.local_auth;

    const userLabel = authConfig.ldap_enabled
      ? "Учётная запись домена" + (authConfig.ldap_domain ? " (@" + authConfig.ldap_domain + ")" : "")
      : "Имя пользователя или Email";

    const adBadge = authConfig.ldap_enabled
      ? `<div class="ad-badge">🔐 Вход через Active Directory${adHybrid ? " или локальный аккаунт" : ""}</div>`
      : "";

    const ssoEnabled = !!authConfig.sso_enabled;
    const ssoTrying = ssoEnabled
      ? `<div class="ad-badge" id="sso-status" style="display:none">🔑 Проверка единого входа Windows…</div>`
      : "";
    const ssoBtn = ssoEnabled
      ? `<button type="button" class="btn-primary" id="sso-btn" style="margin-bottom:12px;background:#107c10">🔐 Вход через Windows (SSO)</button>`
      : "";

    const registerLink = authConfig.local_auth
      ? `<p class="auth-switch">Нет аккаунта? <a id="to-register">Зарегистрироваться</a></p>`
      : (adOnly ? `<p class="auth-switch ad-note">Регистрация управляется администратором домена</p>` : "");

    app().innerHTML = `
      <div class="auth-wrap">
        <div class="auth-card">
          <div class="auth-logo">C</div>
          <h1>Corporate Chat</h1>
          <p class="subtitle">${adOnly ? "Вход с доменной учётной записью" : "Войдите в свой аккаунт"}</p>
          ${ssoTrying}
          ${adBadge}
          <div class="form-error" id="form-error"></div>
          ${ssoBtn}
          <form id="login-form" autocomplete="on">
            <div class="field">
              <label>${userLabel}</label>
              <input type="text" id="login-username" required autofocus placeholder="${authConfig.ldap_enabled ? "напр. ivanov" : ""}" />
            </div>
            <div class="field">
              <label>Пароль</label>
              <input type="password" id="login-password" required />
            </div>
            <button type="submit" class="btn-primary" id="login-submit">${adOnly ? "Войти через AD" : "Войти"}</button>
          </form>
          ${registerLink}
        </div>
      </div>`;

    const toReg = document.getElementById("to-register");
    if (toReg) toReg.addEventListener("click", () => Router.navigate("/register"));
    document.getElementById("login-form").addEventListener("submit", handleLogin);

    const ssoBtnEl = document.getElementById("sso-btn");
    if (ssoBtnEl) ssoBtnEl.addEventListener("click", doSSO);

    if (ssoEnabled) {
      autoTrySSO();
    }
  }

  async function handleLogin(e) {
    e.preventDefault();
    clearError();
    const username = document.getElementById("login-username").value.trim();
    const password = document.getElementById("login-password").value;
    if (!username || !password) {
      showError("Заполните все поля");
      return;
    }
    const btn = document.getElementById("login-submit");
    btn.disabled = true;
    btn.textContent = "Вход…";
    try {
      const res = await API.login({ username, password });
      API.Store.setToken(res.access_token);
      API.Store.setUser(res.user);
      window.toast("Добро пожаловать, " + (res.user.full_name || res.user.username) + "!", "success");
      Router.navigate("/chats");
    } catch (err) {
      showError(err.message || "Ошибка входа");
      btn.disabled = false;
      btn.textContent = "Войти";
    }
  }

  async function renderRegister() {
    await loadAuthConfig();
    if (!authConfig.local_auth) {
      window.toast("Локальная регистрация отключена. Вход только через Active Directory.", "error");
      Router.navigate("/login");
      return;
    }
    app().innerHTML = `
      <div class="auth-wrap">
        <div class="auth-card">
          <div class="auth-logo">C</div>
          <h1>Создать аккаунт</h1>
          <p class="subtitle">Присоединяйтесь к Corporate Chat</p>
          <div class="form-error" id="form-error"></div>
          <form id="register-form" autocomplete="on">
            <div class="field">
              <label>Имя пользователя</label>
              <input type="text" id="reg-username" required placeholder="латиница, цифры, _" />
            </div>
            <div class="field">
              <label>Полное имя</label>
              <input type="text" id="reg-fullname" placeholder="Иван Иванов" />
            </div>
            <div class="field">
              <label>Email</label>
              <input type="email" id="reg-email" required />
            </div>
            <div class="field">
              <label>Пароль</label>
              <input type="password" id="reg-password" required placeholder="минимум 6 символов" />
            </div>
            <button type="submit" class="btn-primary" id="reg-submit">Зарегистрироваться</button>
          </form>
          <p class="auth-switch">Уже есть аккаунт? <a id="to-login">Войти</a></p>
        </div>
      </div>`;

    document.getElementById("to-login").addEventListener("click", () => Router.navigate("/login"));
    document.getElementById("register-form").addEventListener("submit", handleRegister);
  }

  async function handleRegister(e) {
    e.preventDefault();
    clearError();
    const username = document.getElementById("reg-username").value.trim();
    const full_name = document.getElementById("reg-fullname").value.trim();
    const email = document.getElementById("reg-email").value.trim();
    const password = document.getElementById("reg-password").value;

    if (!username || !email || !password) {
      showError("Заполните обязательные поля");
      return;
    }
    if (!/^[A-Za-z0-9_]{3,64}$/.test(username)) {
      showError("Имя пользователя: 3-64 символа, латиница, цифры, _");
      return;
    }
    if (password.length < 6) {
      showError("Пароль должен быть не короче 6 символов");
      return;
    }

    const btn = document.getElementById("reg-submit");
    btn.disabled = true;
    btn.textContent = "Создание…";
    try {
      const res = await API.register({ username, email, password, full_name });
      API.Store.setToken(res.access_token);
      API.Store.setUser(res.user);
      window.toast("Аккаунт создан!", "success");
      Router.navigate("/chats");
    } catch (err) {
      showError(err.message || "Ошибка регистрации");
      btn.disabled = false;
      btn.textContent = "Зарегистрироваться";
    }
  }

  window.AuthViews = { renderLogin, renderRegister };
})();
