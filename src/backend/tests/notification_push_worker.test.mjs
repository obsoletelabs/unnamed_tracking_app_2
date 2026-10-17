// Exercise the actual host fragment in a browser-worker-like event context.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("../src/plugin_api/pwa_assets/notification-push.js", import.meta.url), "utf8");
const destination = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const payload = { version: 1, destination, revision: 2, generation: "current" };

const notification = "11111111-2222-3333-4444-555555555555";
const story = "99999999-8888-7777-6666-555555555555";
const identified = { ...payload, notification, operation: "send", story, story_revision: 1 };
const tag = `unnamed-tracking:notification:${destination}:story:${story}`;
const story_preview = (extra = {}) => ({
  generic: false, title: "Episode 5 is out", body: "Now streaming", url: `/notifications/stories/${story}`,
  story_revision: 1, image: "https://img.example/a.jpg",
  actions: [{ action: "open", title: "Open", url: "/tv/1" }, { action: "x", title: "Evil", url: "https://evil.example/" },
    { action: "y", title: "Evil2", url: "//evil.example/" }, { action: "z", title: "Third", url: "/tv/3" }], ...extra,
});

function worker({ enabled = true, retired = false, status = 200, state = {}, failure = false,
  platform = { maxActions: 2, image: true }, existing = [] } = {}) {
  const handlers = new Map();
  const messages = [], requests = [], windows = [], shown = [];
  let unsubscribed = 0;
  const sandbox = {
    URL, JSON, Number, AbortController, setTimeout, clearTimeout,
    CONFIG: { enabled, generation: "current" }, retired,
    Notification: platform ? function () {} : undefined,
    self: {
      location: { origin: "https://app.example" },
      addEventListener: (type, handler) => handlers.set(type, handler),
      registration: {
        pushManager: { getSubscription: async () => ({ unsubscribe: async () => { unsubscribed++; } }) },
        showNotification: async (...values) => shown.push(values),
      },
      clients: {
        matchAll: async () => windows,
        openWindow: async url => messages.push(url),
      },
    },
    fetch: async (...values) => {
      requests.push(values);
      if (failure) throw new Error("offline");
      return { ok: status === 200, json: async () => ({ enabled: true, generation: "current", ...state }) };
    },
  };
  if (platform) {
    sandbox.Notification.maxActions = platform.maxActions;
    if (platform.image) sandbox.Notification.prototype.image = "";
  }
  sandbox.self.registration.getNotifications = async query => existing.filter(item => item.tag === query.tag);
  runInNewContext(source, sandbox);
  async function fire(type, values = {}) {
    const promises = [];
    handlers.get(type)({ ...values, waitUntil: promise => promises.push(promise) });
    await Promise.all(promises);
  }
  return { fire, sandbox, shown, requests, messages, windows, unsubscribed: () => unsubscribed };
}

test("push checks live owner routing and renders constant generic content", async () => {
  const current = worker();
  await current.fire("push", { data: { text: () => JSON.stringify({ ...payload, title: "Private account", body: "Recovery secret", link: "https://attacker.example" }) } });
  assert.equal(current.requests.length, 1);
  assert.equal(current.requests[0][0], `/api/settings/notification-providers/browser-destinations/${destination}/status?revision=2`);
  assert.equal(current.requests[0][1].credentials, "include");
  assert.equal(current.requests[0][1].cache, "no-store");
  assert.equal(current.requests[0][1].redirect, "error");
  assert.equal(current.shown.length, 1);
  assert.equal(current.shown[0][0], "New notification");
  assert.equal(current.shown[0][1].body, "Open Unnamed Tracking to view your notifications.");
  assert.doesNotMatch(JSON.stringify(current.shown), /Private account|Recovery secret|attacker/);
});

for (const options of [{ enabled: false }, { retired: true }, { status: 401 }, { status: 404 },
  { state: { enabled: false } }, { state: { generation: "another-installation" } }, { failure: true }]) {
  test(`withdrawn, switched, expired or unavailable routes fail closed: ${JSON.stringify(options)}`, async () => {
    const current = worker(options);
    await current.fire("push", { data: { text: () => JSON.stringify(payload) } });
    assert.equal(current.shown.length, 0);
  });
}

test("malformed and stale pushes make no authenticated request", async () => {
  const current = worker();
  for (const value of [null, {}, { ...payload, destination: "../another-user" },
    { ...payload, revision: 0 }, { ...payload, revision: 1.5 }, { ...payload, generation: "old" },
    { ...payload, version: 2 }, "X".repeat(513)]) {
    await current.fire("push", { data: { text: () => JSON.stringify(value) } });
  }
  await current.fire("push", { data: { text: () => "not JSON" } });
  assert.equal(current.requests.length, 0);
});

test("revocation while the authenticated status is fetched prevents display", async () => {
  const current = worker();
  current.sandbox.fetch = async () => {
    current.sandbox.retired = true;
    return { ok: true, json: async () => ({ enabled: true, generation: "current" }) };
  };
  await current.fire("push", { data: { text: () => JSON.stringify(payload) } });
  assert.equal(current.shown.length, 0);
});

test("click ignores message links and opens only this worker's inbox", async () => {
  const current = worker();
  let closed = false;
  await current.fire("notificationclick", { notification: {
    tag: "unnamed-tracking:notification:" + destination,
    data: { link: "https://attacker.example" }, close: () => { closed = true; },
  } });
  assert.ok(closed);
  assert.deepEqual(current.messages, ["https://app.example/notifications"]);
});

test("click focuses a same-origin inbox without navigating unrelated clients", async () => {
  const current = worker();
  const calls = [];
  current.windows.push({ url: "https://another.example/", navigate: () => assert.fail("Unrelated origin") });
  current.windows.push({ url: "https://app.example/library",
    navigate: async url => calls.push(url), focus: async () => calls.push("focused") });
  await current.fire("notificationclick", { notification: {
    tag: "unnamed-tracking:notification:" + destination, close() {},
  } });
  assert.deepEqual(calls, ["https://app.example/notifications", "focused"]);
  assert.equal(current.messages.length, 0);
});

test("retired and unrelated notifications cannot open a window", async () => {
  const current = worker({ retired: true });
  await current.fire("notificationclick", { notification: {
    tag: "unnamed-tracking:notification:" + destination, close() {},
  } });
  assert.equal(current.messages.length, 0);
  const active = worker();
  await active.fire("notificationclick", { notification: {
    tag: "unrelated", close: () => assert.fail("Unrelated notification must remain untouched"),
  } });
  assert.equal(active.messages.length, 0);
});

test("retirement unsubscribes instead of leaving a device endpoint enrolled", async () => {
  const current = worker();
  await current.fire("message", { data: { type: "tracking-pwa-retire" } });
  assert.equal(current.unsubscribed(), 1);
  const disabled = worker({ enabled: false });
  await disabled.fire("activate");
  assert.equal(disabled.unsubscribed(), 1);
});

test("subscription replacement requires fresh owner enrollment", async () => {
  const current = worker();
  let replacementUnsubscribed = false;
  current.windows.push({ postMessage: value => current.messages.push(value) });
  await current.fire("pushsubscriptionchange", { newSubscription: {
    unsubscribe: async () => { replacementUnsubscribed = true; },
  } });
  assert.ok(replacementUnsubscribed);
  assert.equal(current.messages[0].type, "tracking-push-subscription-expired");
  assert.equal(current.requests.length, 0);
});

const push = value => ({ data: { text: () => JSON.stringify(value) } });
const options = current => current.shown[0][1];

test("story pushes fetch the preview for that notification and replace by a stable story tag", async () => {
  const current = worker({ state: { preview: story_preview() } });
  await current.fire("push", push(identified));
  assert.equal(current.requests[0][0],
    `/api/settings/notification-providers/browser-destinations/${destination}/status?revision=2&notification=${notification}`);
  assert.equal(current.shown[0][0], "Episode 5 is out");
  assert.equal(options(current).tag, tag);
  assert.equal(options(current).body, "Now streaming");
  assert.equal(options(current).renotify, true);
  assert.equal(options(current).data.url, `/notifications/stories/${story}`);
  assert.equal(options(current).icon, "/pwa/icons/current/192.png");
});

test("an edit replaces the earlier alert quietly under the same tag", async () => {
  const current = worker({ state: { preview: story_preview({ story_revision: 2 }) } });
  await current.fire("push", push({ ...identified, operation: "edit", story_revision: 2 }));
  assert.equal(options(current).tag, tag);
  assert.equal(options(current).renotify, false);
  assert.equal(options(current).silent, true);
});

test("replayed or out-of-order pushes do not alert again", async () => {
  for (const revision of [1, 0]) {
    const current = worker({ state: { preview: story_preview({ story_revision: 1 }) },
      existing: [{ tag, data: { notification, revision: 1 + revision } }] });
    await current.fire("push", push(identified));
    assert.equal(current.shown.length, 0);
  }
  const newer = worker({ state: { preview: story_preview({ story_revision: 2 }) },
    existing: [{ tag, data: { notification, revision: 1 } }] });
  await newer.fire("push", push({ ...identified, operation: "edit", story_revision: 2 }));
  assert.equal(newer.shown.length, 1);
});

test("generic previews never use item text, image, actions or links", async () => {
  const current = worker({ state: { preview: { generic: true, title: "Private", body: "Secret",
    url: `/notifications/stories/${story}`, story_revision: 1, image: "https://img.example/a.jpg",
    actions: [{ action: "open", title: "Open", url: "/tv/1" }] } } });
  await current.fire("push", push(identified));
  assert.equal(current.shown[0][0], "New notification");
  assert.equal(options(current).body, "Open Unnamed Tracking to view your notifications.");
  assert.equal(options(current).image, undefined);
  assert.equal(options(current).actions, undefined);
  assert.doesNotMatch(JSON.stringify(current.shown), /Private|Secret/);
  assert.equal(options(current).data.url, `/notifications/stories/${story}`);
});

test("unsupported images and actions degrade without losing the message", async () => {
  for (const platform of [{ maxActions: 0, image: false }, null]) {
    const current = worker({ platform, state: { preview: story_preview() } });
    await current.fire("push", push(identified));
    assert.equal(current.shown[0][0], "Episode 5 is out");
    assert.equal(options(current).body, "Now streaming");
    assert.equal(options(current).image, undefined);
    assert.equal(options(current).actions, undefined);
  }
});

test("supported actions are capped, same-origin and mapped to their own path", async () => {
  const current = worker({ platform: { maxActions: 1, image: true }, state: { preview: story_preview() } });
  await current.fire("push", push(identified));
  assert.equal(JSON.stringify(options(current).actions), JSON.stringify([{ action: "open", title: "Open" }]));
  assert.equal(options(current).image, "https://img.example/a.jpg");
  const roomy = worker({ state: { preview: story_preview() } });
  await roomy.fire("push", push(identified));
  assert.equal(options(roomy).actions.map(item => item.action).join(), "open,z");
});

test("non-https images and hostile preview fields are dropped", async () => {
  const current = worker({ state: { preview: story_preview({ image: "javascript:alert(1)",
    title: "A\u202eB\n".repeat(60), url: "https://evil.example/x" }) } });
  await current.fire("push", push(identified));
  assert.equal(options(current).image, undefined);
  assert.ok(current.shown[0][0].length <= 80 && !/[\u202e\n]/.test(current.shown[0][0]));
  assert.equal(options(current).data.url, "/notifications");
});

test("an item that is gone, expired or not routed here is never shown", async () => {
  for (const preview of [null, undefined]) {
    const current = worker({ state: { preview } });
    await current.fire("push", push(identified));
    assert.equal(current.shown.length, 0);
  }
});

test("story fields in the push are validated before any request", async () => {
  const current = worker();
  for (const value of [{ ...identified, notification: "not-a-uuid" }, { ...identified, story: "../x" },
    { ...identified, operation: "delete" }, { ...identified, story_revision: -1 },
    { ...identified, story_revision: 1.5 }, { ...identified, pad: "x".repeat(1100) }]) {
    await current.fire("push", push(value));
  }
  assert.equal(current.requests.length, 0);
});

test("clicks open only this origin's story page, or the chosen same-origin action", async () => {
  const current = worker();
  const data = { url: `/notifications/stories/${story}`, actions: { open: "/tv/1" } };
  await current.fire("notificationclick", { notification: { tag, data, close() {} } });
  await current.fire("notificationclick", { action: "open", notification: { tag, data, close() {} } });
  await current.fire("notificationclick", { notification: { tag, data: { url: "https://evil.example/" }, close() {} } });
  await current.fire("notificationclick", { notification: { tag, data: { url: "//evil.example/" }, close() {} } });
  await current.fire("notificationclick", { action: "nope", notification: { tag, data, close() {} } });
  assert.deepEqual(current.messages, [`https://app.example/notifications/stories/${story}`, "https://app.example/tv/1",
    "https://app.example/notifications", "https://app.example/notifications", "https://app.example/notifications"]);
});

test("story clicks do nothing once the worker is retired", async () => {
  const current = worker({ retired: true });
  await current.fire("notificationclick", { notification: { tag, data: { url: "/tv/1" }, close() {} } });
  assert.equal(current.messages.length, 0);
});
