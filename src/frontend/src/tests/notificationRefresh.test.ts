import { afterEach, expect, it, vi } from "vitest";
import { currentUser } from "../state/auth";
import {
  loadMoreMediaNotifications,
  mediaNotifications,
  mediaUnread,
  refreshMediaNotifications,
  refreshMediaUnread,
} from "../state/notifications";

function stubFetch(unread: number) {
  const fetcher = vi.fn<typeof fetch>(
    async () =>
      new Response(JSON.stringify({ items: [], unread, total: 0 }), {
        status: 200,
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

afterEach(() => {
  currentUser.value = null;
  vi.unstubAllGlobals();
});

it("lets simultaneously mounted components share one notification request", async () => {
  currentUser.value = { id: "reader" } as typeof currentUser.value;
  const fetcher = stubFetch(3);
  await Promise.all([
    refreshMediaNotifications({ shared: true }),
    refreshMediaNotifications({ shared: true }),
    refreshMediaNotifications({ shared: true }),
  ]);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(mediaUnread.value).toBe(3);
});

it("still sends a fresh request when a caller asks for current data", async () => {
  currentUser.value = { id: "reader" } as typeof currentUser.value;
  const fetcher = stubFetch(1);
  await Promise.all([
    refreshMediaNotifications({ shared: true }),
    refreshMediaNotifications(),
  ]);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("does not hand one account's pending request to another account", async () => {
  currentUser.value = { id: "first" } as typeof currentUser.value;
  const fetcher = stubFetch(2);
  const first = refreshMediaNotifications({ shared: true });
  currentUser.value = { id: "second" } as typeof currentUser.value;
  const second = refreshMediaNotifications({ shared: true });
  await Promise.all([first, second]);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("allows a new shared request once the earlier one has settled", async () => {
  currentUser.value = { id: "reader" } as typeof currentUser.value;
  const fetcher = stubFetch(0);
  await refreshMediaNotifications({ shared: true });
  await refreshMediaNotifications({ shared: true });
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("shares a count-only request without fetching the inbox at startup", async () => {
  currentUser.value = { id: "reader" } as typeof currentUser.value;
  const fetcher = stubFetch(7);
  await Promise.all([
    refreshMediaUnread({ shared: true }),
    refreshMediaUnread({ shared: true }),
  ]);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(String(fetcher.mock.calls[0]?.[0])).toContain("/unread-count");
  expect(mediaUnread.value).toBe(7);
  expect(mediaNotifications.value).toEqual([]);
});

it("fetches ten items on demand and appends the next page without duplicates", async () => {
  currentUser.value = { id: "reader" } as typeof currentUser.value;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          items: [{ id: "first", title: "First", read: false }],
          unread: 2,
          next_offset: 10,
        }),
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          items: [
            { id: "first", title: "First", read: false },
            { id: "next", title: "Next", read: false },
          ],
          unread: 2,
          next_offset: null,
        }),
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  await refreshMediaNotifications();
  await Promise.all([
    loadMoreMediaNotifications(),
    loadMoreMediaNotifications(),
  ]);
  expect(fetcher).toHaveBeenCalledTimes(2);
  const firstUrl = new URL(
    String(fetcher.mock.calls[0]?.[0]),
    "http://localhost",
  );
  expect(firstUrl.searchParams.get("limit")).toBe("10");
  expect(firstUrl.searchParams.get("include_summary")).toBe("false");
  const nextUrl = new URL(
    String(fetcher.mock.calls[1]?.[0]),
    "http://localhost",
  );
  expect(nextUrl.searchParams.get("offset")).toBe("10");
  expect(mediaNotifications.value.map((item) => item.id)).toEqual([
    "first",
    "next",
  ]);
});

it("discards a late badge response after switching accounts", async () => {
  currentUser.value = { id: "first" } as typeof currentUser.value;
  let complete!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          complete = resolve;
        }),
    ),
  );
  const pending = refreshMediaUnread();
  currentUser.value = { id: "second" } as typeof currentUser.value;
  complete(new Response(JSON.stringify({ unread: 99 })));
  await pending;
  expect(mediaUnread.value).toBe(0);
});
