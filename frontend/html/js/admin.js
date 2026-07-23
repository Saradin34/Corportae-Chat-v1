/* ============================================================
   Admin panel: stats, users, chats, audit log, broadcast.
   Tabbed interface.
   ============================================================ */
(function () {
  "use strict";

  const app = () => document.getElementById("app");
  let currentTab = "analytics";
  let _lastUsers = [];
  let adminWs = null;
  let adminWsReconnectTimer = null;
  let usersRefreshTimer = null;
  let adminUserSearchTimer = null;

  // Same local Material-like SVG icons as chat UI. No external libraries/CDN.
  const ICON_PATHS = {
    back: "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.42-1.41L7.83 13H20v-2Z",
    shield: "M12 1 3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4Zm-1 15-4-4 1.4-1.4 2.6 2.59 5.6-5.59L18 9l-7 7Z",
    broadcast: "M3 11v2h4l10 6V5L7 11H3Zm16.5 1c0-1.77-1-3.29-2.5-4.03v8.05c1.5-.73 2.5-2.25 2.5-4.02Z",
    cleanup: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12ZM8 9h8v10H8V9Zm7.5-5-1-1h-5l-1 1H5v2h14V4h-3.5Z",
    refresh: "M17.65 6.35A7.95 7.95 0 0 0 12 4a8 8 0 1 0 7.45 5h-2.1A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h8V3l-3.35 3.35Z",
    analytics: "M3 13h4v8H3v-8Zm7-10h4v18h-4V3Zm7 6h4v12h-4V9Z",
    users: "M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5s-3 1.34-3 3 1.34 3 3 3Zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3Zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13Zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5Z",
    groups: "M20 0H4v2h16V0ZM4 24h16v-2H4v2ZM20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2ZM8 8.75c1.24 0 2.25 1.01 2.25 2.25S9.24 13.25 8 13.25 5.75 12.24 5.75 11 6.76 8.75 8 8.75ZM12.5 17h-9v-.75C3.5 14.75 6.5 14 8 14s4.5.75 4.5 2.25V17Zm8-1.5h-6V14h6v1.5Zm0-3h-6V11h6v1.5Zm0-3h-6V8h6v1.5Z",
    chat: "M4 4h16c1.1 0 2 .9 2 2v10c0 1.1-.9 2-2 2H7l-5 4V6c0-1.1.9-2 2-2Zm0 2v11l2.3-2H20V6H4Z",
    support: "M12 2a10 10 0 0 0-10 10v7c0 1.1.9 2 2 2h4v-8H4v-1a8 8 0 0 1 16 0v1h-4v8h4c1.1 0 2-.9 2-2v-7A10 10 0 0 0 12 2Zm-2 13h4v2h-4v-2Zm0-7h4v6h-4V8Z",
    audit: "M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6Zm2 16H8v-2h8v2Zm0-4H8v-2h8v2Zm-3-5V3.5L18.5 9H13Z",
    diagnostics: "M9.4 16.6 4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4Zm5.2 0L19.2 12l-4.6-4.6L16 6l6 6-6 6-1.4-1.4Z",
    system: "M21 16V8l-9-5-9 5v8l9 5 9-5ZM12 5.3 18.5 9 12 12.7 5.5 9 12 5.3ZM5 10.7l6 3.4v4.6l-6-3.4v-4.6Zm8 8v-4.6l6-3.4v4.6l-6 3.4Z",

    pin: "M16 9V4l1-1V2H7v1l1 1v5l-2 2v1h5v8l1 1 1-1v-8h5v-1l-2-2Z",
    sync: "M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.96-.7 2.8l1.46 1.46A7.93 7.93 0 0 0 20 12c0-4.42-3.58-8-8-8Zm-6 8c0-1.01.25-1.96.7-2.8L5.24 7.74A7.93 7.93 0 0 0 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3c-3.31 0-6-2.69-6-6Z",
    building: "M3 21V3h10v4h8v14h-2v-2H5v2H3Zm4-4h2v-2H7v2Zm0-4h2v-2H7v2Zm0-4h2V7H7v2Zm4 8h2v-2h-2v2Zm0-4h2v-2h-2v2Zm0-4h2V7h-2v2Zm4 8h2v-2h-2v2Zm0-4h2v-2h-2v2Zm0-4h2V7h-2v2Z",
    database: "M12 3C7.58 3 4 4.34 4 6v12c0 1.66 3.58 3 8 3s8-1.34 8-3V6c0-1.66-3.58-3-8-3Zm0 2c3.31 0 6 .67 6 1s-2.69 1-6 1-6-.67-6-1 2.69-1 6-1Zm0 14c-3.31 0-6-.67-6-1v-2.03C7.45 16.6 9.6 17 12 17s4.55-.4 6-1.03V18c0 .33-2.69 1-6 1Zm0-4c-3.31 0-6-.67-6-1v-2.03C7.45 12.6 9.6 13 12 13s4.55-.4 6-1.03V14c0 .33-2.69 1-6 1Zm0-4c-3.31 0-6-.67-6-1V7.97C7.45 8.6 9.6 9 12 9s4.55-.4 6-1.03V10c0 .33-2.69 1-6 1Z",
    bolt: "M7 2v11h3v9l7-12h-4l4-8H7Z",
    lock: "M17 8h-1V6c0-2.76-2.24-5-5-5S6 3.24 6 6v2H5c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2Zm-3 0H8V6c0-1.66 1.34-3 3-3s3 1.34 3 3v2Z",
    upload: "M5 20h14v-2H5v2ZM19 9h-4V3H9v6H5l7 7 7-7Z",
    image: "M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2ZM8.5 11.5l2.5 3.01L14.5 10l4.5 6H5l3.5-4.5Z",
    write: "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25ZM19 3.5 20.5 5 19 6.5 17.5 5 19 3.5Z",
    forward: "M12 8V4l8 8-8 8v-4H4V8h8Z",
    trash: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12ZM8 9h8v10H8V9Zm7.5-5-1-1h-5l-1 1H5v2h14V4h-3.5Z",
    thumb: "M1 21h4V9H1v12Zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2Z",
    download: "M5 20h14v-2H5v2ZM19 9h-4V3H9v6H5l7 7 7-7Z",
    tag: "M20.59 13.41 11.17 4H4v7.17l9.41 9.42c.78.78 2.05.78 2.83 0l4.35-4.35c.78-.78.78-2.05 0-2.83ZM6.5 8C5.67 8 5 7.33 5 6.5S5.67 5 6.5 5 8 5.67 8 6.5 7.33 8 6.5 8Z",
    search: "M9.5 3a6.5 6.5 0 0 1 5.17 10.44l4.44 4.44-1.41 1.41-4.44-4.44A6.5 6.5 0 1 1 9.5 3Zm0 2a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9Z",
    health: "M19.43 12.98c.04-.32.07-.65.07-.98s-.02-.66-.07-.98l2.11-1.65-2-3.46-2.49 1a7.3 7.3 0 0 0-1.69-.98L14.5 2h-4l-.38 2.65c-.61.23-1.18.55-1.69.98l-2.49-1-2 3.46 2.11 1.65c-.04.32-.08.65-.08.98s.03.66.08.98l-2.11 1.65 2 3.46 2.49-1c.51.4 1.08.73 1.69.98L10.5 22h4l.38-2.65c.61-.25 1.18-.58 1.69-.98l2.49 1 2-3.46-2.11-1.65ZM12 15.5A3.5 3.5 0 1 1 12 8a3.5 3.5 0 0 1 0 7.5Z",
    info: "M11 17h2v-6h-2v6Zm1-14a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 16a7 7 0 1 1 0-14 7 7 0 0 1 0 14Zm-1-10h2V7h-2v2Z",
    settings: "M19.43 12.98c.04-.32.07-.65.07-.98s-.02-.66-.07-.98l2.11-1.65c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.37-.31-.6-.22l-2.49 1a7.3 7.3 0 0 0-1.69-.98L14.5 2.42C14.47 2.18 14.25 2 14 2h-4c-.25 0-.46.18-.5.42L9.12 5.07c-.61.23-1.18.55-1.69.98l-2.49-1c-.23-.08-.48 0-.6.22l-2 3.46c-.13.22-.07.49.12.64l2.11 1.65c-.04.32-.08.65-.08.98s.03.66.08.98l-2.11 1.65c-.19.15-.24.42-.12.64l2 3.46c.12.22.37.31.6.22l2.49-1c.51.4 1.08.73 1.69.98l.38 2.65c.04.24.25.42.5.42h4c.25 0 .47-.18.5-.42l.38-2.65c.61-.25 1.18-.58 1.69-.98l2.49 1c.23.08.48 0 .6-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.65ZM12 15.5A3.5 3.5 0 1 1 12 8a3.5 3.5 0 0 1 0 7.5Z"
  };
  function icon(name) { const path = ICON_PATHS[name] || ICON_PATHS.chat; return `<svg class="mui-icon mui-icon-${name}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="${path}"></path></svg>`; }
  function iconLabel(name, text) { return `${icon(name)}<span>${text}</span>`; }

  async function mount() {
    const me = API.Store.getUser();
    if (!me || me.role !== "admin") {
      window.toast("Доступ только для администраторов", "error");
      Router.navigate("/chats");
      return;
    }
    app().innerHTML = `
      <div class="admin-wrap">
        <div class="admin-header">
          <button class="icon-btn" id="admin-back" title="Назад">${icon("back")}</button>
          <h1>${icon("shield")} Админ-панель</h1>
          <button class="btn-secondary" id="admin-broadcast-btn">${iconLabel("broadcast", "Рассылка")}</button>
          <button class="btn-secondary" id="admin-cleanup-btn">${iconLabel("cleanup", "Очистка")}</button>
          <button class="btn-secondary" id="admin-refresh">${iconLabel("refresh", "Обновить")}</button>
        </div>
        <div class="admin-tabs">
          <button class="admin-tab active" data-tab="analytics">${iconLabel("analytics", "Аналитика")}</button>
          <button class="admin-tab" data-tab="users">${iconLabel("users", "Пользователи")}</button>
          <button class="admin-tab" data-tab="groups">${iconLabel("groups", "Группы")}</button>
          <button class="admin-tab" data-tab="chats">${iconLabel("chat", "Чаты")}</button>
          <button class="admin-tab" data-tab="support">${iconLabel("support", "Поддержка")}</button>
          <button class="admin-tab" data-tab="audit">${iconLabel("audit", "Журнал")}</button>
          <button class="admin-tab" data-tab="diagnostics">${iconLabel("diagnostics", "AD / SSO")}</button>
          <button class="admin-tab" data-tab="system">${iconLabel("system", "Система")}</button>
          <button class="admin-tab" data-tab="settings">${iconLabel("settings", "Настройки")}</button>
        </div>
        <div class="admin-body">
          <div class="admin-body-inner">
            <div class="stat-grid" id="stat-grid"></div>
            <div id="admin-content"></div>
          </div>
        </div>
      </div>`;

    document.getElementById("admin-back").addEventListener("click", () => Router.navigate("/chats"));
    document.getElementById("admin-refresh").addEventListener("click", () => { loadStats(); loadTab(currentTab); });
    document.getElementById("admin-broadcast-btn").addEventListener("click", openBroadcast);
    document.getElementById("admin-cleanup-btn").addEventListener("click", openCleanupModal);
    document.querySelectorAll(".admin-tab").forEach((t) => t.addEventListener("click", function () {
      document.querySelectorAll(".admin-tab").forEach((x) => x.classList.remove("active"));
      this.classList.add("active");
      currentTab = this.getAttribute("data-tab");
      loadTab(currentTab);
    }));

    connectAdminWS();
    await loadStats();
    await loadTab("analytics");
  }


  function connectAdminWS() {
    const token = API.Store.getToken();
    if (!token) return;
    if (adminWs && (adminWs.readyState === 0 || adminWs.readyState === 1)) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    adminWs = new WebSocket(proto + "://" + window.location.host + "/ws?token=" + encodeURIComponent(token));
    adminWs.onmessage = (ev) => {
      let data; try { data = JSON.parse(ev.data); } catch (e) { return; }
      if (data.type === "presence") handleAdminPresence(data);
      if (data.type === "force_logout") { window.toast(data.reason || "Сессия завершена", "error"); API.Store.clearAll(); Router.navigate("/login"); }
    };
    adminWs.onclose = () => {
      adminWs = null;
      if (API.Store.getToken() && Router.currentPath && Router.currentPath() === "/admin") {
        clearTimeout(adminWsReconnectTimer);
        adminWsReconnectTimer = setTimeout(connectAdminWS, 1500);
      }
    };
  }

  function handleAdminPresence(data) {
    const uid = data.user_id;
    const online = !!data.online;
    const u = _lastUsers.find((x) => x.id === uid);
    if (u) u.is_online = online;
    // Update visible rows immediately where possible.
    document.querySelectorAll(`tr[data-user-row="${uid}"]`).forEach((row) => {
      const av = row.querySelector(".avatar");
      if (av) {
        let dot = av.querySelector(".online-dot");
        if (online) { if (!dot) { dot = document.createElement("span"); dot.className = "online-dot"; av.appendChild(dot); } dot.classList.toggle("away", data.status === "away"); }
        else if (dot) dot.remove();
      }
      const badge = row.querySelector('[data-user-status]');
      if (badge) {
        const cls = u && !u.is_active ? "off" : (online ? (data.status === "away" ? "away" : "online") : "offline");
        badge.className = "badge " + cls;
        badge.textContent = u && !u.is_active ? "Заблокирован" : (online ? (data.status === "away" ? "Не на месте" : "Онлайн") : "Не в сети");
      }
    });
    clearTimeout(usersRefreshTimer);
    usersRefreshTimer = setTimeout(() => {
      loadStats();
      if (currentTab === "users" && document.getElementById("admin-search")) loadUsers(document.getElementById("admin-search").value.trim());
    }, 900);
  }

  async function loadStats() {
    try {
      const s = await API.adminStats();
      const cards = [
        ["Пользователей", s.total_users, "#3390ec"],
        ["Онлайн", s.online_users, "#4dcd5e"],
        ["Новых за неделю", s.new_users_week, "#a695e7"],
        ["Групп-отделов", s.groups, "#f2b04a"],
        ["Групповых чатов", s.group_chats, "#faa774"],
        ["Личных чатов", s.private_chats, "#65aadd"],
        ["Сообщений", s.total_messages, "#ee7aae"],
        ["Сообщений сегодня", s.messages_today, "#6ec9cb"],
        ["AD-пользователей", s.ldap_users, "#7e9cd8"],
        ["Заблокировано", s.banned_users, "#e17076"],
      ];
      document.getElementById("stat-grid").innerHTML = cards.map(([l, v, color]) =>
        `<div class="stat-card"><div class="stat-value" style="color:${color}">${v}</div><div class="stat-label">${l}</div></div>`).join("");
    } catch (e) { window.toast(e.message, "error"); }
  }

  function loadTab(tab) {
    if (tab === "analytics") return loadAnalytics();
    if (tab === "users") return loadUsers("");
    if (tab === "groups") return loadGroups();
    if (tab === "chats") return loadAdminChats();
    if (tab === "audit") return loadAudit();
    if (tab === "support") return loadSupportAdmin();
    if (tab === "settings") return loadSettings();
    if (tab === "diagnostics") return loadDiagnostics();
    if (tab === "system") return loadSystemHealth();
  }


  // ---------- Analytics dashboard ----------
  async function loadAnalytics() {
    const content = document.getElementById("admin-content");
    content.innerHTML = `<div class="list-empty">Загрузка аналитики…</div>`;
    try {
      const a = await API.adminAnalytics();
      const totalMessages = (a.messages_by_day || []).reduce((x, y) => x + y, 0);
      const totalNewUsers = (a.users_by_day || []).reduce((x, y) => x + y, 0);
      const types = a.chat_types || {};
      content.innerHTML = `
        <div class="analytics-toolbar">
          <div><h2>${icon("analytics")} Аналитика</h2><div class="settings-sub">Последние 14 дней · без внешних библиотек</div></div>
          <div class="table-actions">
            <button class="btn-secondary" id="analytics-refresh">${iconLabel("refresh", "Обновить")}</button>
            <button class="btn-secondary" id="analytics-broadcast">${iconLabel("broadcast", "Рассылка")}</button>
          </div>
        </div>
        <div class="analytics-kpis">
          <div class="analytics-card"><b>${totalMessages}</b><span>сообщений за период</span></div>
          <div class="analytics-card"><b>${totalNewUsers}</b><span>новых пользователей</span></div>
          <div class="analytics-card"><b>${types.private || 0}</b><span>личных чатов</span></div>
          <div class="analytics-card"><b>${types.group || 0}</b><span>групп</span></div>
          <div class="analytics-card"><b>${types.channel || 0}</b><span>каналов</span></div>
          <div class="analytics-card"><b>${a.ad_linked_groups || 0}</b><span>AD-групп связано</span></div>
          <div class="analytics-card"><b>${a.users_without_group || 0}</b><span>без отдела</span></div>
        </div>
        <div class="analytics-grid">
          <div class="analytics-panel"><h3>Сообщения по дням</h3>${barChart(a.days, a.messages_by_day, "#3390ec")}</div>
          <div class="analytics-panel"><h3>Новые пользователи</h3>${barChart(a.days, a.users_by_day, "#4dcd5e")}</div>
          <div class="analytics-panel"><h3>Топ активных пользователей</h3>${rankList(a.top_users, "messages", (x) => `${esc(x.name)} <span>@${esc(x.username)}</span>`)}</div>
          <div class="analytics-panel"><h3>Топ чатов</h3>${rankList(a.top_chats, "messages", (x) => `${x.type === "channel" ? icon("channel") : x.type === "group" ? icon("users") : icon("chat")} ${esc(x.name)}`)}</div>
        </div>
        <div class="telegram-tips">
          <h3>Фишки в стиле Telegram для контроля</h3>
          <div class="tip-grid">
            <div>${icon("pin")} Следите за топ-чатами: если канал важный — закрепите объявления.</div>
            <div>${icon("broadcast")} Для срочного сообщения используйте рассылку всем онлайн.</div>
            <div>${icon("user")} Через «Войти как» можно проверить права пользователя без смены AD.</div>
            <div>${icon("sync")} В группах с меткой AD используйте «Синхр. AD», чтобы обновить состав отдела.</div>
            <div>${icon("building")} Оргструктура доступна пользователям из бокового меню.</div>
            <div>${icon("analytics")} Пики сообщений помогают понять нагрузку и активность отделов.</div>
          </div>
        </div>`;
      document.getElementById("analytics-refresh").addEventListener("click", loadAnalytics);
      document.getElementById("analytics-broadcast").addEventListener("click", openBroadcast);
    } catch (e) {
      content.innerHTML = `<div class="list-empty">Не удалось загрузить аналитику: ${esc(e.message)}</div>`;
    }
  }

  function barChart(labels, values, color) {
    labels = labels || []; values = values || [];
    const max = Math.max(1, ...values);
    const bars = values.map((v, i) => {
      const h = Math.round((v / max) * 132);
      const x = 18 + i * 34;
      const y = 150 - h;
      const day = labels[i] ? labels[i].slice(5).replace("-", ".") : "";
      return `<g><rect x="${x}" y="${y}" width="20" height="${h}" rx="5" fill="${color}"><title>${day}: ${v}</title></rect><text x="${x + 10}" y="170" text-anchor="middle" font-size="10" fill="currentColor">${day}</text></g>`;
    }).join("");
    return `<svg class="bar-chart" viewBox="0 0 510 185" role="img">${bars}<line x1="10" y1="151" x2="500" y2="151" stroke="currentColor" opacity=".16"/></svg>`;
  }

  function rankList(items, valueKey, labelFn) {
    items = items || [];
    if (!items.length) return `<div class="list-empty compact">Нет данных</div>`;
    const max = Math.max(1, ...items.map((x) => x[valueKey] || 0));
    return `<div class="rank-list">${items.map((x, i) => {
      const pct = Math.round(((x[valueKey] || 0) / max) * 100);
      return `<div class="rank-item"><div class="rank-top"><span>${i + 1}. ${labelFn(x)}</span><b>${x[valueKey] || 0}</b></div><div class="rank-bar"><i style="width:${pct}%"></i></div></div>`;
    }).join("")}</div>`;
  }

  // ---------- Users ----------
  let _groupsCache = [];
  async function loadUsers(q) {
    try {
      const [users, groups] = await Promise.all([API.adminUsers(q), API.adminGroups().catch(() => [])]);
      _lastUsers = users;
      _groupsCache = groups;
      const me = API.Store.getUser();
      const content = document.getElementById("admin-content");
      content.innerHTML = `
        <div class="admin-users-toolbar">
          <div class="search-box"><input type="text" id="admin-search" placeholder="Поиск пользователей..." value="${esc(q)}" /></div>
          <button class="btn-secondary" id="users-export">${iconLabel("download", "CSV")}</button>
        </div>
        <div class="bulk-toolbar" id="bulk-toolbar">
          <span id="bulk-count">Выбрано: 0</span>
          <select class="mini-select" id="bulk-group">
            <option value="__skip__">Назначить группу…</option>
            <option value="">— без группы —</option>
            ${_groupsCache.filter((g) => !g.is_default).map((g) => `<option value="${g.id}">${esc(g.name)}</option>`).join("")}
          </select>
          <button class="mini-btn primary" data-bulk="role-admin">Сделать админами</button>
          <button class="mini-btn" data-bulk="role-user">Сделать юзерами</button>
          <button class="mini-btn" data-bulk="unblock">Разблокировать</button>
          <button class="mini-btn danger" data-bulk="block">Заблокировать</button>
          <button class="mini-btn" data-bulk="force-logout">Выкинуть онлайн</button>
          <button class="mini-btn danger" data-bulk="delete">Удалить</button>
        </div>
        <div class="admin-table users-table">
          <table>
            <thead><tr><th class="sel-col"><input type="checkbox" id="users-select-all" /></th><th>ID</th><th>Пользователь</th><th>Email</th><th>Группа</th><th>Роль</th><th>Статус</th><th>Действия</th></tr></thead>
            <tbody>${users.map((u) => userRow(u, me)).join("")}</tbody>
          </table>
        </div>`;
      const search = document.getElementById("admin-search");
      search.addEventListener("input", () => {
        clearTimeout(adminUserSearchTimer);
        const val = search.value.trim();
        adminUserSearchTimer = setTimeout(() => loadUsers(val), 550);
      });
      if (q) {
        requestAnimationFrame(() => {
          const s = document.getElementById("admin-search");
          if (s) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
        });
      }
      content.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", () => handleUserAction(b)));
      content.querySelectorAll("select[data-group-for]").forEach((sel) => sel.addEventListener("change", () => handleGroupAssign(sel)));
      content.querySelectorAll(".user-select").forEach((cb) => cb.addEventListener("change", updateBulkToolbar));
      const allCb = document.getElementById("users-select-all");
      if (allCb) allCb.addEventListener("change", () => { content.querySelectorAll(".user-select:not(:disabled)").forEach((cb) => { cb.checked = allCb.checked; }); updateBulkToolbar(); });
      const exportBtn = document.getElementById("users-export");
      if (exportBtn) exportBtn.addEventListener("click", exportUsersCsv);
      const bulkGroup = document.getElementById("bulk-group");
      if (bulkGroup) bulkGroup.addEventListener("change", () => bulkAssignGroup(bulkGroup));
      content.querySelectorAll("button[data-bulk]").forEach((b) => b.addEventListener("click", () => handleBulkAction(b.getAttribute("data-bulk"))));
      updateBulkToolbar();
      initTooltips(content);
    } catch (e) { window.toast(e.message, "error"); }
  }

  function groupSelectHtml(u) {
    const opts = [`<option value="">— без группы —</option>`].concat(
      _groupsCache.filter((g) => !g.is_default).map((g) =>
        `<option value="${g.id}" ${u.group_id === g.id ? "selected" : ""}>${esc(g.name)}</option>`)
    );
    return `<select class="mini-select" data-group-for="${u.id}">${opts.join("")}</select>`;
  }

  function userRow(u, me) {
    return `<tr data-user-row="${u.id}">
      <td class="sel-col" data-label="Выбор">${u.id === me.id ? "" : `<input type="checkbox" class="user-select" value="${u.id}" />`}</td>
      <td data-label="ID">${u.id}</td>
      <td data-label="Пользователь"><div style="display:flex;align-items:center;gap:8px">
        <div class="avatar sm" style="background:${esc(u.avatar_color)}">${initials(u.full_name || u.username)}${u.is_online ? '<span class="online-dot"></span>' : ""}</div>
        <div><div style="font-weight:600">${esc(u.full_name || u.username)} ${u.auth_source === "ldap" ? '<span class="badge ad">AD</span>' : ""}</div><div class="settings-sub">@${esc(u.username)}</div></div></div></td>
      <td data-label="Email">${esc(u.email)}</td>
      <td data-label="Группа">${groupSelectHtml(u)}</td>
      <td data-label="Роль"><span class="badge ${u.role}">${u.role === "admin" ? "Админ" : "Юзер"}</span></td>
      <td data-label="Статус"><span data-user-status class="badge ${!u.is_active ? "off" : (u.is_online ? "online" : "offline")}">${!u.is_active ? "Заблокирован" : (u.is_online ? "Онлайн" : "Не в сети")}</span></td>
      <td data-label="Действия"><div class="table-actions">
        ${u.id === me.id ? '<span class="settings-sub">— это вы —</span>' : `
          <button class="mini-btn primary" data-act="impersonate" data-id="${u.id}" data-tooltip="Открыть приложение от лица этого пользователя">Войти как</button>
          <button class="mini-btn" data-act="details" data-id="${u.id}">Профиль</button>
          <button class="mini-btn ${u.role === "admin" ? "" : "primary"}" data-act="role" data-id="${u.id}" data-role="${u.role === "admin" ? "user" : "admin"}">${u.role === "admin" ? "Снять админа" : "Сделать админом"}</button>
          <button class="mini-btn" data-act="toggle" data-id="${u.id}">${u.is_active ? "Заблокировать" : "Разблокировать"}</button>
          ${u.is_online ? `<button class="mini-btn" data-act="forcelogout" data-id="${u.id}">Выкинуть</button>` : ""}
          ${u.auth_source === "ldap" ? "" : `<button class="mini-btn" data-act="resetpw" data-id="${u.id}">Сброс пароля</button>`}
          <button class="mini-btn danger" data-act="delete" data-id="${u.id}">Удалить</button>`}
      </div></td></tr>`;
  }

  async function doImpersonate(id) {
    const me = API.Store.getUser();
    const token = API.Store.getToken();
    if (!me || !token) return;
    if (!(await uiConfirm("Войти под выбранным пользователем? Текущая админ-сессия будет сохранена, можно будет вернуться через меню."))) return;
    try {
      const res = await API.adminImpersonate(id);
      localStorage.setItem("cc_admin_token", token);
      localStorage.setItem("cc_admin_user", JSON.stringify(me));
      localStorage.setItem("cc_impersonated_at", new Date().toISOString());
      API.Store.setToken(res.access_token);
      API.Store.setUser(res.user);
      window.toast("Вы вошли как " + (res.user.full_name || res.user.username), "success");
      Router.navigate("/chats");
    } catch (e) { window.toast(e.message, "error"); }
  }

  async function openUserDetails(id) {
    let u;
    try { u = await API.getUser(id); } catch (e) { window.toast(e.message, "error"); return; }
    const overlay = document.getElementById("modal-overlay") || createOverlay();
    const group = _groupsCache.find((g) => g.id === u.group_id);
    const line = (label, val) => val ? `<div class="usercard-row"><span class="uc-label">${label}</span><span class="uc-val">${esc(val)}</span></div>` : "";
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Профиль пользователя</h2><button class="modal-close">✕</button></div>
        <div class="modal-body" style="text-align:center">
          <div class="usercard-avatar"><div class="avatar lg" style="background:${esc(u.avatar_color)}">${initials(u.full_name || u.username)}${u.is_online ? '<span class="online-dot"></span>' : ""}</div></div>
          <div class="usercard-name">${esc(u.full_name || u.username)}</div>
          <div class="usercard-status">${u.is_online ? "в сети" : "не в сети"} · ${u.role === "admin" ? "администратор" : "пользователь"} · ${u.auth_source === "ldap" ? "AD" : "локальный"}</div>
          <div class="admin-user-summary" id="admin-user-summary"><div class="settings-sub">Загрузка активности…</div></div>
          <div class="usercard-rows">
            ${line("ID", String(u.id))}
            ${line("Логин", "@" + u.username)}
            ${line("Email", u.email)}
            ${line("Группа", group ? group.name : "— без группы —")}
            ${line("Должность", u.title)}
            ${line("Телефон", u.phone)}
            ${line("Офис", u.office)}
            ${line("О себе", u.bio)}
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" id="copy-login">Копировать логин</button>
          <button class="btn-primary inline" id="login-as-user">Войти как</button>
          <button class="btn-secondary" id="modal-cancel">Закрыть</button>
        </div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.getElementById("copy-login").addEventListener("click", () => {
      const text = u.username + (u.email ? " <" + u.email + ">" : "");
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => window.toast("Скопировано", "success"));
      else window.toast(text);
    });
    document.getElementById("login-as-user").addEventListener("click", () => { close(); doImpersonate(u.id); });
    loadUserSummaryInto(u.id);
  }


  async function loadUserSummaryInto(id) {
    const box = document.getElementById("admin-user-summary");
    if (!box) return;
    try {
      const s = await API.adminUserSummary(id);
      box.innerHTML = `
        <div class="summary-mini-grid">
          <div><b>${s.chats_count}</b><span>чатов</span></div>
          <div><b>${s.sent_messages}</b><span>сообщений</span></div>
          <div><b>${s.created_chats}</b><span>создано чатов</span></div>
        </div>
        <div class="settings-sub" style="margin-top:8px">Последнее сообщение: ${s.last_message_at ? new Date(s.last_message_at).toLocaleString("ru-RU") : "—"}</div>
        ${s.recent_chats && s.recent_chats.length ? `<div class="recent-chat-list">${s.recent_chats.map((c) => `<span class="badge user">${c.type === "channel" ? "📢" : c.type === "group" ? "👥" : "💬"} ${esc(c.name)}${c.is_admin ? " · админ" : ""}</span>`).join("")}</div>` : ""}`;
    } catch (e) { box.innerHTML = `<div class="settings-sub">Активность недоступна</div>`; }
  }

  function selectedUserIds() {
    return Array.from(document.querySelectorAll(".user-select:checked")).map((cb) => parseInt(cb.value, 10)).filter(Boolean);
  }

  function updateBulkToolbar() {
    const ids = selectedUserIds();
    const bar = document.getElementById("bulk-toolbar");
    const cnt = document.getElementById("bulk-count");
    if (bar) bar.classList.toggle("show", ids.length > 0);
    if (cnt) cnt.textContent = "Выбрано: " + ids.length;
    const allCb = document.getElementById("users-select-all");
    if (allCb) {
      const all = Array.from(document.querySelectorAll(".user-select:not(:disabled)"));
      allCb.checked = all.length > 0 && all.every((cb) => cb.checked);
      allCb.indeterminate = ids.length > 0 && !allCb.checked;
    }
  }

  async function bulkAssignGroup(sel) {
    const ids = selectedUserIds();
    if (!ids.length || sel.value === "__skip__") return;
    const gid = sel.value ? parseInt(sel.value, 10) : null;
    try {
      await API.adminAssignGroup(ids, gid);
      window.toast("Группа назначена: " + ids.length, "success");
      await loadUsers(document.getElementById("admin-search") ? document.getElementById("admin-search").value.trim() : "");
    } catch (e) { window.toast(e.message, "error"); }
  }

  async function handleBulkAction(action) {
    const ids = selectedUserIds();
    if (!ids.length) return;
    const label = { "role-admin": "сделать админами", "role-user": "сделать юзерами", block: "заблокировать", unblock: "разблокировать", "force-logout": "завершить сессии", delete: "удалить" }[action] || action;
    const msg = action === "delete"
      ? `Удалить выбранных пользователей (${ids.length})? Действие необратимо.`
      : `Для выбранных пользователей (${ids.length}) выполнить: ${label}?`;
    if (!(await uiConfirm(msg))) return;
    try {
      if (action === "role-admin" || action === "role-user") {
        const role = action === "role-admin" ? "admin" : "user";
        for (const id of ids) await API.adminSetRole(id, role);
      } else if (action === "block" || action === "unblock") {
        const wantActive = action === "unblock";
        const targets = _lastUsers.filter((u) => ids.includes(u.id) && u.is_active !== wantActive);
        for (const u of targets) await API.adminToggleActive(u.id);
      } else if (action === "force-logout") {
        for (const id of ids) await API.adminForceLogout(id);
      } else if (action === "delete") {
        const me = API.Store.getUser();
        for (const id of ids) {
          if (me && id === me.id) continue;
          await API.adminDeleteUser(id);
        }
      }
      window.toast("Готово: " + ids.length, "success");
      await loadStats();
      await loadUsers(document.getElementById("admin-search") ? document.getElementById("admin-search").value.trim() : "");
    } catch (e) { window.toast(e.message, "error"); }
  }

  function exportUsersCsv() {
    const rows = [["id", "username", "full_name", "email", "role", "auth_source", "active", "online", "group_id", "title", "phone", "office"]];
    _lastUsers.forEach((u) => rows.push([u.id, u.username, u.full_name, u.email, u.role, u.auth_source, u.is_active ? "1" : "0", u.is_online ? "1" : "0", u.group_id || "", u.title || "", u.phone || "", u.office || ""]));
    const csv = rows.map((r) => r.map((v) => '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"').join(",")).join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "corporate-chat-users.csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function initTooltips(root) {
    root = root || document;
    let tip = document.getElementById("ui-tooltip");
    if (!tip) {
      tip = document.createElement("div");
      tip.id = "ui-tooltip";
      tip.className = "ui-tooltip";
      document.body.appendChild(tip);
    }
    function show(el) {
      const text = el.getAttribute("data-tooltip");
      if (!text) return;
      tip.textContent = text;
      tip.classList.add("show");
      const r = el.getBoundingClientRect();
      const tw = tip.offsetWidth || 180;
      const th = tip.offsetHeight || 32;
      let left = r.left + r.width / 2 - tw / 2;
      let top = r.top - th - 8;
      if (top < 8) top = r.bottom + 8;
      left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
      tip.style.left = left + "px";
      tip.style.top = top + "px";
    }
    function hide() { tip.classList.remove("show"); }
    root.querySelectorAll("[data-tooltip]").forEach((el) => {
      if (el._tooltipBound) return;
      el._tooltipBound = true;
      el.addEventListener("mouseenter", () => show(el));
      el.addEventListener("focus", () => show(el));
      el.addEventListener("mouseleave", hide);
      el.addEventListener("blur", hide);
      el.addEventListener("click", hide);
    });
  }


  async function handleGroupAssign(sel) {
    const userId = parseInt(sel.getAttribute("data-group-for"), 10);
    const gid = sel.value ? parseInt(sel.value, 10) : null;
    try {
      await API.adminAssignGroup([userId], gid);
      window.toast("Группа обновлена", "success");
    } catch (e) { window.toast(e.message, "error"); }
  }

  async function handleUserAction(btn) {
    const act = btn.getAttribute("data-act"), id = parseInt(btn.getAttribute("data-id"), 10);
    try {
      if (act === "impersonate") { await doImpersonate(id); return; }
      else if (act === "details") { await openUserDetails(id); return; }
      else if (act === "forcelogout") {
        if (!(await uiConfirm("Завершить активную сессию пользователя?"))) return;
        await API.adminForceLogout(id); window.toast("Сессия завершена", "success");
      }
      else if (act === "role") { await API.adminSetRole(id, btn.getAttribute("data-role")); window.toast("Роль обновлена", "success"); }
      else if (act === "toggle") { const u = await API.adminToggleActive(id); window.toast(u.is_active ? "Разблокирован" : "Заблокирован", "success"); }
      else if (act === "resetpw") {
        const pw = await uiPrompt({ title: "Сброс пароля", message: "Новый пароль (мин. 6 символов):", type: "text" });
        if (!pw) return;
        if (pw.length < 6) { window.toast("Слишком короткий пароль", "error"); return; }
        await API.adminResetPassword(id, pw); window.toast("Пароль сброшен", "success");
      } else if (act === "delete") {
        if (!(await uiConfirm("Удалить пользователя? Необратимо."))) return;
        await API.adminDeleteUser(id); window.toast("Удалён", "success");
      }
      await loadStats();
      await loadUsers(document.getElementById("admin-search") ? document.getElementById("admin-search").value.trim() : "");
    } catch (e) { window.toast(e.message, "error"); }
  }

  // ---------- Chats ----------
  async function loadAdminChats() {
    try {
      const chats = await API.adminChats();
      const content = document.getElementById("admin-content");
      content.innerHTML = `
        <div class="admin-table">
          <table>
            <thead><tr><th>ID</th><th>Название</th><th>Тип</th><th>Участников</th><th>Сообщений</th><th>Создан</th><th></th></tr></thead>
            <tbody>${chats.map((c) => `
              <tr>
                <td data-label="ID">${c.id}</td>
                <td data-label="Название" style="font-weight:600">${esc(c.name)}</td>
                <td data-label="Тип"><span class="badge ${c.type === "private" ? "user" : "admin"}">${c.type === "private" ? "Личный" : c.type === "group" ? "Группа" : c.type}</span></td>
                <td data-label="Участников">${c.members}</td>
                <td data-label="Сообщений">${c.messages}</td>
                <td data-label="Создан" class="settings-sub">${c.created_at ? new Date(c.created_at).toLocaleDateString("ru-RU") : "—"}</td>
                <td data-label="Действия"><button class="mini-btn danger" data-del-chat="${c.id}">Удалить</button></td>
              </tr>`).join("")}</tbody>
          </table>
        </div>`;
      content.querySelectorAll("button[data-del-chat]").forEach((b) => b.addEventListener("click", async () => {
        if (!(await uiConfirm("Удалить чат и все его сообщения?"))) return;
        try { await API.adminDeleteChat(parseInt(b.getAttribute("data-del-chat"), 10)); window.toast("Чат удалён", "success"); loadStats(); loadAdminChats(); }
        catch (e) { window.toast(e.message, "error"); }
      }));
    } catch (e) { window.toast(e.message, "error"); }
  }


  // ---------- Support ----------
  async function loadSupportAdmin() {
    const content = document.getElementById("admin-content");
    content.innerHTML = `<div class="list-empty">Загрузка поддержки…</div>`;
    let meta = { statuses: ["open", "in_progress", "waiting_user", "resolved", "closed"] };
    try { const m = await API.supportMeta(); meta.statuses = m.statuses || meta.statuses; } catch (e) {}
    let statusFilter = "";
    let tickets = [];
    let templates = [];
    function statusLabel(s) { return ({ open:"открыто", in_progress:"в работе", waiting_user:"ожидает пользователя", pending:"ожидает", resolved:"решено", closed:"закрыто" })[s] || s; }
    function prLabel(p) { return ({ low:"низкий", normal:"обычный", high:"высокий", critical:"критический" })[p] || p; }

    content.innerHTML = `
      <div class="support-admin-toolbar">
        <select class="mini-select" id="sup-status-filter"><option value="">Все статусы</option>${meta.statuses.map((x) => `<option value="${x}">${statusLabel(x)}</option>`).join("")}</select>
        <button class="btn-secondary" id="sup-templates-btn">Шаблоны</button>
        <button class="btn-secondary" id="sup-refresh-btn">⟳ Обновить</button>
      </div>
      <div class="support-admin-layout"><div id="support-admin-list" class="support-list"><div class="list-empty">Загрузка…</div></div><div id="support-admin-thread" class="support-thread"><div class="list-empty">Выберите обращение</div></div></div>`;
    document.getElementById("sup-status-filter").onchange = (e) => { statusFilter = e.target.value; loadTickets(); };
    document.getElementById("sup-refresh-btn").onclick = () => loadTickets();
    document.getElementById("sup-templates-btn").onclick = openTemplatesManager;

    async function loadTickets(activeId) {
      try { tickets = await API.supportAdminTickets(statusFilter, ""); }
      catch (e) { document.getElementById("support-admin-list").innerHTML = `<div class="list-empty">${esc(e.message)}</div>`; return; }
      renderList(activeId);
      if (activeId && tickets.some((t) => t.id === activeId)) openTicket(activeId);
      else if (tickets[0]) openTicket(tickets[0].id);
      else document.getElementById("support-admin-thread").innerHTML = `<div class="list-empty">Обращений нет</div>`;
    }

    function renderList(activeId) {
      const list = document.getElementById("support-admin-list");
      if (!tickets.length) { list.innerHTML = `<div class="list-empty">Обращений нет</div>`; return; }
      list.innerHTML = tickets.map((t) => `<div class="support-ticket ${String(t.id) === String(activeId) ? "active" : ""}" data-ticket="${t.id}"><b>#${t.id} ${esc(t.subject)}</b><span>${esc(t.user_name)} · ${esc(t.last_message || "")}</span><em>${prLabel(t.priority)} · ${statusLabel(t.status)}${t.assigned_admin_name ? " · " + esc(t.assigned_admin_name) : ""}${t.unread ? " · новых " + t.unread : ""}</em></div>`).join("");
      list.querySelectorAll("[data-ticket]").forEach((el) => el.addEventListener("click", () => openTicket(parseInt(el.getAttribute("data-ticket"), 10))));
    }

    async function openTicket(id) {
      const t = tickets.find((x) => x.id === id); if (!t) return;
      renderList(id);
      const box = document.getElementById("support-admin-thread");
      box.innerHTML = `<div class="list-empty">Загрузка…</div>`;
      let msgs = [];
      try { msgs = await API.supportMessages(id); templates = await API.supportTemplates(""); }
      catch (e) { box.innerHTML = `<div class="list-empty">${esc(e.message)}</div>`; return; }
      box.innerHTML = `<div class="support-thread-head"><div><b>#${t.id} ${esc(t.subject)}</b><div class="settings-sub">${esc(t.user_name)} · ${prLabel(t.priority)} · ${statusLabel(t.status)}${t.assigned_admin_name ? " · ответственный: " + esc(t.assigned_admin_name) : ""}</div></div><div class="table-actions"><button class="mini-btn primary" id="sup-assign-me">Назначить на себя</button>${meta.statuses.map((st) => `<button class="mini-btn ${st === "closed" ? "danger" : ""}" data-st="${st}">${statusLabel(st)}</button>`).join("")}</div></div><div class="support-messages">${msgs.map((m) => `<div class="support-msg ${m.sender_role === "admin" ? "admin" : "user"}"><b>${esc(m.sender_name)}</b><p>${esc(m.text)}</p><span>${new Date(m.created_at).toLocaleString("ru-RU")}</span></div>`).join("")}</div><div class="support-reply"><div class="support-template-row"><select class="mini-select" id="support-template-select"><option value="">Шаблон ответа…</option>${templates.map((tpl) => `<option value="${tpl.id}">${esc(tpl.title)}</option>`).join("")}</select></div><textarea id="support-admin-text" rows="3" placeholder="Ответ пользователю…"></textarea><button class="btn-primary inline" id="support-admin-send">Ответить</button></div>`;
      document.getElementById("sup-assign-me").onclick = async () => { try { await API.supportAssign(id, null); await loadTickets(id); } catch (e) { window.toast(e.message, "error"); } };
      box.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", async () => { try { await API.supportSetStatus(id, b.getAttribute("data-st")); await loadTickets(id); } catch (e) { window.toast(e.message, "error"); } }));
      const tplSelect = document.getElementById("support-template-select");
      tplSelect.onchange = () => { const tpl = templates.find((x) => String(x.id) === tplSelect.value); if (tpl) document.getElementById("support-admin-text").value = tpl.text; };
      document.getElementById("support-admin-send").onclick = async () => { const txt = document.getElementById("support-admin-text").value.trim(); if (!txt) return; try { await API.supportReply(id, txt); await loadTickets(id); } catch (e) { window.toast(e.message, "error"); } };
    }

    function openTemplatesManager() {
      const overlay = document.getElementById("modal-overlay") || createOverlay();
      overlay.innerHTML = `<div class="modal modal-lg"><div class="modal-header"><h2>Шаблоны поддержки</h2><button class="modal-close">✕</button></div><div class="modal-body"><div class="settings-sub" style="margin-bottom:10px">Создавайте быстрые ответы для общей очереди поддержки.</div><button class="btn-primary inline" id="tpl-new">＋ Шаблон</button><div id="tpl-list" style="margin-top:12px"><div class="list-empty">Загрузка…</div></div></div></div>`;
      overlay.classList.add("show");
      const close = () => { overlay.classList.remove("show"); overlay.innerHTML = ""; };
      overlay.querySelector(".modal-close").onclick = close;
      async function loadTpls() {
        let rows = []; try { rows = await API.supportTemplates(""); } catch (e) { window.toast(e.message, "error"); }
        document.getElementById("tpl-list").innerHTML = rows.map((tpl) => `<div class="calendar-manager-row"><span>${esc(tpl.title)}</span><div><button class="mini-btn" data-edit="${tpl.id}">Изм.</button><button class="mini-btn danger" data-del="${tpl.id}">Удалить</button></div></div>`).join("") || `<div class="list-empty">Шаблонов нет</div>`;
        document.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => editTpl(rows.find((x) => String(x.id) === b.getAttribute("data-edit"))));
        document.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => { if (!(await uiConfirm("Удалить шаблон?"))) return; await API.supportDeleteTemplate(b.getAttribute("data-del")); loadTpls(); });
      }
      async function editTpl(tpl) {
        const title = await uiPrompt({ title: tpl ? "Изменить шаблон" : "Новый шаблон", message: "Название", value: tpl ? tpl.title : "" }); if (!title) return;
        const text = await uiPrompt({ title: "Текст шаблона", textarea: true, value: tpl ? tpl.text : "" }); if (!text) return;
        try { tpl ? await API.supportUpdateTemplate(tpl.id, { title, text, category: "general" }) : await API.supportCreateTemplate({ title, text, category: "general" }); await loadTpls(); } catch (e) { window.toast(e.message, "error"); }
      }
      document.getElementById("tpl-new").onclick = () => editTpl(null);
      loadTpls();
    }

    await loadTickets();
  }

  // ---------- Audit ----------
  async function loadAudit() {
    try {
      const logs = await API.adminAudit();
      const content = document.getElementById("admin-content");
      if (!logs.length) { content.innerHTML = `<div class="list-empty">Журнал пуст</div>`; return; }
      content.innerHTML = `
        <div class="admin-table">
          <table>
            <thead><tr><th>Время</th><th>Администратор</th><th>Действие</th><th>Детали</th></tr></thead>
            <tbody>${logs.map((l) => `
              <tr>
                <td data-label="Время" class="settings-sub">${new Date(l.created_at).toLocaleString("ru-RU")}</td>
                <td data-label="Администратор" style="font-weight:600">${esc(l.actor_name)}</td>
                <td data-label="Действие"><span class="badge admin">${esc(actionLabel(l.action))}</span></td>
                <td data-label="Детали">${esc(l.details)}</td>
              </tr>`).join("")}</tbody>
          </table>
        </div>`;
    } catch (e) { window.toast(e.message, "error"); }
  }

  function actionLabel(a) {
    const map = { ban: "Блокировка", unban: "Разблокировка", set_role: "Смена роли", reset_password: "Сброс пароля", delete_user: "Удаление юзера", delete_chat: "Удаление чата", broadcast: "Рассылка", impersonate: "Вход под пользователем", force_logout: "Завершение сессии" };
    return map[a] || a;
  }

  // ---------- Groups (departments + permissions, TrueConf-style) ----------
  // Permission columns: [flag, icon, tooltip]
  const PERM_COLS = [
    ["can_send_messages", icon("write"), "Писать сообщения"],
    ["can_create_private", icon("chat"), "Создавать личные чаты"],
    ["can_create_groups", icon("users"), "Создавать группы"],
    ["can_send_files", icon("attach"), "Отправлять файлы"],
    ["can_send_images", icon("image"), "Отправлять изображения"],
    ["can_forward", icon("forward"), "Пересылать сообщения"],
    ["can_pin", icon("pin"), "Закреплять сообщения"],
    ["can_edit_own", icon("edit"), "Редактировать свои сообщения"],
    ["can_delete_own", icon("trash"), "Удалять свои сообщения"],
    ["can_react", icon("thumb"), "Ставить реакции"],
  ];

  async function loadGroups() {
    try {
      const groups = await API.adminGroups();
      const content = document.getElementById("admin-content");
      content.innerHTML = `
        <div class="groups-toolbar">
          <button class="btn-primary inline" id="group-add">＋ Новая группа</button>
          <span class="settings-sub">Отметьте права, которые разрешены участникам каждой группы. Администраторы имеют все права всегда.</span>
        </div>
        <div class="admin-table groups-table">
          <table>
            <thead><tr>
              <th>Группа</th>
              <th class="num">Участников</th>
              ${PERM_COLS.map(([f, ic, t]) => `<th class="perm-col" data-tooltip="${esc(t)}">${ic}</th>`).join("")}
              <th></th>
            </tr></thead>
            <tbody>${groups.map(groupRow).join("")}</tbody>
          </table>
        </div>`;
      document.getElementById("group-add").addEventListener("click", openCreateGroup);
      content.querySelectorAll("input[data-perm]").forEach((cb) => cb.addEventListener("change", () => togglePerm(cb)));
      content.querySelectorAll("button[data-del-group]").forEach((b) => b.addEventListener("click", () => deleteGroup(b)));
      content.querySelectorAll("button[data-edit-group]").forEach((b) => b.addEventListener("click", () => openEditGroup(b)));
      content.querySelectorAll("button[data-sync-ad]").forEach((b) => b.addEventListener("click", () => syncAdGroup(b)));
      initTooltips(content);
    } catch (e) { window.toast(e.message, "error"); }
  }

  function groupRow(g) {
    return `<tr data-group-id="${g.id}">
      <td>
        <div style="font-weight:600">${g.is_default ? icon("tag") + " " : ""}${esc(g.name)}</div>
        ${g.ad_group_dn ? '<div class="settings-sub"><span class="badge ad">AD</span> синхронизирована</div>' : (g.description ? `<div class="settings-sub">${esc(g.description)}</div>` : "")}
      </td>
      <td class="num">${g.member_count}</td>
      ${PERM_COLS.map(([f]) => `<td class="perm-col"><input type="checkbox" data-perm="${f}" data-group="${g.id}" ${g[f] ? "checked" : ""} /></td>`).join("")}
      <td><div class="table-actions">
        ${g.ad_group_dn ? `<button class="mini-btn primary" data-sync-ad="${g.id}">Синхр. AD</button>` : ""}
        <button class="mini-btn" data-edit-group="${g.id}">Изменить</button>
        ${g.is_default ? "" : `<button class="mini-btn danger" data-del-group="${g.id}">Удалить</button>`}
      </div></td>
    </tr>`;
  }

  async function togglePerm(cb) {
    const gid = parseInt(cb.getAttribute("data-group"), 10);
    const flag = cb.getAttribute("data-perm");
    try {
      await API.adminUpdateGroup(gid, { [flag]: cb.checked });
      window.toast("Права обновлены", "success");
    } catch (e) {
      cb.checked = !cb.checked; // revert on failure
      window.toast(e.message, "error");
    }
  }

  function openCreateGroup() {
    const overlay = document.getElementById("modal-overlay") || createOverlay();
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Новая группа</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div class="field"><label>Название</label><input type="text" id="g-name" placeholder="Например: Бухгалтерия" /></div>
          <div class="field"><label>Описание (необязательно)</label><input type="text" id="g-desc" placeholder="Краткое описание" /></div>

          <div class="ad-import-box">
            <div class="ad-import-title">${icon("search")} Поиск группы в Active Directory</div>
            <div class="settings-sub" style="margin-bottom:8px">Найдите группу в AD по названию — она будет связана, а её участники из AD добавлены в эту группу (создаются автоматически, если их ещё нет).</div>
            <div class="field"><input type="text" id="ad-q" placeholder="Введите название группы AD…" /></div>
            <div id="ad-results" class="ad-results"></div>
          </div>
        </div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Отмена</button><button class="btn-primary inline" id="g-create">Создать пустую</button></div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.getElementById("g-create").addEventListener("click", async () => {
      const name = document.getElementById("g-name").value.trim();
      if (!name) { window.toast("Введите название", "error"); return; }
      try {
        await API.adminCreateGroup({ name, description: document.getElementById("g-desc").value.trim() });
        window.toast("Группа создана", "success"); close(); loadStats(); loadGroups();
      } catch (e) { window.toast(e.message, "error"); }
    });

    // ---- AD group search ----
    const adQ = document.getElementById("ad-q");
    const adResults = document.getElementById("ad-results");
    let adTimer = null;
    adQ.addEventListener("input", () => {
      clearTimeout(adTimer);
      const q = adQ.value.trim();
      if (q.length < 2) { adResults.innerHTML = ""; return; }
      adResults.innerHTML = `<div class="ad-hint">Поиск…</div>`;
      adTimer = setTimeout(() => runAdSearch(q, adResults, close), 350);
    });
  }

  async function runAdSearch(q, adResults, closeModal) {
    let groups = [];
    try {
      groups = await API.adminAdSearchGroups(q);
    } catch (e) {
      adResults.innerHTML = `<div class="ad-hint err">${esc(e.message)}</div>`;
      return;
    }
    if (!groups.length) { adResults.innerHTML = `<div class="ad-hint">Группы не найдены</div>`; return; }
    adResults.innerHTML = groups.map((g) => `
      <div class="ad-item">
        <div class="ad-item-info">
          <div class="ad-item-name">${esc(g.name)} ${g.linked ? '<span class="badge ad">связана</span>' : ""}</div>
          <div class="ad-item-dn">${esc(g.description || g.dn)}</div>
          <div class="ad-item-meta">${g.member_count} участ.</div>
        </div>
        <button class="mini-btn primary" data-import-dn="${esc(g.dn)}" data-import-name="${esc(g.name)}">Импортировать</button>
      </div>`).join("");
    adResults.querySelectorAll("button[data-import-dn]").forEach((b) => b.addEventListener("click", async () => {
      const dn = b.getAttribute("data-import-dn");
      const name = b.getAttribute("data-import-name");
      if (!(await uiConfirm(`Импортировать группу «${name}» из AD и добавить её участников?`))) return;
      const orig = b.textContent; b.disabled = true; b.textContent = "Импорт…";
      try {
        const r = await API.adminAdImportGroup(dn, name);
        window.toast(`Импортировано: ${r.group.name}. Участников: ${r.added_members} (новых пользователей: ${r.created_users})`, "success");
        closeModal(); loadStats(); loadGroups();
      } catch (e) {
        window.toast(e.message, "error");
        b.disabled = false; b.textContent = orig;
      }
    }));
  }

  async function syncAdGroup(btn) {
    const gid = parseInt(btn.getAttribute("data-sync-ad"), 10);
    if (!(await uiConfirm("Синхронизировать участников и профили этой группы из AD?"))) return;
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = "Синхр…";
    try {
      const r = await API.adminAdSyncGroup(gid);
      window.toast(`AD синхронизирована: участников ${r.members}, новых ${r.created_users}`, "success");
      loadStats(); loadGroups();
    } catch (e) {
      window.toast(e.message, "error");
      btn.disabled = false; btn.textContent = old;
    }
  }


  function openEditGroup(btn) {
    const gid = parseInt(btn.getAttribute("data-edit-group"), 10);
    const row = document.querySelector(`tr[data-group-id="${gid}"]`);
    const isDefault = row && row.querySelector("td").textContent.includes("🏷️") || !!row.querySelector(".mui-icon-tag");
    const name = row ? row.querySelector("td div").textContent.replace("🏷️", "").trim() : "";
    const overlay = document.getElementById("modal-overlay") || createOverlay();
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>Изменить группу</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div class="field"><label>Название</label><input type="text" id="g-name" value="${esc(name)}" ${isDefault ? "disabled" : ""} /></div>
          ${isDefault ? '<div class="settings-sub">Это группа по умолчанию — переименовать нельзя.</div>' : ""}
          <div class="field"><label>Описание</label><input type="text" id="g-desc" placeholder="Краткое описание" /></div>
        </div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Отмена</button><button class="btn-primary inline" id="g-save">Сохранить</button></div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.getElementById("g-save").addEventListener("click", async () => {
      const patch = { description: document.getElementById("g-desc").value.trim() };
      if (!isDefault) patch.name = document.getElementById("g-name").value.trim();
      try {
        await API.adminUpdateGroup(gid, patch);
        window.toast("Сохранено", "success"); close(); loadGroups();
      } catch (e) { window.toast(e.message, "error"); }
    });
  }

  async function deleteGroup(btn) {
    const gid = parseInt(btn.getAttribute("data-del-group"), 10);
    if (!(await uiConfirm("Удалить группу? Её участники станут «без группы»."))) return;
    try {
      const r = await API.adminDeleteGroup(gid);
      window.toast("Группа удалена" + (r.freed_members ? ` (освобождено: ${r.freed_members})` : ""), "success");
      loadStats(); loadGroups();
    } catch (e) { window.toast(e.message, "error"); }
  }



  // ---------- System health ----------
  async function loadSystemHealth() {
    const content = document.getElementById("admin-content");
    content.innerHTML = `<div class="list-empty">Проверка системы…</div>`;
    let h;
    try { h = await API.adminSystemHealth(); }
    catch (e) { content.innerHTML = `<div class="list-empty">${esc(e.message)}</div>`; return; }
    const cards = [
      ["PostgreSQL", h.db, icon("database")],
      ["Redis", h.redis, icon("bolt")],
      ["LDAP", h.ldap, icon("contacts")],
      ["Kerberos SSO", h.sso, icon("lock")],
      ["Asterisk AMI", h.ami, icon("phone")],
      ["Uploads", h.uploads, icon("attach")],
    ];
    content.innerHTML = `
      <div class="system-toolbar">
        <div><h2>${icon("system")} Состояние системы</h2><div class="settings-sub">Версия: ${esc((h.app && h.app.version) || "")}</div></div>
        <button class="btn-secondary" id="health-refresh">${iconLabel("refresh", "Проверить снова")}</button>
      </div>
      <div class="system-health-grid">
        ${cards.map(([title, data, icon]) => healthCard(title, data || {}, icon)).join("")}
      </div>
      <div class="diag-card" style="margin-top:16px"><h3>Итог</h3><div class="diag-status ${h.ok ? "ok" : "bad"}">${h.ok ? "Все основные сервисы доступны" : "Есть проблемы — откройте карточки ниже"}</div></div>`;
    document.getElementById("health-refresh").addEventListener("click", loadSystemHealth);
  }

  function healthCard(title, data, icon) {
    const ok = !!data.ok;
    const rows = Object.entries(data).filter(([k]) => k !== "ok").map(([k, v]) => `<div class="diag-kv"><span>${esc(k)}</span><b>${esc(typeof v === "object" ? JSON.stringify(v) : String(v))}</b></div>`).join("");
    return `<section class="system-health-card ${ok ? "ok" : "bad"}">
      <div class="sh-top"><span class="sh-icon">${icon}</span><div><h3>${esc(title)}</h3><p>${ok ? "OK" : "Проблема"}</p></div></div>
      <div class="diag-kvs">${rows || '<div class="settings-sub">Нет деталей</div>'}</div>
    </section>`;
  }


  // ---------- AD / SSO diagnostics ----------
  async function loadDiagnostics() {
    const content = document.getElementById("admin-content");
    content.innerHTML = `<div class="list-empty">Загрузка диагностики…</div>`;
    let cfg = null;
    try { cfg = await API.adminDiagConfig(); } catch (e) { content.innerHTML = `<div class="list-empty">${esc(e.message)}</div>`; return; }
    content.innerHTML = `
      <div class="diag-wrap">
        <div class="diag-toolbar">
          <div><h2>${icon("diagnostics")} AD / SSO диагностика</h2><div class="settings-sub">Проверка LDAP, Kerberos keytab, SPN и текущих настроек без паролей.</div></div>
          <button class="btn-secondary" id="diag-refresh">${iconLabel("refresh", "Обновить")}</button>
        </div>
        <div class="diag-grid">
          <div class="diag-card"><h3>Конфигурация</h3>${diagConfigHtml(cfg)}</div>
          <div class="diag-card"><h3>Kerberos keytab</h3><div id="diag-keytab">Нажмите «Проверить keytab»</div><button class="btn-secondary" id="diag-keytab-btn">Проверить keytab</button></div>
          <div class="diag-card"><h3>LDAP bind</h3><div id="diag-bind">Проверка сервисной учётки LDAP.</div><button class="btn-secondary" id="diag-bind-btn">Проверить bind</button></div>
          <div class="diag-card"><h3>SPN</h3><div id="diag-spn">SPN из настроек: <b>${esc(cfg.spn || "")}</b></div><button class="btn-secondary" id="diag-spn-btn">Найти SPN в AD</button></div>
          <div class="diag-card"><h3>Поиск пользователя</h3><div class="diag-line"><input id="diag-user-q" placeholder="логин / ФИО / email"><button class="btn-secondary" id="diag-user-btn">Поиск</button></div><div id="diag-user-out"></div></div>
          <div class="diag-card"><h3>Поиск группы AD</h3><div class="diag-line"><input id="diag-group-q" placeholder="название группы"><button class="btn-secondary" id="diag-group-btn">Поиск</button></div><div id="diag-group-out"></div></div>
        </div>
        <div class="diag-card diag-hints"><h3>Подсказки</h3>${(cfg.hints || []).map((h) => `<div>💡 ${esc(h)}</div>`).join("")}</div>
      </div>`;
    document.getElementById("diag-refresh").addEventListener("click", loadDiagnostics);
    document.getElementById("diag-keytab-btn").addEventListener("click", runKeytabDiag);
    document.getElementById("diag-bind-btn").addEventListener("click", runBindDiag);
    document.getElementById("diag-spn-btn").addEventListener("click", runSpnDiag);
    document.getElementById("diag-user-btn").addEventListener("click", runUserDiag);
    document.getElementById("diag-group-btn").addEventListener("click", runGroupDiag);
  }

  function diagConfigHtml(cfg) {
    const rows = Object.entries(cfg.settings || {}).map(([k, v]) => `<div class="diag-kv"><span>${esc(k)}</span><b>${esc(String(v))}</b></div>`).join("");
    const kt = cfg.keytab || {};
    return `<div class="diag-kvs">${rows}</div><div class="diag-status ${kt.exists && kt.readable ? "ok" : "bad"}">keytab: ${esc(kt.path || "")} · exists=${!!kt.exists} · readable=${!!kt.readable} · size=${kt.size || 0}</div>`;
  }

  function diagResultHtml(r) {
    const ok = !!(r && r.ok);
    return `<div class="diag-status ${ok ? "ok" : "bad"}">${ok ? "OK" : "Ошибка"}</div><pre class="diag-pre">${esc(JSON.stringify(r, null, 2))}</pre>`;
  }

  async function runKeytabDiag() { const el = document.getElementById("diag-keytab"); el.innerHTML = "Проверка…"; try { el.innerHTML = diagResultHtml(await API.adminDiagKeytab()); } catch (e) { el.innerHTML = `<div class="diag-status bad">${esc(e.message)}</div>`; } }
  async function runBindDiag() { const el = document.getElementById("diag-bind"); el.innerHTML = "Проверка…"; try { el.innerHTML = diagResultHtml(await API.adminDiagLdapBind()); } catch (e) { el.innerHTML = `<div class="diag-status bad">${esc(e.message)}</div>`; } }
  async function runSpnDiag() { const el = document.getElementById("diag-spn"); el.innerHTML = "Поиск…"; try { el.innerHTML = diagResultHtml(await API.adminDiagSpn()); } catch (e) { el.innerHTML = `<div class="diag-status bad">${esc(e.message)}</div>`; } }
  async function runUserDiag() { const el = document.getElementById("diag-user-out"), q = document.getElementById("diag-user-q").value.trim(); if (!q) return; el.innerHTML = "Поиск…"; try { el.innerHTML = diagResultHtml(await API.adminDiagLdapUser(q)); } catch (e) { el.innerHTML = `<div class="diag-status bad">${esc(e.message)}</div>`; } }
  async function runGroupDiag() { const el = document.getElementById("diag-group-out"), q = document.getElementById("diag-group-q").value.trim(); if (!q) return; el.innerHTML = "Поиск…"; try { el.innerHTML = diagResultHtml(await API.adminDiagLdapGroup(q)); } catch (e) { el.innerHTML = `<div class="diag-status bad">${esc(e.message)}</div>`; } }


  // ---------- Server settings ----------
  async function loadSettings() {
    try {
      const s = await API.adminGetSettings();
      const content = document.getElementById("admin-content");
      content.innerHTML = `
        <div class="settings-form settings-form-wide">
          <div class="settings-hero">
            <div><h2>${icon("settings")} Настройки приложения</h2><div class="settings-sub">Основные политики сервера, безопасность, файлы и оформление.</div></div>
            <button class="btn-primary inline" id="s-save-top">Сохранить всё</button>
          </div>
          <div class="settings-grid-admin">
            <section class="settings-card-admin">
              <h3>📎 Файлы и вложения</h3>
              <p>Лимиты загрузки для документов, изображений и аватаров.</p>
              <div class="setting-row"><div><label>Макс. размер файла</label><div class="settings-sub">Документы, изображения, архивы</div></div><input type="number" id="s-upload" min="1" max="2048" value="${s.max_upload_mb}" /><span class="unit">МБ</span></div>
              <div class="setting-row"><div><label>Макс. размер аватара</label><div class="settings-sub">Фото профиля и групп</div></div><input type="number" id="s-avatar" min="1" max="64" value="${s.max_avatar_mb}" /><span class="unit">МБ</span></div>
            </section>
            <section class="settings-card-admin">
              <h3>🔐 Безопасность и вход</h3>
              <p>Локальные аккаунты можно оставить как резервный вход для администратора.</p>
              <div class="setting-row"><div><label>Мин. длина пароля</label><div class="settings-sub">Для локальных аккаунтов</div></div><input type="number" id="s-pwlen" min="4" max="64" value="${s.password_min_length}" /></div>
              <div class="setting-row"><div><label>Локальный вход</label><div class="settings-sub">Логин/пароль приложения</div></div><label class="switch"><input type="checkbox" id="s-local" ${s.allow_local_auth ? "checked" : ""}><span class="slider"></span></label></div>
              <div class="setting-row"><div><label>Active Directory</label><div class="settings-sub">Использовать настроенное LDAP-подключение</div></div><label class="switch"><input type="checkbox" id="s-ldap" ${s.ldap_enabled ? "checked" : ""}><span class="slider"></span></label></div>
            </section>
            <section class="settings-card-admin">
              <h3>🎨 Брендинг</h3>
              <p>Название и основной цвет интерфейса.</p>
              <div class="setting-row"><div><label>Название приложения</label><div class="settings-sub">Показывается в интерфейсе</div></div><input type="text" id="s-title" maxlength="64" value="${esc(s.app_title)}" /></div>
              <div class="setting-row"><div><label>Основной цвет</label><div class="settings-sub">Кнопки и акценты</div></div><input type="color" id="s-color" value="${esc(s.brand_color)}" /></div>
            </section>
            <section class="settings-card-admin">
              <h3>${icon("phone")} IP АТС / AMI</h3>
              <p>Настройки подключения задаются в .env и применяются после пересоздания backend.</p>
              <div class="settings-sub env-list">AMI_ENABLED, AMI_HOST, AMI_PORT, AMI_USERNAME, AMI_SECRET</div>
              <button class="btn-secondary" type="button" id="open-diag-from-settings">Открыть диагностику AD/SSO</button>
            </section>
          </div>
          <div class="settings-actions settings-actions-sticky">
            <button class="btn-primary inline" id="s-save">Сохранить настройки</button>
          </div>
        </div>`;
      document.getElementById("s-save").addEventListener("click", saveSettings);
      document.getElementById("s-save-top").addEventListener("click", saveSettings);
      const diag = document.getElementById("open-diag-from-settings");
      if (diag) diag.addEventListener("click", () => {
        currentTab = "diagnostics";
        document.querySelectorAll(".admin-tab").forEach((x) => x.classList.toggle("active", x.getAttribute("data-tab") === "diagnostics"));
        loadDiagnostics();
      });
    } catch (e) { window.toast(e.message, "error"); }
  }


  async function saveSettings() {
    const patch = {
      max_upload_mb: parseInt(document.getElementById("s-upload").value, 10),
      max_avatar_mb: parseInt(document.getElementById("s-avatar").value, 10),
      password_min_length: parseInt(document.getElementById("s-pwlen").value, 10),
      allow_local_auth: document.getElementById("s-local").checked,
      ldap_enabled: document.getElementById("s-ldap").checked,
      app_title: document.getElementById("s-title").value.trim(),
      brand_color: document.getElementById("s-color").value,
    };
    try {
      await API.adminUpdateSettings(patch);
      window.toast("Настройки сохранены", "success");
    } catch (e) { window.toast(e.message, "error"); }
  }

  // ---------- Broadcast ----------
  function openBroadcast() {
    const overlay = document.getElementById("modal-overlay") || createOverlay();
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>${icon("broadcast")} Рассылка всем онлайн</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div class="field"><label>Сообщение</label><textarea id="bc-text" rows="3" style="width:100%;padding:12px;border-radius:10px;border:1.5px solid var(--border);background:var(--bg);color:var(--text)" placeholder="Введите объявление..."></textarea></div>
        </div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Отмена</button><button class="btn-primary inline" id="bc-send">Отправить</button></div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.getElementById("bc-send").addEventListener("click", async () => {
      const text = document.getElementById("bc-text").value.trim();
      if (!text) return;
      try { const r = await API.adminBroadcast(text); window.toast("Доставлено: " + r.delivered + " польз.", "success"); close(); }
      catch (e) { window.toast(e.message, "error"); }
    });
  }


  // ---------- Cleanup histories ----------
  function openCleanupModal() {
    const overlay = document.getElementById("modal-overlay") || createOverlay();
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h2>🧹 Очистка историй</h2><button class="modal-close">✕</button></div>
        <div class="modal-body">
          <div class="settings-warn">Удаление необратимо. Очистка удаляет записи истории за выбранный период: журнал аудита, историю вызовов и/или историю просмотров/скачиваний файлов.</div>
          <div class="field"><label>Раздел</label><select id="cleanup-target" class="set-select">
            <option value="audit">Журнал аудита</option>
            <option value="calls">История вызовов</option>
            <option value="downloads">История файлов</option>
            <option value="all">Все истории</option>
          </select></div>
          <div class="field"><label>Период</label><select id="cleanup-period" class="set-select">
            <option value="day">За сутки</option>
            <option value="7d">За 7 дней</option>
            <option value="month">За месяц</option>
            <option value="year">За год</option>
            <option value="all">Полностью</option>
          </select></div>
          <div class="settings-sub">Например, «За 7 дней» удалит записи, созданные за последние 7 дней. «Полностью» удалит весь выбранный журнал.</div>
        </div>
        <div class="modal-footer"><button class="btn-secondary" id="modal-cancel">Отмена</button><button class="btn-danger" id="cleanup-run">Очистить</button></div>
      </div>`;
    overlay.classList.add("show");
    function close() { overlay.classList.remove("show"); overlay.innerHTML = ""; }
    overlay.querySelector(".modal-close").addEventListener("click", close);
    document.getElementById("modal-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.getElementById("cleanup-run").addEventListener("click", async () => {
      const target = document.getElementById("cleanup-target").value;
      const period = document.getElementById("cleanup-period").value;
      if (!(await uiConfirm("Подтвердите очистку. Данные будут удалены без восстановления."))) return;
      try {
        const r = await API.adminCleanupHistory(target, period);
        const total = Object.values(r.deleted || {}).reduce((a, b) => a + (b || 0), 0);
        window.toast("Удалено записей: " + total, "success");
        close();
        loadStats();
        loadTab(currentTab);
      } catch (e) { window.toast(e.message, "error"); }
    });
  }

  function createOverlay() {
    const o = document.createElement("div");
    o.className = "modal-overlay"; o.id = "modal-overlay";
    document.body.appendChild(o);
    return o;
  }

  function initials(name) {
    if (!name) return "?";
    const p = name.trim().split(/\s+/);
    if (p.length >= 2) return (p[0][0] + p[1][0]).toUpperCase();
    return name.slice(0, 2).toUpperCase();
  }
  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }

  window.AdminView = { mount };
})();
