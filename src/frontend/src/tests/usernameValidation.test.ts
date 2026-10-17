import { afterEach, describe, expect, it, vi } from "vitest";
import {
  usernameFieldError,
  usernameRuleError,
  usernameServerError,
} from "../services/usernameValidation";
import { updateProfile } from "../services/auth";
import { createUser } from "../services/admin";

afterEach(() => vi.unstubAllGlobals());

// Keep in step with src/backend/tests/test_username_validation.py.
const VECTORS: [string, string | null][] = [
  ["", "Enter a username."],
  ["   ", "Enter a username."],
  [
    "a".repeat(101),
    "Username is too long: use at most 100 characters (this one has 101).",
  ],
  [
    "new name",
    "Username cannot contain spaces. Use only ASCII letters, digits, underscores, hyphens and dots.",
  ],
  [
    "me@example.com",
    "Username cannot contain \u201c@\u201d. Use only ASCII letters, digits, underscores, hyphens and dots.",
  ],
  [
    "caf\u00e9 \u00fc!",
    "Username cannot contain \u201c\u00e9\u201d, spaces, \u201c\u00fc\u201d, \u201c!\u201d. Use only ASCII letters, digits, underscores, hyphens and dots.",
  ],
  [
    "a/b\\c:d;e,f'g",
    "Username cannot contain \u201c/\u201d, \u201c\\\u201d, \u201c:\u201d, \u201c;\u201d, \u201c,\u201d, \u2026. Use only ASCII letters, digits, underscores, hyphens and dots.",
  ],
  ["a".repeat(100), null],
  ["  padded.Name_1-x  ", null],
];

describe("username rules", () => {
  it.each(VECTORS)("%j gives the backend's reason", (value, expected) => {
    expect(usernameRuleError(value)).toBe(expected);
  });

  it("never flags the account's own unchanged legacy username", () => {
    expect(usernameFieldError("Legacy Name", "Legacy Name")).toBeNull();
    expect(usernameFieldError("Legacy Name 2", "Legacy Name")).toContain(
      "cannot contain spaces",
    );
  });

  it("routes only username sentences to the field", () => {
    expect(
      usernameServerError(
        "Username: Username cannot contain spaces. Use only ASCII.",
      ),
    ).toBe("Username cannot contain spaces. Use only ASCII.");
    expect(
      usernameServerError(
        "That username is already taken. Choose a different username.",
      ),
    ).toContain("already taken");
    expect(usernameServerError("That email address is already in use.")).toBe(
      null,
    );
    expect(usernameServerError("The email or username is already in use")).toBe(
      null,
    );
    // The non-enumerating reply for ordinary users names neither field, so it
    // stays in the form's general error area rather than under the username.
    expect(
      usernameServerError("That username or email is already in use."),
    ).toBeNull();
    expect(usernameServerError(null)).toBeNull();
  });
});

describe("conflict replies keep the server's reason", () => {
  const conflict = () =>
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail:
            "That username is already taken. Choose a different username.",
        }),
        { status: 409 },
      ),
    );

  it("profile update", async () => {
    vi.stubGlobal("fetch", conflict());
    await expect(updateProfile({ username: "taken" })).rejects.toThrow(
      "That username is already taken. Choose a different username.",
    );
  });

  it("admin user creation", async () => {
    vi.stubGlobal("fetch", conflict());
    await expect(
      createUser({
        username: "taken",
        email: "a@example.test",
        password: "x",
        isAdmin: false,
      }),
    ).rejects.toThrow("That username is already taken.");
  });
});

describe("profile conflict for an ordinary user", () => {
  it("surfaces the combined message without naming a field", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: "That username or email is already in use.",
          }),
          { status: 409 },
        ),
      ),
    );
    await expect(
      updateProfile({ email: "taken@example.test" }),
    ).rejects.toThrow("That username or email is already in use.");
  });
});
