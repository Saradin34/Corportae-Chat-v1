/* ============================================================
   Admin panel: stats, users, chats, audit log, broadcast.
   Tabbed interface.
   ============================================================ */
(function () {
  "use strict";

  const app = () => document.getElementById("app");
  let currentTab = "users";

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
          <button class="icon-btn" id="admin-back" title="Назад">‹</button>
          <h1>🛡️ Админ-панель</h1>
          <button class="btn-secondary" id="admin-broadcast-btn">📢 Рассылка</button>
          <button class="btn-secondary" id="admin-refresh">⟳ Обновить</button>
        </div>
        <div class="admin-tabs">
          <button class="admin-tab active" data-tab="users">👥 Пользователи</button>
          <button class="admin-tab" data-tab="groups">📇 Группы</button>
          <button class="admin-tab" data-tab="chats">💬 Чаты</button>
          <button class="admin-tab" data-tab="audit">📋 Журнал</button>
          <button class="admin-tab" data-tab="settings">⚙️ Настройки</button>
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
    document.querySelectorAll(".admin-tab").forEach((t) => t.addEventListener("click", function () {
      document.querySelectorAll(".admin-tab").forEach((x) => x.classList.remove("active"));
      this.classList.add("active");
      currentTab = this.getAttribute("data-tab");
      loadTab(currentTab);
    }));

    await loadStats();
    await loadTab("users");
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
    if (tab === "users") return loadUsers("");
    if (tab === "groups") return loadGroups();
    if (tab === "chats") return loadAdminChats();
    if (tab === "audit") return loadAudit();
    if (tab === "settings") return loadSettings();
  }

  // ---------- Users ----------
  let _groupsCache = [];
  async function loadUsers(q) {
    try {
      const [users, groups] = await Promise.all([API.adminUsers(q), API.adminGroups().catch(() => [])]);
      _groupsCache = groups;
      const me = API.Store.getUser();
      const content = document.getElementById("admin-content");
      content.innerHTML = `
        <div class="search-box" style="margin-bottom:16px;max-width:360px"><input type="text" id="admin-search" placeholder="Поиск пользователей..." value="${esc(q)}" /></div>
        <div class="admin-table">
          <table>
            <thead><tr><th>ID</th><th>Пользователь</th><th>Email</th><th>Группа</th><th>Роль</th><th>Статус</th><th>Действия</th></tr></thead>
            <tbody>${users.map((u) => userRow(u, me)).join("")}</tbody>
          </table>
        </div>`;
      const search = document.getElementById("admin-search");
      let t = null;
      search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => loadUsers(search.value.trim()), 250); });
      content.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", () => handleUserAction(b)));
      content.querySelectorAll("select[data-group-for]").forEach((sel) => sel.addEventListener("change", () => handleGroupAssign(sel)));
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
    return `<tr>
      <td>${u.id}</td>
      <td><div style="display:flex;align-items:center;gap:8px">
        <div class="avatar sm" style="background:${esc(u.avatar_color)}">${initials(u.full_name || u.username)}${u.is_online ? '<span class="online-dot"></span>' : ""}</div>
        <div><div style="font-weight:600">${esc(u.full_name || u.username)} ${u.auth_source === "ldap" ? '<span class="badge ad">AD</span>' : ""}</div><div class="settings-sub">@${esc(u.username)}</div></div></div></td>
      <td>${esc(u.email)}</td>
      <td>${groupSelectHtml(u)}</td>
      <td><span class="badge ${u.role}">${u.role === "admin" ? "Админ" : "Юзер"}</span></td>
      <td><span class="badge ${u.is_active ? "on" : "off"}">${u.is_active ? "Активен" : "Заблокирован"}</span></td>
      <td><div class="table-actions">
        ${u.id === me.id ? '<span class="settings-sub">— это вы —</span>' : `
          <button class="mini-btn ${u.role === "admin" ? "" : "primary"}" data-act="role" data-id="${u.id}" data-role="${u.role === "admin" ? "user" : "admin"}">${u.role === "admin" ? "Снять админа" : "Сделать админом"}</button>
          <button class="mini-btn" data-act="toggle" data-id="${u.id}">${u.is_active ? "Заблокировать" : "Разблокировать"}</button>
          <button class="mini-btn" data-act="resetpw" data-id="${u.id}">Сброс пароля</button>
          <button class="mini-btn danger" data-act="delete" data-id="${u.id}">Удалить</button>`}
      </div></td></tr>`;
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
      if (act === "role") { await API.adminSetRole(id, btn.getAttribute("data-role")); window.toast("Роль обновлена", "success"); }
      else if (act === "toggle") { const u = await API.adminToggleActive(id); window.toast(u.is_active ? "Разблокирован" : "Заблокирован", "success"); }
      else if (act === "resetpw") {
        const pw = prompt("Новый пароль (мин. 6 символов):");
        if (!pw) return;
        if (pw.length < 6) { window.toast("Слишком короткий пароль", "error"); return; }
        await API.adminResetPassword(id, pw); window.toast("Пароль сброшен", "success");
      } else if (act === "delete") {
        if (!confirm("Удалить пользователя? Необратимо.")) return;
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
                <td>${c.id}</td>
                <td style="font-weight:600">${esc(c.name)}</td>
                <td><span class="badge ${c.type === "private" ? "user" : "admin"}">${c.type === "private" ? "Личный" : c.type === "group" ? "Группа" : c.type}</span></td>
                <td>${c.members}</td>
                <td>${c.messages}</td>
                <td class="settings-sub">${c.created_at ? new Date(c.created_at).toLocaleDateString("ru-RU") : "—"}</td>
                <td><button class="mini-btn danger" data-del-chat="${c.id}">Удалить</button></td>
              </tr>`).join("")}</tbody>
          </table>
        </div>`;
      content.querySelectorAll("button[data-del-chat]").forEach((b) => b.addEventListener("click", async () => {
        if (!confirm("Удалить чат и все его сообщения?")) return;
        try { await API.adminDeleteChat(parseInt(b.getAttribute("data-del-chat"), 10)); window.toast("Чат удалён", "success"); loadStats(); loadAdminChats(); }
        catch (e) { window.toast(e.message, "error"); }
      }));
    } catch (e) { window.toast(e.message, "error"); }
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
                <td class="settings-sub">${new Date(l.created_at).toLocaleString("ru-RU")}</td>
                <td style="font-weight:600">${esc(l.actor_name)}</td>
                <td><span class="badge admin">${esc(actionLabel(l.action))}</span></td>
                <td>${esc(l.details)}</td>
              </tr>`).join("")}</tbody>
          </table>
        </div>`;
    } catch (e) { window.toast(e.message, "error"); }
  }

  function actionLabel(a) {
    const map = { ban: "Блокировка", unban: "Разблокировка", set_role: "Смена роли", reset_password: "Сброс пароля", delete_user: "Удаление юзера", delete_chat: "Удаление чата", broadcast: "Рассылка" };
    return map[a] || a;
  }

  // ---------- Groups (departments + permissions, TrueConf-style) ----------
  // Permission columns: [flag, icon, tooltip]
  const PERM_COLS = [
    ["can_send_messages", "✍️", "Писать сообщения"],
    ["can_create_private", "💬", "Создавать личные чаты"],
    ["can_create_groups", "👥", "Создавать группы"],
    ["can_send_files", "📎", "Отправлять файлы"],
    ["can_send_images", "🖼️", "Отправлять изображения"],
    ["can_forward", "↪️", "Пересылать сообщения"],
    ["can_pin", "📌", "Закреплять сообщения"],
    ["can_edit_own", "✏️", "Редактировать свои сообщения"],
    ["can_delete_own", "🗑️", "Удалять свои сообщения"],
    ["can_react", "👍", "Ставить реакции"],
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
              ${PERM_COLS.map(([f, ic, t]) => `<th class="perm-col" title="${esc(t)}">${ic}</th>`).join("")}
              <th></th>
            </tr></thead>
            <tbody>${groups.map(groupRow).join("")}</tbody>
          </table>
        </div>`;
      document.getElementById("group-add").addEventListener("click", openCreateGroup);
      content.querySelectorAll("input[data-perm]").forEach((cb) => cb.addEventListener("change", () => togglePerm(cb)));
      content.querySelectorAll("button[data-del-group]").forEach((b) => b.addEventListener("click", () => deleteGroup(b)));
      content.querySelectorAll("button[data-edit-group]").forEach((b) => b.addEventListener("click", () => openEditGroup(b)));
    } catch (e) { window.toast(e.message, "error"); }
  }

  function groupRow(g) {
    return `<tr data-group-id="${g.id}">
      <td>
        <div style="font-weight:600">${g.is_default ? "🏷️ " : ""}${esc(g.name)}</div>
        ${g.ad_group_dn ? '<div class="settings-sub"><span class="badge ad">AD</span> синхронизирована</div>' : (g.description ? `<div class="settings-sub">${esc(g.description)}</div>` : "")}
      </td>
      <td class="num">${g.member_count}</td>
      ${PERM_COLS.map(([f]) => `<td class="perm-col"><input type="checkbox" data-perm="${f}" data-group="${g.id}" ${g[f] ? "checked" : ""} /></td>`).join("")}
      <td><div class="table-actions">
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
            <div class="ad-import-title">🔎 Поиск группы в Active Directory</div>
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
      if (!confirm(`Импортировать группу «${name}» из AD и добавить её участников?`)) return;
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

  function openEditGroup(btn) {
    const gid = parseInt(btn.getAttribute("data-edit-group"), 10);
    const row = document.querySelector(`tr[data-group-id="${gid}"]`);
    const isDefault = row && row.querySelector("td").textContent.includes("🏷️");
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
    if (!confirm("Удалить группу? Её участники станут «без группы».")) return;
    try {
      const r = await API.adminDeleteGroup(gid);
      window.toast("Группа удалена" + (r.freed_members ? ` (освобождено: ${r.freed_members})` : ""), "success");
      loadStats(); loadGroups();
    } catch (e) { window.toast(e.message, "error"); }
  }

  // ---------- Server settings ----------
  async function loadSettings() {
    try {
      const s = await API.adminGetSettings();
      const content = document.getElementById("admin-content");
      content.innerHTML = `
        <div class="settings-form">
          <h3 class="settings-group-title">Файлы и вложения</h3>
          <div class="setting-row"><label>Макс. размер файла (МБ)</label><input type="number" id="s-upload" min="1" max="2048" value="${s.max_upload_mb}" /></div>
          <div class="setting-row"><label>Макс. размер аватара (МБ)</label><input type="number" id="s-avatar" min="1" max="64" value="${s.max_avatar_mb}" /></div>

          <h3 class="settings-group-title">Безопасность</h3>
          <div class="setting-row"><label>Мин. длина пароля</label><input type="number" id="s-pwlen" min="4" max="64" value="${s.password_min_length}" /></div>
          <div class="setting-row"><label>Локальный вход (логин/пароль)</label>
            <label class="switch"><input type="checkbox" id="s-local" ${s.allow_local_auth ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="setting-row"><label>Вход через Active Directory</label>
            <label class="switch"><input type="checkbox" id="s-ldap" ${s.ldap_enabled ? "checked" : ""}><span class="slider"></span></label></div>
          <div class="settings-sub">Переключатель AD здесь включает/выключает использование уже настроенного подключения. Параметры серверов AD задаются в переменных окружения.</div>

          <h3 class="settings-group-title">Брендинг</h3>
          <div class="setting-row"><label>Название приложения</label><input type="text" id="s-title" maxlength="64" value="${esc(s.app_title)}" /></div>
          <div class="setting-row"><label>Основной цвет</label><input type="color" id="s-color" value="${esc(s.brand_color)}" /></div>

          <div class="settings-actions">
            <button class="btn-primary inline" id="s-save">Сохранить настройки</button>
          </div>
        </div>`;
      document.getElementById("s-save").addEventListener("click", saveSettings);
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
        <div class="modal-header"><h2>📢 Рассылка всем онлайн</h2><button class="modal-close">✕</button></div>
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
