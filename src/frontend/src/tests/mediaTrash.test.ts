// @vitest-environment happy-dom

import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MediaTrashSection from "../components/settings/MediaTrashSection.vue";
import {
  emptyMovieTrash,
  fetchMovieTrash,
  restoreMovie,
  purgeMovie,
} from "../services/movies";
import { emptyTVShowTrash, fetchTVShowTrash } from "../services/tvShows";
import { emptyAnimeTrash, fetchAnimeTrash } from "../services/anime";
import {
  DEFAULT_PREFERENCES,
  fetchPreferences,
  queuePreferences,
} from "../services/preferences";
import type { EmptyTrashResult, TrashedMedia } from "../services/mediaApi";
import {
  emptyTrashMessage,
  expiredUnder,
  newestDeletion,
  retentionDeadline,
} from "../utils/mediaTrash";

const { confirm } = vi.hoisted(() => ({ confirm: vi.fn() }));
vi.mock("../state/dialog", () => ({ useConfirm: () => confirm }));
vi.mock("../services/movies", () => ({
  fetchMovieTrash: vi.fn(),
  restoreMovie: vi.fn(),
  purgeMovie: vi.fn(),
  emptyMovieTrash: vi.fn(),
}));
vi.mock("../services/tvShows", () => ({
  fetchTVShowTrash: vi.fn(),
  restoreTVShow: vi.fn(),
  purgeTVShow: vi.fn(),
  emptyTVShowTrash: vi.fn(),
}));
vi.mock("../services/anime", () => ({
  fetchAnimeTrash: vi.fn(),
  restoreAnime: vi.fn(),
  purgeAnime: vi.fn(),
  emptyAnimeTrash: vi.fn(),
}));
vi.mock("../services/preferences", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn(),
  queuePreferences: vi.fn(),
}));

const DAY = 24 * 60 * 60;
const now = () => Math.floor(Date.now() / 1000);
const row = (id: string, title: string, deletedAt: number): TrashedMedia => ({
  id,
  title,
  deleted_at: deletedAt,
});
const done = (changes: Partial<EmptyTrashResult> = {}): EmptyTrashResult => ({
  deleted: 0,
  failedItems: [],
  remaining: 0,
  error: null,
  ...changes,
});

function serveTrash(
  movies: TrashedMedia[] = [],
  shows: TrashedMedia[] = [],
  anime: TrashedMedia[] = [],
) {
  vi.mocked(fetchMovieTrash).mockResolvedValue(movies);
  vi.mocked(fetchTVShowTrash).mockResolvedValue(shows);
  vi.mocked(fetchAnimeTrash).mockResolvedValue(anime);
}

function serveRetention(days: 0 | 7 | 14 | 30 | 90) {
  vi.mocked(fetchPreferences).mockResolvedValue({
    ...DEFAULT_PREFERENCES,
    media_trash_retention_days: days,
  });
}

async function section() {
  const wrapper = mount(MediaTrashSection);
  await flushPromises();
  return wrapper;
}

const emptyButton = (
  wrapper: Awaited<ReturnType<typeof section>>,
  label: string,
) => wrapper.find(`button[aria-label="Empty trash: ${label}"]`);
const segment = (wrapper: Awaited<ReturnType<typeof section>>, text: string) =>
  wrapper.findAll(".segment").find((button) => button.text() === text)!;

beforeEach(() => {
  serveTrash();
  serveRetention(0);
});
afterEach(() => vi.clearAllMocks());

describe("emptying one kind of media trash", () => {
  it("deletes only after an explicit confirmation, bounded by what was shown", async () => {
    serveTrash([row("m1", "Heat", 100), row("m2", "Alien", 200)]);
    const wrapper = await section();
    expect(emptyButton(wrapper, "Movies").exists()).toBe(true);
    // nothing to empty, nothing offered
    expect(emptyButton(wrapper, "TV Shows").exists()).toBe(false);
    expect(emptyButton(wrapper, "Anime").exists()).toBe(false);

    confirm.mockResolvedValueOnce(false);
    await emptyButton(wrapper, "Movies").trigger("click");
    await flushPromises();
    expect(emptyMovieTrash).not.toHaveBeenCalled();
    expect(confirm.mock.calls[0][0]).toMatchObject({
      title: "Empty the movie trash?",
      message:
        "Permanently delete all 2 movies in the trash? This can't be undone.",
      confirmLabel: "Delete all 2 forever",
      danger: true,
    });

    confirm.mockResolvedValueOnce(true);
    vi.mocked(emptyMovieTrash).mockResolvedValue(done({ deleted: 2 }));
    vi.mocked(fetchMovieTrash).mockResolvedValue([]);
    await emptyButton(wrapper, "Movies").trigger("click");
    await flushPromises();
    expect(emptyMovieTrash).toHaveBeenCalledWith(200);
    expect(emptyTVShowTrash).not.toHaveBeenCalled();
    expect(emptyAnimeTrash).not.toHaveBeenCalled();
    const notice = wrapper.find(".trash-notice");
    expect(notice.text()).toBe("Deleted 2 movies permanently.");
    expect(notice.attributes("role")).toBe("status");
    expect(restoreMovie).not.toHaveBeenCalled();
    expect(purgeMovie).not.toHaveBeenCalled();
  });

  it("names titles that could not be deleted instead of reporting success", async () => {
    serveTrash(
      [],
      [row("t1", "Lost", 10), row("t2", "Dark", 20), row("t3", "Fargo", 30)],
    );
    const wrapper = await section();
    await wrapper.find("input[type=search]").setValue("lost");
    confirm.mockResolvedValueOnce(true);
    vi.mocked(emptyTVShowTrash).mockResolvedValue(
      done({ deleted: 2, failedItems: [row("t1", "Lost", 10)], remaining: 1 }),
    );
    await emptyButton(wrapper, "TV Shows").trigger("click");
    await flushPromises();
    // the whole kind, including titles the search hides
    expect(confirm.mock.calls[0][0].message).toBe(
      "Permanently delete all 3 TV shows in the trash, with their seasons and episodes? This can't be undone. This includes titles hidden by the search.",
    );
    expect(emptyTVShowTrash).toHaveBeenCalledWith(30);
    const notice = wrapper.find(".trash-notice");
    expect(notice.text()).toBe(
      'Deleted 2 TV shows permanently. 1 TV show could not be deleted and is still in the trash: "Lost". Try again, or delete it one at a time.',
    );
    expect(notice.attributes("role")).toBe("alert");
    expect(notice.classes()).toContain("incomplete");
  });

  it("says nothing was deleted when the request fails", async () => {
    serveTrash([], [], [row("a1", "Mushishi", 50)]);
    const wrapper = await section();
    confirm.mockResolvedValueOnce(true);
    vi.mocked(emptyAnimeTrash).mockResolvedValue(
      done({ error: "The server had a problem. Wait a moment and try again." }),
    );
    await emptyButton(wrapper, "Anime").trigger("click");
    await flushPromises();
    expect(confirm.mock.calls[0][0]).toMatchObject({
      message:
        "Permanently delete the 1 anime entry in the trash, with its seasons and episodes? This can't be undone.",
      confirmLabel: "Delete it forever",
    });
    expect(wrapper.find(".trash-notice").text()).toBe(
      "Could not empty the anime trash: The server had a problem. Wait a moment and try again. Nothing was deleted; try again.",
    );
  });
});

describe("opt-in media trash retention", () => {
  it("defaults to Never and only turns deletion on after confirming", async () => {
    serveTrash([row("m1", "Old", now() - 40 * DAY), row("m2", "New", now())]);
    const wrapper = await section();
    expect(segment(wrapper, "Never").attributes("aria-pressed")).toBe("true");
    expect(wrapper.text()).toContain("Never: titles stay in the trash");

    confirm.mockResolvedValueOnce(false);
    await segment(wrapper, "After 30 days").trigger("click");
    await flushPromises();
    expect(confirm.mock.calls[0][0]).toMatchObject({
      title: "Delete trashed titles automatically?",
      message:
        "Movies, TV shows and anime that have been in the trash for 30 days will be deleted permanently, with their seasons and episodes. 1 title already qualifies and will be deleted at the next clean-up. This can't be undone.",
      confirmLabel: "Delete after 30 days",
      danger: true,
    });
    expect(queuePreferences).not.toHaveBeenCalled();
    expect(segment(wrapper, "Never").attributes("aria-pressed")).toBe("true");

    confirm.mockResolvedValueOnce(true);
    vi.mocked(queuePreferences).mockResolvedValue({
      prefs: { ...DEFAULT_PREFERENCES, media_trash_retention_days: 30 },
      latest: true,
    });
    await segment(wrapper, "After 30 days").trigger("click");
    await flushPromises();
    expect(queuePreferences).toHaveBeenCalledWith({
      media_trash_retention_days: 30,
    });
    expect(segment(wrapper, "After 30 days").attributes("aria-pressed")).toBe(
      "true",
    );
    expect(wrapper.find(".retention-hint").text()).toContain(
      "30 days after they were moved to the trash",
    );
    expect(wrapper.find(".retention-hint").text()).toContain(
      "This can't be undone.",
    );
    const metas = wrapper.findAll(".trash-meta").map((meta) => meta.text());
    expect(metas[0]).toContain("deleted for good at the next clean-up");
    expect(metas[1]).toContain("kept until");
  });

  it("shows the saved choice again when saving fails", async () => {
    const wrapper = await section();
    confirm.mockResolvedValueOnce(true);
    vi.mocked(queuePreferences).mockRejectedValue(
      new Error("Failed to save preferences: 500"),
    );
    await segment(wrapper, "After 7 days").trigger("click");
    await flushPromises();
    expect(segment(wrapper, "Never").attributes("aria-pressed")).toBe("true");
    expect(wrapper.find("[role=alert]").text()).toBe(
      "Automatic deletion was not changed: Failed to save preferences: 500",
    );
  });

  it("does not ask before keeping titles longer or turning deletion off", async () => {
    serveRetention(30);
    const wrapper = await section();
    vi.mocked(queuePreferences).mockImplementation(async (changes) => ({
      prefs: { ...DEFAULT_PREFERENCES, ...changes },
      latest: true,
    }));
    await segment(wrapper, "After 90 days").trigger("click");
    await flushPromises();
    await segment(wrapper, "Never").trigger("click");
    await flushPromises();
    expect(confirm).not.toHaveBeenCalled();
    expect(vi.mocked(queuePreferences).mock.calls).toEqual([
      [{ media_trash_retention_days: 90 }],
      [{ media_trash_retention_days: 0 }],
    ]);
    // shortening deletes sooner, so it asks again
    confirm.mockResolvedValueOnce(false);
    await segment(wrapper, "After 14 days").trigger("click");
    await flushPromises();
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("offers no choice when the saved one cannot be loaded", async () => {
    vi.mocked(fetchPreferences).mockRejectedValue(new Error("offline"));
    const wrapper = await section();
    expect(wrapper.findAll(".segment")).toHaveLength(0);
    expect(wrapper.find("[role=alert]").text()).toBe(
      "Could not load automatic deletion: offline",
    );
  });
});

describe("media trash wording", () => {
  const movies = { one: "movie", many: "movies" };

  it("reports every outcome of emptying a trash", () => {
    expect(emptyTrashMessage(done({ deleted: 1 }), movies, "movie")).toBe(
      "Deleted 1 movie permanently.",
    );
    expect(emptyTrashMessage(done(), movies, "movie")).toBe(
      "Nothing was deleted: those titles had already been restored or deleted.",
    );
    expect(
      emptyTrashMessage(
        done({
          deleted: 3,
          failedItems: ["A", "B", "C", "D", "E", "F", "G"].map((t) =>
            row(t, t, 1),
          ),
          remaining: 9,
        }),
        movies,
        "movie",
      ),
    ).toBe(
      'Deleted 3 movies permanently. 7 movies could not be deleted and are still in the trash: "A", "B", "C", "D", "E" and 2 more. Try again, or delete them one at a time. 2 movies are still in the trash; empty it again to continue.',
    );
    expect(
      emptyTrashMessage(
        done({ deleted: 200, error: "Failed to fetch" }),
        movies,
        "movie",
      ),
    ).toBe(
      "Deleted 200 movies permanently, then emptying the movie trash stopped: Failed to fetch. The rest are still in the trash; try again.",
    );
  });

  it("measures retention from each title's deletion time", () => {
    const rows = [row("a", "A", 1000), row("b", "B", 1000 + 7 * DAY)];
    expect(newestDeletion(rows)).toBe(1000 + 7 * DAY);
    expect(newestDeletion([])).toBe(0);
    expect(retentionDeadline(1000, 0)).toBeNull();
    expect(retentionDeadline(1000, 7)).toBe(1000 + 7 * DAY);
    expect(expiredUnder(rows, 0, 1000 + 100 * DAY)).toBe(0);
    expect(expiredUnder(rows, 7, 1000 + 7 * DAY)).toBe(1);
    expect(expiredUnder(rows, 7, 1000 + 7 * DAY - 1)).toBe(0);
    expect(expiredUnder(rows, 7, 1000 + 14 * DAY)).toBe(2);
  });
});
