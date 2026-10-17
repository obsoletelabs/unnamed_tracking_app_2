import { afterEach, describe, expect, it, vi } from "vitest";
import {
  accountDisplayName,
  accountInitials,
  updateProfile,
} from "../services/auth";
import { createUser } from "../services/admin";

afterEach(() => vi.unstubAllGlobals());

describe("account display name", () => {
  it("falls back to the sign-in username", () => {
    expect(accountDisplayName({ username: "ada.l" })).toBe("ada.l");
    expect(accountDisplayName({ username: "ada.l", display_name: null })).toBe(
      "ada.l",
    );
    expect(accountDisplayName({ username: "ada.l", display_name: "  " })).toBe(
      "ada.l",
    );
    expect(accountDisplayName(null)).toBe("");
  });

  it("prefers the chosen name without altering markup characters", () => {
    expect(
      accountDisplayName({ username: "u", display_name: "<b>Ada</b> & Co" }),
    ).toBe("<b>Ada</b> & Co");
  });

  it("builds initials from whole characters", () => {
    expect(accountInitials({ username: "ada.l" })).toBe("AD");
    expect(accountInitials({ username: "u", display_name: "山田 太郎" })).toBe(
      "山田",
    );
    expect(accountInitials({ username: "u", display_name: "🎮🎲 Games" })).toBe(
      "🎮🎲",
    );
    expect(accountInitials(null)).toBe("?");
  });

  it("sends display_name only when the caller provides it", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await updateProfile({ email: "a@example.test" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).not.toHaveProperty(
      "display_name",
    );
    await updateProfile({ displayName: "" });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).display_name).toBe("");
  });

  it("creates users with an optional display name", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    await createUser({
      username: "new.user",
      displayName: " Nuovo Utente ",
      email: "n@example.test",
      password: "test-only-password",
      isAdmin: false,
    });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).display_name).toBe(
      "Nuovo Utente",
    );
  });

  it("shows the server's display-name rejection readably", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [
              {
                loc: ["body", "display_name"],
                msg: "Value error, Display names cannot contain control or invisible characters.",
              },
            ],
          }),
          { status: 422 },
        ),
      ),
    );
    await expect(updateProfile({ displayName: "x" })).rejects.toThrow(
      "Display name: Display names cannot contain control or invisible characters.",
    );
  });
});
