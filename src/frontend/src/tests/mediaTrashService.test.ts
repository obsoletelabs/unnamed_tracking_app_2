import { afterEach, describe, expect, it, vi } from "vitest";
import { emptyMovieTrash, movieLibraryRevision } from "../services/movies";
import { emptyTVShowTrash } from "../services/tvShows";

function reply(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function serve(...replies: ReturnType<typeof reply>[]) {
  const fetchMock = vi.fn();
  for (const r of replies) fetchMock.mockResolvedValueOnce(r);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const batch = (deleted: number, remaining: number, failed: string[] = []) => ({
  deleted,
  failed: failed.length,
  failed_items: failed.map((title) => ({ id: title, title, deleted_at: 1 })),
  remaining,
  deleted_through: 500,
});

afterEach(() => vi.unstubAllGlobals());

describe("emptying a media trash through the API", () => {
  it("repeats bounded batches with the confirmed time until only failures are left", async () => {
    const fetchMock = serve(
      reply(batch(200, 5)),
      reply(batch(4, 1, ["Stuck"])),
    );
    const before = movieLibraryRevision();
    const result = await emptyMovieTrash(500);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).toBe("/api/movie/trash/empty");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({
        confirm: true,
        deleted_through: 500,
      });
    }
    expect(result).toEqual({
      deleted: 204,
      failedItems: [{ id: "Stuck", title: "Stuck", deleted_at: 1 }],
      remaining: 1,
      error: null,
    });
    // the library reloads after titles were removed
    expect(movieLibraryRevision()).toBeGreaterThan(before);
  });

  it("stops when a batch makes no progress", async () => {
    const fetchMock = serve(reply(batch(0, 3)));
    const result = await emptyTVShowTrash(500);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result).toMatchObject({ deleted: 0, remaining: 3, error: null });
  });

  it("keeps the count of what was deleted before a request failed", async () => {
    serve(reply(batch(200, 50)), reply({ detail: "boom" }, 500));
    const result = await emptyMovieTrash(500);
    expect(result.deleted).toBe(200);
    expect(result.error).toBe(
      "The server had a problem. Wait a moment and try again.",
    );
  });
});
