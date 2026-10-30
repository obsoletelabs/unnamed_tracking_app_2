// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MediaWatchLinks from "../components/MediaWatchLinks.vue";
import { fetchWatchLinks } from "../services/mediaWatchLinks";

vi.mock("../services/mediaWatchLinks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/mediaWatchLinks")>()),
  fetchWatchLinks: vi.fn(),
}));
const fetchLinks = vi.mocked(fetchWatchLinks);

const mounted: { unmount: () => void }[] = [];
function mountLinks(providerIds: Record<string, string>) {
  const wrapper = mount(MediaWatchLinks, {
    props: { mediaType: "movie", mediaId: "m1", providerIds },
    attachTo: document.body,
  });
  mounted.push(wrapper);
  return wrapper;
}

describe("MediaWatchLinks button", () => {
  beforeEach(() => {
    fetchLinks.mockReset();
    window.localStorage.clear();
  });

  afterEach(() => {
    while (mounted.length) mounted.pop()?.unmount();
  });

  it("opens the first site on the button when no media server has the title", async () => {
    fetchLinks.mockResolvedValue([]);
    const wrapper = mountLinks({ tmdb: "603", imdb: "tt0133093" });
    await flushPromises();
    const main = wrapper.get("a.watch-main");
    expect(main.text()).toBe("↗ Open in TMDB");
    expect(main.attributes("href")).toBe(
      "https://www.themoviedb.org/movie/603",
    );
    expect(main.classes()).toContain("has-menu");
    // The other sites sit behind the separate arrow, and the arrow is not a second button label.
    const toggle = wrapper.get("button.watch-toggle");
    expect(toggle.text()).toBe("▾");
    await toggle.trigger("click");
    const items = [...document.body.querySelectorAll(".watch-menu a")].map(
      (item) => item.textContent,
    );
    expect(items).toEqual(["Open in TMDB", "Open in IMDb"]);
    expect(wrapper.emitted("present")?.at(-1)).toEqual([true]);
  });

  it("shows no button until the saved links have loaded", async () => {
    let finish: (links: never[]) => void = () => {};
    fetchLinks.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const wrapper = mountLinks({ tmdb: "603" });
    await flushPromises();
    expect(wrapper.find("a.watch-main").exists()).toBe(false);
    expect(wrapper.emitted("present")?.at(-1)).toEqual([false]);
    finish([]);
    await flushPromises();
    expect(wrapper.get("a.watch-main").text()).toBe("↗ Open in TMDB");
  });

  it("has no arrow when there is only one place to open", async () => {
    fetchLinks.mockResolvedValue([]);
    const wrapper = mountLinks({ tmdb: "603" });
    await flushPromises();
    expect(wrapper.get("a.watch-main").text()).toBe("↗ Open in TMDB");
    expect(wrapper.find("button.watch-toggle").exists()).toBe(false);
  });

  it("puts a media server first, with the sites in the menu", async () => {
    fetchLinks.mockResolvedValue([
      {
        id: "jellyfin",
        label: "Jellyfin",
        url: "https://jf.example/web/index.html#!/details?id=1",
        lastPlayed: null,
      },
    ] as never);
    const wrapper = mountLinks({ tmdb: "603" });
    await flushPromises();
    expect(wrapper.get("a.watch-main").text()).toBe("▶ Watch in Jellyfin");
    await wrapper.get("button.watch-toggle").trigger("click");
    const items = [...document.body.querySelectorAll(".watch-menu a")].map(
      (item) => item.textContent,
    );
    expect(items).toEqual(["Watch in Jellyfin", "Open in TMDB"]);
  });

  it("shows nothing and reports no button when there is nowhere to go", async () => {
    fetchLinks.mockResolvedValue([]);
    const wrapper = mountLinks({});
    await flushPromises();
    expect(wrapper.find(".watch-links").exists()).toBe(false);
    expect(wrapper.emitted("present")?.at(-1)).toEqual([false]);
  });
});
