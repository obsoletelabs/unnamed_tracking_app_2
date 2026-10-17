// Host-owned notification extension of the reviewed root PWA worker.
// Push carries only opaque identities, never notification content; the preview is fetched
// from the signed-in app origin and rendered only as far as this platform supports.
(() => {
  const TAG = "unnamed-tracking:notification:";

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

  const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
  const OPERATIONS = ["send", "edit", "follow_up"];
  const GENERIC_TITLE = "New notification";
  const GENERIC_BODY = "Open Unnamed Tracking to view your notifications.";
  const MAX_PAYLOAD = 1024;

  // Only a path on this origin may ever be opened; never a scheme, authority or traversal.
  function appPath(value) {
    if (typeof value !== "string" || value.length > 512 || !/^\/(?!\/)[^\\\s]*$/.test(value)) return null;
    try {
      const url = new URL(value, self.location.origin);
      if (url.origin !== self.location.origin || url.pathname.split("/").includes("..")) return null;
      return url.pathname + url.search;
    } catch { return null; }
  }

  function line(value, limit) {
    return String(value).replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069]+/g, " ")
      .replace(/\s+/g, " ").trim().slice(0, limit);
  }

  // Optional fields are shown only where this platform advertises them.
  function platform() {
    const N = typeof Notification === "undefined" ? undefined : Notification;
    return {
      actions: N && Number.isInteger(N.maxActions) ? Math.min(N.maxActions, 2) : 0,
      image: !!N && !!N.prototype && "image" in N.prototype,
    };
  }

  function parsePush(event) {
    if (!event.data) return null;
    let payload;
    try {
      const text = event.data.text();
      if (text.length > MAX_PAYLOAD) return null;
      payload = JSON.parse(text);
    } catch { return null; }
    if (!payload || payload.version !== 1 ||
        typeof payload.destination !== "string" || !UUID.test(payload.destination) ||
        !Number.isSafeInteger(payload.revision) || payload.revision < 1 ||
        payload.generation !== CONFIG.generation) return null;
    const identified = payload.notification !== undefined;
    if (identified && (typeof payload.notification !== "string" || !UUID.test(payload.notification) ||
        !OPERATIONS.includes(payload.operation) ||
        (payload.story !== undefined && (typeof payload.story !== "string" || !UUID.test(payload.story))) ||
        !Number.isSafeInteger(payload.story_revision) || payload.story_revision < 0)) return null;
    return payload;
  }

  function display(preview, payload) {
    const generic = !preview || preview.generic !== false || typeof preview.title !== "string" ||
      typeof preview.body !== "string" || !line(preview.title, 80);
    const fields = platform();
    const options = {
      body: generic ? GENERIC_BODY : line(preview.body, 240),
      tag: tagFor(payload),
      icon: `/pwa/icons/${encodeURIComponent(CONFIG.generation)}/192.png`,
      badge: `/pwa/icons/${encodeURIComponent(CONFIG.generation)}/192.png`,
    };
    const actions = {};
    if (!generic && fields.actions && Array.isArray(preview.actions)) {
      options.actions = [];
      for (const action of preview.actions) {
        const url = appPath(action && action.url);
        if (!url || options.actions.length >= fields.actions || typeof action.action !== "string" ||
            !/^[a-z0-9._-]{1,128}$/i.test(action.action) || typeof action.title !== "string" ||
            !line(action.title, 40)) continue;
        options.actions.push({ action: action.action, title: line(action.title, 40) });
        actions[action.action] = url;
      }
    }
    if (!generic && fields.image && typeof preview.image === "string" && preview.image.length <= 2048) {
      try {
        if (new URL(preview.image).protocol === "https:") options.image = preview.image;
      } catch { /* Unsupported or malformed images never block the message. */ }
    }
    const revision = Number.isSafeInteger(preview && preview.story_revision) ? preview.story_revision : 0;
    options.data = {
      url: appPath(preview && preview.url) || "/notifications",
      destination: payload.destination, notification: payload.notification || null, revision, actions,
    };
    // An edit replaces the story's earlier alert quietly; a new alert for the tag may sound.
    options.renotify = payload.operation !== "edit";
    options.silent = payload.operation === "edit";
    return [generic ? GENERIC_TITLE : line(preview.title, 80), options];
  }

  function tagFor(payload) {
    const identity = payload.story ? "story:" + payload.story
      : payload.notification ? "notification:" + payload.notification : "";
    return TAG + payload.destination + (identity ? ":" + identity : "");
  }

  async function alreadyShown(tag, payload, revision) {
    // Idempotency: a replayed or reordered push never re-alerts for the same or an older revision.
    if (!payload.notification) return false;
    try {
      for (const existing of await self.registration.getNotifications({ tag })) {
        const data = existing.data || {};
        if (data.notification === payload.notification && Number.isSafeInteger(data.revision) &&
            data.revision >= revision) return true;
      }
    } catch { /* Platforms without getNotifications simply replace by tag. */ }
    return false;
  }

  async function present(event) {
    if (!CONFIG.enabled || retired) return;
    const payload = parsePush(event);
    if (!payload) return;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      // The active account and current routing are checked on the app origin.
      // An unavailable app, logged-out browser or switched account fails closed.
      const query = `revision=${payload.revision}` +
        (payload.notification ? `&notification=${payload.notification}` : "");
      const response = await fetch(
        `/api/settings/notification-providers/browser-destinations/${payload.destination}/status?${query}`,
        { cache: "no-store", credentials: "include", redirect: "error", signal: controller.signal },
      );
      if (!response.ok || retired) return;
      const state = await response.json();
      if (state.enabled !== true || state.generation !== CONFIG.generation || retired) return;
      // A deleted, expired or no longer routed item has no preview and is never shown.
      if (payload.notification && !state.preview) return;
      const [title, options] = display(state.preview, payload);
      if (await alreadyShown(options.tag, payload, options.data.revision) || retired) return;
      await self.registration.showNotification(title, options);
    } catch { /* No private preview, cached account state or offline assumption. */ }
    finally { clearTimeout(timer); }
  }

  self.addEventListener("push", event => event.waitUntil(present(event)));

  self.addEventListener("notificationclick", event => {
    if (!event.notification.tag.startsWith(TAG)) return;
    event.notification.close();
    const data = event.notification.data || {};
    const chosen = event.action && data.actions ? data.actions[event.action] : data.url;
    const target = new URL(appPath(chosen) || "/notifications", self.location.origin).href;
    event.waitUntil((async () => {
      if (!CONFIG.enabled || retired) return;
      for (const client of await self.clients.matchAll({ type: "window" })) {
        if (new URL(client.url).origin !== self.location.origin) continue;
        try {
          await client.navigate(target);
          await client.focus();
          return;
        } catch { /* An old window may have closed; open the same-origin page. */ }
      }
      await self.clients.openWindow(target);
    })());
  });
})();
