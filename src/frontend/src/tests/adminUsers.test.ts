import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  changeUserEmail,
  changeUserPassword,
  forcePasswordReset,
  listInvitations,
  requestEmailVerification,
  resendInvitation,
  sendResetLink,
} from "../services/adminUsers";

const fetchMock = vi.fn();
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status });
}

const proof = { adminPassword: "Admin-Password-123!", confirmUsername: "kim" };

it("sends the administrator proof and typed username with email changes", async () => {
  fetchMock.mockImplementation(() =>
    Promise.resolve(reply(200, { email: "new@example.test" })),
  );
  await changeUserEmail("u1", "new@example.test", proof);
  const [path, init] = fetchMock.mock.calls[0];
  expect(path).toBe("/api/auth/users/u1/email");
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body)).toEqual({
    email: "new@example.test",
    admin_password: "Admin-Password-123!",
    confirm_username: "kim",
  });
});

it("shows the reviewed server sentence for a wrong administrator password", async () => {
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      reply(403, { detail: "Your administrator password is incorrect." }),
    ),
  );
  await expect(
    changeUserPassword("u1", "New-Password-1!", proof),
  ).rejects.toThrow("Your administrator password is incorrect.");
});

it("reports that no verified recovery address exists instead of forcing delivery", async () => {
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      reply(409, { detail: "This user has no verified email destination." }),
    ),
  );
  await expect(sendResetLink("u1")).rejects.toThrow("no verified email");
  await expect(forcePasswordReset("u1", proof)).rejects.toThrow(
    "no verified email",
  );
});

it("requests verification from the mailbox without a confirmation body", async () => {
  fetchMock.mockImplementation(() =>
    Promise.resolve(reply(200, { status: "verification_sent" })),
  );
  await requestEmailVerification("u1");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    destination_id: null,
  });
});

it("lists and resends invitations through the admin endpoints", async () => {
  fetchMock.mockResolvedValueOnce(reply(200, []));
  expect(await listInvitations()).toEqual([]);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/user-invitations");
  fetchMock.mockResolvedValueOnce(reply(200, { status: "resent", id: "i2" }));
  await resendInvitation("i1");
  expect(fetchMock.mock.calls[1][0]).toBe(
    "/api/auth/user-invitations/i1/resend",
  );
});
