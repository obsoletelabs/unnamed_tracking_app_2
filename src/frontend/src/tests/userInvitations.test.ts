import { afterEach, expect, it, vi } from "vitest";
import {
  invitationStatus,
  acceptInvitation,
} from "../services/userInvitations";
import { authLinkCredential } from "../services/passwordRecovery";
import { safeReturnPath } from "../state/startup";

afterEach(() => vi.unstubAllGlobals());

it("rejects saving invitation credentials in return-path state", () => {
  const id = "a134038e-9a2e-4ea1-935c-68a06d631598",
    token = "a".repeat(43);
  expect(authLinkCredential(`#id=${id}&token=${token}`)).toEqual({ id, token });
  expect(safeReturnPath(`/accept-invite#id=${id}&token=${token}`)).toBeNull();
});

it("submits credentials by POST with no email override or administrator privilege", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ message: "Account created." }),
  });
  vi.stubGlobal("fetch", fetchMock);
  await acceptInvitation("id", "secret", "user", "password");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/auth/user-invites/accept",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        id: "id",
        token: "secret",
        username: "user",
        password: "password",
      }),
    }),
  );
});

it("fails closed on unavailable flows and preserves the host's policy error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Username already used" }),
    }),
  );
  // a failed check is an error callers can retry, never "enabled"
  await expect(invitationStatus()).rejects.toThrow("availability");
  await expect(
    acceptInvitation("id", "secret", "user", "password"),
  ).rejects.toThrow("Username already used");
});
