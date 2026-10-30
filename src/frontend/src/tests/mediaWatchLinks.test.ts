import { describe, expect, it } from "vitest";
import { watchLinks } from "../services/mediaWatchLinks";

describe("watchLinks", () => {
  it("offers only available sources that recorded a web address", () => {
    const links = watchLinks([
      {
        source: "jellyfin",
        account_scope: "server:user",
        available: true,
        watch_url: "http://jellyfin.lan:8096/web/#!/details?id=abc",
        watch_label: "Jellyfin · Home",
      },
      { source: "plex", available: false, watch_url: "https://plex.example/x" },
      { source: "kodi", available: true },
      { source: "evil", available: true, watch_url: "javascript:alert(1)" },
    ]);
    expect(links).toEqual([
      {
        id: "jellyfin:server:user",
        label: "Jellyfin · Home",
        url: "http://jellyfin.lan:8096/web/#!/details?id=abc",
      },
    ]);
  });

  it("falls back to the source's name", () => {
    expect(
      watchLinks([
        {
          source: "jellyfin",
          available: true,
          watch_url: "https://j.example/x",
        },
      ])[0].label,
    ).toBe("Jellyfin");
  });
});
