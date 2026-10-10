// Exercise the actual host fragment in a browser-worker-like event context.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("../src/plugin_api/pwa_assets/notification-push.js", import.meta.url), "utf8");
const destination = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const payload = { version: 1, destination, revision: 2, generation: "current" };

function worker({ enabled = true, retired = false, status = 200, state = {}, failure = false } = {}) {
  const handlers = new Map();
  const messages = [], requests = [], windows = [], shown = [];
  let unsubscribed = 0;
  const sandbox = {
    URL, JSON, Number, AbortController, setTimeout, clearTimeout,
    CONFIG: { enabled, generation: "current" }, retired,
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
