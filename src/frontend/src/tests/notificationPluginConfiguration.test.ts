import { afterEach, describe, expect, it, vi } from "vitest";
import {
  configurePluginDestination,
  configurePluginNotificationServer,
  removePluginDestination,
  requestPluginVerification,
  confirmPluginVerification,
  revokePluginVerification,
  setPluginRecovery,
} from "../services/notifications";

afterEach(() => vi.unstubAllGlobals());

describe("generic plugin configuration boundary", () => {
  it("uses owner-only core proof and separate recovery consent without plugin assertions", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(
        async () => new Response('{"challenge_id":"proof","verified":true}'),
      );
    vi.stubGlobal("fetch", fetch);
    const challenge = await requestPluginVerification("owned/account");
    await confirmPluginVerification(
      "owned/account",
      challenge.challenge_id,
      "12345678",
    );
    await setPluginRecovery("owned/account", true);
    await revokePluginVerification("owned/account");
    const root =
      "/api/settings/notification-providers/plugin-destinations/owned%2Faccount";
    expect(
      fetch.mock.calls.map(([url, options]) => [url, options.method]),
    ).toEqual([
      [root + "/verification", "POST"],
      [root + "/verification/confirm", "POST"],
      [root + "/recovery", "PUT"],
      [root + "/verification/revoke", "POST"],
    ]);
    expect(
      fetch.mock.calls.every(
        ([, options]) => options.credentials === "include",
      ),
    ).toBe(true);
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
      challenge_id: "proof",
      code: "12345678",
    });
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ enabled: true });
    fetch.mockImplementation(
      async () =>
        new Response('{"detail":"Cannot receive sensitive content"}', {
          status: 403,
        }),
    );
    await expect(requestPluginVerification("owned/account")).rejects.toThrow(
      "You do not have permission",
    );
  });
  it("sends declared configuration through authenticated core enrollment without caller authority", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"id":"owned"}'));
    vi.stubGlobal("fetch", fetch);
    expect(
      await configurePluginDestination({
        provider_id: "example.provider.webhook",
        kind: "webhook",
        label: "Releases",
        values: { url: "plugin-secret", enabled: false, count: 0 },
        share_followed_media: true,
      }),
    ).toEqual({ id: "owned" });
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe(
      "/api/settings/notification-providers/plugin-destinations",
    );
    expect(options.method).toBe("POST");
    expect(options.credentials).toBe("include");
    expect(JSON.parse(options.body)).toEqual({
      provider_id: "example.provider.webhook",
      kind: "webhook",
      label: "Releases",
      values: { url: "plugin-secret", enabled: false, count: 0 },
      share_followed_media: true,
    });
  });
  it("updates only explicit values and reports incomplete plugin cleanup", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(
        async (_url, options) =>
          new Response(
            options.method === "DELETE"
              ? '{"removed":true,"plugin_cleanup_complete":false}'
              : '{"configured":true}',
          ),
      );
    vi.stubGlobal("fetch", fetch);
    await configurePluginDestination(
      {
        provider_id: "example.provider.webhook",
        kind: "webhook",
        label: "Changed",
        values: {},
      },
      "owned/endpoint",
    );
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/settings/notification-providers/plugin-destinations/owned%2Fendpoint",
    );
    expect(fetch.mock.calls[0][1].method).toBe("PUT");
    expect(JSON.parse(fetch.mock.calls[0][1].body).values).toEqual({});
    expect(await removePluginDestination("owned/endpoint")).toEqual({
      removed: true,
      plugin_cleanup_complete: false,
    });
    expect(fetch.mock.calls[1][1].method).toBe("DELETE");
  });
  it("uses the admin server boundary and propagates a rejected save", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response('{"detail":"Administrator access is required"}', {
        status: 403,
      }),
    );
    vi.stubGlobal("fetch", fetch);
    await expect(
      configurePluginNotificationServer("example.provider/webhook", {
        token: "secret",
      }),
    ).rejects.toThrow("You do not have permission to do that.");
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/settings/notification-providers/plugin-server-configuration/example.provider%2Fwebhook",
    );
    expect(fetch.mock.calls[0][1]).toMatchObject({
      method: "PUT",
      credentials: "include",
      body: '{"values":{"token":"secret"}}',
    });
  });
});
