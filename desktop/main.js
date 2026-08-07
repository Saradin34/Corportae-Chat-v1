/* ============================================================
   Corporate Chat — Electron main process.
   Wraps the web frontend in a native desktop window. The server
   URL is configurable (first-run setup), so the same build works
   against any deployment (localhost Docker or a company server).

   Window-close behaviour (as requested):
   - Clicking [X] does NOT quit the app — it MINIMIZES the window to
     the taskbar (the bar with Start). The app keeps running and its
     button stays visible in the taskbar.
   - To actually close: right-click the taskbar button → "Закрыть окно"
     (the window is minimized at that moment, so this performs a real
     close), or use the in-app menu «Файл → Закрыть приложение».
   ============================================================ */
const { app, BrowserWindow, Menu, shell, ipcMain, dialog, session, nativeImage } = require("electron");
const path = require("path");
const fs = require("fs");

const CONFIG_PATH = path.join(app.getPath("userData"), "config.json");
const DEFAULT_SERVER = "https://chat.kupava.by";
const ICON_PATH = path.join(__dirname, "assets", process.platform === "win32" ? "icon.ico" : "icon.png");

let mainWindow = null;
let isQuitting = false;

// ----- single instance: focus existing window instead of opening a new one -----
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => { showWindow(); });
}

function loadConfig() {
  try {
    return Object.assign({ server: DEFAULT_SERVER, keepInTray: true, noProxy: false, clearCacheOnStart: true }, JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8")));
  } catch (e) { return { server: DEFAULT_SERVER, keepInTray: true, noProxy: false, clearCacheOnStart: true }; }
}
function saveConfig(patch) {
  const cfg = Object.assign(loadConfig(), patch);
  try { fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2)); } catch (e) {}
  return cfg;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 360,
    minHeight: 560,
    title: "Corporate Chat",
    backgroundColor: "#17212b",
    icon: ICON_PATH,
    skipTaskbar: false,           // keep the button in the taskbar
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });

  Menu.setApplicationMenu(buildMenu());

  const cfg = loadConfig();
  // Corporate build: open the company server on first launch. Users can still
  // change it later via File → Change server.
  loadApp(cfg.server || DEFAULT_SERVER);

  // open external links in the system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http")) { shell.openExternal(url); return { action: "deny" }; }
    return { action: "allow" };
  });

  // Native right-click edit menu for inputs/textareas/contenteditable areas.
  // Gives desktop users the expected Undo/Redo/Cut/Copy/Paste/Delete/Select All
  // menu instead of the browser's inconsistent default.
  mainWindow.webContents.on("context-menu", (event, params) => {
    buildEditorContextMenu(params).popup({ window: mainWindow });
  });

  /* ---- The key behaviour ----
     If "keep in tray" is ON (default): [X] while visible → minimize to taskbar;
     a close that arrives while minimized (taskbar → "Закрыть окно") → real close.
     If "keep in tray" is OFF: [X] closes the app normally. */
  mainWindow.on("close", (e) => {
    if (isQuitting) return;                  // explicit quit → allow
    const cfg = loadConfig();
    if (cfg.keepInTray === false) return;    // user opted out → normal close
    if (mainWindow.isMinimized()) return;    // taskbar "Закрыть окно" → real close
    e.preventDefault();
    mainWindow.minimize();                   // [X] → just minimize to taskbar
  });

  // Stop the taskbar flash as soon as the window is brought to the front.
  mainWindow.on("focus", () => { try { mainWindow.flashFrame(false); } catch (e) {} });

  mainWindow.on("closed", () => { mainWindow = null; });
}

function showWindow() {
  if (!mainWindow) { createWindow(); return; }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function sendShortcut(key, modifiers) {
  if (!mainWindow) return;
  try {
    mainWindow.webContents.sendInputEvent({ type: "keyDown", keyCode: key, modifiers: modifiers || ["control"] });
    mainWindow.webContents.sendInputEvent({ type: "keyUp", keyCode: key, modifiers: modifiers || ["control"] });
  } catch (e) {}
}

function wcEdit(action) {
  if (!mainWindow || !mainWindow.webContents) return;
  try {
    const wc = mainWindow.webContents;
    if (typeof wc[action] === "function") wc[action]();
  } catch (e) {}
}

function writeClipboardText(text) {
  try { require("electron").clipboard.writeText(String(text || "")); } catch (e) {}
}

function buildEditorContextMenu(params) {
  const isEditable = !!params.isEditable;
  const hasSelection = !!(params.selectionText && params.selectionText.length);
  const editFlags = params.editFlags || {};
  const canUndo = isEditable; // keep enabled: Chromium editFlags can be unreliable in embedded pages
  const canRedo = isEditable;
  const canCut = isEditable && hasSelection;
  const canCopy = hasSelection;
  const canPaste = isEditable; // always allow paste in editable fields; web page decides what it accepts
  const canDelete = isEditable && hasSelection;
  const canSelectAll = isEditable || hasSelection;
  const template = [];

  if (params.linkURL) {
    template.push(
      { label: "Открыть ссылку в браузере", click: () => shell.openExternal(params.linkURL) },
      { label: "Копировать ссылку", click: () => writeClipboardText(params.linkURL) },
      { type: "separator" },
    );
  }

  if (params.srcURL && params.mediaType === "image") {
    template.push(
      { label: "Открыть изображение в браузере", click: () => shell.openExternal(params.srcURL) },
      { label: "Копировать адрес изображения", click: () => writeClipboardText(params.srcURL) },
      { type: "separator" },
    );
  }

  template.push(
    { label: "Отменить", accelerator: "Ctrl+Z", enabled: canUndo, click: () => wcEdit("undo") },
    { label: "Повторить", accelerator: "Ctrl+Y", enabled: canRedo, click: () => wcEdit("redo") },
    { type: "separator" },
    { label: "Вырезать", accelerator: "Ctrl+X", enabled: canCut, click: () => wcEdit("cut") },
    { label: "Копировать", accelerator: "Ctrl+C", enabled: canCopy, click: () => wcEdit("copy") },
    { label: "Вставить", accelerator: "Ctrl+V", enabled: canPaste, click: () => wcEdit("paste") },
    { label: "Вставить без форматирования", accelerator: "Ctrl+Shift+V", enabled: canPaste, click: () => wcEdit("pasteAndMatchStyle") },
    { label: "Удалить", accelerator: "Del", enabled: canDelete, click: () => wcEdit("delete") },
    { type: "separator" },
    {
      label: "Форматирование",
      enabled: isEditable,
      submenu: [
        { label: "Жирный", accelerator: "Ctrl+B", click: () => sendShortcut("B") },
        { label: "Курсив", accelerator: "Ctrl+I", click: () => sendShortcut("I") },
        { label: "Подчёркнутый", accelerator: "Ctrl+U", click: () => sendShortcut("U") },
        { type: "separator" },
        { label: "Моноширинный", accelerator: "Ctrl+Shift+M", click: () => sendShortcut("M", ["control", "shift"]) },
      ],
    },
    { type: "separator" },
    { label: "Выбрать всё", accelerator: "Ctrl+A", enabled: canSelectAll, click: () => wcEdit("selectAll") },
  );

  if (!isEditable && !hasSelection && !params.linkURL && !params.srcURL) {
    template.push(
      { type: "separator" },
      { label: "Назад", enabled: mainWindow.webContents.canGoBack(), click: () => mainWindow.webContents.goBack() },
      { label: "Вперёд", enabled: mainWindow.webContents.canGoForward(), click: () => mainWindow.webContents.goForward() },
      { label: "Перезагрузить", click: () => mainWindow.reload() },
      { label: "Жёстко обновить", click: () => hardReload() },
    );
  }

  return Menu.buildFromTemplate(template);
}

function loadApp(server) {
  const url = normalizeServer(server);
  mainWindow.loadURL(url).catch(() => showConnError(url));
  mainWindow.webContents.removeAllListeners("did-fail-load");
  mainWindow.webContents.on("did-fail-load", (e, code, desc, validatedURL) => {
    if (validatedURL && validatedURL.startsWith(url)) showConnError(url);
  });
}

function normalizeServer(s) {
  s = (s || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\//.test(s)) s = "http://" + s;
  return s;
}


async function clearWebCache() {
  try { await session.defaultSession.clearCache(); } catch (e) {}
  try { await session.defaultSession.clearStorageData({ storages: ["serviceworkers", "cachestorage"] }); } catch (e) {}
}

function hardReload() {
  if (!mainWindow) return;
  clearWebCache().finally(() => {
    try { mainWindow.webContents.reloadIgnoringCache(); }
    catch (e) { try { mainWindow.reload(); } catch (e2) {} }
  });
}

function currentServer() {
  return normalizeServer(loadConfig().server || DEFAULT_SERVER);
}

async function changeServerDialog() {
  const cfg = loadConfig();
  const res = await dialog.showMessageBox(mainWindow, {
    type: "question",
    title: "Сменить сервер",
    message: "Открыть экран настройки адреса сервера?",
    detail: "Текущий адрес: " + (cfg.server || "не задан") + "\nПосле сохранения приложение перезагрузит веб-интерфейс.",
    buttons: ["Открыть настройку", "Отмена"],
    defaultId: 0,
    cancelId: 1,
  });
  if (res.response === 0) loadSetup();
}

function showConnError(url) {
  const html = setupHtml(url, "Не удалось подключиться к серверу. Проверьте адрес и доступность.");
  mainWindow.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
}

function loadSetup() {
  const html = setupHtml(DEFAULT_SERVER, "");
  mainWindow.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
}

function setupHtml(prefill, error) {
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
  <style>
    body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:linear-gradient(135deg,#3390ec,#5eb5f7);
      height:100vh;margin:0;display:flex;align-items:center;justify-content:center;color:#000}
    .card{background:#fff;border-radius:18px;padding:40px;max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,.3);text-align:center}
    .logo{width:84px;height:84px;border-radius:50%;background:linear-gradient(135deg,#3390ec,#5eb5f7);color:#fff;
      font-size:42px;font-weight:700;display:flex;align-items:center;justify-content:center;margin:0 auto 18px}
    h1{font-size:22px;margin:0 0 6px} p{color:#707579;font-size:14px;margin:0 0 22px}
    input{width:100%;box-sizing:border-box;padding:13px 14px;border:1.5px solid #e4e4e7;border-radius:10px;font-size:15px;margin-bottom:14px}
    input:focus{outline:none;border-color:#3390ec}
    button{width:100%;padding:13px;background:#3390ec;color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer}
    button:hover{background:#2b7cd3}
    .err{background:#fde8e8;color:#c0392b;padding:10px;border-radius:8px;font-size:13px;margin-bottom:14px}
    .hint{font-size:12px;color:#9aa0a6;margin-top:12px}
  </style></head><body>
    <div class="card">
      <div class="logo">C</div>
      <h1>Corporate Chat</h1>
      <p>Укажите адрес сервера вашей компании</p>
      ${error ? `<div class="err">${error}</div>` : ""}
      <input id="srv" value="${prefill}" placeholder="http://localhost или http://chat.company.local" />
      <button onclick="go()">Подключиться</button>
      <div class="hint">Адрес можно изменить позже в меню «Файл → Сменить сервер»</div>
    </div>
    <script>
      function go(){
        const v = document.getElementById('srv').value;
        if (window.electronSetup) window.electronSetup.connect(v);
      }
      document.getElementById('srv').addEventListener('keydown', function(e){ if(e.key==='Enter') go(); });
    </script>
  </body></html>`;
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  return Menu.buildFromTemplate([
    ...(isMac ? [{ role: "appMenu" }] : []),
    {
      label: "Файл",
      submenu: [
        { label: "Сменить сервер…", click: () => changeServerDialog() },
        { label: "Перезагрузить", accelerator: "CmdOrCtrl+R", click: () => mainWindow && mainWindow.reload() },
        { label: "Жёстко обновить (очистить кэш)", accelerator: "CmdOrCtrl+Shift+R", click: () => hardReload() },
        { label: "Очистить кэш", click: async () => { await clearWebCache(); dialog.showMessageBox(mainWindow, { message: "Кэш очищен", type: "info" }); } },
        { label: "Свернуть", accelerator: "CmdOrCtrl+M", click: () => mainWindow && mainWindow.minimize() },
        { type: "separator" },
        { label: "Закрыть приложение", accelerator: "CmdOrCtrl+Q", click: () => { isQuitting = true; app.quit(); } },
      ],
    },
    {
      label: "Правка",
      submenu: [
        { label: "Отменить", accelerator: "CmdOrCtrl+Z", click: () => wcEdit("undo") },
        { label: "Повторить", accelerator: "CmdOrCtrl+Y", click: () => wcEdit("redo") },
        { type: "separator" },
        { label: "Вырезать", accelerator: "CmdOrCtrl+X", click: () => wcEdit("cut") },
        { label: "Копировать", accelerator: "CmdOrCtrl+C", click: () => wcEdit("copy") },
        { label: "Вставить", accelerator: "CmdOrCtrl+V", click: () => wcEdit("paste") },
        { label: "Вставить без форматирования", accelerator: "CmdOrCtrl+Shift+V", click: () => wcEdit("pasteAndMatchStyle") },
        { label: "Удалить", click: () => wcEdit("delete") },
        { type: "separator" },
        { label: "Выделить всё", accelerator: "CmdOrCtrl+A", click: () => wcEdit("selectAll") },
      ],
    },
    {
      label: "Вид",
      submenu: [
        { role: "resetZoom", label: "Сбросить масштаб" },
        { role: "zoomIn", label: "Увеличить" },
        { role: "zoomOut", label: "Уменьшить" },
        { type: "separator" },
        { role: "togglefullscreen", label: "Полный экран" },
        { role: "toggleDevTools", label: "Инструменты разработчика" },
      ],
    },
    {
      label: "Справка",
      submenu: [
        { label: "Как закрыть приложение", click: () => dialog.showMessageBox(mainWindow, {
          title: "Закрытие приложения",
          message: "Кнопка [X] сворачивает окно в панель задач",
          detail: "Приложение продолжает работать. Чтобы полностью закрыть:\n" +
                  "• щёлкните правой кнопкой по значку в панели задач → «Закрыть окно», или\n" +
                  "• используйте меню «Файл → Закрыть приложение» (Ctrl+Q).",
        }) },
        { label: "О программе", click: () => dialog.showMessageBox(mainWindow, {
          title: "Corporate Chat", message: "Corporate Chat v2.0",
          detail: "Корпоративный мессенджер с интеграцией Active Directory.",
        }) },
      ],
    },
  ]);
}

// IPC from the setup page
ipcMain.handle("setup:connect", (e, server) => {
  const url = normalizeServer(server);
  saveConfig({ server: url });
  loadApp(url);
});

// ---- App control IPC (called from the web settings via preload bridge) ----
function applyAutostart(enabled) {
  try {
    app.setLoginItemSettings({
      openAtLogin: !!enabled,
      // start minimized so it doesn't steal focus on boot
      args: enabled ? ["--hidden"] : [],
    });
  } catch (e) {}
}

ipcMain.handle("app:set-autostart", (e, enabled) => {
  saveConfig({ autostart: !!enabled });
  applyAutostart(enabled);
  return { ok: true };
});
ipcMain.handle("app:set-keep-tray", (e, enabled) => {
  saveConfig({ keepInTray: !!enabled });
  return { ok: true };
});
ipcMain.handle("app:set-no-proxy", (e, enabled) => {
  saveConfig({ noProxy: !!enabled });
  try {
    if (enabled) session.defaultSession.setProxy({ mode: "direct" });
    else session.defaultSession.setProxy({ mode: "system" });
  } catch (e2) {}
  return { ok: true };
});
ipcMain.handle("app:get-state", () => {
  const cfg = loadConfig();
  let openAtLogin = false;
  try { openAtLogin = app.getLoginItemSettings().openAtLogin; } catch (e) {}
  return { server: cfg.server, keepInTray: cfg.keepInTray !== false, noProxy: !!cfg.noProxy, autostart: openAtLogin, clearCacheOnStart: cfg.clearCacheOnStart !== false, appVersion: app.getVersion() };
});
ipcMain.handle("app:hard-reload", async () => { hardReload(); return { ok: true }; });
ipcMain.handle("app:clear-cache", async () => { await clearWebCache(); return { ok: true }; });
ipcMain.handle("app:change-server", async () => { await changeServerDialog(); return { ok: true }; });

ipcMain.handle("app:open-local-path", async (e, rawPath) => {
  const p = String(rawPath || "").trim().replace(/^"|"$/g, "");
  if (!/^([A-Za-z]:\|\\)/.test(p)) return { ok: false, error: "Разрешены только Windows/UNC пути" };
  try {
    const err = await shell.openPath(p);
    return err ? { ok: false, error: err } : { ok: true };
  } catch (ex) { return { ok: false, error: ex.message || String(ex) }; }
});

// ---- Application icon state + taskbar highlight ----
// Base titlebar/start icon is assets/icon.ico. Runtime taskbar overlay changes
// according to state: unread messages, missed calls, or both.
const _stateIcons = { base: null, unread: null, call: null, both: null };
function loadStateIcon(name, file) {
  if (_stateIcons[name] !== null) return _stateIcons[name] || null;
  try {
    const img = nativeImage.createFromPath(path.join(__dirname, "assets", file));
    _stateIcons[name] = img && !img.isEmpty() ? img : false;
  } catch (e) { _stateIcons[name] = false; }
  return _stateIcons[name] || null;
}
function stateIcon(mode) {
  if (mode === "both") return loadStateIcon("both", "app-both.png");
  if (mode === "call") return loadStateIcon("call", "app-call.png");
  if (mode === "unread") return loadStateIcon("unread", "app-unread.png");
  return loadStateIcon("base", "app-main.png");
}
function stateMode(chatUnread, callUnread) {
  chatUnread = Math.max(0, parseInt(chatUnread, 10) || 0);
  callUnread = Math.max(0, parseInt(callUnread, 10) || 0);
  if (chatUnread > 0 && callUnread > 0) return "both";
  if (callUnread > 0) return "call";
  if (chatUnread > 0) return "unread";
  return "base";
}
function applyUnreadState(chatUnread, callUnread, flash) {
  if (!mainWindow) return;
  chatUnread = Math.max(0, parseInt(chatUnread, 10) || 0);
  callUnread = Math.max(0, parseInt(callUnread, 10) || 0);
  const count = chatUnread + callUnread;
  const mode = stateMode(chatUnread, callUnread);
  const tooltip = mode === "both" ? `${chatUnread} сообщений, ${callUnread} пропущенных вызовов`
    : mode === "call" ? `${callUnread} пропущенных вызовов`
    : mode === "unread" ? `${chatUnread} непрочитанных сообщений`
    : "";

  try { if (typeof app.setBadgeCount === "function") app.setBadgeCount(count); } catch (e) {}

  try {
    if (process.platform === "win32") {
      // Keep the main window/title icon as the main application icon. Change only
      // the taskbar overlay while the app is running.
      if (mode === "base") mainWindow.setOverlayIcon(null, "");
      else mainWindow.setOverlayIcon(stateIcon(mode), tooltip);
    }
  } catch (e) {}

  try {
    if (count > 0 && flash && !mainWindow.isFocused()) mainWindow.flashFrame(true);
    else if (count === 0) mainWindow.flashFrame(false);
  } catch (e) {}
}
function applyUnread(count, flash) {
  // Backward-compatible path used by old web bundles.
  applyUnreadState(Math.max(0, parseInt(count, 10) || 0), 0, flash);
}

ipcMain.handle("app:set-unread", (e, payload) => {
  const { count, flash } = payload || {};
  applyUnread(count, flash);
  return { ok: true };
});
ipcMain.handle("app:set-unread-state", (e, payload) => {
  const { chatUnread, callUnread, flash } = payload || {};
  applyUnreadState(chatUnread, callUnread, flash);
  return { ok: true };
});
ipcMain.handle("app:clear-flash", () => {
  if (mainWindow) { try { mainWindow.flashFrame(false); } catch (e) {} }
  return { ok: true };
});

app.whenReady().then(async () => {
  // grant notifications (used by the web app)
  session.defaultSession.setPermissionRequestHandler((wc, permission, callback) => {
    callback(permission === "notifications" || permission === "media");
  });
  // Make the desktop shell prefer fresh server assets. Server still controls
  // auth/localStorage; we only prevent stale JS/CSS/service-worker cache.
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = Object.assign({}, details.responseHeaders || {});
    if (/\/(js|css)\//.test(details.url) || /\.(js|css)(\?|$)/.test(details.url) || details.url.endsWith("/index.html")) {
      headers["Cache-Control"] = ["no-store, no-cache, must-revalidate"];
      headers["Pragma"] = ["no-cache"];
    }
    callback({ responseHeaders: headers });
  });
  // apply persisted proxy preference on launch
  const cfg = loadConfig();
  try {
    session.defaultSession.setProxy({ mode: cfg.noProxy ? "direct" : "system" });
  } catch (e) {}
  if (cfg.clearCacheOnStart !== false) await clearWebCache();

  createWindow();

  // If launched at login with --hidden, start minimized to the taskbar.
  if (process.argv.includes("--hidden") && mainWindow) {
    mainWindow.minimize();
  }
});

// When the window is truly closed, exit the app.
app.on("window-all-closed", () => { app.quit(); });
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); else showWindow(); });
app.on("before-quit", () => { isQuitting = true; });
