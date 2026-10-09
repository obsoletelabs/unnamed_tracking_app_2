import { afterEach, expect, it, vi } from "vitest";
import { setDestinationNotificationUrl } from "../services/notifications";
import { updatePreferences } from "../services/preferences";

afterEach(() => vi.unstubAllGlobals());

it("saves an owner destination URL through the authenticated settings API", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ updated: true })));
  vi.stubGlobal("fetch", fetch);
  await setDestinationNotificationUrl(
    "id/with-slash",
    "https://email.example.test",
  );
  expect(fetch).toHaveBeenCalledWith(
    "/api/settings/notification-providers/destinations/id%2Fwith-slash/url",
    expect.objectContaining({
      method: "PATCH",
      credentials: "include",
      body: JSON.stringify({ notification_url: "https://email.example.test" }),
    }),
  );
});

it("can clear personal and destination overrides to restore inherited URLs", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", fetch);
  await setDestinationNotificationUrl("email", "");
  expect(JSON.parse(fetch.mock.calls[0]![1].body)).toEqual({
    notification_url: "",
  });
  await updatePreferences({ notification_url: "" });
  expect(fetch.mock.calls[1]![0]).toBe("/api/preferences");
  expect(JSON.parse(fetch.mock.calls[1]![1].body)).toEqual({
    notification_url: "",
  });
});

it("reports rejected destination URL changes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "External notification destination not found",
        }),
        { status: 404 },
      ),
    ),
  );
  await expect(
    setDestinationNotificationUrl("someone-else", "https://example.test"),
  ).rejects.toThrow("That could not be found. It may have been deleted.");
});
