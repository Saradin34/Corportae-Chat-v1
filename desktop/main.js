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
const DEFAULT_SERVER = "http://localhost";
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
    return Object.assign({ server: "", keepInTray: true, noProxy: false }, JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8")));
  } catch (e) { return { server: "", keepInTray: true, noProxy: false }; }
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
  if (cfg.server) loadApp(cfg.server);
  else loadSetup();

  // open external links in the system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http")) { shell.openExternal(url); return { action: "deny" }; }
    return { action: "allow" };
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
        { label: "Сменить сервер…", click: () => loadSetup() },
        { label: "Перезагрузить", accelerator: "CmdOrCtrl+R", click: () => mainWindow && mainWindow.reload() },
        { label: "Свернуть", accelerator: "CmdOrCtrl+M", click: () => mainWindow && mainWindow.minimize() },
        { type: "separator" },
        { label: "Закрыть приложение", accelerator: "CmdOrCtrl+Q", click: () => { isQuitting = true; app.quit(); } },
      ],
    },
    {
      label: "Правка",
      submenu: [
        { role: "undo", label: "Отменить" }, { role: "redo", label: "Повторить" }, { type: "separator" },
        { role: "cut", label: "Вырезать" }, { role: "copy", label: "Копировать" }, { role: "paste", label: "Вставить" },
        { role: "selectAll", label: "Выделить всё" },
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
  return { server: cfg.server, keepInTray: cfg.keepInTray !== false, noProxy: !!cfg.noProxy, autostart: openAtLogin };
});

// ---- Unread badge + taskbar highlight ----
// A small red-dot PNG overlay icon (SVG is not rasterized by nativeImage on
// Windows/Linux, so we ship a real PNG). The dot signals "unread" on the
// taskbar button; the exact count is shown in the tab title and tooltip.
let _badgeImg = null;
function badgeIcon() {
  if (_badgeImg === null) {
    try {
      _badgeImg = nativeImage.createFromPath(path.join(__dirname, "assets", "badge.png"));
      if (_badgeImg.isEmpty()) _badgeImg = false;
    } catch (e) { _badgeImg = false; }
  }
  return _badgeImg || null;
}

function applyUnread(count, flash) {
  if (!mainWindow) return;
  count = Math.max(0, parseInt(count, 10) || 0);
  // macOS / Linux: dock badge count
  try { if (typeof app.setBadgeCount === "function") app.setBadgeCount(count); } catch (e) {}
  // Windows: taskbar overlay icon (red badge) + flash the taskbar button
  try {
    if (process.platform === "win32") {
      if (count > 0 && badgeIcon()) {
        mainWindow.setOverlayIcon(badgeIcon(), count + " непрочитанных");
      } else {
        mainWindow.setOverlayIcon(null, "");
      }
    }
  } catch (e) {}
  // Flash/highlight the taskbar button when asked (window not focused).
  try {
    if (count > 0 && flash && !mainWindow.isFocused()) {
      mainWindow.flashFrame(true);
    } else if (count === 0) {
      mainWindow.flashFrame(false);
    }
  } catch (e) {}
}

ipcMain.handle("app:set-unread", (e, payload) => {
  const { count, flash } = payload || {};
  applyUnread(count, flash);
  return { ok: true };
});
ipcMain.handle("app:clear-flash", () => {
  if (mainWindow) { try { mainWindow.flashFrame(false); } catch (e) {} }
  return { ok: true };
});

app.whenReady().then(() => {
  // grant notifications (used by the web app)
  session.defaultSession.setPermissionRequestHandler((wc, permission, callback) => {
    callback(permission === "notifications");
  });
  // apply persisted proxy preference on launch
  const cfg = loadConfig();
  try {
    session.defaultSession.setProxy({ mode: cfg.noProxy ? "direct" : "system" });
  } catch (e) {}

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
