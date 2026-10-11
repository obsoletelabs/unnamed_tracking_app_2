import { afterEach, expect, it, vi } from "vitest";
import {
  completePasswordReset,
  requestPasswordReset,
  resetCredential,
  passwordResetStatus,
} from "../services/passwordRecovery";
import { safeReturnPath } from "../state/startup";

afterEach(() => vi.unstubAllGlobals());

it("keeps recovery credentials out of return-path storage and rejects malformed fragments", () => {
  const id = "a134038e-9a2e-4ea1-935c-68a06d631598";
  const token = "a".repeat(43);
  expect(resetCredential(`#id=${id}&token=${token}`)).toEqual({ id, token });
  expect(resetCredential("#id=invalid&token=small")).toBeNull();
  expect(safeReturnPath(`/reset-password#id=${id}&token=${token}`)).toBeNull();
});

it("requests recovery and submits a password only to narrow public host endpoints", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({ message: "Done" }) });
  vi.stubGlobal("fetch", fetchMock);
  await requestPasswordReset("person@example.test");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/auth/password-reset/request",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ identifier: "person@example.test" }),
    }),
  );
  await completePasswordReset("id", "secret", "new-password");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/auth/password-reset/complete",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        id: "id",
        token: "secret",
        password: "new-password",
      }),
    }),
  );
  expect(
    fetchMock.mock.calls.every(([url]) => !String(url).includes("secret")),
  ).toBe(true);
});

it("fails closed on unavailable contributions and preserves safe policy errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Password policy" }),
    }),
  );
  expect(await passwordResetStatus()).toBe(false);
  await expect(completePasswordReset("id", "secret", "short")).rejects.toThrow(
    "Password policy",
  );
});
