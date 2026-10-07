import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchSessions,
  revokeSession,
  revokeSessions,
  sessionDevice,
} from "../services/sessions";

afterEach(() => vi.unstubAllGlobals());

describe("built-in session service", () => {
  it("requests basic metadata in the owner and admin scopes", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() => Promise.resolve(new Response("[]")));
    vi.stubGlobal("fetch", fetch);
    await fetchSessions();
    await fetchSessions(true);
    expect(fetch.mock.calls).toEqual([
      [
        "/api/sessions/me?enriched=false",
        { method: "GET", credentials: "include" },
      ],
      [
        "/api/sessions/admin?enriched=false",
        { method: "GET", credentials: "include" },
      ],
    ]);
  });

  it("keeps single, owner bulk, user bulk and server bulk revocations distinct", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() => Promise.resolve(new Response('{"revoked":1}')));
    vi.stubGlobal("fetch", fetch);
    await revokeSession("owned");
    await revokeSession("foreign", true);
    await revokeSessions();
    await revokeSessions(true, "user");
    await revokeSessions(true);
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/sessions/me/owned",
      "/api/sessions/admin/foreign",
      "/api/sessions/me/all",
      "/api/sessions/admin/user/user",
      "/api/sessions/admin/all",
    ]);
    expect(
      fetch.mock.calls.every(([, options]) => options.method === "DELETE"),
    ).toBe(true);
  });

  it("surfaces authentication and server failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("", { status: 401 })),
    );
    await expect(fetchSessions()).rejects.toThrow("signed out");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("", { status: 500 })),
    );
    await expect(revokeSessions()).rejects.toThrow("server had a problem");
  });
});

describe("session browser summaries", () => {
  it.each([
    [
      "Mozilla/5.0 (Windows NT 10.0) Chrome/140.0 Safari/537.36 Edg/140.0",
      "Edge on Windows",
    ],
    [
      "Mozilla/5.0 (Linux; Android 16) Chrome/140.0 Safari/537.36",
      "Chrome on Android",
    ],
    [
      "Mozilla/5.0 (iPhone; CPU iPhone OS 18) Version/18.0 Mobile Safari/604.1",
      "Safari on iOS",
    ],
    ["Mozilla/5.0 (Macintosh) Firefox/140.0", "Firefox on macOS"],
    [null, "Browser unavailable"],
    ["custom-client", "Other browser"],
  ])("summarizes %s", (agent, expected) =>
    expect(sessionDevice(agent)).toBe(expected),
  );
});
