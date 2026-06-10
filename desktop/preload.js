/* Secure bridge between the setup page and the main process. */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronSetup", {
  connect: (server) => ipcRenderer.invoke("setup:connect", server),
});

// Tell the web app it's running inside the desktop shell + expose app controls.
contextBridge.exposeInMainWorld("CorporateChatDesktop", {
  isDesktop: true,
  version: "2.0.0",
  platform: process.platform,
  setAutostart: (enabled) => ipcRenderer.invoke("app:set-autostart", enabled),
  setKeepInTray: (enabled) => ipcRenderer.invoke("app:set-keep-tray", enabled),
  setNoProxy: (enabled) => ipcRenderer.invoke("app:set-no-proxy", enabled),
  getState: () => ipcRenderer.invoke("app:get-state"),
  // Unread badge + taskbar flash. count = total unread; flash = whether to
  // flash/highlight the taskbar button (usually only when window not focused).
  setUnread: (count, flash) => ipcRenderer.invoke("app:set-unread", { count, flash }),
  clearFlash: () => ipcRenderer.invoke("app:clear-flash"),
});
