import { afterEach, expect, it, vi } from "vitest";
import { currentUser } from "../state/auth";
import { inboxCount, refreshInboxCount } from "../state/inbox";
afterEach(() => {
  currentUser.value = null;
  vi.unstubAllGlobals();
});
it("loads only the recorded count for the sidebar", async () => {
  currentUser.value = { id: "reader" } as typeof currentUser.value;
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ count: 7 })));
  vi.stubGlobal("fetch", fetcher);
  await refreshInboxCount();
  expect(fetcher.mock.calls).toHaveLength(1);
  expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe("/api/media/inbox/count");
  expect(inboxCount.value).toBe(7);
});
it("discards the previous account's badge response", async () => {
  currentUser.value = { id: "first" } as typeof currentUser.value;
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const pending = refreshInboxCount();
  currentUser.value = { id: "second" } as typeof currentUser.value;
  finish(new Response(JSON.stringify({ count: 9 })));
  await pending;
  expect(inboxCount.value).toBe(0);
});
