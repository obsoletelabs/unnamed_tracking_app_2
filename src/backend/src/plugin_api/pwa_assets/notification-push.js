// Host-owned notification extension of the reviewed root PWA worker.
// Push carries only an opaque destination/revision, never notification content.
(() => {
  const TAG = "unnamed-tracking:notification:";
  const INBOX = new URL("/notifications", self.location.origin).href;

  async function unsubscribe() {
    try {
      const subscription = await self.registration.pushManager?.getSubscription();
      await subscription?.unsubscribe();
    } catch { /* Host routing also withdraws the destination; retry on next visit. */ }
  }

  self.addEventListener("message", event => {
    if (event.data?.type === "tracking-pwa-retire") event.waitUntil(unsubscribe());
  });

  self.addEventListener("activate", event => {
    if (!CONFIG.enabled || retired) event.waitUntil(unsubscribe());
  });

  self.addEventListener("pushsubscriptionchange", event => {
    // A replacement subscription needs a fresh authenticated owner's consent.
    // Do not silently attach it to whichever account last used this worker.
    event.waitUntil((async () => {
      try { await event.newSubscription?.unsubscribe(); } catch { /* Already expired. */ }
      for (const client of await self.clients.matchAll({ type: "window" })) {
        client.postMessage({ type: "tracking-push-subscription-expired" });
      }
    })());
  });

  async function present(event) {
    if (!CONFIG.enabled || retired || !event.data) return;
    let payload;
    try {
      const text = event.data.text();
      if (text.length > 512) return;
      payload = JSON.parse(text);
    } catch { return; }
    if (!payload || payload.version !== 1 ||
        typeof payload.destination !== "string" ||
        !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(payload.destination) ||
        !Number.isSafeInteger(payload.revision) || payload.revision < 1 ||
        payload.generation !== CONFIG.generation) return;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      // The active account and current routing are checked on the app origin.
      // An unavailable app, logged-out browser or switched account fails closed.
      const response = await fetch(
        `/api/settings/notification-providers/browser-destinations/${payload.destination}/status?revision=${payload.revision}`,
        { cache: "no-store", credentials: "include", redirect: "error", signal: controller.signal },
      );
      if (!response.ok || retired) return;
      const state = await response.json();
      if (state.enabled !== true || state.generation !== CONFIG.generation || retired) return;
      await self.registration.showNotification("New notification", {
        body: "Open Unnamed Tracking to view your notifications.",
        tag: TAG + payload.destination,
        data: { inbox: true },
      });
    } catch { /* No private preview, cached account state or offline assumption. */ }
    finally { clearTimeout(timer); }
  }

  self.addEventListener("push", event => event.waitUntil(present(event)));

  self.addEventListener("notificationclick", event => {
    if (!event.notification.tag.startsWith(TAG)) return;
    event.notification.close();
    event.waitUntil((async () => {
      if (!CONFIG.enabled || retired) return;
      for (const client of await self.clients.matchAll({ type: "window" })) {
        if (new URL(client.url).origin !== self.location.origin) continue;
        try {
          await client.navigate(INBOX);
          await client.focus();
          return;
        } catch { /* An old window may have closed; open the same-origin inbox. */ }
      }
      await self.clients.openWindow(INBOX);
    })());
  });
})();
