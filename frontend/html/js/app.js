/* ============================================================
   App bootstrap: theme, routing, auth guard.
   Note: emoji picker is initialised ONLY when a chat is opened,
   never on page load — this avoids the previous crash where
   initEmojiPicker ran against elements that didn't exist yet.
   ============================================================ */
(function () {
  "use strict";

  // Restore theme
  const savedTheme = localStorage.getItem("cc_theme");
  if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);

  // Apply saved user preferences (font size / compact) as early as possible.
  try {
    const prefs = JSON.parse(localStorage.getItem("cc_prefs") || "{}");
    const fontStacks = {
      segoe: '"Segoe UI", "Segoe UI Variable", system-ui, -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif',
      system: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif',
      inter: 'Inter, "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif',
      roboto: 'Roboto, "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, Arial, sans-serif',
    };
    document.documentElement.style.setProperty("--app-font", fontStacks[prefs.fontFamily] || fontStacks.segoe);
    if (prefs.fontSize) document.documentElement.style.setProperty("--msg-font-size", prefs.fontSize + "px");
    if (prefs.compact) document.body.classList.add("compact-mode");
    if (prefs.bubbleStyle === "square") document.body.classList.add("bubbles-square");
    if (prefs.showSidebar === false) document.body.classList.add("hide-right-sidebar");
  } catch (e) {}

  // If "Автоматическая авторизация" is OFF, drop the session on a brand-new
  // app launch (a fresh tab/process). We use sessionStorage as the "this
  // session is alive" marker, which is cleared when the app/tab fully closes.
  try {
    const prefs = JSON.parse(localStorage.getItem("cc_prefs") || "{}");
    if (prefs.autoLogin === false && !sessionStorage.getItem("cc_session_alive")) {
      localStorage.removeItem("cc_token");
      localStorage.removeItem("cc_user");
    }
    sessionStorage.setItem("cc_session_alive", "1");
  } catch (e) {}

  function isAuthed() {
    return !!API.Store.getToken();
  }

  // Auth guard — redirect rules
  Router.setGuard(function (path) {
    const authed = isAuthed();
    const isAuthPage = path === "/login" || path === "/register";
    if (!authed && !isAuthPage) return "/login";
    if (authed && isAuthPage) return "/chats";
    if (path === "/") return authed ? "/chats" : "/login";
    return null;
  });

  // Routes
  Router.add("/login", () => AuthViews.renderLogin());
  Router.add("/register", () => AuthViews.renderRegister());
  Router.add("/chats", () => ChatView.mount());
  Router.add("/chats/:id", (params) => ChatView.openChat(parseInt(params.id, 10)));
  Router.add("/admin", () => AdminView.mount());

  Router.notFound(() => {
    Router.navigate(isAuthed() ? "/chats" : "/login");
  });

  // Validate token on startup (best-effort), then start the router.
  async function boot() {
    if (isAuthed()) {
      try {
        const me = await API.me();      // refresh user, also validates token
        API.Store.setUser(me);
      } catch (e) {
        // token invalid -> cleared inside api.js for 401
        if (e.status === 401) API.Store.clearAll();
      }
    }
    if (!window.location.hash) {
      window.location.hash = "#/";
    }
    Router.start();
  }

  // Register the same-origin service worker for reliable notifications.
  // Requires a secure context (HTTPS or localhost) — silently skipped otherwise.
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* non-fatal */ });
    });
  }

  let booted = false;
  function bootOnce() { if (booted) return; booted = true; boot(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootOnce);
  } else {
    bootOnce();
  }
})();
