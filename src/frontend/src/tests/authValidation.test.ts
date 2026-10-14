import { afterEach, describe, expect, it, vi } from "vitest";
import { updateProfile } from "../services/auth";
import { createUser } from "../services/admin";

afterEach(() => vi.unstubAllGlobals());

describe("account field validation guidance", () => {
  it("shows the rejected username rule without raw response JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [
              {
                loc: ["body", "username"],
                msg: "Value error, Use ASCII letters, digits, underscores, hyphens or dots. No spaces.",
              },
            ],
          }),
          { status: 422 },
        ),
      ),
    );
    await expect(
      createUser({
        username: "bad name",
        email: "reader@example.test",
        password: "test-only-password",
        isAdmin: false,
      }),
    ).rejects.toThrow(
      "Username: Use ASCII letters, digits, underscores, hyphens or dots. No spaces.",
    );
  });

  it("shows the profile rename explanation directly", async () => {
    const message =
      "Use ASCII letters, digits, underscores, hyphens or dots. No spaces.";
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: message }), { status: 400 }),
        ),
    );
    await expect(updateProfile({ username: "bad name" })).rejects.toThrow(
      message,
    );
  });
});
