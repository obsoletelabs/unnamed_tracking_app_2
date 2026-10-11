import { afterEach, expect, it, vi } from "vitest";
import {
  beginSteamAccount,
  beginSteamQr,
  cancelSteamLogin,
  disconnectSteam,
  pollSteamLogin,
  SteamConnectionError,
  submitSteamGuard,
} from "../services/steamConnection";

afterEach(() => vi.unstubAllGlobals());

it("keeps account passwords and Guard codes out of URLs and disables caching", async () => {
  const fetch = vi.fn(
    async () =>
      new Response(JSON.stringify({ connected: false, pending: null })),
  );
  vi.stubGlobal("fetch", fetch);
  await beginSteamAccount("fixture-account", "fixture-password");
  await submitSteamGuard("fixture-attempt", "ABCDE", 2);
  expect(fetch.mock.calls.map((args: unknown[]) => args[0])).toEqual([
    "/api/settings/steam/login/account",
    "/api/settings/steam/login/guard",
  ]);
  const calls = fetch.mock.calls as unknown as [string, RequestInit][];
  for (const [, options] of calls) {
    expect(options.method).toBe("POST");
    expect(options.credentials).toBe("include");
    expect(options.cache).toBe("no-store");
  }
  expect(JSON.parse(String(calls[0][1].body))).toEqual({
    account_name: "fixture-account",
    password: "fixture-password",
  });
});

it("supports QR approval, explicit attempt cancellation and disconnect", async () => {
  const fetch = vi.fn<(url: string, options: RequestInit) => Promise<Response>>(
    async () =>
      new Response(JSON.stringify({ connected: false, pending: null })),
  );
  vi.stubGlobal("fetch", fetch);
  await beginSteamQr();
  await pollSteamLogin("fixture-attempt");
  await cancelSteamLogin("fixture-attempt");
  await disconnectSteam();
  expect(
    fetch.mock.calls.map(([url, options]) => [url, options.method]),
  ).toEqual([
    ["/api/settings/steam/login/qr", "POST"],
    ["/api/settings/steam/login/poll", "POST"],
    ["/api/settings/steam/login/cancel", "POST"],
    ["/api/settings/steam", "DELETE"],
  ]);
  expect(JSON.parse(String(fetch.mock.calls[2][1].body))).toEqual({
    attempt_id: "fixture-attempt",
  });
});

it("returns safe provider messages without echoing arbitrary error responses", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ input: "fixture-secret" }), {
          status: 400,
        }),
    ),
  );
  await expect(beginSteamAccount("name", "fixture-secret")).rejects.toThrow(
    "Steam connection request failed. Try again.",
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            detail: "Wait for the current Steam sign-in request.",
          }),
          { status: 429 },
        ),
    ),
  );
  await expect(pollSteamLogin("attempt")).rejects.toMatchObject<
    Partial<SteamConnectionError>
  >({ status: 429, message: "Wait for the current Steam sign-in request." });
});
