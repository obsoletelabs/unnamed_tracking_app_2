import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchAutomaticMerges,
  fetchReviewQueue,
  keepReviewEntrySeparate,
  mergeReviewEntry,
  runIdentityMatching,
  undoAutomaticMerge,
} from "../services/identityReview";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);
afterEach(() => fetchMock.mockReset());

const ok = (body: unknown = null, status = 200) =>
  Promise.resolve(
    new Response(body === null ? null : JSON.stringify(body), { status }),
  );

describe("identity review service", () => {
  it("posts each queue and banner action to its own route", async () => {
    fetchMock.mockImplementation(() => ok(null, 204));
    await runIdentityMatching();
    await mergeReviewEntry("a");
    await keepReviewEntrySeparate("b");
    await undoAutomaticMerge("c");
    expect(
      fetchMock.mock.calls.map(([url, init]) => [url, init.method]),
    ).toEqual([
      ["/api/game-identity/review/run", "POST"],
      ["/api/game-identity/review/queue/a/merge", "POST"],
      ["/api/game-identity/review/queue/b/keep-separate", "POST"],
      ["/api/game-identity/review/automatic/c/undo", "POST"],
    ]);
  });

  it("reads the queue and the banner list", async () => {
    fetchMock.mockImplementationOnce(() =>
      ok({ entries: [], has_more: false }),
    );
    expect(await fetchReviewQueue()).toEqual({ entries: [], has_more: false });
    fetchMock.mockImplementationOnce(() => ok({ merges: [{ id: "m" }] }));
    expect(await fetchAutomaticMerges()).toEqual([{ id: "m" }]);
  });

  it("surfaces a failed action instead of hiding the entry", async () => {
    fetchMock.mockImplementation(() => ok({ detail: "already resolved" }, 404));
    await expect(mergeReviewEntry("x")).rejects.toBeInstanceOf(Error);
  });
});
