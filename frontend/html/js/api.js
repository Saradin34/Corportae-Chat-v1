/* ============================================================
   API client — talks to the backend through the nginx /api proxy.
   Uses relative paths so it works on any host/port (no 502 from
   hardcoded localhost). Handles token storage and errors cleanly.
   ============================================================ */
(function () {
  "use strict";

  const TOKEN_KEY = "cc_token";
  const USER_KEY = "cc_user";

  const Store = {
    getToken() { return localStorage.getItem(TOKEN_KEY); },
    setToken(t) { localStorage.setItem(TOKEN_KEY, t); },
    clearToken() { localStorage.removeItem(TOKEN_KEY); },
    getUser() {
      try { return JSON.parse(localStorage.getItem(USER_KEY) || "null"); }
      catch (e) { return null; }
    },
    setUser(u) { localStorage.setItem(USER_KEY, JSON.stringify(u)); },
    clearUser() { localStorage.removeItem(USER_KEY); },
    clearAll() { this.clearToken(); this.clearUser(); },
  };

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  async function request(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    const token = Store.getToken();
    if (token) headers["Authorization"] = "Bearer " + token;

    let res;
    try {
      res = await fetch("/api" + path, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (networkErr) {
      // fetch itself failed (server down / network) — friendly message
      throw new ApiError("Сервер недоступен. Проверьте подключение.", 0);
    }

    // 401 -> token invalid, force logout (but not on the login/register/SSO calls)
    if (res.status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/auth/register") && !path.startsWith("/auth/sso")) {
      Store.clearAll();
      if (window.Router) window.Router.navigate("/login");
    }

    let data = null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      try { data = await res.json(); } catch (e) { data = null; }
    }

    if (!res.ok) {
      let msg = "Ошибка запроса";
      if (data && data.detail) {
        msg = typeof data.detail === "string"
          ? data.detail
          : (Array.isArray(data.detail) && data.detail[0] ? (data.detail[0].msg || msg) : msg);
      } else if (res.status === 502 || res.status === 503 || res.status === 504) {
        msg = "Сервер запускается, попробуйте через пару секунд…";
      }
      throw new ApiError(msg, res.status);
    }
    return data;
  }

  // Multipart upload with progress. `onProgress` receives 0..100.
  async function upload(path, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api" + path);
      const token = Store.getToken();
      if (token) xhr.setRequestHeader("Authorization", "Bearer " + token);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && typeof onProgress === "function") {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) {}
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
        } else {
          const msg = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : "Ошибка загрузки")
            : (xhr.status === 413 ? "Файл слишком большой" : "Ошибка загрузки");
          reject(new ApiError(msg, xhr.status));
        }
      };
      xhr.onerror = () => reject(new ApiError("Сеть недоступна при загрузке", 0));
      const fd = new FormData();
      fd.append("file", file, file.name || "file");
      xhr.send(fd);
    });
  }

  const API = {
    Store,
    ApiError,
    upload,
    uploadFile: (file, onProgress) => upload("/uploads/file", file, onProgress),
    uploadAvatar: (file, onProgress) => upload("/uploads/avatar", file, onProgress),
    uploadChatAvatar: (chatId, file, onProgress) => upload("/uploads/chat/" + chatId + "/avatar", file, onProgress),
    // auth
    register: (d) => request("POST", "/auth/register", d),
    login: (d) => request("POST", "/auth/login", d),
    sso: async () => {
      const res = await fetch("/api/auth/sso", {
        method: "GET",
        credentials: "include",
        headers: { "Accept": "application/json" },
      });
      if (!res.ok) throw new ApiError("SSO failed", res.status);
      return res.json();
    },
    authConfig: () => request("GET", "/auth/config"),
    me: () => request("GET", "/auth/me"),
    // users
    searchUsers: (q) => request("GET", "/users?q=" + encodeURIComponent(q || "")),
    listUsers: (q, limit) => request("GET", "/users?q=" + encodeURIComponent(q || "") + "&limit=" + (limit || 1000)),
    myPermissions: () => request("GET", "/users/me/permissions"),
    getUser: (id) => request("GET", "/users/" + id),
    updateMe: (d) => request("PATCH", "/users/me", d),
    changePassword: (d) => request("POST", "/auth/change-password", d),
    // chats
    listChats: () => request("GET", "/chats"),
    createChat: (d) => request("POST", "/chats", d),
    getChat: (id) => request("GET", "/chats/" + id),
    updateChat: (id, d) => request("PATCH", "/chats/" + id, d),
    deleteChat: (id) => request("DELETE", "/chats/" + id),
    addMembers: (id, ids) => request("POST", "/chats/" + id + "/members", { member_ids: ids }),
    removeMember: (id, mid) => request("DELETE", "/chats/" + id + "/members/" + mid),
    toggleMemberAdmin: (id, mid) => request("POST", "/chats/" + id + "/members/" + mid + "/admin"),
    toggleMute: (id) => request("POST", "/chats/" + id + "/mute"),
    markRead: (id) => request("POST", "/chats/" + id + "/read"),
    // messages — supports pagination via `before` (older) and `limit`
    listMessages: (chatId, before, limit) => {
      const params = [];
      if (before) params.push("before=" + before);
      if (limit) params.push("limit=" + limit);
      const qs = params.length ? "?" + params.join("&") : "";
      return request("GET", "/chats/" + chatId + "/messages" + qs);
    },
    sendMessage: (chatId, d) => request("POST", "/chats/" + chatId + "/messages", d),
    editMessage: (chatId, mid, d) => request("PATCH", "/chats/" + chatId + "/messages/" + mid, d),
    deleteMessage: (chatId, mid) => request("DELETE", "/chats/" + chatId + "/messages/" + mid),
    reactMessage: (chatId, mid, emoji) => request("POST", "/chats/" + chatId + "/messages/" + mid + "/react", { emoji }),
    pinMessage: (chatId, mid) => request("POST", "/chats/" + chatId + "/messages/" + mid + "/pin"),
    listPinned: (chatId) => request("GET", "/chats/" + chatId + "/messages/pinned"),
    searchMessages: (chatId, q) => request("GET", "/chats/" + chatId + "/messages/search?q=" + encodeURIComponent(q)),
    forwardMessage: (chatId, mid, toChatId) => request("POST", "/chats/" + chatId + "/messages/forward", { message_id: mid, to_chat_id: toChatId }),
    // admin
    adminStats: () => request("GET", "/admin/stats"),
    adminUsers: (q) => request("GET", "/admin/users?q=" + encodeURIComponent(q || "")),
    adminToggleActive: (id) => request("POST", "/admin/users/" + id + "/toggle-active"),
    adminSetRole: (id, role) => request("POST", "/admin/users/" + id + "/role?role=" + role),
    adminResetPassword: (id, pwd) => request("POST", "/admin/users/" + id + "/reset-password?new_password=" + encodeURIComponent(pwd)),
    adminDeleteUser: (id) => request("DELETE", "/admin/users/" + id),
    adminChats: () => request("GET", "/admin/chats"),
    adminDeleteChat: (id) => request("DELETE", "/admin/chats/" + id),
    adminAudit: () => request("GET", "/admin/audit"),
    adminBroadcast: (text) => request("POST", "/admin/broadcast", { text }),
    // admin: groups
    adminGroups: () => request("GET", "/admin/groups"),
    adminCreateGroup: (d) => request("POST", "/admin/groups", d),
    adminUpdateGroup: (id, d) => request("PATCH", "/admin/groups/" + id, d),
    adminDeleteGroup: (id) => request("DELETE", "/admin/groups/" + id),
    adminAssignGroup: (userIds, groupId) => request("POST", "/admin/groups/assign", { user_ids: userIds, group_id: groupId }),
    adminAdSearchGroups: (q) => request("GET", "/admin/groups/ad/search?q=" + encodeURIComponent(q || "")),
    adminAdImportGroup: (dn, name) => request("POST", "/admin/groups/ad/import", { dn, name: name || "" }),
    // admin: server settings
    adminGetSettings: () => request("GET", "/admin/settings"),
    adminUpdateSettings: (d) => request("PATCH", "/admin/settings", d),
  };

  window.API = API;

  // ---- Toast helper (global) ----
  window.toast = function (message, type) {
    const c = document.getElementById("toast-container");
    if (!c) return;
    const el = document.createElement("div");
    el.className = "toast" + (type ? " " + type : "");
    el.textContent = message;
    c.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .3s";
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 300);
    }, 3000);
  };
})();
