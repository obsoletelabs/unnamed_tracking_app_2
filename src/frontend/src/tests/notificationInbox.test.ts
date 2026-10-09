import { afterEach, expect, it, vi } from "vitest";
import {
  fetchMediaNotifications,
  dismissMediaNotification,
  deleteMediaNotification,
} from "../services/notifications";
import type { MediaNotification } from "../services/notifications";
import { groupNotifications } from "../utils/notificationPresentation";
import { currentUser } from "../state/auth";
import {
  refreshMediaNotifications,
  mediaNotifications,
  mediaUnread,
} from "../state/notifications";

afterEach(() => {
  currentUser.value = null;
  vi.unstubAllGlobals();
});
const notice: MediaNotification = {
  id: "one",
  kind: "game_sale",
  mediaType: "game",
  mediaId: "game-one",
  title: "Cinder Trails",
  body: "On sale",
  posterUrl: null,
  eventAt: 1800000000,
  read: false,
  source: "store",
  eventType: "game.sale.started",
  purpose: "standard",
  requiredTrust: 1,
  severity: "info",
  groupKey: "same-game",
};
it("groups related notices without crossing purpose, trust or source boundaries", () => {
  const groups = groupNotifications([
    notice,
    { ...notice, id: "two" },
    { ...notice, id: "three", purpose: "recovery" },
    { ...notice, id: "four", requiredTrust: 2 },
    { ...notice, id: "five", source: "another-store" },
    { ...notice, id: "six", groupKey: null },
  ]);
  expect(groups.map((group) => group.items.length)).toEqual([2, 1, 1, 1, 1]);
});
it("passes pagination/filter options and preserves authoritative server totals", async () => {
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        items: [
          {
            id: "one",
            kind: "game_sale",
            media_type: "game",
            media_id: "game-one",
            title: "Cinder Trails",
            body: "On sale",
            event_at: 1800000000,
            read: false,
            source: "store",
            severity: "info",
            required_trust: 1,
            group_key: "same-game",
          },
        ],
        total: 240,
        unread: 89,
        counts: { all: 240, releases: 180 },
        next_offset: 51,
        sources: ["store"],
      }),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  const page = await fetchMediaNotifications(1, {
    category: "releases",
    search: "50% & sale",
    offset: 50,
  });
  expect(fetch.mock.calls[0]![0]).toBe(
    "/api/notifications?limit=1&category=releases&search=50%25+%26+sale&offset=50",
  );
  expect(page.total).toBe(240);
  expect(page.unread).toBe(89);
  expect(page.nextOffset).toBe(51);
  expect(page.items[0]).toMatchObject({
    source: "store",
    requiredTrust: 1,
    groupKey: "same-game",
  });
});
it("keeps dismissal separate from cancellation via deletion", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetch);
  await dismissMediaNotification("one");
  await deleteMediaNotification("one");
  expect(fetch.mock.calls[0]).toEqual([
    "/api/notifications/one/dismiss",
    { method: "POST", credentials: "include" },
  ]);
  expect(fetch.mock.calls[1]).toEqual([
    "/api/notifications/one",
    { method: "DELETE", credentials: "include" },
  ]);
});
it("discards notification responses when the account changes", async () => {
  currentUser.value = {
    id: "old",
    username: "old",
    email: "old@example.test",
    is_admin: false,
    steamgriddb_api_key: null,
  };
  let complete!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    () =>
      new Promise<Response>((resolve) => {
        complete = resolve;
      }),
  );
  const oldRequest = refreshMediaNotifications();
  currentUser.value = { ...currentUser.value, id: "new" };
  complete(new Response(JSON.stringify({ items: [], unread: 89 })));
  await oldRequest;
  expect(mediaUnread.value).toBe(0);
  expect(mediaNotifications.value).toEqual([]);
});
