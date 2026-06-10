/* ============================================================
   Tiny hash-based SPA router.
   Routes are like #/login, #/chats, #/chats/5, #/admin, #/settings.
   Hash routing avoids needing nginx try_files rewrites and never 404s.
   ============================================================ */
(function () {
  "use strict";

  const routes = [];
  let notFoundHandler = null;
  let guard = null;

  function add(pattern, handler) {
    // pattern: "/chats/:id" -> regex
    const keys = [];
    const regex = new RegExp(
      "^" +
        pattern.replace(/:[^/]+/g, (m) => {
          keys.push(m.slice(1));
          return "([^/]+)";
        }) +
        "$"
    );
    routes.push({ regex, keys, handler });
  }

  function currentPath() {
    let h = window.location.hash || "#/";
    return h.slice(1) || "/";
  }

  async function resolve() {
    const path = currentPath();

    if (guard) {
      const redirect = guard(path);
      if (redirect && redirect !== path) {
        navigate(redirect, true);
        return;
      }
    }

    for (const r of routes) {
      const m = path.match(r.regex);
      if (m) {
        const params = {};
        r.keys.forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1])));
        await r.handler(params);
        return;
      }
    }
    if (notFoundHandler) notFoundHandler(path);
  }

  function navigate(path, replace) {
    const target = "#" + path;
    if (replace) {
      window.location.replace(target);
    } else if (("#" + currentPath()) === target) {
      // same path -> force re-resolve
      resolve();
    } else {
      window.location.hash = target;
    }
  }

  window.addEventListener("hashchange", resolve);

  window.Router = {
    add,
    navigate,
    currentPath,
    start: resolve,
    setGuard: (g) => (guard = g),
    notFound: (h) => (notFoundHandler = h),
  };
})();
