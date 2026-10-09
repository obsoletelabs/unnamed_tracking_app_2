import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createEmailDestination,
  requestEmailVerification,
  confirmEmailVerification,
  updateEmailDestination,
  revokeEmailVerification,
  removeEmailDestination,
  sendSmtpTest,
  sendDestinationTest,
} from "../services/notifications";

afterEach(() => vi.unstubAllGlobals());

describe("email destination contract", () => {
  it("tests SMTP through the admin boundary and personal providers through owned destinations", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(
        async (url: string) =>
          new Response(
            url.endsWith("smtp/test")
              ? '{"sent":true}'
              : '{"status":"pending"}',
          ),
      );
    vi.stubGlobal("fetch", fetch);
    await sendSmtpTest("account@example.test");
    expect(fetch.mock.calls[0]).toEqual([
      "/api/settings/notification-providers/smtp/test",
      expect.objectContaining({
        credentials: "include",
        method: "POST",
        body: '{"address":"account@example.test"}',
      }),
    ]);
    expect(await sendDestinationTest("owned")).toEqual({ status: "pending" });
    expect(fetch.mock.calls[1][0]).toBe(
      "/api/settings/notification-providers/destinations/owned/test",
    );
    fetch.mockResolvedValue(
      new Response('{"sent":false,"error":"smtp_rejected"}'),
    );
    await expect(sendSmtpTest("account@example.test")).rejects.toThrow(
      "smtp_rejected",
    );
  });
  it("separates enrollment, proof, recovery consent and removal with authenticated requests", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response('{"challenge_id":"challenge","expires_in":600}'),
      );
    // Each call needs its own body stream.
    fetch.mockImplementation(
      async () => new Response('{"challenge_id":"challenge","expires_in":600}'),
    );
    vi.stubGlobal("fetch", fetch);
    await createEmailDestination("me@example.test", "Home");
    const challenge = await requestEmailVerification("owned");
    await confirmEmailVerification("owned", challenge.challenge_id, "12345678");
    await updateEmailDestination("owned", { recovery_allowed: true });
    await revokeEmailVerification("owned");
    await removeEmailDestination("owned");
    expect(
      fetch.mock.calls.map(([url, options]) => [url, options.method]),
    ).toEqual([
      ["/api/settings/notification-providers/email-destinations", "POST"],
      [
        "/api/settings/notification-providers/email-destinations/owned/verification",
        "POST",
      ],
      [
        "/api/settings/notification-providers/email-destinations/owned/verification/confirm",
        "POST",
      ],
      [
        "/api/settings/notification-providers/email-destinations/owned",
        "PATCH",
      ],
      [
        "/api/settings/notification-providers/email-destinations/owned/verification/revoke",
        "POST",
      ],
      [
        "/api/settings/notification-providers/email-destinations/owned",
        "DELETE",
      ],
    ]);
    expect(
      fetch.mock.calls.every(
        ([, options]) => options.credentials === "include",
      ),
    ).toBe(true);
    expect(JSON.parse(fetch.mock.calls[2]![1].body)).toEqual({
      challenge_id: "challenge",
      code: "12345678",
    });
    expect(fetch.mock.calls[3]![1].body).not.toContain("trust");
  });
  it("shows server rate limits and invalid-code messages without treating them as success", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            '{"detail":"Verification requests are limited; try again later"}',
            { status: 429 },
          ),
        ),
    );
    await expect(requestEmailVerification("owned")).rejects.toThrow("limited");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            '{"detail":"The code is invalid, expired or already used"}',
            { status: 400 },
          ),
        ),
    );
    await expect(
      confirmEmailVerification("owned", "challenge", "12345678"),
    ).rejects.toThrow("already used");
  });
});
