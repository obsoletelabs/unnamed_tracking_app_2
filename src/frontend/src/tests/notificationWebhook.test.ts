import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createWebhookDestination,
  updateWebhookDestination,
  removeWebhookDestination,
} from "../services/notifications";

afterEach(() => vi.unstubAllGlobals());

describe("owner webhook configuration", () => {
  it("enrolls credentials only through the authenticated host API", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(async () => new Response('{"id":"owned"}'));
    vi.stubGlobal("fetch", fetch);
    await createWebhookDestination(
      "official.discord-notifications.webhook",
      "https://discord.com/api/webhooks/12345/test-token",
      "Releases",
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/notification-providers/webhook-destinations",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({
          provider_id: "official.discord-notifications.webhook",
          url: "https://discord.com/api/webhooks/12345/test-token",
          label: "Releases",
        }),
      }),
    );
  });
  it("keeps disclosure consent separate from endpoint enablement and removes owned credentials", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    await updateWebhookDestination("owned/endpoint", {
      share_followed_media: true,
    });
    await updateWebhookDestination("owned/endpoint", { enabled: false });
    await removeWebhookDestination("owned/endpoint");
    expect(
      fetch.mock.calls.map(([url, options]) => [
        url,
        options.method,
        options.body,
      ]),
    ).toEqual([
      [
        "/api/settings/notification-providers/webhook-destinations/owned%2Fendpoint",
        "PATCH",
        '{"share_followed_media":true}',
      ],
      [
        "/api/settings/notification-providers/webhook-destinations/owned%2Fendpoint",
        "PATCH",
        '{"enabled":false}',
      ],
      [
        "/api/settings/notification-providers/webhook-destinations/owned%2Fendpoint",
        "DELETE",
        undefined,
      ],
    ]);
    expect(
      fetch.mock.calls.every(
        ([, options]) => options.credentials === "include",
      ),
    ).toBe(true);
  });
  it("reports invalid endpoints and revoked grants without claiming success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response('{"detail":"Enter a valid notification destination"}', {
          status: 400,
        }),
      ),
    );
    await expect(
      createWebhookDestination("provider", "invalid", "Releases"),
    ).rejects.toThrow("valid notification destination");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response('{"detail":"Provider unavailable"}', { status: 403 }),
        ),
    );
    await expect(
      updateWebhookDestination("owned", { share_followed_media: true }),
    ).rejects.toThrow();
  });
});
