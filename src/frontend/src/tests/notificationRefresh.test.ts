import { afterEach, expect, it, vi } from "vitest";
import { currentUser } from "../state/auth";
import { mediaUnread, refreshMediaNotifications } from "../state/notifications";

function stubFetch(unread: number) {
  const fetcher = vi.fn(
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
