/* Minimal same-origin service worker.
   Purpose: enables reliable notifications (some browsers require an active SW
   to display notifications, and lets a notification click focus the app).
   It does NOT cache anything and makes NO external requests. */
self.addEventListener("install", (e) => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  const data = event.notification && event.notification.data || {};
  event.notification.close();
  const targetHash = data.route === "calls" ? "/#/chats?calls=1" : "/#/chats";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cls) => {
      for (const c of cls) {
        if ("focus" in c) {
          try {
            if (data.route === "calls" && "navigate" in c) c.navigate(targetHash);
          } catch (e) {}
          return c.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetHash);
    })
  );
});
