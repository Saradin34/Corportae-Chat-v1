/* ============================================================
   Chat view: sidebar + chat area + composer.
   Real-time via WebSocket. Telegram-style features:
   reactions, pin, forward, reply, edit, in-chat search,
   unread badges, mute, group management.
   ============================================================ */
(function () {
  "use strict";

  const app = () => document.getElementById("app");
  // Desktop (Electron) bridge — present only inside the desktop app.
  const DESKTOP = (typeof window !== "undefined" && window.CorporateChatDesktop) || null;
  const ISDESKTOP = !!DESKTOP;

  const State = {
    chats: [],
    activeChatId: null,
    activeChat: null,
    messages: [],
    // --- lazy-load pagination (Telegram-style infinite scroll up) ---
    pageSize: 40,         // messages per page
    hasMoreOlder: false,  // true if older messages exist on the server
    loadingOlder: false,  // guard against parallel fetches
    replyTo: null,
    editingId: null,
    forwardMsgId: null,
    pendingAttachments: [],  // [{tempId, file, kind, status, progress, result}]
    allUsers: [],
    rsRefreshTimer: null,
    ws: null,
    wsReconnectTimer: null,
    wsReconnectDelay: 1000,
    me: null,
    statusMap: {},        // userId -> "online" | "away" | "offline"
    myStatus: "online",
    idleBound: false,
    emojiDocClick: null,
    typingClear: null,
    // group permissions for the current user (resolved server-side). Default
    // permissive so the UI isn't crippled before the fetch completes.
    perms: {
      can_send_messages: true, can_create_private: true, can_create_groups: true,
      can_send_files: true, can_send_images: true, can_forward: true,
      can_pin: true, can_edit_own: true, can_delete_own: true, can_react: true,
    },
  };

  // Convenience: check a permission flag.
  function can(flag) { return State.perms ? State.perms[flag] !== false : true; }

  async function loadPermissions() {
    try {
      const p = await API.myPermissions();
      State.perms = p;
      applyPermsToUI();
    } catch (e) { /* keep permissive defaults */ }
  }

  // Show/hide global UI affordances based on permissions (chat-level buttons
  // are gated where they're rendered via can(...)).
  function applyPermsToUI() {
    const newChat = document.getElementById("new-chat-btn");
    // hide "new chat" pencil entirely only if the user can't create anything
    if (newChat) newChat.style.display = (can("can_create_private") || can("can_create_groups")) ? "" : "none";
    // composer (if a chat is open)
    const composerRow = document.querySelector(".composer-row");
    if (composerRow) {
      const input = document.getElementById("composer-input");
      const sendBtn = document.getElementById("send-btn");
      const attachBtn = document.getElementById("attach-btn");
      const allowSend = can("can_send_messages");
      const allowAttach = can("can_send_files") || can("can_send_images");
      if (input) {
        input.disabled = !allowSend;
        input.placeholder = allowSend ? "Сообщение..." : "Ваша группа не может отправлять сообщения";
      }
      if (sendBtn) sendBtn.style.display = allowSend ? "" : "none";
      if (attachBtn) attachBtn.style.display = allowAttach ? "" : "none";
    }
  }

  const QUICK_REACTIONS = ["👍", "❤️", "😂", "🔥", "👏", "😮", "😢", "🙏"];

  // ---------- User preferences (local, per-device) ----------
  const Prefs = {
    KEY: "cc_prefs",
    _cache: null,
    _defaults: {
      // Appearance
      fontSize: 15,
      compact: false,
      bubbleStyle: "rounded", // rounded | square
      showSidebar: true,      // right-side online users panel
      // Chat
      enterToSend: true,
      time24: true,           // 24h vs 12h time format
      spellcheck: true,       // textarea spellcheck
      // Notifications
      sound: true,
      notify: false,
      notifyPreview: true,    // show message text in notifications
      // General / App (desktop)
      keepInTray: true,       // keep running after [X]
      autostart: false,       // launch on OS login
      awayOnIdle: true,       // set "away" after 15 min idle
      // Connection
      autoLogin: true,        // keep me signed in between sessions
      preferSSO: false,       // prefer SSO sign-in button
      noProxy: false,         // do not use a proxy (desktop)
    },
    all() {
      if (this._cache) return this._cache;
      try { this._cache = Object.assign({}, this._defaults, JSON.parse(localStorage.getItem(this.KEY) || "{}")); }
      catch (e) { this._cache = Object.assign({}, this._defaults); }
      return this._cache;
    },
    get(k) { return this.all()[k]; },
    set(k, v) {
      const a = this.all(); a[k] = v;
      this._cache = a;
      try { localStorage.setItem(this.KEY, JSON.stringify(a)); } catch (e) {}
    },
    invalidate() { this._cache = null; },
  };

  function applyPrefs() {
    const p = Prefs.all();
    document.documentElement.style.setProperty("--msg-font-size", p.fontSize + "px");
    document.body.classList.toggle("compact-mode", !!p.compact);
    document.body.classList.toggle("bubbles-square", p.bubbleStyle === "square");
    document.body.classList.toggle("hide-right-sidebar", !p.showSidebar);
    const ta = document.getElementById("composer-input");
    if (ta) ta.spellcheck = !!p.spellcheck;
  }

  // ---------- Archive (localStorage-backed, like Telegram archive) ----------

  const EMOJI = {
    "😀": ["😀","😃","😄","😁","😆","😅","😂","🤣","😊","😇","🙂","🙃","😉","😌","😍","🥰","😘","😗","😙","😚","😋","😛","😝","😜","🤪","🤨","🧐","🤓","😎","🥳","😏","😒","😞","😔","😟","😕","🙁","☹️","😣","😖","😫","😩","🥺","😢","😭","😤","😠","😡","🤬","🤯","😳","🥵","🥶","😱","😨","😰","😥","😓","🤗","🤔","🤭","🤫","🤥","😶","😐","😑","😬","🙄","😯","😦","😧","😮","😲","🥱","😴","🤤","😪","😵","🤐","🥴","🤢","🤮","🤧","😷","🤒","🤕"],
    "👍": ["👍","👎","👌","✌️","🤞","🤟","🤘","🤙","👈","👉","👆","👇","☝️","✋","🤚","🖐️","🖖","👋","🤝","🙏","✊","👊","🤛","🤜","👏","🙌","👐","🤲","💪","🦾","👀","👁️","❤️","🧡","💛","💚","💙","💜","🖤","🤍","🤎","💔","❣️","💕","💞","💓","💗","💖","💘","💝","🔥","⭐","🌟","✨","⚡","💯","✅","❌","❓","❗"],
    "🎉": ["🎉","🎊","🎈","🎂","🎁","🎀","🎄","🎃","🎆","🎇","🧨","🥂","🍾","🍻","🍺","🍷","☕","🍵","🍰","🧁","🍩","🍪","🍫","🍬","🍭","🍕","🍔","🍟","🌭","🍿","🥪","🌮","🌯","🥗","🍜","🍲","🍱","🍣","🍙","🍚","🍛","🥘","🍳","🥞","🧇","🥓","🍗","🍖","🍇","🍉","🍊","🍋","🍌","🍍","🥭","🍎","🍏","🍓","🫐","🍑","🍒"],
    "🚀": ["🚀","✈️","🚁","🚂","🚗","🚕","🚙","🚌","🏎️","🏍️","🚲","⛵","🚤","🛳️","⚓","🏠","🏢","🏬","🏭","🏯","🏰","💻","🖥️","⌨️","🖱️","📱","📞","☎️","📷","📹","🎥","💡","🔦","📚","📖","📝","✏️","📌","📍","📎","🔗","🔒","🔓","🔑","🔨","🛠️","⚙️","🧲","💰","💳","💎","⏰","⏳","📅","📆","🌍","🌎","🌏","🗺️","🧭","⚽","🏀","🏈","🎾","🎮"],
  };

  // ---------- WebSocket ----------
  function connectWS() {
    const token = API.Store.getToken();
    if (!token) return;
    if (State.ws && (State.ws.readyState === 0 || State.ws.readyState === 1)) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = proto + "://" + window.location.host + "/ws?token=" + encodeURIComponent(token);
    const ws = new WebSocket(url);
    State.ws = ws;
    ws.onopen = () => {
      State.wsReconnectDelay = 1000;
      State.pingInterval = setInterval(() => {
        if (ws.readyState === 1) ws.send(JSON.stringify({ type: "ping" }));
      }, 25000);
    };
    ws.onmessage = (ev) => {
      let data; try { data = JSON.parse(ev.data); } catch (e) { return; }
      handleWSMessage(data);
    };
    ws.onclose = () => {
      clearInterval(State.pingInterval);
      State.ws = null;
      if (API.Store.getToken()) {
        clearTimeout(State.wsReconnectTimer);
        State.wsReconnectTimer = setTimeout(connectWS, State.wsReconnectDelay);
        State.wsReconnectDelay = Math.min(State.wsReconnectDelay * 2, 30000);
      }
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} State.ws = null; };
  }

  function disconnectWS() {
    clearInterval(State.pingInterval);
    clearTimeout(State.wsReconnectTimer);
    if (State.ws) { try { State.ws.close(); } catch (e) {} State.ws = null; }
  }

  function handleWSMessage(data) {
    switch (data.type) {
      case "new_message": {
        const m = data.message;
        const mine = m.sender_id === State.me.id;
        if (m.chat_id === State.activeChatId) {
          if (!State.messages.some((x) => x.id === m.id)) {
            const box = document.getElementById("messages");
            const nearBottom = box && (box.scrollHeight - box.scrollTop - box.clientHeight < 150);
            State.messages.push(m);
            appendMessage(m);
            if (nearBottom || mine) scrollMessages();
            else {
              const sb = document.getElementById("scroll-bottom-btn");
              if (sb) sb.classList.add("show");
            }
            API.markRead(State.activeChatId).catch(() => {});
          }
        }
        // notify (sound + browser notification) for messages from others
        if (!mine && !m.is_system) notifyIncoming(m);
        refreshChatList();
        break;
      }
      case "edit_message": {
        const m = data.message;
        if (m.chat_id === State.activeChatId) {
          const i = State.messages.findIndex((x) => x.id === m.id);
          if (i >= 0) { State.messages[i] = m; rerenderMessages(); }
        }
        break;
      }
      case "delete_message": {
        if (data.chat_id === State.activeChatId) {
          const i = State.messages.findIndex((x) => x.id === data.message_id);
          if (i >= 0) { State.messages[i].is_deleted = true; State.messages[i].text = ""; rerenderMessages(); }
        }
        refreshChatList();
        break;
      }
      case "reaction_changed": {
        if (data.chat_id === State.activeChatId) {
          const i = State.messages.findIndex((x) => x.id === data.message_id);
          if (i >= 0) {
            // recompute "reacted" from my id
            data.reactions.forEach((r) => { r.reacted = (r.user_ids || []).indexOf(State.me.id) >= 0; });
            State.messages[i].reactions = data.reactions;
            rerenderMessages();
          }
        }
        break;
      }
      case "pin_changed": {
        if (data.chat_id === State.activeChatId) {
          const i = State.messages.findIndex((x) => x.id === data.message_id);
          if (i >= 0) State.messages[i].is_pinned = data.is_pinned;
          refreshPinnedBar();
          rerenderMessages();
        }
        break;
      }
      case "presence": { updatePresence(data.user_id, data.online, data.status); break; }
      case "typing": { if (data.chat_id === State.activeChatId) showTyping(data.username); break; }
      case "chat_created":
      case "chat_updated": {
        refreshChatList();
        if (data.chat_id === State.activeChatId) reloadActiveChat();
        break;
      }
      case "chat_deleted": {
        refreshChatList();
        if (data.chat_id === State.activeChatId) {
          window.toast("Чат был удалён");
          State.activeChatId = null; State.activeChat = null;
          Router.navigate("/chats");
        }
        break;
      }
      case "broadcast": {
        window.toast("📢 " + data.text, "success");
        break;
      }
      case "force_logout": {
        window.toast(data.reason || "Сессия завершена", "error");
        logout();
        break;
      }
    }
  }

  async function reloadActiveChat() {
    try {
      State.activeChat = await API.getChat(State.activeChatId);
      updateChatHeader();
    } catch (e) {}
  }

  // ---------- Layout ----------
  function renderLayout() {
    State.me = API.Store.getUser();
    app().innerHTML = `
      <div class="app-layout" id="app-layout">
        <aside class="sidebar">
          <div class="sidebar-header">
            <button class="menu-btn" id="menu-btn" title="Меню">☰</button>
            <div class="search-box"><input type="text" id="search-input" placeholder="Поиск" /></div>
            <button class="icon-btn" id="contacts-btn" title="Контактная книга">📇</button>
            <button class="icon-btn" id="new-chat-btn" title="Новый чат">✏️</button>
          </div>
          <div class="chat-list" id="chat-list"></div>
        </aside>
        <main class="chat-area" id="chat-area">
          <div class="chat-area-empty" id="chat-empty">
            <div class="big-icon">💬</div>
            <div class="empty-pill">Выберите чат, чтобы начать общение</div>
          </div>
        </main>
        <aside class="right-sidebar" id="right-sidebar">
          <div class="rs-header">
            <div class="rs-title">👥 Пользователи <span class="rs-count" id="rs-count"></span></div>
            <input type="text" class="rs-search" id="rs-search" placeholder="Поиск по всем пользователям" />
          </div>
          <div class="rs-list" id="rs-list"></div>
        </aside>
      </div>
      <div class="drawer-overlay" id="drawer-overlay"></div>
      <nav class="drawer" id="drawer">
        <div class="drawer-header">
          <div class="avatar lg" id="drawer-avatar"></div>
          <div class="drawer-name" id="drawer-name"></div>
          <div class="drawer-email" id="drawer-email"></div>
        </div>
        <div class="drawer-menu" id="drawer-menu"></div>
        <div class="drawer-footer">Corporate Chat v2.0</div>
      </nav>
      <div class="modal-overlay" id="modal-overlay"></div>`;

    const me = State.me;
    const dav = document.getElementById("drawer-avatar");
    if (me.avatar_url) {
      dav.style.background = "transparent";
      dav.innerHTML = `<img class="avatar-img" src="${escapeAttr(me.avatar_url)}" alt="" />`;
    } else {
      dav.style.background = me.avatar_color;
      dav.textContent = initials(me.full_name || me.username);
    }
    document.getElementById("drawer-name").textContent = me.full_name || me.username;
    document.getElementById("drawer-email").textContent = me.email;

    let items = `
      <div class="drawer-item" data-action="profile"><span class="di-icon">👤</span> Мой профиль</div>
      <div class="drawer-item" data-action="contacts"><span class="di-icon">📇</span> Контактная книга</div>
      <div class="drawer-item" data-action="newgroup"><span class="di-icon">👥</span> Новая группа</div>
      <div class="drawer-item" data-action="settings"><span class="di-icon">⚙️</span> Настройки</div>
      <div class="drawer-item" data-action="theme"><span class="di-icon">🌓</span> Сменить тему</div>`;
    if (me.role === "admin") items += `<div class="drawer-item" data-action="admin"><span class="di-icon">🛡️</span> Админ-панель</div>`;
    items += `<div class="drawer-item" data-action="logout"><span class="di-icon">🚪</span> Выйти</div>`;
    const menu = document.getElementById("drawer-menu");
    menu.innerHTML = items;
    menu.addEventListener("click", (e) => {
      const item = e.target && typeof e.target.closest === "function" ? e.target.closest(".drawer-item") : null;
      if (!item) return;
      handleDrawerAction(item.getAttribute("data-action"));
    });

    document.getElementById("menu-btn").addEventListener("click", openDrawer);
    document.getElementById("drawer-overlay").addEventListener("click", closeDrawer);
    document.getElementById("contacts-btn").addEventListener("click", openContactsModal);
    document.getElementById("new-chat-btn").addEventListener("click", () => openNewChatModal(false));

    const search = document.getElementById("search-input");
    let st = null;
    search.addEventListener("input", () => { clearTimeout(st); st = setTimeout(() => doSidebarSearch(search.value.trim()), 250); });

    // right sidebar: search all users (online + offline)
    const rsSearch = document.getElementById("rs-search");
    let rst = null;
    if (rsSearch) rsSearch.addEventListener("input", () => {
      clearTimeout(rst);
      rst = setTimeout(() => loadRightSidebar(rsSearch.value.trim()), 250);
    });
    loadRightSidebar("");
  }

  // ---------- Right sidebar (online users + search) ----------
  async function loadRightSidebar(q) {
    const list = document.getElementById("rs-list");
    const countEl = document.getElementById("rs-count");
    if (!list) return;
    let users = [];
    try { users = await API.searchUsers(q || ""); } catch (e) { return; }
    State.allUsers = users;
    const online = users.filter((u) => u.is_online);
    const offline = users.filter((u) => !u.is_online);
    if (countEl) countEl.textContent = "· " + online.length + " онлайн";

    let html = "";
    if (online.length) {
      html += `<div class="rs-section">В сети — ${online.length}</div>`;
      html += online.map(rsUserHtml).join("");
    }
    if (offline.length) {
      html += `<div class="rs-section">Не в сети — ${offline.length}</div>`;
      html += offline.map((u) => rsUserHtml(u, true)).join("");
    }
    if (!html) html = `<div class="list-empty">Пользователи не найдены</div>`;
    list.innerHTML = html;
    // click on the user row (avatar/name) -> start a private chat
    list.querySelectorAll(".rs-user-main").forEach((el) =>
      el.addEventListener("click", () => startPrivateChat(parseInt(el.closest("[data-uid]").getAttribute("data-uid"), 10))));
    // click on the ⓘ button -> open the profile card (don't start a chat)
    list.querySelectorAll(".rs-info-btn").forEach((b) =>
      b.addEventListener("click", (e) => { e.stopPropagation(); openUserCard(parseInt(b.getAttribute("data-info"), 10)); }));
  }

  function rsUserHtml(u, offline) {
    const away = u.is_online && State.statusMap[u.id] === "away";
    const dot = u.is_online ? `<span class="online-dot${away ? " away" : ""}"></span>` : "";
    // Under each user: only email + phone (when present).
    const lines = [];
    if (u.email) lines.push(`<div class="rs-line" title="Почта"><a href="mailto:${escapeAttr(u.email)}" onclick="event.stopPropagation()">${escapeHtml(u.email)}</a></div>`);
    if (u.phone) lines.push(`<div class="rs-line" title="Телефон"><a href="tel:${escapeAttr(u.phone)}" onclick="event.stopPropagation()">${escapeHtml(u.phone)}</a></div>`);
    const contact = lines.length ? `<div class="rs-contact">${lines.join("")}</div>` : "";
    return `<div class="rs-user ${offline ? "offline" : ""}" data-uid="${u.id}">
      <div class="rs-user-row">
        <div class="rs-user-main" title="Написать ${escapeAttr(u.full_name || u.username)}">
          ${avatarHtml({ url: u.avatar_url, color: u.avatar_color, name: u.full_name || u.username, size: "sm", extra: dot })}
          <div class="rs-namewrap">
            <div class="rs-name">${escapeHtml(u.full_name || u.username)}</div>
            ${contact}
          </div>
        </div>
        <button class="rs-info-btn" data-info="${u.id}" title="Профиль">ⓘ</button>
      </div>
    </div>`;
  }

  function handleDrawerAction(a) {
    closeDrawer();
    if (a === "logout") return logout();
    if (a === "theme") return toggleTheme();
    if (a === "admin") return Router.navigate("/admin");
    if (a === "settings") return openSettingsModal();
    if (a === "profile") return openProfileModal();
    if (a === "contacts") return openContactsModal();
    if (a === "newgroup") return openNewChatModal(true);
  }

  // ---------- Contact book (all company users) ----------
  async function openContactsModal() {
    const overlay = document.getElementById("modal-overlay");
    overlay.innerHTML = `
      <div class="modal modal-lg">
        <div class="modal-header">
          <h2>📇 Контактная книга</h2>
          <button class="modal-close">✕</button>
        </div>
        <div class="modal-body">
          <div class="field"><input type="text" id="contacts-search" placeholder="Поиск по имени, логину или e-mail..." autofocus /></div>
          <div class="contacts-count" id="contacts-count"></div>
          <div class="contacts-list" id="contacts-list"><div class="list-empty">Загрузка…</div></div>
        </div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

    const searchInput = document.getElementById("contacts-search");
    const listEl = document.getElementById("contacts-list");
    const countEl = document.getElementById("contacts-count");

    async function render(q) {
      let users = [];
      try { users = await API.listUsers(q, 1000); } catch (e) { listEl.innerHTML = `<div class="list-empty">Не удалось загрузить список</div>`; return; }
      if (!users.length) { listEl.innerHTML = `<div class="list-empty">Пользователи не найдены</div>`; countEl.textContent = ""; return; }
      const online = users.filter((u) => u.is_online).length;
      countEl.textContent = `Всего: ${users.length} · в сети: ${online}`;

      // group alphabetically by first letter of the display name
      const groups = {};
      users.forEach((u) => {
        const name = (u.full_name || u.username || "").trim();
        const letter = (name[0] || "#").toUpperCase();
        (groups[letter] = groups[letter] || []).push(u);
      });
      const letters = Object.keys(groups).sort((a, b) => a.localeCompare(b, "ru"));
      listEl.innerHTML = letters.map((L) => `
        <div class="contacts-letter">${escapeHtml(L)}</div>
        ${groups[L].map(contactRowHtml).join("")}
      `).join("");

      listEl.querySelectorAll("[data-uid]").forEach((el) => {
        el.querySelector(".contact-msg").addEventListener("click", (ev) => {
          ev.stopPropagation();
          close();
          startPrivateChat(parseInt(el.getAttribute("data-uid"), 10));
        });
        el.addEventListener("click", () => {
          close();
          openUserCard(parseInt(el.getAttribute("data-uid"), 10));
        });
      });
    }

    let t = null;
    searchInput.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => render(searchInput.value.trim()), 250); });
    render("");
  }

  function contactRowHtml(u) {
    const away = u.is_online && State.statusMap[u.id] === "away";
    const dot = u.is_online ? `<span class="online-dot${away ? " away" : ""}"></span>` : "";
    const status = !u.is_online ? "не в сети" : (away ? "не на месте" : "в сети");
    return `<div class="contact-row" data-uid="${u.id}">
      ${avatarHtml({ url: u.avatar_url, color: u.avatar_color, name: u.full_name || u.username, size: "sm", extra: dot })}
      <div class="contact-info">
        <div class="contact-name">${escapeHtml(u.full_name || u.username)}</div>
        <div class="contact-sub">@${escapeHtml(u.username)}${u.email ? " · " + escapeHtml(u.email) : ""} · ${status}</div>
      </div>
      <button class="contact-msg" title="Написать сообщение">💬</button>
    </div>`;
  }

  // Small read-only profile card for a user (opened from the contact book or
  // the right sidebar ⓘ button). Always fetches fresh data so the directory
  // contact fields (title/phone/office) are up to date.
  async function openUserCard(userId) {
    let u = (State.allUsers || []).find((x) => x.id === userId);
    try { const fresh = await API.getUser(userId); if (fresh) u = fresh; } catch (e) {}
    if (!u) return;
    const overlay = document.getElementById("modal-overlay");
    const away = u.is_online && State.statusMap[u.id] === "away";
    const status = !u.is_online ? "не в сети" : (away ? "не на месте" : "в сети");
    const row = (label, val, isLink) => {
      if (!val) return "";
      const content = isLink
        ? `<a href="${escapeAttr(isLink + val)}">${escapeHtml(val)}</a>`
        : escapeHtml(val);
      return `<div class="usercard-row"><span class="uc-label">${label}</span><span class="uc-val">${content}</span></div>`;
    };
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Контакт</h2><button class="modal-close">✕</button></div>
        <div class="modal-body" style="text-align:center">
          <div class="usercard-avatar">${avatarHtml({ url: u.avatar_url, color: u.avatar_color, name: u.full_name || u.username, size: "lg" })}</div>
          <div class="usercard-name">${escapeHtml(u.full_name || u.username)}</div>
          ${u.title ? `<div class="usercard-title">${escapeHtml(u.title)}</div>` : ""}
          <div class="usercard-status">${status}</div>
          <div class="usercard-rows">
            ${row("Логин", "@" + u.username)}
            ${row("Должность", u.title)}
            ${row("E-mail", u.email, "mailto:")}
            ${row("Телефон", u.phone, "tel:")}
            ${row("Кабинет", u.office)}
            ${row("О себе", u.bio)}
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn-primary inline" id="uc-message">Написать сообщение</button>
        </div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.getElementById("uc-message").addEventListener("click", () => { close(); startPrivateChat(u.id); });
  }

  function openDrawer() { document.getElementById("drawer").classList.add("show"); document.getElementById("drawer-overlay").classList.add("show"); }
  function closeDrawer() { document.getElementById("drawer").classList.remove("show"); document.getElementById("drawer-overlay").classList.remove("show"); }

  function logout() {
    disconnectWS();
    API.Store.clearAll();
    window.toast("Вы вышли из аккаунта");
    Router.navigate("/login");
  }

  function toggleTheme() {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("cc_theme", next);
  }

  // ---------- Chat list ----------
  async function loadChats() {
    try { State.chats = await API.listChats(); renderChatList(State.chats); updateUnreadIndicator(); }
    catch (err) { window.toast(err.message, "error"); }
  }
  async function refreshChatList() {
    try { State.chats = await API.listChats(); renderChatList(State.chats); updateUnreadIndicator(); } catch (e) {}
  }

  /* ---------- Unread indicator (taskbar / tab highlight) ----------
     When there are unread messages, we:
       • prefix the browser-tab title with a (N) badge,
       • swap the favicon for a red-dotted one,
       • in the desktop (Electron) app, set an overlay badge + flash the
         taskbar button so the window stands out in the Start bar. */
  const _baseTitle = "Corporate Chat";
  let _lastUnread = -1;
  function totalUnread() {
    return (State.chats || []).reduce((sum, c) => sum + (c.is_muted ? 0 : (c.unread || 0)), 0);
  }
  function updateUnreadIndicator() {
    const n = totalUnread();
    // browser tab title
    document.title = n > 0 ? `(${n > 99 ? "99+" : n}) ${_baseTitle}` : _baseTitle;
    // favicon: red-dot variant when unread
    setFavicon(n > 0);
    // desktop app: overlay badge + taskbar flash (only when it actually changed
    // and the window isn't focused, to avoid flashing while the user is typing)
    if (n !== _lastUnread) {
      _lastUnread = n;
      const D = window.CorporateChatDesktop;
      if (D && typeof D.setUnread === "function") {
        try { D.setUnread(n, !document.hasFocus()); } catch (e) {}
      }
    }
  }
  let _faviconUnread = null;
  function setFavicon(unread) {
    if (_faviconUnread === unread) return;
    _faviconUnread = unread;
    let link = document.querySelector("link[rel='icon']");
    if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
    const dot = unread
      ? `<circle cx='78' cy='22' r='20' fill='%23e74c3c' stroke='white' stroke-width='4'/>`
      : "";
    link.href = "data:image/svg+xml," +
      `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>` +
      `<circle cx='50' cy='50' r='50' fill='%233390ec'/>` +
      `<text x='50' y='68' font-size='55' text-anchor='middle' fill='white'>C</text>` +
      dot + `</svg>`;
  }

  function renderChatList(chats) {
    const list = document.getElementById("chat-list");
    if (!list) return;
    if (!chats.length) {
      list.innerHTML = `<div class="list-empty">Нет чатов.<br>Нажмите ✏️ чтобы начать новый чат.</div>`;
      return;
    }
    list.innerHTML = chats.map((c) => {
      const online = c.members && c.members.some((m) => m.id !== State.me.id && m.is_online);
      const isGroup = c.type !== "private";
      return `
      <div class="chat-item ${c.id === State.activeChatId ? "active" : ""}" data-chat-id="${c.id}">
        ${avatarHtml({ url: c.avatar_url, color: c.avatar_color, name: c.name, isGroup, extra: online ? '<span class="online-dot"></span>' : "" })}
        <div class="chat-meta">
          <div class="chat-top">
            <span class="chat-name">${c.is_muted ? "🔇 " : ""}${escapeHtml(c.name)}</span>
            <span class="chat-time">${c.last_message_at ? formatTime(c.last_message_at) : ""}</span>
          </div>
          <div class="chat-bottom">
            <span class="chat-last">${c.last_message ? escapeHtml(c.last_message.slice(0, 60)) : "Нет сообщений"}</span>
            ${c.unread ? `<span class="unread-badge">${c.unread > 99 ? "99+" : c.unread}</span>` : ""}
          </div>
        </div>
      </div>`;
    }).join("");
    list.querySelectorAll(".chat-item").forEach((el) => {
      const cid = parseInt(el.getAttribute("data-chat-id"), 10);
      el.addEventListener("click", () => Router.navigate("/chats/" + el.getAttribute("data-chat-id")));
      // right-click -> chat context menu (delete)
      el.addEventListener("contextmenu", (e) => { e.preventDefault(); openChatListMenu(cid, e.clientX, e.clientY); });
      // long-press (touch) -> same menu
      let lpTimer = null;
      el.addEventListener("touchstart", (e) => {
        lpTimer = setTimeout(() => {
          const t = e.touches && e.touches[0];
          openChatListMenu(cid, t ? t.clientX : 0, t ? t.clientY : 0);
        }, 550);
      }, { passive: true });
      const cancelLP = () => { if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; } };
      el.addEventListener("touchend", cancelLP);
      el.addEventListener("touchmove", cancelLP);
    });
  }

  // ---------- Chat list context menu (delete / archive) ----------
  function closeChatListMenu() {
    const m = document.getElementById("chat-ctx-menu");
    if (m) m.remove();
    document.removeEventListener("click", closeChatListMenu);
    document.removeEventListener("scroll", closeChatListMenu, true);
  }

  function openChatListMenu(chatId, x, y) {
    closeChatListMenu();
    const chat = State.chats.find((c) => c.id === chatId);
    if (!chat) return;
    const isGroup = chat.type !== "private";
    let canDelete = true;
    if (isGroup) {
      const me = chat.members && chat.members.find((m) => m.id === State.me.id);
      const amGroupAdmin = !!(me && me.is_chat_admin);
      canDelete = amGroupAdmin || chat.created_by === State.me.id || State.me.role === "admin";
    }
    const items = [];
    items.push({ act: "open", icon: "💬", label: "Открыть" });
    if (canDelete) items.push({ sep: true }, { act: "delete", icon: "🗑️", label: "Удалить чат", danger: true });

    const menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.id = "chat-ctx-menu";
    menu.innerHTML = items.map((it) => it.sep
      ? `<div class="ctx-sep"></div>`
      : `<div class="ctx-item ${it.danger ? "danger" : ""}" data-act="${it.act}"><span class="ctx-icon">${it.icon}</span>${it.label}</div>`
    ).join("");
    document.body.appendChild(menu);
    const mw = 200, mh = menu.offsetHeight || 90;
    menu.style.left = Math.min(x, window.innerWidth - mw - 8) + "px";
    menu.style.top = Math.min(y, window.innerHeight - mh - 8) + "px";

    menu.addEventListener("click", (e) => {
      const item = e.target.closest(".ctx-item");
      if (!item) return;
      const act = item.getAttribute("data-act");
      closeChatListMenu();
      if (act === "open") Router.navigate("/chats/" + chatId);
      else if (act === "delete") confirmDeleteChat(chat);
    });
    setTimeout(() => {
      document.addEventListener("click", closeChatListMenu);
      document.addEventListener("scroll", closeChatListMenu, true);
    }, 0);
  }

  async function confirmDeleteChat(chat) {
    const what = chat.type !== "private" ? `группу «${chat.name}»` : "этот чат";
    if (!confirm(`Удалить ${what}? Все сообщения будут удалены. Действие необратимо.`)) return;
    try {
      await API.deleteChat(chat.id);
      window.toast("Чат удалён", "success");
      if (State.activeChatId === chat.id) { State.activeChatId = null; State.activeChat = null; Router.navigate("/chats"); }
      loadChats();
    } catch (e) { window.toast(e.message, "error"); }
  }

  async function doSidebarSearch(q) {
    const list = document.getElementById("chat-list");
    if (!q) { renderChatList(State.chats); return; }
    const filtered = State.chats.filter((c) => c.name.toLowerCase().includes(q.toLowerCase()));
    let users = [];
    try { users = await API.searchUsers(q); } catch (e) {}
    let html = "";
    if (filtered.length) {
      html += `<div class="list-section-title">Чаты</div>` + filtered.map(chatItemHtml).join("");
    }
    if (users.length) {
      html += `<div class="list-section-title">Пользователи</div>` + users.map((u) => `
        <div class="chat-item" data-user-id="${u.id}">
          ${avatarHtml({ url: u.avatar_url, color: u.avatar_color, name: u.full_name || u.username, extra: u.is_online ? '<span class="online-dot"></span>' : "" })}
          <div class="chat-meta"><div class="chat-top"><span class="chat-name">${escapeHtml(u.full_name || u.username)}</span></div><div class="chat-last">@${escapeHtml(u.username)}</div></div>
        </div>`).join("");
    }
    if (!html) html = `<div class="list-empty">Ничего не найдено</div>`;
    list.innerHTML = html;
    list.querySelectorAll("[data-chat-id]").forEach((el) => el.addEventListener("click", () => Router.navigate("/chats/" + el.getAttribute("data-chat-id"))));
    list.querySelectorAll("[data-user-id]").forEach((el) => el.addEventListener("click", () => startPrivateChat(parseInt(el.getAttribute("data-user-id"), 10))));
  }

  function chatItemHtml(c) {
    const isGroup = c.type !== "private";
    return `<div class="chat-item" data-chat-id="${c.id}">
      ${avatarHtml({ url: c.avatar_url, color: c.avatar_color, name: c.name, isGroup })}
      <div class="chat-meta"><div class="chat-top"><span class="chat-name">${escapeHtml(c.name)}</span></div>
      <div class="chat-last">${c.last_message ? escapeHtml(c.last_message.slice(0, 60)) : "Нет сообщений"}</div></div></div>`;
  }

  async function startPrivateChat(userId) {
    try {
      const chat = await API.createChat({ type: "private", member_ids: [userId] });
      document.getElementById("search-input").value = "";
      await loadChats();
      Router.navigate("/chats/" + chat.id);
    } catch (err) { window.toast(err.message, "error"); }
  }

  // ---------- Open chat ----------
  async function openChat(chatId) {
    State.activeChatId = chatId;
    // Clear composer state when switching chats
    State.replyTo = null;
    State.editingId = null;
    State.forwardMsgId = null;
    State.pendingAttachments = [];
    const typing = document.getElementById("typing-indicator");
    if (typing) typing.textContent = "";
    clearTimeout(State.typingClear);
    State.typingClear = null;
    document.getElementById("app-layout").classList.add("has-active-chat");
    try {
      State.activeChat = await API.getChat(chatId);
      const first = await API.listMessages(chatId, null, State.pageSize);
      State.messages = first;
      // If we got a full page, there are probably older messages to lazy-load.
      State.hasMoreOlder = first.length >= State.pageSize;
      State.loadingOlder = false;
      renderChatArea();
      renderChatList(State.chats);
      API.markRead(chatId).then(() => refreshChatList()).catch(() => {});
    } catch (err) {
      window.toast(err.message, "error");
      Router.navigate("/chats");
    }
  }

  function isMyGroupAdmin() {
    if (!State.activeChat) return false;
    if (State.me.role === "admin") return true;
    const me = State.activeChat.members.find((m) => m.id === State.me.id);
    return me && me.is_chat_admin;
  }

  function renderChatArea() {
    const chat = State.activeChat;
    const area = document.getElementById("chat-area");
    area.innerHTML = `
      <div class="chat-header">
        <button class="back-btn" id="back-btn">‹</button>
        ${avatarHtml({ url: chat.avatar_url, color: chat.avatar_color, name: chat.name, isGroup: chat.type !== "private", size: "sm", id: "ch-avatar" })}
        <div class="chat-header-info" id="ch-info-click" style="cursor:pointer">
          <div class="chat-header-name" id="ch-name">${escapeHtml(chat.name)}</div>
          <div class="chat-header-status" id="header-status"></div>
        </div>
        <button class="icon-btn" id="chat-search-btn" title="Поиск в чате">🔍</button>
        <button class="icon-btn" id="chat-info-btn" title="Информация">ⓘ</button>
      </div>
      <div class="pinned-bar" id="pinned-bar" style="display:none"></div>
      <div class="search-bar" id="search-bar" style="display:none">
        <input type="text" id="in-chat-search" placeholder="Поиск сообщений..." />
        <button class="icon-btn" id="close-search">✕</button>
      </div>
      <div class="messages" id="messages"></div>
      <div class="drop-overlay" id="drop-overlay">
        <div class="drop-card"><div class="drop-icon">📎</div><div class="drop-text">Перетащите файлы сюда, чтобы отправить</div></div>
      </div>
      <button class="scroll-bottom-btn" id="scroll-bottom-btn" title="Вниз">⬇</button>
      <div class="typing-indicator" id="typing-indicator"></div>
      <div class="composer" id="composer">
        <div class="reply-preview" id="reply-preview">
          <div class="rp-body"><div class="rp-name" id="rp-name"></div><div class="rp-text" id="rp-text"></div></div>
          <button class="icon-btn" id="cancel-reply">✕</button>
        </div>
        <div class="attach-preview" id="attach-preview"></div>
        <div class="composer-row">
          <button class="emoji-btn" id="attach-btn" title="Прикрепить файл">📎</button>
          <button class="emoji-btn" id="emoji-btn" title="Эмодзи">😊</button>
          <textarea id="composer-input" rows="1" placeholder="Сообщение..."></textarea>
          <button class="send-btn" id="send-btn" title="Отправить">➤</button>
        </div>
        <input type="file" id="file-input" multiple style="display:none"
          accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip,.rar,.7z,.csv,.json" />
        <div class="emoji-picker" id="emoji-picker"></div>
      </div>`;

    document.getElementById("back-btn").addEventListener("click", () => {
      document.getElementById("app-layout").classList.remove("has-active-chat");
      State.activeChatId = null; Router.navigate("/chats");
    });
    document.getElementById("chat-info-btn").addEventListener("click", openChatInfo);
    document.getElementById("ch-info-click").addEventListener("click", openChatInfo);
    document.getElementById("chat-search-btn").addEventListener("click", toggleInChatSearch);
    document.getElementById("close-search").addEventListener("click", toggleInChatSearch);
    document.getElementById("cancel-reply").addEventListener("click", clearReply);

    const inSearch = document.getElementById("in-chat-search");
    let stt = null;
    inSearch.addEventListener("input", () => { clearTimeout(stt); stt = setTimeout(() => doInChatSearch(inSearch.value.trim()), 250); });

    const input = document.getElementById("composer-input");
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
      sendTyping();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && Prefs.get("enterToSend")) { e.preventDefault(); doSend(); }
    });
    document.getElementById("send-btn").addEventListener("click", doSend);

    const msgsBox = document.getElementById("messages");
    const sbBtn = document.getElementById("scroll-bottom-btn");
    msgsBox.addEventListener("scroll", () => {
      const nearBottom = msgsBox.scrollHeight - msgsBox.scrollTop - msgsBox.clientHeight < 120;
      sbBtn.classList.toggle("show", !nearBottom);
      // Lazy-load older messages when scrolled near the top (Telegram-style)
      if (msgsBox.scrollTop < 150 && State.hasMoreOlder && !State.loadingOlder) {
        loadOlderMessages();
      }
    });
    sbBtn.addEventListener("click", () => { msgsBox.scrollTop = msgsBox.scrollHeight; sbBtn.classList.remove("show"); });

    initAttachments();
    initEmojiPicker();
    updateChatHeader();
    refreshPinnedBar();
    rerenderMessages();
    scrollMessages();
    applyPermsToUI();
    applyPrefs();
    input.focus();
  }

  function updateChatHeader() {
    const chat = State.activeChat;
    if (!chat) return;
    const nameEl = document.getElementById("ch-name");
    const st = document.getElementById("header-status");
    if (nameEl) nameEl.textContent = chat.name;
    if (!st) return;
    if (chat.type === "private") {
      const other = chat.members.find((m) => m.id !== State.me.id);
      const online = other && other.is_online;
      const away = online && State.statusMap[other.id] === "away";
      st.textContent = !online ? "не в сети" : (away ? "не на месте" : "в сети");
      st.classList.toggle("online", !!online && !away);
    } else {
      const onlineCount = chat.members.filter((m) => m.is_online).length;
      st.textContent = chat.members.length + " участников, " + onlineCount + " в сети";
      st.classList.remove("online");
    }
  }

  async function refreshPinnedBar() {
    const bar = document.getElementById("pinned-bar");
    if (!bar) return;
    let pinned = [];
    try { pinned = await API.listPinned(State.activeChatId); } catch (e) {}
    if (!pinned.length) { bar.style.display = "none"; return; }
    const p = pinned[0];
    bar.style.display = "flex";
    bar.innerHTML = `
      <span class="pin-icon">📌</span>
      <div class="pin-body"><div class="pin-title">Закреплённое (${pinned.length})</div>
      <div class="pin-text">${escapeHtml((p.text || "").slice(0, 80))}</div></div>`;
    bar.onclick = () => {
      const el = document.querySelector('[data-msg-id="' + p.id + '"]');
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    };
  }

  function toggleInChatSearch() {
    const bar = document.getElementById("search-bar");
    const show = bar.style.display === "none";
    bar.style.display = show ? "flex" : "none";
    if (show) { document.getElementById("in-chat-search").focus(); }
    else { document.getElementById("in-chat-search").value = ""; rerenderMessages(); }
  }

  async function doInChatSearch(q) {
    if (!q) { rerenderMessages(); return; }
    try {
      const results = await API.searchMessages(State.activeChatId, q);
      const box = document.getElementById("messages");
      if (!results.length) { box.innerHTML = `<div class="list-empty">Ничего не найдено по «${escapeHtml(q)}»</div>`; return; }
      box.innerHTML = `<div class="list-section-title">Найдено: ${results.length}</div>` + results.map((m) => messageHtml(m, results)).join("");
      bindMessageActions();
    } catch (err) { window.toast(err.message, "error"); }
  }

  // ---------- Messages render ----------
  function rerenderMessages() {
    const box = document.getElementById("messages");
    if (!box) return;
    if (!State.messages.length) {
      box.innerHTML = `<div class="chat-area-empty" style="flex:1"><div class="empty-pill">Сообщений пока нет. Напишите первым!</div></div>`;
      return;
    }
    let html = "";
    // top loader shown while older messages can still be fetched
    if (State.hasMoreOlder) {
      html += `<div class="load-older" id="load-older"><div class="mini-spinner"></div></div>`;
    }
    let lastDate = "";
    State.messages.forEach((m) => {
      const d = formatDate(m.created_at);
      if (d !== lastDate) { html += `<div class="date-sep">${d}</div>`; lastDate = d; }
      html += messageHtml(m, State.messages);
    });
    box.innerHTML = html;
    bindMessageActions();
    if (window.Emoji) window.Emoji.parse(box);
  }

  // Lazy-load OLDER messages and prepend them, keeping the scroll position
  // anchored so the view doesn't jump (Telegram-style infinite scroll up).
  async function loadOlderMessages() {
    if (State.loadingOlder || !State.hasMoreOlder || !State.messages.length) return;
    State.loadingOlder = true;
    const box = document.getElementById("messages");
    if (!box) { State.loadingOlder = false; return; }

    const oldestId = State.messages[0].id;
    const prevHeight = box.scrollHeight;
    const prevTop = box.scrollTop;
    const chatId = State.activeChatId;

    let older = [];
    try {
      older = await API.listMessages(chatId, oldestId, State.pageSize);
    } catch (e) {
      State.loadingOlder = false;
      return;
    }
    // user switched chats mid-request -> discard
    if (chatId !== State.activeChatId) {
      State.loadingOlder = false;
      return;
    }

    if (!older.length) {
      State.hasMoreOlder = false;
      const loader = document.getElementById("load-older");
      if (loader) loader.remove();
      State.loadingOlder = false;
      return;
    }

    // dedupe and prepend
    const known = new Set(State.messages.map((m) => m.id));
    const fresh = older.filter((m) => !known.has(m.id));
    State.messages = fresh.concat(State.messages);
    State.hasMoreOlder = older.length >= State.pageSize;

    rerenderMessages();
    // restore scroll position so the viewport stays on the same message
    const newHeight = box.scrollHeight;
    box.scrollTop = newHeight - prevHeight + prevTop;
    State.loadingOlder = false;
  }

  function appendMessage(m) {
    const box = document.getElementById("messages");
    if (!box) return;
    if (box.querySelector(".chat-area-empty")) box.innerHTML = "";
    box.insertAdjacentHTML("beforeend", messageHtml(m, State.messages));
    bindMessageActions();
    if (window.Emoji) window.Emoji.parse(box.lastElementChild || box);
  }

  function messageHtml(m, pool) {
    if (m.is_system) {
      return `<div class="system-msg" data-msg-id="${m.id}">${escapeHtml(m.text)}</div>`;
    }
    const out = m.sender_id === State.me.id;
    const isGroup = State.activeChat && State.activeChat.type !== "private";
    let replyHtml = "";
    if (m.reply_to) {
      const orig = (pool || []).find((x) => x.id === m.reply_to);
      if (orig) replyHtml = `<div class="msg-reply"><div class="r-name">${escapeHtml(orig.sender_name)}</div><div class="r-text">${escapeHtml((orig.text || "").slice(0, 50))}</div></div>`;
    }
    let fwdHtml = "";
    if (m.forwarded_from_name) fwdHtml = `<div class="msg-fwd">↪️ Переслано от <b>${escapeHtml(m.forwarded_from_name)}</b></div>`;

    const attHtml = m.is_deleted ? "" : attachmentHtml(m);
    let textHtml = "";
    if (m.text) {
      const emojiCls = emojiOnlyClass(m.text);
      textHtml = `<div class="msg-text${emojiCls}">${linkify(escapeHtml(m.text))}</div>`;
    }

    let body;
    if (m.is_deleted) body = `<div class="msg-text msg-deleted">Сообщение удалено</div>`;
    else body = `${fwdHtml}${replyHtml}${attHtml}${textHtml}`;

    const senderLabel = (!out && isGroup && !m.is_deleted) ? `<div class="msg-sender" style="color:${escapeAttr(m.sender_color)}">${escapeHtml(m.sender_name)}</div>` : "";

    let reactionsHtml = "";
    if (m.reactions && m.reactions.length) {
      reactionsHtml = `<div class="msg-reactions">` + m.reactions.map((r) =>
        `<button class="reaction-chip ${r.reacted ? "mine" : ""}" data-react="${escapeAttr(r.emoji)}" data-id="${m.id}">${r.emoji} ${r.count}</button>`).join("") + `</div>`;
    }

    // Lightweight hover affordances: quick-react + a "more" (⋯) opener.
    const actions = m.is_deleted ? "" : `
      <div class="msg-actions">
        ${can("can_react") ? `<button data-act="react" data-id="${m.id}" title="Реакция">😊</button>` : ""}
        <button data-act="menu" data-id="${m.id}" title="Ещё">⋯</button>
      </div>`;

    return `
      <div class="msg-row ${out ? "out" : "in"}" data-msg-id="${m.id}">
        <div class="msg-bubble ${m.is_pinned ? "pinned" : ""}">
          ${m.is_pinned ? '<span class="pin-flag" title="Закреплено">📌</span>' : ""}
          ${senderLabel}
          ${body}
          ${reactionsHtml}
          <div class="msg-footer">
            ${m.is_edited && !m.is_deleted ? '<span class="msg-edited">изменено</span>' : ""}
            <span class="msg-time">${formatTime(m.created_at)}</span>
          </div>
          ${actions}
        </div>
      </div>`;
  }

  function attachmentHtml(m) {
    if (!m.attachment_kind || !m.attachment_url) return "";
    if (m.attachment_kind === "image") {
      const src = m.attachment_thumb || m.attachment_url;
      // constrain display size based on aspect ratio
      let style = "";
      if (m.attachment_w && m.attachment_h) {
        const maxW = 320, maxH = 360;
        let w = m.attachment_w, h = m.attachment_h;
        const ratio = Math.min(maxW / w, maxH / h, 1);
        style = `style="width:${Math.round(w * ratio)}px;max-width:100%"`;
      }
      return `<div class="msg-image" data-full="${escapeAttr(m.attachment_url)}" data-name="${escapeAttr(m.attachment_name)}">
        <img src="${escapeAttr(src)}" alt="${escapeAttr(m.attachment_name)}" loading="lazy" ${style} />
      </div>`;
    }
    // file card — supports both "open in browser" and "download"
    const ext = (m.attachment_name.split(".").pop() || "").toLowerCase();
    const extLabel = ext.toUpperCase().slice(0, 5);
    const canOpen = isOpenableExt(ext);
    const url = escapeAttr(m.attachment_url);
    const name = escapeAttr(m.attachment_name);
    // The whole card opens (inline) when the type is viewable; otherwise it
    // downloads. A separate ⬇ button always downloads.
    return `<div class="msg-file" data-fileurl="${url}" data-filename="${name}" data-canopen="${canOpen ? 1 : 0}">
      <div class="file-icon">${extLabel || "📄"}</div>
      <div class="file-meta">
        <div class="file-name">${escapeHtml(m.attachment_name || "Файл")}</div>
        <div class="file-size">${humanSize(m.attachment_size)}${canOpen ? " · открыть" : " · скачать"}</div>
      </div>
      <div class="file-actions">
        ${canOpen ? `<a class="file-act" data-act="open" href="${url}" target="_blank" rel="noopener" title="Открыть">↗</a>` : ""}
        <a class="file-act" data-act="download" href="${url}" download="${name}" title="Скачать">⬇</a>
      </div>
    </div>`;
  }

  // File types the browser can display inline (open without downloading).
  function isOpenableExt(ext) {
    return [
      "pdf", "txt", "log", "csv", "json", "xml", "md", "html", "htm",
      "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg",
      "mp4", "webm", "ogg", "mp3", "wav", "m4a",
    ].indexOf((ext || "").toLowerCase()) >= 0;
  }

  /* Event delegation: a SINGLE set of listeners on the messages container
     handles all clicks/right-clicks, instead of binding one listener per
     button on every render. Much lighter for long chat histories. */
  function bindMessageActions() {
    const box = document.getElementById("messages");
    if (!box || box._delegated) return;
    box._delegated = true;

    box.addEventListener("click", (e) => {
      const t = e.target;
      if (!t || typeof t.closest !== "function") return;
      const actBtn = t.closest(".msg-actions button");
      if (actBtn) {
        e.stopPropagation();
        const act = actBtn.getAttribute("data-act");
        const id = parseInt(actBtn.getAttribute("data-id"), 10);
        if (act === "react") openReactionPicker(id, actBtn);
        else if (act === "menu") openContextMenu(id, actBtn.getBoundingClientRect());
        return;
      }
      const chip = t.closest(".reaction-chip");
      if (chip) {
        const id = parseInt(chip.getAttribute("data-id"), 10);
        API.reactMessage(State.activeChatId, id, chip.getAttribute("data-react")).catch((er) => window.toast(er.message, "error"));
        return;
      }
      const img = t.closest(".msg-image");
      if (img) { openLightbox(img.getAttribute("data-full"), img.getAttribute("data-name")); return; }
      // file card: explicit ⬇/↗ buttons are native <a> links and work on their
      // own. A click anywhere else on the card opens (if viewable) or downloads.
      if (t.closest(".file-act")) return;   // let the native link handle it
      const card = t.closest(".msg-file");
      if (card) {
        const url = card.getAttribute("data-fileurl");
        const fname = card.getAttribute("data-filename");
        const canOpen = card.getAttribute("data-canopen") === "1";
        if (!url) return;
        openOrDownloadFile(url, fname, canOpen);
        return;
      }
    });

    box.addEventListener("contextmenu", (e) => {
      const t = e.target;
      if (!t || typeof t.closest !== "function") return;
      const row = t.closest(".msg-row");
      if (!row) return;
      const id = parseInt(row.getAttribute("data-msg-id"), 10);
      const m = State.messages.find((x) => x.id === id);
      if (!m || m.is_deleted || m.is_system) return;
      e.preventDefault();
      openContextMenu(id, { left: e.clientX, top: e.clientY, right: e.clientX, bottom: e.clientY });
    });
  }

  // ---------- Context menu (right-click / ⋯) ----------
  function closeContextMenu() {
    const m = document.getElementById("ctx-menu");
    if (m) m.remove();
    document.removeEventListener("click", closeContextMenu);
    document.removeEventListener("scroll", closeContextMenu, true);
  }

  function openContextMenu(id, rect) {
    closeContextMenu();
    const m = State.messages.find((x) => x.id === id);
    if (!m) return;
    const out = m.sender_id === State.me.id;
    const isAdmin = State.me.role === "admin";
    const canPin = (isMyGroupAdmin() || State.activeChat.type === "private" || out) && (can("can_pin") || isAdmin);
    const canDelete = (out && (can("can_delete_own") || isAdmin)) || isAdmin;
    const hasText = !!(m.text && m.text.trim());

    const items = [];
    if (can("can_send_messages")) items.push({ act: "reply", icon: "↩️", label: "Ответить" });
    if (hasText) items.push({ act: "copy", icon: "📋", label: "Копировать" });
    if (can("can_forward")) items.push({ act: "forward", icon: "↪️", label: "Переслать" });
    if (canPin) items.push({ act: "pin", icon: "📌", label: m.is_pinned ? "Открепить" : "Закрепить" });
    if (out && hasText && (can("can_edit_own") || isAdmin)) items.push({ act: "edit", icon: "✏️", label: "Редактировать" });
    if (canDelete) items.push({ sep: true }, { act: "delete", icon: "🗑️", label: "Удалить", danger: true });

    const menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.id = "ctx-menu";
    menu.innerHTML = items.map((it) =>
      it.sep ? `<div class="ctx-sep"></div>`
        : `<div class="ctx-item ${it.danger ? "danger" : ""}" data-act="${it.act}"><span class="ctx-icon">${it.icon}</span>${it.label}</div>`
    ).join("");
    document.body.appendChild(menu);

    // position (keep on-screen)
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    let left = rect.left, top = (rect.bottom || rect.top) + 4;
    if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
    if (top + mh > window.innerHeight - 8) top = (rect.top || top) - mh - 4;
    menu.style.left = Math.max(8, left) + "px";
    menu.style.top = Math.max(8, top) + "px";

    menu.querySelectorAll(".ctx-item").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const act = el.getAttribute("data-act");
        closeContextMenu();
        if (act === "reply") setReply(id);
        else if (act === "copy") copyMessage(id);
        else if (act === "forward") openForwardModal(id);
        else if (act === "pin") doPin(id);
        else if (act === "edit") startEdit(id);
        else if (act === "delete") doDelete(id);
      });
    });
    setTimeout(() => {
      document.addEventListener("click", closeContextMenu);
      document.addEventListener("scroll", closeContextMenu, true);
    }, 0);
  }

  function copyMessage(id) {
    const m = State.messages.find((x) => x.id === id);
    if (!m || !m.text) return;
    const done = () => window.toast("Скопировано", "success");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(m.text).then(done).catch(() => fallbackCopy(m.text, done));
    } else { fallbackCopy(m.text, done); }
  }
  function fallbackCopy(text, done) {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); } catch (e) {}
    ta.remove();
  }

  function openReactionPicker(id, anchor) {
    closeFloatingReactions();
    const pop = document.createElement("div");
    pop.className = "reaction-popover";
    pop.id = "reaction-popover";
    pop.innerHTML = QUICK_REACTIONS.map((e) => `<button data-emoji="${e}">${e}</button>`).join("");
    document.body.appendChild(pop);
    if (window.Emoji) window.Emoji.parse(pop);
    const r = anchor.getBoundingClientRect();
    pop.style.top = Math.max(8, r.top - 50) + "px";
    pop.style.left = Math.min(window.innerWidth - 280, Math.max(8, r.left - 100)) + "px";
    pop.querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => {
        API.reactMessage(State.activeChatId, id, b.getAttribute("data-emoji")).catch((e) => window.toast(e.message, "error"));
        closeFloatingReactions();
      });
    });
    setTimeout(() => document.addEventListener("click", closeFloatingReactionsOnce), 0);
  }
  function closeFloatingReactions() { const p = document.getElementById("reaction-popover"); if (p) p.remove(); }
  function closeFloatingReactionsOnce(e) {
    const p = document.getElementById("reaction-popover");
    if (p && !p.contains(e.target)) { p.remove(); document.removeEventListener("click", closeFloatingReactionsOnce); }
  }

  function setReply(id) {
    const m = State.messages.find((x) => x.id === id); if (!m) return;
    State.replyTo = id; State.editingId = null;
    document.getElementById("rp-name").textContent = m.sender_name;
    document.getElementById("rp-text").textContent = (m.text || "").slice(0, 60);
    document.getElementById("reply-preview").classList.add("show");
    document.getElementById("composer-input").focus();
  }
  function clearReply() { State.replyTo = null; State.editingId = null; document.getElementById("reply-preview").classList.remove("show"); }
  function startEdit(id) {
    const m = State.messages.find((x) => x.id === id); if (!m) return;
    State.editingId = id; State.replyTo = null;
    const input = document.getElementById("composer-input");
    input.value = m.text; input.focus();
    document.getElementById("rp-name").textContent = "Редактирование";
    document.getElementById("rp-text").textContent = (m.text || "").slice(0, 60);
    document.getElementById("reply-preview").classList.add("show");
  }
  async function doDelete(id) {
    if (!confirm("Удалить сообщение?")) return;
    try { await API.deleteMessage(State.activeChatId, id); } catch (e) { window.toast(e.message, "error"); }
  }
  async function doPin(id) {
    try { const r = await API.pinMessage(State.activeChatId, id); window.toast(r.is_pinned ? "Закреплено" : "Откреплено"); }
    catch (e) { window.toast(e.message, "error"); }
  }

  async function doSend() {
    const input = document.getElementById("composer-input");
    const text = input.value.trim();
    const atts = State.pendingAttachments.slice();

    // Editing mode: attachments are not edited, just text.
    if (State.editingId) {
      if (!text) return;
      input.value = ""; input.style.height = "auto";
      try { await API.editMessage(State.activeChatId, State.editingId, { text }); State.editingId = null; clearReply(); }
      catch (err) { window.toast(err.message, "error"); input.value = text; }
      return;
    }

    if (atts.length) {
      // Wait for uploads still in progress.
      const stillUploading = atts.some((a) => a.status === "uploading");
      if (stillUploading) { window.toast("Дождитесь завершения загрузки…"); return; }
      const ready = atts.filter((a) => a.status === "done" && a.result);
      if (!ready.length) { window.toast("Файлы не загрузились", "error"); return; }

      input.value = ""; input.style.height = "auto";
      clearAttachments();
      const replyTo = State.replyTo;
      clearReply();
      try {
        // first attachment carries the caption; the rest go as bare attachments
        for (let i = 0; i < ready.length; i++) {
          const r = ready[i].result;
          await API.sendMessage(State.activeChatId, {
            text: i === 0 ? text : "",
            reply_to: i === 0 ? replyTo : null,
            attachment_kind: r.kind,
            attachment_url: r.url,
            attachment_thumb: r.thumb || "",
            attachment_name: r.name || "",
            attachment_size: r.size || 0,
            attachment_w: r.width || 0,
            attachment_h: r.height || 0,
          });
        }
      } catch (err) { window.toast(err.message, "error"); }
      return;
    }

    if (!text) return;
    input.value = ""; input.style.height = "auto";
    try {
      await API.sendMessage(State.activeChatId, { text, reply_to: State.replyTo });
      clearReply();
    } catch (err) { window.toast(err.message, "error"); input.value = text; }
  }

  function sendTyping() {
    if (State.ws && State.ws.readyState === 1) State.ws.send(JSON.stringify({ type: "typing", chat_id: State.activeChatId }));
  }
  function showTyping(username) {
    const el = document.getElementById("typing-indicator"); if (!el) return;
    el.textContent = username + " печатает…";
    clearTimeout(State.typingClear);
    State.typingClear = setTimeout(() => { el.textContent = ""; }, 2500);
  }
  function updatePresence(userId, online, status) {
    if (status) State.statusMap[userId] = online ? status : "offline";
    if (State.activeChat) {
      const m = State.activeChat.members.find((x) => x.id === userId);
      if (m) { m.is_online = online; updateChatHeader(); }
    }
    State.chats.forEach((c) => c.members && c.members.forEach((m) => { if (m.id === userId) m.is_online = online; }));
    if (Router.currentPath().startsWith("/chats")) renderChatList(State.chats);
    // refresh the online sidebar (debounced to avoid thrashing on bursts)
    clearTimeout(State.rsRefreshTimer);
    State.rsRefreshTimer = setTimeout(() => {
      const rs = document.getElementById("rs-search");
      loadRightSidebar(rs ? rs.value.trim() : "");
    }, 400);
  }

  // ---- Idle / away watcher: marks me "away" after 15 min of no activity ----
  function setupIdleWatcher() {
    if (State.idleBound) { resetIdle(); return; }
    State.idleBound = true;
    const IDLE_MS = 15 * 60 * 1000;
    function sendStatus(s) {
      if (State.myStatus === s) return;
      State.myStatus = s;
      if (State.ws && State.ws.readyState === 1) State.ws.send(JSON.stringify({ type: "status", status: s }));
    }
    State._resetIdle = function () {
      clearTimeout(State.idleTimer);
      if (Prefs.get("awayOnIdle") === false) { sendStatus("online"); return; }
      sendStatus("online");
      State.idleTimer = setTimeout(() => sendStatus("away"), IDLE_MS);
    };
    ["mousemove", "keydown", "mousedown", "touchstart", "focus"].forEach((ev) =>
      window.addEventListener(ev, () => State._resetIdle(), { passive: true }));
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) State._resetIdle();
    });
    State._resetIdle();
  }
  function resetIdle() { if (State._resetIdle) State._resetIdle(); }
  function scrollMessages() { const box = document.getElementById("messages"); if (box) box.scrollTop = box.scrollHeight; }

  function notifyIncoming(m) {
    const p = Prefs.all();

    // Is the user actively looking at THIS chat in a focused window?
    const viewingThisChat = (m.chat_id === State.activeChatId) && !document.hidden && document.hasFocus();

    // Sound: play unless the user is actively watching this chat.
    if (p.sound && !viewingThisChat) playMessageSound();

    // Desktop/browser notification: show when the user is NOT actively
    // viewing this chat (window hidden, unfocused, or a different chat open).
    // This works on every PC, including the desktop app window in background.
    if (!p.notify || viewingThisChat) return;
    if (!("Notification" in window)) return;

    let body = "";
    if (p.notifyPreview) {
      body = m.attachment_kind === "image" ? "📷 Фото"
        : m.attachment_kind === "file" ? ("📎 " + (m.attachment_name || "Файл"))
        : (m.text || "");
    } else {
      body = "Новое сообщение";
    }
    const title = m.sender_name || "Новое сообщение";

    function show() {
      try {
        const n = new Notification(title, {
          body: body.slice(0, 140),
          tag: "cc-chat-" + m.chat_id,   // collapse repeats from the same chat
          renotify: true,
        });
        n.onclick = () => {
          window.focus();
          if (m.chat_id) Router.navigate("/chats/" + m.chat_id);
          n.close();
        };
      } catch (e) {
        // Some engines require the ServiceWorker API for notifications.
        if (navigator.serviceWorker && navigator.serviceWorker.ready) {
          navigator.serviceWorker.ready.then((reg) => {
            try { reg.showNotification(title, { body: body.slice(0, 140), tag: "cc-chat-" + m.chat_id }); } catch (e2) {}
          });
        }
      }
    }

    if (Notification.permission === "granted") {
      show();
    } else if (Notification.permission !== "denied") {
      // ask once, then show
      Notification.requestPermission().then((perm) => { if (perm === "granted") show(); });
    }
  }

  let _audioCtx = null;
  function playMessageSound() {
    try {
      _audioCtx = _audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const ctx = _audioCtx, t = ctx.currentTime;
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine"; o.frequency.setValueAtTime(660, t); o.frequency.exponentialRampToValueAtTime(990, t + 0.08);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
      o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + 0.26);
    } catch (e) {}
  }

  // ---------- Attachments (upload, drag&drop, paste) ----------
  const IMAGE_MIME = /^image\//;
  let tempCounter = 0;

  function initAttachments() {
    State.pendingAttachments = [];
    const attachBtn = document.getElementById("attach-btn");
    const fileInput = document.getElementById("file-input");
    const composer = document.getElementById("composer");
    const input = document.getElementById("composer-input");

    attachBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      handleFiles(Array.from(fileInput.files || []));
      fileInput.value = "";
    });

    // paste image from clipboard
    input.addEventListener("paste", (e) => {
      const items = (e.clipboardData && e.clipboardData.items) || [];
      const files = [];
      for (const it of items) {
        if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); }
      }
      if (files.length) { e.preventDefault(); handleFiles(files); }
    });

    // drag & drop onto the composer (subtle highlight, no banner text)
    composer.addEventListener("dragenter", (e) => { e.preventDefault(); composer.classList.add("drag-active"); });
    composer.addEventListener("dragover", (e) => { e.preventDefault(); });
    composer.addEventListener("dragleave", (e) => {
      e.preventDefault();
      if (!composer.contains(e.relatedTarget)) composer.classList.remove("drag-active");
    });
    composer.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();   // don't let the chat-area handler fire too (double upload)
      composer.classList.remove("drag-active");
      const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      if (files.length) handleFiles(files);
    });

    // drag & drop onto the ENTIRE chat area (messages, header, etc.) — shows a
    // big "drop here" overlay so users can drop files above the input line too.
    const chatArea = document.getElementById("chat-area");
    const dropOverlay = document.getElementById("drop-overlay");
    if (chatArea && dropOverlay) {
      let dragDepth = 0;
      const hasFiles = (e) => {
        const dt = e.dataTransfer;
        if (!dt) return false;
        if (dt.types && Array.prototype.indexOf.call(dt.types, "Files") >= 0) return true;
        return false;
      };
      chatArea.addEventListener("dragenter", (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth++;
        dropOverlay.classList.add("show");
      });
      chatArea.addEventListener("dragover", (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      });
      chatArea.addEventListener("dragleave", (e) => {
        if (!hasFiles(e)) return;
        dragDepth--;
        if (dragDepth <= 0) { dragDepth = 0; dropOverlay.classList.remove("show"); }
      });
      chatArea.addEventListener("drop", (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth = 0;
        dropOverlay.classList.remove("show");
        composer.classList.remove("drag-active");
        const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
        if (files.length) handleFiles(files);
      });
    }
  }

  function handleFiles(files) {
    files.forEach((file) => {
      const tempId = "att" + (++tempCounter);
      const kind = IMAGE_MIME.test(file.type) ? "image" : "file";
      const item = { tempId, file, kind, status: "uploading", progress: 0, result: null, preview: null };
      State.pendingAttachments.push(item);
      if (kind === "image") {
        const reader = new FileReader();
        reader.onload = () => { item.preview = reader.result; renderAttachPreview(); };
        reader.readAsDataURL(file);
      }
      renderAttachPreview();
      // start upload
      API.uploadFile(file, (p) => { item.progress = p; updateAttachProgress(item); })
        .then((res) => { item.status = "done"; item.result = res; renderAttachPreview(); })
        .catch((err) => { item.status = "error"; item.error = err.message; renderAttachPreview(); window.toast(err.message, "error"); });
    });
  }

  function renderAttachPreview() {
    const box = document.getElementById("attach-preview");
    if (!box) return;
    if (!State.pendingAttachments.length) { box.classList.remove("show"); box.innerHTML = ""; return; }
    box.classList.add("show");
    box.innerHTML = State.pendingAttachments.map((a) => {
      const thumb = a.kind === "image" && a.preview
        ? `<img class="att-thumb" src="${a.preview}" alt="" />`
        : `<div class="att-thumb file">${a.kind === "image" ? "🖼️" : "📄"}</div>`;
      const status = a.status === "uploading"
        ? `<div class="att-progress"><span id="prog-${a.tempId}" style="width:${a.progress}%"></span></div>`
        : (a.status === "error" ? `<span class="att-err">ошибка</span>` : `<span class="att-ok">✓</span>`);
      return `<div class="att-item" data-temp="${a.tempId}">
        ${thumb}
        <div class="att-info"><div class="att-name">${escapeHtml(a.file.name)}</div>${status}</div>
        <button class="att-remove" data-remove="${a.tempId}" title="Убрать">✕</button>
      </div>`;
    }).join("");
    box.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
      const id = b.getAttribute("data-remove");
      State.pendingAttachments = State.pendingAttachments.filter((x) => x.tempId !== id);
      renderAttachPreview();
    }));
  }

  function updateAttachProgress(item) {
    const el = document.getElementById("prog-" + item.tempId);
    if (el) el.style.width = item.progress + "%";
  }

  function clearAttachments() {
    State.pendingAttachments = [];
    renderAttachPreview();
  }

  // ---------- Emoji picker ----------
  function initEmojiPicker() {
    const picker = document.getElementById("emoji-picker");
    const btn = document.getElementById("emoji-btn");
    if (!picker || !btn) return;
    const cats = Object.keys(EMOJI);
    picker.innerHTML = `<div class="emoji-cats">${cats.map((c, i) => `<button type="button" class="emoji-cat ${i === 0 ? "active" : ""}" data-cat="${c}">${c}</button>`).join("")}</div><div class="emoji-grid" id="emoji-grid"></div>`;
    if (window.Emoji) window.Emoji.parse(picker.querySelector(".emoji-cats"));
    function renderGrid(cat) {
      const grid = document.getElementById("emoji-grid");
      grid.innerHTML = EMOJI[cat].map((e) => `<button type="button" data-emoji="${e}">${e}</button>`).join("");
      if (window.Emoji) window.Emoji.parse(grid);
    }
    renderGrid(cats[0]);
    picker.querySelectorAll(".emoji-cat").forEach((cb) => {
      cb.addEventListener("click", function () {
        picker.querySelectorAll(".emoji-cat").forEach((b) => b.classList.remove("active"));
        this.classList.add("active");
        renderGrid(this.getAttribute("data-cat"));
      });
    });
    document.getElementById("emoji-grid").addEventListener("click", function (e) {
      const t = e.target;
      if (!t || typeof t.closest !== "function") return;
      const b = t.closest("button[data-emoji]"); if (!b) return;
      insertEmoji(b.getAttribute("data-emoji"));
    });
    btn.addEventListener("click", (e) => { e.stopPropagation(); picker.classList.toggle("show"); });
    if (State.emojiDocClick) {
      document.removeEventListener("click", State.emojiDocClick);
    }
    State.emojiDocClick = (e) => {
      if (!picker.classList.contains("show")) return;
      const t = e.target;
      const inside = typeof t.closest === "function" && t.closest("#emoji-picker");
      const onBtn = typeof t.closest === "function" && t.closest("#emoji-btn");
      if (!inside && !onBtn) picker.classList.remove("show");
    };
    document.addEventListener("click", State.emojiDocClick);
  }
  function insertEmoji(emoji) {
    const input = document.getElementById("composer-input"); if (!input) return;
    const s = input.selectionStart || input.value.length, e = input.selectionEnd || input.value.length;
    input.value = input.value.slice(0, s) + emoji + input.value.slice(e);
    input.focus(); const p = s + emoji.length; input.setSelectionRange(p, p);
  }

  // ---------- New chat / group modal ----------
  async function openNewChatModal(groupMode) {
    const overlay = document.getElementById("modal-overlay");
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>${groupMode ? "Новая группа" : "Новый чат"}</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div id="group-name-field" class="field" style="${groupMode ? "" : "display:none"}">
            <label>Название группы</label>
            <input type="text" id="group-name" placeholder="Например: Команда разработки" />
          </div>
          <div class="field"><input type="text" id="user-search" placeholder="Поиск пользователей..." /></div>
          <div id="user-pick-list"></div>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" id="modal-cancel">Отмена</button>
          <button class="btn-primary inline" id="modal-create" disabled>Создать</button>
        </div>
      </div>`;
    overlay.classList.add("show");
    const selected = new Set();
    const searchInput = document.getElementById("user-search");
    const listEl = document.getElementById("user-pick-list");
    const createBtn = document.getElementById("modal-create");
    const groupField = document.getElementById("group-name-field");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

    async function renderUsers(q) {
      let users = [];
      try { users = await API.searchUsers(q); } catch (e) {}
      if (!users.length) { listEl.innerHTML = `<div class="list-empty">Пользователи не найдены</div>`; return; }
      listEl.innerHTML = users.map((u) => `
        <div class="user-pick-item ${selected.has(u.id) ? "selected" : ""}" data-id="${u.id}">
          ${avatarHtml({ url: u.avatar_url, color: u.avatar_color, name: u.full_name || u.username, size: "sm" })}
          <div class="user-pick-name"><div class="un">${escapeHtml(u.full_name || u.username)}</div><div class="uh">@${escapeHtml(u.username)}</div></div>
          ${selected.has(u.id) ? '<span class="checkmark">✓</span>' : ""}
        </div>`).join("");
      listEl.querySelectorAll(".user-pick-item").forEach((el) => el.addEventListener("click", () => {
        const id = parseInt(el.getAttribute("data-id"), 10);
        if (selected.has(id)) selected.delete(id); else selected.add(id);
        update(); renderUsers(searchInput.value.trim());
      }));
    }
    function update() {
      createBtn.disabled = selected.size === 0;
      if (groupMode) groupField.style.display = "block";
      else groupField.style.display = selected.size > 1 ? "block" : "none";
    }
    let t = null;
    searchInput.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => renderUsers(searchInput.value.trim()), 250); });
    renderUsers("");
    createBtn.addEventListener("click", async () => {
      const ids = Array.from(selected);
      try {
        let chat;
        const wantGroup = groupMode || ids.length > 1;
        if (!wantGroup) chat = await API.createChat({ type: "private", member_ids: ids });
        else {
          const name = document.getElementById("group-name").value.trim() || "Группа";
          chat = await API.createChat({ type: "group", name, member_ids: ids });
        }
        close(); await loadChats(); Router.navigate("/chats/" + chat.id);
      } catch (err) { window.toast(err.message, "error"); }
    });
  }

  // ---------- Forward modal ----------
  async function openForwardModal(messageId) {
    const overlay = document.getElementById("modal-overlay");
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Переслать в…</h2><button class="modal-close">✕</button></div>
        <div class="modal-body" id="fwd-list"></div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Отмена</button></div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    const list = document.getElementById("fwd-list");
    list.innerHTML = State.chats.map((c) => `
      <div class="user-pick-item" data-id="${c.id}">
        ${avatarHtml({ url: c.avatar_url, color: c.avatar_color, name: c.name, isGroup: c.type !== "private", size: "sm" })}
        <div class="user-pick-name"><div class="un">${escapeHtml(c.name)}</div></div>
      </div>`).join("") || `<div class="list-empty">Нет доступных чатов</div>`;
    list.querySelectorAll(".user-pick-item").forEach((el) => el.addEventListener("click", async () => {
      const toId = parseInt(el.getAttribute("data-id"), 10);
      try { await API.forwardMessage(State.activeChatId, messageId, toId); window.toast("Переслано", "success"); close(); refreshChatList(); }
      catch (e) { window.toast(e.message, "error"); }
    }));
  }

  // ---------- Profile / Settings ----------
  function openProfileModal() {
    const me = State.me;
    const isAD = me.auth_source === "ldap";
    const dis = isAD ? "disabled" : "";
    const overlay = document.getElementById("modal-overlay");
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Мой профиль</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div style="text-align:center;margin-bottom:18px">
            <div class="avatar-edit" id="p-avatar-wrap">
              ${avatarHtml({ url: me.avatar_url, color: me.avatar_color, name: me.full_name || me.username, size: "lg" })}
              <div class="avatar-edit-overlay">📷</div>
            </div>
            <input type="file" id="p-avatar-input" accept="image/*" style="display:none" />
            <div class="avatar-hint">Нажмите на аватар, чтобы изменить фото</div>
          </div>
          <div class="field"><label>Имя пользователя</label><input type="text" value="${escapeAttr(me.username)}" disabled /></div>
          <div class="field"><label>Полное имя</label><input type="text" id="p-fullname" value="${escapeAttr(me.full_name)}" ${dis} /></div>
          <div class="field"><label>Email</label><input type="text" value="${escapeAttr(me.email)}" disabled /></div>
          <div class="field"><label>Должность</label><input type="text" id="p-title" value="${escapeAttr(me.title || "")}" placeholder="Например: Главный бухгалтер" ${dis} /></div>
          <div class="field"><label>Телефон</label><input type="text" id="p-phone" value="${escapeAttr(me.phone || "")}" placeholder="Внутренний / мобильный" ${dis} /></div>
          <div class="field"><label>Кабинет / офис</label><input type="text" id="p-office" value="${escapeAttr(me.office || "")}" placeholder="Например: каб. 312" ${dis} /></div>
          <div class="field"><label>О себе</label><input type="text" id="p-bio" value="${escapeAttr(me.bio || "")}" placeholder="Расскажите о себе" /></div>
          ${isAD ? '<div class="settings-sub">Контактные данные синхронизируются из Active Directory.</div>' : ""}
        </div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Закрыть</button><button class="btn-primary inline" id="modal-save">Сохранить</button></div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

    const avInput = document.getElementById("p-avatar-input");
    document.getElementById("p-avatar-wrap").addEventListener("click", () => avInput.click());
    avInput.addEventListener("change", async () => {
      const f = avInput.files && avInput.files[0];
      if (!f) return;
      try {
        await API.uploadAvatar(f);
        const u = await API.me();           // fresh user with new avatar_url
        API.Store.setUser(u); State.me = u;
        window.toast("Аватар обновлён", "success");
        renderLayout();                     // updates drawer avatar
        await loadChats();                  // updates list avatars
        openProfileModal();                 // reopen with new photo
      } catch (e) { window.toast(e.message, "error"); }
    });

    document.getElementById("modal-save").addEventListener("click", async () => {
      try {
        // bio is always editable; AD accounts have the rest synced from AD.
        const payload = { bio: document.getElementById("p-bio").value.trim() };
        if (!isAD) {
          payload.full_name = document.getElementById("p-fullname").value.trim();
          payload.title = document.getElementById("p-title").value.trim();
          payload.phone = document.getElementById("p-phone").value.trim();
          payload.office = document.getElementById("p-office").value.trim();
        }
        const u = await API.updateMe(payload);
        API.Store.setUser(u); State.me = u; window.toast("Профиль обновлён", "success"); close(); renderLayout(); loadChats();
      } catch (e) { window.toast(e.message, "error"); }
    });
  }

  function openSettingsModal() {
    const overlay = document.getElementById("modal-overlay");
    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    const p = Prefs.all();
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Настройки</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div class="settings-group-title">Основное</div>
          <div class="settings-sub" style="margin-bottom:6px">Приложение${ISDESKTOP ? "" : " (доступно в десктоп-версии)"}</div>
          <div class="settings-row"><div><div class="settings-label">Оставлять приложение в трее после закрытия</div><div class="settings-sub">[X] сворачивает, а не закрывает</div></div>
            <label class="switch"><input type="checkbox" id="set-keeptray" ${p.keepInTray ? "checked" : ""} ${ISDESKTOP ? "" : "disabled"}><span class="slider"></span></label></div>
          <div class="settings-row"><div><div class="settings-label">Автоматический запуск при старте</div><div class="settings-sub">Запускать при входе в Windows</div></div>
            <label class="switch"><input type="checkbox" id="set-autostart" ${p.autostart ? "checked" : ""} ${ISDESKTOP ? "" : "disabled"}><span class="slider"></span></label></div>
          <div class="settings-row"><div><div class="settings-label">Статус «Нет на месте» при бездействии</div><div class="settings-sub">Через 15 минут без активности</div></div>
            <label class="switch"><input type="checkbox" id="set-away" ${p.awayOnIdle ? "checked" : ""}><span class="slider"></span></label></div>

          <div class="settings-group-title">Подключение</div>
          <div class="conn-panel" id="conn-panel">
            <span class="conn-dot" id="conn-dot"></span>
            <div><div class="conn-title" id="conn-title">Подключение…</div><div class="conn-addr" id="conn-addr"></div></div>
          </div>
          <div class="settings-row"><div><div class="settings-label">Автоматическая авторизация</div><div class="settings-sub">Не выходить между сеансами</div></div>
            <label class="switch"><input type="checkbox" id="set-autologin" ${p.autoLogin ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="settings-row"><div><div class="settings-label">Вход с помощью SSO</div><div class="settings-sub">Кнопка единого входа (AD/SSO)</div></div>
            <label class="switch"><input type="checkbox" id="set-sso" ${p.preferSSO ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="settings-sub" style="margin:8px 0 4px">Настройки прокси</div>
          <div class="settings-row"><div><div class="settings-label">Не использовать прокси</div><div class="settings-sub">Прямое соединение${ISDESKTOP ? "" : " (десктоп)"}</div></div>
            <label class="switch"><input type="checkbox" id="set-noproxy" ${p.noProxy ? "checked" : ""} ${ISDESKTOP ? "" : "disabled"}><span class="slider"></span></label></div>

          <div class="settings-group-title">Оформление</div>
          <div class="settings-row"><div><div class="settings-label">Тёмная тема</div><div class="settings-sub">Ночной режим</div></div>
            <label class="switch"><input type="checkbox" id="set-dark" ${isDark ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="settings-row"><div><div class="settings-label">Цвет аватара</div><div class="settings-sub">Ваш цвет в кружочке</div></div>
            <input type="color" id="set-color" value="${escapeAttr(State.me.avatar_color)}" style="width:44px;height:32px;border:none;background:none"></div>
          <div class="settings-row fs-row">
            <div>
              <div class="settings-label">Размер сообщений</div>
              <div class="settings-sub">Текст в чате крупнее или мельче</div>
            </div>
            <div class="fs-control">
              <button type="button" class="fs-btn" id="fs-dec" title="Меньше">A−</button>
              <span class="fs-value" id="fs-label">${p.fontSize}px</span>
              <button type="button" class="fs-btn" id="fs-inc" title="Больше">A+</button>
            </div>
          </div>
          <div class="settings-row fs-presets-row">
            <div class="fs-presets">
              <button type="button" class="fs-preset" data-size="13">Маленький</button>
              <button type="button" class="fs-preset" data-size="15">Обычный</button>
              <button type="button" class="fs-preset" data-size="19">Крупный</button>
              <button type="button" class="fs-preset" data-size="23">Очень крупный</button>
            </div>
          </div>
          <div class="fs-preview-row">
            <div class="fs-preview-bubble" id="fs-preview">Пример сообщения 👍</div>
          </div>
          <div class="settings-row"><div><div class="settings-label">Компактный режим</div><div class="settings-sub">Плотнее сообщения</div></div>
            <label class="switch"><input type="checkbox" id="set-compact" ${p.compact ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="settings-row"><div><div class="settings-label">Форма пузырей</div><div class="settings-sub">Скруглённые или прямые углы</div></div>
            <select id="set-bubble" class="set-select">
              <option value="rounded" ${p.bubbleStyle === "rounded" ? "selected" : ""}>Скруглённые</option>
              <option value="square" ${p.bubbleStyle === "square" ? "selected" : ""}>Прямые</option>
            </select></div>
          <div class="settings-row"><div><div class="settings-label">Панель пользователей справа</div><div class="settings-sub">Список «онлайн» сбоку</div></div>
            <label class="switch"><input type="checkbox" id="set-sidebar" ${p.showSidebar ? "checked" : ""}><span class="slider"></span></label></div>

          <div class="settings-group-title">Чат</div>
          <div class="settings-row"><div><div class="settings-label">Enter — отправить</div><div class="settings-sub">Иначе Enter = новая строка</div></div>
            <label class="switch"><input type="checkbox" id="set-enter" ${p.enterToSend ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="settings-row"><div><div class="settings-label">Формат времени</div><div class="settings-sub">24-часовой или 12-часовой</div></div>
            <select id="set-time" class="set-select">
              <option value="24" ${p.time24 ? "selected" : ""}>24 часа</option>
              <option value="12" ${!p.time24 ? "selected" : ""}>12 часов (AM/PM)</option>
            </select></div>
          <div class="settings-row"><div><div class="settings-label">Проверка орфографии</div><div class="settings-sub">Подчёркивание ошибок при наборе</div></div>
            <label class="switch"><input type="checkbox" id="set-spell" ${p.spellcheck ? "checked" : ""}><span class="slider"></span></label></div>

          <div class="settings-group-title">Уведомления</div>
          ${(typeof window.isSecureContext !== "undefined" && !window.isSecureContext)
            ? `<div class="settings-warn">⚠️ Всплывающие уведомления требуют <b>HTTPS</b>. Сейчас вы открыли приложение по http:// — на других ПК уведомления не появятся. Откройте по адресу <b>https://…</b></div>`
            : ""}
          <div class="settings-row"><div><div class="settings-label">Звук сообщений</div><div class="settings-sub">Сигнал при новом сообщении</div></div>
            <label class="switch"><input type="checkbox" id="set-sound" ${p.sound ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="settings-row"><div><div class="settings-label">Уведомления</div><div class="settings-sub">Всплывающие при сворачивании</div></div>
            <label class="switch"><input type="checkbox" id="set-notify" ${p.notify ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="settings-row"><div><div class="settings-label">Текст в уведомлении</div><div class="settings-sub">Показывать содержимое сообщения</div></div>
            <label class="switch"><input type="checkbox" id="set-preview" ${p.notifyPreview ? "checked" : ""}><span class="slider"></span></label></div>

          <div class="settings-group-title">Аккаунт</div>
          <div class="settings-row" style="cursor:pointer" id="open-changepw"><div><div class="settings-label">Сменить пароль</div><div class="settings-sub">Обновить пароль входа</div></div><span>›</span></div>
          <div class="settings-row" style="cursor:pointer" id="open-shortcuts"><div><div class="settings-label">Горячие клавиши</div><div class="settings-sub">Список сочетаний</div></div><span>›</span></div>
          <div class="settings-row" style="cursor:pointer" id="clear-cache"><div><div class="settings-label">Очистить локальные данные</div><div class="settings-sub">Сбросить настройки этого устройства</div></div><span>›</span></div>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" id="modal-cancel">Отмена</button>
          <button class="btn-primary inline" id="settings-save" disabled>Сохранить</button>
        </div>
      </div>`;
    overlay.classList.add("show");

    // ---- Draft model: changes preview live but persist only on "Сохранить" ----
    const saved = Prefs.all();
    const savedTheme = document.documentElement.getAttribute("data-theme") || "light";
    const draft = Object.assign({}, saved);
    let draftTheme = savedTheme;
    let dirty = false;

    function markDirty() {
      dirty = true;
      const b = document.getElementById("settings-save");
      if (b) { b.disabled = false; b.textContent = "Сохранить"; }
    }
    function previewApply() {
      document.documentElement.style.setProperty("--msg-font-size", draft.fontSize + "px");
      document.body.classList.toggle("compact-mode", !!draft.compact);
      document.body.classList.toggle("bubbles-square", draft.bubbleStyle === "square");
      document.body.classList.toggle("hide-right-sidebar", !draft.showSidebar);
      document.documentElement.setAttribute("data-theme", draftTheme);
    }
    function revertPreview() {
      applyPrefs();
      document.documentElement.setAttribute("data-theme", savedTheme);
    }
    async function commit() {
      Object.keys(draft).forEach((k) => Prefs.set(k, draft[k]));
      localStorage.setItem("cc_theme", draftTheme);
      applyPrefs();
      // desktop-only actions
      if (DESKTOP) {
        if (DESKTOP.setKeepInTray) { try { await DESKTOP.setKeepInTray(draft.keepInTray); } catch (e) {} }
        if (DESKTOP.setAutostart) { try { await DESKTOP.setAutostart(draft.autostart); } catch (e) {} }
        if (DESKTOP.setNoProxy) { try { await DESKTOP.setNoProxy(draft.noProxy); } catch (e) {} }
      }
      setupIdleWatcher();
      if (State.activeChatId) rerenderMessages();
      refreshChatList();
      dirty = false;
      const b = document.getElementById("settings-save");
      if (b) { b.disabled = true; b.textContent = "Сохранено ✓"; }
      window.toast("Настройки сохранены", "success");
    }

    function close() {
      if (dirty) revertPreview();   // discard unsaved changes
      overlay.classList.remove("show"); overlay.innerHTML = "";
    }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.getElementById("settings-save").addEventListener("click", commit);

    // ---- Основное (App) ----
    const onAppPref = (id, key) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("change", () => { draft[key] = el.checked; markDirty(); });
    };
    onAppPref("set-keeptray", "keepInTray");
    onAppPref("set-autostart", "autostart");
    onAppPref("set-away", "awayOnIdle");

    // sync desktop checkboxes with the actual OS/app state (into the draft)
    if (DESKTOP && DESKTOP.getState) {
      DESKTOP.getState().then((st) => {
        if (!st) return;
        const a = document.getElementById("set-autostart");
        const k = document.getElementById("set-keeptray");
        const np = document.getElementById("set-noproxy");
        if (a) { a.checked = !!st.autostart; draft.autostart = !!st.autostart; }
        if (k) { k.checked = st.keepInTray !== false; draft.keepInTray = st.keepInTray !== false; }
        if (np) { np.checked = !!st.noProxy; draft.noProxy = !!st.noProxy; }
      }).catch(() => {});
    }

    // ---- Подключение ----
    fillConnectionPanel();
    onAppPref("set-autologin", "autoLogin");
    onAppPref("set-sso", "preferSSO");
    onAppPref("set-noproxy", "noProxy");

    document.getElementById("set-dark").addEventListener("change", (e) => {
      draftTheme = e.target.checked ? "dark" : "light";
      previewApply(); markDirty();
    });
    document.getElementById("set-color").addEventListener("change", async (e) => {
      // avatar color is an account setting -> saved immediately via API
      try { const u = await API.updateMe({ avatar_color: e.target.value }); API.Store.setUser(u); State.me = u; window.toast("Цвет обновлён", "success"); renderLayout(); loadChats(); }
      catch (err) { window.toast(err.message, "error"); }
    });
    // ---- Message font size: −/+ stepper, presets, live preview ----
    const FS_MIN = 12, FS_MAX = 26;
    function refreshFsUI() {
      const size = draft.fontSize;
      const label = document.getElementById("fs-label");
      const preview = document.getElementById("fs-preview");
      if (label) label.textContent = size + "px";
      if (preview) preview.style.fontSize = size + "px";
      const dec = document.getElementById("fs-dec");
      const inc = document.getElementById("fs-inc");
      if (dec) dec.disabled = size <= FS_MIN;
      if (inc) inc.disabled = size >= FS_MAX;
      document.querySelectorAll(".fs-preset").forEach((b) =>
        b.classList.toggle("active", parseInt(b.getAttribute("data-size"), 10) === size));
    }
    function setFontSize(size) {
      size = Math.max(FS_MIN, Math.min(FS_MAX, size));
      draft.fontSize = size;
      previewApply();        // live preview (not persisted yet)
      refreshFsUI();
      markDirty();
    }
    document.getElementById("fs-dec").addEventListener("click", () => setFontSize(draft.fontSize - 1));
    document.getElementById("fs-inc").addEventListener("click", () => setFontSize(draft.fontSize + 1));
    document.querySelectorAll(".fs-preset").forEach((b) =>
      b.addEventListener("click", () => setFontSize(parseInt(b.getAttribute("data-size"), 10))));
    refreshFsUI();
    document.getElementById("set-compact").addEventListener("change", (e) => { draft.compact = e.target.checked; previewApply(); markDirty(); });
    document.getElementById("set-bubble").addEventListener("change", (e) => { draft.bubbleStyle = e.target.value; previewApply(); markDirty(); });
    document.getElementById("set-sidebar").addEventListener("change", (e) => { draft.showSidebar = e.target.checked; previewApply(); markDirty(); });
    document.getElementById("set-enter").addEventListener("change", (e) => { draft.enterToSend = e.target.checked; markDirty(); });
    document.getElementById("set-time").addEventListener("change", (e) => { draft.time24 = e.target.value === "24"; markDirty(); });
    document.getElementById("set-spell").addEventListener("change", (e) => { draft.spellcheck = e.target.checked; markDirty(); });
    document.getElementById("set-sound").addEventListener("change", (e) => { draft.sound = e.target.checked; markDirty(); });
    document.getElementById("set-preview").addEventListener("change", (e) => { draft.notifyPreview = e.target.checked; markDirty(); });
    document.getElementById("set-notify").addEventListener("change", async (e) => {
      if (e.target.checked && "Notification" in window && Notification.permission !== "granted") {
        const perm = await Notification.requestPermission();
        if (perm !== "granted") {
          e.target.checked = false;
          window.toast(secureCtxHint() || "Уведомления запрещены браузером", "error");
          return;
        }
      }
      draft.notify = e.target.checked; markDirty();
    });
    document.getElementById("open-changepw").addEventListener("click", () => { close(); openChangePasswordModal(); });
    document.getElementById("open-shortcuts").addEventListener("click", () => { close(); openShortcutsModal(); });
    document.getElementById("clear-cache").addEventListener("click", () => {
      if (!confirm("Сбросить настройки и кэш на этом устройстве? Вы останетесь в аккаунте.")) return;
      const token = API.Store.getToken(), user = localStorage.getItem("cc_user");
      localStorage.clear();
      if (token) localStorage.setItem("cc_token", token);
      if (user) localStorage.setItem("cc_user", user);
      window.toast("Локальные данные очищены", "success");
      Prefs.invalidate();
      dirty = false;
      overlay.classList.remove("show"); overlay.innerHTML = "";
      applyPrefs(); renderLayout(); loadChats();
    });
  }

  // Warn the user when notifications can't work because the page isn't a
  // secure context (HTTP over a LAN IP). HTTPS or localhost is required.
  function secureCtxHint() {
    if (typeof window.isSecureContext !== "undefined" && !window.isSecureContext) {
      return "Уведомления требуют HTTPS. Откройте приложение по https:// (или на localhost).";
    }
    return "";
  }

  // Fills the "Подключение" status panel with the current server address.
  async function fillConnectionPanel() {
    const dot = document.getElementById("conn-dot");
    const title = document.getElementById("conn-title");
    const addr = document.getElementById("conn-addr");
    if (!title) return;
    let host = window.location.host || "localhost";
    let connected = false;
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      connected = r.ok;
    } catch (e) { connected = false; }
    if (dot) dot.className = "conn-dot " + (connected ? "ok" : "bad");
    title.textContent = connected ? "Подключено к серверу" : "Нет связи с сервером";
    addr.textContent = (window.location.protocol + "//" + host);
  }

  function openShortcutsModal() {
    const overlay = document.getElementById("modal-overlay");
    const rows = [
      ["Enter", "Отправить сообщение (если включено)"],
      ["Shift + Enter", "Новая строка"],
      ["Esc", "Закрыть окно / отменить ответ"],
      ["ПКМ на сообщении", "Меню: ответить, копировать, переслать…"],
      ["Ctrl/⌘ + K", "Поиск по чатам"],
    ];
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Горячие клавиши</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          ${rows.map(([k, v]) => `<div class="settings-row"><div class="settings-label">${v}</div><kbd class="kbd">${k}</kbd></div>`).join("")}
        </div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Закрыть</button></div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  }

  function openChangePasswordModal() {
    const overlay = document.getElementById("modal-overlay");
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Смена пароля</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div class="form-error" id="cp-error"></div>
          <div class="field"><label>Текущий пароль</label><input type="password" id="cp-old" /></div>
          <div class="field"><label>Новый пароль</label><input type="password" id="cp-new" placeholder="минимум 6 символов" /></div>
        </div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Отмена</button><button class="btn-primary inline" id="cp-save">Сохранить</button></div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.getElementById("cp-save").addEventListener("click", async () => {
      const oldp = document.getElementById("cp-old").value, np = document.getElementById("cp-new").value;
      const err = document.getElementById("cp-error");
      if (np.length < 6) { err.textContent = "Новый пароль слишком короткий"; err.classList.add("show"); return; }
      try { await API.changePassword({ old_password: oldp, new_password: np }); window.toast("Пароль изменён", "success"); close(); }
      catch (e) { err.textContent = e.message; err.classList.add("show"); }
    });
  }

  // ---------- Chat info / members management ----------
  function openChatInfo() {
    const chat = State.activeChat;
    const overlay = document.getElementById("modal-overlay");
    const amAdmin = isMyGroupAdmin();
    const isGroup = chat.type !== "private";
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>${isGroup ? "О группе" : "О чате"}</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div style="text-align:center;margin-bottom:16px">
            <div class="avatar-edit ${isGroup && amAdmin ? "" : "no-edit"}" id="ci-avatar-wrap" style="margin:0 auto 10px">
              ${avatarHtml({ url: chat.avatar_url, color: chat.avatar_color, name: chat.name, isGroup, size: "lg" })}
              ${isGroup && amAdmin ? '<div class="avatar-edit-overlay">📷</div>' : ""}
            </div>
            ${isGroup && amAdmin ? '<input type="file" id="ci-avatar-input" accept="image/*" style="display:none" />' : ""}
            <div style="font-size:18px;font-weight:600" id="ci-name">${escapeHtml(chat.name)}</div>
            <div class="settings-sub">${isGroup ? "Группа" : "Личный чат"} · ${chat.members.length} участн.</div>
            ${chat.description ? `<div style="margin-top:8px;color:var(--text-secondary)">${escapeHtml(chat.description)}</div>` : ""}
          </div>
          ${isGroup && amAdmin ? `
            <div class="field"><label>Название</label><input type="text" id="ci-edit-name" value="${escapeAttr(chat.name)}" /></div>
            <div class="field"><label>Описание</label><input type="text" id="ci-edit-desc" value="${escapeAttr(chat.description || "")}" placeholder="Описание группы" /></div>
            <button class="btn-primary inline" id="ci-save" style="margin-bottom:14px">Сохранить изменения</button>
            <button class="btn-secondary" id="ci-add" style="margin-bottom:14px">➕ Добавить участников</button>
          ` : ""}
          <div class="settings-row" style="padding:8px 0">
            <div class="settings-label">${chat.is_muted ? "🔇 Уведомления выключены" : "🔔 Уведомления включены"}</div>
            <label class="switch"><input type="checkbox" id="ci-mute" ${chat.is_muted ? "" : "checked"}><span class="slider"></span></label>
          </div>
          <div class="list-section-title">Участники</div>
          <div id="ci-members"></div>
        </div>
        <div class="modal-footer">
          ${isGroup ? `<button class="btn-danger" id="ci-leave">Покинуть группу</button>` : ""}
          ${(!isGroup || amAdmin) ? `<button class="btn-danger" id="ci-delete">Удалить чат</button>` : ""}
          <button class="btn-secondary" id="modal-cancel">Закрыть</button>
        </div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

    renderCIMembers();
    function renderCIMembers() {
      const box = document.getElementById("ci-members");
      box.innerHTML = chat.members.map((m) => `
        <div class="user-pick-item">
          ${avatarHtml({ url: m.avatar_url, color: m.avatar_color, name: m.full_name || m.username, size: "sm", extra: m.is_online ? '<span class="online-dot"></span>' : "" })}
          <div class="user-pick-name">
            <div class="un">${escapeHtml(m.full_name || m.username)}${m.id === State.me.id ? " (вы)" : ""} ${m.is_chat_admin ? '<span class="badge admin">админ</span>' : ""}</div>
            <div class="uh">@${escapeHtml(m.username)}</div>
          </div>
          ${isGroup && amAdmin && m.id !== State.me.id ? `
            <button class="mini-btn" data-mact="admin" data-id="${m.id}">${m.is_chat_admin ? "Снять" : "Админ"}</button>
            <button class="mini-btn danger" data-mact="remove" data-id="${m.id}">Удалить</button>` : ""}
        </div>`).join("");
      box.querySelectorAll("button[data-mact]").forEach((b) => b.addEventListener("click", async () => {
        const id = parseInt(b.getAttribute("data-id"), 10), act = b.getAttribute("data-mact");
        try {
          if (act === "admin") { await API.toggleMemberAdmin(chat.id, id); window.toast("Готово", "success"); }
          else if (act === "remove") { if (!confirm("Удалить участника?")) return; await API.removeMember(chat.id, id); window.toast("Удалён", "success"); }
          await reloadActiveChat(); close(); openChatInfo();
        } catch (e) { window.toast(e.message, "error"); }
      }));
    }

    const muteToggle = document.getElementById("ci-mute");
    if (muteToggle) muteToggle.addEventListener("change", async () => {
      try { const r = await API.toggleMute(chat.id); chat.is_muted = r.is_muted; window.toast(r.is_muted ? "Звук выключен" : "Звук включён"); refreshChatList(); }
      catch (e) { window.toast(e.message, "error"); }
    });

    const ciAvatarInput = document.getElementById("ci-avatar-input");
    const ciAvatarWrap = document.getElementById("ci-avatar-wrap");
    if (ciAvatarInput && ciAvatarWrap) {
      ciAvatarWrap.addEventListener("click", () => ciAvatarInput.click());
      ciAvatarInput.addEventListener("change", async () => {
        const f = ciAvatarInput.files && ciAvatarInput.files[0];
        if (!f) return;
        try {
          await API.uploadChatAvatar(chat.id, f);
          window.toast("Аватар группы обновлён", "success");
          await reloadActiveChat();
          refreshChatList();
          close(); openChatInfo();
        } catch (e) { window.toast(e.message, "error"); }
      });
    }

    const saveBtn = document.getElementById("ci-save");
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      try {
        const upd = await API.updateChat(chat.id, { name: document.getElementById("ci-edit-name").value.trim(), description: document.getElementById("ci-edit-desc").value.trim() });
        State.activeChat = upd; window.toast("Сохранено", "success"); updateChatHeader(); refreshChatList(); close(); openChatInfo();
      } catch (e) { window.toast(e.message, "error"); }
    });
    const addBtn = document.getElementById("ci-add");
    if (addBtn) addBtn.addEventListener("click", () => { close(); openAddMembersModal(chat.id); });
    const leaveBtn = document.getElementById("ci-leave");
    if (leaveBtn) leaveBtn.addEventListener("click", async () => {
      if (!confirm("Покинуть группу?")) return;
      try { await API.removeMember(chat.id, State.me.id); window.toast("Вы покинули группу"); close(); State.activeChatId = null; Router.navigate("/chats"); loadChats(); }
      catch (e) { window.toast(e.message, "error"); }
    });
    const delBtn = document.getElementById("ci-delete");
    if (delBtn) delBtn.addEventListener("click", async () => {
      if (!confirm("Удалить чат? Это действие необратимо.")) return;
      try { await API.deleteChat(chat.id); window.toast("Чат удалён"); close(); State.activeChatId = null; Router.navigate("/chats"); loadChats(); }
      catch (e) { window.toast(e.message, "error"); }
    });
  }

  async function openAddMembersModal(chatId) {
    const overlay = document.getElementById("modal-overlay");
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Добавить участников</h2><button class="modal-close">✕</button></div>
        <div class="modal-body"><div class="field"><input type="text" id="am-search" placeholder="Поиск..." /></div><div id="am-list"></div></div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Отмена</button><button class="btn-primary inline" id="am-add" disabled>Добавить</button></div>
      </div>`;
    overlay.classList.add("show");
    const selected = new Set();
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    const search = document.getElementById("am-search"), listEl = document.getElementById("am-list"), addBtn = document.getElementById("am-add");
    const existingIds = new Set((State.activeChat.members || []).map((m) => m.id));
    async function render(q) {
      let users = []; try { users = await API.searchUsers(q); } catch (e) {}
      users = users.filter((u) => !existingIds.has(u.id));
      if (!users.length) { listEl.innerHTML = `<div class="list-empty">Никого не найдено</div>`; return; }
      listEl.innerHTML = users.map((u) => `
        <div class="user-pick-item ${selected.has(u.id) ? "selected" : ""}" data-id="${u.id}">
          ${avatarHtml({ url: u.avatar_url, color: u.avatar_color, name: u.full_name || u.username, size: "sm" })}
          <div class="user-pick-name"><div class="un">${escapeHtml(u.full_name || u.username)}</div><div class="uh">@${escapeHtml(u.username)}</div></div>
          ${selected.has(u.id) ? '<span class="checkmark">✓</span>' : ""}</div>`).join("");
      listEl.querySelectorAll(".user-pick-item").forEach((el) => el.addEventListener("click", () => {
        const id = parseInt(el.getAttribute("data-id"), 10);
        if (selected.has(id)) selected.delete(id); else selected.add(id);
        addBtn.disabled = selected.size === 0; render(search.value.trim());
      }));
    }
    let t = null;
    search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => render(search.value.trim()), 250); });
    render("");
    addBtn.addEventListener("click", async () => {
      try { await API.addMembers(chatId, Array.from(selected)); window.toast("Участники добавлены", "success"); close(); reloadActiveChat(); }
      catch (e) { window.toast(e.message, "error"); }
    });
  }

  // ---------- Helpers ----------
  function initials(name) {
    if (!name) return "?";
    const p = name.trim().split(/\s+/);
    if (p.length >= 2) return (p[0][0] + p[1][0]).toUpperCase();
    return name.slice(0, 2).toUpperCase();
  }

  // Inner HTML for an avatar circle: photo if avatar_url, else colored initials.
  // `extra` (e.g. online dot / group glyph) is appended.
  function avatarInner(opts) {
    const url = opts.url;
    const extra = opts.extra || "";
    if (url) {
      return `<img class="avatar-img" src="${escapeAttr(url)}" alt="" loading="lazy" />${extra}`;
    }
    const label = opts.isGroup ? "👥" : initials(opts.name);
    return `${label}${extra}`;
  }
  // Build a full avatar element. size: "" | "sm" | "lg"
  function avatarHtml(opts) {
    const size = opts.size ? " " + opts.size : "";
    const bg = opts.url ? "transparent" : (opts.color || "#3390ec");
    const id = opts.id ? ` id="${opts.id}"` : "";
    return `<div class="avatar${size}"${id} style="background:${escapeAttr(bg)}">${avatarInner(opts)}</div>`;
  }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function escapeAttr(s) { return escapeHtml(s); }
  function humanSize(num) {
    num = num || 0;
    const units = ["Б", "КБ", "МБ", "ГБ"];
    let i = 0;
    while (num >= 1024 && i < units.length - 1) { num /= 1024; i++; }
    return (i === 0 ? num.toFixed(0) : num.toFixed(1)) + " " + units[i];
  }
  // Open a file inline when possible (image/pdf/text/video/audio shown in an
  // in-app viewer), otherwise open in a new tab or download.
  function openOrDownloadFile(url, name, canOpen) {
    const ext = (name || "").split(".").pop().toLowerCase();
    const isImg = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].indexOf(ext) >= 0;
    const isPdf = ext === "pdf";
    const isVideo = ["mp4", "webm", "ogg"].indexOf(ext) >= 0;
    const isAudio = ["mp3", "wav", "m4a", "ogg"].indexOf(ext) >= 0;
    const isText = ["txt", "log", "csv", "json", "xml", "md", "html", "htm"].indexOf(ext) >= 0;

    if (isImg) { openLightbox(url, name); return; }
    if (isPdf || isVideo || isAudio || isText) { openDocViewer(url, name, ext); return; }
    if (canOpen) { window.open(url, "_blank", "noopener"); return; }
    // fall back to a download
    triggerDownload(url, name);
  }

  function triggerDownload(url, name) {
    const a = document.createElement("a");
    a.href = url; a.download = name || ""; a.rel = "noopener";
    document.body.appendChild(a); a.click(); a.remove();
  }

  // Full-screen document viewer for PDF / video / audio / text files.
  function openDocViewer(url, name, ext) {
    let dv = document.getElementById("doc-viewer");
    if (!dv) {
      dv = document.createElement("div");
      dv.id = "doc-viewer";
      dv.className = "doc-viewer";
      document.body.appendChild(dv);
    }
    let inner;
    if (ext === "pdf") {
      inner = `<iframe class="dv-frame" src="${escapeAttr(url)}#toolbar=1" title="${escapeAttr(name)}"></iframe>`;
    } else if (["mp4", "webm"].indexOf(ext) >= 0) {
      inner = `<video class="dv-media" src="${escapeAttr(url)}" controls autoplay></video>`;
    } else if (["mp3", "wav", "m4a", "ogg"].indexOf(ext) >= 0) {
      inner = `<div class="dv-audio"><div class="dv-audio-name">🎵 ${escapeHtml(name)}</div><audio src="${escapeAttr(url)}" controls autoplay></audio></div>`;
    } else {
      // text-like: fetch and show as preformatted text (safe, escaped)
      inner = `<pre class="dv-text" id="dv-text">Загрузка…</pre>`;
    }
    dv.innerHTML = `
      <div class="dv-bar">
        <div class="dv-title">${escapeHtml(name)}</div>
        <div class="dv-tools">
          <a class="dv-btn" href="${escapeAttr(url)}" target="_blank" rel="noopener" title="Открыть в новой вкладке">↗</a>
          <a class="dv-btn" href="${escapeAttr(url)}" download="${escapeAttr(name)}" title="Скачать">⬇</a>
          <button class="dv-btn dv-close" title="Закрыть">✕</button>
        </div>
      </div>
      <div class="dv-body">${inner}</div>`;
    dv.classList.add("show");
    const close = () => { dv.classList.remove("show"); dv.innerHTML = ""; };
    dv.querySelector(".dv-close").addEventListener("click", close);
    dv.addEventListener("click", (e) => { if (e.target === dv) close(); });
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
    });
    // load text content (escaped) for text-like files
    const pre = document.getElementById("dv-text");
    if (pre) {
      fetch(url).then((r) => r.text()).then((txt) => {
        pre.textContent = txt.slice(0, 500000); // cap to ~500KB for safety
      }).catch(() => { pre.textContent = "Не удалось загрузить файл."; });
    }
  }

  function openLightbox(url, name) {
    let lb = document.getElementById("lightbox");
    if (!lb) {
      lb = document.createElement("div");
      lb.id = "lightbox";
      lb.className = "lightbox";
      document.body.appendChild(lb);
    }
    lb.innerHTML = `
      <button class="lb-close" title="Закрыть">✕</button>
      <a class="lb-download" href="${escapeAttr(url)}" download="${escapeAttr(name || "")}" target="_blank" rel="noopener" title="Скачать">⬇</a>
      <img src="${escapeAttr(url)}" alt="${escapeAttr(name || "")}" />`;
    lb.classList.add("show");
    const close = () => lb.classList.remove("show");
    lb.querySelector(".lb-close").addEventListener("click", close);
    lb.addEventListener("click", (e) => { if (e.target === lb) close(); });
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
    });
  }
  // Detect messages that are purely emoji (1-6 of them) -> render jumbo.
  function emojiOnlyClass(text) {
    const t = (text || "").trim();
    if (!t) return "";
    // strip emoji, variation selectors, ZWJ, skin tones, and whitespace
    let stripped;
    try {
      stripped = t.replace(/[\p{Extended_Pictographic}\u200d\uFE0F\u{1F3FB}-\u{1F3FF}\s]/gu, "");
    } catch (e) {
      return "";  // very old engine without unicode property escapes
    }
    if (stripped.length > 0) return "";  // contains non-emoji text
    let count;
    if (typeof Intl !== "undefined" && Intl.Segmenter) {
      count = [...new Intl.Segmenter().segment(t)].filter((s) => s.segment.trim()).length;
    } else {
      count = [...t.replace(/\s/g, "")].length;  // rough fallback
    }
    if (count === 0 || count > 6) return "";
    return count <= 3 ? " emoji-only few" : " emoji-only";
  }

  function linkify(safeHtml) {
    return safeHtml.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  }
  function formatTime(iso) {
    return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hour12: !Prefs.get("time24") });
  }
  function formatDate(iso) {
    const d = new Date(iso), today = new Date(), y = new Date(); y.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Сегодня";
    if (d.toDateString() === y.toDateString()) return "Вчера";
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  }

  // When the user brings the window to the front, stop the taskbar flash /
  // clear the desktop overlay badge (bound once).
  let _focusBound = false;
  function bindFocusClear() {
    if (_focusBound) return;
    _focusBound = true;
    window.addEventListener("focus", () => {
      const D = window.CorporateChatDesktop;
      if (D && typeof D.clearFlash === "function") { try { D.clearFlash(); } catch (e) {} }
    });
  }

  window.ChatView = {
    mount: async function () { renderLayout(); applyPrefs(); connectWS(); setupIdleWatcher(); bindFocusClear(); loadPermissions(); await loadChats(); },
    openChat: async function (chatId) {
      if (!document.getElementById("app-layout")) { renderLayout(); connectWS(); await loadChats(); }
      await openChat(chatId);
    },
    ensureConnected: connectWS,
    disconnect: disconnectWS,
  };
})();
