// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { fetchCalendarFeeds } from "../services/mediaExtras";

afterEach(() => vi.unstubAllGlobals());

it("maps the combined and per-kind feed paths to absolute links", async () => {
  const fetchMock = vi.fn(async () =>
    Response.json({
      path: "/api/calendar/feed/tok.ics",
      layers: {
        anime: "/api/calendar/feed/tok/anime.ics",
        movies: "/api/calendar/feed/tok/movies.ics",
      },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const links = await fetchCalendarFeeds();
  const origin = window.location.origin;
  expect(links.combined).toBe(`${origin}/api/calendar/feed/tok.ics`);
  expect(links.layers).toEqual({
    anime: `${origin}/api/calendar/feed/tok/anime.ics`,
    movies: `${origin}/api/calendar/feed/tok/movies.ics`,
  });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/calendar/feed-token",
    expect.objectContaining({ method: "GET" }),
  );
});

it("regenerates through POST and tolerates a server without layers", async () => {
  const fetchMock = vi.fn(async () =>
    Response.json({ path: "/api/calendar/feed/new.ics" }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const links = await fetchCalendarFeeds(true);
  expect(links.layers).toEqual({});
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/calendar/feed-token/regenerate",
    expect.objectContaining({ method: "POST" }),
  );
});
