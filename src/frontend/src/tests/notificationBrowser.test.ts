import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  createBrowserDestination,
  fetchBrowserPushConfiguration,
  generateBrowserPushKey,
  removeBrowserDestination,
  updateBrowserDestination,
} from "../services/notifications";
import { enableBrowserPush } from "../services/notificationBrowser";
import {
  browserPushRegistration,
  unsubscribeBrowserPush,
} from "../services/pwa";
import { logout } from "../services/auth";
vi.mock("../services/pwa", () => ({
  browserPushRegistration: vi.fn(),
  unsubscribeBrowserPush: vi.fn(),
}));
const configuration = {
  enabled: true,
  session_authenticated: true,
  public_key: btoa("public-key"),
  current_destination_id: null as string | null,
};
const payload = {
  endpoint: "https://fcm.googleapis.com/fcm/send/test",
  keys: { auth: "auth", p256dh: "public" },
};
const unsubscribe = vi.fn(async () => true);
const previousUnsubscribe = vi.fn(async () => true);
const subscribe = vi.fn(async () => ({ toJSON: () => payload, unsubscribe }));
const permission = vi.fn(async () => "granted");
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("window", {
    isSecureContext: true,
    Notification: {},
    PushManager: {},
  });
  vi.stubGlobal("navigator", { serviceWorker: {} });
  vi.stubGlobal("Notification", { requestPermission: permission });
  permission.mockResolvedValue("granted");
  subscribe.mockResolvedValue({ toJSON: () => payload, unsubscribe });
  vi.mocked(browserPushRegistration).mockResolvedValue({
    pushManager: {
      getSubscription: async () => ({ unsubscribe: previousUnsubscribe }),
      subscribe,
    },
  } as unknown as ServiceWorkerRegistration);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response('{"id":"fresh"}')),
  );
});
afterEach(() => vi.unstubAllGlobals());
it("asks permission directly, replaces previous subscription and sends no trust claim", async () => {
  const result = enableBrowserPush(
    { ...configuration, current_destination_id: "old/owned" },
    "Home laptop",
  );
  expect(permission).toHaveBeenCalledTimes(1);
  await result;
  expect(previousUnsubscribe).toHaveBeenCalledTimes(1);
  expect(subscribe).toHaveBeenCalledWith({
    userVisibleOnly: true,
    applicationServerKey: Uint8Array.from("public-key", (c) => c.charCodeAt(0)),
  });
  expect(
    vi.mocked(fetch).mock.calls.map(([url, options]) => [url, options?.method]),
  ).toEqual([
    [
      "/api/settings/notification-providers/browser-destinations/old%2Fowned",
      "DELETE",
    ],
    ["/api/settings/notification-providers/browser-destinations", "POST"],
  ]);
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1]?.[1]?.body))).toEqual(
    {
      subscription: payload,
      public_key: configuration.public_key,
      label: "Home laptop",
    },
  );
});
it.each(["denied", "default"])(
  "permission %s creates no subscription or enrollment",
  async (outcome) => {
    permission.mockResolvedValue(outcome);
    await expect(enableBrowserPush(configuration, "Browser")).rejects.toThrow(
      "permission was not granted",
    );
    expect(browserPushRegistration).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  },
);
it.each([{ enabled: false }, { session_authenticated: false }])(
  "rejects unavailable or non-session enrollment",
  async (changes) => {
    await expect(
      enableBrowserPush({ ...configuration, ...changes }, "Browser"),
    ).rejects.toThrow("Sign in");
    expect(permission).not.toHaveBeenCalled();
  },
);
it("rolls back the new browser subscription when the host rejects enrollment", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response('{"detail":"Server key changed"}', { status: 409 }),
    ),
  );
  await expect(enableBrowserPush(configuration, "Browser")).rejects.toThrow(
    "Server key changed",
  );
  expect(unsubscribe).toHaveBeenCalledTimes(1);
});
it("keeps native permission failures visible without claiming enrollment", async () => {
  permission.mockRejectedValueOnce(new Error("Browser denied request"));
  await expect(enableBrowserPush(configuration, "Browser")).rejects.toThrow(
    "Browser denied request",
  );
  expect(fetch).not.toHaveBeenCalled();
});
it("uses authenticated no-store public configuration and owner-only controls", async () => {
  await fetchBrowserPushConfiguration();
  await generateBrowserPushKey();
  await updateBrowserDestination("owned/id", { enabled: false, label: "Work" });
  await removeBrowserDestination("owned/id");
  expect(vi.mocked(fetch).mock.calls[0]).toEqual([
    "/api/settings/notification-providers/browser-configuration",
    { credentials: "include", cache: "no-store" },
  ]);
  expect(vi.mocked(fetch).mock.calls[1]?.[1]?.method).toBe("POST");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([, options]) => options?.credentials === "include"),
  ).toBe(true);
  expect(vi.mocked(fetch).mock.calls[2]?.[1]?.body).toBe(
    '{"enabled":false,"label":"Work"}',
  );
});
it("does not turn a subscription rejection into success", async () => {
  subscribe.mockRejectedValueOnce(new Error("Subscription unavailable"));
  await expect(enableBrowserPush(configuration, "Browser")).rejects.toThrow(
    "Subscription unavailable",
  );
  expect(fetch).not.toHaveBeenCalled();
});
it("reports owner enrollment API rejection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response('{"detail":"Sign in with a browser session"}', {
          status: 403,
        }),
    ),
  );
  await expect(
    createBrowserDestination(payload, configuration.public_key, "Browser"),
  ).rejects.toThrow("You do not have permission to do that.");
});

it("confirmed logout attempts browser cleanup even when native unsubscription fails", async () => {
  vi.mocked(unsubscribeBrowserPush).mockRejectedValueOnce(
    new Error("Browser offline"),
  );
  await expect(logout()).resolves.toBeUndefined();
  expect(fetch).toHaveBeenCalledWith("/api/auth/logout", {
    method: "POST",
    credentials: "include",
  });
  expect(unsubscribeBrowserPush).toHaveBeenCalledTimes(1);
});
it("failed logout preserves the browser subscription", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("unavailable", { status: 503 })),
  );
  await expect(logout()).rejects.toThrow("Could not sign out");
  expect(unsubscribeBrowserPush).not.toHaveBeenCalled();
});
