import { beforeEach, expect, it, vi } from "vitest";
import type { RouteLocationNormalized } from "vue-router";
import { saveLibraryScroll } from "../state/libraryScroll";

vi.mock("vue-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("vue-router")>();
  return { ...actual, createWebHistory: actual.createMemoryHistory };
});
import router from "../router";

const scroll = router.options.scrollBehavior!;
const route = (path: string): RouteLocationNormalized => {
  const resolved = router.resolve(path);
  return { ...resolved, name: resolved.name ?? undefined };
};
beforeEach(() => saveLibraryScroll(0));

it.each(["/games", "/movies", "/tv", "/anime"])(
  "preserves the viewport when filtering %s without a document reload",
  async (path) => {
    expect(
      await scroll(
        route(`${path}?tags=Puzzle&genres=Drama`),
        route(path),
        null,
      ),
    ).toBe(false);
    expect(await scroll(route(path), route(`${path}?tags=Puzzle`), null)).toBe(
      false,
    );
  },
);
it("preserves scroll when changing sort or other query options on the same page", async () => {
  expect(
    await scroll(
      route("/games?sort=rating&filters=1"),
      route("/games?filters=1"),
      null,
    ),
  ).toBe(false);
});
it("uses the browser history position before query-only preservation", async () => {
  const saved = { left: 0, top: 720 };
  expect(
    await scroll(route("/games?tags=Puzzle"), route("/games"), saved),
  ).toEqual(saved);
});
it("starts a different page at the top", async () => {
  expect(
    await scroll(route("/movies"), route("/games?tags=Puzzle"), null),
  ).toEqual({ top: 0 });
});
it("keeps existing fragment navigation behavior", async () => {
  expect(await scroll(route("/games#details"), route("/games"), null)).toEqual({
    top: 0,
  });
});
it("leaves an asynchronous game-detail return to the existing restoration", async () => {
  saveLibraryScroll(640);
  expect(
    await scroll(route("/games"), route("/games/example"), { left: 0, top: 0 }),
  ).toBe(false);
});
